from __future__ import annotations

import pytest

from engineering.models import WorkflowState
from engineering.workflow import delegate, prepare_branch
from engineering.workflow_engine import _STATE_HANDLERS, dispatch_workflow
from engineering.workflow_store import StoredWorkflow


@pytest.mark.parametrize("state", tuple(WorkflowState))
def test_dispatch_workflow_handles_every_state(
    state: WorkflowState,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
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

    if state is WorkflowState.PREPARE_BRANCH:
        def prepare_stub(stored: StoredWorkflow) -> StoredWorkflow:
            print("Executing workflow state: PREPARE_BRANCH")
            return StoredWorkflow(
                task_id=stored.task_id,
                feature_branch=stored.feature_branch,
                state=WorkflowState.DELEGATE,
            )

        monkeypatch.setitem(_STATE_HANDLERS, state, prepare_stub)

    if state is WorkflowState.DELEGATE:
        def delegate_stub(stored: StoredWorkflow) -> StoredWorkflow:
            print("Executing workflow state: DELEGATE")
            return StoredWorkflow(
                task_id=stored.task_id,
                feature_branch=stored.feature_branch,
                state=WorkflowState.WAIT_FOR_AGENT,
            )

        monkeypatch.setitem(_STATE_HANDLERS, state, delegate_stub)

    result = dispatch_workflow(workflow)

    if state is WorkflowState.DISCOVER:
        expected_state = WorkflowState.PLAN
    elif state is WorkflowState.PLAN:
        expected_state = WorkflowState.PREPARE_BRANCH
    elif state is WorkflowState.PREPARE_BRANCH:
        expected_state = WorkflowState.DELEGATE
    elif state is WorkflowState.DELEGATE:
        expected_state = WorkflowState.WAIT_FOR_AGENT
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


def test_prepare_branch_handler_is_registered() -> None:
    assert _STATE_HANDLERS[WorkflowState.PREPARE_BRANCH] is prepare_branch.run


def test_delegate_handler_is_registered() -> None:
    assert _STATE_HANDLERS[WorkflowState.DELEGATE] is delegate.run
