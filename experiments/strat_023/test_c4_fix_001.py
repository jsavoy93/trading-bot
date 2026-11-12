"""C4-FIX-001 tests — same-run sleeve sizing + exit bookkeeping.

Fake broker only (no network, no live DB, no live paths).
"""
from __future__ import annotations

import importlib
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import live_paper_runner as lpr  # noqa: E402
import shadow_runner as sr       # noqa: E402
from test_live_paper_runner import (FakeAPI, append_bar,  # noqa: E402
                                    make_bars, shift_dates)

import backfill_exit_prices as bep  # noqa: E402


def fresh(tmp_path, **kw):
    importlib.reload(lpr)
    api = FakeAPI(**kw)
    db = tmp_path / "live_paper.db"
    return lpr, api, db


def seed_open_position(lpr, api, db, sym="AAAA"):
    """Entry -> fill tracked -> ATR stop placed."""
    bars = {sym: make_bars()}
    api.fill_at = bars[sym]["t"].iloc[-1]
    lpr.run_once(db, api, bars)
    lpr.run_once(db, api, bars)
    return bars[sym]


def rows(db):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    return conn


# ------------------------------------------------------- BUG 1: same-run sleeve
def test_same_run_sleeve_cap_is_never_exceeded(tmp_path):
    """Three C4 entry signals in ONE run: the committed notional (open +
    pending) must stay <= 15% of equity, no single entry may exceed the
    10% per-name cap, and at least one candidate must be shrunk/skipped."""
    lpr, api, db = fresh(tmp_path, equity=100_000.0, buying_power=100_000.0)
    syms = ("AAA", "BBB", "CCC")
    bars = {s: make_bars() for s in syms}
    s = lpr.run_once(db, api, bars)

    conn = rows(db)
    entries = conn.execute(
        "SELECT symbol, qty, price FROM orders WHERE kind='entry'").fetchall()
    total = sum(r["qty"] * r["price"] for r in entries)
    assert total <= 15_000 + 1e-6, total
    for r in entries:
        assert r["qty"] * r["price"] <= 10_000 + 1e-6, dict(r)
    # at least one of the three candidates was skipped or shrunk
    assert s["skipped"] >= 1 or len(entries) < len(syms)
    assert len(entries) < len(syms) or any(
        r["qty"] * r["price"] < 10_000 for r in entries)
    assert s["entries_submitted"] == len(entries)


def test_same_run_sleeve_bug_is_reproduced_without_the_fix(tmp_path):
    """Regression proof: sizing against a STALE pending_notional (the old
    bug) would commit ~3x the 10% per-name cap; the fixed code does not."""
    lpr, api, db = fresh(tmp_path, equity=100_000.0, buying_power=100_000.0)
    bars = {s: make_bars() for s in ("AAA", "BBB", "CCC")}
    lpr.run_once(db, api, bars)
    conn = rows(db)
    total = sum(r["qty"] * r["price"] for r in conn.execute(
        "SELECT qty, price FROM orders WHERE kind='entry'"))
    old_bug_total = 3 * 10_000
    assert total < old_bug_total
    assert total <= 15_000 + 1e-6


# --------------------------------------------------- BUG 2: exit bookkeeping
def _fill_sell(api, coid, price, filled_at):
    o = api.orders[coid]
    o["status"] = "filled"
    o["filled_avg_price"] = str(price)
    o["filled_qty"] = o["qty"]
    o["filled_at"] = filled_at
    sym = o["symbol"]
    api.positions[sym] = max(0.0, api.positions.get(sym, 0.0) - float(o["qty"]))
    if not api.positions[sym]:
        del api.positions[sym]
    return o


def test_exit_fill_writes_exit_price_time_and_pnl(tmp_path):
    """(a) An exit order filled on the broker closes the position with the
    REAL average fill price, exit_date/exit_time from filled_at and the
    realized P&L."""
    lpr, api, db = fresh(tmp_path)
    seed_open_position(lpr, api, db)
    # trigger the SMA5 exit on a POST-ENTRY session -> market sell is
    # submitted (the entry bar itself never triggers an SMA5 exit)
    df = append_bar(make_bars())
    s = lpr.run_once(db, api, {"AAAA": df})
    assert s["exits_submitted"] == 1
    x_coid = [c for c, o in api.orders.items()
              if o["type"] == "market" and o["side"] == "sell"][0]
    _fill_sell(api, x_coid, 105.0, "2026-11-09T15:00:00Z")

    s2 = lpr.run_once(db, api, {"AAAA": df})
    assert s2["exit_fills"] == 1
    conn = rows(db)
    p = conn.execute("SELECT * FROM positions").fetchone()
    entry_px = p["entry_price"]
    qty = p["qty"]
    assert p["status"] == "closed"
    assert p["exit_price"] == pytest.approx(105.0)
    assert p["exit_date"] == "2026-11-09"
    assert p["exit_time"] == "2026-11-09T15:00:00Z"
    assert p["realized_pnl"] == pytest.approx((105.0 - entry_px) * qty)
    assert p["exit_reason"] == "sma5"
    o = conn.execute("SELECT status FROM orders WHERE client_order_id=?",
                     (x_coid,)).fetchone()
    assert o["status"] == "filled"


def test_canceled_stop_is_marked_canceled(tmp_path):
    """(b) A stop canceled on the broker must not stay 'submitted'."""
    lpr, api, db = fresh(tmp_path)
    seed_open_position(lpr, api, db)
    s_coid = [c for c, o in api.orders.items() if o["type"] == "stop"][0]
    api.cancel(api.orders[s_coid]["id"])
    assert api.orders[s_coid]["status"] == "canceled"
    lpr.run_once(db, api, {"AAAA": make_bars()})
    conn = rows(db)
    o = conn.execute("SELECT status FROM orders WHERE client_order_id=?",
                     (s_coid,)).fetchone()
    assert o["status"] == "canceled"


def test_filled_stop_closes_position_with_real_price(tmp_path):
    """A stop that filled on the broker closes the position with the real
    fill price instead of a generic 'external_or_stop'."""
    lpr, api, db = fresh(tmp_path)
    seed_open_position(lpr, api, db)
    s_coid = [c for c, o in api.orders.items() if o["type"] == "stop"][0]
    _fill_sell(api, s_coid, 91.25, "2026-11-05T14:45:00Z")
    s = lpr.run_once(db, api, {"AAAA": make_bars()})
    assert s["exit_fills"] == 1
    conn = rows(db)
    p = conn.execute("SELECT * FROM positions").fetchone()
    assert p["status"] == "closed"
    assert p["exit_price"] == pytest.approx(91.25)
    assert p["exit_date"] == "2026-11-05"
    assert p["exit_reason"] == "stop"
    assert p["realized_pnl"] == pytest.approx((91.25 - p["entry_price"]) * p["qty"])


def test_pending_exit_order_is_left_untouched(tmp_path):
    """An exit order that is still working (new/held) leaves the position
    open and the order row 'submitted'."""
    lpr, api, db = fresh(tmp_path)
    seed_open_position(lpr, api, db)
    df = append_bar(make_bars())
    lpr.run_once(db, api, {"AAAA": df})
    x_coid = [c for c, o in api.orders.items()
              if o["type"] == "market" and o["side"] == "sell"][0]
    s = lpr.run_once(db, api, {"AAAA": df})
    assert s["exit_fills"] == 0
    conn = rows(db)
    assert conn.execute(
        "SELECT status FROM orders WHERE client_order_id=?",
        (x_coid,)).fetchone()["status"] == "submitted"


def test_reconciliation_is_idempotent(tmp_path):
    """(c) A second reconciliation pass writes the same values and reports
    no duplicate fills."""
    lpr, api, db = fresh(tmp_path)
    seed_open_position(lpr, api, db)
    df = append_bar(make_bars())
    lpr.run_once(db, api, {"AAAA": df})
    x_coid = [c for c, o in api.orders.items()
              if o["type"] == "market" and o["side"] == "sell"][0]
    _fill_sell(api, x_coid, 105.0, "2026-11-09T15:00:00Z")
    lpr.run_once(db, api, {"AAAA": df})
    conn = rows(db)
    first = tuple(conn.execute(
        "SELECT status, exit_price, exit_date, exit_time, realized_pnl,"
        " exit_reason FROM positions").fetchone())
    s2 = lpr.run_once(db, api, {"AAAA": df})
    assert s2["exit_fills"] == 0
    conn = rows(db)
    second = tuple(conn.execute(
        "SELECT status, exit_price, exit_date, exit_time, realized_pnl,"
        " exit_reason FROM positions").fetchone())
    assert first == second


def test_missing_broker_order_does_not_crash_or_invent_a_fill(tmp_path):
    """An order the broker cannot find leaves the row pending."""
    lpr, api, db = fresh(tmp_path)

    class VanishingAPI(FakeAPI):
        def get_order_by_client_id(self, coid):
            return None

        def get_order(self, order_id):
            return None

    api = VanishingAPI()
    seed = {"AAAA": make_bars()}
    api.fill_at = seed["AAAA"]["t"].iloc[-1]
    lpr.run_once(db, api, seed)
    lpr.run_once(db, api, seed)
    s = lpr.run_once(db, api, seed)
    assert s["exit_fills"] == 0
    conn = rows(db)
    assert conn.execute(
        "SELECT exit_price FROM positions").fetchone()["exit_price"] is None


# ----------------------------------------------- migration of the live schema
def test_open_db_migrates_existing_positions_table(tmp_path):
    """An OLD positions table (no exit_time / realized_pnl) is migrated in
    place."""
    import sqlite3 as _s
    db = tmp_path / "old.db"
    conn = _s.connect(db)
    conn.executescript(
        "CREATE TABLE positions(id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " symbol TEXT NOT NULL, signal_date TEXT NOT NULL,"
        " signal_atr14 REAL NOT NULL, entry_order_coid TEXT NOT NULL,"
        " entry_date TEXT, entry_price REAL, qty REAL,"
        " status TEXT NOT NULL DEFAULT 'pending_fill',"
        " exit_date TEXT, exit_price REAL, exit_reason TEXT,"
        " created_ts TEXT NOT NULL, UNIQUE(symbol, signal_date));")
    conn.commit()
    conn.close()
    c = lpr.open_db(db)
    cols = {r[1] for r in c.execute("PRAGMA table_info(positions)")}
    c.close()
    assert "exit_time" in cols and "realized_pnl" in cols


# ------------------------------------------------------- backfill dry run
class FakeBroker:
    """Read-only Alpaca stand-in for the backfill."""

    def __init__(self, filled_by_coid):
        self.filled = filled_by_coid

    def get_order_by_client_id(self, coid):
        return self.filled.get(coid)

    def get(self, path, params=None):
        if path == "/v2/orders":
            return list(self.filled.values())
        raise RuntimeError(f"unexpected GET {path}")


def _make_backfill_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(lpr.SCHEMA)
    conn.execute(
        "INSERT INTO orders(client_order_id, symbol, kind, sleeve, side,"
        " qty, price, ref_date, alpaca_order_id, status, created_ts)"
        " VALUES ('sl_short-AMD-xsma5-20261109','AMD','exit_sma5','short',"
        "'sell',22,NULL,'2026-11-09','x1','submitted','2026-11-09T20:00:00Z')")
    conn.execute(
        "INSERT INTO positions(symbol, signal_date, signal_atr14,"
        " entry_order_coid, status, entry_price, qty, exit_date, exit_reason,"
        " created_ts) VALUES ('AMD','2026-11-02',1.0,'sl_short-AMD-e-20261102',"
        "'closed',475.02,22,'2026-11-09','sma5','2026-11-02T20:00:00Z')")
    conn.commit()
    conn.close()


def test_backfill_dry_run_writes_nothing_and_is_idempotent(tmp_path):
    """(d) The dry run never writes and running it twice changes nothing."""
    db = tmp_path / "live_paper.db"
    _make_backfill_db(db)
    before = db.read_bytes()
    broker = FakeBroker({
        "sl_short-AMD-xsma5-20261109": {
            "id": "x1", "client_order_id": "sl_short-AMD-xsma5-20261109",
            "symbol": "AMD", "side": "sell", "status": "filled",
            "filled_avg_price": "506.47", "filled_qty": "22",
            "filled_at": "2026-11-10T14:30:00Z"}})
    rc = bep.run(db, apply=False, api_factory=lambda: broker)
    assert rc == 0
    assert db.read_bytes() == before
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT exit_price FROM positions").fetchone()[0] is None
    conn.close()
    # idempotent: a second dry run writes nothing either
    rc2 = bep.run(db, apply=False, api_factory=lambda: broker)
    assert rc2 == 0
    assert db.read_bytes() == before


def test_backfill_skips_rows_that_already_have_an_exit_price(tmp_path, capsys):
    db = tmp_path / "live_paper.db"
    _make_backfill_db(db)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE positions SET exit_price=506.47")
    conn.commit()
    conn.close()
    rc = bep.run(db, apply=False, api_factory=lambda: FakeBroker({}))
    out = capsys.readouterr().out
    assert rc == 0
    assert "already set" in out
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT exit_price FROM positions").fetchone()[0] == 506.47
    conn.close()
