from __future__ import annotations

from engineering.models import WorkflowState
from engineering.workflow.discover import run
from engineering.workflow_store import StoredWorkflow


def test_discover_advances_to_plan(capsys) -> None:
    workflow = StoredWorkflow(
        task_id="TEST-001",
        feature_branch="agent/test-001",
        state=WorkflowState.DISCOVER,
    )

    result = run(workflow)

    assert result.state is WorkflowState.PLAN
    assert capsys.readouterr().out == (
        "Executing workflow state: DISCOVER\n"
    )
