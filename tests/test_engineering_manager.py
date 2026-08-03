from pathlib import Path

from engineering.manager import persist_workflow_result
from engineering.manager import _parser
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


def test_manager_cli_defaults_to_single_step() -> None:
    args = _parser().parse_args([])
    assert args.drive is False
    assert args.max_steps == 8
    assert args.max_elapsed_seconds == 900.0
    assert args.wait_poll_interval_seconds == 30.0
    assert args.max_wait_polls == 20


def test_manager_cli_drive_is_explicit_and_bounded() -> None:
    args = _parser().parse_args(["--drive", "--max-steps", "2", "--max-elapsed-seconds", "5", "--wait-poll-interval-seconds", "1", "--max-wait-polls", "3"])
    assert args.drive is True
    assert (args.max_steps, args.max_elapsed_seconds, args.wait_poll_interval_seconds, args.max_wait_polls) == (2, 5.0, 1.0, 3)
