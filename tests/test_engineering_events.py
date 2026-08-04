from datetime import UTC, datetime

import pytest

from engineering.engineering_events import EventType, build_event, sanitize_payload


def test_event_identity_is_deterministic_and_payload_is_bounded() -> None:
    args = dict(
        occurred_at="2026-08-04T01:00:00+00:00",
        task_id="OPS-014",
        identity="PLAN:evidence-digest",
        payload={"state": "PLAN", "reason": "x" * 3_000},
        workflow_id="OPS-014:agent/ops-014",
    )
    first = build_event(EventType.WORKFLOW_TRANSITION, **args)
    second = build_event(EventType.WORKFLOW_TRANSITION, **args)

    assert first.event_id == second.event_id
    assert len(first.payload["reason"]) == 2_000
    assert first.occurred_at == "2026-08-04T01:00:00+00:00"


def test_payload_sanitizer_rejects_unknown_fields_and_unbounded_lists() -> None:
    with pytest.raises(ValueError, match="unsupported fields"):
        sanitize_payload({"token": "secret"})
    with pytest.raises(ValueError, match="bounded string list"):
        sanitize_payload({"reason": ["item"] * 101})


def test_event_rejects_naive_occurrence_time() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        build_event(
            EventType.WORKFLOW_TRANSITION,
            occurred_at="2026-08-04T01:00:00",
            task_id="OPS-014",
            identity="PLAN",
            payload={"state": "PLAN"},
        )


def test_all_required_event_types_are_represented() -> None:
    assert {item.value for item in EventType} >= {
        "task.completed",
        "task.failed",
        "workflow.blocked",
        "workflow.stale",
        "pr.ready",
        "approval.required",
        "delegation.status",
        "qa.result",
        "report.generated",
        "manager.paused",
        "manager.resumed",
    }
