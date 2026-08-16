"""ENGSUP-001 Phase 2 — Auto-dispatcher for approved routine supervisor decisions.

Architectural principle: supervisor.py stays read-only (decision production only).
This module owns dispatch behavior and safety enforcement.

Usage::

    from engineering.auto_dispatcher import supervise_and_auto_dispatch

    decision = supervise_and_auto_dispatch(packet, repo_root=Path("/repo"))
    # decision.human_approval_required == False means dispatch succeeded
    # decision.human_approval_required == True means blocked (audit recorded)

Kill switch: set ENGSUP_AUTO_DISPATCH_ENABLED=0 in the process environment.
"""
from __future__ import annotations

import os
import re
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from engineering.engineering_events import (
    EngineeringEvent,
    EventSeverity,
    EventType,
    build_event,
)
from engineering.event_store import EngineeringEventStore
from engineering.models import (
    CompletionPacket,
    DelegationStatus,
    SupervisorDecision,
    SupervisorDecisionKind,
    WorkflowState,
)
from engineering.supervisor import Supervisor
from engineering.workflow_engine import dispatch_workflow
from engineering.workflow_store import DelegationRecord, QARecord, StoredWorkflow, WorkflowStore


# ---------------------------------------------------------------------------
# Kill switch — checked before every dispatch attempt
# ---------------------------------------------------------------------------

def _is_auto_dispatch_enabled() -> bool:
    """True unless ENGSUP_AUTO_DISPATCH_ENABLED is explicitly set to '0'."""
    return os.environ.get("ENGSUP_AUTO_DISPATCH_ENABLED", "1") != "0"


# ---------------------------------------------------------------------------
# Safety predicate patterns
# ---------------------------------------------------------------------------

_DESTRUCTIVE_GIT_PATTERNS = re.compile(
    r"^\s*(git\s+push\s+.*--force|git\s+clean\s+-fdx?|git\s+reset\s+--hard)",
    re.IGNORECASE,
)
_DESTRUCTIVE_FS_PATTERNS = re.compile(
    r"^\s*(rm\s+-rf\s+/|rm\s+-rf\s+\*|dd\s+|mkfs\s+|fdisk\s+-u)",
    re.IGNORECASE,
)
_LIVE_TRADING_PATTERNS = re.compile(
    r"(live|livetrading|realmoney|real money| Alpaca |brokerage|market order|buy|sell)\s*"
    r"(?!in\s+test|in\s+simulation|in\s+paper)",
    re.IGNORECASE,
)
_DB_WRITE_PATTERNS = re.compile(
    r"^\s*(DELETE\s+FROM|INSERT\s+INTO\s+\w+|UPDATE\s+\w+\s+SET|DROP\s+TABLE)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Whitelist — approved decision kinds for auto-dispatch
# ---------------------------------------------------------------------------

_AUTO_DISPATCH_WHITELIST = frozenset({
    SupervisorDecisionKind.RUN_QA,
    SupervisorDecisionKind.RUN_READ_ONLY_REVIEW,
    SupervisorDecisionKind.RETRY,
})

# RETRY-specific bounds (per Josh's approved whitelist)
_RETRY_MAX_RETRY_COUNT = 3
_RETRY_MAX_SAME_FAILURE = 2

# General loop limit: max consecutive automatic supervisor actions per workflow run
_AUTO_CHAIN_LIMIT = 3


# ---------------------------------------------------------------------------
# Per-task chain counter (in-memory; reset on process restart)
# ---------------------------------------------------------------------------

_chain_counts: dict[str, int] = {}


def _get_chain_count(task_id: str) -> int:
    return _chain_counts.get(task_id, 0)


def _increment_chain_count(task_id: str) -> int:
    count = _chain_counts.get(task_id, 0) + 1
    _chain_counts[task_id] = count
    return count


def _reset_chain_count(task_id: str) -> None:
    _chain_counts.pop(task_id, None)


# ---------------------------------------------------------------------------
# Clock (timezone-aware UTC; overridable for testing)
# ---------------------------------------------------------------------------

_default_clock: Callable[[], datetime] = lambda: datetime.now(UTC)


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

def _record_audit(
    task_id: str,
    decision_kind: SupervisorDecisionKind,
    dispatch_occurred: bool,
    *,
    block_reason: str = "",
    verified_head: str = "",
    verified_branch: str = "",
    auto_chain_count: int = 0,
    event_store: EngineeringEventStore | None = None,
    clock: Callable[[], datetime] | None = None,
) -> None:
    """Append a SupervisorAutoDispatchAttempt event to the event store."""
    if event_store is None:
        return

    now = (clock or _default_clock)()
    event = EngineeringEvent(
        event_id=f"auto-dispatch-{task_id}-{now.isoformat()}",
        event_type=EventType.SUPERVISOR_AUTO_DISPATCH_ATTEMPT,
        severity=EventSeverity.INFO,
        occurred_at=now.isoformat(),
        task_id=task_id,
        workflow_id=task_id,  # workflow_id == task_id in single-project setup
        payload={
            "decision_kind": decision_kind.value,
            "dispatch_occurred": dispatch_occurred,
            "block_reason": block_reason,
            "verified_head": verified_head,
            "verified_branch": verified_branch,
            "auto_chain_count": auto_chain_count,
        },
    )
    try:
        event_store.append(event)
    except Exception:
        # Audit failure must not block dispatch
        pass


# ---------------------------------------------------------------------------
# Safety predicates
# ---------------------------------------------------------------------------

def _check_destructive_instruction(instruction: str) -> str | None:
    """Return a block reason if instruction contains destructive operations."""
    lines = instruction.splitlines()
    for line in lines:
        stripped = line.strip()
        if _DESTRUCTIVE_GIT_PATTERNS.match(stripped):
            return f"destructive git command in instruction: {stripped[:60]!r}"
        if _DESTRUCTIVE_FS_PATTERNS.match(stripped):
            return f"destructive filesystem command in instruction: {stripped[:60]!r}"
        if _DB_WRITE_PATTERNS.match(stripped):
            return f"database write in instruction: {stripped[:60]!r}"
        if _LIVE_TRADING_PATTERNS.search(stripped):
            return f"live trading/brokerage action in instruction: {stripped[:60]!r}"
    return None


def _check_scope_drift(
    changed_files: tuple[str, ...],
    allowed_areas: tuple[str, ...],
) -> bool:
    """Return True if any changed file is outside allowed areas."""
    if not allowed_areas:
        return True
    for f in changed_files:
        if not any(f.startswith(area) for area in allowed_areas):
            return True
    return False


def _pre_dispatch_safety_check(
    packet: CompletionPacket,
    bundle,  # EvidenceBundle — type imported lazily to avoid circular
    decision: SupervisorDecision,
    auto_chain_count: int,
) -> tuple[bool, str]:
    """Return (allowed, block_reason). All checks must pass."""
    # Kill switch
    if not _is_auto_dispatch_enabled():
        return False, "kill switch: ENGSUP_AUTO_DISPATCH_ENABLED=0"

    # Whitelist
    if decision.decision not in _AUTO_DISPATCH_WHITELIST:
        return False, f"decision {decision.decision.value!r} not in whitelist"

    # Human approval required
    if decision.human_approval_required:
        return False, "human approval required"

    # Chain limit
    if auto_chain_count > _AUTO_CHAIN_LIMIT:
        return False, f"auto-chain limit reached ({auto_chain_count}/{_AUTO_CHAIN_LIMIT})"

    # Git tree clean
    if bundle.git_tree_clean is False:
        return False, "dirty working tree"

    # HEAD match
    if bundle.git_head and packet.head_commit:
        if bundle.git_head != packet.head_commit:
            return False, f"HEAD mismatch: {packet.head_commit!r} != {bundle.git_head!r}"

    # Branch match
    if bundle.git_branch and packet.feature_branch:
        if bundle.git_branch != packet.feature_branch:
            return False, f"branch mismatch: {packet.feature_branch!r} != {bundle.git_branch!r}"

    # Evidence conflicts
    if decision.evidence_conflicts:
        conflicts = [
            f"{c.field_label}: {c.source_a}={c.value_a!r} vs {c.source_b}={c.value_b!r}"
            for c in decision.evidence_conflicts
        ]
        return False, f"evidence conflict: {'; '.join(conflicts)}"

    # Stale evidence
    if decision.stale_evidence:
        return False, f"stale evidence: {', '.join(decision.stale_evidence)}"

    # Scope drift
    if _check_scope_drift(packet.changed_files, packet.allowed_areas):
        return False, f"scope drift: changed files include paths outside allowed areas {packet.allowed_areas!r}"

    # RETRY-specific bounds
    if decision.decision == SupervisorDecisionKind.RETRY:
        if packet.retry_count >= _RETRY_MAX_RETRY_COUNT:
            return False, f"retry_count {packet.retry_count} >= {_RETRY_MAX_RETRY_COUNT}"
        if packet.same_qa_failure_count >= _RETRY_MAX_SAME_FAILURE:
            return False, f"same_failure {packet.same_qa_failure_count} >= {_RETRY_MAX_SAME_FAILURE}"
        # Timed-out retry is not safe
        if getattr(packet, "qa_timed_out", False):
            return False, "qa_timed_out=True on RETRY; not safe to auto-dispatch"

    # Generated instruction safety
    if decision.generated_instruction:
        block = _check_destructive_instruction(decision.generated_instruction)
        if block:
            return False, block

    # Missing instruction
    if not decision.generated_instruction:
        return False, "no generated instruction; cannot dispatch"

    return True, ""


# ---------------------------------------------------------------------------
# Workflow state advancement helpers
# ---------------------------------------------------------------------------

def _load_workflow(
    task_id: str,
    workflow_store_path: Path,
) -> StoredWorkflow:
    """Load current workflow from store. Raises if not found."""
    store = WorkflowStore(workflow_store_path)
    if not store.exists():
        raise FileNotFoundError(f"No workflow found for task {task_id!r}")
    return store.load()


def _save_workflow(
    workflow: StoredWorkflow,
    workflow_store_path: Path,
) -> None:
    """Save updated workflow to store."""
    store = WorkflowStore(workflow_store_path)
    store.save(workflow)


def _dispatch(
    workflow: StoredWorkflow,
) -> StoredWorkflow:
    """Advance workflow by one step. Returns updated workflow."""
    return dispatch_workflow(workflow)


# ---------------------------------------------------------------------------
# Dispatch action handlers
# ---------------------------------------------------------------------------

def _do_run_qa(
    packet: CompletionPacket,
    bundle,  # EvidenceBundle
    event_store: EngineeringEventStore | None,
    workflow_store_path: Path,
    clock: Callable[[], datetime],
) -> tuple[StoredWorkflow | None, str]:
    """Advance workflow from WAIT_FOR_AGENT through QA to REVIEW.

    Precondition: packet.workflow_state == WAIT_FOR_AGENT and
                  packet.delegation_status == COMPLETE.
    """
    workflow = _load_workflow(packet.task_id, workflow_store_path)

    # Update delegation with results from packet
    delegation = DelegationRecord(
        run_id="",
        agent_name=packet.agent_name,
        started_at="",
        status=DelegationStatus.COMPLETE,
        request_id=None,
        updated_at=None,
        deadline_at=None,
        stdout_path=None,
        stderr_path=None,
        exit_code=packet.delegation_exit_code,
        completed_at=now.isoformat() if (now := clock()) else None,
        failure_reason=packet.delegation_failure_reason,
    )
    updated = replace(workflow, delegation=delegation)

    # QA step: set qa record from packet if available
    if packet.qa_exit_code is not None:
        qa_record = QARecord(
            command=("pytest",),
            exit_code=packet.qa_exit_code,
            duration_seconds=0.0,
            passed_count=packet.qa_passed_count,
            failed_count=packet.qa_failed_count,
            timed_out=packet.qa_timed_out,
        )
        updated = replace(updated, qa=qa_record, state=WorkflowState.QA)

    _save_workflow(updated, workflow_store_path)

    # Advance one step — runs QA handler if qa is set, otherwise waits for agent
    result = _dispatch(updated)
    return result, ""


def _do_run_read_only_review(
    packet: CompletionPacket,
    bundle,  # EvidenceBundle
    event_store: EngineeringEventStore | None,
    workflow_store_path: Path,
    clock: Callable[[], datetime],
) -> tuple[StoredWorkflow | None, str]:
    """Advance workflow from QA through REVIEW to REPORT.

    Precondition: packet.workflow_state == QA and QA has passed.
    """
    workflow = _load_workflow(packet.task_id, workflow_store_path)

    # Set QA record from packet
    qa_record = QARecord(
        command=("pytest",),
        exit_code=packet.qa_exit_code if packet.qa_exit_code is not None else 0,
        duration_seconds=0.0,
        passed_count=packet.qa_passed_count,
        failed_count=packet.qa_failed_count,
        timed_out=packet.qa_timed_out,
    )

    # REVIEW step: set review to None so review.py runs the reviewer
    updated = replace(
        workflow,
        qa=qa_record,
        state=WorkflowState.QA,  # ensure we're in QA state
    )
    _save_workflow(updated, workflow_store_path)

    # Advance one step — QA → REVIEW
    result = _dispatch(updated)

    # If review was generated in this step, advance to REPORT
    if result.state == WorkflowState.REVIEW and result.review is not None:
        _save_workflow(result, workflow_store_path)
        result = _dispatch(result)

    return result, ""


def _do_retry(
    packet: CompletionPacket,
    bundle,  # EvidenceBundle
    event_store: EngineeringEventStore | None,
    workflow_store_path: Path,
    clock: Callable[[], datetime],
) -> tuple[StoredWorkflow | None, str]:
    """Re-dispatch the same task for a bounded retry.

    Precondition: packet.delegation_status in (FAILED, TIMED_OUT) and
                  retry_count < 3 and same_failure < 2.
    """
    workflow = _load_workflow(packet.task_id, workflow_store_path)

    # Reset delegation to PENDING for re-run
    delegation = DelegationRecord(
        run_id="",
        agent_name=packet.agent_name,
        started_at="",
        status=DelegationStatus.PENDING,
        request_id=None,
        updated_at=None,
        deadline_at=None,
        stdout_path=None,
        stderr_path=None,
        exit_code=None,
        completed_at=None,
        failure_reason="",
    )
    updated = replace(
        workflow,
        delegation=delegation,
        state=WorkflowState.WAIT_FOR_AGENT,
    )
    _save_workflow(updated, workflow_store_path)

    # Advance one step
    result = _dispatch(updated)
    return result, ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def supervise_and_auto_dispatch(
    packet: CompletionPacket,
    *,
    repo_root: Path | None = None,
    event_store: EngineeringEventStore | None = None,
    clock: Callable[[], datetime] | None = None,
) -> SupervisorDecision:
    """Evaluate packet, run safety checks, auto-dispatch if all pass.

    Returns the SupervisorDecision with updated human_approval_required:
      - False  = auto-dispatch succeeded (or was blocked by safety check)
      - True   = manual approval required

    On auto-dispatch success: dispatches the next workflow step.
    On safety block: records audit event with block_reason; decision is
    returned with human_approval_required=True for Josh to handle manually.

    Does NOT raise on dispatch errors — returns decision with blockers set.
    """
    repo = (repo_root or Path.cwd()).resolve()

    # Produce supervisor decision (read-only)
    supervisor = Supervisor(repo_root=repo)
    decision = supervisor.supervise(packet)

    # Build evidence bundle for safety checks
    bundle = supervisor._build_bundle(packet)

    # Chain count for this task
    chain_count = _get_chain_count(packet.task_id)

    # Pre-dispatch safety check
    allowed, block_reason = _pre_dispatch_safety_check(
        packet, bundle, decision, chain_count
    )

    # Record audit event for every dispatch attempt (success or block)
    _record_audit(
        task_id=packet.task_id,
        decision_kind=decision.decision,
        dispatch_occurred=False,  # always False here; set by dispatch handler
        block_reason=block_reason if not allowed else "",
        verified_head=bundle.git_head or "",
        verified_branch=bundle.git_branch or "",
        auto_chain_count=chain_count,
        event_store=event_store,
        clock=clock,
    )

    if not allowed:
        # Safety check failed — return decision with block reason for manual handling
        return replace(
            decision,
            human_approval_required=True,
            blockers=(*decision.blockers, f"[auto-dispatch blocked] {block_reason}"),
        )

    # Safety passed — dispatch
    workflow_store_path = repo / "engineering" / "workflow_store.json"

    dispatch_error = ""
    try:
        if decision.decision == SupervisorDecisionKind.RUN_QA:
            result, dispatch_error = _do_run_qa(
                packet, bundle, event_store, workflow_store_path,
                clock or _default_clock,
            )
        elif decision.decision == SupervisorDecisionKind.RUN_READ_ONLY_REVIEW:
            result, dispatch_error = _do_run_read_only_review(
                packet, bundle, event_store, workflow_store_path,
                clock or _default_clock,
            )
        elif decision.decision == SupervisorDecisionKind.RETRY:
            result, dispatch_error = _do_retry(
                packet, bundle, event_store, workflow_store_path,
                clock or _default_clock,
            )
        else:
            dispatch_error = f"unhandled whitelisted decision: {decision.decision.value}"

        if dispatch_error:
            return replace(
                decision,
                human_approval_required=True,
                blockers=(*decision.blockers, f"[dispatch error] {dispatch_error}"),
            )

        # Dispatch succeeded — increment chain count
        new_count = _increment_chain_count(packet.task_id)

        # Record successful dispatch audit
        _record_audit(
            task_id=packet.task_id,
            decision_kind=decision.decision,
            dispatch_occurred=True,
            block_reason="",
            verified_head=bundle.git_head or "",
            verified_branch=bundle.git_branch or "",
            auto_chain_count=new_count,
            event_store=event_store,
            clock=clock,
        )

        # Chain limit reached after this dispatch
        if new_count >= _AUTO_CHAIN_LIMIT:
            _reset_chain_count(packet.task_id)
            return replace(
                decision,
                human_approval_required=True,
                warnings=(
                    *decision.warnings,
                    f"Auto-chain limit {_AUTO_CHAIN_LIMIT} reached; "
                    "next decision requires Josh approval.",
                ),
                supervisor_note=(
                    f"{decision.supervisor_note} "
                    f"Auto-chain limit reached after {new_count} dispatches."
                ),
            )

        # Decision stays with human_approval_required=False (dispatch succeeded)
        return decision

    except Exception as exc:
        dispatch_error = f"{type(exc).__name__}: {exc}"
        return replace(
            decision,
            human_approval_required=True,
            blockers=(*decision.blockers, f"[dispatch exception] {dispatch_error}"),
        )
