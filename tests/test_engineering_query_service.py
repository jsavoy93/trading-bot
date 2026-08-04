from pathlib import Path

from engineering.event_store import EngineeringEventStore
from engineering.models import WorkflowState
from engineering.query_service import EngineeringQueryService
from engineering.workflow_store import StoredWorkflow, WorkflowStore


BACKLOG = """### OPS-014 — Events\n\nStatus: TODO\nOwner: trading-manager\nPriority: P0\n\nAcceptance criteria:\n\n- Durable.\n- Bounded.\n"""


def service(tmp_path: Path, *, active: bool) -> EngineeringQueryService:
    backlog = tmp_path / "AGENT_BACKLOG.md"
    backlog.write_text(BACKLOG, encoding="utf-8")
    events = EngineeringEventStore(tmp_path / "events.sqlite3")
    workflows = WorkflowStore(tmp_path / "workflow.json", event_store=events)
    if active:
        workflows.save(StoredWorkflow("OPS-014", "agent/ops-014", WorkflowState.PLAN))
    return EngineeringQueryService(
        event_store=events, workflow_store=workflows, backlog_path=backlog
    )


def test_snapshot_uses_shared_bounded_projection(tmp_path: Path) -> None:
    snapshot = service(tmp_path, active=True).snapshot()
    assert snapshot["current_task"] == {
        "id": "OPS-014",
        "title": "Events",
        "priority": "P0",
        "owner": "trading-manager",
        "state": "PLAN",
        "feature_branch": "agent/ops-014",
    }
    assert snapshot["acceptance_criteria"] == ["Durable.", "Bounded."]
    assert snapshot["timeline"][0]["type"] == "workflow.transition"
    assert snapshot["pr_links"] == [] and snapshot["current_goals"] == []


def test_snapshot_handles_no_active_workflow_explicitly(tmp_path: Path) -> None:
    snapshot = service(tmp_path, active=False).snapshot()
    assert snapshot["current_task"] is None
    assert snapshot["recommended_next_step"] == "Start approved task OPS-014."
    assert "No active workflow is recorded." in snapshot["remaining_gaps"]
