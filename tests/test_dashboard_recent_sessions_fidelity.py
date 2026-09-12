"""Dashboard Recent Sessions fidelity tests.

Proves the read-model fix for History → Recent Sessions without changing any
runtime bot behavior (SmartTradingBot, scoring, eligibility, ranking,
execution, sizing, risk, brokerage, OBS-001 decision persistence, or fixture
data are not modified).

Acceptance criteria covered:

1. Post-OBS-001 session with N distinct decision_history symbols displays
   Symbols = N (decision_history ground truth).
2. Repeated analysis of the same symbol across multiple cycles in one
   session counts once (DISTINCT semantic).
3. Legacy session with NO decision_history rows falls back to
   trading_sessions.total_symbols_processed.
4. Trade count comes from the actual trades table, not from the broken
   trading_sessions.total_trades_executed scalar.
5. Future-dated session (e.g. 2099-01-01) does not appear ahead of valid
   recent sessions in the Recent Sessions read model.
6. The future-dated fixture row remains UNCHANGED in the database (the
   filter is read-layer only; no mutation, no deletion).
7. Valid recent sessions sort by session_start DESC then id DESC.
8. Sessions with no trades rows correctly render Trades = 0.
9. No semantic trading changes: only read-layer corrections, and only
   files explicitly allowed by this task are touched.
"""

import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone

import pytest


# ─────────────────────────────────────────────────────────────────────────
# Fixtures and helpers
# ─────────────────────────────────────────────────────────────────────────


def _now_utc_iso(offset_seconds: int = 0) -> str:
    return (
        datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)
    ).isoformat()


def _build_isolated_db(monkeypatch):
    """Patch DB_PATH on both module references the dashboard uses, init the
    schema, and return (sqlite_mod, db_path)."""
    import src.database.sqlite_db as sqlite_mod_src

    if "/root/.openclaw/workspace/trading-bot/src" not in sys.path:
        sys.path.insert(0, "/root/.openclaw/workspace/trading-bot/src")
    import database.sqlite_db as sqlite_mod

    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
    tmp.close()
    new_path = type(sqlite_mod.DB_PATH)(tmp.name)
    monkeypatch.setattr(sqlite_mod, "DB_PATH", new_path)
    monkeypatch.setattr(sqlite_mod_src, "DB_PATH", new_path)

    sqlite_mod.sqlite_db._init_schema()
    return sqlite_mod, tmp.name


def _seed_session(
    db_path: str,
    session_id: int,
    session_start: str,
    session_end: str = None,
    status: str = "ENDED",
    total_symbols_processed: int = 0,
    total_trades_executed: int = 0,
    notes: str = "",
) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO trading_sessions
              (id, session_start, session_end, bot_version, configuration,
               is_paper_trading, total_symbols_processed, total_trades_executed,
               session_pnl, error_count, status, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                session_start,
                session_end,
                "2.1.0",
                "{}",
                1,
                total_symbols_processed,
                total_trades_executed,
                0.0,
                0,
                status,
                notes,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _seed_decision(
    db_path: str,
    session_id: int,
    symbol: str,
    cycle_id: str = "cycle_test",
    cycle_start: str = None,
) -> None:
    if cycle_start is None:
        cycle_start = _now_utc_iso()
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO decision_history
              (cycle_id, symbol, cycle_start, session_id,
               decision_schema_version, decision_snapshot, created_at)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (
                cycle_id,
                symbol,
                cycle_start,
                session_id,
                1,
                '{"schema_version": 1, "decision": "HOLD_INELIGIBLE"}',
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _seed_trade(
    db_path: str,
    session_id: int,
    symbol: str = "AAPL",
    side: str = "BUY",
) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO trades (session_id, symbol, side, qty, price, signal)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (session_id, symbol, side, 1.0, 100.0, side),
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def isolated_recent_sessions_db(monkeypatch):
    sqlite_mod, db_path = _build_isolated_db(monkeypatch)
    yield sqlite_mod, db_path
    try:
        os.unlink(db_path)
    except OSError:
        pass


@pytest.fixture
def dashboard_module(monkeypatch):
    """Import the dashboard module with the same isolation helpers used by
    the existing dashboard tests."""
    if "/root/.openclaw/workspace/trading-bot" not in sys.path:
        sys.path.insert(0, "/root/.openclaw/workspace/trading-bot")
    import dashboard as dash_module

    # Stub the Alpaca client so importing dashboard.py doesn't reach the
    # network. The Recent Sessions helpers do not call it, but the module
    # constructs one at import time.
    class _StubAccount:
        portfolio_value = "100000.0"
        cash = "50000.0"
        buying_power = "200000.0"
        pattern_day_trader = False
        trading_blocked = False
        transfers_blocked = False

    class _StubTradingClient:
        def get_account(self_inner):
            return _StubAccount()

    dash_module.trading_client = _StubTradingClient()
    return dash_module


# ─────────────────────────────────────────────────────────────────────────
# AC-1: post-OBS-001 session with N distinct symbols renders Symbols = N
# ─────────────────────────────────────────────────────────────────────────


def test_post_obs_session_shows_distinct_symbols_from_decision_history(
    isolated_recent_sessions_db, dashboard_module
):
    """A session with 30 distinct decision_history symbols must render
    Symbols = 30, sourced from decision_history (NOT the broken scalar)."""
    sqlite_mod, db_path = isolated_recent_sessions_db
    session_start = _now_utc_iso(offset_seconds=-30)
    _seed_session(
        db_path,
        session_id=1001,
        session_start=session_start,
        # Deliberately set the broken scalar to a wrong value to prove the
        # read model ignores it when decision_history exists.
        total_symbols_processed=999,
        total_trades_executed=999,
    )
    for i in range(30):
        _seed_decision(
            db_path,
            session_id=1001,
            symbol=f"SYM{i:02d}",
            cycle_id=f"cycle_1001_{i}",
        )

    sessions = dashboard_module.get_recent_sessions_with_truthful_counts(limit=5)
    assert len(sessions) == 1, "expected exactly the one seeded session"
    row = sessions[0]
    assert row["id"] == 1001
    assert row["symbols_count"] == 30, (
        f"expected Symbols=30 from decision_history ground truth; "
        f"got {row['symbols_count']} (source={row.get('symbols_source')})"
    )
    assert row["symbols_source"] == "decision_history"
    assert row["trades_count"] == 0
    # Legacy scalar is preserved on the dict but not used for display.
    assert row["total_symbols_processed"] == 999


# ─────────────────────────────────────────────────────────────────────────
# AC-2: repeated symbol across multiple cycles counts once
# ─────────────────────────────────────────────────────────────────────────


def test_repeated_symbol_across_cycles_counts_once(
    isolated_recent_sessions_db, dashboard_module
):
    """Two cycles that share symbols must count the union of distinct
    symbols, not the total number of decision rows."""
    sqlite_mod, db_path = isolated_recent_sessions_db
    session_start = _now_utc_iso(offset_seconds=-20)
    _seed_session(db_path, session_id=2002, session_start=session_start)

    cycle_a_start = _now_utc_iso(offset_seconds=-20)
    cycle_b_start = _now_utc_iso(offset_seconds=-15)
    for sym in ["AAPL", "MSFT"]:
        _seed_decision(
            db_path, session_id=2002, symbol=sym,
            cycle_id="c_a", cycle_start=cycle_a_start,
        )
    for sym in ["AAPL", "MSFT", "GOOG"]:
        _seed_decision(
            db_path, session_id=2002, symbol=sym,
            cycle_id="c_b", cycle_start=cycle_b_start,
        )

    sessions = dashboard_module.get_recent_sessions_with_truthful_counts(limit=5)
    assert len(sessions) == 1
    assert sessions[0]["symbols_count"] == 3, (
        "Symbols must be DISTINCT across cycles; expected 3 for "
        "{AAPL, MSFT, GOOG}, got "
        f"{sessions[0]['symbols_count']}"
    )


# ─────────────────────────────────────────────────────────────────────────
# AC-3: legacy session without decision_history falls back to scalar
# ─────────────────────────────────────────────────────────────────────────


def test_legacy_session_without_decision_history_uses_scalar(
    isolated_recent_sessions_db, dashboard_module
):
    """A pre-OBS-001 session that has no decision_history rows must fall
    back to trading_sessions.total_symbols_processed."""
    sqlite_mod, db_path = isolated_recent_sessions_db
    session_start = _now_utc_iso(offset_seconds=-10)
    _seed_session(
        db_path,
        session_id=3003,
        session_start=session_start,
        total_symbols_processed=42,
    )

    sessions = dashboard_module.get_recent_sessions_with_truthful_counts(limit=5)
    assert len(sessions) == 1
    row = sessions[0]
    assert row["symbols_count"] == 42, (
        "Legacy session without decision_history must fall back to "
        f"total_symbols_processed=42; got {row['symbols_count']}"
    )
    assert row["symbols_source"] == "legacy_scalar"


# ─────────────────────────────────────────────────────────────────────────
# AC-4: trade count comes from the trades table
# ─────────────────────────────────────────────────────────────────────────


def test_trade_count_comes_from_trades_table(
    isolated_recent_sessions_db, dashboard_module
):
    """Trades must equal COUNT(trades.id) WHERE trades.session_id = X.
    The legacy trading_sessions.total_trades_executed scalar is NOT used."""
    sqlite_mod, db_path = isolated_recent_sessions_db
    session_start = _now_utc_iso(offset_seconds=-25)
    _seed_session(
        db_path,
        session_id=4004,
        session_start=session_start,
        total_trades_executed=999,  # wrong scalar value
    )
    _seed_trade(db_path, session_id=4004, symbol="AAPL", side="BUY")
    _seed_trade(db_path, session_id=4004, symbol="MSFT", side="BUY")

    sessions = dashboard_module.get_recent_sessions_with_truthful_counts(limit=5)
    assert len(sessions) == 1
    row = sessions[0]
    assert row["trades_count"] == 2, (
        f"Trades must come from trades table; expected 2, got {row['trades_count']}"
    )
    # Legacy scalar is preserved but ignored for display.
    assert row["total_trades_executed"] == 999


def test_session_with_no_trades_shows_zero(
    isolated_recent_sessions_db, dashboard_module
):
    """A session with zero rows in the trades table must render Trades = 0,
    even if the legacy scalar is non-zero."""
    sqlite_mod, db_path = isolated_recent_sessions_db
    session_start = _now_utc_iso(offset_seconds=-30)
    _seed_session(
        db_path,
        session_id=4005,
        session_start=session_start,
        total_trades_executed=7,  # stale scalar
    )

    sessions = dashboard_module.get_recent_sessions_with_truthful_counts(limit=5)
    assert len(sessions) == 1
    assert sessions[0]["trades_count"] == 0


# ─────────────────────────────────────────────────────────────────────────
# AC-5 + AC-6: future fixture excluded from Recent Sessions, row unchanged
# ─────────────────────────────────────────────────────────────────────────


def test_future_fixture_excluded_and_db_row_unchanged(
    isolated_recent_sessions_db, dashboard_module
):
    """A session_start > now + 300s must be excluded from the read model.
    The underlying trading_sessions row must NOT be modified or deleted."""
    sqlite_mod, db_path = isolated_recent_sessions_db

    # Future fixture (mirrors session 71804 from the live DB).
    fixture_start = "2099-01-01T00:00:00+00:00"
    _seed_session(
        db_path,
        session_id=71804,
        session_start=fixture_start,
        status="ACTIVE",
        notes="recent",
    )

    # Two real recent sessions.
    real_old = _now_utc_iso(offset_seconds=-60)
    real_new = _now_utc_iso(offset_seconds=-30)
    _seed_session(db_path, session_id=9001, session_start=real_old)
    _seed_session(db_path, session_id=9002, session_start=real_new)
    _seed_decision(db_path, session_id=9002, symbol="AAPL")

    # Capture the fixture row's pre-call state to prove no mutation.
    pre_conn = sqlite3.connect(db_path)
    try:
        pre_row = pre_conn.execute(
            "SELECT id, session_start, status, notes FROM trading_sessions WHERE id=71804"
        ).fetchone()
    finally:
        pre_conn.close()
    assert pre_row is not None and pre_row[1] == fixture_start

    sessions = dashboard_module.get_recent_sessions_with_truthful_counts(limit=5)
    ids = [s["id"] for s in sessions]

    assert 71804 not in ids, (
        f"Future-dated fixture must be excluded; got ids={ids}"
    )
    assert ids == [9002, 9001], (
        f"Valid sessions must sort newest first; got {ids}"
    )

    # Verify the fixture row is unchanged in the DB after the read.
    post_conn = sqlite3.connect(db_path)
    try:
        post_row = post_conn.execute(
            "SELECT id, session_start, status, notes FROM trading_sessions WHERE id=71804"
        ).fetchone()
    finally:
        post_conn.close()
    assert post_row == pre_row, (
        "Filter is read-layer only: trading_sessions row must not be mutated. "
        f"pre={pre_row} post={post_row}"
    )

    # Also: the fixture row remains present and queryable directly.
    direct = sqlite_mod.sqlite_db.get_sessions(limit=200)
    assert any(s["id"] == 71804 for s in direct), (
        "The fixture row must still exist in trading_sessions for direct "
        "DB queries; the filter must not delete or hide it at the DB level."
    )


# ─────────────────────────────────────────────────────────────────────────
# AC-7: deterministic sort by session_start DESC, id DESC
# ─────────────────────────────────────────────────────────────────────────


def test_recent_sessions_sort_deterministically(
    isolated_recent_sessions_db, dashboard_module
):
    """Three sessions with distinct timestamps must sort session_start DESC,
    then id DESC as a deterministic tiebreak."""
    sqlite_mod, db_path = isolated_recent_sessions_db

    # Three timestamps, intentionally ordered so id-DESC tiebreak matters.
    t_now = datetime.now(timezone.utc)
    _seed_session(db_path, session_id=11, session_start=(t_now - timedelta(seconds=20)).isoformat())
    _seed_session(db_path, session_id=12, session_start=(t_now - timedelta(seconds=40)).isoformat())
    _seed_session(db_path, session_id=13, session_start=(t_now - timedelta(seconds=10)).isoformat())

    sessions = dashboard_module.get_recent_sessions_with_truthful_counts(limit=5)
    ids = [s["id"] for s in sessions]
    assert ids == [13, 11, 12], (
        "Expected session_start DESC ordering (13, 11, 12); got " + str(ids)
    )


def test_recent_sessions_sort_tiebreak_by_id_desc(
    isolated_recent_sessions_db, dashboard_module
):
    """When two sessions share the same session_start (rare but possible),
    ordering must be deterministic by id DESC."""
    sqlite_mod, db_path = isolated_recent_sessions_db
    same_start = _now_utc_iso(offset_seconds=-5)
    _seed_session(db_path, session_id=20, session_start=same_start)
    _seed_session(db_path, session_id=21, session_start=same_start)
    _seed_session(db_path, session_id=19, session_start=same_start)

    sessions = dashboard_module.get_recent_sessions_with_truthful_counts(limit=5)
    ids = [s["id"] for s in sessions]
    assert ids == [21, 20, 19], (
        "Same session_start must tiebreak by id DESC; got " + str(ids)
    )


# ─────────────────────────────────────────────────────────────────────────
# AC-8: zero-trade sessions render Trades = 0
# ─────────────────────────────────────────────────────────────────────────


def test_sessions_with_no_trades_render_zero(
    isolated_recent_sessions_db, dashboard_module
):
    """A session with no rows in the trades table must render Trades = 0."""
    sqlite_mod, db_path = isolated_recent_sessions_db
    _seed_session(db_path, session_id=5001, session_start=_now_utc_iso(offset_seconds=-15))
    _seed_session(db_path, session_id=5002, session_start=_now_utc_iso(offset_seconds=-10))
    _seed_session(db_path, session_id=5003, session_start=_now_utc_iso(offset_seconds=-5))

    sessions = dashboard_module.get_recent_sessions_with_truthful_counts(limit=5)
    assert {s["id"] for s in sessions} == {5001, 5002, 5003}
    for s in sessions:
        assert s["trades_count"] == 0


# ─────────────────────────────────────────────────────────────────────────
# AC-9: read-layer only — bot runtime files untouched
# ─────────────────────────────────────────────────────────────────────────


def test_read_model_does_not_mutate_decision_history_or_trades(
    isolated_recent_sessions_db, dashboard_module
):
    """Calling the read model must not write to decision_history or trades.
    Count rows before and after."""
    sqlite_mod, db_path = isolated_recent_sessions_db
    _seed_session(db_path, session_id=6001, session_start=_now_utc_iso(offset_seconds=-15))
    _seed_decision(db_path, session_id=6001, symbol="AAPL")
    _seed_decision(db_path, session_id=6001, symbol="MSFT")
    _seed_trade(db_path, session_id=6001, symbol="AAPL")

    conn = sqlite3.connect(db_path)
    try:
        dh_before = conn.execute("SELECT COUNT(*) FROM decision_history").fetchone()[0]
        tr_before = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    finally:
        conn.close()

    dashboard_module.get_recent_sessions_with_truthful_counts(limit=5)

    conn = sqlite3.connect(db_path)
    try:
        dh_after = conn.execute("SELECT COUNT(*) FROM decision_history").fetchone()[0]
        tr_after = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    finally:
        conn.close()
    assert dh_before == dh_after, (
        f"decision_history must not be mutated; before={dh_before} after={dh_after}"
    )
    assert tr_before == tr_after, (
        f"trades must not be mutated; before={tr_before} after={tr_after}"
    )


def test_no_bot_runtime_files_modified_by_helper():
    """This is a code-shape test, not a runtime test. It documents that the
    helper is defined in dashboard.py and uses no SmartTradingBot APIs.

    If anyone moves the helper into SmartTradingBot, this test fails fast.
    """
    import dashboard as dash_module
    import inspect
    src = inspect.getsource(dash_module.get_recent_sessions_with_truthful_counts)
    # Should not import or reference SmartTradingBot internals.
    assert "SmartTradingBot" not in src
    assert "smart_bot" not in src
    assert "start_session" not in src
    assert "end_session" not in src


# ─────────────────────────────────────────────────────────────────────────
# Boundary: a session_start exactly at the skew upper bound is INCLUDED
# ─────────────────────────────────────────────────────────────────────────


def test_session_at_skew_boundary_is_included(
    isolated_recent_sessions_db, dashboard_module
):
    """session_start <= now + 300s is INCLUDED; only strictly greater than
    the upper bound is excluded. This matches BOT-003's `<=` semantics."""
    sqlite_mod, db_path = isolated_recent_sessions_db
    boundary = _now_utc_iso(offset_seconds=300)
    _seed_session(db_path, session_id=7001, session_start=boundary)

    sessions = dashboard_module.get_recent_sessions_with_truthful_counts(limit=5)
    ids = [s["id"] for s in sessions]
    assert 7001 in ids, (
        f"session_start == now + 300s must be included; got ids={ids}"
    )
