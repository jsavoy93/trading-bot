from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from engineering.models import WorkflowState
from engineering.workflow import (
    complete,
    delegate,
    discover,
    plan,
    prepare_branch,
    qa,
    report,
    review,
    wait_for_agent,
)
from engineering.workflow_store import StoredWorkflow


# Kept for backward compatibility with tests that monkeypatch individual handlers.
WorkflowHandler = Callable[..., StoredWorkflow]

_STATE_HANDLERS: dict[WorkflowState, WorkflowHandler] = {
    WorkflowState.DISCOVER: discover.run,
    WorkflowState.PLAN: plan.run,
    WorkflowState.PREPARE_BRANCH: prepare_branch.run,
    WorkflowState.DELEGATE: delegate.run,
    WorkflowState.WAIT_FOR_AGENT: wait_for_agent.run,
    WorkflowState.QA: qa.run,
    WorkflowState.REVIEW: review.run,
    WorkflowState.REPORT: report.run,
    WorkflowState.COMPLETE: complete.run,
}


def dispatch_workflow(
    workflow: StoredWorkflow,
    *,
    repository_root: Path,
) -> StoredWorkflow:
    """Dispatch a workflow step using the handler for the current state.

    Parameters
    ----------
    workflow : StoredWorkflow
        The current workflow state.
    repository_root : Path
        The authoritative repository root for the active project.
        Must be absolute. All project-sensitive filesystem, Git, and QA
        operations derive from this root.

    Returns
    -------
    StoredWorkflow
        The updated workflow after the handler runs.

    Raises
    ------
    RuntimeError
        If no handler is registered for the workflow's current state.
    """
    handler = _STATE_HANDLERS.get(workflow.state)
    if handler is None:
        raise RuntimeError(
            f"No workflow handler registered for state: {workflow.state}"
        )

    repository_root = repository_root.resolve()

    match workflow.state:
        case WorkflowState.DISCOVER:
            return handler(workflow, repository_root=repository_root)
        case WorkflowState.PLAN:
            return handler(workflow, repository_root=repository_root)
        case WorkflowState.PREPARE_BRANCH:
            return handler(workflow, repo_root=repository_root)
        case WorkflowState.DELEGATE:
            return handler(workflow, repository_root=repository_root)
        case WorkflowState.WAIT_FOR_AGENT:
            return handler(workflow, repository_root=repository_root)
        case WorkflowState.QA:
            return handler(workflow, repo_root=repository_root)
        case WorkflowState.REVIEW:
            return handler(workflow, repository_root=repository_root)
        case WorkflowState.REPORT:
            return handler(workflow, repository_root=repository_root)
        case WorkflowState.COMPLETE:
            return handler(workflow, repository_root=repository_root)
        case _:
            # Should be unreachable; _STATE_HANDLERS.get already returned None
            raise RuntimeError(
                f"No workflow handler registered for state: {workflow.state}"
            )
