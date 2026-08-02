from __future__ import annotations

from collections.abc import Callable

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

WorkflowHandler = Callable[[StoredWorkflow], StoredWorkflow]


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


def dispatch_workflow(workflow: StoredWorkflow) -> StoredWorkflow:
    try:
        handler = _STATE_HANDLERS[workflow.state]
    except KeyError as exc:
        raise RuntimeError(
            f"No workflow handler registered for state: {workflow.state}"
        ) from exc

    return handler(workflow)
