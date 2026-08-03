import json
from pathlib import Path

import pytest

from engineering.models import (
    CriterionStatus,
    DelegationStatus,
    ReviewRecommendation,
    WorkflowState,
)
from engineering.reviewer import CriterionEvidence
from engineering.workflow_store import (
    DelegationRecord,
    QARecord,
    ReportRecord,
    ReviewRecord,
    StoredWorkflow,
    WorkflowStore,
)


def test_save_and_load_workflow(tmp_path: Path) -> None:
    state_path = tmp_path / "workflow.json"
    store = WorkflowStore(state_path)

    workflow = StoredWorkflow(
        task_id="TEST-001",
        feature_branch="agent/test-001",
        state=WorkflowState.PREPARE_BRANCH,
    )

    store.save(workflow)

    assert store.exists() is True
    assert store.load() == workflow


def test_save_replaces_existing_workflow(tmp_path: Path) -> None:
    state_path = tmp_path / "workflow.json"
    store = WorkflowStore(state_path)

    store.save(
        StoredWorkflow(
            task_id="TEST-001",
            feature_branch="agent/test-001",
            state=WorkflowState.DISCOVER,
        )
    )

    updated = StoredWorkflow(
        task_id="TEST-001",
        feature_branch="agent/test-001",
        state=WorkflowState.QA,
    )

    store.save(updated)

    assert store.load() == updated


def test_clear_removes_workflow_state(tmp_path: Path) -> None:
    state_path = tmp_path / "workflow.json"
    store = WorkflowStore(state_path)

    store.save(
        StoredWorkflow(
            task_id="TEST-001",
            feature_branch="agent/test-001",
            state=WorkflowState.COMPLETE,
        )
    )

    store.clear()

    assert store.exists() is False

    with pytest.raises(FileNotFoundError):
        store.load()


def test_archive_completed_preserves_evidence(tmp_path: Path) -> None:
    from tests.test_engineering_complete import completed_workflow

    workflow = completed_workflow()
    store = WorkflowStore(tmp_path / "workflow.json")

    archive_path = store.archive_completed(workflow)

    assert archive_path.parent == tmp_path / "engineering-reports"
    assert WorkflowStore(archive_path).load() == workflow


def test_archive_completed_rejects_incomplete_workflow(tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path / "workflow.json")

    with pytest.raises(RuntimeError, match="Only a completed workflow"):
        store.archive_completed(
            StoredWorkflow("TEST-001", "agent/test", WorkflowState.REPORT)
        )


def test_load_rejects_invalid_workflow_structure(tmp_path: Path) -> None:
    state_path = tmp_path / "workflow.json"
    state_path.write_text(
        json.dumps(
            {
                "task_id": "TEST-001",
                "state": "QA",
            }
        ),
        encoding="utf-8",
    )

    store = WorkflowStore(state_path)

    with pytest.raises(
        RuntimeError,
        match="Invalid workflow state file",
    ):
        store.load()


def test_load_rejects_unknown_workflow_state(tmp_path: Path) -> None:
    state_path = tmp_path / "workflow.json"
    state_path.write_text(
        json.dumps(
            {
                "task_id": "TEST-001",
                "feature_branch": "agent/test-001",
                "state": "UNKNOWN",
            }
        ),
        encoding="utf-8",
    )

    store = WorkflowStore(state_path)

    with pytest.raises(
        RuntimeError,
        match="Invalid workflow state file",
    ):
        store.load()


def test_save_and_load_delegation_metadata(tmp_path: Path) -> None:
    state_path = tmp_path / "workflow.json"
    store = WorkflowStore(state_path)
    workflow = StoredWorkflow(
        task_id="TEST-001",
        feature_branch="agent/test-001",
        state=WorkflowState.WAIT_FOR_AGENT,
        delegation=DelegationRecord(
            run_id="run-123",
            agent_name="trading-exec",
            started_at="2026-08-02T15:00:00+00:00",
            status=DelegationStatus.ACTIVE,
            request_id="request-123",
            updated_at="2026-08-02T15:01:00+00:00",
            deadline_at="2026-08-02T15:30:00+00:00",
            stdout_path="/tmp/run-123/stdout.log",
            stderr_path="/tmp/run-123/stderr.log",
        ),
    )

    store.save(workflow)

    assert store.load() == workflow
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert payload["delegation"] == {
        "agent_name": "trading-exec",
        "run_id": "run-123",
        "request_id": "request-123",
        "started_at": "2026-08-02T15:00:00+00:00",
        "status": "ACTIVE",
        "updated_at": "2026-08-02T15:01:00+00:00",
        "deadline_at": "2026-08-02T15:30:00+00:00",
        "stdout_path": "/tmp/run-123/stdout.log",
        "stderr_path": "/tmp/run-123/stderr.log",
    }


def test_loads_legacy_delegation_without_ops_012_fields(tmp_path: Path) -> None:
    state_path = tmp_path / "workflow.json"
    state_path.write_text(
        json.dumps(
            {
                "task_id": "TEST-001",
                "feature_branch": "agent/test-001",
                "state": "WAIT_FOR_AGENT",
                "delegation": {
                    "run_id": "run-123",
                    "agent_name": "trading-exec",
                    "started_at": "2026-08-02T15:00:00+00:00",
                    "status": "ACTIVE",
                },
            }
        ),
        encoding="utf-8",
    )

    loaded = WorkflowStore(state_path).load()

    assert loaded.delegation is not None
    assert loaded.delegation.request_id is None
    assert loaded.delegation.deadline_at is None


def test_loads_legacy_workflow_without_delegation_metadata(tmp_path: Path) -> None:
    state_path = tmp_path / "workflow.json"
    state_path.write_text(
        json.dumps(
            {
                "task_id": "TEST-001",
                "feature_branch": "agent/test-001",
                "state": "PLAN",
            }
        ),
        encoding="utf-8",
    )

    loaded = WorkflowStore(state_path).load()

    assert loaded.delegation is None


def test_load_rejects_invalid_delegation_metadata(tmp_path: Path) -> None:
    state_path = tmp_path / "workflow.json"
    state_path.write_text(
        json.dumps(
            {
                "task_id": "TEST-001",
                "feature_branch": "agent/test-001",
                "state": "WAIT_FOR_AGENT",
                "delegation": {"run_id": "run-123"},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="Invalid workflow state file"):
        WorkflowStore(state_path).load()


def test_save_and_load_qa_evidence(tmp_path: Path) -> None:
    state_path = tmp_path / "workflow.json"
    workflow = StoredWorkflow(
        task_id="TEST-001",
        feature_branch="agent/test-001",
        state=WorkflowState.REVIEW,
        qa=QARecord(
            command=("python", "-m", "pytest"),
            exit_code=0,
            duration_seconds=1.25,
            output_summary="3 passed",
            changed_files=("src/example.py",),
            completed_at="2026-08-02T16:15:00+00:00",
            passed_count=3,
            failed_count=0,
        ),
    )

    WorkflowStore(state_path).save(workflow)

    assert WorkflowStore(state_path).load() == workflow


def test_loads_legacy_workflow_without_qa_evidence(tmp_path: Path) -> None:
    state_path = tmp_path / "workflow.json"
    state_path.write_text(
        json.dumps(
            {
                "task_id": "TEST-001",
                "feature_branch": "agent/test-001",
                "state": "PLAN",
            }
        ),
        encoding="utf-8",
    )

    assert WorkflowStore(state_path).load().qa is None


def test_load_rejects_invalid_qa_evidence(tmp_path: Path) -> None:
    state_path = tmp_path / "workflow.json"
    state_path.write_text(
        json.dumps(
            {
                "task_id": "TEST-001",
                "feature_branch": "agent/test-001",
                "state": "QA",
                "qa": {
                    "command": "python -m pytest",
                    "exit_code": 0,
                    "duration_seconds": 1.0,
                    "output_summary": "passed",
                    "changed_files": [],
                    "completed_at": "2026-08-02T16:15:00+00:00",
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="Invalid workflow state file"):
        WorkflowStore(state_path).load()


def test_save_and_load_review_evidence(tmp_path: Path) -> None:
    state_path = tmp_path / "workflow.json"
    workflow = StoredWorkflow(
        "TEST-001",
        "agent/test-001",
        WorkflowState.REPORT,
        review=ReviewRecord(
            criteria=(
                CriterionEvidence(
                    "Criterion one",
                    "pytest -k one",
                    "1 passed",
                    CriterionStatus.PASS,
                ),
            ),
            recommendation=ReviewRecommendation.ACCEPT,
            completed_at="2026-08-02T16:25:00+00:00",
        ),
    )

    WorkflowStore(state_path).save(workflow)

    assert WorkflowStore(state_path).load() == workflow


def test_loads_legacy_workflow_without_review_evidence(tmp_path: Path) -> None:
    state_path = tmp_path / "workflow.json"
    state_path.write_text(
        json.dumps(
            {"task_id": "TEST-001", "feature_branch": "agent/test-001", "state": "QA"}
        ),
        encoding="utf-8",
    )

    assert WorkflowStore(state_path).load().review is None


def test_load_rejects_malformed_review_evidence(tmp_path: Path) -> None:
    state_path = tmp_path / "workflow.json"
    state_path.write_text(
        json.dumps(
            {
                "task_id": "TEST-001",
                "feature_branch": "agent/test-001",
                "state": "REVIEW",
                "review": {
                    "criteria": [
                        {
                            "criterion": "Criterion one",
                            "proof_method": 123,
                            "exact_result": "1 passed",
                            "status": "PASS",
                        }
                    ],
                    "recommendation": "ACCEPT",
                    "completed_at": "2026-08-02T16:25:00+00:00",
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="Invalid workflow state file"):
        WorkflowStore(state_path).load()


def test_save_and_load_report_evidence(tmp_path: Path) -> None:
    state_path = tmp_path / "workflow.json"
    criterion = CriterionEvidence(
        "Criterion one", "pytest -k one", "1 passed", CriterionStatus.PASS
    )
    workflow = StoredWorkflow(
        "TEST-001",
        "agent/test-001",
        WorkflowState.COMPLETE,
        report=ReportRecord(
            "TEST-001", "Example", "agent/test-001", "trading-exec", 60.0,
            ("src/a.py",), ("python", "-m", "pytest"), 0, 1, 0,
            (criterion,), ("Human approval required.",),
            ReviewRecommendation.ACCEPT, "Request approval", "2026-08-02T16:30:00+00:00",
            "Task: TEST-001\n",
        ),
    )

    WorkflowStore(state_path).save(workflow)

    assert WorkflowStore(state_path).load() == workflow


def test_loads_legacy_workflow_without_report_evidence(tmp_path: Path) -> None:
    state_path = tmp_path / "workflow.json"
    state_path.write_text(
        json.dumps({"task_id": "TEST-001", "feature_branch": "agent/test", "state": "REPORT"}),
        encoding="utf-8",
    )
    assert WorkflowStore(state_path).load().report is None


def test_load_rejects_malformed_report_evidence(tmp_path: Path) -> None:
    state_path = tmp_path / "workflow.json"
    workflow = StoredWorkflow("TEST-001", "agent/test", WorkflowState.COMPLETE)
    WorkflowStore(state_path).save(workflow)
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    payload["report"] = {"changed_files": "src/a.py"}
    state_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RuntimeError, match="Invalid workflow state file"):
        WorkflowStore(state_path).load()
