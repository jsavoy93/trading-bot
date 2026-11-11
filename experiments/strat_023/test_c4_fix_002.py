"""C4-FIX-002 tests — backfill counter, required --db, canceled/expired/
rejected ENTRY handling, one-off TEAM cleanup command, terminal exit
statuses and P&L from filled_qty.

Fake broker only (no network, no live DB, no live paths).
"""
from __future__ import annotations

import importlib
import sqlite3
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import live_paper_runner as lpr  # noqa: E402
import shadow_runner as sr       # noqa: E402
from test_live_paper_runner import FakeAPI, make_bars  # noqa: E402

import backfill_exit_prices as bep          # noqa: E402
import cleanup_canceled_entries as cce      # noqa: E402


def fresh(tmp_path, **kw):
    importlib.reload(lpr)
    api = FakeAPI(**kw)
    db = tmp_path / "live_paper.db"
    return lpr, api, db


def rows(db):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    return conn


def seed_closed_missing_exit(db, sym="ZZZZ", entry=100.0, qty=10.0):
    """A closed position whose exit_price was never recorded."""
    conn = lpr.open_db(db)
    conn.execute(
        "INSERT INTO positions(symbol, signal_date, signal_atr14,"
        " entry_order_coid, entry_date, entry_price, qty, status, exit_date,"
        " exit_reason, created_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (sym, "2026-11-01", 2.0, f"sl_short-{sym}-e-20261101", "2026-11-01",
         entry, qty, "closed", "2026-11-05", "sma5", "2026-11-01T00:00:00Z"))
    conn.execute(
        "INSERT INTO orders(client_order_id, symbol, kind, side, qty, price,"
        " ref_date, alpaca_order_id, status, created_ts)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (f"sl_short-{sym}-xsma5-20261105", sym, "exit_sma5", "sell", qty,
         None, "2026-11-05", "ord-1", "submitted", "2026-11-05T00:00:00Z"))
    conn.commit()
    conn.close()


class FakeBroker:
    """Minimal read-only broker stub: client_order_id -> order dict."""

    def __init__(self, orders=None):
        self.orders = orders or {}

    def get_order_by_client_id(self, coid):
        o = self.orders.get(coid)
        return dict(o) if o else None

    def get_order(self, order_id):
        for o in self.orders.values():
            if o.get("id") == order_id:
                return dict(o)
        return None

    def get(self, path, params=None):
        raise RuntimeError(f"unexpected GET {path}")


# =========================================================== CHANGE 1
def test_backfill_dry_run_counts_would_be_updates(tmp_path, capsys):
    """A dry run must report how many rows it WOULD update (the counter
    used to live inside `if apply:` and always printed 0)."""
    db = tmp_path / "live_paper.db"
    seed_closed_missing_exit(db)
    broker = FakeBroker({"sl_short-ZZZZ-xsma5-20261105": {
        "id": "ord-1", "client_order_id": "sl_short-ZZZZ-xsma5-20261105",
        "symbol": "ZZZZ", "status": "filled", "filled_avg_price": "110.0",
        "filled_qty": "10", "filled_at": "2026-11-05T15:00:00Z"}})
    rc = bep.run(db, apply=False, api_factory=lambda: broker)
    out = capsys.readouterr().out
    assert rc == 0
    assert "DRY-RUN: 1 position(s) WOULD be updated" in out, out
    # and nothing was written
    conn = rows(db)
    p = conn.execute("SELECT * FROM positions").fetchone()
    assert p["exit_price"] is None
    o = conn.execute("SELECT status FROM orders").fetchone()
    assert o["status"] == "submitted"


def test_backfill_apply_counts_actual_updates(tmp_path, capsys):
    db = tmp_path / "live_paper.db"
    seed_closed_missing_exit(db)
    broker = FakeBroker({"sl_short-ZZZZ-xsma5-20261105": {
        "id": "ord-1", "client_order_id": "sl_short-ZZZZ-xsma5-20261105",
        "symbol": "ZZZZ", "status": "filled", "filled_avg_price": "110.0",
        "filled_qty": "10", "filled_at": "2026-11-05T15:00:00Z"}})
    rc = bep.run(db, apply=True, api_factory=lambda: broker)
    out = capsys.readouterr().out
    assert rc == 0
    assert "APPLIED: 1 position(s) updated" in out, out
    conn = rows(db)
    p = conn.execute("SELECT * FROM positions").fetchone()
    assert p["exit_price"] == 110.0
    assert p["realized_pnl"] == pytest.approx(100.0)


# =========================================================== CHANGE 2
def test_backfill_db_argument_is_required(capsys):
    with pytest.raises(SystemExit) as e:
        bep.main([])
    assert e.value.code == 2
    err = capsys.readouterr().err
    assert "--db" in err


def test_backfill_db_argument_has_no_default():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    with pytest.raises(SystemExit):
        ap.parse_args([])


def test_cleanup_db_argument_is_required(capsys):
    with pytest.raises(SystemExit) as e:
        cce.main([])
    assert e.value.code == 2
    assert "--db" in capsys.readouterr().err


# =========================================================== CHANGE 3
def _cancel_entry(api, coid_, filled_qty=None):
    """Make an entry order terminal-not-filled on the broker."""
    o = api.orders[coid_]
    o["status"] = "canceled"
    o.pop("filled_avg_price", None)
    o.pop("filled_at", None)
    o["filled_qty"] = "0" if filled_qty is None else str(filled_qty)
    if filled_qty is None:
        api.positions.pop(o["symbol"], None)
    else:
        api.positions[o["symbol"]] = float(filled_qty)
    return o


def test_canceled_entry_with_zero_fill_closes_position_with_no_pnl(tmp_path):
    """A canceled entry order with NO fill closes the position row as
    canceled: no exit_price, no realized P&L."""
    lpr, api, db = fresh(tmp_path, equity=100_000.0, buying_power=100_000.0)
    bars = {"AAAA": make_bars()}
    lpr.run_once(db, api, bars)
    conn = rows(db)
    p = conn.execute("SELECT * FROM positions").fetchone()
    assert p["status"] == "pending_fill"
    e_coid = p["entry_order_coid"]
    _cancel_entry(api, e_coid)

    s = lpr.run_once(db, api, bars)
    assert s["entries_canceled"] == 1
    conn = rows(db)
    p = conn.execute("SELECT * FROM positions").fetchone()
    assert p["status"] == "closed"
    assert p["exit_reason"] == "entry_canceled"
    assert p["exit_price"] is None
    assert p["realized_pnl"] is None
    o = conn.execute("SELECT status FROM orders WHERE client_order_id=?",
                     (e_coid,)).fetchone()
    assert o["status"] == "canceled"


def test_canceled_entry_frees_slot_and_sleeve_notional(tmp_path):
    """The canceled entry no longer counts against MAX_POSITIONS or the
    sleeve notional: a later candidate can be entered."""
    lpr, api, db = fresh(tmp_path, equity=100_000.0, buying_power=100_000.0)
    syms = ("AAAA", "BBBB", "CCCC")
    bars = {s: make_bars() for s in syms}
    lpr.run_once(db, api, bars)
    conn = rows(db)
    pend = conn.execute(
        "SELECT * FROM positions WHERE status='pending_fill'").fetchall()
    assert len(pend) >= 1
    # the first candidate was skipped in run 1 (the sleeve was full)
    assert conn.execute(
        "SELECT COUNT(*) FROM orders WHERE kind='entry'").fetchone()[0] < 3
    # cancel the FIRST (biggest) entry
    big = min(pend, key=lambda r: r["id"])
    e_coid = big["entry_order_coid"]
    _cancel_entry(api, e_coid)

    s2 = lpr.run_once(db, api, bars)
    assert s2["entries_canceled"] == 1
    conn = rows(db)
    # the canceled row is closed and its orders row is terminal
    p = conn.execute("SELECT * FROM positions WHERE id=?", (big["id"],)).fetchone()
    assert p["status"] == "closed" and p["exit_reason"] == "entry_canceled"
    o = conn.execute("SELECT status FROM orders WHERE client_order_id=?",
                     (e_coid,)).fetchone()
    assert o["status"] == "canceled"
    # the freed sleeve let the skipped candidate in
    assert s2["entries_submitted"] == 1
    # the runner's own sleeve accounting stays inside the 15% cap:
    # open notional + still-pending entry notional
    sleeve_open = conn.execute(
        "SELECT COALESCE(SUM(qty*entry_price),0) FROM positions"
        " WHERE status='open'").fetchone()[0]
    pending = conn.execute(
        "SELECT COALESCE(SUM(qty*price),0) FROM orders WHERE kind='entry'"
        " AND status='submitted'").fetchone()[0]
    assert sleeve_open + pending <= 15_000 + 1e-6, (sleeve_open, pending)


def test_partially_filled_canceled_entry_keeps_filled_shares_only(tmp_path):
    """A canceled entry with a PARTIAL fill keeps the filled shares as an
    open position and places the normal 3xATR stop for that quantity."""
    lpr, api, db = fresh(tmp_path, equity=100_000.0, buying_power=100_000.0)
    sym = "AAAA"
    bars = {sym: make_bars()}
    lpr.run_once(db, api, bars)
    conn = rows(db)
    p = conn.execute("SELECT * FROM positions").fetchone()
    e_coid = p["entry_order_coid"]
    full_qty = float(api.orders[e_coid]["qty"])
    _cancel_entry(api, e_coid, filled_qty=full_qty / 2)
    api.orders[e_coid]["filled_avg_price"] = "100.0"
    api.orders[e_coid]["filled_at"] = "2026-10-30T14:30:00Z"

    s = lpr.run_once(db, api, bars)
    assert s["fills_tracked"] == 1
    assert s["stops_placed"] == 1
    conn = rows(db)
    p = conn.execute("SELECT * FROM positions").fetchone()
    assert p["status"] == "open"
    assert p["qty"] == pytest.approx(full_qty / 2)
    assert p["entry_price"] == pytest.approx(100.0)
    assert p["entry_date"] == "2026-10-30"
    # the protective stop covers the FILLED quantity only
    stop = conn.execute(
        "SELECT * FROM orders WHERE kind='stop'").fetchone()
    assert stop is not None
    assert float(stop["qty"]) == pytest.approx(full_qty / 2)
    o = conn.execute("SELECT status FROM orders WHERE client_order_id=?",
                     (e_coid,)).fetchone()
    assert o["status"] == "partially_filled"


def test_rejected_entry_is_treated_as_terminal(tmp_path):
    """'rejected' (and 'expired') entry orders are terminal too."""
    lpr, api, db = fresh(tmp_path, equity=100_000.0, buying_power=100_000.0)
    bars = {"AAAA": make_bars()}
    lpr.run_once(db, api, bars)
    conn = rows(db)
    e_coid = conn.execute(
        "SELECT entry_order_coid FROM positions").fetchone()[0]
    for status in ("rejected", "expired"):
        api.orders[e_coid]["status"] = status
        api.orders[e_coid]["filled_qty"] = "0"
        api.positions.pop("AAAA", None)
        s = lpr.run_once(db, api, bars)
        assert s["entries_canceled"] == 1
        conn = rows(db)
        p = conn.execute("SELECT * FROM positions").fetchone()
        assert p["status"] == "closed"
        assert p["exit_reason"] == "entry_canceled"
        o = conn.execute("SELECT status FROM orders WHERE client_order_id=?",
                         (e_coid,)).fetchone()
        assert o["status"] == status
        # reset for the next iteration
        conn.execute("UPDATE positions SET status='pending_fill',"
                     " exit_reason=NULL, exit_date=NULL WHERE id=?",
                     (p["id"],))
        conn.commit()


# =========================================================== CHANGE 4
def _seed_pending_entry(db, sym="TEAM", coid_="sl_short-TEAM-e-20261110"):
    conn = lpr.open_db(db)
    conn.execute(
        "INSERT INTO orders(client_order_id, symbol, kind, side, qty, price,"
        " ref_date, alpaca_order_id, status, created_ts)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (coid_, sym, "entry", "buy", 10.0, 100.0, "2026-11-10", "ord-9",
         "submitted", "2026-11-10T00:00:00Z"))
    conn.execute(
        "INSERT INTO positions(symbol, signal_date, signal_atr14,"
        " entry_order_coid, status, created_ts) VALUES (?,?,?,?,?,?)",
        (sym, "2026-11-10", 2.0, coid_, "pending_fill",
         "2026-11-10T00:00:00Z"))
    conn.commit()
    conn.close()


def test_cleanup_dry_run_writes_nothing(tmp_path, capsys):
    db = tmp_path / "live_paper.db"
    _seed_pending_entry(db)
    broker = FakeBroker({"sl_short-TEAM-e-20261110": {
        "id": "ord-9", "client_order_id": "sl_short-TEAM-e-20261110",
        "symbol": "TEAM", "status": "canceled", "filled_qty": "0",
        "updated_at": "2026-11-10T20:00:00Z"}})
    rc = cce.run(db, apply=False, api_factory=lambda: broker)
    out = capsys.readouterr().out
    assert rc == 0
    assert "WOULD close as canceled" in out, out
    assert "DRY-RUN: 1 position(s) WOULD be closed as canceled" in out, out
    conn = rows(db)
    p = conn.execute("SELECT * FROM positions").fetchone()
    assert p["status"] == "pending_fill"
    o = conn.execute("SELECT status FROM orders").fetchone()
    assert o["status"] == "submitted"


def test_cleanup_apply_closes_canceled_entry_and_is_idempotent(tmp_path,
                                                               capsys):
    db = tmp_path / "live_paper.db"
    _seed_pending_entry(db)
    broker = FakeBroker({"sl_short-TEAM-e-20261110": {
        "id": "ord-9", "client_order_id": "sl_short-TEAM-e-20261110",
        "symbol": "TEAM", "status": "canceled", "filled_qty": "0",
        "updated_at": "2026-11-10T20:00:00Z"}})
    rc = cce.run(db, apply=True, api_factory=lambda: broker)
    out = capsys.readouterr().out
    assert rc == 0
    assert "APPLIED: 1 position(s) closed as canceled" in out, out
    conn = rows(db)
    p = conn.execute("SELECT * FROM positions").fetchone()
    assert p["status"] == "closed"
    assert p["exit_reason"] == "entry_canceled"
    assert p["exit_price"] is None
    assert p["realized_pnl"] is None
    o = conn.execute("SELECT status FROM orders").fetchone()
    assert o["status"] == "canceled"

    # idempotent: a second run changes nothing
    rc2 = cce.run(db, apply=True, api_factory=lambda: broker)
    out2 = capsys.readouterr().out
    assert rc2 == 0
    assert "nothing to do" in out2, out2
    conn = rows(db)
    p2 = conn.execute("SELECT * FROM positions").fetchone()
    assert p2["status"] == "closed"


def test_cleanup_leaves_unknown_order_untouched(tmp_path, capsys):
    """An order that cannot be found on the broker is never 'invented'."""
    db = tmp_path / "live_paper.db"
    _seed_pending_entry(db)
    broker = FakeBroker({})
    rc = cce.run(db, apply=True, api_factory=lambda: broker)
    out = capsys.readouterr().out
    assert rc == 0
    assert "NOT FOUND ON THE BROKER" in out, out
    conn = rows(db)
    p = conn.execute("SELECT * FROM positions").fetchone()
    assert p["status"] == "pending_fill"


def test_cleanup_ignores_partially_filled_orders(tmp_path, capsys):
    db = tmp_path / "live_paper.db"
    _seed_pending_entry(db)
    broker = FakeBroker({"sl_short-TEAM-e-20261110": {
        "id": "ord-9", "client_order_id": "sl_short-TEAM-e-20261110",
        "symbol": "TEAM", "status": "canceled", "filled_qty": "5",
        "updated_at": "2026-11-10T20:00:00Z"}})
    cce.run(db, apply=True, api_factory=lambda: broker)
    out = capsys.readouterr().out
    assert "partial fill, left untouched" in out, out
    conn = rows(db)
    p = conn.execute("SELECT * FROM positions").fetchone()
    assert p["status"] == "pending_fill"


def test_cleanup_default_is_dry_run(tmp_path, capsys):
    """Without --apply the command must write NOTHING."""
    db = tmp_path / "live_paper.db"
    _seed_pending_entry(db)
    broker = FakeBroker({"sl_short-TEAM-e-20261110": {
        "id": "ord-9", "client_order_id": "sl_short-TEAM-e-20261110",
        "symbol": "TEAM", "status": "canceled", "filled_qty": "0",
        "updated_at": "2026-11-10T20:00:00Z"}})
    cce.main(["--db", str(db)])       # no --apply
    out = capsys.readouterr().out
    assert "DRY-RUN" in out, out
    conn = rows(db)
    p = conn.execute("SELECT * FROM positions").fetchone()
    assert p["status"] == "pending_fill"


# =========================================================== CHANGE 5
def _seed_open_with_exit_order(db, sym="AAAA", kind="exit_sma5",
                                status="submitted"):
    conn = lpr.open_db(db)
    conn.execute(
        "INSERT INTO positions(symbol, signal_date, signal_atr14,"
        " entry_order_coid, entry_date, entry_price, qty, status, created_ts)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (sym, "2026-11-01", 2.0, f"sl_short-{sym}-e-20261101", "2026-11-01",
         100.0, 50.0, "open", "2026-11-01T00:00:00Z"))
    conn.execute(
        "INSERT INTO orders(client_order_id, symbol, kind, side, qty, price,"
        " ref_date, alpaca_order_id, status, created_ts)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (f"sl_short-{sym}-x-20261105", sym, kind, "sell", 50.0, None,
         "2026-11-05", "ord-2", status, "2026-11-05T00:00:00Z"))
    conn.commit()
    conn.close()


@pytest.mark.parametrize("terminal", ["canceled", "cancelled", "expired",
                                      "rejected"])
def test_reconcile_marks_terminal_exit_statuses(tmp_path, terminal):
    db = tmp_path / "live_paper.db"
    _seed_open_with_exit_order(db, status="submitted")
    conn = lpr.open_db(db)
    conn.row_factory = sqlite3.Row
    summary = {"exit_fills": 0}
    lpr.reconcile_exit_orders(
        conn, None,
        lambda c: {"status": terminal, "filled_avg_price": None,
                   "filled_qty": "0"},
        summary)
    conn.commit()
    o = conn.execute("SELECT status FROM orders").fetchone()
    assert o["status"] == terminal
    p = conn.execute("SELECT status, exit_price FROM positions").fetchone()
    assert p["status"] == "open"        # no fill -> not closed
    assert p["exit_price"] is None
    conn.close()


def test_reconcile_pnl_uses_filled_qty_not_ordered_qty(tmp_path):
    """The realized P&L follows the FILLED quantity of the exit order."""
    db = tmp_path / "live_paper.db"
    _seed_open_with_exit_order(db)
    conn = lpr.open_db(db)
    conn.row_factory = sqlite3.Row
    summary = {"exit_fills": 0}
    lpr.reconcile_exit_orders(
        conn, None,
        lambda c: {"status": "filled", "filled_avg_price": "110.0",
                   "filled_qty": "20", "filled_at": "2026-11-05T15:00:00Z"},
        summary)
    conn.commit()
    p = conn.execute(
        "SELECT status, exit_price, realized_pnl FROM positions").fetchone()
    assert p["status"] == "closed"
    assert p["exit_price"] == pytest.approx(110.0)
    assert p["realized_pnl"] == pytest.approx((110.0 - 100.0) * 20.0)
    conn.close()
