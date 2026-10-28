"""EXEC-004 (2026-10-28) — tranche/stop-loss/portfolio ledger fidelity.

Owner-approved scope (PAPER ONLY, no merge/deploy without approval):

  1. Tranche 2/3 BUYs recorded in the ledger exactly like tranche 1
     (submission persistence landed in PR #109; this file also guards it).
  2. Tranches 2/3 must pass the SAME risk checks as an initial entry
     (margin, pending-BUY duplication, position concentration, sector,
     correlation, beta) BEFORE any order is submitted.
  3. Stop-loss and portfolio-management orders must go through the ledger
     (SUBMITTED rows with broker_order_id, reconciled by the fill poller).
  4. A one-time reconciliation path (scripts/reconcile_ledger_with_broker.py)
     backfills FILLED ledger rows from broker history WITHOUT placing
     orders, and is idempotent.

Every test that encodes NEW behaviour FAILS on main 9c4c2a2 and PASSES
with the fixes. The tranche-submission persistence guards (a7/b1) pass on
main too — they protect must-not-change behaviour from PR #109.

Deterministic. Mocked broker responses only — NO Alpaca/network calls and
no order is ever submitted to a real endpoint (TESTING / UNIT_TESTING are
set by conftest before imports; the paper-only guard stays armed).
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
# The bot runs with src/ as its path root (python src/main.py), which is
# what makes _execute_pending_entry_tranches' inner
# `from analysis.position_scaling...` import work. Mirror that here.
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import src.database.sqlite_db as sqlite_db_module  # noqa: E402
from src.core.smart_bot import SmartTradingBot  # noqa: E402


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """A SQLiteDB bound to a fresh tmp DB (schema fully migrated)."""
    db_path = tmp_path / "trading_bot.db"
    db_path.touch()
    monkeypatch.setattr(sqlite_db_module, "DB_PATH", str(db_path))
    instance = sqlite_db_module.SQLiteDB()
    instance._init_schema()
    return instance


def _rows(db, symbol=None):
    with sqlite_db_module._get_conn() as conn:
        sql = "SELECT * FROM trades"
        params = ()
        if symbol:
            sql += " WHERE symbol = ?"
            params = (symbol,)
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


class _Iloc:
    def __init__(self, row):
        self.row = row

    def __getitem__(self, _i):
        return self.row


class _FakeDF:
    """df.iloc[-1] → dict row with ['close'] and .get(...) support."""

    def __init__(self, row):
        self.iloc = _Iloc(row)


def _pending_buy_order(symbol="TEST", oid="open-1"):
    from alpaca.trading.enums import OrderStatus

    return SimpleNamespace(
        id=oid, symbol=symbol, side="buy", status=OrderStatus.NEW
    )


def _open_stop_order(symbol="TEST", oid="stop-1"):
    from alpaca.trading.enums import OrderStatus

    return SimpleNamespace(
        id=oid, symbol=symbol, side="sell", status=OrderStatus.NEW
    )


class _RecordingClient:
    """Stand-in trading_client: records submits, scripts everything else."""

    def __init__(self, cash=1000.0, orders=(), positions=None):
        self.cash = cash
        self._orders = list(orders)
        self.positions = positions or []
        self.submitted = []

    def get_account(self):
        return SimpleNamespace(cash=self.cash, portfolio_value=100000.0)

    def get_orders(self):
        return list(self._orders)

    def get_all_positions(self):
        return list(self.positions)

    def submit_order(self, order_data):
        self.submitted.append(order_data)
        return SimpleNamespace(id=f"ord-{len(self.submitted)}")


_PASS_CHECKS = dict(
    check_position_limits=lambda s, q, p, pv: (True, q, 5.0),
    check_sector_concentration=lambda s, d: (True, 0.0, 0.0, "ok"),
    check_correlation_risk=lambda s: (True, 0.0, [], "ok"),
    check_beta_exposure=lambda: (True, 1.0, "ok"),
    get_portfolio_total_value=lambda: 100000.0,
)


def _tranche_bot(db, client, **check_overrides):
    """A namespace with everything _execute_pending_entry_tranches needs."""
    checks = dict(_PASS_CHECKS)
    checks.update(check_overrides)
    checks.update(
        db=db,
        session_id=7,
        trading_client=client,
        _pending_entry_tranches={
            "TEST": {
                "entry_price": 100.0,
                "created_at": None,
                "tranches": [
                    {"tranche_num": 1, "qty": 3, "status": "submitted",
                     "trigger": "immediate"},
                    {"tranche_num": 2, "qty": 5, "status": "pending",
                     "trigger": "pullback_to_vwap_or_confirm"},
                ],
            }
        },
        get_market_data=lambda s: _FakeDF(
            {"close": 99.0, "RSI": 40.0, "vwap": 100.0, "volume_ratio": 1.2}
        ),
        calculate_indicators=lambda df: df,
    )
    ns = SimpleNamespace(**checks)
    # Bind the REAL gate to the namespace so the tests exercise the
    # production gate logic against the stubbed checks above.
    ns._tranche_risk_gate = (
        lambda symbol, qty, price: SmartTradingBot._tranche_risk_gate(ns, symbol, qty, price)
    )
    return ns


def _run_tranches(bot):
    SmartTradingBot._execute_pending_entry_tranches(bot)


# --------------------------------------------------------------------------- #
# A. Tranche 2/3 risk gate — blocks BEFORE any order is submitted
# --------------------------------------------------------------------------- #


def _assert_blocked_no_side_effects(bot, client, db):
    plan = bot._pending_entry_tranches["TEST"]  # snapshot: a completed plan is popped
    _run_tranches(bot)
    assert client.submitted == [], "risk gate must block the order submission"
    rows = _rows(db, "TEST")
    assert rows == [], "a blocked tranche must not write a ledger row"
    tranche = plan["tranches"][1]
    assert tranche["status"] == "blocked_risk"
    assert tranche.get("block_reason")


def test_a1_margin_blocks_tranche(db):
    client = _RecordingClient(cash=-50.0)
    bot = _tranche_bot(db, client)
    plan = bot._pending_entry_tranches["TEST"]
    _assert_blocked_no_side_effects(bot, client, db)
    assert "margin" in plan["tranches"][1]["block_reason"]


def test_a2_pending_buy_order_blocks_tranche(db):
    client = _RecordingClient(orders=[_pending_buy_order()])
    bot = _tranche_bot(db, client)
    _assert_blocked_no_side_effects(bot, client, db)


def test_a2b_open_stop_loss_sell_does_not_block_tranche(db):
    """Held positions normally carry an open stop SELL — it must NOT block."""
    client = _RecordingClient(orders=[_open_stop_order()], cash=1000.0)
    bot = _tranche_bot(db, client)
    _run_tranches(bot)
    assert len(client.submitted) == 1, "stop SELL order must not block a tranche BUY"


def test_a3_position_limit_blocks_tranche(db):
    client = _RecordingClient()
    bot = _tranche_bot(
        db, client, check_position_limits=lambda s, q, p, pv: (False, 0, 12.0)
    )
    _assert_blocked_no_side_effects(bot, client, db)


def test_a4_sector_concentration_blocks_tranche(db):
    client = _RecordingClient()
    bot = _tranche_bot(
        db, client,
        check_sector_concentration=lambda s, d: (False, 35.0, 40.0, "Sector X > 30%"),
    )
    _assert_blocked_no_side_effects(bot, client, db)


def test_a5_correlation_blocks_tranche(db):
    client = _RecordingClient()
    bot = _tranche_bot(
        db, client,
        check_correlation_risk=lambda s: (False, 0.9, ["AAA"], "correlated"),
    )
    _assert_blocked_no_side_effects(bot, client, db)


def test_a6_beta_blocks_tranche(db):
    client = _RecordingClient()
    bot = _tranche_bot(
        db, client, check_beta_exposure=lambda: (False, 1.7, "beta 1.7 > 1.5")
    )
    _assert_blocked_no_side_effects(bot, client, db)


def test_a9_gate_fails_closed_on_unexpected_error(db):
    def _boom():
        raise RuntimeError("unexpected gate error")

    client = _RecordingClient()
    bot = _tranche_bot(db, client, check_position_limits=_boom)
    plan = bot._pending_entry_tranches["TEST"]
    _assert_blocked_no_side_effects(bot, client, db)
    assert "fail-closed" in plan["tranches"][1]["block_reason"]


# --------------------------------------------------------------------------- #
# B. Tranche 2/3 ledger recording — exactly like tranche 1
# --------------------------------------------------------------------------- #


def test_b1_passing_tranche_is_ledgered_like_tranche_1(db):
    """Guard (passes on main): a submitted tranche persists a SUBMITTED
    row with broker_order_id + tranche note, status 'submitted' not 'filled'."""
    client = _RecordingClient(cash=1000.0)
    bot = _tranche_bot(db, client)
    plan = bot._pending_entry_tranches["TEST"]
    _run_tranches(bot)
    assert len(client.submitted) == 1
    rows = _rows(db, "TEST")
    assert len(rows) == 1
    r = rows[0]
    assert r["status"] == "SUBMITTED"
    assert r["side"] == "BUY"
    assert r["broker_order_id"] == "ord-1"
    assert r["session_id"] == 7
    assert "tranche 2" in (r.get("notes") or "")
    tranche = plan["tranches"][1]
    assert tranche["status"] == "submitted"
    assert "TEST" not in bot._pending_entry_tranches  # plan completed


def test_b2_position_limit_adjustment_reduces_tranche_qty(db):
    client = _RecordingClient()
    bot = _tranche_bot(
        db, client, check_position_limits=lambda s, q, p, pv: (True, 2, 9.0)
    )
    _run_tranches(bot)
    assert len(client.submitted) == 1
    assert client.submitted[0].qty == 2, "gate must use the risk-adjusted quantity"


# --------------------------------------------------------------------------- #
# C. Stop-loss and portfolio orders go through the ledger
# --------------------------------------------------------------------------- #


def _stop_loss_bot(db, client):
    return SimpleNamespace(
        db=db,
        session_id=9,
        trading_client=client,
        has_active_stop_order=lambda s: False,
        has_pending_orders=lambda s: False,
        send_trade_notification=lambda **kw: None,
    )


def test_c1_stop_loss_order_writes_ledger_row(db):
    client = _RecordingClient(
        positions=[SimpleNamespace(symbol="TEST", qty="10", market_value="900.0")]
    )
    bot = _stop_loss_bot(db, client)
    ok = SmartTradingBot._place_stop_loss_order(bot, "TEST", 10, 92.0)
    assert ok is True
    assert len(client.submitted) == 1
    rows = _rows(db, "TEST")
    assert len(rows) == 1, "a stop-loss submission MUST leave a ledger row"
    r = rows[0]
    assert r["side"] == "SELL"
    assert r["status"] == "SUBMITTED"
    assert r["broker_order_id"] == "ord-1"
    assert "STOP-LOSS" in (r.get("notes") or "")


def test_c2_portfolio_order_writes_ledger_row(db):
    client = _RecordingClient()
    bot = SimpleNamespace(
        db=db,
        session_id=9,
        trading_client=client,
        send_trade_notification=lambda **kw: None,
    )
    ok = SmartTradingBot._place_portfolio_order(
        bot, "TEST", 4, "sell", "Reduce concentration risk - 12.0% of portfolio"
    )
    assert ok is True
    rows = _rows(db, "TEST")
    assert len(rows) == 1, "a portfolio order MUST leave a ledger row"
    r = rows[0]
    assert r["side"] == "SELL"
    assert r["status"] == "SUBMITTED"
    assert r["qty"] == 4
    assert r["broker_order_id"] == "ord-1"
    assert "Portfolio Management" in (r.get("notes") or "")
    assert r["pnl"] is None


def test_c3_stop_loss_and_portfolio_rows_are_pollable(db):
    """The new rows must be picked up by the fill poller query."""
    db.log_trade(9, {
        "session_id": 9, "symbol": "TEST", "side": "SELL", "qty": 10,
        "price": 92.0, "pnl": None, "signal": "SELL", "rsi": None,
        "order_time": "2026-10-28T20:00:00+00:00", "status": "SUBMITTED",
        "broker_order_id": "ord-1", "notes": "STOP-LOSS stop=$92.00",
    })
    pending = db.get_pending_fill_trades()
    assert any(r["broker_order_id"] == "ord-1" for r in pending)


# --------------------------------------------------------------------------- #
# D. One-time reconciliation (scripts/reconcile_ledger_with_broker.py)
# --------------------------------------------------------------------------- #

try:
    import reconcile_ledger_with_broker as recon  # noqa: E402
except ModuleNotFoundError:  # pristine main has no script yet → tests must FAIL there
    recon = None


def _require_recon():
    assert recon is not None, (
        "scripts/reconcile_ledger_with_broker.py is missing — the EXEC-004 "
        "reconciliation path is not implemented on this checkout"
    )


def _broker_order(oid, symbol="QQQI", side="buy", status="filled",
                  filled_qty=20, avg=90.0, created="2026-10-01T15:00:00Z"):
    return SimpleNamespace(
        id=oid, symbol=symbol, side=side, status=status, qty=filled_qty,
        filled_qty=filled_qty if status == "filled" else 0,
        filled_avg_price=avg, created_at=created,
    )


def test_d1_only_unrecorded_fills_are_planned():
    _require_recon()
    ledger = [{"broker_order_id": "a"}, {"broker_order_id": None}]
    orders = [
        _broker_order("a"),                       # already recorded
        _broker_order("b"),                       # missing
        _broker_order("c", status="canceled"),    # not a fill
        _broker_order("d", status="filled", filled_qty=0),  # no qty
    ]
    orders = [recon.normalize_broker_order(o) for o in orders]
    missing = recon.find_unrecorded_fills(ledger, orders)
    assert [o["id"] for o in missing] == ["b"]


def test_d2_backfill_row_uses_only_trades_schema_columns(db):
    _require_recon()
    row = recon.build_backfill_row(
        recon.normalize_broker_order(_broker_order("b", filled_qty=59, avg=98.5))
    )
    with sqlite_db_module._get_conn() as conn:
        schema_cols = {
            r["name"] for r in conn.execute("PRAGMA table_info(trades)").fetchall()
        }
    assert set(row) <= schema_cols, "backfill row must match the trades schema"
    assert row["status"] == "FILLED"
    assert row["side"] == "BUY"
    assert row["broker_order_id"] == "b"
    assert row["filled_qty"] == 59
    assert row["pnl"] is None
    assert "reconciliation" in row["notes"]


def test_d3_reconciliation_is_idempotent(db):
    _require_recon()
    db.log_trade(1, {
        "session_id": 1, "symbol": "QQQI", "side": "BUY", "qty": 39,
        "price": 90.0, "pnl": None, "signal": "BUY", "rsi": None,
        "order_time": "2026-10-01T14:00:00+00:00", "status": "FILLED",
        "broker_order_id": "a",
    })
    orders = [recon.normalize_broker_order(o) for o in (
        _broker_order("a"), _broker_order("b", filled_qty=59, avg=98.5)
    )]
    ledger_rows = [
        {"broker_order_id": "a", "symbol": "QQQI", "side": "BUY",
         "status": "FILLED", "qty": 39, "filled_qty": 39},
    ]
    missing = recon.find_unrecorded_fills(ledger_rows, orders)
    assert [o["id"] for o in missing] == ["b"]

    inserted = recon.apply_backfill(db, [recon.build_backfill_row(o) for o in missing])
    assert inserted == 1

    # Re-plan against the updated ledger: nothing left to backfill.
    ledger_rows.append({"broker_order_id": "b", "symbol": "QQQI", "side": "BUY",
                        "status": "FILLED", "qty": 59, "filled_qty": 59})
    assert recon.find_unrecorded_fills(ledger_rows, orders) == []


def test_d4_position_summary_is_signed_by_side():
    _require_recon()
    ledger = [
        {"symbol": "QQQI", "side": "BUY", "status": "FILLED", "qty": 39,
         "filled_qty": 39, "broker_order_id": "a"},
        {"symbol": "QQQI", "side": "BUY", "status": "FILLED", "qty": 59,
         "filled_qty": 59, "broker_order_id": "b"},
        {"symbol": "QQQI", "side": "SELL", "status": "FILLED", "qty": 10,
         "filled_qty": 10, "broker_order_id": "c"},
        {"symbol": "QQQI", "side": "BUY", "status": "SUBMITTED", "qty": 5,
         "filled_qty": None, "broker_order_id": "d"},  # not a fill — ignored
    ]
    positions = [SimpleNamespace(symbol="QQQI", qty="98")]
    summary = recon.summarize_positions(ledger, positions)
    q = next(s for s in summary if s["symbol"] == "QQQI")
    assert q["broker_qty"] == 98
    assert q["ledger_qty"] == 88  # 39 + 59 - 10
    assert q["gap"] == 10


def test_d5_script_never_submits_orders():
    _require_recon()
    """Static safety guard: no order-submission call anywhere in the script."""
    source = (PROJECT_ROOT / "scripts" / "reconcile_ledger_with_broker.py").read_text()
    assert ".submit_order(" not in source
    assert ".cancel_order(" not in source
    assert ".replace_order(" not in source


def test_d6_dry_run_writes_nothing_apply_writes(monkeypatch, tmp_path, db):
    _require_recon()
    db_file = Path(sqlite_db_module.DB_PATH)
    fake_client = SimpleNamespace(
        get_orders=lambda req: [_broker_order("b", filled_qty=59, avg=98.5)],
        get_all_positions=lambda: [SimpleNamespace(symbol="QQQI", qty="98")],
    )
    monkeypatch.setattr(recon, "_make_read_only_client", lambda: fake_client)

    rc = recon.main(["--db", str(db_file)])
    assert rc == 0
    assert _rows(db) == [], "dry run must not write"

    rc = recon.main(["--db", str(db_file), "--apply"])
    assert rc == 0
    rows = _rows(db)
    assert len(rows) == 1
    assert rows[0]["broker_order_id"] == "b"
    assert rows[0]["status"] == "FILLED"
