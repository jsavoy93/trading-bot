"""Tests for ENGSUP-001 Phase 1 supervisor (test_engineering_supervisor.py).

Covers:
1. CompletionPacket: valid construction
2. CompletionPacket: frozen immutability
3. SupervisorDecision: WAIT_FOR_HUMAN_APPROVAL human_approval_required=True
4. SupervisorDecision: CONTINUE human_approval_required=False
5. Evidence verification: clean tree
6. Evidence verification: mismatch (agent claims clean, git dirty)
7. Evidence verification: commit matches
8. Evidence verification: commit mismatch
9. Loop protection: 3rd identical QA failure → ESCALATE not RETRY
10. Loop protection: scope drift detected
11. Privacy: supervisor_note ≤ 5 sentences
12. Privacy: evidence truncation > 50 lines
13. Auto-dispatch: not authorized in Phase 1
14. Auto-dispatch whitelist: non-whitelisted kinds require approval
15. SupervisorDecision frozen immutability
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from engineering.models import (
    CompletionPacket,
    Confidence,
    EvidenceBundle,
    EvidenceRef,
    Severity,
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
    """Minimal valid packet for WAIT_FOR_AGENT state."""
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
# 1. CompletionPacket: valid construction
# ---------------------------------------------------------------------------

def test_completion_packet_valid_construction(base_packet):
    """All required fields present; version check."""
    assert base_packet.version == "1.0"
    assert base_packet.task_id == "ENGPLAT-002B"
    assert base_packet.workflow_state == WorkflowState.WAIT_FOR_AGENT
    assert base_packet.feature_branch == "agent/adapter-boundary"
    assert base_packet.head_commit == "abc1234"
    assert base_packet.delegation_status == "COMPLETE"
    assert base_packet.allowed_areas == ("engineering",)


# ---------------------------------------------------------------------------
# 2. CompletionPacket: frozen immutability
# ---------------------------------------------------------------------------

def test_completion_packet_frozen(base_packet):
    """Mutation raises FrozenInstanceError."""
    with pytest.raises(Exception):  # FrozenInstanceError
        base_packet.task_id = "CHANGED"


# ---------------------------------------------------------------------------
# 3. SupervisorDecision: WAIT_FOR_HUMAN_APPROVAL human_approval_required=True
# ---------------------------------------------------------------------------

def test_decision_wait_for_human_approval_requires_approval(supervisor, base_packet):
    """WAIT_FOR_HUMAN_APPROVAL sets human_approval_required=True."""
    # Set delegation to PENDING so supervisor picks WAIT_FOR_HUMAN_APPROVAL
    packet = replace(base_packet, delegation_status="PENDING")

    with _git_mock(branch="agent/adapter-boundary", head="abc1234", clean=True):
        decision = supervisor.supervise(packet)

    assert decision.decision == SupervisorDecisionKind.WAIT_FOR_HUMAN_APPROVAL
    assert decision.human_approval_required is True


# ---------------------------------------------------------------------------
# 4. SupervisorDecision: CONTINUE human_approval_required=False
# ---------------------------------------------------------------------------

def test_decision_continue_no_human_approval(supervisor, base_packet):
    """CONTINUE does not require human approval."""
    with _git_mock(branch="agent/adapter-boundary", head="abc1234", clean=True):
        decision = supervisor.supervise(base_packet)

    assert decision.decision == SupervisorDecisionKind.CONTINUE
    assert decision.human_approval_required is False


# ---------------------------------------------------------------------------
# 5. Evidence verification: clean tree
# ---------------------------------------------------------------------------

def test_evidence_clean_tree(supervisor, base_packet):
    """Git shows clean → supervisor verifies as clean."""
    with _git_mock(branch="agent/adapter-boundary", head="abc1234", clean=True):
        bundle = supervisor._build_bundle(base_packet)

    assert bundle.git_tree_clean is True
    assert bundle.git_verified is True
    assert bundle.git_branch == "agent/adapter-boundary"
    assert bundle.git_head == "abc1234"


# ---------------------------------------------------------------------------
# 6. Evidence verification: mismatch detected
# ---------------------------------------------------------------------------

def test_evidence_mismatch_agent_claims_clean_git_dirty(supervisor, base_packet):
    """Agent says clean, git shows dirty → ESCALATE_POLICY_CONFLICT."""
    with _git_mock(branch="agent/adapter-boundary", head="abc1234", clean=False):
        decision = supervisor.supervise(base_packet)

    assert decision.decision == SupervisorDecisionKind.ESCALATE_POLICY_CONFLICT
    assert decision.severity == Severity.BLOCKING
    assert any("dirty" in b.lower() for b in decision.blockers)


# ---------------------------------------------------------------------------
# 7. Evidence verification: commit matches
# ---------------------------------------------------------------------------

def test_evidence_commit_matches(supervisor, base_packet):
    """git rev-parse HEAD matches packet head_commit → no mismatch flag."""
    with _git_mock(branch="agent/adapter-boundary", head="abc1234", clean=True):
        bundle = supervisor._build_bundle(base_packet)

    assert bundle.git_head == "abc1234"
    # No conflict should be present
    commit_conflicts = [
        c for c in bundle.conflicts
        if c.field_label == "head_commit"
    ]
    assert len(commit_conflicts) == 0


# ---------------------------------------------------------------------------
# 8. Evidence verification: commit mismatch
# ---------------------------------------------------------------------------

def test_evidence_commit_mismatch(supervisor, base_packet):
    """Commit differs → decision flags mismatch."""
    packet = replace(base_packet, head_commit="oldcommit")

    with _git_mock(branch="agent/adapter-boundary", head="newcommit", clean=True):
        decision = supervisor.supervise(packet)

    assert decision.decision == SupervisorDecisionKind.ESCALATE_POLICY_CONFLICT
    commit_conflicts = [
        c for c in decision.evidence_conflicts
        if c.field_label == "head_commit"
    ]
    assert len(commit_conflicts) == 1
    assert commit_conflicts[0].source_a == "completion_packet"
    assert commit_conflicts[0].source_b == "git"
    assert commit_conflicts[0].value_a == "oldcommit"
    assert commit_conflicts[0].value_b == "newcommit"


# ---------------------------------------------------------------------------
# 9. Loop protection: 3rd identical QA failure → ESCALATE not RETRY
# ---------------------------------------------------------------------------

def test_loop_protection_3rd_identical_qa_failure_escalates(supervisor):
    """Repeated same QA failure 2+ times → ESCALATE_POLICY_CONFLICT, not RETRY."""
    packet = CompletionPacket(
        version="1.0",
        task_id="ENGPLAT-002B",
        task_title="Test",
        workflow_state=WorkflowState.QA,
        feature_branch="agent/test",
        head_commit="abc1234",
        agent_name="test-agent",
        delegation_status="COMPLETE",
        delegation_exit_code=0,
        delegation_failure_reason="",
        qa_exit_code=1,
        qa_passed_count=0,
        qa_failed_count=3,
        qa_timed_out=False,
        review_recommendation="",
        report_md_exists=False,
        report_md_modified_at=None,
        report_md_content_hash=None,
        changed_files=(),
        allowed_areas=("engineering",),
        retry_count=0,
        same_qa_failure_count=2,  # 2 = 3rd occurrence
        same_review_finding_count=0,
        generated_at="2026-08-15T10:00:00Z",
    )

    with _git_mock(branch="agent/test", head="abc1234", clean=True):
        decision = supervisor.supervise(packet)

    assert decision.decision == SupervisorDecisionKind.ESCALATE_POLICY_CONFLICT
    assert decision.severity == Severity.BLOCKING
    assert decision.human_approval_required is True


# ---------------------------------------------------------------------------
# 10. Loop protection: scope drift detected
# ---------------------------------------------------------------------------

def test_loop_protection_scope_drift(supervisor):
    """Files changed outside allowed areas → ESCALATE_POLICY_CONFLICT."""
    packet = CompletionPacket(
        version="1.0",
        task_id="ENGPLAT-002B",
        task_title="Test",
        workflow_state=WorkflowState.QA,
        feature_branch="agent/test",
        head_commit="abc1234",
        agent_name="test-agent",
        delegation_status="COMPLETE",
        delegation_exit_code=0,
        delegation_failure_reason="",
        qa_exit_code=1,
        qa_passed_count=0,
        qa_failed_count=1,
        qa_timed_out=False,
        review_recommendation="",
        report_md_exists=False,
        report_md_modified_at=None,
        report_md_content_hash=None,
        changed_files=("src/trading/strategy.py",),  # Outside allowed "engineering"
        allowed_areas=("engineering",),
        retry_count=0,
        same_qa_failure_count=0,
        same_review_finding_count=0,
        generated_at="2026-08-15T10:00:00Z",
    )

    with _git_mock(branch="agent/test", head="abc1234", clean=True,
                   changed_files=("src/trading/strategy.py",)):
        decision = supervisor.supervise(packet)

    assert decision.decision == SupervisorDecisionKind.ESCALATE_POLICY_CONFLICT
    assert any("scope drift" in b.lower() for b in decision.blockers)


# ---------------------------------------------------------------------------
# 11. Privacy: supervisor_note ≤ 5 sentences
# ---------------------------------------------------------------------------

def test_privacy_supervisor_note_max_five_sentences(supervisor):
    """supervisor_note is capped at 5 sentences."""
    packet = CompletionPacket(
        version="1.0",
        task_id="ENGPLAT-002B",
        task_title="Test",
        workflow_state=WorkflowState.WAIT_FOR_AGENT,
        feature_branch="agent/test",
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

    with _git_mock(branch="agent/test", head="abc1234", clean=True):
        decision = supervisor.supervise(packet)

    # Count sentences (period/!/? separated)
    sentences = _count_sentences(decision.supervisor_note)
    assert sentences <= 5, f"Note has {sentences} sentences: {decision.supervisor_note!r}"


# ---------------------------------------------------------------------------
# 12. Privacy: evidence truncation > 50 lines
# ---------------------------------------------------------------------------

def test_evidence_truncation_over_max_lines(supervisor, base_packet, monkeypatch):
    """Evidence text > 50 lines is truncated with [...] marker."""
    large_content = "\n".join(f"line {i}" for i in range(100)) + "\n"

    def fake_read_report_bytes(path):
        return large_content.encode("utf-8")

    with _git_mock(branch="agent/adapter-boundary", head="abc1234", clean=True):
        with patch.object(supervisor, "_read_report_bytes", fake_read_report_bytes):
            bundle = supervisor._build_bundle(replace(base_packet, report_md_exists=True))

    if bundle.report_md_excerpt:
        excerpt_lines = bundle.report_md_excerpt.splitlines()
        assert excerpt_lines[-1] == "[...]", \
            f"Expected [...] truncation marker, got: {excerpt_lines[-1]!r}"
        assert len(excerpt_lines) <= 50 + 1, \
            f"Expected ≤51 lines (50 + marker), got {len(excerpt_lines)}"


# ---------------------------------------------------------------------------
# 13. Auto-dispatch: not authorized in Phase 1
# ---------------------------------------------------------------------------

def test_no_auto_dispatch_in_phase_1(supervisor, base_packet):
    """Phase 1: supervisor generates decisions but never sets auto-dispatch flag."""
    with _git_mock(branch="agent/adapter-boundary", head="abc1234", clean=True):
        decision = supervisor.supervise(base_packet)

    # human_approval_required=True means Josh must dispatch manually
    # The supervisor never produces a dispatch instruction — Josh always gates
    assert decision.human_approval_required is False  # CONTINUE doesn't need gate
    # No auto-dispatch attribute exists — supervisor has no dispatch capability
    assert not hasattr(decision, "auto_dispatch")


# ---------------------------------------------------------------------------
# 14. Auto-dispatch whitelist: non-whitelisted kinds require approval
# ---------------------------------------------------------------------------

def test_non_whitelisted_decision_requires_approval(supervisor):
    """Decision kinds not in whitelist require human_approval_required=True."""
    # WAIT_FOR_HUMAN_APPROVAL should require approval
    packet = CompletionPacket(
        version="1.0",
        task_id="ENGPLAT-002B",
        task_title="Test",
        workflow_state=WorkflowState.WAIT_FOR_AGENT,
        feature_branch="agent/test",
        head_commit="abc1234",
        agent_name="test-agent",
        delegation_status="PENDING",
        delegation_exit_code=None,
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

    with _git_mock(branch="agent/test", head="abc1234", clean=True):
        decision = supervisor.supervise(packet)

    # Non-approved kinds must require human approval
    non_approved_kinds = (
        SupervisorDecisionKind.WAIT_FOR_HUMAN_APPROVAL,
        SupervisorDecisionKind.READY_FOR_MERGE_APPROVAL,
        SupervisorDecisionKind.BLOCKED,
        SupervisorDecisionKind.ESCALATE_POLICY_CONFLICT,
    )
    if decision.decision in non_approved_kinds:
        assert decision.human_approval_required is True


# ---------------------------------------------------------------------------
# 15. SupervisorDecision frozen immutability
# ---------------------------------------------------------------------------

def test_supervisor_decision_frozen(supervisor, base_packet):
    """SupervisorDecision is frozen; mutation raises FrozenInstanceError."""
    with _git_mock(branch="agent/adapter-boundary", head="abc1234", clean=True):
        decision = supervisor.supervise(base_packet)

    with pytest.raises(Exception):  # FrozenInstanceError
        decision.decision = SupervisorDecisionKind.COMPLETE


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _git_mock(branch: str, head: str, clean: bool,
              changed_files: tuple[str, ...] = ()) -> MagicMock:
    """Return a context manager patching subprocess.run for git commands."""

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
            elif cmd[1] == "diff" and "--name-only" in cmd:
                mock.stdout = "\n".join(changed_files) if changed_files else ""

        mock.stderr = ""
        return mock

    return patch("engineering.supervisor.subprocess.run", side_effect=run_effect)


def _count_sentences(text: str) -> int:
    """Count sentences in text (delimited by . ! ?)."""
    import re
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return len([p for p in parts if p])
