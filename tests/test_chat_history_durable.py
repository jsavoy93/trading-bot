"""Tests for the PR3 durable chat-history read API."""

from __future__ import annotations

from pathlib import Path

import pytest

from dashboard_api.chat_history_durable import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    StoreBackedChatHistoryDurableProvider,
)
from dashboard_api.chat_persistence import ChatPersistenceStore


CONVERSATION_ID = "agent:trading-manager:telegram:direct:8455029949"


@pytest.fixture
def store(tmp_path):
    db_path = tmp_path / "engineering-chat.sqlite3"
    return ChatPersistenceStore(
        db_path,
        project_id="trading-bot",
        agent_id="trading-manager",
    )


@pytest.fixture
def seeded_store(store):
    """Pre-populate 60 assistant rows + 5 user rows for pagination tests."""
    for i in range(60):
        store.persist_assistant_message(
            conversation_id=CONVERSATION_ID,
            openclaw_session_id="session-A",
            source_message_id=f"mid-{i:03d}",
            response_id=f"resp-{i:03d}",
            raw_timestamp_ms=1788869000000 + i,
            text=f"assistant row {i:03d}",
            truncated=False,
            truncation_source=None,
        )
    for i in range(5):
        store.persist_user_message(
            conversation_id=CONVERSATION_ID,
            openclaw_session_id="session-A",
            text=f"user row {i}",
            run_id=f"run-{i}",
            iso_timestamp="2026-09-08T12:30:00+00:00",
        )
    return store


def test_latest_returns_oldest_first_within_default_limit(seeded_store):
    provider = StoreBackedChatHistoryDurableProvider(
        seeded_store, conversation_id=CONVERSATION_ID
    )
    latest = provider.latest()
    assert len(latest) == DEFAULT_LIMIT
    # The 60 assistant rows are inserted first; the 5 user rows come after.
    # latest(50) returns the newest 50 = the 5 user rows + the 45 newest
    # assistant rows. Within those 50, ordering is oldest → newest.
    assert latest[0].text == "assistant row 015"
    assert latest[-1].text == "user row 4"


def test_latest_respects_explicit_limit(seeded_store):
    provider = StoreBackedChatHistoryDurableProvider(
        seeded_store, conversation_id=CONVERSATION_ID
    )
    latest = provider.latest(limit=10)
    assert len(latest) == 10
    # The newest 10 = the 5 user rows + the 5 newest assistant rows.
    assert latest[0].text == "assistant row 055"
    assert latest[-1].text == "user row 4"


def test_older_than_paginates_backwards(seeded_store):
    provider = StoreBackedChatHistoryDurableProvider(
        seeded_store, conversation_id=CONVERSATION_ID
    )
    latest = provider.latest(limit=10)
    # The first element of `latest` is the OLDEST of the 10 newest. Page
    # backwards from its id.
    cursor = latest[0].id if hasattr(latest[0], "id") else None
    # ChatMessage doesn't carry id (it's the public projection), so use the
    # store directly to get the cursor.
    cursor_rows = seeded_store.list_messages(
        conversation_id=CONVERSATION_ID, limit=10
    )
    cursor = cursor_rows[0].id
    page = provider.older_than(before_id=cursor, limit=10)
    # The page is 10 rows strictly older than the cursor.
    assert len(page) == 10
    # Should be assistant rows 045..054 (10 older than 055).
    assert page[0].text == "assistant row 045"
    assert page[-1].text == "assistant row 054"


def test_limit_is_bounded_by_max_limit(seeded_store):
    provider = StoreBackedChatHistoryDurableProvider(
        seeded_store, conversation_id=CONVERSATION_ID
    )
    latest = provider.latest(limit=99999)
    assert len(latest) <= MAX_LIMIT


def test_older_than_rejects_non_positive_cursor(seeded_store):
    provider = StoreBackedChatHistoryDurableProvider(
        seeded_store, conversation_id=CONVERSATION_ID
    )
    with pytest.raises(ValueError):
        provider.older_than(before_id=0, limit=10)
    with pytest.raises(ValueError):
        provider.older_than(before_id=-5, limit=10)


def test_provider_scopes_by_conversation_id(seeded_store):
    """A different conversation_id MUST NOT see rows seeded under the canonical one."""
    provider = StoreBackedChatHistoryDurableProvider(
        seeded_store,
        conversation_id="different-conversation-id",
    )
    latest = provider.latest()
    assert latest == []


def test_empty_store_returns_empty_list(store):
    provider = StoreBackedChatHistoryDurableProvider(
        store, conversation_id=CONVERSATION_ID
    )
    assert provider.latest() == []
    # Pagination on empty store is also empty.
    assert provider.older_than(before_id=1, limit=10) == []


# Sanity: the durable provider must NOT crash on a missing conversation.
def test_unknown_conversation_returns_empty(store):
    provider = StoreBackedChatHistoryDurableProvider(
        store, conversation_id="never-seeded"
    )
    assert provider.latest() == []
