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
    #
    # Models the REAL Alpaca REST API: GET /v2/orders/{id} accepts the
    # Alpaca order id ONLY (a client_order_id in that path 404s — the
    # exact STRAT-023-C4-FILL bug), and client_order_id lookups must go
    # through GET /v2/orders:by_client_order_id.
    def get_order_by_client_id(self, coid):
        o = self.orders.get(coid)
        return dict(o) if o is not None else None

    def get_order(self, order_id):
        for o in self.orders.values():
            if o["id"] == order_id:
                return dict(o)
        return None

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

    def get(self, path, params=None):
        if path == "/v2/orders:by_client_order_id":
            coid = (params or {}).get("client_order_id")
            o = self.orders.get(coid)
            return dict(o) if o is not None else None
        if path.startswith("/v2/orders/"):
            oid = path.rsplit("/", 1)[-1]
            for o in self.orders.values():
                if o["id"] == oid:
                    return dict(o)
            return None  # real API: 404 (e.g. a client_order_id here)
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


def append_bar(df, close=None, days=1):
    """Append ONE new trading session AFTER the last bar.

    The frozen C4 rule evaluates the SMA5 exit ONLY on a bar strictly
    after the entry bar (the entry day is never an exit bar), so every
    exit test must supply a post-entry session."""
    last = df.iloc[-1]
    t = pd.Timestamp(last["t"]) + pd.tseries.offsets.BDay(days)
    c = float(last["c"]) + 25.0 if close is None else float(close)
    row = {"t": t.strftime("%Y-%m-%dT00:00:00Z"), "o": c,
           "h": c + 0.3, "l": c - 0.3, "c": c, "v": last["v"]}
    return pd.concat([df, pd.DataFrame([row])], ignore_index=True)


def shift_dates(df, last_date):
    """Re-date the bars so the LAST bar falls on last_date (business
    days). Used to place a post-entry session after a fixed entry_date."""
    n = len(df)
    days = pd.bdate_range(end=pd.Timestamp(last_date, tz="UTC"), periods=n)
    out = df.copy()
    out["t"] = days.strftime("%Y-%m-%dT00:00:00Z")
    return out


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
    # S3 (Josh 2026-11-03): the sleeve is capped DOWN to a fixed 15% of
    # equity (was 30%). The per-position cap and max positions are
    # unchanged; no SIGNAL parameter was touched.
    assert lpr.SLEEVE_EQUITY_FRAC == 0.15 and lpr.MAX_POSITIONS == 3
    assert lpr.MAX_POSITION_PCT == 0.10
    assert lpr.PAPER_API_URL == "https://paper-api.alpaca.markets"
    assert lpr.COID_PREFIX == "sl_short"


# ------------------------------------------------- S3 sleeve fraction (15%)
def test_s3_sleeve_defaults_to_15pct(tmp_path, monkeypatch):
    monkeypatch.delenv("STRAT024_SLEEVE_EQUITY_FRAC", raising=False)
    lpr, _, _ = fresh(tmp_path)
    assert lpr.DEFAULT_SLEEVE_EQUITY_FRAC == 0.15
    assert lpr.SLEEVE_EQUITY_FRAC == 0.15


def test_s3_sleeve_fraction_is_configurable(tmp_path, monkeypatch):
    monkeypatch.setenv("STRAT024_SLEEVE_EQUITY_FRAC", "0.20")
    lpr, _, _ = fresh(tmp_path)
    assert lpr.SLEEVE_EQUITY_FRAC == 0.20
    # 20% sleeve = 20k, but the 10% per-position cap still binds first
    assert lpr.position_qty(100_000, 0, 0, 50.0, 100_000) == 200.0
    # a tighter configured sleeve DOES bind
    monkeypatch.setenv("STRAT024_SLEEVE_EQUITY_FRAC", "0.05")
    lpr, _, _ = fresh(tmp_path)
    assert lpr.SLEEVE_EQUITY_FRAC == 0.05
    assert lpr.position_qty(100_000, 0, 0, 50.0, 100_000) == 100.0


def test_s3_sleeve_fraction_rejects_bad_values(tmp_path, monkeypatch):
    import live_paper_runner as lpr
    for bad in ("not-a-number", "0", "1.5", "-0.1"):
        monkeypatch.setenv("STRAT024_SLEEVE_EQUITY_FRAC", bad)
        with pytest.raises(ValueError):
            importlib.reload(lpr)
    monkeypatch.delenv("STRAT024_SLEEVE_EQUITY_FRAC", raising=False)
    importlib.reload(lpr)          # restore a clean module
    assert lpr.SLEEVE_EQUITY_FRAC == 0.15


def test_s3_sleeve_caps_total_c4_exposure(tmp_path, monkeypatch):
    # The whole point of S3: C4 exposure is capped at a FIXED 15% of
    # equity. With 14k already open, only ~1k of sleeve is left.
    monkeypatch.delenv("STRAT024_SLEEVE_EQUITY_FRAC", raising=False)
    lpr, _, _ = fresh(tmp_path)
    assert lpr.position_qty(100_000, 14_000, 0, 50.0, 100_000) == 20.0
    # ... and the sleeve is exhausted -> no new entry
    assert lpr.position_qty(100_000, 15_000, 0, 50.0, 100_000) == 0.0
    assert lpr.position_qty(100_000, 20_000, 0, 50.0, 100_000) == 0.0


# -------------------------------------------------------------------- sizing
def test_position_qty_caps():
    q = sr_position_qty(100_000, 0, 0, 50.0, 100_000)
    # cap = min(15_000 sleeve, 10_000 max-position, BP) -> 10000/50 = 200
    assert q == 200.0


def sr_position_qty(equity, open_notion, pending, price, bp):
    import live_paper_runner as lpr

    return lpr.position_qty(equity, open_notion, pending, price, bp)


def test_position_qty_respects_open_sleeve():
    q = sr_position_qty(100_000, 12_000, 0, 50.0, 100_000)
    # remaining sleeve 3_000 -> 60 shares (also under 10% cap of 10_000)
    assert q == 60.0


def test_position_qty_skips_when_sleeve_already_over_committed():
    # 15% sleeve of 100k = 15k; 25k already open -> nothing left
    assert sr_position_qty(100_000, 25_000, 0, 50.0, 100_000) == 0.0


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
    """MAX_POSITIONS is still 3, but the 15% sleeve (and the 10% per-name
    cap) now BIND inside a single run: with equity 100k only 15k may be
    committed at once, so the third candidate is skipped instead of
    over-committing the sleeve (C4-FIX-001)."""
    lpr, api, db = fresh(tmp_path)
    assert lpr.MAX_POSITIONS == 3
    bars = {s: make_bars() for s in ("A1", "A2", "A3", "A4")}
    s = lpr.run_once(db, api, bars)
    # 15% sleeve of 100k = 15k; the per-name cap (10k) binds first, so one
    # full entry + one shrunk entry fit, and the rest is skipped.
    assert s["entries_submitted"] == 2
    assert s["skipped"] >= 1
    import sqlite3

    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT qty, price FROM orders WHERE kind='entry'").fetchall()
    total = sum(q * p for q, p in rows)
    assert total <= 15_000 + 1e-6
    assert all(q * p <= 10_000 + 1e-6 for q, p in rows)


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
    # the SMA5 exit is only evaluated on a POST-ENTRY session (frozen C4
    # rule): append one new bar whose close is far above the SMA5.
    df = append_bar(make_bars())
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
    df = append_bar(make_bars())
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


# ------------------------------------------ STRAT-023-C4-FILL lookup fix
def test_path_lookup_by_coid_fails_like_real_alpaca(tmp_path):
    """Regression proof of the bug's root cause: on the real Alpaca API,
    GET /v2/orders/<client_order_id> does NOT find the order."""
    lpr, api, db = fresh(tmp_path)
    bars = {"AAAA": make_bars()}
    lpr.run_once(db, api, bars)
    e_coid = [c for c, o in api.orders.items() if o["side"] == "buy"][0]
    assert api.get(f"/v2/orders/{e_coid}") is None  # real API: 404
    assert api.get_order_by_client_id(e_coid)["side"] == "buy"  # fixed path


def test_fill_detected_via_client_order_id_lookup(tmp_path):
    """(a) A pending_fill row flips to open with fill price + date via the
    /v2/orders:by_client_order_id lookup; fills_tracked increments."""
    lpr, api, db = fresh(tmp_path)
    bars = {"AAAA": make_bars()}
    api.fill_at = bars["AAAA"]["t"].iloc[-1]
    lpr.run_once(db, api, bars)            # submit entry (fake fills it)
    s = lpr.run_once(db, api, bars)        # fill-tracking pass
    import sqlite3

    conn = sqlite3.connect(db)
    status, px, dt, qty = conn.execute(
        "SELECT status, entry_price, entry_date, qty FROM positions"
    ).fetchone()
    assert status == "open"
    assert px == 100.0 and dt == bars["AAAA"]["t"].iloc[-1][:10]
    assert qty and qty > 0
    assert s["fills_tracked"] == 1
    assert conn.execute(
        "SELECT status FROM orders WHERE kind='entry'").fetchone()[0] == "filled"


def test_stop_placed_on_fill_detection_at_fill_minus_3xatr(tmp_path):
    """(b) The same pass that detects the fill places the resting stop at
    fill_price - 3 * ATR14."""
    lpr, api, db = fresh(tmp_path)
    bars = {"AAAA": make_bars()}
    api.fill_at = bars["AAAA"]["t"].iloc[-1]
    lpr.run_once(db, api, bars)
    s = lpr.run_once(db, api, bars)
    assert s["stops_placed"] == 1
    import sqlite3

    conn = sqlite3.connect(db)
    atr = conn.execute("SELECT signal_atr14 FROM positions").fetchone()[0]
    stop = [o for o in api.orders.values() if o["type"] == "stop"][0]
    assert float(stop["stop_price"]) == pytest.approx(
        round(100.0 - 3.0 * atr, 2))
    assert stop["client_order_id"].startswith("sl_short-AAAA-s-")


def test_exit_runs_after_fill(tmp_path):
    """(c) After the fill is tracked, a close > SMA5 pass submits the
    market exit (the previously-dead exit path)."""
    lpr, api, db = fresh(tmp_path)
    bars = {"AAAA": make_bars()}
    api.fill_at = bars["AAAA"]["t"].iloc[-1]
    lpr.run_once(db, api, bars)
    lpr.run_once(db, api, bars)            # fill + stop
    df = append_bar(bars["AAAA"])          # a POST-ENTRY session
    s = lpr.run_once(db, api, {"AAAA": df})
    assert s["exits_submitted"] == 1
    import sqlite3

    conn = sqlite3.connect(db)
    reason = conn.execute("SELECT exit_reason FROM positions").fetchone()[0]
    assert reason == "sma5"


def test_fill_falls_back_to_stored_alpaca_order_id(tmp_path):
    """When the by-client-order-id lookup cannot find the order (e.g.
    broker retention), the stored alpaca_order_id still detects the fill."""
    lpr, api, db = fresh(tmp_path)

    class NoCoidLookupAPI(FakeAPI):
        def get_order_by_client_id(self, coid):
            return None  # force the fallback path

    api = NoCoidLookupAPI()
    bars = {"AAAA": make_bars()}
    api.fill_at = bars["AAAA"]["t"].iloc[-1]
    lpr.run_once(db, api, bars)
    s = lpr.run_once(db, api, bars)
    assert s["fills_tracked"] == 1 and s["stops_placed"] == 1
    import sqlite3

    conn = sqlite3.connect(db)
    assert conn.execute(
        "SELECT status FROM positions").fetchone()[0] == "open"


def test_paper_api_lookup_raises_loudly_on_unexpected_error(tmp_path):
    """Non-404 lookup errors must raise, never be mistaken for pending."""
    import requests as _requests

    lpr, _, _ = fresh(tmp_path)

    class Resp:
        status_code = 503
        text = "service unavailable"

        def raise_for_status(self):
            raise _requests.HTTPError("503")

    class Sess:
        def get(self, *a, **kw):
            return Resp()

    paper = lpr.PaperAPI("key", "sec", session=Sess())
    with pytest.raises(_requests.HTTPError):
        paper.get_order_by_client_id("sl_short-X-e-20261102")
    with pytest.raises(_requests.HTTPError):
        paper.get_order("ord-1")


def test_paper_api_lookup_returns_none_on_404(tmp_path):
    lpr, _, _ = fresh(tmp_path)

    class Resp:
        status_code = 404
        text = "order not found"

        def raise_for_status(self):
            raise AssertionError("must not raise on 404")

    class Sess:
        def get(self, *a, **kw):
            return Resp()

    paper = lpr.PaperAPI("key", "sec", session=Sess())
    assert paper.get_order_by_client_id("sl_short-X-e-20261102") is None
    assert paper.get_order("ord-1") is None


# ------------------------------------- dry-run proof: live DB state replay
# State observed read-only in /root/strat-swing/experiments/strat_023/
# live_paper.db on 2026-11-03: AMD / AVTR / CRL pending_fill with these
# exact client_order_ids and alpaca_order_ids, all filled at the Nov 3
# open on the broker but stuck pending_fill locally.
LIVE_FILL_STATE = [
    # symbol, coid, alpaca_id, atr14, fill_px, fill_qty
    ("AMD", "sl_short-AMD-e-20261102",
     "1c2f5f29-c63d-4a65-b449-de58e959784a", 32.9896642857143,
     467.05, 22.0),
    ("AVTR", "sl_short-AVTR-e-20261102",
     "35a04df6-c159-4391-9604-66ba49fea25d", 0.562321428571428,
     15.62, 679.0),
    ("CRL", "sl_short-CRL-e-20261102",
     "75fcaf81-e7e5-48da-843d-6dbab6e7036f", 16.9818785714286,
     338.54, 31.0),
]
FILL_TS = "2026-11-03T14:30:00Z"


def _seed_live_state(lpr, api, db, tmp_path):
    import sqlite3

    conn = sqlite3.connect(db)
    conn.executescript(lpr.SCHEMA)
    now = datetime(2026, 11, 3, 21, 45, tzinfo=timezone.utc).isoformat()
    for sym, c, aid, atr, px, qty in LIVE_FILL_STATE:
        conn.execute(
            "INSERT INTO orders(client_order_id, symbol, kind, sleeve,"
            " side, qty, price, ref_date, alpaca_order_id, status,"
            " created_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (c, sym, "entry", "short", "buy", qty, px, "2026-11-02",
             aid, "submitted", now))
        conn.execute(
            "INSERT INTO positions(symbol, signal_date, signal_atr14,"
            " entry_order_coid, status, created_ts)"
            " VALUES (?,?,?,?, 'pending_fill', ?)",
            (sym, "2026-11-02", atr, c, now))
        api.orders[c] = {
            "id": aid, "client_order_id": c, "symbol": sym,
            "qty": str(qty), "side": "buy", "type": "market",
            "status": "filled", "filled_avg_price": str(px),
            "filled_qty": str(qty), "filled_at": FILL_TS}
        api.positions[sym] = api.positions.get(sym, 0) + qty
    conn.commit()
    conn.close()


def test_dryrun_live_state_three_fills_detected_stops_placed(tmp_path):
    """One runner pass over the exact live DB state: 3 fills detected via
    client_order_id lookup, 3 stops at fill - 3*ATR14, rows flipped open."""
    lpr, api, db = fresh(tmp_path)
    _seed_live_state(lpr, api, db, tmp_path)
    s = lpr.run_once(db, api, {}, run_ts=datetime(
        2026, 11, 3, 21, 45, tzinfo=timezone.utc))
    assert s["fills_tracked"] == 3
    assert s["stops_placed"] == 3
    import sqlite3

    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT symbol, status, entry_price, entry_date FROM positions"
        " ORDER BY symbol").fetchall()
    assert rows == [
        ("AMD", "open", 467.05, "2026-11-03"),
        ("AVTR", "open", 15.62, "2026-11-03"),
        ("CRL", "open", 338.54, "2026-11-03")]
    stops = {o["symbol"]: o for o in api.orders.values()
             if o["type"] == "stop"}
    assert set(stops) == {"AMD", "AVTR", "CRL"}
    for sym, c, aid, atr, px, qty in LIVE_FILL_STATE:
        assert float(stops[sym]["stop_price"]) == pytest.approx(
            round(px - 3.0 * atr, 2))
        assert stops[sym]["qty"] == str(int(qty))
    assert conn.execute(
        "SELECT COUNT(*) FROM orders WHERE kind='entry' AND"
        " status='filled'").fetchone()[0] == 3


def test_dryrun_live_state_exits_run_on_next_pass(tmp_path):
    """Exits are live again: after the fills are tracked, an SMA5 breach
    on the next pass submits market sells for all 3 symbols."""
    lpr, api, db = fresh(tmp_path)
    _seed_live_state(lpr, api, db, tmp_path)
    ts = datetime(2026, 11, 3, 21, 45, tzinfo=timezone.utc)
    lpr.run_once(db, api, {}, run_ts=ts)   # fills + stops
    bars = {}
    for sym, *_ in LIVE_FILL_STATE:
        # the fills are dated 2026-11-03, so the exit session must be a
        # LATER date (the frozen C4 rule: no SMA5 exit on the entry day)
        df = shift_dates(make_bars(), "2026-11-04")
        df.loc[df.index[-1], "c"] = float(df["c"].iloc[-1]) + 25.0
        df.loc[df.index[-1], "h"] = float(df["h"].iloc[-1]) + 25.0
        bars[sym] = df
    s = lpr.run_once(db, api, bars, run_ts=ts)
    assert s["exits_submitted"] == 3
    assert len(api.cancelled) == 3          # resting stops cancelled first
