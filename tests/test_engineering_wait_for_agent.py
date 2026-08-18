from datetime import UTC, datetime
from pathlib import Path

import pytest

from engineering.executor import AgentRun
from engineering.models import DelegationStatus, WorkflowState
from engineering.workflow.wait_for_agent import run
from engineering.workflow_store import DelegationRecord, StoredWorkflow


class StubMonitor:
    def __init__(self, status: DelegationStatus):
        self.returned_status = status
        self.calls: list[str] = []

    def status(self, run_id: str) -> AgentRun:
        self.calls.append(run_id)
        terminal = self.returned_status in {
            DelegationStatus.COMPLETE,
            DelegationStatus.FAILED,
            DelegationStatus.TIMED_OUT,
        }
        exit_code = None
        if self.returned_status is DelegationStatus.COMPLETE:
            exit_code = 0
        elif self.returned_status is DelegationStatus.TIMED_OUT:
            exit_code = 124
        elif self.returned_status is DelegationStatus.FAILED:
            exit_code = 17
        return AgentRun(
            request_id="request-123",
            run_id=run_id,
            agent_name="trading-exec",
            feature_branch="agent/test-001",
            status=self.returned_status,
            started_at="2026-08-02T15:00:00+00:00",
            updated_at="2026-08-02T15:05:00+00:00",
            deadline_at="2026-08-02T15:30:00+00:00",
            stdout_path="/tmp/run-123/stdout.log",
            stderr_path="/tmp/run-123/stderr.log",
            exit_code=exit_code,
            completed_at="2026-08-02T15:05:00+00:00" if terminal else None,
            failure_reason="failed" if exit_code not in {None, 0} else "",
        )


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
            updated_at="2026-08-02T15:00:00+00:00",
            deadline_at="2026-08-02T15:30:00+00:00",
            stdout_path="/tmp/run-123/stdout.log",
            stderr_path="/tmp/run-123/stderr.log",
            exit_code=(17 if status is DelegationStatus.FAILED else 124)
            if status in {DelegationStatus.FAILED, DelegationStatus.TIMED_OUT}
            else None,
            completed_at="2026-08-02T15:01:00+00:00"
            if status in {DelegationStatus.FAILED, DelegationStatus.TIMED_OUT}
            else None,
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
    tmp_path: Path,
    status: DelegationStatus,
) -> None:
    workflow = make_workflow()
    monitor = StubMonitor(status)

    result = run(
        workflow,
        repository_root=tmp_path,
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
    tmp_path: Path,
    status: DelegationStatus,
) -> None:
    workflow = make_workflow()

    result = run(workflow, repository_root=tmp_path, monitor=StubMonitor(status))

    assert result.state is WorkflowState.WAIT_FOR_AGENT
    assert result.delegation is not None
    assert result.delegation.status is status


@pytest.mark.parametrize(
    "status",
    (DelegationStatus.FAILED, DelegationStatus.TIMED_OUT),
)
def test_persisted_terminal_status_stops_without_polling(
    tmp_path: Path,
    status: DelegationStatus,
) -> None:
    monitor = StubMonitor(DelegationStatus.ACTIVE)
    workflow = make_workflow(status)

    result = run(workflow, repository_root=tmp_path, monitor=monitor)

    assert result is workflow
    assert monitor.calls == []


def test_complete_status_advances_to_qa(tmp_path: Path) -> None:
    workflow = make_workflow()

    result = run(workflow, repository_root=tmp_path, monitor=StubMonitor(DelegationStatus.COMPLETE))

    assert workflow.state is WorkflowState.WAIT_FOR_AGENT
    assert result.state is WorkflowState.QA
    assert result.delegation is not None
    assert result.delegation.status is DelegationStatus.COMPLETE


def test_wait_requires_delegation_metadata(tmp_path: Path) -> None:
    workflow = StoredWorkflow(
        task_id="TEST-001",
        feature_branch="agent/test-001",
        state=WorkflowState.WAIT_FOR_AGENT,
    )

    with pytest.raises(RuntimeError, match="requires persisted delegation metadata"):
        run(workflow, repository_root=tmp_path, monitor=StubMonitor(DelegationStatus.ACTIVE))


def test_wait_requires_complete_delegation_metadata(tmp_path: Path) -> None:
    workflow = make_workflow()
    incomplete = __import__("dataclasses").replace(
        workflow,
        delegation=__import__("dataclasses").replace(
            workflow.delegation,
            deadline_at=None,
        ),
    )
    with pytest.raises(RuntimeError, match="complete delegation metadata"):
        run(incomplete, repository_root=tmp_path, monitor=StubMonitor(DelegationStatus.ACTIVE))


def test_wait_rejects_mismatched_status_identity(tmp_path: Path) -> None:
    class MismatchedMonitor(StubMonitor):
        def status(self, run_id: str) -> AgentRun:
            return __import__("dataclasses").replace(
                super().status(run_id), request_id="other"
            )

    with pytest.raises(RuntimeError, match="request identity"):
        run(make_workflow(), repository_root=tmp_path, monitor=MismatchedMonitor(DelegationStatus.ACTIVE))


def test_wait_rejects_wrong_workflow_state(tmp_path: Path) -> None:
    workflow = make_workflow()
    wrong_state = StoredWorkflow(
        task_id=workflow.task_id,
        feature_branch=workflow.feature_branch,
        state=WorkflowState.QA,
        delegation=workflow.delegation,
    )

    with pytest.raises(RuntimeError, match="received workflow state: QA"):
        run(wrong_state, repository_root=tmp_path, monitor=StubMonitor(DelegationStatus.ACTIVE))
