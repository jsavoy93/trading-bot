from __future__ import annotations

from collections.abc import Callable

from engineering.models import WorkflowState
from engineering.workflow import delegate, discover, plan, prepare_branch
from engineering.workflow_store import StoredWorkflow

WorkflowHandler = Callable[[StoredWorkflow], StoredWorkflow]


def _report_state(workflow: StoredWorkflow) -> StoredWorkflow:
    print(f"Executing workflow state: {workflow.state.value}")
    return workflow


_STATE_HANDLERS: dict[WorkflowState, WorkflowHandler] = {
    WorkflowState.DISCOVER: discover.run,
    WorkflowState.PLAN: plan.run,
    WorkflowState.PREPARE_BRANCH: prepare_branch.run,
    WorkflowState.DELEGATE: delegate.run,
    WorkflowState.WAIT_FOR_AGENT: _report_state,
    WorkflowState.QA: _report_state,
    WorkflowState.REVIEW: _report_state,
    WorkflowState.REPORT: _report_state,
    WorkflowState.COMPLETE: _report_state,
}


def dispatch_workflow(workflow: StoredWorkflow) -> StoredWorkflow:
    try:
        handler = _STATE_HANDLERS[workflow.state]
    except KeyError as exc:
        raise RuntimeError(
            f"No workflow handler registered for state: {workflow.state}"
        ) from exc

    return handler(workflow)
