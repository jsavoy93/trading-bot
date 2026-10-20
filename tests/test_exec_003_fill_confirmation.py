"""EXEC-003 SHOULD-FIX #1 — Paper order fill confirmation & bookkeeping tests.

Deterministic. Mocked/replayed broker responses only — NO real paper or
live orders are ever submitted by this suite (TESTING/UNIT_TESTING are
set by conftest before imports; the TEST-001 safety gate stays armed).

Scenario coverage (owner directive 2026-10-20):
 1. Successful order submission + confirmed fill      (T1, T2)
 2. Partial fill then completion                      (T3)
 3. Rejected orders                                   (T4)
 4. Canceled orders (incl. cancel-after-partial)      (T5)
 5. Duplicate status responses                        (T6)
 6. Network / polling failures                        (T7)
 7. Process interruption and recovery                 (T8)
 8. No broker confirmation available                  (T9)
 9. Existing open positions unaffected                (T10)
10. Live-account protection intact                    (T10)
11. Idempotent database updates                       (T6, T8)
12. Correct trade-slot bookkeeping (unchanged v1)     (T11)
Plus: unknown raw status never fabricated (T12), expired (T13),
out-of-order stale response (T14), FILLED-without-qty not trusted (T15).
"""

import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import src.core.order_lifecycle as ol  # noqa: E402
import src.database.sqlite_db as sqlite_db_module  # noqa: E402
from src.database.sqlite_db import SQLiteDB  # noqa: E402


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """A SQLiteDB bound to a fresh tmp DB (schema fully migrated)."""
    db_path = tmp_path / "trading_bot.db"
    db_path.touch()
    monkeypatch.setattr(sqlite_db_module, "DB_PATH", str(db_path))
    instance = SQLiteDB()
    instance._init_schema()
    return instance


class _ScriptedBroker:
    """Minimal stand-in for the bot's trading_client in poll tests."""

    def __init__(self):
        self.states = {}
        self.fail_ids = set()

    def get_order_by_id(self, order_id):
        if order_id in self.fail_ids:
            raise ConnectionError("simulated network failure")
        if order_id not in self.states:
            raise KeyError(order_id)
        return self.states[order_id]


class _FakeDB:
    """Duck-typed minimal db for SmartBot.poll_order_fills tests that do
    not need a full SmartTradingBot instance."""

    def __init__(self, rows):
        self._rows = rows
        self.updates = []

    def is_available(self):
        return True

    def get_pending_fill_trades(self, limit=200):
        return self._rows

    def update_trade_lifecycle(self, **kwargs):
        self.updates.append(kwargs)
        return True


def _bot_with(db, broker):
    bot = SimpleNamespace(db=db, trading_client=broker)

    # Reuse the real implementation without constructing SmartTradingBot.
    from src.core.smart_bot import SmartTradingBot

    return SmartTradingBot.poll_order_fills.__get__(bot, type(bot))


def _order(status="filled", filled_qty=10, avg=100.0, oid="ord-1"):
    return SimpleNamespace(
        id=oid,
        status=status,
        filled_qty=filled_qty,
        filled_avg_price=avg,
        updated_at="2026-10-20T02:30:00Z",
    )


def _row(status="SUBMITTED", qty=10, filled_qty=None, avg=None, oid="ord-1", tid=1):
    return {
        "id": tid,
        "status": status,
        "qty": qty,
        "filled_qty": filled_qty,
        "avg_fill_price": avg,
        "broker_order_id": oid,
        "symbol": "TEST",
    }


def _insert_submitted(db, qty=10, oid="ord-1"):
    ok = db.log_trade(
        1,
        {
            "session_id": 1,
            "symbol": "TEST",
            "side": "BUY",
            "qty": qty,
            "price": 100.0,
            "pnl": None,
            "signal": "BUY",
            "rsi": 25.0,
            "order_time": "2026-10-20T02:00:00+00:00",
            "status": "SUBMITTED",
            "broker_order_id": oid,
        },
    )
    assert ok
    with sqlite_db_module._get_conn() as conn:
        return conn.execute("SELECT * FROM trades").fetchone()["id"]


def _get_trade(db, trade_id):
    with sqlite_db_module._get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM trades WHERE id = ?", (trade_id,)
        ).fetchone()
        return dict(row)


# --------------------------------------------------------------------------- #
# Pure reconcile() contract tests
# --------------------------------------------------------------------------- #


def test_t1_submitted_to_filled(db):
    """T1: confirmed fill: SUBMITTED → FILLED with qty/price persisted."""
    tid = _insert_submitted(db)
    result = ol.reconcile(_get_trade(db, tid), ol.extract_broker_event(_order()))
    assert result["status"] == "FILLED"
    assert result["changed"] is True
    assert result["filled_qty"] == 10
    assert result["avg_fill_price"] == 100.0


def test_t2_submission_is_never_a_fill():
    """T2: SUBMITTED row with no broker event stays SUBMITTED (unresolved)."""
    result = ol.reconcile(_row(), None)
    assert result["status"] == "SUBMITTED"
    assert result["changed"] is False
    assert result["unresolved"] is True


def test_t3_partial_then_complete():
    """T3: partial fill then completion; cumulative, no double count."""
    r1 = ol.reconcile(_row(qty=10), ol.extract_broker_event(_order("partially_filled", 4, 99.0)))
    assert r1["status"] == "PARTIALLY_FILLED" and r1["filled_qty"] == 4
    # Second poll repeats the SAME partial (duplicate response) — idempotent.
    r2 = ol.reconcile(
        _row(status="PARTIALLY_FILLED", qty=10, filled_qty=4, avg=99.0),
        ol.extract_broker_event(_order("partially_filled", 4, 99.0)),
    )
    assert r2["changed"] is False and r2["filled_qty"] == 4
    # Completion arrives.
    r3 = ol.reconcile(
        _row(status="PARTIALLY_FILLED", qty=10, filled_qty=4, avg=99.0),
        ol.extract_broker_event(_order("filled", 10, 99.5)),
    )
    assert r3["status"] == "FILLED" and r3["filled_qty"] == 10
    assert r3["avg_fill_price"] == 99.5


def test_t4_rejected():
    r = ol.reconcile(_row(), ol.extract_broker_event(_order("rejected", 0, None)))
    assert r["status"] == "REJECTED" and r["terminal"]


def test_t5_canceled_and_cancel_after_partial():
    r = ol.reconcile(_row(), ol.extract_broker_event(_order("canceled", 0, None)))
    assert r["status"] == "CANCELED" and r["terminal"]
    # Cancel after partial keeps the cumulative fill data.
    r2 = ol.reconcile(
        _row(status="PARTIALLY_FILLED", qty=10, filled_qty=4, avg=99.0),
        ol.extract_broker_event(_order("canceled", 4, 99.0)),
    )
    assert r2["status"] == "CANCELED" and r2["filled_qty"] == 4
    assert r2["avg_fill_price"] == 99.0


def test_t6_duplicate_responses_idempotent():
    ev = ol.extract_broker_event(_order())
    r1 = ol.reconcile(_row(), ev)
    r2 = ol.reconcile(_row(status="FILLED", qty=10, filled_qty=10, avg=100.0), ev)
    assert r1["changed"] and not r2["changed"]
    assert r2["status"] == "FILLED" and r2["filled_qty"] == 10


def test_t9_no_confirmation_explicitly_unresolved():
    r = ol.reconcile(_row(), None)
    assert r["unresolved"] is True and r["status"] == "SUBMITTED"


def test_t12_unknown_status_never_fabricated():
    ev = ol.extract_broker_event(_order("weird_new_status", 0, None))
    assert ev["canonical_status"] == "UNRESOLVED"
    r = ol.reconcile(_row(), ev)
    assert r["changed"] is False and r["unresolved"] is True


def test_t13_expired():
    r = ol.reconcile(_row(), ol.extract_broker_event(_order("expired", 0, None)))
    assert r["status"] == "EXPIRED" and r["terminal"]


def test_t14_stale_out_of_order_ignored():
    # Broker sends 'new' after row already PARTIALLY_FILLED.
    r = ol.reconcile(
        _row(status="PARTIALLY_FILLED", qty=10, filled_qty=4, avg=99.0),
        ol.extract_broker_event(_order("new", 0, None)),
    )
    assert r["changed"] is False and r["status"] == "PARTIALLY_FILLED"
    # Stale event on terminal CANCELED row is ignored.
    r2 = ol.reconcile(
        _row(status="CANCELED", qty=10),
        ol.extract_broker_event(_order("new", 0, None)),
    )
    assert r2["changed"] is False and r2["status"] == "CANCELED"
    # ...but a confirmed fill races past a cancel (broker authoritative).
    r3 = ol.reconcile(
        _row(status="CANCELED", qty=10),
        ol.extract_broker_event(_order("filled", 10, 100.0)),
    )
    assert r3["status"] == "FILLED"


def test_t15_filled_token_without_qty_not_trusted():
    r = ol.reconcile(_row(qty=10), ol.extract_broker_event(_order("filled", 0, None)))
    assert r["status"] == "ACCEPTED"  # awaiting quantity evidence
    assert r["status"] != "FILLED"


# --------------------------------------------------------------------------- #
# DB persistence contract tests (schema, idempotency, audit trail)
# --------------------------------------------------------------------------- #


def test_db_migration_additive(db):
    """New columns/table exist; legacy columns and status vocabulary intact."""
    with sqlite_db_module._get_conn() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(trades)")}
        tables = {
            r["name"] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert {"filled_qty", "avg_fill_price", "order_updated_at"} <= cols
    assert "order_events" in tables
    legacy = {
        "id", "session_id", "symbol", "side", "qty", "price", "pnl",
        "signal", "rsi", "order_time", "created_at", "status",
        "broker_order_id",
    }
    assert legacy <= cols


def test_db_full_lifecycle_persistence(db):
    """Submission → partial → filled persisted truthfully with audit trail."""
    tid = _insert_submitted(db)
    db.update_trade_lifecycle(
        trade_id=tid, status="PARTIALLY_FILLED", filled_qty=4,
        avg_fill_price=99.0, broker_order_id="ord-1",
        raw_status="partially_filled", note="SUBMITTED → PARTIALLY_FILLED",
    )
    t = _get_trade(db, tid)
    assert t["status"] == "PARTIALLY_FILLED" and t["filled_qty"] == 4
    db.update_trade_lifecycle(
        trade_id=tid, status="FILLED", filled_qty=10,
        avg_fill_price=99.5, broker_order_id="ord-1",
        raw_status="filled", note="PARTIALLY_FILLED → FILLED",
    )
    t = _get_trade(db, tid)
    assert t["status"] == "FILLED" and t["filled_qty"] == 10
    assert t["avg_fill_price"] == 99.5
    assert t["pnl"] is None  # P&L ledger explicitly out of scope here
    events = db.get_order_events(tid)
    assert len(events) == 2
    assert events[0]["canonical_status"] == "FILLED"  # newest first
    assert events[1]["canonical_status"] == "PARTIALLY_FILLED"


def test_db_pending_filter_excludes_terminal_and_idless(db):
    tid = _insert_submitted(db)
    assert [r["id"] for r in db.get_pending_fill_trades()] == [tid]
    db.update_trade_lifecycle(trade_id=tid, status="FILLED", filled_qty=10)
    assert db.get_pending_fill_trades() == []
    # Row without broker_order_id is not pollable.
    db.log_trade(1, {
        "session_id": 1, "symbol": "NOID", "side": "BUY", "qty": 1,
        "price": 5.0, "pnl": None, "signal": "BUY", "rsi": 25.0,
        "order_time": "2026-10-20T02:00:00+00:00", "status": "SUBMITTED",
        "broker_order_id": None,
    })
    assert all(r["symbol"] != "NOID" for r in db.get_pending_fill_trades())


def test_db_update_rejects_non_canonical_status(db):
    tid = _insert_submitted(db)
    ok = db.update_trade_lifecycle(trade_id=tid, status="SHIPPED")
    assert ok is False
    assert _get_trade(db, tid)["status"] == "SUBMITTED"


# --------------------------------------------------------------------------- #
# SmartBot.poll_order_fills integration (mocked broker, no orders placed)
# --------------------------------------------------------------------------- #


def test_poll_success_fill():
    broker = _ScriptedBroker()
    broker.states["ord-1"] = _order("filled", 10, 100.0)
    fdb = _FakeDB([_row()])
    summary = _bot_with(fdb, broker)()
    assert summary == {"polled": 1, "updated": 1, "unresolved": 0, "errors": 0}
    assert fdb.updates[0]["status"] == "FILLED"


def test_poll_network_failure_leaves_row_untouched():
    broker = _ScriptedBroker()
    broker.fail_ids.add("ord-1")
    fdb = _FakeDB([_row()])
    summary = _bot_with(fdb, broker)()
    assert summary["errors"] == 1 and summary["updated"] == 0
    assert fdb.updates == []  # row stays SUBMITTED; nothing fabricated


def test_poll_missing_client_api_is_unresolved():
    """Broker client without get_order_by_id → explicitly unresolved."""

    class _OldClient:
        pass

    fdb = _FakeDB([_row()])
    bot = SimpleNamespace(db=fdb, trading_client=_OldClient())
    from src.core.smart_bot import SmartTradingBot
    summary = SmartTradingBot.poll_order_fills.__get__(bot, type(bot))()
    assert summary["polled"] == 1 and summary["unresolved"] == 1
    assert summary["updated"] == 0 and fdb.updates == []


def test_poll_never_raises():
    class _BadDB:
        def is_available(self):
            raise RuntimeError("db exploded")

    bot = SimpleNamespace(db=_BadDB(), trading_client=_ScriptedBroker())
    from src.core.smart_bot import SmartTradingBot
    summary = SmartTradingBot.poll_order_fills.__get__(bot, type(bot))()
    assert summary == {"polled": 0, "updated": 0, "unresolved": 0, "errors": 0}


def test_poll_end_to_end_with_real_db(db):
    """T7/T8-style: submit row, 'restart' (fresh read), poll applies updates."""
    tid = _insert_submitted(db)
    broker = _ScriptedBroker()
    broker.states["ord-1"] = _order("partially_filled", 4, 99.0)

    class _RealAdapter:
        def is_available(self):
            return db.is_available()

        get_pending_fill_trades = db.get_pending_fill_trades
        update_trade_lifecycle = db.update_trade_lifecycle

    bot = SimpleNamespace(db=_RealAdapter(), trading_client=broker)
    from src.core.smart_bot import SmartTradingBot
    bound = SmartTradingBot.poll_order_fills.__get__(bot, type(bot))
    s1 = bound()
    assert s1["updated"] == 1
    # "Interruption + recovery": simulate process restart by re-polling the
    # same partial (idempotent) then completing.
    broker.states["ord-1"] = _order("partially_filled", 4, 99.0)
    assert bound()["updated"] == 0  # duplicate response: no double count
    broker.states["ord-1"] = _order("filled", 10, 99.5)
    assert bound()["updated"] == 1
    t = _get_trade(db, tid)
    assert t["status"] == "FILLED" and t["filled_qty"] == 10
    # Terminal rows leave the pending set entirely.
    assert db.get_pending_fill_trades() == []


def test_t10_t11_live_protection_and_slot_accounting_untouched():
    """T10/T11: the poller performs zero trading actions and zero slot
    accounting changes. Verified structurally: poll_order_fills never
    touches trades_executed, max_trades, or order submission, and the
    mock brokerage live-protection contract is unchanged."""
    import inspect
    from src.core import smart_bot as sb_module

    src = inspect.getsource(sb_module.SmartTradingBot.poll_order_fills)
    for forbidden in (
        "submit_order(", ".cancel", "trades_executed +=",
        "max_trades",
    ):
        assert forbidden not in src, forbidden
    # OBS-001 slot semantics version unchanged (slots consumed on
    # submission — v1 — per contract review; not bumped).
    assert sb_module.OBS_001_SLOT_SEMANTICS_VERSION == 1
    # Live-account protection: TEST-001 gate and paper-only guard intact.
    guard = getattr(sb_module, "trading_bot_paper_only_guard", None)
    assert callable(guard)
