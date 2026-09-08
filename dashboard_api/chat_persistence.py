from __future__ import annotations

import hashlib
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

# ---------------------------------------------------------------------------
# Durable engineering-chat storage (PR3)
#
# Persists ONLY already-projected visible chat messages so an OpenClaw
# session rotation or trajectory restart does NOT lose prior conversation
# context. The dashboard /api/engineering/chat/history endpoint continues
# to use the live OpenClaw Gateway projection; this store is read by a new
# /api/engineering/chat/history/durable endpoint (PR3 does NOT switch the
# browser UI to it — that is PR4).
#
# Hard rules (from PR3 spec, locked with Josh on 2026-09-08):
#   * Single source of truth for visibility filtering is
#     dashboard_api.chat_gateway.project_message (the existing projection).
#     This module never re-implements the role / stopReason / delivery-mirror
#     filter; the caller is expected to have already run the projection and
#     pass only surviving ChatMessage instances here.
#   * Two durable-row sources:
#       1. user rows: inserted when chat.send returns status="accepted"
#          (run_id present). Rejected / failed sends are NOT persisted
#          (documented choice in chat_persistence + chat_gateway send wrapper).
#       2. assistant rows: inserted when chat.history projection emits a
#          visible ChatMessage (role=="assistant" AND stopReason=="stop" AND
#          model!="delivery-mirror"). User messages arriving via chat.history
#          are NOT inserted here; the durable history endpoint shows the
#          user rows inserted via path 1 alongside assistant rows from path 2.
#   * Dedup_key uniqueness is enforced via SQLite UNIQUE constraint +
#     INSERT OR IGNORE. Repeated 15-second polling, page refresh, and
#     post-restart reconciliation are all safe.
#   * dedup_key MUST NOT collapse distinct messages on text match alone.
#     See _build_user_dedup_key / _build_assistant_dedup_key below.
#   * File mode is enforced at 0600 root:root; parent dir 0700. The DB file
#     lives under .agent-state/ which is gitignored.
# ---------------------------------------------------------------------------

DEFAULT_PATH = Path(".agent-state/engineering-chat.sqlite3")
SCHEMA_VERSION = 1
FILE_MODE = 0o600
PARENT_MODE = 0o700
DEFAULT_USER_DEDUP_FALLBACK_NS_UUID = "uuid4"

# Role CHECK constraint values. Kept in sync with the SQLite CHECK.
ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"
ALLOWED_ROLES = frozenset({ROLE_USER, ROLE_ASSISTANT})

# delivery_status enum (intentionally small in PR3 — failed/rejected are not
# persisted, by design).
DELIVERY_STATUS_ACCEPTED = "accepted"
DELIVERY_STATUS_PERSISTED = "persisted"
ALLOWED_DELIVERY_STATUSES = frozenset({DELIVERY_STATUS_ACCEPTED, DELIVERY_STATUS_PERSISTED})


@dataclass(frozen=True)
class DurableChatMessage:
    """Public read-side view of a persisted durable chat message.

    Mirrors the columns the PR3 spec mandates. ``response_id`` is consumed
    at insertion time as a priority-2 dedup-key input but is NOT stored
    separately because the dedup_key column is the sole authoritative
    identity.
    """

    id: int
    role: str
    text: str
    timestamp: str | None
    truncated: bool
    truncation_source: str | None
    openclaw_session_id: str | None
    delivery_status: str
    source_message_id: str | None
    openclaw_run_id: str | None


class ChatPersistenceStore:
    """Bounded SQLite store for durable dashboard chat history.

    Thread-affine: one connection per thread / process. uvicorn runs the
    dashboard in a single worker so contention is not a concern in PR3.
    """

    def __init__(
        self,
        path: Path = DEFAULT_PATH,
        *,
        project_id: str,
        agent_id: str,
    ) -> None:
        if not project_id:
            raise ValueError("project_id is required")
        if not agent_id:
            raise ValueError("agent_id is required")
        self.path = Path(path)
        self.project_id = project_id
        self.agent_id = agent_id
        self._ensure_parent_dir()
        # _ensure_schema also performs the first connection which materializes
        # the file; we chmod 0600 immediately afterwards so the file is never
        # readable by other users even briefly.
        self._ensure_schema()
        self._enforce_file_mode()

    # ------------------------------------------------------------------ infra
    def _ensure_parent_dir(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            # Parent may be unwritable; the connect() call below will surface
            # the real error to the caller.
            return
        try:
            self.path.parent.chmod(PARENT_MODE)
        except OSError:
            pass

    def _enforce_file_mode(self) -> None:
        try:
            os.chmod(self.path, FILE_MODE)
        except OSError:
            pass

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        # WAL is required by the PR3 spec (PR2 hardening reviewed by Josh).
        # Failure to set WAL raises — caller will see the real exception.
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _ensure_schema(self) -> None:
        with self._connect() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, SCHEMA_VERSION):
                raise RuntimeError(
                    f"Unsupported engineering-chat schema version: {version} "
                    f"(expected 0 or {SCHEMA_VERSION})"
                )
            connection.executescript(
                """
                BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS engineering_chat_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    openclaw_session_id TEXT,
                    role TEXT NOT NULL CHECK(role IN ('user','assistant')),
                    text TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    source_message_id TEXT,
                    openclaw_run_id TEXT,
                    truncated INTEGER NOT NULL DEFAULT 0,
                    truncation_source TEXT,
                    dedup_key TEXT NOT NULL UNIQUE,
                    delivery_status TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_eng_chat_conv_created
                    ON engineering_chat_messages
                    (project_id, agent_id, conversation_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_eng_chat_created_id
                    ON engineering_chat_messages (created_at, id);
                PRAGMA user_version = 1;
                COMMIT;
                """
            )

    # ------------------------------------------------------------ persistence
    def persist_user_message(
        self,
        *,
        conversation_id: str,
        openclaw_session_id: str | None,
        text: str,
        run_id: str | None,
        iso_timestamp: str,
    ) -> bool:
        """Insert a durable visible user row from a chat.send accept path.

        Returns True if a new row was inserted, False if the dedup_key
        already existed (INSERT OR IGNORE).

        Rejected (pre-RPC) and failed (transport / RPC) chat.send calls
        are NOT persisted; callers MUST NOT call this method for those
        outcomes. See PR3 spec §"SEND FAILURE CONTRACT".
        """
        if not conversation_id:
            raise ValueError("conversation_id is required for user persistence")
        if not text or not text.strip():
            raise ValueError("text is required for user persistence")
        if not iso_timestamp:
            raise ValueError("iso_timestamp is required for user persistence")
        dedup_key = _build_user_dedup_key(
            project_id=self.project_id,
            agent_id=self.agent_id,
            conversation_id=conversation_id,
            run_id=run_id,
            text=text,
            iso_timestamp=iso_timestamp,
            openclaw_session_id=openclaw_session_id,
        )
        return self._insert_message(
            role=ROLE_USER,
            text=text,
            created_at=iso_timestamp,
            conversation_id=conversation_id,
            openclaw_session_id=openclaw_session_id,
            source_message_id=None,
            openclaw_run_id=_string_or_none(run_id),
            truncated=0,
            truncation_source=None,
            dedup_key=dedup_key,
            delivery_status=DELIVERY_STATUS_ACCEPTED,
        )

    def persist_assistant_message(
        self,
        *,
        conversation_id: str,
        openclaw_session_id: str | None,
        source_message_id: str | None,
        response_id: str | None,
        raw_timestamp_ms: int | None,
        text: str,
        truncated: bool,
        truncation_source: str | None,
    ) -> bool:
        """Insert a durable visible assistant row from a chat.history projection.

        Returns True if a new row was inserted, False if the dedup_key
        already existed.

        The caller is expected to have already applied
        `chat_gateway.project_message` (which enforces the role / stopReason /
        delivery-mirror contract). This method only needs the raw fields
        required to synthesize a stable dedup_key plus the projected
        ChatMessage fields.
        """
        if not conversation_id:
            raise ValueError("conversation_id is required for assistant persistence")
        if not text or not text.strip():
            raise ValueError("text is required for assistant persistence")
        created_at = _ms_to_iso_or_now(raw_timestamp_ms)
        dedup_key = _build_assistant_dedup_key(
            project_id=self.project_id,
            agent_id=self.agent_id,
            conversation_id=conversation_id,
            openclaw_session_id=openclaw_session_id,
            source_message_id=source_message_id,
            response_id=response_id,
            raw_timestamp_ms=raw_timestamp_ms,
            text=text,
        )
        return self._insert_message(
            role=ROLE_ASSISTANT,
            text=text,
            created_at=created_at,
            conversation_id=conversation_id,
            openclaw_session_id=openclaw_session_id,
            source_message_id=_string_or_none(source_message_id),
            openclaw_run_id=None,
            truncated=1 if truncated else 0,
            truncation_source=_string_or_none(truncation_source),
            dedup_key=dedup_key,
            delivery_status=DELIVERY_STATUS_PERSISTED,
        )

    def _insert_message(
        self,
        *,
        role: str,
        text: str,
        created_at: str,
        conversation_id: str,
        openclaw_session_id: str | None,
        source_message_id: str | None,
        openclaw_run_id: str | None,
        truncated: int,
        truncation_source: str | None,
        dedup_key: str,
        delivery_status: str,
    ) -> bool:
        assert role in ALLOWED_ROLES, role
        assert delivery_status in ALLOWED_DELIVERY_STATUSES, delivery_status
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO engineering_chat_messages (
                    project_id, agent_id, conversation_id, openclaw_session_id,
                    role, text, created_at, source_message_id, openclaw_run_id,
                    truncated, truncation_source, dedup_key, delivery_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.project_id,
                    self.agent_id,
                    conversation_id,
                    openclaw_session_id,
                    role,
                    text,
                    created_at,
                    source_message_id,
                    openclaw_run_id,
                    truncated,
                    truncation_source,
                    dedup_key,
                    delivery_status,
                ),
            )
            return cursor.rowcount > 0

    # ------------------------------------------------------------------ read
    def list_messages(
        self,
        *,
        conversation_id: str,
        limit: int = 50,
        before_id: int | None = None,
        max_limit: int = 200,
    ) -> list[DurableChatMessage]:
        """Return durable visible messages ordered oldest → newest.

        `limit` is the maximum number of rows to return (default 50).
        `before_id` paginates older than the supplied id; the smallest id
        is returned first.

        `max_limit` is a hard ceiling to prevent an over-large query.
        """
        if not conversation_id:
            raise ValueError("conversation_id is required for list_messages")
        bounded_limit = max(1, min(int(limit), int(max_limit)))
        params: list[Any] = [self.project_id, self.agent_id, conversation_id]
        sql = (
            "SELECT id, role, text, created_at, openclaw_session_id, source_message_id, "
            "openclaw_run_id, truncated, truncation_source, delivery_status "
            "FROM engineering_chat_messages "
            "WHERE project_id = ? AND agent_id = ? AND conversation_id = ?"
        )
        if before_id is not None:
            sql += " AND id < ?"
            params.append(int(before_id))
        # Pull newest-first so LIMIT picks the correct tail; reverse before
        # returning so the response is oldest → newest as the API contract
        # requires.
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(bounded_limit)
        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        rows.reverse()
        return [
            DurableChatMessage(
                id=int(row["id"]),
                role=str(row["role"]),
                text=str(row["text"]),
                timestamp=str(row["created_at"]) if row["created_at"] is not None else None,
                truncated=bool(row["truncated"]),
                truncation_source=row["truncation_source"],
                openclaw_session_id=row["openclaw_session_id"],
                delivery_status=str(row["delivery_status"]),
                source_message_id=row["source_message_id"],
                openclaw_run_id=row["openclaw_run_id"],
            )
            for row in rows
        ]

    # -------------------------------------------------------------- diagnostics
    def count_for_conversation(self, conversation_id: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS n FROM engineering_chat_messages "
                "WHERE project_id = ? AND agent_id = ? AND conversation_id = ?",
                (self.project_id, self.agent_id, conversation_id),
            ).fetchone()
        return int(row["n"])


# ---------------------------------------------------------------------------
# Dedup key synthesis
# ---------------------------------------------------------------------------
def _build_user_dedup_key(
    *,
    project_id: str,
    agent_id: str,
    conversation_id: str,
    run_id: str | None,
    text: str,
    iso_timestamp: str,
    openclaw_session_id: str | None,
) -> str:
    """Build the user-row dedup_key.

    Priority order (locked with Josh 2026-09-08):
      1. chat.send `run_id` (Gateway primary identity for an accepted send).
         Two retries of the same chat.send with the same run_id collapse to
         one row; intentional retries with different run_ids do NOT collapse.
      2. Fallback when Gateway omits run_id: include enough source/time/
         session identity to avoid collapsing two intentional identical user
         messages sent close together. We bucket by iso second and add a
         short text hash so two `hello` sends in the same second on the same
         conversation are still distinguished by their text hash.
      3. Last-resort backstop: server-generated uuid4. This guarantees
         uniqueness even under pathological collision of the priority-2
         inputs. Documented and never reused.
    """
    rid = _string_or_none(run_id)
    if rid:
        return f"u|{project_id}|{agent_id}|{conversation_id}|run:{rid}"
    second_bucket = _iso_to_second_bucket(iso_timestamp)
    text_hash = _short_text_hash(text)
    return (
        f"u|{project_id}|{agent_id}|{conversation_id}|"
        f"ts:{second_bucket}|sid:{openclaw_session_id or ''}|sha:{text_hash}"
    )


def _build_assistant_dedup_key(
    *,
    project_id: str,
    agent_id: str,
    conversation_id: str,
    openclaw_session_id: str | None,
    source_message_id: str | None,
    response_id: str | None,
    raw_timestamp_ms: int | None,
    text: str,
) -> str:
    """Build the assistant-row dedup_key.

    Priority order (locked with Josh 2026-09-08):
      1. Gateway message id (`__openclaw.id`) — present on every message,
         observed unique within a 50-message window. Combined with the
         rotating `openclaw_session_id` so session rotation preserves old
         rows and appends new ones under the same logical conversation_id.
      2. `responseId` (assistant-only) — upstream Anthropic API response id,
         also observed unique. Combined with session_id for the same reason.
      3. Final fallback: epoch-ms timestamp + session identity + short text
         hash. The text hash is ONLY included as a tiebreaker when both the
         Gateway message id AND the model response id are missing; in normal
         operation this branch never fires.
    """
    sid = openclaw_session_id or ""
    mid = _string_or_none(source_message_id)
    if mid:
        return f"a|{project_id}|{agent_id}|{conversation_id}|mid:{mid}|sid:{sid}"
    rid = _string_or_none(response_id)
    if rid:
        return f"a|{project_id}|{agent_id}|{conversation_id}|rid:{rid}|sid:{sid}"
    ts = int(raw_timestamp_ms) if isinstance(raw_timestamp_ms, (int, float)) else 0
    return (
        f"a|{project_id}|{agent_id}|{conversation_id}|"
        f"ts:{ts}|sid:{sid}|sha:{_short_text_hash(text)}"
    )


# ---------------------------------------------------------------------------
# Helpers (module-private)
# ---------------------------------------------------------------------------
def _string_or_none(value: object) -> str | None:
    if isinstance(value, str):
        text = value.strip()
        return text or None
    return None


def _short_text_hash(text: str) -> str:
    """Short (16-char) deterministic text hash used as a fallback tiebreaker."""
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return digest[:16]


def _iso_to_second_bucket(iso_timestamp: str) -> str:
    """Bucket an ISO-8601 timestamp to one-second resolution for stable fallback keys."""
    try:
        parsed = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return "0"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return str(int(parsed.timestamp()))


def _ms_to_iso_or_now(raw_timestamp_ms: int | None) -> str:
    """Convert a Gateway epoch-ms timestamp to ISO-8601 UTC, or fall back to now()."""
    if isinstance(raw_timestamp_ms, (int, float)) and raw_timestamp_ms > 0:
        try:
            seconds = float(raw_timestamp_ms) / 1000.0
            return datetime.fromtimestamp(seconds, tz=UTC).isoformat()
        except (OverflowError, OSError, ValueError):
            pass
    return datetime.now(tz=UTC).isoformat()


def generate_local_user_dedup_fallback() -> str:
    """Generate a uuid4 backstop used when both run_id and iso_timestamp are unusable.

    Exposed so tests can monkey-patch it deterministically without affecting
    the rest of the module.
    """
    return uuid.uuid4().hex


__all__ = [
    "DEFAULT_PATH",
    "SCHEMA_VERSION",
    "FILE_MODE",
    "PARENT_MODE",
    "ROLE_USER",
    "ROLE_ASSISTANT",
    "DELIVERY_STATUS_ACCEPTED",
    "DELIVERY_STATUS_PERSISTED",
    "ALLOWED_ROLES",
    "ALLOWED_DELIVERY_STATUSES",
    "DurableChatMessage",
    "ChatPersistenceStore",
    "generate_local_user_dedup_fallback",
]
