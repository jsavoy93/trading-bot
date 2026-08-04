from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from engineering.engineering_control import EngineeringControlService
from engineering.engineering_events import EventType
from engineering.event_store import EngineeringEventStore


NOW = datetime(2026, 8, 4, 3, 0, tzinfo=UTC)


def store(tmp_path: Path) -> EngineeringEventStore:
    return EngineeringEventStore(tmp_path / "events.sqlite3", clock=lambda: NOW)


def test_pause_resume_are_revisioned_idempotent_and_audited(tmp_path: Path) -> None:
    events = store(tmp_path)
    controls = EngineeringControlService(events, clock=lambda: NOW)

    paused = controls.pause()
    duplicate = controls.pause()
    resumed = controls.resume()

    assert (paused.paused, paused.revision, paused.changed) == (True, 1, True)
    assert (duplicate.revision, duplicate.changed) == (1, False)
    assert (resumed.paused, resumed.revision, resumed.changed) == (False, 2, True)
    stored = [item.event for item in events.list_events()]
    assert [event.event_type for event in stored] == [
        EventType.MANAGER_PAUSED,
        EventType.MANAGER_RESUMED,
    ]
    assert [event.payload["revision"] for event in stored] == [1, 2]


def test_control_rejects_non_josh_actor_without_state_or_event(tmp_path: Path) -> None:
    events = store(tmp_path)
    with pytest.raises(ValueError, match="not authorized"):
        EngineeringControlService(events).pause(actor="agent")
    assert events.pause_state()["revision"] == 0
    assert events.list_events() == ()


def test_control_revision_race_fails_closed(tmp_path: Path, monkeypatch) -> None:
    events = store(tmp_path)
    controls = EngineeringControlService(events, clock=lambda: NOW)
    original = events.set_paused_with_event

    def raced(paused, **kwargs):
        events.set_paused(True, actor="josh", reason="competing authorized change")
        return original(paused, **kwargs)

    monkeypatch.setattr(events, "set_paused_with_event", raced)
    with pytest.raises(RuntimeError, match="revision changed"):
        controls.pause()
    assert events.pause_state()["revision"] == 1
    assert events.list_events() == ()


def test_consumer_offset_lease_is_single_restart_safe_and_finite(tmp_path: Path) -> None:
    events = store(tmp_path)
    first = events.claim_telegram_consumer("telegram", lease_owner="one", lease_seconds=5)
    assert first is not None and first.update_offset == 0
    assert events.claim_telegram_consumer("telegram", lease_owner="two") is None
    advanced = events.advance_telegram_offset(
        "telegram", lease_owner="one", next_offset=11
    )
    assert advanced.update_offset == 11
    events.release_telegram_consumer("telegram", lease_owner="one")
    restarted = events.claim_telegram_consumer("telegram", lease_owner="two")
    assert restarted is not None and restarted.update_offset == 11

    events.clock = lambda: NOW + timedelta(seconds=301)
    recovered = events.claim_telegram_consumer("telegram", lease_owner="three")
    assert recovered is not None and recovered.update_offset == 11


def test_consumer_offset_requires_matching_unexpired_lease(tmp_path: Path) -> None:
    events = store(tmp_path)
    events.claim_telegram_consumer("telegram", lease_owner="one", lease_seconds=1)
    events.clock = lambda: NOW + timedelta(seconds=2)
    with pytest.raises(RuntimeError, match="lease"):
        events.advance_telegram_offset("telegram", lease_owner="one", next_offset=1)
