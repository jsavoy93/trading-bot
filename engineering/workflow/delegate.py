from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from engineering.backlog import load_backlog
from engineering.executor import (
    AgentLauncher,
    CommandAgentLauncher,
    build_agent_prompt,
    build_request_id,
    select_specialist,
)
from engineering.models import BacklogTask, WorkflowState
from engineering.workflow_store import DelegationRecord, StoredWorkflow


def _resolve_task(task_id: str, backlog_path: Path) -> BacklogTask:
    for task in load_backlog(backlog_path):
        if task.task_id == task_id:
            return task
    raise RuntimeError(f"Cannot delegate unknown backlog task: {task_id}")


def run(
    workflow: StoredWorkflow,
    *,
    backlog_path: Path | None = None,
    launcher: AgentLauncher | None = None,
    clock: Callable[[], datetime] | None = None,
) -> StoredWorkflow:
    if workflow.state is not WorkflowState.DELEGATE:
        raise RuntimeError(
            f"DELEGATE handler received workflow state: {workflow.state.value}"
        )
    if workflow.delegation is not None:
        raise RuntimeError(
            f"Refusing duplicate delegation for existing run {workflow.delegation.run_id!r}."
        )

    print(f"Executing workflow state: {workflow.state.value}")
    resolved_backlog = backlog_path or Path.cwd() / "AGENT_BACKLOG.md"
    task = _resolve_task(workflow.task_id, resolved_backlog)
    agent_name = select_specialist(task)
    prompt = build_agent_prompt(task, workflow.feature_branch)
    request_id = build_request_id(task.task_id, workflow.feature_branch)
    launched = (launcher or CommandAgentLauncher()).launch(
        agent_name,
        workflow.feature_branch,
        prompt,
        request_id,
    )
    started_at = (
        (clock or (lambda: datetime.now(UTC)))()
        .astimezone(UTC)
        .isoformat()
    )

    print(f"Agent: {agent_name}")
    print(f"Run ID: {launched.run_id}")
    print(f"Status: {launched.status.value}")

    return replace(
        workflow,
        state=WorkflowState.WAIT_FOR_AGENT,
        delegation=DelegationRecord(
            run_id=launched.run_id,
            agent_name=agent_name,
            started_at=started_at,
            status=launched.status,
            request_id=request_id,
            updated_at=started_at,
        ),
    )
