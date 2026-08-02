from datetime import UTC, datetime

import pytest

from engineering.models import DelegationStatus, WorkflowState
from engineering.workflow.wait_for_agent import run
from engineering.workflow_store import DelegationRecord, StoredWorkflow


class StubMonitor:
    def __init__(self, status: DelegationStatus):
        self.returned_status = status
        self.calls: list[str] = []

    def status(self, run_id: str) -> DelegationStatus:
        self.calls.append(run_id)
        return self.returned_status


def make_workflow(
    status: DelegationStatus = DelegationStatus.ACTIVE,
) -> StoredWorkflow:
    return StoredWorkflow(
        task_id="TEST-001",
        feature_branch="agent/test-001",
        state=WorkflowState.WAIT_FOR_AGENT,
        delegation=DelegationRecord(
            run_id="run-123",
            agent_name="trading-exec",
            started_at="2026-08-02T15:00:00+00:00",
            status=status,
            request_id="request-123",
        ),
    )


@pytest.mark.parametrize(
    "status",
    (
        DelegationStatus.PENDING,
        DelegationStatus.ACTIVE,
    ),
)
def test_noncomplete_status_is_persisted_without_advancing(
    status: DelegationStatus,
) -> None:
    workflow = make_workflow()
    monitor = StubMonitor(status)

    result = run(
        workflow,
        monitor=monitor,
        clock=lambda: datetime(2026, 8, 2, 15, 5, tzinfo=UTC),
    )

    assert monitor.calls == ["run-123"]
    assert result.state is WorkflowState.WAIT_FOR_AGENT
    assert result.delegation is not None
    assert result.delegation.status is status
    assert result.delegation.updated_at == "2026-08-02T15:05:00+00:00"
    assert result.delegation.started_at == workflow.delegation.started_at
    assert result.delegation.request_id == workflow.delegation.request_id


@pytest.mark.parametrize(
    "status",
    (DelegationStatus.FAILED, DelegationStatus.TIMED_OUT),
)
def test_new_terminal_status_is_persisted_without_advancing(
    status: DelegationStatus,
) -> None:
    workflow = make_workflow()

    result = run(workflow, monitor=StubMonitor(status))

    assert result.state is WorkflowState.WAIT_FOR_AGENT
    assert result.delegation is not None
    assert result.delegation.status is status


@pytest.mark.parametrize(
    "status",
    (DelegationStatus.FAILED, DelegationStatus.TIMED_OUT),
)
def test_persisted_terminal_status_stops_without_polling(
    status: DelegationStatus,
) -> None:
    monitor = StubMonitor(DelegationStatus.ACTIVE)
    workflow = make_workflow(status)

    result = run(workflow, monitor=monitor)

    assert result is workflow
    assert monitor.calls == []


def test_complete_status_advances_to_qa() -> None:
    workflow = make_workflow()

    result = run(workflow, monitor=StubMonitor(DelegationStatus.COMPLETE))

    assert workflow.state is WorkflowState.WAIT_FOR_AGENT
    assert result.state is WorkflowState.QA
    assert result.delegation is not None
    assert result.delegation.status is DelegationStatus.COMPLETE


def test_wait_requires_delegation_metadata() -> None:
    workflow = StoredWorkflow(
        task_id="TEST-001",
        feature_branch="agent/test-001",
        state=WorkflowState.WAIT_FOR_AGENT,
    )

    with pytest.raises(RuntimeError, match="requires persisted delegation metadata"):
        run(workflow, monitor=StubMonitor(DelegationStatus.ACTIVE))


def test_wait_rejects_wrong_workflow_state() -> None:
    workflow = make_workflow()
    wrong_state = StoredWorkflow(
        task_id=workflow.task_id,
        feature_branch=workflow.feature_branch,
        state=WorkflowState.QA,
        delegation=workflow.delegation,
    )

    with pytest.raises(RuntimeError, match="received workflow state: QA"):
        run(wrong_state, monitor=StubMonitor(DelegationStatus.ACTIVE))
