"""SLEEVE-P1 — sleeve tagging tests (two-sleeve design §6 / §10-P1).

Proves:

- Migration: `trades.sleeve` column added idempotently, no data loss,
  legacy rows left NULL (never back-filled).
- Tagging: every NEW ledger write path persists sleeve='S'
  (execute_trade, tranche submission, stop-loss via
  _ledger_log_submitted, portfolio-management via _ledger_log_submitted).
- Validation: log_trade fails closed on invalid sleeve values.
- order_events: inherits sleeve via the parent trade row join.
- SleevePositionBook: derived-only per-sleeve map from ledger fills,
  fail-closed read errors, broker-authoritative contract.

Run with TESTING=1 UNIT_TESTING=1 (set by conftest). Mock clients only.
"""

import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# --------------------------------------------------------------------------- #
# Helpers (mirror tests/test_exec_003_1_trade_persistence.py conventions)     #
# --------------------------------------------------------------------------- #


def _make_test_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "trading_bot.db"
    db_path.touch()
    return db_path


def _initialised_db(tmp_path: Path, monkeypatch):
    import src.database.sqlite_db as sqlite_db_module

    db_path = _make_test_db(tmp_path)
    monkeypatch.setattr(sqlite_db_module, "DB_PATH", db_path)
    db = sqlite_db_module.SQLiteDB()
    db.available = True
    return db, db_path


def _trades_columns(db_path: Path) -> list:
    with sqlite3.connect(str(db_path)) as conn:
        return [
            row[1] for row in conn.execute("PRAGMA table_info(trades)").fetchall()
        ]


def _seed_session(db_path: Path, session_id: int = 42) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO trading_sessions "
            "(id, session_start, status, is_paper_trading) "
            "VALUES (?, ?, ?, ?)",
            (session_id, "2026-10-29T00:00:00Z", "ACTIVE", 1),
        )
        conn.commit()


def _insert_legacy_row(db_path: Path, symbol: str = "LEGACY") -> None:
    """Insert a row the pre-sleeve way (no sleeve key) directly."""
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO trades (session_id, symbol, side, qty, price, "
            "pnl, signal, status) VALUES (42, ?, 'BUY', 10, 100.0, "
            "NULL, 'BUY', 'FILLED')",
            (symbol,),
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
        "timestamp": "2026-10-29T00:00:00+00:00",
    }


def _bot_with_real_db():
    import src.database.sqlite_db as sqlite_db_module

    trading_client = Mock()
    trading_client.get_account.return_value = SimpleNamespace(cash="10000")
    trading_client.get_open_position.return_value = None
    trading_client.submit_order.return_value = SimpleNamespace(
        id="paper-order-sleeve-p1"
    )
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
        has_pending_orders=Mock(return_value=False),
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
    from src.core.smart_bot import SmartTradingBot

    return SmartTradingBot.execute_trade(bot, _analysis(signal))


def _all_rows(db_path: Path):
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        return [
            dict(r)
            for r in conn.execute("SELECT * FROM trades ORDER BY id").fetchall()
        ]


# --------------------------------------------------------------------------- #
# Migration                                                                   #
# --------------------------------------------------------------------------- #


def test_migration_adds_sleeve_column(tmp_path, monkeypatch):
    db, db_path = _initialised_db(tmp_path, monkeypatch)
    cols = _trades_columns(db_path)
    assert "sleeve" in cols


def test_migration_is_idempotent(tmp_path, monkeypatch):
    _db, db_path = _initialised_db(tmp_path, monkeypatch)
    import src.database.sqlite_db as sqlite_db_module

    # Re-run _init_schema (simulates bot restart on migrated DB).
    sqlite_db_module.SQLiteDB()._init_schema()
    cols = _trades_columns(db_path)
    assert cols.count("sleeve") == 1


def test_migration_no_data_loss_and_legacy_rows_stay_null(
    tmp_path, monkeypatch
):
    db, db_path = _initialised_db(tmp_path, monkeypatch)
    _seed_session(db_path)
    _insert_legacy_row(db_path, "OLDA")
    assert db.log_trade(
        42,
        {
            "session_id": 42,
            "symbol": "NEWA",
            "side": "BUY",
            "qty": 5,
            "price": 10.0,
            "pnl": None,
            "signal": "BUY",
            "rsi": 25.0,
            "status": "SUBMITTED",
            "sleeve": "S",
        },
    )
    # Re-run migration; existing rows survive with sleeve unchanged.
    import src.database.sqlite_db as sqlite_db_module

    sqlite_db_module.SQLiteDB()._init_schema()
    rows = {r["symbol"]: r for r in _all_rows(db_path)}
    assert len(rows) == 2
    assert rows["OLDA"]["sleeve"] is None  # legacy never back-filled (D7)
    assert rows["NEWA"]["sleeve"] == "S"
    assert rows["OLDA"]["qty"] == 10  # no data loss


def test_log_trade_without_sleeve_key_persists_null(tmp_path, monkeypatch):
    db, db_path = _initialised_db(tmp_path, monkeypatch)
    _seed_session(db_path)
    ok = db.log_trade(
        42,
        {
            "session_id": 42,
            "symbol": "NOKEY",
            "side": "BUY",
            "qty": 1,
            "price": 2.0,
            "pnl": None,
            "signal": "BUY",
            "status": "SUBMITTED",
        },
    )
    assert ok
    (row,) = _all_rows(db_path)
    assert row["sleeve"] is None


# --------------------------------------------------------------------------- #
# Validation — fail closed on invalid sleeve                                  #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("bad", ["X", "s", "l", "LONG", 3, ""])
def test_log_trade_rejects_invalid_sleeve(tmp_path, monkeypatch, bad):
    db, db_path = _initialised_db(tmp_path, monkeypatch)
    _seed_session(db_path)
    ok = db.log_trade(
        42,
        {
            "session_id": 42,
            "symbol": "BAD",
            "side": "BUY",
            "qty": 1,
            "price": 2.0,
            "pnl": None,
            "signal": "BUY",
            "status": "SUBMITTED",
            "sleeve": bad,
        },
    )
    assert ok is False
    assert _all_rows(db_path) == []  # fail-closed: nothing persisted


def test_log_trade_fatal_mode_raises_on_invalid_sleeve(
    tmp_path, monkeypatch
):
    db, db_path = _initialised_db(tmp_path, monkeypatch)
    _seed_session(db_path)
    monkeypatch.setenv("TRADING_BOT_TESTING_FATAL_PERSISTENCE", "1")
    with pytest.raises(sqlite3.OperationalError):
        db.log_trade(
            42,
            {
                "session_id": 42,
                "symbol": "BAD",
                "side": "BUY",
                "qty": 1,
                "price": 2.0,
                "pnl": None,
                "signal": "BUY",
                "status": "SUBMITTED",
                "sleeve": "X",
            },
        )


def test_log_trade_accepts_L_and_S(tmp_path, monkeypatch):
    db, db_path = _initialised_db(tmp_path, monkeypatch)
    _seed_session(db_path)
    for sym, sleeve in (("AAA", "L"), ("BBB", "S")):
        assert db.log_trade(
            42,
            {
                "session_id": 42,
                "symbol": sym,
                "side": "BUY",
                "qty": 1,
                "price": 2.0,
                "pnl": None,
                "signal": "BUY",
                "status": "SUBMITTED",
                "sleeve": sleeve,
            },
        )
    rows = {r["symbol"]: r for r in _all_rows(db_path)}
    assert rows["AAA"]["sleeve"] == "L"
    assert rows["BBB"]["sleeve"] == "S"


# --------------------------------------------------------------------------- #
# Tagging on every NEW write path                                             #
# --------------------------------------------------------------------------- #


def test_execute_trade_tags_sleeve_S(tmp_path, monkeypatch):
    db, db_path = _initialised_db(tmp_path, monkeypatch)
    _seed_session(db_path)
    bot = _bot_with_real_db()
    assert _execute(bot, "BUY")
    rows = _all_rows(db_path)
    assert len(rows) == 1
    assert rows[0]["sleeve"] == "S"
    assert rows[0]["status"] == "SUBMITTED"


def test_tranche_submission_tags_sleeve_S(tmp_path, monkeypatch):
    from src.core.smart_bot import _persist_tranche_submission

    db, db_path = _initialised_db(tmp_path, monkeypatch)
    _seed_session(db_path)
    ok = _persist_tranche_submission(
        db=db,
        session_id=42,
        symbol="AAPL",
        qty=3,
        price=100.0,
        rsi=25.0,
        tranche={"tranche_num": 2, "trigger": "test"},
        broker_order_id="paper-tranche-2",
    )
    assert ok
    (row,) = _all_rows(db_path)
    assert row["sleeve"] == "S"
    assert row["broker_order_id"] == "paper-tranche-2"


def test_ledger_log_submitted_tags_sleeve_S_central(
    tmp_path, monkeypatch
):
    """Stop-loss and portfolio-management rows go through
    _ledger_log_submitted; the central tag must apply even when the
    caller's dict omits sleeve entirely."""
    from src.core.smart_bot import _ledger_log_submitted

    db, db_path = _initialised_db(tmp_path, monkeypatch)
    _seed_session(db_path)
    base = {
        "session_id": 42,
        "symbol": "AAPL",
        "side": "SELL",
        "qty": 5,
        "price": 95.0,
        "pnl": None,
        "signal": "SELL",
        "rsi": None,
        "status": "SUBMITTED",
        "broker_order_id": "paper-sl-1",
        "notes": "STOP-LOSS stop=$95.00",
    }
    # Caller dict WITHOUT a sleeve key (stop-loss path shape).
    ok = _ledger_log_submitted(db, dict(base), context="STOP-LOSS")
    assert ok
    # Caller dict WITH an explicit different sleeve is preserved.
    ok2 = _ledger_log_submitted(
        db,
        dict(base, symbol="MSFT", broker_order_id="paper-pm-1",
             sleeve="L"),
        context="PORTFOLIO-MANAGEMENT",
    )
    assert ok2
    rows = {r["symbol"]: r for r in _all_rows(db_path)}
    assert rows["AAPL"]["sleeve"] == "S"
    assert rows["MSFT"]["sleeve"] == "L"


# --------------------------------------------------------------------------- #
# order_events inherit sleeve via the parent trades row                       #
# --------------------------------------------------------------------------- #


def test_order_event_join_inherits_sleeve(tmp_path, monkeypatch):
    db, db_path = _initialised_db(tmp_path, monkeypatch)
    _seed_session(db_path)
    assert db.log_trade(
        42,
        {
            "session_id": 42,
            "symbol": "AAPL",
            "side": "BUY",
            "qty": 5,
            "price": 100.0,
            "pnl": None,
            "signal": "BUY",
            "status": "SUBMITTED",
            "broker_order_id": "paper-join-1",
            "sleeve": "S",
        },
    )
    trade_id = _all_rows(db_path)[0]["id"]
    assert db.update_trade_lifecycle(
        trade_id=trade_id,
        status="FILLED",
        filled_qty=5.0,
        avg_fill_price=100.5,
        broker_order_id="paper-join-1",
    )
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        joined = conn.execute(
            "SELECT oe.canonical_status, t.sleeve, t.symbol "
            "FROM order_events oe JOIN trades t ON oe.trade_id = t.id"
        ).fetchall()
    assert len(joined) == 1
    assert joined[0]["canonical_status"] == "FILLED"
    assert joined[0]["sleeve"] == "S"
    # update_trade_lifecycle must not have altered the sleeve tag.
    assert _all_rows(db_path)[0]["sleeve"] == "S"


# --------------------------------------------------------------------------- #
# SleevePositionBook — derived map, broker-authoritative                      #
# --------------------------------------------------------------------------- #


def _fill_row(symbol, sleeve, qty, price, status="FILLED", side="BUY",
              filled_qty=None):
    return {
        "symbol": symbol,
        "sleeve": sleeve,
        "side": side,
        "status": status,
        "filled_qty": qty if filled_qty is None else filled_qty,
        "avg_fill_price": price,
    }


def test_position_map_derives_per_sleeve_qty():
    from src.core.sleeve_positions import book_from_ledger_rows

    rows = [
        _fill_row("AAPL", "S", 10, 100.0),
        _fill_row("AAPL", "S", 5, 110.0),
        _fill_row("AAPL", "L", 20, 90.0),
        _fill_row("MSFT", "S", 5, 200.0, side="SELL"),  # signed negative
    ]
    book = book_from_ledger_rows(rows)
    s = book.get("AAPL", "S")
    assert s["qty"] == 15
    assert s["avg_entry"] == pytest.approx((10 * 100.0 + 5 * 110.0) / 15)
    l = book.get("AAPL", "L")
    assert l["qty"] == 20
    assert book.get("MSFT", "S")["qty"] == -5
    assert set(book.symbols()) == {"AAPL", "MSFT"}


def test_position_map_skips_unconfirmed_and_untagged_rows():
    from src.core.sleeve_positions import book_from_ledger_rows

    rows = [
        _fill_row("SUB", "S", 10, 100.0, status="SUBMITTED"),
        _fill_row("REJ", "S", 10, 100.0, status="REJECTED"),
        _fill_row("LEG", None, 10, 100.0),          # legacy NULL sleeve
        _fill_row("BAD", "X", 10, 100.0),           # invalid sleeve
        _fill_row("NOQ", "S", None, 100.0),         # no confirmed qty
    ]
    book = book_from_ledger_rows(rows)
    assert len(book) == 0
    assert book.get("SUB", "S") is None


def test_position_map_full_exit_resets_entry():
    from src.core.sleeve_positions import book_from_ledger_rows

    rows = [
        _fill_row("AAPL", "S", 10, 100.0),
        _fill_row("AAPL", "S", 10, 105.0, side="SELL"),
    ]
    book = book_from_ledger_rows(rows)
    pos = book.get("AAPL", "S")
    assert pos["qty"] == 0
    assert pos["avg_entry"] is None


def test_position_map_fail_closed_on_read_error():
    from src.core.sleeve_positions import book_from_db

    class ExplodingDB:
        def get_all_trades(self, limit=None):
            raise RuntimeError("ledger unavailable")

    book = book_from_db(ExplodingDB())  # must not raise
    assert len(book) == 0


def test_position_map_broker_authoritative_contract():
    """The book is derived-only: it exposes data but contains no
    order-submission, sizing, or gating surface (no methods that
    touch a broker or produce trading decisions)."""
    from src.core import sleeve_positions

    forbidden = {"submit_order", "size_position", "execute", "sell", "buy"}
    assert not forbidden & set(dir(sleeve_positions.SleevePositionBook))


def test_position_map_from_db_builds_from_ledger(tmp_path, monkeypatch):
    db, db_path = _initialised_db(tmp_path, monkeypatch)
    _seed_session(db_path)
    assert db.log_trade(
        42,
        {
            "session_id": 42,
            "symbol": "AAPL",
            "side": "BUY",
            "qty": 10,
            "price": 100.0,
            "pnl": None,
            "signal": "BUY",
            "status": "SUBMITTED",
            "broker_order_id": "paper-pm-9",
            "sleeve": "S",
        },
    )
    trade_id = _all_rows(db_path)[0]["id"]
    db.update_trade_lifecycle(
        trade_id=trade_id,
        status="FILLED",
        filled_qty=10.0,
        avg_fill_price=100.0,
        broker_order_id="paper-pm-9",
    )
    from src.core.sleeve_positions import book_from_db

    book = book_from_db(db)
    assert book.get("AAPL", "S")["qty"] == 10
