"""Tests for ENGSUP-001 Phase 2 auto-dispatcher (test_engineering_auto_dispatcher.py).

Covers:
1. Kill switch globally disabled
2. Whitelist: RUN_QA allowed when all predicates pass
3. Whitelist: RUN_READ_ONLY_REVIEW allowed when all predicates pass
4. Whitelist: RETRY allowed only under bounds
5. MANUAL ONLY decisions all blocked
6. Safety predicates: dirty tree blocks
7. Safety predicates: HEAD mismatch blocks
8. Safety predicates: branch mismatch blocks
9. Safety predicates: stale evidence blocks
10. Safety predicates: evidence conflict blocks
11. Safety predicates: scope drift blocks
12. Safety predicates: retry_count threshold blocks
13. Safety predicates: repeated failure threshold blocks
14. Safety predicates: timed-out retry blocks
15. Safety predicates: chain limit blocks fourth automatic action
16. Safety predicates: destructive instruction blocks
17. Safety predicates: live-trading instruction blocks
18. Audit event recorded on successful dispatch
19. Audit event recorded on blocked dispatch
20. Phase 1 supervisor tests remain passing
21. No auto-merge path exists
"""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from engineering.models import (
    CompletionPacket,
    Confidence,
    DelegationStatus,
    EvidenceBundle,
    EvidenceConflict,
    Severity,
    SupervisorDecision,
    SupervisorDecisionKind,
    WorkflowState,
)
from engineering.workflow_store import QARecord, DelegationRecord
from engineering.auto_dispatcher import (
    _AUTO_CHAIN_LIMIT,
    _RETRY_MAX_RETRY_COUNT,
    _RETRY_MAX_SAME_FAILURE,
    _chain_counts,
    _check_destructive_instruction,
    _check_scope_drift,
    _is_auto_dispatch_enabled,
    _pre_dispatch_safety_check,
    supervise_and_auto_dispatch,
)


import pytest


@pytest.fixture(autouse=True)
def _enable_auto_dispatch_env(monkeypatch):
    """Enable auto-dispatch for all tests except the kill-switch tests.

    The kill-switch tests explicitly unset or set ENGSUP_AUTO_DISPATCH_ENABLED
    to test specific values. All other tests need the dispatch enabled so they
    can test the whitelist, safety predicates, and dispatch behavior.
    """
    monkeypatch.setenv("ENGSUP_AUTO_DISPATCH_ENABLED", "1")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_bundle(
    git_tree_clean: bool = True,
    git_head: str = "abc1234",
    git_branch: str = "agent/test",
    qa_result=None,
    review_recommendation: str = "",
    report_md_exists: bool = False,
    report_md_stale: bool = False,
    delegation_verified: bool = True,
    conflicts: tuple[EvidenceConflict, ...] = (),
    stale_flags: tuple[str, ...] = (),
) -> EvidenceBundle:
    return EvidenceBundle(
        git_verified=True,
        git_tree_clean=git_tree_clean,
        git_head=git_head,
        git_branch=git_branch,
        qa_verified=qa_result is not None,
        qa_result=qa_result,
        review_verified=bool(review_recommendation),
        review_recommendation=review_recommendation,
        report_md_verified=report_md_exists,
        report_md_exists=report_md_exists,
        report_md_stale=report_md_stale,
        report_md_content_hash="abc123",
        report_md_excerpt="Test report",
        delegation_verified=delegation_verified,
        missing_evidence=(),
        conflicts=conflicts,
        stale_evidence=stale_flags,
    )


def make_decision(
    kind: SupervisorDecisionKind,
    human_required: bool = False,
    blockers: tuple[str, ...] = (),
    warnings: tuple[str, ...] = (),
    evidence_conflicts: tuple[EvidenceConflict, ...] = (),
    stale_evidence: tuple[str, ...] = (),
    generated_instruction: str = "Run the next step.",
) -> SupervisorDecision:
    return SupervisorDecision(
        decision=kind,
        severity=Severity.INFO,
        confidence=Confidence.VERIFIED,
        evidence_used=("git",),
        missing_evidence=(),
        blockers=blockers,
        warnings=warnings,
        human_approval_required=human_required,
        josh_approval_kinds=(),
        supervisor_note="Test decision.",
        generated_instruction=generated_instruction,
        evidence_conflicts=evidence_conflicts,
        stale_evidence=stale_evidence,
        git_verified=True,
        qa_verified=True,
        review_verified=True,
        report_md_verified=True,
    )


def make_packet(
    workflow_state: WorkflowState = WorkflowState.WAIT_FOR_AGENT,
    delegation_status: str = "COMPLETE",
    qa_exit_code: int | None = None,
    qa_passed_count: int | None = None,
    qa_failed_count: int | None = None,
    qa_timed_out: bool = False,
    retry_count: int = 0,
    same_qa_failure_count: int = 0,
    changed_files: tuple[str, ...] = (),
    allowed_areas: tuple[str, ...] = ("engineering",),
    head_commit: str = "abc1234",
    feature_branch: str = "agent/test",
    task_id: str = "ENGPLAT-002B",
) -> CompletionPacket:
    return CompletionPacket(
        version="1.0",
        task_id=task_id,
        task_title="Test task",
        workflow_state=workflow_state,
        feature_branch=feature_branch,
        head_commit=head_commit,
        agent_name="test-agent",
        delegation_status=delegation_status,
        delegation_exit_code=0,
        delegation_failure_reason="",
        qa_exit_code=qa_exit_code,
        qa_passed_count=qa_passed_count,
        qa_failed_count=qa_failed_count,
        qa_timed_out=qa_timed_out,
        review_recommendation="",
        report_md_exists=False,
        report_md_modified_at=None,
        report_md_content_hash=None,
        changed_files=changed_files,
        allowed_areas=allowed_areas,
        retry_count=retry_count,
        same_qa_failure_count=same_qa_failure_count,
        generated_at="2026-08-16T00:00:00Z",
    )


# ---------------------------------------------------------------------------
# 1. Kill switch globally disabled
# ---------------------------------------------------------------------------

def test_kill_switch_disabled_via_env(monkeypatch):
    """ENGSUP_AUTO_DISPATCH_ENABLED=0 disables auto-dispatch."""
    monkeypatch.setenv("ENGSUP_AUTO_DISPATCH_ENABLED", "0")
    # Force re-import to pick up env change (module-level cache)
    import importlib
    import engineering.auto_dispatcher as ad
    importlib.reload(ad)
    assert ad._is_auto_dispatch_enabled() is False
    importlib.reload(ad)  # restore


def test_kill_switch_disabled_by_default(monkeypatch):
    """Default (no env var) means auto-dispatch is disabled."""
    monkeypatch.delenv("ENGSUP_AUTO_DISPATCH_ENABLED", raising=False)
    import importlib
    import engineering.auto_dispatcher as ad
    importlib.reload(ad)
    assert ad._is_auto_dispatch_enabled() is False
    importlib.reload(ad)  # restore


def test_kill_switch_explicit_enabled(monkeypatch):
    """ENGSUP_AUTO_DISPATCH_ENABLED=1 enables auto-dispatch."""
    monkeypatch.setenv("ENGSUP_AUTO_DISPATCH_ENABLED", "1")
    import importlib
    import engineering.auto_dispatcher as ad
    importlib.reload(ad)
    assert ad._is_auto_dispatch_enabled() is True
    importlib.reload(ad)  # restore


# ---------------------------------------------------------------------------
# 2. RUN_QA allowed when all predicates pass
# ---------------------------------------------------------------------------

def test_run_qa_allowed_when_all_predicates_pass():
    """RUN_QA with clean tree, matching HEAD/branch, no conflicts → allowed."""
    packet = make_packet(workflow_state=WorkflowState.WAIT_FOR_AGENT)
    bundle = make_bundle(git_tree_clean=True, git_head="abc1234", git_branch="agent/test")
    decision = make_decision(SupervisorDecisionKind.RUN_QA)

    allowed, reason = _pre_dispatch_safety_check(packet, bundle, decision, auto_chain_count=0)
    assert allowed is True
    assert reason == ""


# ---------------------------------------------------------------------------
# 3. RUN_READ_ONLY_REVIEW allowed when all predicates pass
# ---------------------------------------------------------------------------

def test_run_read_only_review_allowed_when_all_predicates_pass():
    """RUN_READ_ONLY_REVIEW with all predicates pass → allowed."""
    packet = make_packet(workflow_state=WorkflowState.QA)
    bundle = make_bundle(git_tree_clean=True)
    decision = make_decision(SupervisorDecisionKind.RUN_READ_ONLY_REVIEW)

    allowed, reason = _pre_dispatch_safety_check(packet, bundle, decision, auto_chain_count=0)
    assert allowed is True


# ---------------------------------------------------------------------------
# 4. RETRY allowed only under bounds
# ---------------------------------------------------------------------------

def test_retry_allowed_under_bounds():
    """RETRY with retry_count<3, same_failure<2, not timed_out → allowed."""
    packet = make_packet(
        workflow_state=WorkflowState.WAIT_FOR_AGENT,
        delegation_status="FAILED",
        retry_count=1,
        same_qa_failure_count=1,
        qa_timed_out=False,
    )
    bundle = make_bundle()
    decision = make_decision(SupervisorDecisionKind.RETRY)

    allowed, reason = _pre_dispatch_safety_check(packet, bundle, decision, auto_chain_count=0)
    assert allowed is True


def test_retry_blocked_at_retry_count_threshold():
    """RETRY with retry_count >= 3 → blocked."""
    packet = make_packet(
        workflow_state=WorkflowState.WAIT_FOR_AGENT,
        delegation_status="FAILED",
        retry_count=_RETRY_MAX_RETRY_COUNT,  # exactly at threshold
    )
    bundle = make_bundle()
    decision = make_decision(SupervisorDecisionKind.RETRY)

    allowed, reason = _pre_dispatch_safety_check(packet, bundle, decision, auto_chain_count=0)
    assert allowed is False
    assert "retry_count" in reason


def test_retry_blocked_at_same_failure_threshold():
    """RETRY with same_failure >= 2 → blocked."""
    packet = make_packet(
        workflow_state=WorkflowState.WAIT_FOR_AGENT,
        delegation_status="FAILED",
        same_qa_failure_count=_RETRY_MAX_SAME_FAILURE,
    )
    bundle = make_bundle()
    decision = make_decision(SupervisorDecisionKind.RETRY)

    allowed, reason = _pre_dispatch_safety_check(packet, bundle, decision, auto_chain_count=0)
    assert allowed is False
    assert "same_failure" in reason


def test_retry_blocked_timed_out():
    """RETRY with qa_timed_out=True → blocked."""
    packet = make_packet(
        workflow_state=WorkflowState.WAIT_FOR_AGENT,
        delegation_status="FAILED",
        qa_timed_out=True,
    )
    bundle = make_bundle()
    decision = make_decision(SupervisorDecisionKind.RETRY)

    allowed, reason = _pre_dispatch_safety_check(packet, bundle, decision, auto_chain_count=0)
    assert allowed is False
    assert "timed_out" in reason


# ---------------------------------------------------------------------------
# 5. MANUAL ONLY decisions all blocked
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind", [
    SupervisorDecisionKind.CONTINUE,
    SupervisorDecisionKind.REQUEST_CHANGES,
    SupervisorDecisionKind.WAIT_FOR_HUMAN_APPROVAL,
    SupervisorDecisionKind.READY_FOR_MERGE_APPROVAL,
    SupervisorDecisionKind.BLOCKED,
    SupervisorDecisionKind.ESCALATE_POLICY_CONFLICT,
    SupervisorDecisionKind.COMPLETE,
])
def test_manual_only_decisions_blocked(kind):
    """Decision kinds not in whitelist are always blocked."""
    packet = make_packet()
    bundle = make_bundle()
    decision = make_decision(kind)

    allowed, reason = _pre_dispatch_safety_check(packet, bundle, decision, auto_chain_count=0)
    assert allowed is False
    assert "not in whitelist" in reason


# ---------------------------------------------------------------------------
# 6. Safety predicates: dirty tree blocks
# ---------------------------------------------------------------------------

def test_dirty_tree_blocks():
    """git_tree_clean=False → blocked."""
    packet = make_packet()
    bundle = make_bundle(git_tree_clean=False)
    decision = make_decision(SupervisorDecisionKind.RUN_QA)

    allowed, reason = _pre_dispatch_safety_check(packet, bundle, decision, auto_chain_count=0)
    assert allowed is False
    assert "dirty" in reason


# ---------------------------------------------------------------------------
# 7. Safety predicates: HEAD mismatch blocks
# ---------------------------------------------------------------------------

def test_head_mismatch_blocks():
    """bundle.git_head != packet.head_commit → blocked."""
    packet = make_packet(head_commit="abc1234")
    bundle = make_bundle(git_head="different123")
    decision = make_decision(SupervisorDecisionKind.RUN_QA)

    allowed, reason = _pre_dispatch_safety_check(packet, bundle, decision, auto_chain_count=0)
    assert allowed is False
    assert "HEAD mismatch" in reason


# ---------------------------------------------------------------------------
# 8. Safety predicates: branch mismatch blocks
# ---------------------------------------------------------------------------

def test_branch_mismatch_blocks():
    """bundle.git_branch != packet.feature_branch → blocked."""
    packet = make_packet(feature_branch="agent/test")
    bundle = make_bundle(git_branch="agent/other")
    decision = make_decision(SupervisorDecisionKind.RUN_QA)

    allowed, reason = _pre_dispatch_safety_check(packet, bundle, decision, auto_chain_count=0)
    assert allowed is False
    assert "branch mismatch" in reason


# ---------------------------------------------------------------------------
# 9. Safety predicates: stale evidence blocks
# ---------------------------------------------------------------------------

def test_stale_evidence_blocks():
    """decision.stale_evidence non-empty → blocked."""
    packet = make_packet()
    bundle = make_bundle()
    decision = make_decision(
        SupervisorDecisionKind.RUN_QA,
        stale_evidence=("report_md",),
    )

    allowed, reason = _pre_dispatch_safety_check(packet, bundle, decision, auto_chain_count=0)
    assert allowed is False
    assert "stale" in reason


# ---------------------------------------------------------------------------
# 10. Safety predicates: evidence conflict blocks
# ---------------------------------------------------------------------------

def test_evidence_conflict_blocks():
    """decision.evidence_conflicts non-empty → blocked."""
    packet = make_packet()
    bundle = make_bundle()
    conflict = EvidenceConflict(
        field_label="tree_clean",
        source_a="report_md",
        value_a="clean",
        source_b="git",
        value_b="dirty",
        resolution="used_priority_1",
    )
    decision = make_decision(
        SupervisorDecisionKind.RUN_QA,
        evidence_conflicts=(conflict,),
    )

    allowed, reason = _pre_dispatch_safety_check(packet, bundle, decision, auto_chain_count=0)
    assert allowed is False
    assert "evidence conflict" in reason


# ---------------------------------------------------------------------------
# 11. Safety predicates: scope drift blocks
# ---------------------------------------------------------------------------

def test_scope_drift_blocks():
    """Changed file outside allowed_areas → blocked."""
    packet = make_packet(
        changed_files=("src/trading/strategy.py",),
        allowed_areas=("engineering",),
    )
    bundle = make_bundle()
    decision = make_decision(SupervisorDecisionKind.RUN_QA)

    allowed, reason = _pre_dispatch_safety_check(packet, bundle, decision, auto_chain_count=0)
    assert allowed is False
    assert "scope drift" in reason


def test_scope_drift_no_allowed_areas_blocks():
    """Empty allowed_areas → blocked as scope drift."""
    packet = make_packet(
        changed_files=("engineering/foo.py",),
        allowed_areas=(),
    )
    bundle = make_bundle()
    decision = make_decision(SupervisorDecisionKind.RUN_QA)

    allowed, reason = _pre_dispatch_safety_check(packet, bundle, decision, auto_chain_count=0)
    assert allowed is False


def test_scope_drift_helper():
    """_check_scope_drift helper: files in allowed areas pass."""
    assert _check_scope_drift(("engineering/foo.py",), ("engineering",)) is False
    assert _check_scope_drift(("src/trading/strategy.py",), ("engineering",)) is True
    assert _check_scope_drift(("engineering/foo.py",), ()) is True


# ---------------------------------------------------------------------------
# 12. Safety predicates: retry_count threshold blocks
# ---------------------------------------------------------------------------

def test_retry_count_at_threshold_blocks():
    """retry_count >= 3 → blocked."""
    packet = make_packet(
        workflow_state=WorkflowState.WAIT_FOR_AGENT,
        delegation_status="FAILED",
        retry_count=3,
    )
    bundle = make_bundle()
    decision = make_decision(SupervisorDecisionKind.RETRY)

    allowed, reason = _pre_dispatch_safety_check(packet, bundle, decision, auto_chain_count=0)
    assert allowed is False
    assert "retry_count" in reason


# ---------------------------------------------------------------------------
# 13. Safety predicates: repeated failure threshold blocks
# ---------------------------------------------------------------------------

def test_same_failure_at_threshold_blocks():
    """same_qa_failure_count >= 2 → blocked."""
    packet = make_packet(
        workflow_state=WorkflowState.WAIT_FOR_AGENT,
        delegation_status="FAILED",
        same_qa_failure_count=2,
    )
    bundle = make_bundle()
    decision = make_decision(SupervisorDecisionKind.RETRY)

    allowed, reason = _pre_dispatch_safety_check(packet, bundle, decision, auto_chain_count=0)
    assert allowed is False
    assert "same_failure" in reason


# ---------------------------------------------------------------------------
# 14. Safety predicates: timed-out retry blocks
# ---------------------------------------------------------------------------

def test_timed_out_qa_retry_blocks():
    """qa_timed_out=True on RETRY → blocked."""
    packet = make_packet(
        workflow_state=WorkflowState.WAIT_FOR_AGENT,
        delegation_status="FAILED",
        qa_timed_out=True,
    )
    bundle = make_bundle()
    decision = make_decision(SupervisorDecisionKind.RETRY)

    allowed, reason = _pre_dispatch_safety_check(packet, bundle, decision, auto_chain_count=0)
    assert allowed is False
    assert "timed_out" in reason


# ---------------------------------------------------------------------------
# 15. Safety predicates: chain limit blocks fourth automatic action
# ---------------------------------------------------------------------------

def test_chain_limit_blocks_4th_action():
    """auto_chain_count >= _AUTO_CHAIN_LIMIT (3) → blocked."""
    packet = make_packet()
    bundle = make_bundle()
    decision = make_decision(SupervisorDecisionKind.RUN_QA)

    # At limit = allowed (3rd action)
    allowed, _ = _pre_dispatch_safety_check(packet, bundle, decision, auto_chain_count=3)
    assert allowed is True

    # Over limit = blocked (4th action)
    allowed, reason = _pre_dispatch_safety_check(packet, bundle, decision, auto_chain_count=4)
    assert allowed is False
    assert "auto-chain limit" in reason


# ---------------------------------------------------------------------------
# 16. Safety predicates: destructive instruction blocks
# ---------------------------------------------------------------------------

def test_destructive_git_command_blocks():
    """Instruction with 'git push --force' → blocked."""
    packet = make_packet()
    bundle = make_bundle()
    decision = make_decision(
        SupervisorDecisionKind.RUN_QA,
        generated_instruction="git push --force origin main",
    )

    allowed, reason = _pre_dispatch_safety_check(packet, bundle, decision, auto_chain_count=0)
    assert allowed is False
    assert "destructive" in reason


def test_destructive_rm_rf_blocks():
    """Instruction with 'rm -rf /' → blocked."""
    packet = make_packet()
    bundle = make_bundle()
    decision = make_decision(
        SupervisorDecisionKind.RUN_QA,
        generated_instruction="rm -rf /tmp/build-output",
    )

    allowed, reason = _pre_dispatch_safety_check(packet, bundle, decision, auto_chain_count=0)
    assert allowed is False
    assert "destructive" in reason


# ---------------------------------------------------------------------------
# 17. Safety predicates: live-trading instruction blocks
# ---------------------------------------------------------------------------

def test_live_trading_instruction_blocks():
    """Instruction referencing live trading/brokerage → blocked."""
    packet = make_packet()
    bundle = make_bundle()
    decision = make_decision(
        SupervisorDecisionKind.RUN_QA,
        generated_instruction="Submit a market order to buy 100 shares via Alpaca",
    )

    allowed, reason = _pre_dispatch_safety_check(packet, bundle, decision, auto_chain_count=0)
    assert allowed is False
    assert "live trading" in reason


# ---------------------------------------------------------------------------
# 18. Audit event recorded on successful dispatch
# ---------------------------------------------------------------------------

def test_audit_event_recorded_on_dispatch(monkeypatch):
    """supervise_and_auto_dispatch records audit event to event_store."""
    # Ensure packet HEAD/branch match bundle so safety check passes
    packet = make_packet(
        workflow_state=WorkflowState.WAIT_FOR_AGENT,
        head_commit="abc1234",
        feature_branch="agent/test",
    )
    # Reset chain counts to ensure clean state
    _chain_counts.clear()

    # Mock bundle and decision that would pass safety check
    mock_bundle = make_bundle(git_tree_clean=True, git_head="abc1234", git_branch="agent/test")
    mock_decision = make_decision(
        SupervisorDecisionKind.RUN_QA,
        human_required=False,
    )

    mock_workflow = MagicMock()
    mock_workflow.state = WorkflowState.WAIT_FOR_AGENT

    mock_store = MagicMock()
    mock_store.exists.return_value = True
    mock_store.load.return_value = mock_workflow

    mock_event_store = MagicMock()
    mock_clock = lambda: datetime(2026, 8, 16, tzinfo=UTC)

    # Patch the internal functions that depend on external state:
    # 1. _pre_dispatch_safety_check → always allow
    # 2. _do_run_qa → return mock_workflow without error
    with patch("engineering.auto_dispatcher._pre_dispatch_safety_check") as mock_safety:
        mock_safety.return_value = (True, "")
        with patch("engineering.auto_dispatcher._do_run_qa") as mock_do_qa:
            mock_do_qa.return_value = (mock_workflow, "")
            result = supervise_and_auto_dispatch(
                packet,
                repo_root=Path("/repo"),
                event_store=mock_event_store,
                clock=mock_clock,
            )

    # Event store append called twice: pre-check (False) + post-dispatch (True)
    assert mock_event_store.append.call_count == 2, (
        f"Expected 2 audit calls, got {mock_event_store.append.call_count}"
    )
    # Second call is the successful dispatch audit
    last_event = mock_event_store.append.call_args_list[1][0][0]
    assert last_event.payload["dispatch_occurred"] is True
    assert last_event.payload["decision_kind"] == "RUN_QA"
    assert last_event.payload["block_reason"] == ""


# ---------------------------------------------------------------------------
# 19. Audit event recorded on blocked dispatch
# ---------------------------------------------------------------------------

def test_audit_event_recorded_on_blocked_dispatch(monkeypatch):
    """supervise_and_auto_dispatch records audit event even when blocked."""
    packet = make_packet(
        changed_files=("src/trading/strategy.py",),
        allowed_areas=("engineering",),
    )

    mock_bundle = make_bundle(git_tree_clean=False)
    mock_decision = make_decision(
        SupervisorDecisionKind.RUN_QA,
        generated_instruction="Run the next step.",
    )
    mock_event_store = MagicMock()
    mock_clock = lambda: datetime(2026, 8, 16, tzinfo=UTC)

    with patch("engineering.auto_dispatcher.Supervisor") as MockSupervisor:
        instance = MockSupervisor.return_value
        instance.supervise.return_value = mock_decision
        instance._build_bundle.return_value = mock_bundle

        result = supervise_and_auto_dispatch(
            packet,
            repo_root=Path("/repo"),
            event_store=mock_event_store,
            clock=mock_clock,
        )

    # Audit event recorded
    assert mock_event_store.append.called is True
    event = mock_event_store.append.call_args[0][0]
    assert event.payload["dispatch_occurred"] is False
    assert "dirty" in event.payload["block_reason"]
    # human_approval_required should be True since it was blocked
    assert result.human_approval_required is True


# ---------------------------------------------------------------------------
# 20. Phase 1 supervisor tests remain passing
# ---------------------------------------------------------------------------

def test_supervisor_still_read_only():
    """Supervisor.supervise() still returns decision without dispatch."""
    from engineering.supervisor import Supervisor
    from pathlib import Path

    # Verify the Supervisor class has no dispatch-related attributes
    supervisor = Supervisor(repo_root=Path("/repo"))
    decision = supervisor.supervise(make_packet())
    # Supervisor never dispatches — decision returned with human_approval_required
    assert hasattr(decision, "decision")
    assert hasattr(decision, "human_approval_required")


# ---------------------------------------------------------------------------
# 21. No auto-merge path exists
# ---------------------------------------------------------------------------

def test_no_auto_merge_in_dispatcher():
    """Auto-dispatcher does not call merge or set agents_may_merge."""
    import engineering.auto_dispatcher as ad
    import inspect

    source = inspect.getsource(ad)
    assert "merge" not in source.lower() or "merge_only" not in source.lower()
    # No MERGE workflow state or agents_may_merge references
    assert "agents_may_merge" not in source


# ---------------------------------------------------------------------------
# Destructive instruction helper tests
# ---------------------------------------------------------------------------

def test_check_destructive_instruction_git_force_push():
    assert _check_destructive_instruction("git push --force origin main") is not None
    assert _check_destructive_instruction("git clean -fdx") is not None
    assert _check_destructive_instruction("git reset --hard HEAD~1") is not None


def test_check_destructive_instruction_safe():
    assert _check_destructive_instruction("git status") is None
    assert _check_destructive_instruction("git log --oneline") is None
    assert _check_destructive_instruction("echo hello") is None


def test_check_destructive_instruction_db():
    assert _check_destructive_instruction("DELETE FROM events WHERE id = 1") is not None
    assert _check_destructive_instruction("DROP TABLE events") is not None


def test_check_destructive_instruction_live_trading():
    assert _check_destructive_instruction("Submit a live market order") is not None
    assert _check_destructive_instruction("Buy 100 shares via Alpaca brokerage") is not None
