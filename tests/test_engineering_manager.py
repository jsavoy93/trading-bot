from pathlib import Path

from engineering.manager import persist_workflow_result
from engineering.models import WorkflowState
from engineering.workflow_store import StoredWorkflow, WorkflowStore
from tests.test_engineering_complete import completed_workflow


def test_persist_noncomplete_workflow(tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path / "active.json")
    workflow = StoredWorkflow("TEST-001", "agent/test", WorkflowState.PLAN)

    assert persist_workflow_result(store, workflow) is None
    assert store.load() == workflow


def test_archive_and_clear_completed_workflow(tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path / "active.json")
    workflow = completed_workflow()
    store.save(workflow)

    archive_path = persist_workflow_result(store, workflow)

    assert archive_path is not None
    assert store.exists() is False
    assert WorkflowStore(archive_path).load() == workflow
