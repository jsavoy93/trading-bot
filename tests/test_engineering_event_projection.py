from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from engineering.engineering_events import EventType
from engineering.event_projection import reconcile_workflow, workflow_events
from engineering.event_store import EngineeringEventStore
from engineering.models import DelegationStatus, WorkflowState
from engineering.workflow_store import DelegationRecord, DriverRecord, StoredWorkflow
from tests.test_engineering_complete import completed_workflow


def test_reconcile_is_idempotent_and_creates_notification_outbox(tmp_path: Path) -> None:
    store = EngineeringEventStore(tmp_path / "events.sqlite3")
    workflow = completed_workflow()

    first = reconcile_workflow(workflow, store)
    second = reconcile_workflow(workflow, store)
    types = [item.event.event_type for item in store.list_events()]

    assert first > 0 and second == 0
    assert EventType.TASK_COMPLETED in types
    assert EventType.APPROVAL_REQUIRED in types
    completed = next(item.event for item in store.list_events() if item.event.event_type is EventType.TASK_COMPLETED)
    assert store.get_delivery(completed.event_id, "telegram").status == "PENDING"


def test_failure_blocked_and_stale_events_are_derived_from_persisted_evidence() -> None:
    delegation = DelegationRecord(
        "run-1",
        "trading-exec",
        "2026-08-01T00:00:00+00:00",
        DelegationStatus.FAILED,
        request_id="request-1",
        updated_at="2026-08-04T01:00:00+00:00",
        exit_code=1,
        completed_at="2026-08-04T01:00:00+00:00",
        failure_reason="bounded failure",
    )
    driver = DriverRecord(
        started_at="2026-08-01T00:00:00+00:00",
        updated_at="2026-08-04T01:00:00+00:00",
        blocked=True,
        stale=True,
        last_stop_reason="human review required",
    )
    workflow = StoredWorkflow(
        "OPS-014", "agent/ops-014", WorkflowState.WAIT_FOR_AGENT,
        delegation=delegation, driver=driver,
    )
    types = {event.event_type for event in workflow_events(workflow)}
    assert {
        EventType.TASK_FAILED,
        EventType.WORKFLOW_BLOCKED,
        EventType.WORKFLOW_STALE,
    } <= types


def test_workflow_store_reconciles_after_atomic_save(tmp_path: Path) -> None:
    from engineering.workflow_store import WorkflowStore

    events = EngineeringEventStore(tmp_path / "events.sqlite3")
    store = WorkflowStore(tmp_path / "workflow.json", event_store=events)
    workflow = StoredWorkflow("OPS-014", "agent/ops-014", WorkflowState.PLAN)
    store.save(workflow)
    store.save(workflow)
    assert len(events.list_events()) == 1
