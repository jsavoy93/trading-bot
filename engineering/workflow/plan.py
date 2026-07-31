from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from engineering.backlog import load_backlog
from engineering.git_service import GitService
from engineering.models import BacklogTask, RepositoryState, WorkflowState
from engineering.planner import build_execution_plan, build_feature_branch
from engineering.workflow_store import StoredWorkflow


def _resolve_task(
    task_id: str,
    backlog_path: Path,
) -> BacklogTask:
    for task in load_backlog(backlog_path):
        if task.task_id == task_id:
            return task

    raise RuntimeError(
        f"Cannot plan unknown backlog task: {task_id}"
    )


def _print_list(label: str, values: tuple[str, ...]) -> None:
    print(f"{label}:")

    if not values:
        print("- None specified")
        return

    for value in values:
        print(f"- {value}")


def run(
    workflow: StoredWorkflow,
    *,
    backlog_path: Path | None = None,
    repository: RepositoryState | None = None,
) -> StoredWorkflow:
    if workflow.state is not WorkflowState.PLAN:
        raise RuntimeError(
            f"PLAN handler received workflow state: {workflow.state.value}"
        )

    print(f"Executing workflow state: {workflow.state.value}")

    repo_root = Path.cwd()
    resolved_backlog_path = backlog_path or repo_root / "AGENT_BACKLOG.md"
    task = _resolve_task(workflow.task_id, resolved_backlog_path)
    expected_branch = build_feature_branch(task)

    if workflow.feature_branch != expected_branch:
        raise RuntimeError(
            "Stored feature branch does not match the backlog task: "
            f"stored={workflow.feature_branch!r}, expected={expected_branch!r}"
        )

    repository_state = repository or GitService(repo_root).repository_state()
    execution_plan = build_execution_plan(task, repository_state)

    print("Execution plan")
    print(f"Task: {task.task_id} — {task.title}")
    print(f"Priority: {task.priority.value}")
    print(f"Owner: {task.owner}")
    print(f"Feature branch: {execution_plan.feature_branch}")
    print(f"Risk: {execution_plan.risk.value}")
    print(f"Complexity: {execution_plan.complexity.value}")
    print(f"Repository: {repository_state.root}")
    print(f"Source branch: {repository_state.branch}")
    print(f"Repository clean: {repository_state.is_clean}")
    _print_list("Acceptance criteria", execution_plan.acceptance_criteria)
    _print_list("Allowed areas", execution_plan.allowed_areas)

    return replace(
        workflow,
        state=WorkflowState.PREPARE_BRANCH,
    )
