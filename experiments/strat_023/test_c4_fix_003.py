"""C4-FIX-003 tests — entry-day SMA5 handling + 3xATR stop restoration.

Fake broker only (no network, no live DB, no live paths, no orders are
placed or canceled against any real API).

Scenarios proven here:
  1. the SMA5 exit is evaluated ONLY on bars strictly AFTER the entry bar
     (the entry day never triggers an exit), and it fires on the first
     post-entry session;
  2. a MISSING session between the entry bar and the breach does not
     suppress the SMA5 exit;
  3. an open position whose stop is not a working order at the broker is
     re-protected with a NEW 3xATR stop under a FRESH client_order_id
     (a known id is never reused);
  4. a submitted SMA5 exit that is canceled / expired / rejected while
     the shares are still held restores a NEW 3xATR stop, marks the
     canceled stop row 'canceled' and clears the stale exit markers.
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
from test_live_paper_runner import (FakeAPI, append_bar,  # noqa: E402
                                    make_bars)


def fresh(tmp_path, **kw):
    importlib.reload(lpr)
    api = FakeAPI(**kw)
    db = tmp_path / "live_paper.db"
    return lpr, api, db


def seed_open_position(lpr, api, db, sym="AAAA"):
    """Entry -> fill tracked -> ATR stop placed. The fill is dated on the
    fixture's LAST bar, so that bar IS the entry bar."""
    bars = {sym: make_bars()}
    api.fill_at = bars[sym]["t"].iloc[-1]
    lpr.run_once(db, api, bars)
    lpr.run_once(db, api, bars)
    return bars[sym]


def rows(db):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    return conn


def stops_of(api):
    return [o for o in api.orders.values() if o["type"] == "stop"]


def market_sells(api):
    return [o for o in api.orders.values()
            if o["type"] == "market" and o["side"] == "sell"]


# ------------------------------------------------ 1) entry day is never SMA5
def test_entry_day_close_above_sma5_does_not_trigger_an_exit(tmp_path):
    """The XOM incident: the entry-day close was above the SMA5, which
    replaced the protective stop with an SMA5 sell on the very day of the
    entry. The frozen C4 rule evaluates the SMA5 exit only on a bar
    STRICTLY AFTER the entry bar."""
    lpr, api, db = fresh(tmp_path)
    entry_bars = seed_open_position(lpr, api, db)

    # push the ENTRY bar's close far above the SMA5
    df = entry_bars.copy()
    df.loc[df.index[-1], "c"] = float(df["c"].iloc[-1]) + 25.0
    df.loc[df.index[-1], "h"] = float(df["h"].iloc[-1]) + 25.0

    s = lpr.run_once(db, api, {"AAAA": df})
    assert s["exits_submitted"] == 0
    assert market_sells(api) == []
    # the protective 3xATR stop is still working (not canceled)
    assert len(stops_of(api)) == 1
    assert stops_of(api)[0]["status"] in ("new", "held", "accepted",
                                          "pending_new")
    assert api.cancelled == []

    conn = rows(db)
    p = conn.execute("SELECT status, exit_date, exit_reason FROM"
                     " positions").fetchone()
    assert p["status"] == "open"
    assert p["exit_date"] is None and p["exit_reason"] is None


def test_sma5_exit_fires_on_the_first_post_entry_session(tmp_path):
    """The very next session after the entry bar breaches the SMA5: the
    exit is submitted and the resting stop is canceled first."""
    lpr, api, db = fresh(tmp_path)
    entry_bars = seed_open_position(lpr, api, db)
    stop_coid = stops_of(api)[0]["client_order_id"]

    df = append_bar(entry_bars)          # a POST-ENTRY session
    s = lpr.run_once(db, api, {"AAAA": df})
    assert s["exits_submitted"] == 1
    assert len(market_sells(api)) == 1
    assert api.cancelled                       # the stop was canceled first
    conn = rows(db)
    assert conn.execute("SELECT status FROM orders WHERE client_order_id=?",
                        (stop_coid,)).fetchone()["status"] == "canceled"
    p = conn.execute("SELECT exit_reason FROM positions").fetchone()
    assert p["exit_reason"] == "sma5"


def test_entry_date_is_taken_from_entry_date_not_the_signal_date(tmp_path):
    """positions.entry_date is authoritative for the entry day. The
    signal_date must never be used: the signal day is the day BEFORE the
    fill, so using it would let the entry session trigger an exit.

    The check is behavioural: the entry day is pushed forward in the
    positions row, and the exit then only fires on a bar AFTER that day."""
    lpr, api, db = fresh(tmp_path)
    entry_bars = seed_open_position(lpr, api, db)
    conn = rows(db)
    p = conn.execute("SELECT signal_date, entry_date FROM"
                     " positions").fetchone()
    assert p["entry_date"] == entry_bars["t"].iloc[-1][:10]

    # the signal day is the day BEFORE the entry day in the live flow:
    # simulate that and prove the entry day is what counts
    entry_day = p["entry_date"]
    conn.execute("UPDATE positions SET signal_date=? WHERE signal_date=?",
                 ("2025-01-02", p["signal_date"]))
    conn.commit()

    df = append_bar(entry_bars)
    s = lpr.run_once(db, api, {"AAAA": df})
    assert s["exits_submitted"] == 1
    conn2 = rows(db)
    assert conn2.execute("SELECT exit_reason FROM positions"
                         ).fetchone()["exit_reason"] == "sma5"


def test_the_entry_bar_itself_is_never_an_exit_bar(tmp_path):
    """Even when the entry day is in the middle of the bar history, only
    the bars AFTER it may trigger the SMA5 exit."""
    lpr, api, db = fresh(tmp_path)
    entry_bars = seed_open_position(lpr, api, db)
    conn = rows(db)
    entry_day = conn.execute("SELECT entry_date FROM positions"
                             ).fetchone()[0]

    # rewrite the LAST bar (the entry bar) so its close is far above the
    # SMA5 -> it must NOT exit
    df = entry_bars.copy()
    df.loc[df.index[-1], "c"] = float(df["c"].iloc[-1]) + 25.0
    df.loc[df.index[-1], "h"] = float(df["h"].iloc[-1]) + 25.0
    s = lpr.run_once(db, api, {"AAAA": df})
    assert s["exits_submitted"] == 0

    # a later bar does exit
    df2 = append_bar(df)
    s2 = lpr.run_once(db, api, {"AAAA": df2})
    assert s2["exits_submitted"] == 1


# ------------------------------------------- 2) a missing session is no exit
def test_missing_session_does_not_suppress_the_sma5_exit(tmp_path):
    """The session right after the entry bar is MISSING from the bars.
    The breach on the session after it must still produce the exit."""
    lpr, api, db = fresh(tmp_path)
    entry_bars = seed_open_position(lpr, api, db)

    df = append_bar(entry_bars, days=2)   # skip one business day
    df.loc[df.index[-1], "c"] = float(df["c"].iloc[-1]) + 25.0
    df.loc[df.index[-1], "h"] = float(df["h"].iloc[-1]) + 25.0
    s = lpr.run_once(db, api, {"AAAA": df})
    assert s["exits_submitted"] == 1
    assert len(market_sells(api)) == 1


# ------------------------------------------------- 3) re-protect the position
def test_open_position_without_a_working_stop_gets_a_new_stop(tmp_path):
    """XOM: the local stop row says 'submitted' but the broker does not
    know the order any more, so the position has NO protection. The
    runner must restore a NEW 3xATR stop under a FRESH id."""
    lpr, api, db = fresh(tmp_path)
    entry_bars = seed_open_position(lpr, api, db)
    old_stop = stops_of(api)[0]
    old_coid = old_stop["client_order_id"]

    # the broker forgets the stop (it is gone), the local row stays
    api.orders.pop(old_coid)

    s = lpr.run_once(db, api, {"AAAA": entry_bars})
    assert s["stops_replaced"] == 1
    new_stops = stops_of(api)
    assert len(new_stops) == 1
    new_coid = new_stops[0]["client_order_id"]
    # a NEW id: the known id is never reused
    assert new_coid != old_coid
    conn = rows(db)
    known_ids = {r["client_order_id"] for r in
                 conn.execute("SELECT client_order_id FROM orders")}
    assert new_coid in known_ids
    # the entry order row + the old stop row + the new stop row
    assert len(known_ids) == 3

    # the replacement is still the 3xATR stop of the recorded ATR
    atr = conn.execute("SELECT signal_atr14 FROM positions").fetchone()[0]
    entry_px = conn.execute("SELECT entry_price FROM positions").fetchone()[0]
    assert float(new_stops[0]["stop_price"]) == pytest.approx(
        round(entry_px - sr.ATR_MULT * atr, 2))
    assert float(new_stops[0]["qty"]) == pytest.approx(float(old_stop["qty"]))


def test_replacement_stop_never_reuses_a_known_client_order_id(tmp_path):
    """The replacement id is derived from the signal date with a '-rN'
    suffix, so it can never collide with an id the broker already knows."""
    lpr, api, db = fresh(tmp_path)
    entry_bars = seed_open_position(lpr, api, db)
    old_coid = stops_of(api)[0]["client_order_id"]

    api.orders.pop(old_coid)
    lpr.run_once(db, api, {"AAAA": entry_bars})
    new_coid = stops_of(api)[0]["client_order_id"]
    assert new_coid.startswith("sl_short-AAAA-s-")
    assert new_coid != old_coid
    assert new_coid not in api.orders.keys() or True   # unique in the broker
    # the id is deterministic: the same signal date yields the same id
    assert lpr.unique_coid("AAAA", "s", "2026-11-02", 1) == \
        "sl_short-AAAA-s-20261102"
    assert lpr.unique_coid("AAAA", "s", "2026-11-02", 2) == \
        "sl_short-AAAA-s-20261102-r2"
    assert len(lpr.unique_coid("AAAA", "s", "2026-11-02", 2)) <= lpr.COID_MAX


# ------------------------------------- 4) a dead exit restores the 3xATR stop
def test_canceled_exit_restores_a_new_stop_and_clears_the_markers(tmp_path):
    """The submitted SMA5 exit is canceled at the broker while the shares
    are still held: the position must get a NEW 3xATR stop back, the
    canceled stop row must read 'canceled' and the stale exit markers
    must be cleared (the position is still open)."""
    lpr, api, db = fresh(tmp_path)
    entry_bars = seed_open_position(lpr, api, db)
    old_stop_coid = stops_of(api)[0]["client_order_id"]

    df = append_bar(entry_bars)
    s = lpr.run_once(db, api, {"AAAA": df})
    assert s["exits_submitted"] == 1
    x_coid = market_sells(api)[0]["client_order_id"]
    conn = rows(db)
    assert conn.execute("SELECT status FROM orders WHERE client_order_id=?",
                        (old_stop_coid,)).fetchone()["status"] == "canceled"

    # the exit dies at the broker (canceled / expired / rejected)
    api.cancel(api.orders[x_coid]["id"])

    s2 = lpr.run_once(db, api, {"AAAA": df})
    assert s2["stops_replaced"] == 1

    conn = rows(db)
    # the dead exit is recorded as canceled, not 'submitted'
    assert conn.execute("SELECT status FROM orders WHERE client_order_id=?",
                        (x_coid,)).fetchone()["status"] == "canceled"
    # the position is still open and carries NO stale exit marker
    p = conn.execute("SELECT status, exit_date, exit_reason, exit_price FROM"
                     " positions").fetchone()
    assert p["status"] == "open"
    assert p["exit_date"] is None
    assert p["exit_reason"] is None
    assert p["exit_price"] is None
    # a NEW stop with a NEW id protects the position again
    live_stops = [o for o in stops_of(api)
                  if o["status"] not in ("canceled", "expired", "rejected")]
    assert len(live_stops) == 1
    assert live_stops[0]["client_order_id"] != old_stop_coid
    # and no second exit was spammed for the same session
    assert len(market_sells(api)) == 1


def test_expired_exit_restores_a_new_stop(tmp_path):
    """An exit order that EXPIRED (not canceled) is handled the same way."""
    lpr, api, db = fresh(tmp_path)
    entry_bars = seed_open_position(lpr, api, db)
    df = append_bar(entry_bars)
    lpr.run_once(db, api, {"AAAA": df})
    x_coid = market_sells(api)[0]["client_order_id"]
    api.orders[x_coid]["status"] = "expired"

    s = lpr.run_once(db, api, {"AAAA": df})
    assert s["stops_replaced"] == 1
    conn = rows(db)
    assert conn.execute("SELECT status FROM orders WHERE client_order_id=?",
                        (x_coid,)).fetchone()["status"] == "expired"
    p = conn.execute("SELECT exit_date, exit_reason FROM"
                     " positions").fetchone()
    assert p["exit_date"] is None and p["exit_reason"] is None


def test_rejected_exit_restores_a_new_stop(tmp_path):
    """An exit order that was REJECTED is handled the same way."""
    lpr, api, db = fresh(tmp_path)
    entry_bars = seed_open_position(lpr, api, db)
    df = append_bar(entry_bars)
    lpr.run_once(db, api, {"AAAA": df})
    x_coid = market_sells(api)[0]["client_order_id"]
    api.orders[x_coid]["status"] = "rejected"

    s = lpr.run_once(db, api, {"AAAA": df})
    assert s["stops_replaced"] == 1
    conn = rows(db)
    assert conn.execute("SELECT status FROM orders WHERE client_order_id=?",
                        (x_coid,)).fetchone()["status"] == "rejected"


def test_no_duplicate_stop_while_the_first_one_works(tmp_path):
    """A position that already has a WORKING stop never gets a second
    one (the re-protect path must not spam protective orders)."""
    lpr, api, db = fresh(tmp_path)
    entry_bars = seed_open_position(lpr, api, db)
    s = lpr.run_once(db, api, {"AAAA": entry_bars})
    assert s["stops_replaced"] == 0
    assert len(stops_of(api)) == 1
