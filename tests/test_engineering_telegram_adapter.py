from datetime import UTC, datetime
from pathlib import Path

import pytest

from engineering.engineering_control import EngineeringControlService
from engineering.engineering_events import EventType, NOTIFICATION_EVENT_TYPES, build_event
from engineering.event_store import EngineeringEventStore, MAX_DELIVERY_ATTEMPTS
from engineering.telegram_adapter import AdapterBounds, TelegramEngineeringAdapter
from engineering.telegram_transport import TelegramTransportError, TelegramUpdate


NOW = datetime(2026, 8, 4, 4, 0, tzinfo=UTC)


class FakeTransport:
    def __init__(self, updates=()) -> None:
        self.updates = list(updates)
        self.sent = []
        self.polls = []
        self.send_errors = []
        self.poll_errors = []

    def get_updates(self, *, offset, timeout_seconds, limit):
        self.polls.append((offset, timeout_seconds, limit))
        if self.poll_errors:
            raise self.poll_errors.pop(0)
        return tuple(item for item in self.updates if item.update_id >= offset)[:limit]

    def send_message(self, *, chat_id, text):
        if self.send_errors:
            raise self.send_errors.pop(0)
        self.sent.append((chat_id, text))
        return str(len(self.sent))


class FakeQuery:
    def __init__(self, snapshot=None) -> None:
        self.value = snapshot or {
            "current_task": {
                "id": "OPS-015", "title": "Telegram adapter", "state": "QA",
                "feature_branch": "agent/ops-015",
            },
            "agent_run": {"status": "COMPLETE"},
            "recommended_next_step": "Run focused tests.",
            "report": {
                "task_id": "OPS-015", "recommendation": "ACCEPT",
                "generated_at": NOW.isoformat(), "next_action": "Open review PR.",
            },
            "pause": {"paused": False},
        }
        self.calls = 0

    def snapshot(self, *, timeline_limit=100):
        self.calls += 1
        return self.value


def update(command, *, update_id=1, chat_id=42, sender_id=42, chat_type="private", forwarded=False):
    return TelegramUpdate(update_id, chat_id, sender_id, chat_type, command, forwarded)


def adapter(tmp_path: Path, transport: FakeTransport, query=None, worker="worker"):
    events = EngineeringEventStore(tmp_path / "events.sqlite3", clock=lambda: NOW)
    controls = EngineeringControlService(events, clock=lambda: NOW)
    instance = TelegramEngineeringAdapter(
        authorized_chat_id=42,
        transport=transport,
        query_service=query or FakeQuery(),
        control_service=controls,
        event_store=events,
        worker_id=worker,
        bounds=AdapterBounds(poll_timeout_seconds=5, consumer_lease_seconds=10),
        clock=lambda: NOW,
        sleeper=lambda seconds: None,
    )
    return instance, events


@pytest.mark.parametrize(
    "bad_update",
    (
        update("/status", chat_id=41),
        update("/status", sender_id=41),
        update("/status", chat_type="group"),
        update("/status", chat_id=-100, sender_id=0, chat_type="channel"),
        update("/status", forwarded=True),
    ),
)
def test_default_deny_audits_without_echoing_message_or_identity(tmp_path: Path, bad_update) -> None:
    transport = FakeTransport([bad_update])
    instance, events = adapter(tmp_path, transport)
    instance.poll_once()
    assert transport.sent == []
    denied = events.list_events()[0].event
    assert denied.event_type is EventType.TELEGRAM_ACCESS_DENIED
    rendered = str(denied.payload)
    assert "/status" not in rendered and "41" not in rendered and "42" not in rendered


@pytest.mark.parametrize(
    ("command", "expected"),
    (
        ("/status", "Task OPS-015; workflow QA; agent COMPLETE; manager running."),
        ("/current", "OPS-015 — Telegram adapter; state QA; branch agent/ops-015."),
        ("/next", "Run focused tests."),
        ("/report", "Report for OPS-015; recommendation ACCEPT"),
    ),
)
def test_read_only_commands_use_only_shared_query_snapshot(tmp_path: Path, command, expected) -> None:
    transport = FakeTransport([update(command)])
    query = FakeQuery()
    instance, _ = adapter(tmp_path, transport, query=query)
    result = instance.poll_once()
    assert result.updates_handled == 1
    assert expected in transport.sent[0][1]
    assert query.calls == 1


def test_pause_resume_are_routed_to_control_audited_and_idempotent(tmp_path: Path) -> None:
    transport = FakeTransport([update("/pause", update_id=1)])
    instance, events = adapter(tmp_path, transport)
    instance.poll_once()
    transport.updates.append(update("/pause", update_id=2))
    instance.poll_once()
    transport.updates.append(update("/resume", update_id=3))
    instance.poll_once()
    assert [message for _, message in transport.sent] == [
        "Engineering manager paused (changed); revision 1.",
        "Engineering manager paused (already set); revision 1.",
        "Engineering manager resumed (changed); revision 2.",
    ]
    assert [item.event.event_type for item in events.list_events()] == [
        EventType.MANAGER_PAUSED, EventType.MANAGER_RESUMED,
    ]


@pytest.mark.parametrize("command", ("", "/shell", "/status extra", "/pause now", "x" * 129))
def test_unknown_malformed_and_oversized_commands_are_bounded(tmp_path: Path, command) -> None:
    transport = FakeTransport([update(command)])
    instance, _ = adapter(tmp_path, transport)
    instance.poll_once()
    assert len(transport.sent) == 1
    assert len(transport.sent[0][1]) < 200


def test_offset_is_persisted_and_duplicate_update_is_not_replayed(tmp_path: Path) -> None:
    transport = FakeTransport([update("/status", update_id=10)])
    first, events = adapter(tmp_path, transport, worker="one")
    first.poll_once()
    first.shutdown()
    second = TelegramEngineeringAdapter(
        authorized_chat_id=42, transport=transport, query_service=FakeQuery(),
        control_service=EngineeringControlService(events, clock=lambda: NOW),
        event_store=events, worker_id="two",
        bounds=AdapterBounds(poll_timeout_seconds=5, consumer_lease_seconds=10),
        clock=lambda: NOW, sleeper=lambda seconds: None,
    )
    second.poll_once()
    assert transport.polls == [(0, 5, 20), (11, 5, 20)]
    assert len(transport.sent) == 1


def test_second_consumer_cannot_poll_while_lease_is_active(tmp_path: Path) -> None:
    transport = FakeTransport()
    first, events = adapter(tmp_path, transport, worker="one")
    first.poll_once()
    second = TelegramEngineeringAdapter(
        authorized_chat_id=42, transport=transport, query_service=FakeQuery(),
        control_service=EngineeringControlService(events, clock=lambda: NOW),
        event_store=events, worker_id="two",
        bounds=AdapterBounds(poll_timeout_seconds=5, consumer_lease_seconds=10),
        clock=lambda: NOW,
    )
    assert second.poll_once().lease_acquired is False
    assert len(transport.polls) == 1


def test_all_approved_notifications_deliver_once_from_outbox(tmp_path: Path) -> None:
    transport = FakeTransport()
    instance, events = adapter(tmp_path, transport)
    for index, event_type in enumerate(sorted(NOTIFICATION_EVENT_TYPES, key=lambda x: x.value)):
        event = build_event(
            event_type, occurred_at=NOW.isoformat(), task_id="OPS-015",
            identity=str(index), payload={"state": "REPORT", "next_action": "Review."},
        )
        events.append(event, ("telegram",))
    assert instance.deliver_notifications_once() == 6
    assert instance.deliver_notifications_once() == 0
    assert len(transport.sent) == 6
    assert all(len(text) <= 3_500 for _, text in transport.sent)


def test_transient_delivery_retries_then_dead_letters_without_raw_error(tmp_path: Path) -> None:
    transport = FakeTransport()
    instance, events = adapter(tmp_path, transport)
    event = build_event(
        EventType.TASK_FAILED, occurred_at=NOW.isoformat(), task_id="OPS-015",
        identity="failed", payload={"failure_reason": "bounded"},
    )
    events.append(event, ("telegram",))
    for _ in range(MAX_DELIVERY_ATTEMPTS):
        transport.send_errors.append(
            TelegramTransportError("canary-secret raw failure", transient=True, retry_after=0)
        )
        instance.deliver_notifications_once()
    delivery = events.get_delivery(event.event_id, "telegram")
    assert delivery.status == "DEAD" and delivery.attempts == MAX_DELIVERY_ATTEMPTS
    assert "canary-secret" not in delivery.diagnostic


def test_run_has_finite_backoff_and_graceful_release(tmp_path: Path) -> None:
    transport = FakeTransport()
    transport.poll_errors = [
        TelegramTransportError("temporary", transient=True),
        TelegramTransportError("limited", transient=True, retry_after=4),
    ]
    sleeps = []
    instance, events = adapter(tmp_path, transport)
    instance.sleeper = sleeps.append
    instance.bounds = AdapterBounds(
        poll_timeout_seconds=5, consumer_lease_seconds=10, max_run_polls=3
    )
    assert instance.run() == 3
    assert sleeps == [1.0, 4]
    assert events.claim_telegram_consumer("telegram", lease_owner="after") is not None


def test_poll_timeout_override_can_only_shorten_configured_long_poll(tmp_path: Path) -> None:
    transport = FakeTransport()
    instance, _ = adapter(tmp_path, transport)
    instance.poll_once(poll_timeout_seconds=2)
    assert transport.polls == [(0, 2, 20)]
    with pytest.raises(ValueError, match="override"):
        instance.poll_once(poll_timeout_seconds=6)


def test_snapshot_values_are_bounded_and_control_char_sanitized(tmp_path: Path) -> None:
    query = FakeQuery({
        "current_task": None, "pause": {"paused": False},
        "recommended_next_step": "next\n" + "x" * 5_000, "report": None,
    })
    transport = FakeTransport([update("/next")])
    instance, _ = adapter(tmp_path, transport, query=query)
    instance.poll_once()
    assert "\n" not in transport.sent[0][1]
    assert len(transport.sent[0][1]) == 1_000
