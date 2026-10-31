"""STRAT-024 tests — live paper-order runner.

All Alpaca interactions go through a FakeAPI (no network). Guards,
sizing caps, idempotency, exit-as-orders, and never-sell-main-bot-shares
are proven here. C4 thresholds are proven frozen.
"""
from __future__ import annotations

import importlib
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import shadow_runner as sr  # noqa: E402


# ------------------------------------------------------------------ fixtures
def make_bars(follow_sma200=True, final_dip=True, n=260):
    """Synthetic daily bars. Steady uptrend (close > SMA200), then a sharp
    3-day dip at the end so RSI(2) < 10 and close < SMA5 (C4 entry)."""
    dates = pd.date_range("2025-01-01", periods=n, freq="B", tz="UTC")
    close = np.linspace(100.0, 150.0, n - 3)
    if not follow_sma200:
        close = close * 0.5
    dip = np.array([close[-1], close[-1] - 5.0, close[-1] - 10.0])
    close = np.concatenate([close, dip]) if final_dip else np.concatenate(
        [close, np.array([close[-1], close[-1] + 1.0, close[-1] + 2.0])])
    open_ = close + 0.5
    high = np.maximum(open_, close) + 0.3
    low = np.minimum(open_, close) - 0.3
    vol = np.full(n, 2_000_000.0)
    df = pd.DataFrame({"t": dates.strftime("%Y-%m-%dT00:00:00Z"),
                       "o": open_, "h": high, "l": low, "c": close, "v": vol})
    return df


class FakeAPI:
    """Minimal in-memory stand-in for the Alpaca paper REST API."""

    PAPER = "https://paper-api.alpaca.markets"

    def __init__(self, equity=100_000.0, buying_power=100_000.0,
                 fill_at="2026-10-30T14:30:00Z"):
        self.headers = None
        self.base = self.PAPER
        self.sess = None
        self.equity = equity
        self.buying_power = buying_power
        self.fill_at = fill_at
        self.orders = {}          # coid -> order dict
        self.positions = {}       # symbol -> qty
        self.cancelled = []
        self.order_seq = 0

    # -- endpoint surface used by live_paper_runner.PaperAPI
    def account(self):
        return {"equity": str(self.equity),
                "buying_power": str(self.buying_power)}

    def position(self, symbol):
        if symbol in self.positions:
            return {"qty": str(self.positions[symbol])}
        return None

    def open_runner_orders(self):
        return [dict(o) for o in self.orders.values()
                if o["status"] in ("new", "held", "accepted", "pending_new")]

    def submit(self, body):
        for c in self.orders.values():
            if c["client_order_id"] == body["client_order_id"] and \
                    c["status"] in ("new", "held", "accepted", "pending_new",
                                    "filled"):
                raise RuntimeError("duplicate client_order_id")
        self.order_seq += 1
        o = {"id": f"ord-{self.order_seq}",
             "client_order_id": body["client_order_id"],
             "symbol": body["symbol"], "qty": body["qty"],
             "side": body["side"], "type": body["type"],
             "status": "new"}
        if "stop_price" in body:
            o["stop_price"] = body["stop_price"]
        if "limit_price" in body:
            o["limit_price"] = body["limit_price"]
        self.orders[body["client_order_id"]] = o
        if body["side"] == "buy":
            self.positions[body["symbol"]] = \
                self.positions.get(body["symbol"], 0) + float(body["qty"])
            o.update({"status": "filled", "filled_avg_price": "100.0",
                      "filled_qty": body["qty"], "filled_at": self.fill_at})
        return dict(o)

    def get(self, path):
        if path.startswith("/v2/orders/"):
            coid = path.split("/", 3)[-1]
            if coid in self.orders:
                return dict(self.orders[coid])
            raise RuntimeError("not found")
        if path == "/v2/account":
            return self.account()
        if path.startswith("/v2/positions/"):
            sym = path.rsplit("/", 1)[-1]
            if sym in self.positions:
                return {"qty": str(self.positions[sym])}
        raise RuntimeError(f"unexpected GET {path}")

    def cancel(self, order_id):
        self.cancelled.append(order_id)
        for o in self.orders.values():
            if o["id"] == order_id:
                o["status"] = "canceled"

    # -- test helpers
    def fill_stop(self, coid):
        o = self.orders[coid]
        o["status"] = "filled"
        sym = o["symbol"]
        self.positions[sym] = self.positions.get(sym, 0) - float(o["qty"])
        if self.positions[sym] <= 0:
            del self.positions[sym]


def fresh(tmp_path, **kw):
    import live_paper_runner as lpr

    lpr_mod = importlib.reload(lpr)
    api = FakeAPI(**kw)
    db = tmp_path / "live_paper.db"
    return lpr_mod, api, db


# -------------------------------------------------------------------- guards
def test_guard_refuses_without_env_var(monkeypatch, tmp_path):
    lpr, _, _ = fresh(tmp_path)
    monkeypatch.delenv("TRADING_BOT_PAPER_ONLY", raising=False)
    with pytest.raises(SystemExit) as e:
        lpr.guards()
    assert e.value.code == 2


def test_guard_refuses_non_paper_base_url(monkeypatch, tmp_path):
    lpr, _, _ = fresh(tmp_path)
    monkeypatch.setenv("TRADING_BOT_PAPER_ONLY", "1")
    monkeypatch.setattr(lpr, "API_BASE", "https://api.alpaca.markets")
    with pytest.raises(SystemExit) as e:
        lpr.guards()
    assert e.value.code == 2


def test_guard_accepts_paper(monkeypatch, tmp_path):
    lpr, _, _ = fresh(tmp_path)
    monkeypatch.setenv("TRADING_BOT_PAPER_ONLY", "1")
    monkeypatch.setattr(lpr, "API_BASE", lpr.PAPER_API_URL)
    lpr.guards()  # must not raise


def test_kill_switch_blocks_all_orders(monkeypatch, tmp_path):
    lpr, _, db = fresh(tmp_path)
    monkeypatch.setenv("TRADING_BOT_PAPER_ONLY", "1")
    kill = tmp_path / "KILL"
    kill.write_text("stop")
    monkeypatch.setattr(lpr, "KILL_FILE", kill)
    with pytest.raises(SystemExit) as e:
        lpr.guards()
    assert e.value.code == 2


# -------------------------------------------------------------------- freezes
def test_c4_parameters_not_relaxed(tmp_path):
    lpr, _, _ = fresh(tmp_path)
    assert sr.ENTRY_RSI == 10 and sr.ATR_MULT == 3.0 and sr.TIME_STOP == 10
    assert lpr.SLEEVE_EQUITY_FRAC == 0.30 and lpr.MAX_POSITIONS == 3
    assert lpr.MAX_POSITION_PCT == 0.10
    assert lpr.PAPER_API_URL == "https://paper-api.alpaca.markets"
    assert lpr.COID_PREFIX == "sl_short"


# -------------------------------------------------------------------- sizing
def test_position_qty_caps():
    q = sr_position_qty(100_000, 0, 0, 50.0, 100_000)
    # cap = min(30_000 sleeve, 10_000 max-position, BP) -> 10000/50 = 200
    assert q == 200.0


def sr_position_qty(equity, open_notion, pending, price, bp):
    import live_paper_runner as lpr

    return lpr.position_qty(equity, open_notion, pending, price, bp)


def test_position_qty_respects_open_sleeve():
    q = sr_position_qty(100_000, 25_000, 0, 50.0, 100_000)
    # remaining sleeve 5_000 -> 100 shares (also under 10% cap of 10_000)
    assert q == 100.0


def test_position_qty_skips_when_cannot_afford_share():
    assert sr_position_qty(1_000, 0, 0, 5_000.0, 1_000) == 0.0


# ------------------------------------------------------- entry + stop orders
def test_entry_uses_coid_prefix_and_sleeve_short(tmp_path):
    lpr, api, db = fresh(tmp_path)
    bars = {"AAAA": make_bars()}
    lpr.run_once(db, api, bars, run_ts=datetime(2026, 10, 30, 21, 15,
                                                tzinfo=timezone.utc))
    entry = [o for o in api.orders.values() if o["side"] == "buy"]
    assert len(entry) == 1
    assert entry[0]["client_order_id"].startswith("sl_short-AAAA-e-")
    import sqlite3

    conn = sqlite3.connect(db)
    row = conn.execute("SELECT sleeve, kind FROM orders").fetchone()
    assert row == ("short", "entry")


def test_entry_skips_symbol_main_bot_holds(tmp_path):
    lpr, api, db = fresh(tmp_path)
    api.positions["AAAA"] = 50  # main bot holds; runner does not
    bars = {"AAAA": make_bars()}
    s = lpr.run_once(db, api, bars)
    assert s["entries_submitted"] == 0 and s["skipped"] == 1


def test_max_three_open_positions(tmp_path):
    lpr, api, db = fresh(tmp_path)
    bars = {s: make_bars() for s in ("A1", "A2", "A3", "A4")}
    s = lpr.run_once(db, api, bars)
    assert s["entries_submitted"] == 3  # MAX_POSITIONS


# ------------------------------------------------------- fills, stop, exits
def _seed_open_position(lpr, api, db, sym="AAAA"):
    """Run once to submit entry; FakeAPI fills buys immediately, so a second
    run tracks the fill and places the 3xATR stop. The fill date is pinned to
    the fixture's last bar so day-counting works."""
    bars = {sym: make_bars()}
    api.fill_at = bars[sym]["t"].iloc[-1]
    lpr.run_once(db, api, bars)
    lpr.run_once(db, api, bars)
    stops = [o for o in api.orders.values()
             if o["side"] == "sell" and o["type"] == "stop"]
    assert len(stops) == 1
    return stops[0]


def test_stop_order_price_is_3xatr_below_fill(tmp_path):
    lpr, api, db = fresh(tmp_path)
    stop = _seed_open_position(lpr, api, db)
    # fill 100.0, signal-day ATR14 comes from the bars; verify formula via DB
    import sqlite3

    conn = sqlite3.connect(db)
    atr = conn.execute("SELECT signal_atr14 FROM positions").fetchone()[0]
    assert float(stop["stop_price"]) == pytest.approx(
        round(100.0 - 3.0 * atr, 2))


def test_sma5_exit_places_market_sell_and_cancels_stop(tmp_path):
    lpr, api, db = fresh(tmp_path)
    _seed_open_position(lpr, api, db)
    # engineer latest close above SMA5: bounce the last close hard
    bars = {"AAAA": make_bars()}
    df = bars["AAAA"]
    df.loc[df.index[-1], "c"] = float(df["c"].iloc[-1]) + 25.0
    df.loc[df.index[-1], "h"] = float(df["h"].iloc[-1]) + 25.0
    s = lpr.run_once(db, api, {"AAAA": df})
    assert s["exits_submitted"] == 1
    sells = [o for o in api.orders.values()
             if o["side"] == "sell" and o["type"] == "market"]
    assert len(sells) == 1
    assert api.cancelled  # resting stop was cancelled first


def test_time_exit_after_10_days(tmp_path):
    lpr, api, db = fresh(tmp_path)
    _seed_open_position(lpr, api, db)
    # append 10 extra trading days drifting sideways (close stays under SMA5
    # so only the time stop can trigger)
    bars = {"AAAA": make_bars()}
    df = bars["AAAA"]
    last = df.iloc[-1]
    extra = pd.DataFrame({
        "t": pd.date_range(df["t"].iloc[-1], periods=11, freq="B",
                           tz="UTC")[1:].strftime("%Y-%m-%dT00:00:00Z"),
        "o": last["c"], "h": last["c"] + 0.1, "l": last["c"] - 0.1,
        "c": last["c"], "v": last["v"]})
    df2 = pd.concat([df, extra], ignore_index=True)
    s = lpr.run_once(db, api, {"AAAA": df2})
    assert s["exits_submitted"] == 1
    import sqlite3

    conn = sqlite3.connect(db)
    reason = conn.execute("SELECT exit_reason FROM positions").fetchone()[0]
    assert reason == "time"


def test_never_sells_more_than_own_qty(tmp_path):
    lpr, api, db = fresh(tmp_path)
    _seed_open_position(lpr, api, db, sym="AAAA")
    # main bot bought more of the same symbol after our entry
    tracked = api.positions["AAAA"]
    api.positions["AAAA"] = tracked + 400
    bars = {"AAAA": make_bars()}
    df = bars["AAAA"]
    df.loc[df.index[-1], "c"] = float(df["c"].iloc[-1]) + 25.0
    df.loc[df.index[-1], "h"] = float(df["h"].iloc[-1]) + 25.0
    lpr.run_once(db, api, {"AAAA": df})
    sell = [o for o in api.orders.values()
            if o["side"] == "sell" and o["type"] == "market"][0]
    assert float(sell["qty"]) == float(tracked)


# ---------------------------------------------------------------- idempotency
def test_rerun_places_no_duplicate_orders(tmp_path):
    lpr, api, db = fresh(tmp_path)
    bars = {"AAAA": make_bars()}
    s1 = lpr.run_once(db, api, bars)
    s2 = lpr.run_once(db, api, bars)   # second pass, same signal date
    entries = [o for o in api.orders.values() if o["side"] == "buy"]
    assert len(entries) == 1
    assert s2["entries_submitted"] == 0


def test_external_stop_fill_closes_position(tmp_path):
    lpr, api, db = fresh(tmp_path)
    stop = _seed_open_position(lpr, api, db)
    api.fill_stop(stop["client_order_id"])
    s = lpr.run_once(db, api, {"AAAA": make_bars()})
    import sqlite3

    conn = sqlite3.connect(db)
    status = conn.execute("SELECT status FROM positions").fetchone()[0]
    assert status == "closed"


def test_fixture_mode_needs_second_opt_in(monkeypatch, tmp_path):
    lpr, _, _ = fresh(tmp_path)
    monkeypatch.delenv("STRAT024_FIXTURE_LIVE", raising=False)
    with pytest.raises(SystemExit) as e:
        lpr.fixture_guard("/tmp/somefixture")
    assert e.value.code == 2
    lpr.fixture_guard(None)  # live-data mode unaffected
