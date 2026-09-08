from __future__ import annotations

from typing import Protocol

from dashboard_api.chat_gateway import ChatMessage
from dashboard_api.chat_persistence import (
    ChatPersistenceStore,
    DurableChatMessage,
)


DEFAULT_LIMIT = 50
MAX_LIMIT = 200


class ChatHistoryDurableProvider(Protocol):
    """Read-side protocol for the durable engineering-chat history.

    Implementations MUST be project/agent-scoped: the browser never picks
    conversation_id; the provider is bound to the trading-manager
    conversation at construction time.
    """

    def latest(self, *, limit: int = DEFAULT_LIMIT) -> list[ChatMessage]:
        ...

    def older_than(self, *, before_id: int, limit: int = DEFAULT_LIMIT) -> list[ChatMessage]:
        ...


class StoreBackedChatHistoryDurableProvider:
    """Project/agent-scoped durable chat history reader.

    Uses `ChatPersistenceStore` for reads and projects the stored
    `DurableChatMessage` rows back into the dashboard `ChatMessage` shape
    so the API surface matches the live `/api/engineering/chat/history`
    endpoint byte-for-byte (the PR4 UI switch is just a different URL).
    """

    def __init__(
        self,
        store: ChatPersistenceStore,
        *,
        conversation_id: str,
    ) -> None:
        if not conversation_id:
            raise ValueError("conversation_id is required")
        self._store = store
        self._conversation_id = conversation_id

    def latest(self, *, limit: int = DEFAULT_LIMIT) -> list[ChatMessage]:
        rows = self._store.list_messages(
            conversation_id=self._conversation_id,
            limit=_bounded_limit(limit),
        )
        return [_to_chat_message(row) for row in rows]

    def older_than(self, *, before_id: int, limit: int = DEFAULT_LIMIT) -> list[ChatMessage]:
        if before_id is None or int(before_id) <= 0:
            raise ValueError("before_id must be a positive integer")
        rows = self._store.list_messages(
            conversation_id=self._conversation_id,
            before_id=int(before_id),
            limit=_bounded_limit(limit),
        )
        return [_to_chat_message(row) for row in rows]


def _bounded_limit(limit: int) -> int:
    try:
        n = int(limit)
    except (TypeError, ValueError):
        return DEFAULT_LIMIT
    return max(1, min(n, MAX_LIMIT))


def _to_chat_message(row: DurableChatMessage) -> ChatMessage:
    return ChatMessage(
        role=row.role,
        text=row.text,
        timestamp=row.timestamp,
        truncated=row.truncated,
        truncation_source=row.truncation_source,
    )


__all__ = [
    "ChatHistoryDurableProvider",
    "StoreBackedChatHistoryDurableProvider",
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
]
