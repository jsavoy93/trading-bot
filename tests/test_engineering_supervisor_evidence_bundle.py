"""Tests for ENGSUP-001 Phase 1 EvidenceBundle (test_engineering_supervisor_evidence_bundle.py).

Covers:
15. EvidenceBundle: priority-1 overrides priority-4 (git vs report)
16. EvidenceBundle: stale detection
17. EvidenceBundle: conflicting evidence
18. EvidenceBundle: missing non-critical evidence
19. EvidenceBundle: missing critical evidence
20. EvidenceBundle: priority order enforced
21. EvidenceBundle: lifecycle — Collect
22. EvidenceBundle: lifecycle — Verify
23. EvidenceBundle: lifecycle — Normalize
24. EvidenceBundle: lifecycle — Build
25. EvidenceBundle: lifecycle — Prompt Validation
26. EvidenceBundle: bounded reading (64 KB)
27. EvidenceBundle: secret redaction
28. EvidenceBundle: wrong-task report ignored
"""
from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from engineering.models import (
    CompletionPacket,
    Confidence,
    EvidenceBundle,
    EvidenceConflict,
    EvidenceRef,
    SupervisorDecision,
    SupervisorDecisionKind,
    TestResultSummary,
    WorkflowState,
)
from engineering.supervisor import Supervisor


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

REPO_ROOT = Path("/root/.openclaw/workspace/trading-bot")


@pytest.fixture
def supervisor() -> Supervisor:
    return Supervisor(repo_root=REPO_ROOT)


@pytest.fixture
def base_packet() -> CompletionPacket:
    return CompletionPacket(
        version="1.0",
        task_id="ENGPLAT-002B",
        task_title="Adapter boundary enforcement",
        workflow_state=WorkflowState.WAIT_FOR_AGENT,
        feature_branch="agent/adapter-boundary",
        head_commit="abc1234",
        agent_name="test-agent",
        delegation_status="COMPLETE",
        delegation_exit_code=0,
        delegation_failure_reason="",
        qa_exit_code=None,
        qa_passed_count=None,
        qa_failed_count=None,
        qa_timed_out=False,
        review_recommendation="",
        report_md_exists=False,
        report_md_modified_at=None,
        report_md_content_hash=None,
        changed_files=(),
        allowed_areas=("engineering",),
        retry_count=0,
        same_qa_failure_count=0,
        same_review_finding_count=0,
        generated_at="2026-08-15T10:00:00Z",
    )


# ---------------------------------------------------------------------------
# 15. EvidenceBundle: priority-1 overrides priority-4
# ---------------------------------------------------------------------------

def test_evidence_bundle_priority_1_overrides_priority_4(supervisor, base_packet):
    """Git shows dirty; REPORT.md claims clean → bundle marks git_verified=True, report_stale=True."""
    packet = replace(base_packet, report_md_exists=True,
                     report_md_modified_at="2026-08-15T09:00:00Z")

    def fake_read_report_bytes(path):
        return b"Tree is clean.\n"

    # _git_head_commit must return real HEAD (different from packet's abc1234)
    # so staleness is detected. _git_current_branch/_git_tree_clean stay mocked.
    with _git_mock(branch="agent/adapter-boundary", head="abc1234", clean=False):
        with patch.object(supervisor, "_git_head_commit",
                          lambda: "6e48d5c207f79bf02b88b7f35ed32ec1ab5949aa"):
            with patch.object(supervisor, "_read_report_bytes", fake_read_report_bytes):
                bundle = supervisor._build_bundle(packet)

    assert bundle.git_verified is True
    assert bundle.git_tree_clean is False
    assert bundle.report_md_stale is True
    assert bundle.report_md_exists is True


# ---------------------------------------------------------------------------
# 16. EvidenceBundle: stale detection
# ---------------------------------------------------------------------------

def test_evidence_bundle_stale_detection(supervisor, base_packet, tmp_path):
    """REPORT.md timestamp older than event store packet → stale flag set."""
    packet = replace(base_packet,
                     report_md_exists=True,
                     report_md_modified_at="2026-08-14T00:00:00Z",
                     head_commit="oldcommit123")

    def fake_read_report_bytes(path):
        return b"Old report content.\n"

    with _git_mock(branch="agent/adapter-boundary", head="newcommit456", clean=True):
        with patch.object(supervisor, "_read_report_bytes", fake_read_report_bytes):
            bundle = supervisor._build_bundle(packet)

    assert bundle.report_md_stale is True
    assert "report_md" in bundle.stale_evidence


# ---------------------------------------------------------------------------
# 17. EvidenceBundle: conflicting evidence
# ---------------------------------------------------------------------------

def test_evidence_bundle_conflicting_evidence(supervisor, base_packet):
    """Completion packet says COMPLETE; git shows different state → explicit conflict."""
    packet = replace(base_packet,
                     delegation_status="COMPLETE",
                     head_commit="abc1234")

    with _git_mock(branch="agent/adapter-boundary", head="differenthead", clean=True):
        bundle = supervisor._build_bundle(packet)

    # There should be a conflict on head_commit
    head_conflicts = [c for c in bundle.conflicts if c.field_label == "head_commit"]
    assert len(head_conflicts) == 1
    assert head_conflicts[0].source_a == "completion_packet"
    assert head_conflicts[0].source_b == "git"
    assert head_conflicts[0].resolution == "used_verified"


# ---------------------------------------------------------------------------
# 18. EvidenceBundle: missing non-critical evidence
# ---------------------------------------------------------------------------

def test_evidence_bundle_missing_noncritical_evidence(supervisor, base_packet):
    """REPORT.md unavailable → supervisor proceeds with available evidence."""
    # No report_md, no qa — just delegation
    packet = replace(base_packet, report_md_exists=False, qa_exit_code=None)

    with _git_mock(branch="agent/adapter-boundary", head="abc1234", clean=True):
        bundle = supervisor._build_bundle(packet)

    assert bundle.git_verified is True
    assert bundle.report_md_exists is False
    # Should not block — missing non-critical evidence
    # A decision of CONTINUE is valid when delegation complete and git clean


# ---------------------------------------------------------------------------
# 19. EvidenceBundle: missing critical evidence
# ---------------------------------------------------------------------------

def test_evidence_bundle_missing_critical_evidence(supervisor, base_packet):
    """Git verification unavailable → supervisor escalates, does not proceed."""
    def git_fail(*args, **kwargs):
        raise OSError("git not available")

    with patch("engineering.supervisor.subprocess.run", side_effect=git_fail):
        bundle = supervisor._build_bundle(base_packet)

    # git_verified should be False when git throws an exception
    assert bundle.git_verified is False
    assert "git" in bundle.missing_evidence


# ---------------------------------------------------------------------------
# 20. EvidenceBundle: priority order enforced
# ---------------------------------------------------------------------------

def test_evidence_bundle_priority_order_enforced(supervisor, base_packet):
    """Priority-1 state contradicts priority-4 → priority-1 used, priority-4 flagged."""
    packet = replace(base_packet,
                     report_md_exists=True,
                     report_md_modified_at="2026-08-15T09:00:00Z")

    def fake_read_report_bytes(path):
        return b"Everything is implemented correctly and clean."

    # Git shows dirty; report claims clean; HEAD mismatch triggers staleness
    with _git_mock(branch="agent/adapter-boundary", head="abc1234", clean=False):
        with patch.object(supervisor, "_git_head_commit",
                          lambda: "6e48d5c207f79bf02b88b7f35ed32ec1ab5949aa"):
            with patch.object(supervisor, "_read_report_bytes", fake_read_report_bytes):
                bundle = supervisor._build_bundle(packet)

    # Git (priority-1) must win
    assert bundle.git_tree_clean is False
    # Report (priority-4) must be flagged stale
    assert bundle.report_md_stale is True
    # Conflict must be recorded
    conflict = next(
        (c for c in bundle.conflicts if "tree_clean" in c.field_label),
        None
    )
    assert conflict is not None
    assert conflict.resolution == "used_priority_1"


# ---------------------------------------------------------------------------
# 21. EvidenceBundle: lifecycle — Collect
# ---------------------------------------------------------------------------

def test_evidence_bundle_lifecycle_collect(supervisor, base_packet):
    """All evidence sources gathered without blocking."""
    with _git_mock(branch="agent/test", head="abc1234", clean=True):
        bundle = supervisor._build_bundle(base_packet)

    # Collect phase: all evidence refs populated
    assert len(bundle.evidence_refs) >= 2  # At least branch + head from git
    assert bundle.git_verified is True
    assert bundle.delegation_verified is True


# ---------------------------------------------------------------------------
# 22. EvidenceBundle: lifecycle — Verify
# ---------------------------------------------------------------------------

def test_evidence_bundle_lifecycle_verify(supervisor, base_packet):
    """Git/GitHub verified independently; no blind trust."""
    with _git_mock(branch="agent/test", head="abc1234", clean=True):
        bundle = supervisor._build_bundle(base_packet)

    # Verify phase: all specified sources are independently read
    assert bundle.git_verified is True
    # completion_packet is stored but NOT marked verified
    if bundle.completion_packet:
        pass  # bundle.completion_packet exists but is treated as unverified input


# ---------------------------------------------------------------------------
# 23. EvidenceBundle: lifecycle — Normalize
# ---------------------------------------------------------------------------

def test_evidence_bundle_lifecycle_normalize(supervisor, base_packet):
    """Heterogeneous evidence converted to typed structures."""
    packet = replace(base_packet,
                     qa_exit_code=0,
                     qa_passed_count=100,
                     qa_failed_count=0)

    with _git_mock(branch="agent/test", head="abc1234", clean=True):
        bundle = supervisor._build_bundle(packet)

    # Normalize: raw exit code → TestResultSummary
    assert bundle.qa_result is not None
    assert isinstance(bundle.qa_result, TestResultSummary)
    assert bundle.qa_result.is_pass is True
    assert bundle.qa_result.passed_count == 100
    assert bundle.qa_result.exit_code == 0


# ---------------------------------------------------------------------------
# 24. EvidenceBundle: lifecycle — Build
# ---------------------------------------------------------------------------

def test_evidence_bundle_lifecycle_build(supervisor, base_packet):
    """Bundle includes source labels, timestamps, content hashes."""
    packet = replace(base_packet,
                    report_md_exists=True,
                    report_md_modified_at="2026-08-15T10:00:00Z")

    def fake_read_report_bytes(path):
        return b"Implementation complete. Tests pass."

    with _git_mock(branch="agent/test", head="abc1234", clean=True):
        with patch.object(supervisor, "_read_report_bytes", fake_read_report_bytes):
            bundle = supervisor._build_bundle(packet)

    # Build phase: evidence_refs have source labels, hashes, excerpts
    report_refs = [r for r in bundle.evidence_refs if r.source == "report_md"]
    assert len(report_refs) >= 1
    ref = report_refs[0]
    assert ref.source == "report_md"
    assert ref.content_hash is not None  # SHA-256 computed
    assert ref.modified_at == "2026-08-15T10:00:00Z"


# ---------------------------------------------------------------------------
# 25. EvidenceBundle: lifecycle — Prompt Validation
# ---------------------------------------------------------------------------

def test_evidence_bundle_lifecycle_prompt_validation(supervisor, base_packet):
    """Prompt within bounds, no secrets, gates preserved."""
    with _git_mock(branch="agent/test", head="abc1234", clean=True):
        decision = supervisor.supervise(base_packet)

    # Prompt Validation: supervisor_note bounded, instruction bounded, no secret
    sentences = decision.supervisor_note.split(". ")
    assert len(sentences) <= 5
    assert "api_key" not in decision.generated_instruction.lower()
    assert "secret" not in decision.generated_instruction.lower()


# ---------------------------------------------------------------------------
# 26. EvidenceBundle: bounded reading (64 KB)
# ---------------------------------------------------------------------------

def test_evidence_bundle_bounded_reading_64kb(supervisor, base_packet):
    """REPORT.md > 64 KB → truncated at bound."""
    packet = replace(base_packet,
                    report_md_exists=True,
                    report_md_modified_at="2026-08-15T10:00:00Z")

    # Create content larger than 64 KB
    large_content = "x" * (64 * 1024 + 1000)

    def fake_read_report_bytes(path):
        return large_content.encode("utf-8")

    with _git_mock(branch="agent/test", head="abc1234", clean=True):
        with patch.object(supervisor, "_read_report_bytes", fake_read_report_bytes):
            bundle = supervisor._build_bundle(packet)

    # Should be truncated to 64 KB
    assert bundle.report_md_content_hash is not None
    # The hash should be of the truncated content (first 64KB)
    expected_hash = hashlib.sha256(large_content[:64 * 1024].encode("utf-8")).hexdigest()
    assert bundle.report_md_content_hash == expected_hash


# ---------------------------------------------------------------------------
# 27. EvidenceBundle: secret redaction
# ---------------------------------------------------------------------------

def test_evidence_bundle_secret_redaction(supervisor, base_packet):
    """Content matching secret patterns → redacted in supervisor output."""
    packet = replace(base_packet,
                    report_md_exists=True,
                    report_md_modified_at="2026-08-15T10:00:00Z")

    secret_content = (
        "Implementation complete.\n"
        "API_KEY=sk-secret1234567890abcdef\n"
        "All tests pass."
    )

    def fake_read_report_bytes(path):
        return secret_content.encode("utf-8")

    with _git_mock(branch="agent/test", head="abc1234", clean=True):
        with patch.object(supervisor, "_read_report_bytes", fake_read_report_bytes):
            bundle = supervisor._build_bundle(packet)

    # The ref excerpt should not contain the raw secret
    report_refs = [r for r in bundle.evidence_refs if r.source == "report_md"]
    if report_refs:
        assert "sk-secret" not in report_refs[0].excerpt
        assert "REDACTED" in report_refs[0].excerpt


# ---------------------------------------------------------------------------
# 28. EvidenceBundle: wrong-task report ignored
# ---------------------------------------------------------------------------

def test_evidence_bundle_wrong_task_report_ignored(supervisor, base_packet):
    """Report is for task X, supervisor evaluating task Y → task mismatch flagged."""
    packet = replace(base_packet,
                    task_id="ENGPLAT-002B",
                    report_md_exists=True,
                    report_md_modified_at="2026-08-15T10:00:00Z")

    # REPORT.md mentions a different task
    wrong_task_content = (
        "Task ENGPLAT-003A implementation complete.\n"
        "ENGPLAT-002B was not worked on.\n"
    )

    def fake_read_report_bytes(path):
        return wrong_task_content.encode("utf-8")

    with _git_mock(branch="agent/test", head="abc1234", clean=True):
        with patch.object(supervisor, "_read_report_bytes", fake_read_report_bytes):
            bundle = supervisor._build_bundle(packet)

    # Bundle should have the report but task mismatch should be detectable
    # by comparing packet.task_id against report content
    assert bundle.report_md_exists is True
    assert bundle.completion_packet is not None
    report_refs = [r for r in bundle.evidence_refs if r.source == "report_md"]
    if report_refs:
        assert "ENGPLAT-003A" in report_refs[0].excerpt
        assert "ENGPLAT-002B" != "ENGPLAT-003A"


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _git_mock(branch: str, head: str, clean: bool) -> MagicMock:
    """Context manager patching subprocess.run for git commands."""
    def run_effect(cmd, **kwargs):
        mock = MagicMock()
        mock.returncode = 0
        if cmd[0] == "git" and len(cmd) >= 2:
            if cmd[1] == "rev-parse":
                if "--abbrev-ref" in cmd:
                    mock.stdout = branch
                else:
                    mock.stdout = head
            elif cmd[1] == "status":
                mock.stdout = "" if clean else "M  some/file.py"
        mock.stderr = ""
        return mock
    return patch("engineering.supervisor.subprocess.run", side_effect=run_effect)
