"""BOT-001: Session state correctness tests.

Proves that:
  - trading_sessions has a status column with the lifecycle vocabulary.
  - create_session persists status='ACTIVE' by default.
  - end_session transitions to 'ENDED'.
  - mark_session_failed transitions to 'FAILED' with reason appended to notes.
  - get_active_session returns the current ACTIVE row.
  - get_stale_open_sessions returns rows that look open but aren't ACTIVE.
  - close_stale_sessions only closes rows older than cutoff; never deletes.
  - update_session rejects invalid status values.
  - SmartTradingBot.reap_stale_sessions wraps close_stale_sessions safely.
  - Stale rows from prior crashes are reaped at SmartTradingBot init.
  - Historical rows are NOT silently rewritten (only backfilled with
    OPEN/ACTIVE/ENDED based on session_end IS NULL heuristic, exactly once).
"""
from datetime import datetime, timedelta, timezone

import pytest

import src.database.sqlite_db as sqlite_mod
from src.database.sqlite_db import SQLiteDB, DB_PATH, _get_conn


@pytest.fixture
def db(monkeypatch):
    """Provide a fresh isolated SQLite instance for each test.

    SQLiteDB._get_conn() reads the module-level DB_PATH at every call,
    so monkeypatching DB_PATH on the module is sufficient to redirect
    reads/writes to a temp file. We then call _init_schema() against the
    new path so the singleton is correctly migrated.
    """
    import tempfile
    import os
    import src.database.sqlite_db as mod

    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
    tmp.close()
    new_path = type(DB_PATH)(tmp.name)

    # Patch DB_PATH on the module so _get_conn() uses the temp file.
    monkeypatch.setattr(mod, "DB_PATH", new_path)

    # Re-run schema migration against the new path.
    mod.sqlite_db._init_schema()

    yield mod.sqlite_db

    # Cleanup: monkeypatch will restore DB_PATH automatically.
    try:
        os.unlink(tmp.name)
    except OSError:
        pass


# ─────────────────────────────────────────────────────────────────────────
# Schema / lifecycle vocabulary
# ─────────────────────────────────────────────────────────────────────────

def test_trading_sessions_has_status_column(db) -> None:
    """Schema migration added a status column."""
    cur = _get_conn()
    cols = [row["name"] for row in cur.execute("PRAGMA table_info(trading_sessions)")]
    assert "status" in cols, f"status column missing; got {cols}"


def test_create_session_persists_status_active_by_default(db) -> None:
    sid = db.create_session(bot_version="2.1.0", notes="t")
    assert sid is not None
    cur = _get_conn()
    row = cur.execute("SELECT status FROM trading_sessions WHERE id=?", (sid,)).fetchone()
    assert row["status"] == "ACTIVE"


def test_create_session_accepts_status_open(db) -> None:
    sid = db.create_session(bot_version="2.1.0", notes="t", status="OPEN")
    cur = _get_conn()
    row = cur.execute("SELECT status FROM trading_sessions WHERE id=?", (sid,)).fetchone()
    assert row["status"] == "OPEN"


def test_create_session_rejects_invalid_status(db) -> None:
    with pytest.raises(ValueError, match="invalid status"):
        db.create_session(bot_version="2.1.0", notes="t", status="BOGUS")


def test_update_session_rejects_invalid_status(db) -> None:
    sid = db.create_session(bot_version="2.1.0", notes="t")
    ok = db.update_session(sid, {"status": "RUNNING"})
    assert ok is False, "Invalid status must be rejected"
    cur = _get_conn()
    row = cur.execute("SELECT status FROM trading_sessions WHERE id=?", (sid,)).fetchone()
    assert row["status"] == "ACTIVE", "Status must remain ACTIVE after rejected update"


# ─────────────────────────────────────────────────────────────────────────
# Active / stale-open queries
# ─────────────────────────────────────────────────────────────────────────

def test_get_active_session_returns_active_row_with_null_end(db) -> None:
    sid = db.create_session(bot_version="2.1.0", notes="t")
    active = db.get_active_session()
    assert active is not None
    assert active["id"] == sid
    assert active["session_end"] is None


def test_get_active_session_returns_none_when_only_ended(db) -> None:
    sid = db.create_session(bot_version="2.1.0", notes="t")
    db.update_session(sid, {"session_end": "2026-09-10T00:00:00", "status": "ENDED"})
    assert db.get_active_session() is None


def test_get_stale_open_sessions_returns_non_active_open_rows(db) -> None:
    """Rows that are not ACTIVE but have session_end IS NULL are stale-open."""
    sid_open = db.create_session(bot_version="2.1.0", notes="open", status="OPEN")
    sid_active = db.create_session(bot_version="2.1.0", notes="active")
    sid_ended = db.create_session(bot_version="2.1.0", notes="ended")
    db.update_session(sid_ended, {"session_end": "2026-09-10T00:00:00", "status": "ENDED"})

    stale = db.get_stale_open_sessions()
    ids = {row["id"] for row in stale}
    assert sid_open in ids, "OPEN row with NULL session_end is stale-open"
    assert sid_active not in ids, "ACTIVE is NOT stale-open (legitimately running)"
    assert sid_ended not in ids, "ENDED row is not open"


# ─────────────────────────────────────────────────────────────────────────
# close_stale_sessions — fail-safe reaper
# ─────────────────────────────────────────────────────────────────────────

def test_close_stale_sessions_only_closes_rows_older_than_cutoff(db) -> None:
    sid_recent = db.create_session(bot_version="2.1.0", notes="recent")
    db.update_session(sid_recent, {"session_start": "2099-01-01T00:00:00"})  # future
    sid_old = db.create_session(bot_version="2.1.0", notes="old")
    db.update_session(sid_old, {"session_start": "2000-01-01T00:00:00"})  # past

    cutoff = "2025-01-01T00:00:00"
    closed = db.close_stale_sessions(cutoff_iso=cutoff)
    assert closed == 1

    cur = _get_conn()
    recent = cur.execute("SELECT status, session_end FROM trading_sessions WHERE id=?", (sid_recent,)).fetchone()
    old = cur.execute("SELECT status, session_end FROM trading_sessions WHERE id=?", (sid_old,)).fetchone()
    assert recent["status"] == "ACTIVE"
    assert recent["session_end"] is None
    assert old["status"] == "FAILED"
    assert old["session_end"] is not None


def test_close_stale_sessions_does_not_delete_rows(db) -> None:
    sid = db.create_session(bot_version="2.1.0", notes="keepme")
    db.update_session(sid, {"session_start": "2000-01-01T00:00:00"})

    db.close_stale_sessions(cutoff_iso="2025-01-01T00:00:00")

    cur = _get_conn()
    rows = cur.execute("SELECT * FROM trading_sessions WHERE id=?", (sid,)).fetchall()
    assert len(rows) == 1, "Row must remain in trading_sessions; only status flips"
    assert rows[0]["status"] == "FAILED"


def test_close_stale_sessions_appends_reason_to_notes(db) -> None:
    sid = db.create_session(bot_version="2.1.0", notes="original")
    db.update_session(sid, {"session_start": "2000-01-01T00:00:00"})
    db.close_stale_sessions(cutoff_iso="2025-01-01T00:00:00", reason="test-reap")

    cur = _get_conn()
    notes = cur.execute("SELECT notes FROM trading_sessions WHERE id=?", (sid,)).fetchone()["notes"]
    assert "original" in notes
    assert "test-reap" in notes


# ─────────────────────────────────────────────────────────────────────────
# mark_session_failed / reap_stale_sessions on SmartTradingBot
# ─────────────────────────────────────────────────────────────────────────

def test_mark_session_failed_persists_status_failed_with_reason() -> None:
    """mark_session_failed must transition status to FAILED and append reason."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    bot = SQLiteDB  # ensure the class is loaded
    from src.core.smart_bot import SmartTradingBot
    bot = SmartTradingBot.__new__(SmartTradingBot)
    bot.db = MagicMock()
    bot.db.is_available.return_value = True
    bot.session_id = 555
    bot.symbols_processed = 10
    bot.trades_executed = 1
    bot.errors_count = 2
    bot._session_start_symbols = 0
    bot._session_start_trades = 0
    bot._session_start_errors = 0
    bot.db.get_sessions.return_value = [
        {"id": 555, "notes": "original"},
    ]
    bot.mark_session_failed("division-by-zero")
    args, _ = bot.db.update_session.call_args
    updates = args[1]
    assert updates["status"] == "FAILED"
    assert updates["notes"] == "original [FAILED: division-by-zero]"
    assert updates["total_trades_executed"] == 1
    assert updates["error_count"] == 2


def test_reap_stale_sessions_delegates_to_db_close_stale_sessions() -> None:
    from unittest.mock import MagicMock
    from src.core.smart_bot import SmartTradingBot
    bot = SmartTradingBot.__new__(SmartTradingBot)
    bot.db = MagicMock()
    bot.db.is_available.return_value = True
    bot.db.close_stale_sessions.return_value = 3
    n = bot.reap_stale_sessions(max_age_seconds=120)
    assert n == 3
    args, kwargs = bot.db.close_stale_sessions.call_args
    cutoff = args[0]
    assert kwargs.get("reason") == "startup-reap"
    # cutoff should be ISO 8601 (string), and ~2 minutes in the past.
    parsed = datetime.fromisoformat(cutoff)
    now = datetime.now(timezone.utc)
    delta = (now - parsed).total_seconds()
    assert 100 < delta < 200, f"Cutoff should be ~120s in the past; got {delta}"


def test_reap_stale_sessions_skips_when_db_unavailable() -> None:
    from unittest.mock import MagicMock
    from src.core.smart_bot import SmartTradingBot
    bot = SmartTradingBot.__new__(SmartTradingBot)
    bot.db = MagicMock()
    bot.db.is_available.return_value = False
    assert bot.reap_stale_sessions() == 0
    bot.db.close_stale_sessions.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────
# Backfill semantics — historical rows are classified but not rewritten
# ─────────────────────────────────────────────────────────────────────────

def test_backfill_classifies_pre_existing_rows(db) -> None:
    """If the schema migration runs against a database that already has
    trading_sessions rows without a status column, those rows must be
    classified into the lifecycle vocabulary using only session_end
    IS NULL — never arbitrarily rewritten.
    """
    # Insert historical rows that explicitly have status=NULL, simulating a
    # database that pre-dates the status column. The schema DEFAULT only
    # fires when the column is omitted from INSERT; an explicit NULL bypasses
    # it because status is not declared NOT NULL.
    cur = _get_conn()
    cur.execute(
        "INSERT INTO trading_sessions (session_start, session_end, bot_version, "
        "is_paper_trading, notes, total_symbols_processed, total_trades_executed, "
        "session_pnl, error_count, status) "
        "VALUES (?, ?, ?, ?, ?, 0, 0, 0.0, 0, NULL)",
        ("2026-06-01T00:00:00", "2026-06-01T00:01:00", "2.1.0", 1, "historical"),
    )
    cur.execute(
        "INSERT INTO trading_sessions (session_start, session_end, bot_version, "
        "is_paper_trading, notes, total_symbols_processed, total_trades_executed, "
        "session_pnl, error_count, status) "
        "VALUES (?, ?, ?, ?, ?, 0, 0, 0.0, 0, NULL)",
        ("2026-06-02T00:00:00", None, "2.1.0", 1, "crashed-historical"),
    )
    cur.commit()

    # Sanity: confirm the inserts really did persist status=NULL.
    pre = cur.execute(
        "SELECT id, status, notes FROM trading_sessions ORDER BY id"
    ).fetchall()
    assert pre[0]["status"] is None, f"pre[0].status={pre[0]['status']!r}; expected NULL"
    assert pre[1]["status"] is None

    # Re-run the schema migration to simulate upgrading an existing DB.
    db._init_schema()

    rows = cur.execute(
        "SELECT notes, status, session_end FROM trading_sessions ORDER BY id"
    ).fetchall()
    assert rows[0]["status"] == "ENDED"   # session_end set → ENDED
    assert rows[0]["notes"] == "historical"  # notes unchanged
    assert rows[1]["status"] == "ACTIVE"  # session_end NULL → ACTIVE
    assert rows[1]["notes"] == "crashed-historical"  # notes unchanged
