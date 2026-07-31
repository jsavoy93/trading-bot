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
    workflow = StoredWorkflow(
        task_id="TEST-001",
        feature_branch="agent/test-001-example",
        state=state,
    )

    result = dispatch_workflow(workflow)

    expected_state = (
        WorkflowState.PLAN
        if state is WorkflowState.DISCOVER
        else state
    )

    assert result.task_id == workflow.task_id
    assert result.feature_branch == workflow.feature_branch
    assert result.state is expected_state
    assert capsys.readouterr().out == (
        f"Executing workflow state: {state.value}\n"
    )
