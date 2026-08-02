import json
from pathlib import Path

import pytest

from engineering.models import DelegationStatus, WorkflowState
from engineering.workflow_store import DelegationRecord, StoredWorkflow, WorkflowStore


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
    }


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
