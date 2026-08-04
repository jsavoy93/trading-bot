from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import sqlite3
from typing import Callable, Iterable

from engineering.engineering_events import (
    EngineeringEvent,
    EventSeverity,
    EventType,
    sanitize_payload,
)


EVENT_STORE_SCHEMA_VERSION = 1
DEFAULT_EVENT_STORE_PATH = Path(".agent-state/engineering-events.sqlite3")
MAX_DELIVERY_ATTEMPTS = 5
MAX_DIAGNOSTIC_CHARS = 2_000


@dataclass(frozen=True)
class StoredEvent:
    sequence: int
    event: EngineeringEvent


@dataclass(frozen=True)
class OutboxDelivery:
    event_id: str
    destination: str
    status: str
    attempts: int
    next_attempt_at: str
    lease_owner: str = ""
    lease_deadline_at: str = ""
    receipt_id: str = ""
    diagnostic: str = ""


@dataclass(frozen=True)
class TelegramConsumerState:
    destination: str
    update_offset: int
    lease_owner: str
    lease_deadline_at: str


class EngineeringEventStore:
    def __init__(
        self,
        path: Path = DEFAULT_EVENT_STORE_PATH,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        self.path = path
        self.clock = clock
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _ensure_schema(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            self.path.parent.chmod(0o700)
        except OSError:
            pass
        with self._connect() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, EVENT_STORE_SCHEMA_VERSION):
                raise RuntimeError(f"Unsupported engineering event schema: {version}")
            connection.executescript(
                """
                BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS engineering_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    schema_version INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    workflow_id TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    causation_id TEXT NOT NULL,
                    correlation_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS notification_outbox (
                    event_id TEXT NOT NULL,
                    destination TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at TEXT NOT NULL,
                    lease_owner TEXT NOT NULL DEFAULT '',
                    lease_deadline_at TEXT NOT NULL DEFAULT '',
                    receipt_id TEXT NOT NULL DEFAULT '',
                    diagnostic TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (event_id, destination),
                    FOREIGN KEY (event_id) REFERENCES engineering_events(event_id)
                );
                CREATE TABLE IF NOT EXISTS control_state (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    revision INTEGER NOT NULL,
                    paused INTEGER NOT NULL CHECK (paused IN (0, 1)),
                    actor TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                INSERT OR IGNORE INTO control_state
                    (singleton, revision, paused, actor, reason, updated_at)
                    VALUES (1, 0, 0, '', '', '1970-01-01T00:00:00+00:00');
                CREATE TABLE IF NOT EXISTS telegram_consumer_state (
                    destination TEXT PRIMARY KEY,
                    update_offset INTEGER NOT NULL DEFAULT 0
                        CHECK (update_offset >= 0),
                    lease_owner TEXT NOT NULL DEFAULT '',
                    lease_deadline_at TEXT NOT NULL DEFAULT ''
                );
                PRAGMA user_version = 1;
                COMMIT;
                """
            )
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def append(
        self, event: EngineeringEvent, destinations: Iterable[str] = ()
    ) -> bool:
        payload = json.dumps(event.payload, sort_keys=True, separators=(",", ":"))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO engineering_events (
                    event_id, schema_version, event_type, severity, occurred_at,
                    task_id, workflow_id, request_id, run_id, causation_id,
                    correlation_id, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.schema_version,
                    event.event_type.value,
                    event.severity.value,
                    event.occurred_at,
                    event.task_id,
                    event.workflow_id,
                    event.request_id,
                    event.run_id,
                    event.causation_id,
                    event.correlation_id,
                    payload,
                ),
            )
            for destination in sorted(set(destinations)):
                if not destination or len(destination) > 100:
                    raise ValueError("Outbox destination is invalid")
                connection.execute(
                    """
                    INSERT OR IGNORE INTO notification_outbox (
                        event_id, destination, status, next_attempt_at
                    ) VALUES (?, ?, 'PENDING', ?)
                    """,
                    (event.event_id, destination, event.occurred_at),
                )
            connection.commit()
        return cursor.rowcount == 1

    def list_events(self, *, limit: int = 100) -> tuple[StoredEvent, ...]:
        if not 1 <= limit <= 500:
            raise ValueError("Event query limit must be between 1 and 500")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM engineering_events ORDER BY sequence DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return tuple(self._stored_event(row) for row in reversed(rows))

    def get_event(self, event_id: str) -> StoredEvent:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM engineering_events WHERE event_id = ?", (event_id,)
            ).fetchone()
        if row is None:
            raise KeyError(event_id)
        return self._stored_event(row)

    def _stored_event(self, row: sqlite3.Row) -> StoredEvent:
        return StoredEvent(
            sequence=row["sequence"],
            event=EngineeringEvent(
                event_id=row["event_id"],
                schema_version=row["schema_version"],
                event_type=EventType(row["event_type"]),
                severity=EventSeverity(row["severity"]),
                occurred_at=row["occurred_at"],
                task_id=row["task_id"],
                workflow_id=row["workflow_id"],
                request_id=row["request_id"],
                run_id=row["run_id"],
                causation_id=row["causation_id"],
                correlation_id=row["correlation_id"],
                payload=sanitize_payload(json.loads(row["payload_json"])),
            ),
        )

    def claim(
        self,
        destination: str,
        *,
        lease_owner: str,
        limit: int = 10,
        lease_seconds: int = 30,
    ) -> tuple[OutboxDelivery, ...]:
        if not destination or not lease_owner or not 1 <= limit <= 100:
            raise ValueError("Outbox claim arguments are invalid")
        if not 1 <= lease_seconds <= 300:
            raise ValueError("Outbox lease must be between 1 and 300 seconds")
        now = self.clock().astimezone(UTC)
        now_text = now.isoformat()
        deadline = (now + timedelta(seconds=lease_seconds)).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE notification_outbox
                SET status = 'RETRY', lease_owner = '', lease_deadline_at = ''
                WHERE destination = ? AND status = 'SENDING'
                  AND lease_deadline_at <= ?
                """,
                (destination, now_text),
            )
            rows = connection.execute(
                """
                SELECT event_id FROM notification_outbox
                WHERE destination = ? AND status IN ('PENDING', 'RETRY')
                  AND next_attempt_at <= ? AND attempts < ?
                ORDER BY rowid LIMIT ?
                """,
                (destination, now_text, MAX_DELIVERY_ATTEMPTS, limit),
            ).fetchall()
            ids = [row["event_id"] for row in rows]
            for event_id in ids:
                connection.execute(
                    """
                    UPDATE notification_outbox
                    SET status = 'SENDING', attempts = attempts + 1,
                        lease_owner = ?, lease_deadline_at = ?
                    WHERE event_id = ? AND destination = ?
                    """,
                    (lease_owner, deadline, event_id, destination),
                )
            connection.commit()
        return tuple(self.get_delivery(event_id, destination) for event_id in ids)

    def get_delivery(self, event_id: str, destination: str) -> OutboxDelivery:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM notification_outbox WHERE event_id = ? AND destination = ?",
                (event_id, destination),
            ).fetchone()
        if row is None:
            raise KeyError((event_id, destination))
        return OutboxDelivery(**dict(row))

    def mark_sent(
        self, event_id: str, destination: str, *, lease_owner: str, receipt_id: str
    ) -> None:
        self._terminal_delivery_update(
            event_id, destination, lease_owner=lease_owner, status="SENT",
            receipt_id=receipt_id[:200], diagnostic="",
        )

    def mark_retry(
        self,
        event_id: str,
        destination: str,
        *,
        lease_owner: str,
        diagnostic: str,
        retry_at: datetime,
    ) -> None:
        delivery = self.get_delivery(event_id, destination)
        status = "DEAD" if delivery.attempts >= MAX_DELIVERY_ATTEMPTS else "RETRY"
        self._terminal_delivery_update(
            event_id,
            destination,
            lease_owner=lease_owner,
            status=status,
            receipt_id="",
            diagnostic=diagnostic[:MAX_DIAGNOSTIC_CHARS],
            next_attempt_at=retry_at.astimezone(UTC).isoformat(),
        )

    def _terminal_delivery_update(
        self,
        event_id: str,
        destination: str,
        *,
        lease_owner: str,
        status: str,
        receipt_id: str,
        diagnostic: str,
        next_attempt_at: str | None = None,
    ) -> None:
        if status not in {"SENT", "RETRY", "DEAD"}:
            raise ValueError("Invalid delivery terminal status")
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE notification_outbox
                SET status = ?, receipt_id = ?, diagnostic = ?,
                    next_attempt_at = COALESCE(?, next_attempt_at),
                    lease_owner = '', lease_deadline_at = ''
                WHERE event_id = ? AND destination = ?
                  AND status = 'SENDING' AND lease_owner = ?
                """,
                (
                    status,
                    receipt_id,
                    diagnostic,
                    next_attempt_at,
                    event_id,
                    destination,
                    lease_owner,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Outbox delivery lease does not match")

    def pause_state(self) -> dict[str, object]:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM control_state WHERE singleton = 1").fetchone()
        assert row is not None
        return {
            "revision": row["revision"],
            "paused": bool(row["paused"]),
            "actor": row["actor"],
            "reason": row["reason"],
            "updated_at": row["updated_at"],
        }

    def claim_telegram_consumer(
        self,
        destination: str,
        *,
        lease_owner: str,
        lease_seconds: int = 60,
    ) -> TelegramConsumerState | None:
        if not destination or len(destination) > 100 or not lease_owner or len(lease_owner) > 200:
            raise ValueError("Telegram consumer identity is invalid")
        if not 1 <= lease_seconds <= 300:
            raise ValueError("Telegram consumer lease is outside finite bounds")
        now = self.clock().astimezone(UTC)
        deadline = (now + timedelta(seconds=lease_seconds)).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT OR IGNORE INTO telegram_consumer_state
                    (destination, update_offset, lease_owner, lease_deadline_at)
                    VALUES (?, 0, '', '')
                """,
                (destination,),
            )
            cursor = connection.execute(
                """
                UPDATE telegram_consumer_state
                SET lease_owner = ?, lease_deadline_at = ?
                WHERE destination = ? AND (
                    lease_owner = '' OR lease_owner = ? OR lease_deadline_at <= ?
                )
                """,
                (lease_owner, deadline, destination, lease_owner, now.isoformat()),
            )
            row = connection.execute(
                "SELECT * FROM telegram_consumer_state WHERE destination = ?",
                (destination,),
            ).fetchone()
            connection.commit()
        if cursor.rowcount != 1:
            return None
        assert row is not None
        return TelegramConsumerState(**dict(row))

    def advance_telegram_offset(
        self, destination: str, *, lease_owner: str, next_offset: int
    ) -> TelegramConsumerState:
        if next_offset < 0:
            raise ValueError("Telegram update offset cannot be negative")
        now = self.clock().astimezone(UTC).isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE telegram_consumer_state
                SET update_offset = MAX(update_offset, ?)
                WHERE destination = ? AND lease_owner = ?
                  AND lease_deadline_at > ?
                """,
                (next_offset, destination, lease_owner, now),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Telegram consumer lease does not match")
            row = connection.execute(
                "SELECT * FROM telegram_consumer_state WHERE destination = ?",
                (destination,),
            ).fetchone()
        assert row is not None
        return TelegramConsumerState(**dict(row))

    def release_telegram_consumer(self, destination: str, *, lease_owner: str) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE telegram_consumer_state
                SET lease_owner = '', lease_deadline_at = ''
                WHERE destination = ? AND lease_owner = ?
                """,
                (destination, lease_owner),
            )
        if cursor.rowcount != 1:
            raise RuntimeError("Telegram consumer lease does not match")

    def set_paused_with_event(
        self,
        paused: bool,
        *,
        expected_revision: int,
        actor: str,
        reason: str,
        event: EngineeringEvent,
    ) -> dict[str, object]:
        expected_type = EventType.MANAGER_PAUSED if paused else EventType.MANAGER_RESUMED
        if event.event_type is not expected_type or event.payload.get("paused") is not paused:
            raise ValueError("Pause audit event does not match the requested control state")
        if not actor or len(actor) > 200 or len(reason) > MAX_DIAGNOSTIC_CHARS:
            raise ValueError("Pause control metadata is invalid")
        payload = json.dumps(event.payload, sort_keys=True, separators=(",", ":"))
        now = self.clock().astimezone(UTC).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM control_state WHERE singleton = 1"
            ).fetchone()
            assert row is not None
            if row["revision"] != expected_revision:
                connection.rollback()
                raise RuntimeError("Pause control revision changed")
            if bool(row["paused"]) == paused:
                connection.commit()
                return self.pause_state()
            connection.execute(
                """
                INSERT INTO engineering_events (
                    event_id, schema_version, event_type, severity, occurred_at,
                    task_id, workflow_id, request_id, run_id, causation_id,
                    correlation_id, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id, event.schema_version, event.event_type.value,
                    event.severity.value, event.occurred_at, event.task_id,
                    event.workflow_id, event.request_id, event.run_id,
                    event.causation_id, event.correlation_id, payload,
                ),
            )
            connection.execute(
                """
                UPDATE control_state SET revision = revision + 1, paused = ?,
                    actor = ?, reason = ?, updated_at = ? WHERE singleton = 1
                """,
                (int(paused), actor, reason, now),
            )
            connection.commit()
        return self.pause_state()

    def set_paused(self, paused: bool, *, actor: str, reason: str) -> dict[str, object]:
        if not actor or len(actor) > 200 or len(reason) > MAX_DIAGNOSTIC_CHARS:
            raise ValueError("Pause control metadata is invalid")
        now = self.clock().astimezone(UTC).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM control_state WHERE singleton = 1").fetchone()
            assert row is not None
            if bool(row["paused"]) != paused:
                connection.execute(
                    """
                    UPDATE control_state SET revision = revision + 1, paused = ?,
                        actor = ?, reason = ?, updated_at = ? WHERE singleton = 1
                    """,
                    (int(paused), actor, reason, now),
                )
            connection.commit()
        return self.pause_state()
