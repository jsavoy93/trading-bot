from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable, Mapping, Protocol

from dashboard_api.chat_gateway import (
    ChatHistory,
    ChatMessage,
    ChatSendResult,
    GatewayChatHistoryClient,
)
from dashboard_api.chat_persistence import ChatPersistenceStore


# ---------------------------------------------------------------------------
# Wiring layer: ChatHistoryProvider → ChatPersistenceStore.
#
# This module is the ONLY place the dashboard-side chat paths (chat.history
# poll + chat.send accept) write to the durable store. Two source paths map
# to two different `delivery_status` values; nothing else writes here.
#
# Visibility contract: the persisting wrapper feeds every raw chat.history
# message through the canonical `chat_gateway.project_message` projection
# so the durable rows are guaranteed to be a subset of what the live
# `/api/engineering/chat/history` endpoint would surface. There is no
# second filter — by design.
# ---------------------------------------------------------------------------


ConversationIdResolver = Callable[[ChatHistory], str | None]


class ChatHistoryProvider(Protocol):
    """Subset of `dashboard_api.chat_gateway.ChatHistoryProvider` the
    persisting wrapper actually consumes. Declared locally so the wrapper
    stays testable without importing the full Protocol surface.
    """

    def history(self) -> ChatHistory:
        ...

    def send(self, message: object) -> ChatSendResult:
        ...


@dataclass(frozen=True)
class PersistingChatHistoryResult:
    """Public result envelope exposed by the wrapper.

    Attributes:
      live: the underlying live ChatHistory (or None on error).
      inserted_user: True iff this call inserted a new durable user row.
      inserted_assistant_count: number of new durable assistant rows
        inserted during this call (counts only NEW rows; duplicates are
        silent per INSERT OR IGNORE).
    """

    live: ChatHistory | None
    inserted_user: bool
    inserted_assistant_count: int


class PersistingChatHistoryProvider:
    """Wraps a live ChatHistoryProvider and persists visible rows durably.

    Persistence contract (locked with Josh on 2026-09-08):
      * `send` accepted (status == "accepted", run_id present) → persist
        ONE durable user row with delivery_status="accepted". The user's
        openclaw_session_id is taken from the resolved history session
        (so the user row's openclaw_session_id matches the assistant
        rows that follow under the same logical conversation).
      * `send` rejected (pre-RPC) or failed (transport/RPC) → NOT
        persisted. No "failed" or "rejected" durable rows in PR3.
      * `history` → after projection, every surviving ChatMessage with
        role="assistant" is persisted with delivery_status="persisted".
        User messages arriving via chat.history are NOT persisted here;
        their durable counterpart comes from the chat.send path so the
        optimistic UI flow owns user-row creation.
    """

    def __init__(
        self,
        underlying: ChatHistoryProvider,
        store: ChatPersistenceStore,
        *,
        conversation_id_resolver: ConversationIdResolver | None = None,
    ) -> None:
        self._underlying = underlying
        self._store = store
        # The resolver returns the logical conversation_id for a given
        # resolved ChatHistory; default is the resolved session key
        # (stable across session rotation for Trading Manager).
        self._resolver: ConversationIdResolver = (
            conversation_id_resolver
            if conversation_id_resolver is not None
            else _default_conversation_id_resolver
        )

    # --------------------------------------------------------------- chat.send
    def send(self, message: object) -> ChatSendResult:
        result = self._underlying.send(message)
        # Pass-through tolerance: if the underlying provider returns
        # something other than a ChatSendResult (e.g. a plain dict in a
        # test fixture), do not attempt persistence; just return it. The
        # production wiring uses GatewayChatHistoryClient which always
        # returns ChatSendResult.
        if not isinstance(result, ChatSendResult):
            return result
        if result.status != "accepted" or not result.run_id:
            # Rejected (pre-RPC) or failed (transport / RPC) → NOT persisted.
            return result
        # Resolve conversation_id from the underlying live history so the
        # user row joins the same logical conversation as the assistant
        # rows that follow. Failure to resolve history MUST NOT block
        # persistence of the user row (we fall back to a sentinel and
        # still persist).
        history = self._safe_history()
        conversation_id, session_id = self._resolve_ids(history)
        if not conversation_id:
            conversation_id = "unknown-conversation"
        text = _string_or_empty(message)
        # Persist on a best-effort basis: persistence failures MUST NOT
        # change the user-visible chat.send result (the run is already
        # accepted on the Gateway side; failing locally would falsely
        # surface the run as failed).
        try:
            self._store.persist_user_message(
                conversation_id=conversation_id,
                openclaw_session_id=session_id,
                text=text,
                run_id=result.run_id,
                iso_timestamp=result.timestamp or _now_iso(),
            )
        except Exception:
            # Persistence failure is logged by the store path; chat.send
            # remains a success.
            pass
        return result

    # ------------------------------------------------------------ chat.history
    def history(self) -> ChatHistory:
        history = self._underlying.history()
        # Pass-through tolerance: if the underlying provider returns
        # something other than a ChatHistory (e.g. a plain dict in a
        # test fixture), do not attempt persistence; just return it.
        if not isinstance(history, ChatHistory):
            return history
        if history.status != "available":
            return history
        conversation_id, session_id = self._resolve_ids(history)
        if not conversation_id:
            return history
        # Walk the surviving ChatMessage list (already projection-filtered
        # by _project_gateway_history) and persist assistant rows. We
        # intentionally only feed the projected ChatMessage — never raw
        # messages — so the durable store inherits the canonical visibility
        # contract for free. ChatMessage.source_message_id, response_id,
        # raw_timestamp_ms are populated by chat_gateway.project_message.
        for message in history.messages:
            if not isinstance(message, ChatMessage):
                continue
            if message.role != "assistant":
                continue
            self._safe_persist_assistant(conversation_id, session_id, message)
        return history

    def history_with_persistence_count(self) -> PersistingChatHistoryResult:
        """Variant of history() that also reports the durable insert count.

        Not used by the live API (which doesn't need the count), but
        exposed for tests and ops tooling.
        """
        history = self._underlying.history()
        inserted_user = False
        inserted_assistant = 0
        if history.status != "available":
            return PersistingChatHistoryResult(history, inserted_user, inserted_assistant)
        conversation_id, session_id = self._resolve_ids(history)
        if not conversation_id:
            return PersistingChatHistoryResult(history, inserted_user, inserted_assistant)
        for message in history.messages:
            if message.role != "assistant":
                continue
            try:
                if self._store.persist_assistant_message(
                    conversation_id=conversation_id,
                    openclaw_session_id=session_id,
                    source_message_id=message.source_message_id,
                    response_id=message.response_id,
                    raw_timestamp_ms=message.raw_timestamp_ms,
                    text=message.text,
                    truncated=message.truncated,
                    truncation_source=message.truncation_source,
                ):
                    inserted_assistant += 1
            except Exception:
                pass
        return PersistingChatHistoryResult(history, inserted_user, inserted_assistant)

    # ------------------------------------------------------------- internals
    def _safe_history(self) -> ChatHistory | None:
        try:
            return self._underlying.history()
        except Exception:
            return None

    def _resolve_ids(self, history: ChatHistory | None) -> tuple[str | None, str | None]:
        if history is None:
            return None, None
        try:
            cid = self._resolver(history)
        except Exception:
            cid = None
        return cid, history.resolved_session_id

    def _safe_persist_assistant(
        self,
        conversation_id: str,
        session_id: str | None,
        message: ChatMessage,
    ) -> None:
        try:
            self._store.persist_assistant_message(
                conversation_id=conversation_id,
                openclaw_session_id=session_id,
                source_message_id=message.source_message_id,
                response_id=message.response_id,
                raw_timestamp_ms=message.raw_timestamp_ms,
                text=message.text,
                truncated=message.truncated,
                truncation_source=message.truncation_source,
            )
        except Exception:
            # Persistence failure must never break the live history call.
            pass


# ---------------------------------------------------------------------------
# Default conversation_id resolver.
# ---------------------------------------------------------------------------
def _default_conversation_id_resolver(history: ChatHistory) -> str | None:
    """Default resolver: prefer resolved_session_key; fall back to agent.

    For Trading Manager this returns the stable OpenClaw session key
    (`agent:trading-manager:telegram:direct:8455029949`) which DOES NOT
    rotate across OpenClaw trajectory rotation. See PR3 spec §"Identifier
    semantics".
    """
    if history.resolved_session_key:
        return history.resolved_session_key
    if history.agent:
        return f"agent:{history.agent}"
    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _string_or_empty(value: object) -> str:
    if isinstance(value, str):
        return value
    return ""


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _iso_to_ms(iso: str | None) -> int | None:
    if not isinstance(iso, str) or not iso:
        return None
    try:
        parsed = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return int(parsed.timestamp() * 1000)


def build_default_persisting_provider(
    store: ChatPersistenceStore,
    *,
    underlying: ChatHistoryProvider | None = None,
    conversation_id_resolver: ConversationIdResolver | None = None,
) -> PersistingChatHistoryProvider:
    """Convenience constructor for the production wiring.

    Defaults the underlying provider to a fresh `GatewayChatHistoryClient`
    so app.py can simply call `build_default_persisting_provider(store)`.
    """
    inner: ChatHistoryProvider = underlying or GatewayChatHistoryClient()
    return PersistingChatHistoryProvider(
        inner,
        store,
        conversation_id_resolver=conversation_id_resolver,
    )


__all__ = [
    "ChatHistoryProvider",
    "ConversationIdResolver",
    "PersistingChatHistoryResult",
    "PersistingChatHistoryProvider",
    "build_default_persisting_provider",
]
