from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from engineering.models import DelegationStatus, WorkflowState
from engineering.qa_runner import QAExecution, run_qa
from engineering.workflow_store import QARecord, StoredWorkflow


def run(
    workflow: StoredWorkflow,
    *,
    repo_root: Path | None = None,
    runner: Callable[[Path], QAExecution] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> StoredWorkflow:
    if workflow.state is not WorkflowState.QA:
        raise RuntimeError(f"QA handler received workflow state: {workflow.state.value}")
    if workflow.delegation is None or workflow.delegation.status is not DelegationStatus.COMPLETE:
        raise RuntimeError("QA requires a completed delegated run.")
    if workflow.qa is not None:
        return workflow

    print(f"Executing workflow state: {workflow.state.value}")
    execution = (runner or run_qa)(repo_root)
    completed_at = (
        (clock or (lambda: datetime.now(UTC)))().astimezone(UTC).isoformat()
    )
    qa_record = QARecord(
        command=execution.command,
        exit_code=execution.exit_code,
        duration_seconds=execution.duration_seconds,
        output_summary=execution.output_summary,
        changed_files=execution.changed_files,
        completed_at=completed_at,
        timed_out=execution.timed_out,
        passed_count=execution.passed_count,
        failed_count=execution.failed_count,
    )
    succeeded = execution.exit_code == 0 and not execution.timed_out
    next_state = WorkflowState.REVIEW if succeeded else WorkflowState.QA

    print(f"Exit code: {execution.exit_code}")
    print(f"Duration: {execution.duration_seconds:.3f}s")
    print(f"Changed files: {len(execution.changed_files)}")
    print(f"Next state: {next_state.value}")
    return replace(workflow, state=next_state, qa=qa_record)
