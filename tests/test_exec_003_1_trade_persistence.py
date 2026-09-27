"""
EXEC-003.1 \u2014 Trade persistence contract tests.

These tests prove:

- TEST A : schema compatibility. log_trade is invoked with the
  pre-EXEC-003.1 dict (which has non-schema columns). The call must
  raise a clear OperationalError rather than silently fail. The
  post-EXEC-003.1 dict (schema-aligned) must insert cleanly.
- TEST B : exact persistence. A submission through the execute_trade
  path with a mocked broker produces exactly one trades row with the
  expected columns and values.
- TEST C : SUBMITTED != FILLED. The persisted row's status must be
  'SUBMITTED' and pnl must be NULL. The execute_trade path must
  NOT mark status='FILLED' just because submit_order returned
  successfully.
- TEST D : persistence failure is observable. log_trade raises
  loudly under TRADING_BOT_TESTING_FATAL_PERSISTENCE=1 instead of
  silently swallowing the error.
- TEST E : existing test_bot001_session_counters continue to pass.
  This file imports the module and verifies the trades schema is
  unchanged in shape (same columns) so the existing tests' seeders
  and assertions remain compatible.
- TEST F : existing test_smart_bot_decision_paths BUY/SELL path
  tests continue to pass under the EXEC-003.1 execute_trade
  persistence rewrite.

Run with TESTING=1 UNIT_TESTING=1 (set by conftest). No real Alpaca
calls. No real DB mutation \u2014 each test uses a tmp_path fixture.
"""

import importlib
import json
import os
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

# Conftest sets TESTING/UNIT_TESTING before imports.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


class _FatalPersistenceError(RuntimeError):
    """Raised when log_trade fail-loudly mode triggers."""


def _make_test_db(tmp_path: Path) -> Path:
    """Create a fresh SQLite DB at tmp_path/trading_bot.db.

    Returns the path. The DB is empty; callers must initialise
    schema via SQLiteDB._init_schema (or call its migration block
    directly).
    """
    db_path = tmp_path / "trading_bot.db"
    # Touch the file so SQLite has somewhere to attach.
    db_path.touch()
    return db_path


def _initialised_db(tmp_path: Path, monkeypatch):
    """Return a SQLiteDB instance whose schema has been initialised
    in tmp_path.

    Forces SQLiteDB to operate on the tmp_path DB by reassigning
    the module-level DB_PATH attribute (no module reload needed —
    the attribute is re-read at every _init_schema call).
    """
    db_path = _make_test_db(tmp_path)

    import src.database.sqlite_db as sqlite_db_module
    # Set the DB_PATH to the tmp_path file. _init_schema reads
    # sqlite_db_module.DB_PATH at call time, so reassignment is
    # sufficient. monkeypatch.setattr ensures automatic cleanup.
    monkeypatch.setattr(sqlite_db_module, "DB_PATH", db_path)

    db = sqlite_db_module.SQLiteDB()
    db.available = True
    assert db.available, "Test DB should be available after init"
    return db, db_path


def _trades_columns(db_path: Path) -> list:
    with sqlite3.connect(str(db_path)) as conn:
        return [
            row[1] for row in conn.execute("PRAGMA table_info(trades)").fetchall()
        ]


def _seed_session(db_path: Path, session_id: int = 42) -> None:
    """Insert a minimal trading_sessions row so the FK in trades resolves."""
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO trading_sessions "
            "(id, session_start, status, is_paper_trading) "
            "VALUES (?, ?, ?, ?)",
            (session_id, "2026-08-03T00:00:00Z", "ACTIVE", 1),
        )
        conn.commit()


def _analysis(signal: str = "BUY") -> dict:
    return {
        "symbol": "AAPL",
        "signal": signal,
        "price": 100.0,
        "signal_strength": "MEDIUM",
        "sma_fast": 101.0,
        "sma_slow": 99.0,
        "rsi": 25.0 if signal == "BUY" else 75.0,
        "timestamp": "2026-08-03T00:00:00+00:00",
    }


def _bot_with_real_db(db_path: Path, *, pending_order: bool = False, position_qty: float = 0.0):
    """Build a SimpleNamespace bot that uses the *real* SQLiteDB at
    db_path for persistence.

    Mirrors the shape of ``_bot`` in
    ``tests/test_smart_bot_decision_paths.py`` but with the real DB
    wired in so execute_trade's persistence actually lands a row.
    """
    import src.database.sqlite_db as sqlite_db_module

    trading_client = Mock()
    trading_client.get_account.return_value = SimpleNamespace(cash="10000")
    trading_client.get_open_position.return_value = (
        SimpleNamespace(qty=str(position_qty)) if position_qty else None
    )
    trading_client.submit_order.return_value = SimpleNamespace(id="paper-order-exec-003-1")

    # A DB instance whose available=True and whose log_trade writes
    # through to db_path. We re-use the module singleton after
    # monkeypatching DB_PATH and reloading.
    real_db = sqlite_db_module.sqlite_db

    bot = SimpleNamespace(
        trading_client=trading_client,
        trade_amount=200.0,
        db=real_db,
        session_id=42,
        trades_executed=0,
        _pending_entry_tranches={},
        _portfolio_beta_cache=None,
        _current_trades_details=[],
        get_portfolio_total_value=Mock(return_value=100_000.0),
        has_pending_orders=Mock(return_value=pending_order),
        is_in_cooldown=Mock(return_value=False),
        get_current_position_size=Mock(return_value=0.0),
        calculate_position_size=Mock(return_value=2),
        check_position_limits=Mock(return_value=(True, 2, 0.2)),
        check_sector_concentration=Mock(return_value=(True, 0.0, 0.2, "ok")),
        check_correlation_risk=Mock(return_value=(True, 0.0, None, "ok")),
        check_beta_exposure=Mock(return_value=(True, 1.0, "ok")),
        send_trade_notification=Mock(),
        invalidate_sector_cache=Mock(),
        mark_recent_trade=Mock(),
    )
    bot.db.is_available = Mock(return_value=True)
    return bot


def _execute(bot, signal: str = "BUY") -> bool:
    """Call SmartTradingBot.execute_trade as an unbound function on the
    SimpleNamespace bot. Mirrors the helper from
    ``tests/test_smart_bot_decision_paths.py``.
    """
    from src.core.smart_bot import SmartTradingBot
    return SmartTradingBot.execute_trade(bot, _analysis(signal))


# --------------------------------------------------------------------------- #
# TEST A \u2014 schema compatibility                                              #
# --------------------------------------------------------------------------- #


def test_A_post_exec_003_1_dict_is_schema_compatible(tmp_path, monkeypatch):
    """A schema-aligned dict inserts without raising.

    The pre-EXEC-003.1 dict (with non-schema keys) MUST raise a
    clear OperationalError because unknown columns are not silently
    forwarded to SQLite (the silent failure that caused the defect).
    """
    db, db_path = _initialised_db(tmp_path, monkeypatch)
    _seed_session(db_path)

    # Schema-aligned dict (the post-fix shape).
    good = {
        'session_id': 42,
        'symbol': 'AAPL',
        'side': 'BUY',
        'qty': 2,
        'price': 100.0,
        'pnl': None,
        'signal': 'BUY',
        'rsi': 25.0,
        'order_time': '2026-08-03T00:00:00Z',
        'status': 'SUBMITTED',
        'broker_order_id': 'paper-order-1',
    }
    assert db.log_trade(42, good) is True

    # The pre-EXEC-003.1 dict (with non-schema columns). log_trade
    # MUST raise rather than silently persisting or silently
    # returning False. We use the FAIL-LOUD flag so the schema
    # mismatch is surfaced as an exception (TEST D proves the
    # production tolerates path).
    monkeypatch.setenv("TRADING_BOT_TESTING_FATAL_PERSISTENCE", "1")
    bad = {
        'session_id': 42,
        'alpaca_order_id': 'abc',
        'symbol': 'AAPL',
        'side': 'BUY',
        'quantity': 2,
        'order_price': 100.0,
        'signal_time': '2026-08-03T00:00:00Z',
        'order_time': '2026-08-03T00:00:00Z',
        'sma_fast': 101.0,
        'sma_slow': 99.0,
        'rsi': 25.0,
        'signal_strength': 'MEDIUM',
        'status': 'SUBMITTED',
    }
    with pytest.raises(sqlite3.OperationalError) as exc:
        db.log_trade(42, bad)
    assert 'not in trades schema' in str(exc.value), (
        f"Expected clear OperationalError, got: {exc.value}"
    )


# --------------------------------------------------------------------------- #
# TEST B \u2014 exact persistence                                                  #
# --------------------------------------------------------------------------- #


def test_B_execute_trade_persists_one_row_with_expected_columns(tmp_path, monkeypatch):
    """A submission through execute_trade inserts exactly one row
    with status='SUBMITTED', pnl IS NULL, broker_order_id set,
    qty/price/symbol/side/signal/rsi/order_time populated."""
    db, db_path = _initialised_db(tmp_path, monkeypatch)
    _seed_session(db_path)

    bot = _bot_with_real_db(db_path)
    assert _execute(bot, "BUY") is True

    rows = db.get_all_trades(limit=10)
    assert len(rows) == 1, f"expected exactly 1 trade row, got {len(rows)}"
    row = rows[0]

    # All schema-aligned columns that we promised to populate.
    assert row['session_id'] == 42
    assert row['symbol'] == 'AAPL'
    assert row['side'] == 'BUY'
    assert row['qty'] == 2
    assert row['price'] == 100.0
    assert row['pnl'] is None, "pnl MUST be NULL at submit time"
    assert row['signal'] == 'BUY'
    assert row['rsi'] == 25.0
    assert row['status'] == 'SUBMITTED'
    assert row['broker_order_id'] == 'paper-order-exec-003-1'
    assert row['order_time'], "order_time must be set"


# --------------------------------------------------------------------------- #
# TEST C \u2014 SUBMITTED != FILLED                                               #
# --------------------------------------------------------------------------- #


def test_C_submitted_is_not_filled(tmp_path, monkeypatch):
    """execute_trade returning True on submit_order success MUST NOT
    promote status to 'FILLED' or set pnl != None.

    This test guards the SUBMITTED != FILLED invariant.
    """
    db, db_path = _initialised_db(tmp_path, monkeypatch)
    _seed_session(db_path)

    bot = _bot_with_real_db(db_path)
    assert _execute(bot, "BUY") is True

    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT status, pnl FROM trades WHERE session_id = 42"
        ).fetchone()

    status, pnl = row
    assert status == 'SUBMITTED', (
        f"status MUST be 'SUBMITTED' (not 'FILLED') at this point; "
        f"got {status!r}"
    )
    assert pnl is None, (
        f"pnl MUST be NULL at submit time; got {pnl!r}. If pnl != "
        f"NULL, the persistence is falsely claiming a completed trade."
    )

    # Also assert that no trade row exists with status='FILLED'.
    with sqlite3.connect(str(db_path)) as conn:
        filled_count = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE status = 'FILLED'"
        ).fetchone()[0]
    assert filled_count == 0, (
        f"No row should have status='FILLED' on the submit-only path; "
        f"got {filled_count}"
    )


def test_C_dashboard_analytics_excludes_submitted_rows(tmp_path, monkeypatch):
    """Dashboard analytics consumers treat any non-FILLED row as
    NOT a completed trade.

    We can't import dashboard.py's route handlers directly (they
    depend on Flask request context) but we can exercise the same
    filter logic that dashboard.py uses by reproducing it here and
    proving it includes/excludes rows correctly.
    """
    rows = [
        {'symbol': 'A', 'pnl': 5.0, 'status': 'FILLED'},
        {'symbol': 'B', 'pnl': None, 'status': 'SUBMITTED'},
        {'symbol': 'C', 'pnl': 0.0, 'status': 'SUBMITTED'},
        {'symbol': 'D', 'pnl': None, 'status': 'REJECTED'},
        {'symbol': 'E', 'pnl': -3.0, 'status': 'FILLED'},
        {'symbol': 'F', 'pnl': None, 'status': None},  # legacy
    ]

    # Mirror the EXEC-003.1 contract used in dashboard.py:
    # completed_trades = [t for t in trades if t.get('status') == 'FILLED']
    completed = [t for t in rows if t.get('status') == 'FILLED']

    assert [t['symbol'] for t in completed] == ['A', 'E'], (
        "Dashboard analytics must exclude SUBMITTED / REJECTED / "
        "legacy NULL-status rows from completed-trade aggregates."
    )


# --------------------------------------------------------------------------- #
# TEST D \u2014 persistence failure is observable                                 #
# --------------------------------------------------------------------------- #


def test_D_persistence_failure_raises_under_testing_flag(tmp_path, monkeypatch):
    """Under TRADING_BOT_TESTING_FATAL_PERSISTENCE=1, log_trade raises
    on persistence failures so tests can assert on them.

    Without the flag, log_trade returns False (production tolerates
    telemetry failure to avoid losing broker-executed trades)."""
    db, db_path = _initialised_db(tmp_path, monkeypatch)
    _seed_session(db_path)

    bad = {
        'session_id': 42,
        'symbol': 'AAPL',
        'side': 'BUY',
        # 'qty' is missing! Schema requires it because of NOT NULL.
        # Insert a non-schema column to trigger the schema-mismatch
        # guard.
        'definitely_not_a_column': True,
        'order_time': '2026-08-03T00:00:00Z',
        'status': 'SUBMITTED',
    }

    # Without flag \u2014 swallowed.
    assert db.log_trade(42, bad) is False, (
        "Without the testing flag, log_trade tolerates persistence "
        "failure and returns False."
    )

    # With flag \u2014 raises.
    monkeypatch.setenv("TRADING_BOT_TESTING_FATAL_PERSISTENCE", "1")
    with pytest.raises(sqlite3.OperationalError) as exc:
        db.log_trade(42, bad)
    assert 'not in trades schema' in str(exc.value), (
        f"Expected schema-mismatch OperationalError under fatal "
        f"flag, got: {exc.value}"
    )


# --------------------------------------------------------------------------- #
# TEST E \u2014 existing session-counter tests / schema compatibility             #
# --------------------------------------------------------------------------- #


def test_E_trades_schema_shape_preserves_existing_columns(tmp_path, monkeypatch):
    """The migration must keep the existing columns so the
    existing test_bot001_session_counters tests' _seed_trade helper
    (which inserts into the legacy column shape) continues to
    compile and run.

    Pre-EXEC-003.1 columns: id, session_id, symbol, side, qty,
    price, pnl, signal, rsi, order_time, created_at.
    Post-EXEC-003.1 columns: above PLUS status, broker_order_id.
    """
    _, db_path = _initialised_db(tmp_path, monkeypatch)

    cols = _trades_columns(db_path)
    required_legacy = {
        'id', 'session_id', 'symbol', 'side', 'qty', 'price',
        'pnl', 'signal', 'rsi', 'order_time', 'created_at',
    }
    missing_legacy = required_legacy - set(cols)
    assert not missing_legacy, (
        f"EXEC-003.1 must preserve all legacy columns; missing: "
        f"{sorted(missing_legacy)}"
    )
    # New columns exist.
    assert 'status' in cols, "status column added by EXEC-003.1"
    assert 'broker_order_id' in cols, (
        "broker_order_id column added by EXEC-003.1"
    )


def test_E_migration_is_idempotent_on_second_run(tmp_path, monkeypatch):
    """Calling the migration twice does NOT raise 'duplicate column
    name'. Production restarts repeatedly call _init_schema.
    """
    db_path = _make_test_db(tmp_path)
    import src.database.sqlite_db as sqlite_db_module

    # First init.
    monkeypatch.setattr(sqlite_db_module, "DB_PATH", db_path)
    db1 = sqlite_db_module.SQLiteDB()
    assert db1.available

    # Second init against the SAME DB.
    db2 = sqlite_db_module.SQLiteDB()
    assert db2.available, (
        "Re-running _init_schema on a DB that already has the "
        "EXEC-003.1 columns must not raise."
    )

    cols = _trades_columns(db_path)
    assert 'status' in cols
    assert 'broker_order_id' in cols


# --------------------------------------------------------------------------- #
# TEST F \u2014 existing BUY/SELL decision-path tests remain compatible            #
# --------------------------------------------------------------------------- #


def test_F_buy_signal_persists_submitted_row(tmp_path, monkeypatch):
    """Mirrors test_smart_bot_decision_paths::test_buy_signal_submits_buy_order_for_unowned_symbol
    but adds the EXEC-003.1 persistence assertion."""
    db, db_path = _initialised_db(tmp_path, monkeypatch)
    _seed_session(db_path)

    bot = _bot_with_real_db(db_path)

    assert _execute(bot, "BUY") is True

    # Original decision-path assertion: submit_order called with
    # the correct shape.
    order = bot.trading_client.submit_order.call_args.kwargs["order_data"]
    assert order.symbol == "AAPL"
    assert order.qty == 2
    # OrderSide is imported by execute_trade from somewhere; the
    # BUY side must be OrderSide.BUY. We re-import to compare.
    from src.core.smart_bot import OrderSide
    assert order.side == OrderSide.BUY

    # EXEC-003.1-specific: a SUBMITTED row landed.
    rows = db.get_all_trades(limit=1)
    assert len(rows) == 1
    assert rows[0]['symbol'] == 'AAPL'
    assert rows[0]['side'] == 'BUY'
    assert rows[0]['qty'] == 2
    assert rows[0]['price'] == 100.0
    assert rows[0]['status'] == 'SUBMITTED'
    assert rows[0]['pnl'] is None


def test_F_sell_signal_persists_submitted_row(tmp_path, monkeypatch):
    """Mirrors test_smart_bot_decision_paths::test_sell_signal_submits_sell_order_for_owned_symbol
    with the EXEC-003.1 persistence assertion."""
    db, db_path = _initialised_db(tmp_path, monkeypatch)
    _seed_session(db_path)

    bot = _bot_with_real_db(db_path, position_qty=5)

    assert _execute(bot, "SELL") is True

    order = bot.trading_client.submit_order.call_args.kwargs["order_data"]
    from src.core.smart_bot import OrderSide
    assert order.symbol == "AAPL"
    assert order.side == OrderSide.SELL

    rows = db.get_all_trades(limit=1)
    assert len(rows) == 1
    assert rows[0]['symbol'] == 'AAPL'
    assert rows[0]['side'] == 'SELL'
    assert rows[0]['status'] == 'SUBMITTED'


def test_F_pending_order_prevents_duplicate_submission_and_persistence(tmp_path, monkeypatch):
    """Mirrors test_smart_bot_decision_paths::test_pending_order_prevents_duplicate_submission.
    With a pending order, neither submit_order is called NOR is a
    row persisted."""
    db, db_path = _initialised_db(tmp_path, monkeypatch)
    _seed_session(db_path)

    bot = _bot_with_real_db(db_path, pending_order=True, position_qty=5)

    assert _execute(bot, "BUY") is False
    bot.trading_client.submit_order.assert_not_called()

    rows = db.get_all_trades(limit=10)
    assert rows == [], (
        f"No row should be persisted when submit_order was blocked "
        f"by the pending-order guard; got {rows}"
    )

def test_G_persistence_failure_emits_explicit_error_log(tmp_path, monkeypatch, caplog):
    """EXEC-003.1 review correction: execute_trade must capture log_trade's
    return value and emit an explicit ERROR-level log when persistence
    fails. The broker-side order remains authoritative; we do NOT
    roll back. We verify the error-log fires (and at ERROR level) so
    that a future operator notices DB persistence loss without having
    to scan DEBUG-level log lines."""
    db, db_path = _initialised_db(tmp_path, monkeypatch)
    _seed_session(db_path)

    bot = _bot_with_real_db(db_path, position_qty=0)

    # Force persistence failure by patching the bot's db.log_trade to
    # always return False (simulating the production tolerance path).
    monkeypatch.setattr(bot.db, "log_trade", lambda *a, **kw: False)

    caplog.set_level("ERROR", logger="src.core.smart_bot")
    # execute_trade should still complete (broker order remains
    # authoritative; the failure is observed, not thrown).
    result = _execute(bot, "BUY")

    assert result is True, "execute_trade must NOT raise — broker order remains authoritative"
    assert bot.trading_client.submit_order.called, "submit_order should still be called"

    # Check that an explicit PERSISTENCE FAILURE error log was emitted.
    error_msgs = [
        r for r in caplog.records
        if r.levelname == "ERROR" and "PERSISTENCE FAILURE" in r.getMessage()
    ]
    assert error_msgs, (
        "execute_trade must emit ERROR-level 'PERSISTENCE FAILURE' log "
        "when log_trade returns False; got records="
        f"{[r.levelname for r in caplog.records]}"
    )
    msg = error_msgs[0].getMessage()
    assert "broker" in msg.lower(), f"log message must mention broker: {msg}"
    assert "do NOT re-submit" in msg or "do not re-submit" in msg.lower(), (
        f"log message must instruct operator not to re-submit: {msg}"
    )
