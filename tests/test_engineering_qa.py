from datetime import UTC, datetime
from pathlib import Path

import pytest

from engineering.models import DelegationStatus, WorkflowState
from engineering.qa_runner import QAExecution
from engineering.workflow.qa import run
from engineering.workflow_store import DelegationRecord, QARecord, StoredWorkflow


def make_workflow(
    *,
    delegation_status: DelegationStatus = DelegationStatus.COMPLETE,
    qa: QARecord | None = None,
) -> StoredWorkflow:
    return StoredWorkflow(
        task_id="TEST-001",
        feature_branch="agent/test-001",
        state=WorkflowState.QA,
        delegation=DelegationRecord(
            run_id="run-123",
            agent_name="trading-exec",
            started_at="2026-08-02T16:00:00+00:00",
            status=delegation_status,
        ),
        qa=qa,
    )


def execution(exit_code: int = 0, timed_out: bool = False) -> QAExecution:
    return QAExecution(
        command=("python", "-m", "pytest"),
        exit_code=exit_code,
        duration_seconds=2.5,
        output_summary="118 passed",
        changed_files=("engineering/example.py",),
        timed_out=timed_out,
        passed_count=118 if exit_code == 0 else None,
        failed_count=1 if exit_code == 1 else None,
    )


def test_successful_qa_records_evidence_and_advances_to_review(
    tmp_path: Path,
) -> None:
    workflow = make_workflow()

    result = run(
        workflow,
        repo_root=tmp_path,
        runner=lambda root: execution(),
        clock=lambda: datetime(2026, 8, 2, 16, 15, tzinfo=UTC),
    )

    assert workflow.state is WorkflowState.QA
    assert workflow.qa is None
    assert result.state is WorkflowState.REVIEW
    assert result.qa == QARecord(
        command=("python", "-m", "pytest"),
        exit_code=0,
        duration_seconds=2.5,
        output_summary="118 passed",
        changed_files=("engineering/example.py",),
        completed_at="2026-08-02T16:15:00+00:00",
        timed_out=False,
        passed_count=118,
        failed_count=None,
    )


@pytest.mark.parametrize("result", (execution(1), execution(124, True)))
def test_failed_or_timed_out_qa_stays_in_qa(result: QAExecution) -> None:
    workflow = make_workflow()

    updated = run(workflow, runner=lambda root: result)

    assert updated.state is WorkflowState.QA
    assert updated.qa is not None
    assert updated.qa.exit_code == result.exit_code
    assert updated.qa.timed_out is result.timed_out


def test_persisted_qa_evidence_stops_without_rerunning() -> None:
    existing = QARecord(
        command=("python", "-m", "pytest"),
        exit_code=1,
        duration_seconds=1.0,
        output_summary="failed",
        changed_files=(),
        completed_at="2026-08-02T16:15:00+00:00",
    )
    calls: list[Path] = []
    workflow = make_workflow(qa=existing)

    result = run(workflow, runner=lambda root: calls.append(root))

    assert result is workflow
    assert calls == []


@pytest.mark.parametrize(
    "workflow",
    (
        StoredWorkflow("TEST-001", "agent/test-001", WorkflowState.QA),
        make_workflow(delegation_status=DelegationStatus.ACTIVE),
    ),
)
def test_qa_requires_completed_delegation(workflow: StoredWorkflow) -> None:
    with pytest.raises(RuntimeError, match="requires a completed delegated run"):
        run(workflow, runner=lambda root: execution())


def test_qa_rejects_wrong_workflow_state() -> None:
    workflow = make_workflow()
    wrong_state = StoredWorkflow(
        workflow.task_id,
        workflow.feature_branch,
        WorkflowState.REVIEW,
        delegation=workflow.delegation,
    )

    with pytest.raises(RuntimeError, match="received workflow state: REVIEW"):
        run(wrong_state, runner=lambda root: execution())
