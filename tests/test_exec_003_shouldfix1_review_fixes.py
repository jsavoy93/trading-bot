"""EXEC-003 SHOULD-FIX #1 — regression net for the 2026-10-20 review fixes.

PR #109 (head ce6b92a) was reviewed on 2026-10-20 and three defects were
confirmed OPEN. This suite is the regression net for the authorized fixes:

  (a) Unresolved broker outcomes (network failure, unrecognized status,
      reconcile exception) must leave a durable audit trail — exactly ONE
      ``order_events`` row with ``unresolved = 1`` — and must NOT modify
      the ``trades`` row.
  (b) Lifecycle state must not regress on out-of-order broker statuses,
      and late fill evidence on a CANCELED / EXPIRED row must still be
      reachable (bounded 24h re-poll horizon).
  (c) Follow-up entry tranches (2/3) must be persisted through the same
      ledger as tranche 1 so the poller can reconcile them, and must be
      marked ``submitted`` — never ``filled``.

Every test in this file FAILS on ce6b92a and PASSES with the fixes.

Deterministic. Mocked broker responses only — NO Alpaca/network calls and
no order is ever submitted to a real endpoint (TESTING / UNIT_TESTING are
set by conftest before imports; the paper-only guard stays armed).
"""

import sqlite3  # noqa: F401  (kept for parity with the sibling suite)
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import src.core.order_lifecycle as ol  # noqa: E402
import src.database.sqlite_db as sqlite_db_module  # noqa: E402


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


class _ScriptedBroker:
    """Minimal stand-in for the bot's trading_client in poll tests."""

    def __init__(self, states=None, fail_ids=None):
        self.states = states or {}
        self.fail_ids = fail_ids or set()

    def get_order_by_id(self, order_id):
        if order_id in self.fail_ids:
            raise ConnectionError("simulated network failure")
        if order_id not in self.states:
            raise KeyError(order_id)
        return self.states[order_id]


class _TrancheBroker:
    """Broker stand-in that records submissions and serves scripted states."""

    def __init__(self, order_id="ord-tranche-2"):
        self.order_id = order_id
        self.submitted = []
        self.states = {}

    def submit_order(self, order_data=None, **kwargs):
        self.submitted.append(order_data)
        return SimpleNamespace(
            id=self.order_id,
            status="new",
            filled_qty=0,
            filled_avg_price=None,
        )

    def get_order_by_id(self, order_id):
        if order_id not in self.states:
            raise KeyError(order_id)
        return self.states[order_id]


def _order(status="filled", filled_qty=10, avg=100.0, oid="ord-1"):
    return SimpleNamespace(
        id=oid,
        status=status,
        filled_qty=filled_qty,
        filled_avg_price=avg,
        updated_at="2026-10-28T14:00:00Z",
    )


def _insert_trade(
    db,
    status="SUBMITTED",
    qty=10,
    oid="ord-1",
    symbol="TEST",
    filled_qty=None,
    avg_fill_price=None,
    order_updated_at=None,
    created_at=None,
    notes=None,
):
    data = {
        "session_id": 1,
        "symbol": symbol,
        "side": "BUY",
        "qty": qty,
        "price": 100.0,
        "pnl": None,
        "signal": "BUY",
        "rsi": 25.0,
        "order_time": "2026-10-28T10:00:00+00:00",
        "status": status,
        "broker_order_id": oid,
    }
    if filled_qty is not None:
        data["filled_qty"] = filled_qty
    if avg_fill_price is not None:
        data["avg_fill_price"] = avg_fill_price
    if order_updated_at is not None:
        data["order_updated_at"] = order_updated_at
    if created_at is not None:
        data["created_at"] = created_at
    if notes is not None:
        data["notes"] = notes
    assert db.log_trade(1, data) is True
    with sqlite_db_module._get_conn() as conn:
        return conn.execute(
            "SELECT id FROM trades ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]


def _trade_row(db, trade_id):
    with sqlite_db_module._get_conn() as conn:
        return dict(
            conn.execute(
                "SELECT * FROM trades WHERE id = ?", (trade_id,)
            ).fetchone()
        )


def _events(db, trade_id):
    with sqlite_db_module._get_conn() as conn:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM order_events WHERE trade_id = ? ORDER BY id ASC",
                (trade_id,),
            )
        ]


def _poll(db, broker):
    """Bind the real SmartBot.poll_order_fills to a lightweight stand-in."""
    from src.core.smart_bot import SmartTradingBot

    fake = SimpleNamespace(db=db, trading_client=broker, session_id=1)
    return SmartTradingBot.poll_order_fills.__get__(fake, type(fake))


def _assert_trades_row_unchanged(before, after):
    for field in (
        "status",
        "filled_qty",
        "avg_fill_price",
        "order_updated_at",
        "pnl",
        "qty",
        "price",
    ):
        assert after[field] == before[field], field


# --------------------------------------------------------------------------- #
# (a) Unresolved broker outcomes must be durable, and must not mutate state
# --------------------------------------------------------------------------- #


def test_a1_network_failure_writes_one_unresolved_event_and_leaves_row_untouched(db):
    """(a) Broker unreachable → exactly one unresolved audit event."""
    tid = _insert_trade(db, status="SUBMITTED", qty=10, oid="ord-1")
    before = _trade_row(db, tid)

    broker = _ScriptedBroker(fail_ids={"ord-1"})
    summary = _poll(db, broker)()

    assert summary["polled"] == 1
    assert summary["unresolved"] == 1
    assert summary["updated"] == 0

    events = _events(db, tid)
    assert len(events) == 1
    assert events[0]["unresolved"] == 1
    assert events[0]["canonical_status"] == "UNRESOLVED"
    assert events[0]["broker_order_id"] == "ord-1"

    _assert_trades_row_unchanged(before, _trade_row(db, tid))


def test_a2_unknown_status_writes_one_unresolved_event_and_leaves_row_untouched(db):
    """(a) Unrecognized broker status → exactly one unresolved audit event."""
    tid = _insert_trade(db, status="SUBMITTED", qty=10, oid="ord-1")
    before = _trade_row(db, tid)

    broker = _ScriptedBroker(
        states={"ord-1": _order("totally_unknown_status", 0, None)}
    )
    summary = _poll(db, broker)()

    assert summary["unresolved"] == 1
    assert summary["updated"] == 0

    events = _events(db, tid)
    assert len(events) == 1
    assert events[0]["unresolved"] == 1
    assert events[0]["raw_status"] == "totally_unknown_status"

    _assert_trades_row_unchanged(before, _trade_row(db, tid))


def test_a3_reconcile_exception_still_records_an_unresolved_event(db, monkeypatch):
    """(a) A reconcile failure is an unresolved outcome, not a silent drop."""
    from src.core import smart_bot as sb_module

    tid = _insert_trade(db, status="SUBMITTED", qty=10, oid="ord-1")
    before = _trade_row(db, tid)

    def _boom(*args, **kwargs):
        raise RuntimeError("reconcile exploded")

    monkeypatch.setattr(sb_module.order_lifecycle, "reconcile", _boom)

    broker = _ScriptedBroker(states={"ord-1": _order("filled", 10, 100.0)})
    summary = _poll(db, broker)()

    assert summary["errors"] == 1
    assert summary["updated"] == 0

    events = _events(db, tid)
    assert len(events) == 1
    assert events[0]["unresolved"] == 1

    _assert_trades_row_unchanged(before, _trade_row(db, tid))


def test_a4_record_order_event_is_append_only(db):
    """(a) record_order_event never mutates the trades row."""
    tid = _insert_trade(db, status="SUBMITTED", qty=10, oid="ord-1")
    before = _trade_row(db, tid)

    ok = db.record_order_event(
        trade_id=tid,
        broker_order_id="ord-1",
        raw_status=None,
        note="manual unresolved note",
        unresolved=True,
    )
    assert ok is True

    assert _trade_row(db, tid) == before

    events = _events(db, tid)
    assert len(events) == 1
    assert events[0]["unresolved"] == 1
    assert events[0]["canonical_status"] == "UNRESOLVED"


def test_a5_unresolved_poll_is_idempotent_in_the_ledger_only(db):
    """Repeated unresolved polls do NOT append duplicate audit rows.

    UPDATED 2026-10-28 (owner-approved follow-up): this test used to
    assert the OLD every-poll behaviour (2 rows after 2 identical
    unresolved polls). An unresolved poll now writes an audit row only
    when the OUTCOME CHANGES, so two identical polls produce exactly
    ONE row. The trades row is unchanged either way.
    """
    tid = _insert_trade(db, status="SUBMITTED", qty=10, oid="ord-1")
    before = _trade_row(db, tid)

    broker = _ScriptedBroker(fail_ids={"ord-1"})
    poll = _poll(db, broker)
    poll()
    poll()

    events = _events(db, tid)
    assert len(events) == 1
    assert events[0]["unresolved"] == 1
    assert events[0]["event_note"] == "broker-unreachable"
    _assert_trades_row_unchanged(before, _trade_row(db, tid))


# --------------------------------------------------------------------------- #
# (b) No lifecycle regression; late fill evidence stays reachable
# --------------------------------------------------------------------------- #


def test_b1_partial_fill_is_not_regressed_by_out_of_order_accepted():
    """(b) PARTIALLY_FILLED 4/10 + a late ACCEPTED 6 stays PARTIALLY_FILLED 6."""
    row = {
        "id": 1,
        "status": "PARTIALLY_FILLED",
        "qty": 10,
        "filled_qty": 4,
        "avg_fill_price": 99.0,
        "broker_order_id": "ord-1",
    }
    event = ol.extract_broker_event(_order("accepted", 6, 99.5))
    result = ol.reconcile(row, event)

    assert result["status"] == "PARTIALLY_FILLED"
    assert result["filled_qty"] == 6
    assert result["changed"] is True


def test_b2_quantity_advance_elevates_to_filled_when_complete():
    """(b) An advance that completes the order is a confirmed fill."""
    row = {
        "id": 1,
        "status": "PARTIALLY_FILLED",
        "qty": 10,
        "filled_qty": 4,
        "avg_fill_price": 99.0,
        "broker_order_id": "ord-1",
    }
    event = ol.extract_broker_event(_order("accepted", 10, 99.5))
    result = ol.reconcile(row, event)

    assert result["status"] == "FILLED"
    assert result["filled_qty"] == 10


def test_b3_canceled_row_accepts_late_fill_quantity_without_status_change():
    """(b) CANCELED 4 + late 6 → CANCELED, filled_qty 6 (no status change)."""
    row = {
        "id": 1,
        "status": "CANCELED",
        "qty": 10,
        "filled_qty": 4,
        "avg_fill_price": 99.0,
        "broker_order_id": "ord-1",
    }
    event = ol.extract_broker_event(_order("partially_filled", 6, 99.5))
    result = ol.reconcile(row, event)

    assert result["status"] == "CANCELED"
    assert result["filled_qty"] == 6
    assert result["changed"] is True


def test_b4_canceled_and_expired_rows_are_repolled_only_within_the_horizon(db):
    """(b) CANCELED / EXPIRED rows younger than 24h are re-polled."""
    recent = _insert_trade(
        db,
        status="CANCELED",
        qty=10,
        oid="ord-recent",
        order_updated_at=(
            datetime.now(timezone.utc) - timedelta(hours=2)
        ).isoformat(),
    )
    old = _insert_trade(
        db,
        status="CANCELED",
        qty=10,
        oid="ord-old",
        order_updated_at=(
            datetime.now(timezone.utc) - timedelta(hours=48)
        ).isoformat(),
    )
    expired_recent = _insert_trade(
        db,
        status="EXPIRED",
        qty=10,
        oid="ord-expired-recent",
        order_updated_at=(
            datetime.now(timezone.utc) - timedelta(hours=1)
        ).isoformat(),
    )
    expired_old = _insert_trade(
        db,
        status="EXPIRED",
        qty=10,
        oid="ord-expired-old",
        order_updated_at=(
            datetime.now(timezone.utc) - timedelta(hours=30)
        ).isoformat(),
    )

    ids = [r["id"] for r in db.get_pending_fill_trades()]
    assert recent in ids
    assert expired_recent in ids
    assert old not in ids
    assert expired_old not in ids


def test_b5_filled_and_rejected_rows_are_never_repolled(db):
    """(b) FILLED / REJECTED are truly final — never re-polled."""
    filled = _insert_trade(
        db,
        status="FILLED",
        qty=10,
        oid="ord-filled",
        order_updated_at=datetime.now(timezone.utc).isoformat(),
    )
    rejected = _insert_trade(
        db,
        status="REJECTED",
        qty=10,
        oid="ord-rejected",
        order_updated_at=datetime.now(timezone.utc).isoformat(),
    )
    ids = [r["id"] for r in db.get_pending_fill_trades()]
    assert filled not in ids
    assert rejected not in ids


def test_b6_poller_applies_late_fill_quantity_to_a_canceled_row(db):
    """(b) End-to-end: a late fill updates the quantity of a CANCELED row."""
    tid = _insert_trade(
        db,
        status="CANCELED",
        qty=10,
        oid="ord-1",
        filled_qty=4,
        avg_fill_price=99.0,
        order_updated_at=datetime.now(timezone.utc).isoformat(),
    )
    broker = _ScriptedBroker(
        states={"ord-1": _order("partially_filled", 6, 99.5)}
    )
    summary = _poll(db, broker)()

    assert summary["updated"] == 1
    row = _trade_row(db, tid)
    assert row["status"] == "CANCELED"
    assert row["filled_qty"] == 6
    assert row["avg_fill_price"] == 99.5


def test_b7_stale_event_on_terminal_row_still_changes_nothing(db):
    """(b) A stale event (no quantity advance) leaves the row untouched."""
    tid = _insert_trade(
        db,
        status="CANCELED",
        qty=10,
        oid="ord-1",
        filled_qty=4,
        order_updated_at=datetime.now(timezone.utc).isoformat(),
    )
    broker = _ScriptedBroker(states={"ord-1": _order("new", 0, None)})
    summary = _poll(db, broker)()

    assert summary["updated"] == 0
    row = _trade_row(db, tid)
    assert row["status"] == "CANCELED"
    assert row["filled_qty"] == 4


# --------------------------------------------------------------------------- #
# (c) Follow-up tranches must go through the ledger
# --------------------------------------------------------------------------- #


def test_c1_tranche_submission_is_persisted_and_reconciled_to_filled(db):
    """(c) A tranche-2 submission lands as SUBMITTED and is reconciled."""
    import pandas as pd
    from src.core.smart_bot import SmartTradingBot

    df = pd.DataFrame(
        {
            "close": [100.0],
            "RSI": [50.0],
            "vwap": [100.0],
            "volume_ratio": [1.2],
        }
    )
    plan = {
        "symbol": "TEST",
        "total_qty": 10,
        "entry_price": 100.0,
        "created_at": datetime.now(timezone.utc),
        "tranches": [
            {"tranche_num": 1, "qty": 4, "trigger": "immediate", "status": "filled"},
            {"tranche_num": 2, "qty": 3, "trigger": "immediate", "status": "pending"},
            {
                "tranche_num": 3,
                "qty": 3,
                "trigger": "hold_above_entry_4h",
                "status": "pending",
            },
        ],
    }
    broker = _TrancheBroker()
    fake = SimpleNamespace(
        db=db,
        trading_client=broker,
        session_id=1,
        _pending_entry_tranches={"TEST": plan},
        get_market_data=lambda symbol: df,
        calculate_indicators=lambda data: data,
    )

    SmartTradingBot._execute_pending_entry_tranches.__get__(fake, type(fake))()

    # Only the tranche whose trigger fired is submitted (never tranche 3).
    assert len(broker.submitted) == 1
    assert float(broker.submitted[0].qty) == 3.0
    # In-memory status is 'submitted' — NEVER 'filled' at submission time.
    assert plan["tranches"][1]["status"] == "submitted"
    assert plan["tranches"][1]["status"] != "filled"

    rows = [
        r for r in db.get_all_trades() if r["broker_order_id"] == "ord-tranche-2"
    ]
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "SUBMITTED"
    assert row["pnl"] is None
    assert "tranche 2" in (row["notes"] or "")

    # The fill poller reconciles the tranche exactly like any other order.
    broker.states["ord-tranche-2"] = _order(
        "filled", 3, 100.0, oid="ord-tranche-2"
    )
    poll = SmartTradingBot.poll_order_fills.__get__(fake, type(fake))
    summary = poll()

    assert summary["updated"] == 1
    final = _trade_row(db, row["id"])
    assert final["status"] == "FILLED"
    assert final["filled_qty"] == 3


def test_c2_tranche_plan_is_not_re_executed_after_submission(db):
    """(c) A submitted tranche is not re-submitted on the next cycle."""
    import pandas as pd
    from src.core.smart_bot import SmartTradingBot

    df = pd.DataFrame(
        {
            "close": [100.0],
            "RSI": [50.0],
            "vwap": [100.0],
            "volume_ratio": [1.2],
        }
    )
    plan = {
        "symbol": "TEST",
        "total_qty": 10,
        "entry_price": 100.0,
        "created_at": datetime.now(timezone.utc),
        "tranches": [
            {"tranche_num": 1, "qty": 4, "trigger": "immediate", "status": "filled"},
            {"tranche_num": 2, "qty": 3, "trigger": "immediate", "status": "pending"},
        ],
    }
    broker = _TrancheBroker()
    fake = SimpleNamespace(
        db=db,
        trading_client=broker,
        session_id=1,
        _pending_entry_tranches={"TEST": plan},
        get_market_data=lambda symbol: df,
        calculate_indicators=lambda data: data,
    )

    bound = SmartTradingBot._execute_pending_entry_tranches.__get__(fake, type(fake))
    bound()
    bound()

    assert len(broker.submitted) == 1
    assert len(db.get_all_trades()) == 1
