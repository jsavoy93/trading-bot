from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from engineering.engineering_events import EventType, build_event
from engineering.event_store import EngineeringEventStore, MAX_DELIVERY_ATTEMPTS


NOW = datetime(2026, 8, 4, 1, 0, tzinfo=UTC)


def event(identity: str = "one"):
    return build_event(
        EventType.TASK_COMPLETED,
        occurred_at=NOW.isoformat(),
        task_id="OPS-014",
        identity=identity,
        payload={"state": "COMPLETE", "feature_branch": "agent/ops-014"},
    )


def store(tmp_path: Path) -> EngineeringEventStore:
    return EngineeringEventStore(tmp_path / "events.sqlite3", clock=lambda: NOW)


def test_append_and_outbox_creation_are_atomic_and_idempotent(tmp_path: Path) -> None:
    events = store(tmp_path)
    item = event()

    assert events.append(item, ("telegram",)) is True
    assert events.append(item, ("telegram",)) is False
    assert [stored.event.event_id for stored in events.list_events()] == [item.event_id]
    assert events.get_delivery(item.event_id, "telegram").status == "PENDING"


def test_invalid_destination_rolls_back_event_and_outbox(tmp_path: Path) -> None:
    events = store(tmp_path)
    with pytest.raises(ValueError, match="destination"):
        events.append(event(), ("",))
    assert events.list_events() == ()


def test_claim_send_retry_and_expired_lease_recovery(tmp_path: Path) -> None:
    events = store(tmp_path)
    item = event()
    events.append(item, ("telegram",))

    claimed = events.claim("telegram", lease_owner="worker-1", lease_seconds=5)
    assert len(claimed) == 1 and claimed[0].status == "SENDING"
    with pytest.raises(RuntimeError, match="lease"):
        events.mark_sent(item.event_id, "telegram", lease_owner="wrong", receipt_id="1")

    events.clock = lambda: NOW + timedelta(seconds=6)
    recovered = events.claim("telegram", lease_owner="worker-2")
    assert recovered[0].attempts == 2
    events.mark_sent(item.event_id, "telegram", lease_owner="worker-2", receipt_id="42")
    sent = events.get_delivery(item.event_id, "telegram")
    assert (sent.status, sent.receipt_id) == ("SENT", "42")


def test_retry_is_bounded_and_dead_letters(tmp_path: Path) -> None:
    events = store(tmp_path)
    item = event()
    events.append(item, ("telegram",))
    for attempt in range(MAX_DELIVERY_ATTEMPTS):
        claimed = events.claim("telegram", lease_owner=f"worker-{attempt}")
        assert len(claimed) == 1
        events.mark_retry(
            item.event_id,
            "telegram",
            lease_owner=f"worker-{attempt}",
            diagnostic="failure",
            retry_at=NOW,
        )
    delivery = events.get_delivery(item.event_id, "telegram")
    assert delivery.status == "DEAD"
    assert events.claim("telegram", lease_owner="later") == ()


def test_pause_state_is_revisioned_and_idempotent(tmp_path: Path) -> None:
    events = store(tmp_path)
    assert events.pause_state()["paused"] is False
    paused = events.set_paused(True, actor="josh", reason="review")
    duplicate = events.set_paused(True, actor="josh", reason="ignored duplicate")
    resumed = events.set_paused(False, actor="josh", reason="approved")

    assert paused["revision"] == duplicate["revision"] == 1
    assert resumed["revision"] == 2 and resumed["paused"] is False
    assert events.path.stat().st_mode & 0o777 == 0o600
