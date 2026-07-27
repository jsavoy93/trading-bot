import json
from pathlib import Path

import pytest

from engineering.models import WorkflowState
from engineering.workflow_store import StoredWorkflow, WorkflowStore


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
