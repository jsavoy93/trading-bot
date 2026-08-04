from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import math
import time
from typing import Callable, Mapping, Protocol

from engineering.engineering_control import ControlResult, EngineeringControlService
from engineering.engineering_events import (
    NOTIFICATION_EVENT_TYPES,
    EngineeringEvent,
    EventSeverity,
    EventType,
    build_event,
)
from engineering.event_store import EngineeringEventStore, OutboxDelivery
from engineering.telegram_transport import (
    MAX_TELEGRAM_MESSAGE_CHARS,
    TelegramTransport,
    TelegramTransportError,
    TelegramUpdate,
)


TELEGRAM_DESTINATION = "telegram"
MAX_COMMAND_CHARS = 128
MAX_RENDERED_VALUE_CHARS = 500
MAX_BACKOFF_SECONDS = 300.0
APPROVED_COMMANDS = frozenset(
    {"/status", "/current", "/next", "/report", "/pause", "/resume"}
)


class QueryService(Protocol):
    def snapshot(self, *, timeline_limit: int = 100) -> dict[str, object]: ...


@dataclass(frozen=True)
class AdapterBounds:
    poll_timeout_seconds: int = 30
    update_batch_size: int = 20
    outbox_batch_size: int = 20
    consumer_lease_seconds: int = 60
    outbox_lease_seconds: int = 30
    max_run_polls: int = 100

    def validate(self) -> None:
        if not 1 <= self.poll_timeout_seconds <= 50:
            raise ValueError("Telegram poll timeout is outside finite bounds")
        if not 1 <= self.update_batch_size <= 100:
            raise ValueError("Telegram update batch is outside finite bounds")
        if not 1 <= self.outbox_batch_size <= 100:
            raise ValueError("Telegram outbox batch is outside finite bounds")
        if not 1 <= self.consumer_lease_seconds <= 300:
            raise ValueError("Telegram consumer lease is outside finite bounds")
        if self.consumer_lease_seconds <= self.poll_timeout_seconds:
            raise ValueError("Telegram consumer lease must exceed poll timeout")
        if not 1 <= self.outbox_lease_seconds <= 300:
            raise ValueError("Telegram outbox lease is outside finite bounds")
        if not 1 <= self.max_run_polls <= 10_000:
            raise ValueError("Telegram run poll count is outside finite bounds")


@dataclass(frozen=True)
class PollResult:
    updates_seen: int
    updates_handled: int
    notifications_sent: int
    lease_acquired: bool


def _bounded_text(value: object, limit: int = MAX_RENDERED_VALUE_CHARS) -> str:
    text = str(value).replace("\x00", "").replace("\r", " ").replace("\n", " ")
    return " ".join(text.split())[:limit]


class TelegramEngineeringAdapter:
    def __init__(
        self,
        *,
        authorized_chat_id: int,
        transport: TelegramTransport,
        query_service: QueryService,
        control_service: EngineeringControlService,
        event_store: EngineeringEventStore,
        worker_id: str,
        bounds: AdapterBounds = AdapterBounds(),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleeper: Callable[[float], None] = time.sleep,
        runtime_event_sink: Callable[[str, Mapping[str, object]], None] = (
            lambda event, fields: None
        ),
    ) -> None:
        if authorized_chat_id <= 0:
            raise ValueError("Telegram allowlisted chat ID is invalid")
        if not worker_id or len(worker_id) > 200:
            raise ValueError("Telegram worker identity is invalid")
        bounds.validate()
        self.authorized_chat_id = authorized_chat_id
        self.transport = transport
        self.query_service = query_service
        self.control_service = control_service
        self.event_store = event_store
        self.worker_id = worker_id
        self.bounds = bounds
        self.clock = clock
        self.sleeper = sleeper
        self.runtime_event_sink = runtime_event_sink
        self._leased = False

    def poll_once(self, *, poll_timeout_seconds: int | None = None) -> PollResult:
        timeout_seconds = (
            self.bounds.poll_timeout_seconds
            if poll_timeout_seconds is None
            else poll_timeout_seconds
        )
        if not 1 <= timeout_seconds <= self.bounds.poll_timeout_seconds:
            raise ValueError("Telegram poll timeout override is outside finite bounds")
        state = self.event_store.claim_telegram_consumer(
            TELEGRAM_DESTINATION,
            lease_owner=self.worker_id,
            lease_seconds=self.bounds.consumer_lease_seconds,
        )
        if state is None:
            self.runtime_event_sink("competing_poller", {})
            return PollResult(0, 0, 0, False)
        self._leased = True
        updates = self.transport.get_updates(
            offset=state.update_offset,
            timeout_seconds=timeout_seconds,
            limit=self.bounds.update_batch_size,
        )
        handled = 0
        for update in sorted(updates, key=lambda item: item.update_id):
            if update.update_id < state.update_offset:
                continue
            self._handle_update(update)
            state = self.event_store.advance_telegram_offset(
                TELEGRAM_DESTINATION,
                lease_owner=self.worker_id,
                next_offset=update.update_id + 1,
            )
            handled += 1
        sent = self.deliver_notifications_once()
        return PollResult(len(updates), handled, sent, True)

    def run(self, *, should_stop: Callable[[], bool] = lambda: False) -> int:
        polls = 0
        failures = 0
        try:
            while polls < self.bounds.max_run_polls and not should_stop():
                try:
                    result = self.poll_once()
                    polls += 1
                    failures = 0
                    if not result.lease_acquired:
                        break
                except TelegramTransportError as exc:
                    polls += 1
                    if not exc.transient:
                        break
                    failures += 1
                    delay = exc.retry_after or min(
                        MAX_BACKOFF_SECONDS, float(2 ** min(failures - 1, 8))
                    )
                    self.sleeper(max(0.0, min(delay, MAX_BACKOFF_SECONDS)))
        finally:
            self.shutdown()
        return polls

    def shutdown(self) -> None:
        if self._leased:
            self.event_store.release_telegram_consumer(
                TELEGRAM_DESTINATION, lease_owner=self.worker_id
            )
            self._leased = False
            self.runtime_event_sink("lease_released", {})

    def _authorized(self, update: TelegramUpdate) -> bool:
        return (
            update.chat_id == self.authorized_chat_id
            and update.sender_id == self.authorized_chat_id
            and update.chat_type == "private"
            and not update.forwarded
        )

    def _audit_denied(self, update: TelegramUpdate, reason: str) -> None:
        event = build_event(
            EventType.TELEGRAM_ACCESS_DENIED,
            occurred_at=self._now().isoformat(),
            task_id="OPS-015",
            identity=f"denied-update-{update.update_id}",
            payload={"actor": "unauthorized", "reason": reason},
            severity=EventSeverity.WARNING,
        )
        self.event_store.append(event)
        self.runtime_event_sink("access_denied", {})

    def _handle_update(self, update: TelegramUpdate) -> None:
        if not self._authorized(update):
            self._audit_denied(update, "Telegram identity or private-chat policy denied")
            return
        if not update.text or len(update.text) > MAX_COMMAND_CHARS:
            self._send("Command rejected. Use /status, /current, /next, /report, /pause, or /resume.")
            return
        parts = update.text.strip().split()
        if len(parts) != 1 or parts[0] not in APPROVED_COMMANDS:
            self._send("Unknown command. Use /status, /current, /next, /report, /pause, or /resume.")
            return
        command = parts[0]
        self.runtime_event_sink("authorized_command", {"command": command})
        if command == "/pause":
            self._send(self._render_control("paused", self.control_service.pause()))
        elif command == "/resume":
            self._send(self._render_control("resumed", self.control_service.resume()))
        else:
            snapshot = self.query_service.snapshot(timeline_limit=20)
            self._send(self._render_query(command, snapshot))

    def _send(self, text: str) -> str:
        return self.transport.send_message(
            chat_id=self.authorized_chat_id,
            text=text[:MAX_TELEGRAM_MESSAGE_CHARS],
        )

    @staticmethod
    def _render_control(action: str, result: ControlResult) -> str:
        disposition = "changed" if result.changed else "already set"
        return f"Engineering manager {action} ({disposition}); revision {result.revision}."

    def _render_query(self, command: str, snapshot: dict[str, object]) -> str:
        current = snapshot.get("current_task")
        pause = snapshot.get("pause") if isinstance(snapshot.get("pause"), dict) else {}
        if command == "/status":
            if isinstance(current, dict):
                task = _bounded_text(current.get("id", "unknown"), 80)
                state = _bounded_text(current.get("state", "unknown"), 80)
                run = snapshot.get("agent_run")
                run_status = (
                    _bounded_text(run.get("status", "not recorded"), 80)
                    if isinstance(run, dict)
                    else "not recorded"
                )
                return (
                    f"Task {task}; workflow {state}; agent {run_status}; "
                    f"manager {'paused' if pause.get('paused') else 'running'}."
                )
            return f"No active workflow; manager {'paused' if pause.get('paused') else 'running'}."
        if command == "/current":
            if not isinstance(current, dict):
                return "No active workflow is recorded."
            return (
                f"{_bounded_text(current.get('id', ''), 80)} — "
                f"{_bounded_text(current.get('title', ''), 300)}; "
                f"state {_bounded_text(current.get('state', ''), 80)}; "
                f"branch {_bounded_text(current.get('feature_branch', ''), 200)}."
            )
        if command == "/next":
            return _bounded_text(
                snapshot.get("recommended_next_step", "No recommended next step is recorded."),
                1_000,
            )
        report = snapshot.get("report")
        if not isinstance(report, dict):
            return "No active workflow report is recorded."
        return (
            f"Report for {_bounded_text(report.get('task_id', ''), 80)}; "
            f"recommendation {_bounded_text(report.get('recommendation', ''), 80)}; "
            f"generated {_bounded_text(report.get('generated_at', ''), 100)}; "
            f"next {_bounded_text(report.get('next_action', ''), 1_000)}"
        )

    def deliver_notifications_once(self) -> int:
        deliveries = self.event_store.claim(
            TELEGRAM_DESTINATION,
            lease_owner=self.worker_id,
            limit=self.bounds.outbox_batch_size,
            lease_seconds=self.bounds.outbox_lease_seconds,
        )
        sent = 0
        for delivery in deliveries:
            event = self.event_store.get_event(delivery.event_id).event
            if event.event_type not in NOTIFICATION_EVENT_TYPES:
                self.event_store.mark_sent(
                    delivery.event_id,
                    TELEGRAM_DESTINATION,
                    lease_owner=self.worker_id,
                    receipt_id="not-notifiable",
                )
                continue
            try:
                receipt = self._send(self._render_notification(event))
            except TelegramTransportError as exc:
                self._retry_delivery(delivery, exc)
                continue
            self.event_store.mark_sent(
                delivery.event_id,
                TELEGRAM_DESTINATION,
                lease_owner=self.worker_id,
                receipt_id=receipt,
            )
            self.runtime_event_sink(
                "notification_sent", {"event_type": event.event_type.value}
            )
            sent += 1
        return sent

    def _retry_delivery(
        self, delivery: OutboxDelivery, error: TelegramTransportError
    ) -> None:
        exponential = min(MAX_BACKOFF_SECONDS, float(2 ** min(delivery.attempts - 1, 8)))
        delay = error.retry_after if error.retry_after is not None else exponential
        if not math.isfinite(delay):
            delay = MAX_BACKOFF_SECONDS
        retry_at = self._now() + timedelta(seconds=max(0.0, min(delay, MAX_BACKOFF_SECONDS)))
        self.event_store.mark_retry(
            delivery.event_id,
            TELEGRAM_DESTINATION,
            lease_owner=self.worker_id,
            diagnostic=f"Telegram delivery failed: {type(error).__name__}",
            retry_at=retry_at,
        )
        status = self.event_store.get_delivery(
            delivery.event_id, TELEGRAM_DESTINATION
        ).status
        self.runtime_event_sink(
            "notification_failed",
            {"delivery_status": status, "reason_code": "telegram_transport"},
        )

    @staticmethod
    def _render_notification(event: EngineeringEvent) -> str:
        labels = {
            EventType.TASK_COMPLETED: "Task completed",
            EventType.TASK_FAILED: "Task failed",
            EventType.WORKFLOW_BLOCKED: "Workflow blocked",
            EventType.WORKFLOW_STALE: "Workflow stale",
            EventType.PR_READY: "PR ready",
            EventType.APPROVAL_REQUIRED: "Approval required",
        }
        pieces = [labels[event.event_type], f"task {_bounded_text(event.task_id, 80)}"]
        for key, label in (
            ("state", "state"),
            ("feature_branch", "branch"),
            ("failure_reason", "reason"),
            ("stop_reason", "reason"),
            ("next_action", "next"),
            ("pr_url", "PR"),
        ):
            value = event.payload.get(key)
            if value not in (None, ""):
                pieces.append(f"{label} {_bounded_text(value)}")
        return "; ".join(pieces)[:MAX_TELEGRAM_MESSAGE_CHARS]

    def _now(self) -> datetime:
        now = self.clock()
        if now.tzinfo is None:
            raise ValueError("Telegram adapter clock must be timezone-aware")
        return now.astimezone(UTC)
