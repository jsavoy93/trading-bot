from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from engineering.backlog import load_backlog
from engineering.models import BacklogTask, WorkflowState
from engineering.reporter import build_report
from engineering.workflow_store import ReportRecord, StoredWorkflow


def run(
    workflow: StoredWorkflow,
    *,
    backlog_path: Path | None = None,
    reporter: Callable[[StoredWorkflow, BacklogTask, datetime], ReportRecord] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> StoredWorkflow:
    if workflow.state is not WorkflowState.REPORT:
        raise RuntimeError(
            f"REPORT handler received workflow state: {workflow.state.value}"
        )
    if workflow.report is not None:
        return workflow

    print(f"Executing workflow state: {workflow.state.value}")
    tasks = load_backlog(backlog_path or Path.cwd() / "AGENT_BACKLOG.md")
    task = next((item for item in tasks if item.task_id == workflow.task_id), None)
    if task is None:
        raise RuntimeError(f"Cannot report unknown backlog task: {workflow.task_id}")
    generated_at = (clock or (lambda: datetime.now(UTC)))().astimezone(UTC)
    report = (reporter or build_report)(workflow, task, generated_at)

    print(report.rendered, end="")
    print("Next state: COMPLETE")
    return replace(workflow, state=WorkflowState.COMPLETE, report=report)
