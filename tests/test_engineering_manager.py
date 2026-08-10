from pathlib import Path

from engineering.manager import persist_workflow_result
from engineering.manager import _parser
from engineering.models import WorkflowFiles, WorkflowState
from engineering.workflow_store import StoredWorkflow, WorkflowStore
from tests.test_engineering_complete import completed_workflow


def _make_workflow_adapter(tmp_path: Path):
    """Build a minimal WorkflowAdapterImpl for isolated testing."""
    from engineering.context import EventAdapterImpl, WorkflowAdapterImpl

    workflow_files = WorkflowFiles(
        workflow_store_path=tmp_path / "active.json",
        event_store_path=tmp_path / "events.db",
        report_dir=tmp_path / "reports",
    )
    event_adapter = EventAdapterImpl(workflow_files.event_store_path)
    return WorkflowAdapterImpl(workflow_files, event_adapter)


def test_persist_noncomplete_workflow(tmp_path: Path) -> None:
    adapter = _make_workflow_adapter(tmp_path)
    workflow = StoredWorkflow("TEST-001", "agent/test", WorkflowState.PLAN)

    assert persist_workflow_result(adapter, workflow) is None
    assert adapter.workflow_store().load() == workflow


def test_archive_and_clear_completed_workflow(tmp_path: Path) -> None:
    adapter = _make_workflow_adapter(tmp_path)
    workflow = completed_workflow()
    adapter.workflow_store().save(workflow)

    archive_path = persist_workflow_result(adapter, workflow)

    assert archive_path is not None
    assert adapter.workflow_store().exists() is False
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
