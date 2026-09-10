"""BOT-001: Session counter correctness tests.

Proves that:
  - Session 1 counters do not leak into Session 2 (the regression that
    produced the June-28 "20K+ symbols / 0 trades" audit anomaly).
  - end_session() persists only that session's counts, even on a
    long-lived SmartTradingBot instance.
  - A zero-trade session persists exactly zero trades.
  - The fallback when start_session was never called still produces
    sane (per-session) values.
  - Lifetime / account-level metrics (peak_portfolio_value,
    daily_starting_value) are NOT reset.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.core.smart_bot import SmartTradingBot


def _bot():
    """Build a SmartTradingBot-shaped mock without invoking __init__.

    Bypassing __init__ avoids Alpaca client construction, settings
    service loading, and database schema-migration side effects. We
    only need the methods under test (start_session, end_session,
    get_session_counters).
    """
    bot = SmartTradingBot.__new__(SmartTradingBot)
    bot.db = MagicMock()
    bot.db.is_available.return_value = True
    bot.session_id = None
    bot.symbols_processed = 0
    bot.trades_executed = 0
    bot.errors_count = 0
    bot._session_start_symbols = 0
    bot._session_start_trades = 0
    bot._session_start_errors = 0
    # Attributes referenced by start_session() when constructing the
    # configuration JSON snapshot.
    bot.sma_fast = 10
    bot.sma_slow = 30
    bot.rsi_period = 14
    bot.trade_amount = 1000
    return bot


def test_session_one_counters_do_not_leak_into_session_two() -> None:
    """The regression test. Session 2's end_session must not see Session 1's
    accumulated counts.
    """
    bot = _bot()

    # ── Session 1: simulates a long bot run that processed 200 symbols
    # and hit 3 errors. (Note: this is artificially large for clarity.)
    bot.db.create_session.return_value = 1001
    bot.start_session()
    assert bot.session_id == 1001
    assert bot.symbols_processed == 0  # ← reset at start
    bot.symbols_processed = 200
    bot.trades_executed = 7
    bot.errors_count = 3
    bot.end_session()

    persisted = bot.db.update_session.call_args.kwargs  # (session_id, updates)
    # end_session() called update_session(session_id, {...})
    assert bot.db.update_session.call_count == 1
    args, kwargs = bot.db.update_session.call_args
    updates = args[1]
    assert updates["total_symbols_processed"] == 200, (
        f"Session 1 should persist 200 symbols, got {updates['total_symbols_processed']}"
    )
    assert updates["total_trades_executed"] == 7
    assert updates["error_count"] == 3
    assert updates["status"] == "ENDED"

    # Simulate the bot continuing to run between sessions (long-lived instance).
    # After session 1's end, the bot kept running and accumulated more.
    bot.symbols_processed += 50
    bot.trades_executed += 1
    bot.errors_count += 1

    # ── Session 2: must reset.
    bot.db.create_session.return_value = 1002
    bot.start_session()
    assert bot.session_id == 1002
    assert bot.symbols_processed == 0, (
        "Session 2 must reset symbols_processed to 0 (the BOT-001 fix)"
    )
    assert bot.trades_executed == 0
    assert bot.errors_count == 0
    # Baseline captured at start_session reflects the lifetime counts at that
    # instant (i.e. 250 symbols, 8 trades, 4 errors accumulated from session 1).
    assert bot._session_start_symbols == 250
    assert bot._session_start_trades == 8
    assert bot._session_start_errors == 4

    # Session 2 does some work (10 more symbols, 0 trades, 1 error). Note:
    # we set the lifetime counters to reflect "starting from 250, did 10
    # more", i.e. 260. The per-session delta is 260 - 250 = 10.
    bot.symbols_processed = 260
    bot.trades_executed = 8
    bot.errors_count = 5
    bot.end_session()

    # update_session was called twice (once per end_session). Look at the LAST call.
    assert bot.db.update_session.call_count == 2
    args2, _ = bot.db.update_session.call_args
    updates2 = args2[1]
    assert updates2["total_symbols_processed"] == 10, (
        f"Session 2 must persist 10 symbols (the per-session delta), "
        f"got {updates2['total_symbols_processed']}"
    )
    assert updates2["total_trades_executed"] == 0
    assert updates2["error_count"] == 1


def test_end_session_persists_only_that_sessions_counts() -> None:
    """end_session() must compute a delta from the start-session snapshot,
    not blindly use the current cumulative counter."""
    bot = _bot()
    bot.db.create_session.return_value = 42
    bot.start_session()

    # Lifetime counters (long-lived instance) before this session ran.
    # Inject lifetime numbers into the bot instance to simulate that
    # start_session captures them as baselines.
    bot._session_start_symbols = 1234  # imagine prior loops accumulated
    bot._session_start_trades = 56
    bot._session_start_errors = 78
    bot.symbols_processed = 1234 + 7
    bot.trades_executed = 56 + 2
    bot.errors_count = 78 + 1

    bot.end_session()

    args, _ = bot.db.update_session.call_args
    updates = args[1]
    # Only this session's delta persists.
    assert updates["total_symbols_processed"] == 7
    assert updates["total_trades_executed"] == 2
    assert updates["error_count"] == 1


def test_zero_trade_session_persists_zero_trades() -> None:
    """If the session never traded, end_session must persist exactly 0,
    not leak the lifetime trade count."""
    bot = _bot()
    bot._session_start_trades = 999  # huge lifetime
    bot.trades_executed = 999  # unchanged
    bot.start_session()
    assert bot.trades_executed == 0  # reset
    bot.end_session()

    args, _ = bot.db.update_session.call_args
    updates = args[1]
    assert updates["total_trades_executed"] == 0


def test_fallback_when_start_session_never_called_uses_cumulative() -> None:
    """If end_session is called without a prior start_session (an
    unexpected code path), the persisted value falls back to the
    current cumulative count. This is the SAFE behavior — over-reporting
    rather than silently dropping data.
    """
    bot = _bot()
    # Simulate a long-lived bot with counters set but start_session
    # never called (baselines default to 0).
    bot.symbols_processed = 50
    bot.trades_executed = 3
    bot.errors_count = 1
    bot.session_id = 999  # pretend some prior code set this
    bot.end_session()

    args, _ = bot.db.update_session.call_args
    updates = args[1]
    assert updates["total_symbols_processed"] == 50
    assert updates["total_trades_executed"] == 3
    assert updates["error_count"] == 1


def test_get_session_counters_exposes_baselines_and_deltas() -> None:
    """The observability helper returns the current counters and the
    captured baselines. Tests and ops dashboards rely on this.
    """
    bot = _bot()
    bot.start_session()
    bot.symbols_processed = 17
    bot.trades_executed = 2
    bot.errors_count = 1
    snap = bot.get_session_counters()
    assert snap["symbols_processed"] == 17
    assert snap["trades_executed"] == 2
    assert snap["errors_count"] == 1
    # Baselines captured at start_session.
    assert snap["session_start_symbols"] == 0
    assert snap["session_start_trades"] == 0
    assert snap["session_start_errors"] == 0
    assert snap["session_id"] == bot.session_id


def test_lifetime_metrics_are_not_reset() -> None:
    """Account-level metrics (peak portfolio value, daily starting value)
    must NOT be reset by start_session. This protects the drawdown
    protection and daily-loss-limit features.
    """
    bot = _bot()
    # Lifetime / account metrics (from __init__ defaults).
    bot.peak_portfolio_value = 12345.67
    bot.daily_starting_value = 11000.00
    bot.daily_loss_pct = 1.5

    bot.start_session()

    assert bot.peak_portfolio_value == 12345.67, (
        "peak_portfolio_value is a lifetime metric; start_session must NOT reset"
    )
    assert bot.daily_starting_value == 11000.00
    assert bot.daily_loss_pct == 1.5


def test_start_session_does_not_persist_when_db_unavailable() -> None:
    """If the DB is unavailable, start_session must still reset counters
    so the in-process counters are sane, but must not crash."""
    bot = _bot()
    bot.db.is_available.return_value = False
    bot.symbols_processed = 999
    bot.trades_executed = 99
    bot.errors_count = 9

    bot.start_session()  # should NOT raise

    assert bot.symbols_processed == 0
    assert bot.trades_executed == 0
    assert bot.errors_count == 0
    assert bot.session_id is None  # nothing persisted
    assert bot.db.create_session.call_count == 0


def test_end_session_does_not_persist_when_db_unavailable() -> None:
    bot = _bot()
    bot.db.is_available.return_value = False
    bot.session_id = None  # no active session
    bot.start_session()
    bot.symbols_processed = 5
    bot.end_session()
    # update_session must NOT be called when session_id is None.
    assert bot.db.update_session.call_count == 0


def test_end_session_status_is_ended() -> None:
    bot = _bot()
    bot.start_session()
    bot.end_session()
    args, _ = bot.db.update_session.call_args
    updates = args[1]
    assert updates["status"] == "ENDED"
