"""EXEC-003 SHOULD-FIX #1 follow-up (2026-10-28) — unresolved-event de-duplication.

Two owner-approved narrow fixes on PR #109 (head 46013b6):

  1. EVENT-LOG FLOOD — ``poll_order_fills()`` used to append an
     ``order_events`` row (unresolved = 1) on EVERY analysis loop
     (~10-17 s) for every unresolved order. An unresolved row is now
     written ONLY when the OUTCOME CHANGED for that order: the
     deterministic outcome class (broker-unreachable /
     unrecognized-status:<raw> / reconcile-exception) is compared
     against the order's most recent ``order_events`` row, read from the
     DB so the decision survives a process restart. Repeated identical
     outcomes write NO new row.
  2. NULL-TIMESTAMP LOOSE END — a CANCELED / EXPIRED row whose
     ``order_updated_at`` AND ``created_at`` are both NULL has no usable
     age and must NOT be re-polled (treated as old).

Every test in this file FAILS on 46013b6 and PASSES with the fixes.

Deterministic. Mocked broker responses only — NO Alpaca/network calls and
no order is ever submitted to a real endpoint (TESTING / UNIT_TESTING are
set by conftest before imports; the paper-only guard stays armed).
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

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


def _order(status="filled", filled_qty=10, avg=100.0, oid="ord-1"):
    return SimpleNamespace(
        id=oid,
        status=status,
        filled_qty=filled_qty,
        filled_avg_price=avg,
        updated_at="2026-10-28T14:00:00Z",
    )


def _insert_trade(db, status="SUBMITTED", qty=10, oid="ord-1", **extra):
    data = {
        "session_id": 1,
        "symbol": "TEST",
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
    data.update(extra)
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


def _unreachable_broker():
    return _ScriptedBroker(fail_ids={"ord-1"})


def _unknown_status_broker():
    return _ScriptedBroker(
        states={"ord-1": _order("totally_unknown_status", 0, None)}
    )


# --------------------------------------------------------------------------- #
# (i) N repeated identical unresolved failures → exactly one audit row
# --------------------------------------------------------------------------- #


def test_i1_repeated_identical_unresolved_failures_write_one_row(db):
    """(i) The same unresolved outcome is audited ONCE, not once per loop."""
    tid = _insert_trade(db)
    before = _trade_row(db, tid)

    poll = _poll(db, _unreachable_broker())
    totals = {"polled": 0, "unresolved": 0, "updated": 0}
    for _ in range(25):
        summary = poll()
        for key in totals:
            totals[key] += summary[key]

    # Every loop really did poll the order and really was unresolved.
    assert totals["polled"] == 25
    assert totals["unresolved"] == 25
    assert totals["updated"] == 0

    events = _events(db, tid)
    assert len(events) == 1
    assert events[0]["unresolved"] == 1
    assert events[0]["event_note"] == "broker-unreachable"

    # The trades row is still untouched.
    assert _trade_row(db, tid) == before


def test_i2_repeated_identical_unknown_status_writes_one_row(db):
    """(i) Same rule for an unrecognized broker status."""
    tid = _insert_trade(db)

    poll = _poll(db, _unknown_status_broker())
    for _ in range(10):
        poll()

    events = _events(db, tid)
    assert len(events) == 1
    assert events[0]["unresolved"] == 1
    assert events[0]["event_note"] == "unrecognized-status:totally_unknown_status"
    # The broker's raw status is still preserved on the row.
    assert events[0]["raw_status"] == "totally_unknown_status"


# --------------------------------------------------------------------------- #
# (ii) A change of outcome writes a new row
# --------------------------------------------------------------------------- #


def test_ii1_outcome_change_network_to_unknown_status_writes_new_row(db):
    """(ii) network failure → unknown status is a CHANGE → new audit row."""
    tid = _insert_trade(db)

    broker = _unreachable_broker()
    poll = _poll(db, broker)
    poll()
    assert len(_events(db, tid)) == 1
    assert _events(db, tid)[0]["event_note"] == "broker-unreachable"

    # The broker now answers with an unrecognized status.
    broker.fail_ids.clear()
    broker.states["ord-1"] = _order("totally_unknown_status", 0, None)
    poll()

    events = _events(db, tid)
    assert len(events) == 2
    assert events[0]["event_note"] == "broker-unreachable"
    assert events[1]["event_note"] == "unrecognized-status:totally_unknown_status"
    assert events[1]["unresolved"] == 1


def test_ii2_outcome_change_back_to_a_previous_class_also_writes(db):
    """(ii) The comparison is against the MOST RECENT row, not any row."""
    tid = _insert_trade(db)

    broker = _unreachable_broker()
    poll = _poll(db, broker)
    poll()  # broker-unreachable
    broker.fail_ids.clear()
    broker.states["ord-1"] = _order("weird_status", 0, None)
    poll()  # unrecognized-status:weird_status
    assert len(_events(db, tid)) == 2

    broker.fail_ids.add("ord-1")
    poll()  # back to broker-unreachable → CHANGE → new row

    events = _events(db, tid)
    assert len(events) == 3
    assert events[2]["event_note"] == "broker-unreachable"


def test_ii3_reconcile_exception_class_is_recorded_and_deduplicated(db, monkeypatch):
    """(ii) reconcile-exception is its own deterministic class."""
    from src.core import smart_bot as sb_module

    tid = _insert_trade(db)

    def _boom(*args, **kwargs):
        raise RuntimeError("reconcile exploded")

    monkeypatch.setattr(sb_module.order_lifecycle, "reconcile", _boom)

    poll = _poll(db, _unreachable_broker())
    poll()
    poll()
    poll()

    events = _events(db, tid)
    assert len(events) == 1
    assert events[0]["event_note"] == "reconcile-exception"
    assert events[0]["unresolved"] == 1


# --------------------------------------------------------------------------- #
# (iii) No heartbeat rows — a prolonged unresolved state stays at one row
# --------------------------------------------------------------------------- #


def test_iii1_prolonged_unresolved_state_writes_no_extra_rows(db):
    """(iii) No heartbeat: many polls of the same outcome → still one row."""
    tid = _insert_trade(db)

    poll = _poll(db, _unreachable_broker())
    for _ in range(100):
        poll()

    assert len(_events(db, tid)) == 1


def test_iii2_after_a_change_repeats_add_nothing(db):
    """(iii) The change row is written once; repeats of it add nothing."""
    tid = _insert_trade(db)

    broker = _unreachable_broker()
    poll = _poll(db, broker)
    poll()
    broker.fail_ids.clear()
    broker.states["ord-1"] = _order("weird_status", 0, None)
    poll()
    assert len(_events(db, tid)) == 2

    for _ in range(50):
        poll()
    assert len(_events(db, tid)) == 2


# --------------------------------------------------------------------------- #
# (iv) A restart does not re-write the same outcome
# --------------------------------------------------------------------------- #


def test_iv1_restart_does_not_rewrite_the_same_outcome(db, monkeypatch):
    """(iv) The decision is DB-backed, so a new instance does not re-audit."""
    tid = _insert_trade(db)

    poll = _poll(db, _unreachable_broker())
    poll()
    assert len(_events(db, tid)) == 1

    # "Restart": a brand-new SQLiteDB + bot instance on the SAME database.
    fresh_db = sqlite_db_module.SQLiteDB()
    fresh_db._init_schema()
    poll_after_restart = _poll(fresh_db, _unreachable_broker())
    poll_after_restart()

    events = _events(db, tid)
    assert len(events) == 1
    assert events[0]["event_note"] == "broker-unreachable"


def test_iv2_restart_still_writes_when_the_outcome_changed(db):
    """(iv) After a restart a genuinely different outcome IS audited."""
    tid = _insert_trade(db)

    poll = _poll(db, _unreachable_broker())
    poll()

    fresh_db = sqlite_db_module.SQLiteDB()
    fresh_db._init_schema()
    broker = _unknown_status_broker()
    poll_after_restart = _poll(fresh_db, broker)
    poll_after_restart()

    events = _events(db, tid)
    assert len(events) == 2
    assert events[1]["event_note"] == "unrecognized-status:totally_unknown_status"


# --------------------------------------------------------------------------- #
# (v) NULL timestamps on a terminal row → not re-polled
# --------------------------------------------------------------------------- #


def test_v1_canceled_row_with_null_timestamps_is_not_repolled(db):
    """(v) CANCELED + NULL order_updated_at + NULL created_at → not polled."""
    tid = _insert_trade(
        db,
        status="CANCELED",
        oid="ord-1",
        order_updated_at=None,
        created_at=None,
    )
    row = _trade_row(db, tid)
    assert row["order_updated_at"] is None
    assert row["created_at"] is None

    ids = [r["id"] for r in db.get_pending_fill_trades()]
    assert tid not in ids


def test_v2_expired_row_with_null_timestamps_is_not_repolled(db):
    """(v) Same rule for EXPIRED."""
    tid = _insert_trade(
        db,
        status="EXPIRED",
        oid="ord-1",
        order_updated_at=None,
        created_at=None,
    )
    ids = [r["id"] for r in db.get_pending_fill_trades()]
    assert tid not in ids


def test_v3_non_terminal_rows_with_null_timestamps_are_still_polled(db):
    """(v) The horizon must not affect non-terminal rows."""
    tid = _insert_trade(
        db,
        status="SUBMITTED",
        oid="ord-1",
        order_updated_at=None,
        created_at=None,
    )
    ids = [r["id"] for r in db.get_pending_fill_trades()]
    assert tid in ids


def test_v4_recent_terminal_row_is_still_repolled(db):
    """(v) The 24h horizon still re-polls recent CANCELED rows."""
    tid = _insert_trade(
        db,
        status="CANCELED",
        oid="ord-1",
        order_updated_at=(
            datetime.now(timezone.utc) - timedelta(hours=2)
        ).isoformat(),
    )
    ids = [r["id"] for r in db.get_pending_fill_trades()]
    assert tid in ids


# --------------------------------------------------------------------------- #
# Resolved transitions are still recorded exactly as before
# --------------------------------------------------------------------------- #


def test_resolved_transitions_are_still_recorded(db):
    """A resolved poll still writes its own (unresolved = 0) audit row."""
    tid = _insert_trade(db)

    broker = _ScriptedBroker(states={"ord-1": _order("filled", 10, 100.0)})
    poll = _poll(db, broker)
    summary = poll()

    assert summary["updated"] == 1
    assert summary["unresolved"] == 0

    events = _events(db, tid)
    assert len(events) == 1
    assert events[0]["unresolved"] == 0
    assert events[0]["canonical_status"] == "FILLED"

    row = _trade_row(db, tid)
    assert row["status"] == "FILLED"
    assert row["filled_qty"] == 10


def test_resolved_then_unresolved_writes_both_rows(db):
    """Resolved → unresolved again is a CHANGE → both rows are kept."""
    tid = _insert_trade(db)

    broker = _unreachable_broker()
    poll = _poll(db, broker)
    poll()  # unresolved: broker-unreachable
    assert len(_events(db, tid)) == 1

    # The broker answers: the transition is RESOLVED and is recorded.
    broker.fail_ids.clear()
    broker.states["ord-1"] = _order("accepted", 0, None)
    poll()
    events = _events(db, tid)
    assert len(events) == 2
    assert events[1]["unresolved"] == 0
    assert events[1]["canonical_status"] == "ACCEPTED"
    assert _trade_row(db, tid)["status"] == "ACCEPTED"

    # The broker goes unreachable again: the outcome CHANGED (the most
    # recent row was a resolved one) → a new unresolved row is written.
    broker.fail_ids.add("ord-1")
    poll()

    events = _events(db, tid)
    assert len(events) == 3
    assert events[2]["event_note"] == "broker-unreachable"
    assert events[2]["unresolved"] == 1
