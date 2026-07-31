from __future__ import annotations

import pytest

from engineering.models import WorkflowState
from engineering.workflow_engine import dispatch_workflow
from engineering.workflow_store import StoredWorkflow


@pytest.mark.parametrize("state", tuple(WorkflowState))
def test_dispatch_workflow_handles_every_state(
    state: WorkflowState,
    capsys: pytest.CaptureFixture[str],
) -> None:
    feature_branch = (
        "agent/test-001-prevent-live-brokerage-calls-from-tests"
        if state is WorkflowState.PLAN
        else "agent/test-001-example"
    )
    workflow = StoredWorkflow(
        task_id="TEST-001",
        feature_branch=feature_branch,
        state=state,
    )

    result = dispatch_workflow(workflow)

    if state is WorkflowState.DISCOVER:
        expected_state = WorkflowState.PLAN
    elif state is WorkflowState.PLAN:
        expected_state = WorkflowState.PREPARE_BRANCH
    else:
        expected_state = state

    assert result.task_id == workflow.task_id
    assert result.feature_branch == workflow.feature_branch
    assert result.state is expected_state
    output = capsys.readouterr().out
    assert output.startswith(f"Executing workflow state: {state.value}\n")

    if state is WorkflowState.PLAN:
        assert "Execution plan\n" in output
        assert "Task: TEST-001 — Prevent live brokerage calls from tests" in output
    else:
        assert output == f"Executing workflow state: {state.value}\n"
