"""Runner tests for STRAT-SWING-PAPER with a FakeBroker — no network.

Round 3 (PR #135) covers the Josh decisions of 2026-11-03:
  S1  one GTC stop for the FULL held qty after fill; no partial/OCO
  S2  fixed 15% sleeve, 4 positions max, each <= 25% of the sleeve;
      no subtract-C4 logic
  B   stop raise = PATCH with a NEW deterministic client_order_id
      (...-stop-r<k>); the NEW order id and client id are stored
  C   time exit: cancel-then-poll; pending_cancel waits; 404/422 on an
      already-canceled order is success; unconfirmed cancel keeps the
      stop and retries next run
  D   position gone: cancel leftover rsi2 sell orders; stop_filled
      detected via the CURRENT stop client id
  J   one injected clock through run()
  K   C4 skip matches ref_date of the last completed trading day

The FakeBroker validates order payloads per the Alpaca v2 docs
(order_class/type/legs/qty/side/time_in_force combinations) — malformed
order bodies are REJECTED, not accepted silently.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

HERE = Path(__file__).resolve().parents[1] / "experiments" / "swing_paper"
sys.path.insert(0, str(HERE))

import runner  # noqa: E402
import signals  # noqa: E402
from rules import (RSI2, make_client_order_id,  # noqa: E402
                   make_stop_client_order_id,
                   make_stop_replace_client_order_id)


# ------------------------------------------------------------ fake broker
VALID_TYPES = {"market", "limit", "stop", "stop_limit", "trailing_stop"}
VALID_CLASSES = {"simple", "bracket", "oco", "oto"}
VALID_TIF = {"day", "gtc", "opg", "cls", "ioc", "fok"}
TERMINAL = ("canceled", "expired", "filled")


def validate_order_body(body: dict) -> None:
    """Reject malformed order payloads per the Alpaca v2 order docs.

    Alpaca order docs (docs.alpaca.markets/docs/orders-at-alpaca):
      * `order_class` is simple (default) | bracket | oco | oto.
      * bracket/oco/oto MUST carry a non-empty `legs` array; every leg is
        a complete order itself and must NOT carry a nested order_class;
        type/qty live on the legs, not at the top level.
      * a simple order carries `type` (market | limit | stop | stop_limit |
        trailing_stop) plus the matching price field(s):
        market -> no price field; limit -> limit_price;
        stop -> stop_price; stop_limit -> stop_price AND limit_price;
        trailing_stop -> trail_price or trail_percent.
      * `side` is buy|sell, `qty` (or `ratio_qty`) must be > 0,
        `time_in_force` is day|gtc|opg|cls|ioc|fok, `symbol` is required.
    """
    if not isinstance(body, dict):
        raise ValueError(f"order body must be a dict, got {type(body)!r}")
    oclass = body.get("order_class")
    if oclass is not None and oclass not in VALID_CLASSES:
        raise ValueError(f"bad order_class {oclass!r}")
    if oclass in ("bracket", "oco", "oto"):
        legs = body.get("legs")
        if not isinstance(legs, list) or not legs:
            raise ValueError(
                f"order_class {oclass!r} requires a non-empty legs array")
        if "type" in body or "qty" in body or "ratio_qty" in body:
            raise ValueError(
                f"order_class {oclass!r} carries type/qty on the legs, "
                f"not at the top level")
        for leg in legs:
            if not isinstance(leg, dict):
                raise ValueError(f"leg must be a dict, got {type(leg)!r}")
            if leg.get("order_class") is not None:
                raise ValueError("a leg must not carry its own order_class")
            validate_order_body(leg)
        return
    if "legs" in body:
        raise ValueError("legs require order_class bracket|oco|oto")
    typ = body.get("type")
    if typ not in VALID_TYPES:
        raise ValueError(f"bad order type {typ!r}")
    if typ == "stop" and "stop_price" not in body:
        raise ValueError("stop order missing stop_price")
    if typ == "limit" and "limit_price" not in body:
        raise ValueError("limit order missing limit_price")
    if typ == "stop_limit" and not ("stop_price" in body
                                    and "limit_price" in body):
        raise ValueError("stop_limit needs stop_price and limit_price")
    if typ == "trailing_stop" and not ("trail_price" in body
                                       or "trail_percent" in body):
        raise ValueError("trailing_stop needs trail_price/trail_percent")
    if typ == "market" and ("stop_price" in body or "limit_price" in body):
        raise ValueError("market order must not carry stop/limit price")
    if body.get("side") not in ("buy", "sell"):
        raise ValueError(f"bad side {body.get('side')!r}")
    if "ratio_qty" in body:
        if float(body.get("ratio_qty") or 0) <= 0:
            raise ValueError("ratio_qty must be > 0")
    elif float(body.get("qty", 0) or 0) <= 0:
        raise ValueError("qty must be > 0")
    tif = body.get("time_in_force")
    if tif is not None and tif not in VALID_TIF:
        raise ValueError(f"bad time_in_force {tif!r}")
    if not body.get("symbol"):
        raise ValueError("symbol is required")


class FakeBroker:
    dry_run = False

    def __init__(self, positions=None, open_orders=None, orders_by_coid=None,
                 fail_symbols=(), portfolio_value=100_000.0,
                 bars=None, fail_cancels=(), cancel_polls_until_done=0,
                 unknown_cancel_code=404):
        self.positions = positions or []
        self.open_orders = open_orders or []
        self.orders_by_coid = orders_by_coid or {}
        self.fail_symbols = set(fail_symbols)
        self.fail_cancels = set(fail_cancels)
        self.cancel_polls_until_done = cancel_polls_until_done
        self.unknown_cancel_code = unknown_cancel_code
        self._pv = portfolio_value
        self.bars = bars or {}
        self.submitted = []       # list of submitted bodies
        self.replaced = []        # list of (order_id, patch)
        self.canceled = []
        self._canceled_ids = set()
        self._rep_n = 0
        self.daily_bars_calls = []  # (symbol, now)

    def get_account(self):
        return {"portfolio_value": str(self._pv)}

    def get_positions(self):
        return self.positions

    def get_orders(self, status="open", limit=500):
        return self.open_orders if status == "open" else []

    def _by_id(self, oid):
        for o in self.orders_by_coid.values():
            if o.get("id") == oid:
                return o
        return None

    def get_order_by_client_id(self, coid):
        o = self.orders_by_coid.get(coid)
        if o is None:
            # synthesize a terminal status for orders the fake already
            # canceled (mirrors "the cancel succeeded; lookup confirms")
            if coid in {c for c, o2 in self.orders_by_coid.items()
                        if o2.get("id") in self._canceled_ids}:
                return {"id": None, "status": "canceled"}
            return None
        if o.get("status") == "pending_cancel":
            if o.get("_polls_left", 0) > 0:
                o["_polls_left"] -= 1
            else:
                o["status"] = "canceled"
        return o

    def daily_bars(self, symbol, now=None):
        self.daily_bars_calls.append((symbol, now))
        return self.bars.get(symbol)

    def submit_order(self, body):
        validate_order_body(body)  # malformed payloads are REJECTED
        if body["symbol"] in self.fail_symbols:
            return {"error": "forbidden short selling (test)"}
        self.submitted.append(body)
        oid = f"ord-{len(self.submitted)}"
        self.orders_by_coid[body.get("client_order_id") or oid] = {
            "id": oid, "status": "accepted",
            "client_order_id": body.get("client_order_id")}
        return {"id": oid, "status": "accepted",
                "client_order_id": body.get("client_order_id")}

    def replace_order(self, order_id, patch):
        # models Alpaca: a replace returns a NEW order id and the OLD
        # order ends up 'replaced' (fix B)
        self._rep_n += 1
        new_id = f"ord-rep-{self._rep_n}"
        self.replaced.append((order_id, dict(patch)))
        old = self._by_id(order_id)
        if old is not None:
            old["status"] = "replaced"
        coid = patch.get("client_order_id")
        if coid:
            self.orders_by_coid[coid] = {"id": new_id, "status": "accepted",
                                         "client_order_id": coid}
        resp = {"id": new_id, "status": "accepted"}
        if coid:
            resp["client_order_id"] = coid
        resp.update(patch)
        return resp

    def cancel_order(self, order_id):
        if order_id in self.fail_cancels:
            return 500
        o = self._by_id(order_id)
        if o is None:
            return self.unknown_cancel_code  # 404/422 style
        if o.get("status") in TERMINAL:
            return 422  # Alpaca rejects cancel of a terminal order
        self.canceled.append(order_id)
        self._canceled_ids.add(order_id)
        if self.cancel_polls_until_done > 0:
            o["status"] = "pending_cancel"
            o["_polls_left"] = self.cancel_polls_until_done
        else:
            o["status"] = "canceled"
        self.open_orders = [x for x in self.open_orders
                            if isinstance(x, dict) and x.get("id") != order_id]
        return 204


# ------------------------------------------------------------ synthetic bars
# FIXED anchor date for every synthetic bar set (2026-11-03, a Tuesday).
# Tests are deterministic: the injected clock (NOW) and the bars always
# describe the same market day, whatever day the suite is run on.
ANCHOR = datetime(2026, 11, 3).date()
NOW = datetime(2026, 11, 3, 21, 30, tzinfo=timezone.utc)


def _dates(n, end=None):
    end = end or ANCHOR
    return pd.bdate_range(end=pd.Timestamp(end), periods=n)


def uptrend_bars(n=260, base=100.0, drop_last=2, drop_pct=0.06,
                 volume=2_000_000, end=None):
    """Raw bars (t,o,h,l,c,v): gentle uptrend (close>SMA200), then a sharp
    2-day drop so Wilder RSI(2) < 10."""
    d = _dates(n, end)
    closes = [base + 0.5 * i for i in range(n - drop_last)]
    for _ in range(drop_last):
        closes.append(closes[-1] * (1 - drop_pct))
    closes = np.array(closes)
    opens = np.concatenate([[closes[0]], closes[:-1]])
    return pd.DataFrame({
        "t": [ts.isoformat() + "Z" for ts in d],
        "o": opens, "h": closes * 1.005, "l": closes * 0.995,
        "c": closes, "v": [volume] * n,
    })


def spy_bars(n=260, end=None):
    d = _dates(n, end)
    closes = np.array([400.0 + 0.4 * i for i in range(n)])  # close > SMA200
    return pd.DataFrame({
        "t": [ts.isoformat() + "Z" for ts in d],
        "o": closes, "h": closes * 1.002, "l": closes * 0.998,
        "c": closes, "v": [80_000_000] * n,
    })


@pytest.fixture(scope="module")
def frames():
    sym = signals.add_indicators(uptrend_bars())
    spy = signals.add_indicators(spy_bars())
    return sym, spy


def make_args(symbols=("AMD",), **kw):
    base = dict(symbols=list(symbols), sleeve_pct=0.15, rsi2_cap=4,
                max_open=4, per_position_cap=0.25, c4_db=None)
    base.update(kw)
    return SimpleNamespace(**base)


def make_c4_db(path):
    """Create an empty C4 shadow DB with the REAL schema (fix A)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE orders (client_order_id TEXT PRIMARY KEY, symbol TEXT "
        "NOT NULL, kind TEXT NOT NULL, sleeve TEXT NOT NULL DEFAULT 'short', "
        "side TEXT NOT NULL, qty REAL NOT NULL, price REAL, ref_date TEXT "
        "NOT NULL, alpaca_order_id TEXT, status TEXT NOT NULL DEFAULT "
        "'submitted', created_ts TEXT NOT NULL)")
    con.execute(
        "CREATE TABLE positions (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "symbol TEXT NOT NULL, signal_date TEXT NOT NULL, signal_atr14 REAL "
        "NOT NULL, entry_order_coid TEXT NOT NULL, entry_date TEXT, "
        "entry_price REAL, qty REAL, status TEXT NOT NULL DEFAULT "
        "'pending_fill', exit_date TEXT, exit_price REAL, exit_reason TEXT, "
        "created_ts TEXT NOT NULL, UNIQUE(symbol, signal_date))")
    con.commit()
    con.close()
    return path


@pytest.fixture(autouse=True)
def _c4_db(tmp_path, monkeypatch):
    """Every test in this module runs against a hermetic empty C4 DB so
    the fail-loudly rule (fix A) never depends on the host filesystem."""
    db = make_c4_db(tmp_path / "c4autouse" / "live_paper.db")
    monkeypatch.setattr(runner, "DEFAULT_C4_DB", db)
    return db


@pytest.fixture(autouse=True)
def _fast_cancel_poll(monkeypatch):
    monkeypatch.setattr(runner, "_sleep_s", lambda s: None)


def setup_broker(frames, symbols=("AMD",), **kw):
    sym_df, spy_df = frames
    raw = {"AMD": uptrend_bars(), "SPY": spy_bars()}
    for s in symbols[1:]:
        raw[s] = uptrend_bars(base=80.0)
    broker = FakeBroker(bars=raw, **kw)
    bars = {s: signals.add_indicators(b) for s, b in raw.items()}
    return broker, bars


# ------------------------------------------------------------ entry signal
def test_synthetic_bars_trigger_entry(frames):
    sym_df, spy_df = frames
    assert signals.rsi2_entry_signal(sym_df, spy_df) is True


# ------------------------------------------------------------ entries (S2)
def test_entries_sleeve_and_per_position_cap(frames):
    broker, bars = setup_broker(frames)
    state = {"open": {}}
    results = {"rsi2": [], "errors": []}
    runner.process_entries(state, broker, bars, bars["SPY"], [], [],
                           [], "20261103", make_args(), 100_000.0, results, now=NOW)
    assert len(results["rsi2"]) == 1
    t = state["open"][results["rsi2"][0]["entry_id"]]
    px = t["ref_px"]
    # S2: sleeve = 100k * 15% = 15k; per-position cap 25% of sleeve = 3.75k
    assert t["qty"] * px <= 3_750 + px
    assert t["qty"] * px > 0
    assert "partial_px" not in t  # S1: no partial target anymore
    sides = [b["side"] for b in broker.submitted]
    assert sides == ["buy"]


def test_sleeve_pct_configurable(frames):
    broker, bars = setup_broker(frames)
    state = {"open": {}}
    results = {"rsi2": [], "errors": []}
    runner.process_entries(state, broker, bars, bars["SPY"], [], [],
                           [], "20261103", make_args(sleeve_pct=0.10),
                           100_000.0, results, now=NOW)
    t = state["open"][results["rsi2"][0]["entry_id"]]
    # sleeve 10k; cap 25% of sleeve = 2.5k
    assert t["entry_notional"] <= 2_500 + t["ref_px"]


def test_c4_open_positions_do_not_shrink_sleeve(_c4_db, frames):
    # S2: the subtract-C4 logic is GONE. Open C4 positions must not
    # reduce the RSI(2) sleeve.
    con = sqlite3.connect(_c4_db)
    con.execute(
        "INSERT INTO positions (symbol, signal_date, signal_atr14, "
        "entry_order_coid, entry_date, entry_price, qty, status, created_ts) "
        "VALUES ('QQQ','2026-11-01',1.0,'sl_short-QQQ-e-1','2026-11-01',"
        "100.0,50,'open','2026-11-01T21:00:00Z')")
    con.commit()
    con.close()
    assert not hasattr(runner, "c4_open_notional")  # removed entirely
    broker, bars = setup_broker(frames)
    state = {"open": {}}
    results = {"rsi2": [], "errors": []}
    runner.process_entries(state, broker, bars, bars["SPY"], [], [], [],
                           "20261103", make_args(c4_db=str(_c4_db)),
                           100_000.0, results, now=NOW)
    t = state["open"][results["rsi2"][0]["entry_id"]]
    # full 3.75k cap available — the 5k C4 position did not shrink it
    assert t["entry_notional"] > 3_750 - t["ref_px"]


def test_order_error_records_no_trade(frames):
    broker, bars = setup_broker(frames, symbols=("AMD", "NVDA"),
                                fail_symbols={"NVDA"})
    state = {"open": {}}
    results = {"rsi2": [], "errors": []}
    runner.process_entries(state, broker, bars, bars["SPY"], [], [],
                           [], "20261103",
                           make_args(symbols=("AMD", "NVDA")), 100_000.0,
                           results, now=NOW)
    assert [r["symbol"] for r in results["rsi2"]] == ["AMD"]
    assert len(results["errors"]) == 1
    assert all(t["symbol"] == "AMD" for t in state["open"].values())
    assert not any(b.get("symbol") == "NVDA" for b in broker.submitted)


def test_rerun_idempotent_pending_order_skipped(frames):
    broker, bars = setup_broker(frames)
    state = {"open": {}}
    results = {"rsi2": [], "errors": []}
    runner.process_entries(state, broker, bars, bars["SPY"], [], [],
                           [], "20261103", make_args(), 100_000.0, results, now=NOW)
    assert len(results["rsi2"]) == 1
    entry_id = results["rsi2"][0]["entry_id"]
    broker.open_orders = [{"symbol": "AMD", "side": "buy",
                           "client_order_id": entry_id}]
    state2 = {"open": {}}
    results2 = {"rsi2": [], "errors": []}
    runner.process_entries(state2, broker, bars, bars["SPY"], [],
                           broker.open_orders, [entry_id], "20261103",
                           make_args(), 100_000.0, results2, now=NOW)
    assert results2["rsi2"] == []  # skipped, no duplicate order
    assert len(broker.submitted) == 1


def test_overlap_c4_position_and_sl_short_order(frames):
    broker, bars = setup_broker(frames, symbols=("AMD", "QQQ"))
    broker.positions = [{"symbol": "QQQ", "qty": "10"}]
    broker.open_orders = [{"symbol": "AMD", "side": "buy",
                           "client_order_id": "sl_short-AMD-20261103-1"}]
    state = {"open": {}}
    results = {"rsi2": [], "errors": []}
    runner.process_entries(state, broker, bars, bars["SPY"],
                           broker.positions, broker.open_orders, [],
                           "20261103", make_args(symbols=("AMD", "QQQ")),
                           100_000.0, results, now=NOW)
    assert results["rsi2"] == []
    assert broker.submitted == []


def test_max_open_blocks_new_entries(frames):
    broker, bars = setup_broker(frames)
    state = {"open": {f"rsi2-XXX-20261101-{i}": {"symbol": f"X{i}", "entry_notional": 1}
                      for i in range(4)}}
    results = {"rsi2": [], "errors": []}
    runner.process_entries(state, broker, bars, bars["SPY"], [], [],
                           [], "20261103", make_args(max_open=4),
                           100_000.0, results, now=NOW)
    assert results["rsi2"] == []
    assert broker.submitted == []


# ------------------------------------------------------------ fake-broker validation
def test_fake_broker_rejects_malformed_payloads():
    broker = FakeBroker()
    bad = [
        {"symbol": "AMD", "qty": 10, "side": "sell", "type": "stop",
         "time_in_force": "gtc"},                          # no stop_price
        {"symbol": "AMD", "qty": 10, "side": "sell", "type": "market",
         "time_in_force": "gtc", "stop_price": 90},        # market w/ stop
        {"symbol": "AMD", "qty": 10, "side": "short", "type": "market",
         "time_in_force": "day"},                          # bad side
        {"symbol": "AMD", "qty": 0, "side": "sell", "type": "market",
         "time_in_force": "day"},                          # qty <= 0
        {"symbol": "AMD", "qty": 10, "side": "sell", "type": "stop",
         "stop_price": 90, "time_in_force": "whenever"},   # bad tif
        {"symbol": "AMD", "qty": 10, "side": "sell", "type": "quantum",
         "time_in_force": "day"},                          # bad type
        {"symbol": "AMD", "qty": 10, "side": "sell", "type": "market",
         "time_in_force": "day", "order_class": "oco"},    # oco w/o legs
        {"symbol": "AMD", "qty": 10, "side": "sell", "type": "market",
         "time_in_force": "gtc",
         "legs": [{"symbol": "AMD", "qty": 10, "side": "sell",
                   "type": "limit", "limit_price": 110,
                   "time_in_force": "gtc"}]},               # legs w/o class
    ]
    for body in bad:
        with pytest.raises(ValueError):
            broker.submit_order(body)
    assert broker.submitted == []


# ------------------------------------------------------------ dry-run
def test_dry_run_writes_nothing_but_log(frames, tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "LOG_DIR", tmp_path)
    monkeypatch.setattr(runner, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(runner, "EVENTS_PATH", tmp_path / "swing_events.jsonl")
    monkeypatch.setattr(runner, "DEFAULT_C4_DB",
                        make_c4_db(tmp_path / "c4dir" / "live_paper.db"))
    monkeypatch.setenv("ALPACA_API_KEY", "PKTEST")
    monkeypatch.setenv("ALPACA_API_SECRET", "SECTEST")
    monkeypatch.delenv("TRADING_BOT_PAPER_ONLY", raising=False)
    broker, bars = setup_broker(frames)
    broker.dry_run = True
    monkeypatch.setattr(runner, "AlpacaPaper", lambda *a, **k: broker)
    runner.run(make_args(execute=False))
    assert not (tmp_path / "state.json").exists()
    logs = [p for p in tmp_path.iterdir() if p.is_file()]
    assert all(p.name == "swing_events.jsonl" for p in logs)
    assert broker.submitted  # decisions still simulated
    assert all(b.get("client_order_id", "").startswith("rsi2-")
               for b in broker.submitted)


def test_execute_requires_paper_only_env(monkeypatch, tmp_path):
    monkeypatch.delenv("TRADING_BOT_PAPER_ONLY", raising=False)
    args = make_args(execute=True)
    with pytest.raises(SystemExit) as e:
        runner.run(args)
    assert e.value.code == 2


# ------------------------------------------------------------ bar finality (J)
def test_incomplete_daily_bar_refused_injected_clock():
    sig_day = datetime(2026, 11, 3, tzinfo=timezone.utc).date()
    before_close = datetime(2026, 11, 3, 15, 0, tzinfo=timezone.utc)
    after_close = datetime(2026, 11, 3, 21, 30, tzinfo=timezone.utc)
    assert runner.last_bar_is_final(sig_day, now=before_close) is False
    assert runner.last_bar_is_final(sig_day, now=after_close) is True
    assert runner.last_bar_is_final(sig_day - timedelta(days=1),
                                    now=before_close) is True
    assert runner.last_bar_is_final(sig_day + timedelta(days=1),
                                    now=before_close) is False


def test_run_uses_injected_clock_for_bar_finality(frames, tmp_path, monkeypatch):
    # J: with a fixed clock, entry decisions depend ONLY on that clock —
    # bars ending "today" are tradeable after 21:05 UTC and not before.
    monkeypatch.setattr(runner, "LOG_DIR", tmp_path)
    monkeypatch.setattr(runner, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(runner, "EVENTS_PATH", tmp_path / "swing_events.jsonl")
    monkeypatch.setattr(runner, "DEFAULT_C4_DB",
                        make_c4_db(tmp_path / "c4dir" / "live_paper.db"))
    monkeypatch.setenv("ALPACA_API_KEY", "PKTEST")
    monkeypatch.setenv("ALPACA_API_SECRET", "SECTEST")
    fixed_day = datetime(2026, 11, 3, tzinfo=timezone.utc).date()
    raw = {"AMD": uptrend_bars(end=fixed_day),
           "SPY": spy_bars(end=fixed_day)}
    broker = FakeBroker(bars=raw)
    monkeypatch.setattr(runner, "AlpacaPaper", lambda *a, **k: broker)

    # before the close: today's bar is NOT final -> no entry
    runner.run(make_args(execute=False),
               clock=lambda: datetime(2026, 11, 3, 15, 0, tzinfo=timezone.utc))
    assert broker.submitted == []
    # after the close: today's bar IS final -> entry submitted
    runner.run(make_args(execute=False),
               clock=lambda: datetime(2026, 11, 3, 21, 30, tzinfo=timezone.utc))
    assert [b["side"] for b in broker.submitted] == ["buy"]
    # the SAME injected clock drove the daily-bars fetch window
    assert all(n is not None for _s, n in broker.daily_bars_calls)
    assert broker.daily_bars_calls[0][1] == datetime(
        2026, 11, 3, 15, 0, tzinfo=timezone.utc)


def test_ymd_comes_from_injected_clock():
    now = datetime(2026, 11, 3, 22, 0, tzinfo=timezone.utc)
    assert runner.ymd_today(now) == "20261103"


# ------------------------------------------------------------ exits
def filled_trade(sym="AMD", qty=50, days_ago=5, stop=True):
    sym_df = signals.add_indicators(uptrend_bars())
    entry_date = str(sym_df["date"].iloc[-1 - days_ago])
    t = {
        "strategy": RSI2, "symbol": sym, "qty": qty, "n": 1, "date": "20261103",
        "ref_px": 100.0, "entry_notional": qty * 100.0, "stop_px": 90.0,
        "entry_id": make_client_order_id(RSI2, sym, "20261103", 1),
        "entry_order_id": "ord-1", "entry_filled": True, "entry_fill_px": 100.0,
        "signal_date": entry_date, "atr14": 2.0, "entry_date": entry_date,
    }
    if stop:
        t["stop_order_id"] = "stop-1"
        t["stop_client_order_id"] = make_stop_client_order_id(
            RSI2, sym, "20261103", 1)
        t["stop_gen"] = 1
    return t


def _register_stop(broker, t, status="held"):
    broker.orders_by_coid[t["stop_client_order_id"]] = {
        "id": t["stop_order_id"], "status": status}


def test_time_exit_closes_trade_and_frees_sleeve(frames):
    broker, bars = setup_broker(frames)
    t = filled_trade(days_ago=30)  # > 15 trading days ago
    _register_stop(broker, t)
    state = {"open": {t["entry_id"]: t}}
    results = {"closed": [], "errors": []}
    positions = [{"symbol": "AMD", "qty": "50"}]
    runner.process_exits(state, broker, bars, positions, results)
    assert t["entry_id"] not in state["open"]          # sleeve money freed
    sells = [b for b in broker.submitted if b["side"] == "sell"]
    assert len(sells) == 1 and sells[0]["type"] == "market"
    assert set(broker.canceled) == {"stop-1"}  # S1: only the one stop


def test_chandelier_raises_stop_never_lowers(frames):
    broker, bars = setup_broker(frames)
    t = filled_trade(days_ago=3, stop=True)
    t["stop_px"] = 1.0  # far below any chandelier
    _register_stop(broker, t)
    state = {"open": {t["entry_id"]: t}}
    results = {"closed": [], "errors": []}
    positions = [{"symbol": "AMD", "qty": "50"}]
    runner.process_exits(state, broker, bars, positions, results)
    assert len(broker.replaced) == 1
    order_id, patch = broker.replaced[0]
    assert order_id == "stop-1"
    new_px = patch["stop_price"]
    assert new_px > 1.0
    # FIX B: the PATCH carries a NEW deterministic client_order_id and the
    # state stores the NEW order id and client id — never a stale id.
    assert patch["client_order_id"] == make_stop_replace_client_order_id(
        RSI2, "AMD", "20261103", 1, 2)
    assert t["stop_px"] == new_px
    assert t["stop_order_id"] == "ord-rep-1" != "stop-1"
    assert t["stop_client_order_id"] == "rsi2-AMD-20261103-1-stop-r2"
    assert t["stop_gen"] == 2
    # a second pass with an already-high stop does not lower it
    runner.process_exits(state, broker, bars, positions, results)
    assert len(broker.replaced) == 1


def test_second_raise_uses_r3_label(frames):
    broker, bars = setup_broker(frames)
    t = filled_trade(days_ago=3, stop=True)
    t["stop_px"] = 1.0
    _register_stop(broker, t)
    state = {"open": {t["entry_id"]: t}}
    results = {"closed": [], "errors": []}
    runner.process_exits(state, broker, bars,
                         [{"symbol": "AMD", "qty": "50"}], results)
    # after the first raise, force another by dropping stop_px again
    t["stop_px"] = t["stop_px"] - 5
    runner.process_exits(state, broker, bars,
                         [{"symbol": "AMD", "qty": "50"}], results)
    assert len(broker.replaced) == 2
    assert broker.replaced[1][1]["client_order_id"] == \
        "rsi2-AMD-20261103-1-stop-r3"
    assert t["stop_gen"] == 3


def test_pending_entry_is_not_managed_yet(frames):
    broker, bars = setup_broker(frames)
    t = filled_trade(days_ago=3, stop=False)
    t["entry_filled"] = False
    state = {"open": {t["entry_id"]: t}}
    broker.orders_by_coid[t["entry_id"]] = {"id": "ord-1", "status": "new"}
    results = {"closed": [], "errors": []}
    runner.process_exits(state, broker, bars, [], results)
    assert t["entry_id"] in state["open"]
    assert broker.submitted == []


def test_canceled_entry_dropped(frames):
    broker, bars = setup_broker(frames)
    t = filled_trade(days_ago=3, stop=False)
    t["entry_filled"] = False
    state = {"open": {t["entry_id"]: t}}
    broker.orders_by_coid[t["entry_id"]] = {"id": "ord-1", "status": "expired"}
    results = {"closed": [], "errors": []}
    runner.process_exits(state, broker, bars, [], results)
    assert t["entry_id"] not in state["open"]


def test_one_bad_symbol_does_not_crash_exits(frames):
    broker, bars = setup_broker(frames)
    t = filled_trade(days_ago=3)
    _register_stop(broker, t)
    bad = filled_trade(sym="ZZZZ", days_ago=3)  # no bars for ZZZZ
    state = {"open": {t["entry_id"]: t, bad["entry_id"]: bad}}
    results = {"closed": [], "errors": []}
    runner.process_exits(state, broker, bars,
                         [{"symbol": "AMD", "qty": "50"}], results)
    assert t["entry_id"] in state["open"]  # good trade still managed


# ------------------------------------------------------------ stop-after-fill (S1)
def test_single_gtc_stop_full_qty_after_fill():
    # S1: exactly ONE plain GTC stop covering the FULL held qty; no
    # partial half-sell, no OCO bracket, ever.
    broker = FakeBroker()
    t = filled_trade(days_ago=2, stop=False)
    t["entry_filled"] = False
    state = {"open": {t["entry_id"]: t}}
    results = {"stops_placed": [], "errors": []}
    # not filled yet: nothing placed
    runner.place_stops_after_fill(state, broker,
                                  [{"symbol": "AMD", "qty": "50"}], [], results)
    assert broker.submitted == []
    # filled: one stop for the full 50 shares
    t["entry_filled"] = True
    runner.place_stops_after_fill(state, broker,
                                  [{"symbol": "AMD", "qty": "50"}], [], results)
    sells = [b for b in broker.submitted if b["side"] == "sell"]
    assert len(sells) == 1
    stop = sells[0]
    assert stop["type"] == "stop"
    assert stop["time_in_force"] == "gtc"
    assert stop["qty"] == 50                    # full position
    assert stop["stop_price"] == 90.0
    assert stop["client_order_id"].endswith("-stop")
    assert "legs" not in stop and "order_class" not in stop
    rec = state["open"][t["entry_id"]]
    assert rec["stop_order_id"] and rec["stop_gen"] == 1


def test_stop_qty_never_exceeds_room():
    # held 50 with an existing 30-share sell -> stop covers the remaining 20
    broker = FakeBroker()
    t = filled_trade(days_ago=2, stop=False)
    t["entry_filled"] = True
    state = {"open": {t["entry_id"]: t}}
    results = {"stops_placed": [], "errors": []}
    open_orders = [{"symbol": "AMD", "side": "sell", "qty": 30}]
    runner.place_stops_after_fill(state, broker,
                                  [{"symbol": "AMD", "qty": "50"}],
                                  open_orders, results)
    stops = [b for b in broker.submitted if b.get("type") == "stop"]
    assert len(stops) == 1 and stops[0]["qty"] == 20
    assert 30 + 20 == 50  # never exceeds held


def test_stop_submit_error_no_state_crash():
    broker = FakeBroker(fail_symbols={"AMD"})
    t = filled_trade(days_ago=2, stop=False)
    t["entry_filled"] = True
    state = {"open": {t["entry_id"]: t}}
    results = {"stops_placed": [], "errors": []}
    runner.place_stops_after_fill(state, broker,
                                  [{"symbol": "AMD", "qty": "50"}], [], results)
    assert results["errors"]
    rec = state["open"][t["entry_id"]]
    assert "stop_order_id" not in rec


# ------------------------------------------------------------ C4 ref-date (K)
def test_c4_skip_matches_ref_date_of_last_completed_trading_day(_c4_db):
    con = sqlite3.connect(_c4_db)
    for coid, sym, ref in [
        ("sl_short-AMD-e-1", "AMD", "2026-11-02"),
        ("sl_short-AMD-e-2", "AMD", "2026-11-03"),
        ("sl_short-QQQ-e-9", "QQQ", "2020-01-01"),
    ]:
        con.execute(
            "INSERT INTO orders (client_order_id, symbol, kind, side, qty, "
            "ref_date, created_ts) VALUES (?,?,?,?,?,?,?)",
            (coid, sym, "entry", "buy", 10, ref, ref + "T21:19:05Z"))
    con.commit()
    con.close()
    # K: run BEFORE the close on Nov 3 -> last completed trading day is
    # Nov 2 -> AMD signaled (ref_date 2026-11-02) even though C4 hasn't
    # run yet today; run order no longer matters.
    out = runner.c4_signaled_today(
        ["AMD", "QQQ", "TSLA"], db_path=_c4_db,
        now=datetime(2026, 11, 3, 15, 0, tzinfo=timezone.utc))
    assert out == {"AMD"}
    # after the close on Nov 3 -> ref_date 2026-11-03 (still AMD)
    out = runner.c4_signaled_today(
        ["AMD", "QQQ", "TSLA"], db_path=_c4_db,
        now=datetime(2026, 11, 3, 21, 30, tzinfo=timezone.utc))
    assert out == {"AMD"}
    # stale 2020 signal never matches
    assert "QQQ" not in out


def test_last_completed_trading_day_weekday_logic():
    dt = lambda *a, **k: datetime(*a, tzinfo=timezone.utc, **k)
    # after close on a Tuesday -> today
    assert runner.last_completed_trading_day(dt(2026, 11, 3, 21, 30)) \
        == dt(2026, 11, 3).date()
    # before close on a Tuesday -> Monday
    assert runner.last_completed_trading_day(dt(2026, 11, 3, 15, 0)) \
        == dt(2026, 11, 2).date()
    # Saturday -> Friday
    assert runner.last_completed_trading_day(dt(2026, 11, 7, 12, 0)) \
        == dt(2026, 11, 6).date()
    # Sunday -> Friday
    assert runner.last_completed_trading_day(dt(2026, 11, 8, 12, 0)) \
        == dt(2026, 11, 6).date()


def test_c4_db_missing_or_bad_fails_loudly(tmp_path, monkeypatch):
    # FIX A: a missing or unreadable C4 DB must ABORT — never silently
    # behave as "no C4 signal".
    monkeypatch.setattr(runner, "DEFAULT_C4_DB", tmp_path / "nope.db")
    with pytest.raises(RuntimeError, match="C4 shadow DB not found"):
        runner.c4_signaled_today(["AMD"], now=NOW)
    bad = tmp_path / "bad.db"
    bad.write_text("not a database")
    with pytest.raises(RuntimeError, match="C4 ref-date query failed"):
        runner.c4_signaled_today(["AMD"], now=NOW, db_path=bad)


def test_missing_c4_db_aborts_entries(tmp_path, monkeypatch, frames):
    monkeypatch.setattr(runner, "DEFAULT_C4_DB", tmp_path / "nope.db")
    broker, bars = setup_broker(frames)
    state = {"open": {}}
    results = {"rsi2": [], "errors": []}
    with pytest.raises(RuntimeError):
        runner.process_entries(state, broker, bars, bars["SPY"], [], [],
                               [], "20261103", make_args(), 100_000.0, results, now=NOW)
    assert broker.submitted == []  # nothing traded on a guessed skip set


# ------------------------------------------------------------ dotted symbols
def test_dotted_symbol_label_flow():
    coid = make_client_order_id(RSI2, "BRK.B", "20261103", 1)
    assert coid == "rsi2-BRK.B-20261103-1"
    assert len(make_client_order_id(RSI2, "BF.B", "20261103", 123)) <= 48


# ------------------------------------------- round-3 fixes C, D, F, G
def test_time_exit_pending_cancel_waits_then_sells(frames):
    # FIX C: cancel returns 204 but the order sits in pending_cancel for a
    # few polls; the runner must WAIT and only sell once canceled is
    # CONFIRMED.
    broker, bars = setup_broker(frames, cancel_polls_until_done=2)
    t = filled_trade(days_ago=30)
    _register_stop(broker, t, status="held")
    state = {"open": {t["entry_id"]: t}}
    results = {"closed": [], "errors": []}
    positions = [{"symbol": "AMD", "qty": "50"}]
    runner.process_exits(state, broker, bars, positions, results)
    sells = [b for b in broker.submitted if b["side"] == "sell"]
    assert len(sells) == 1 and sells[0]["type"] == "market"
    assert t["entry_id"] not in state["open"]


def test_time_exit_pending_cancel_never_confirmed_keeps_stop(frames):
    # FIX C: pending_cancel that never resolves -> NO market sell; the
    # stop stays in place and the trade retries next run.
    broker, bars = setup_broker(frames, cancel_polls_until_done=99)
    t = filled_trade(days_ago=30)
    _register_stop(broker, t, status="held")
    state = {"open": {t["entry_id"]: t}}
    results = {"closed": [], "errors": []}
    positions = [{"symbol": "AMD", "qty": "50"}]
    runner.process_exits(state, broker, bars, positions, results)
    assert [b for b in broker.submitted if b["side"] == "sell"] == []
    assert t["entry_id"] in state["open"]
    assert any("unconfirmed" in e["error"] for e in results["errors"])


def test_time_exit_cancel_failure_skips_market_sell(frames):
    # FIX C: if the stop cannot be canceled (hard failure), NO market
    # sell is submitted (no double orders); retry next run.
    broker, bars = setup_broker(frames)
    t = filled_trade(days_ago=30)
    _register_stop(broker, t)
    broker.fail_cancels = {"stop-1"}
    state = {"open": {t["entry_id"]: t}}
    results = {"closed": [], "errors": []}
    positions = [{"symbol": "AMD", "qty": "50"}]
    runner.process_exits(state, broker, bars, positions, results)
    assert t["entry_id"] in state["open"]        # trade stays open
    assert [b for b in broker.submitted if b["side"] == "sell"] == []
    assert results["errors"]


def test_time_exit_already_canceled_422_is_success(frames):
    # FIX C: the stop is ALREADY canceled — DELETE returns 422 and the
    # status confirms terminal: that is SUCCESS, proceed to market sell.
    broker, bars = setup_broker(frames)
    t = filled_trade(days_ago=30)
    _register_stop(broker, t, status="canceled")
    state = {"open": {t["entry_id"]: t}}
    results = {"closed": [], "errors": []}
    positions = [{"symbol": "AMD", "qty": "50"}]
    runner.process_exits(state, broker, bars, positions, results)
    sells = [b for b in broker.submitted if b["side"] == "sell"]
    assert len(sells) == 1
    assert t["entry_id"] not in state["open"]


def test_time_exit_unknown_stop_404_not_treated_as_success(frames):
    # FIX C: a 404 with NO confirmable status is NOT success — no sell.
    broker, bars = setup_broker(frames, unknown_cancel_code=404)
    t = filled_trade(days_ago=30)
    t["stop_client_order_id"] = "rsi2-AMD-20261103-1-stop-gone"  # unlookupable
    state = {"open": {t["entry_id"]: t}}
    results = {"closed": [], "errors": []}
    positions = [{"symbol": "AMD", "qty": "50"}]
    runner.process_exits(state, broker, bars, positions, results)
    assert [b for b in broker.submitted if b["side"] == "sell"] == []
    assert t["entry_id"] in state["open"]


def test_stop_filled_detected_via_current_stop_id(frames):
    # FIX D + B: after a raise, stop_filled must be detected through the
    # CURRENT (replaced) stop client id, not the stale original one.
    broker, bars = setup_broker(frames)
    t = filled_trade(days_ago=3)
    t["stop_order_id"] = "ord-rep-1"
    t["stop_client_order_id"] = "rsi2-AMD-20261103-1-stop-r2"
    t["stop_gen"] = 2
    _register_stop(broker, t, status="filled")
    state = {"open": {t["entry_id"]: t}}
    results = {"closed": [], "errors": []}
    runner.process_exits(state, broker, bars, [], results)  # no position
    assert t["entry_id"] not in state["open"]
    assert results["closed"][0]["reason"] == "stop_filled"


def test_position_gone_cancels_leftover_rsi2_sells(frames):
    # FIX D: position gone -> every leftover rsi2 sell order for the
    # symbol is canceled (non-rsi2 orders are left alone).
    broker, bars = setup_broker(frames)
    t = filled_trade(days_ago=3)
    _register_stop(broker, t, status="canceled")  # stop already gone
    state = {"open": {t["entry_id"]: t}}
    results = {"closed": [], "errors": []}
    leftover_1 = {"id": "lo-1", "symbol": "AMD", "side": "sell", "qty": 10,
                  "client_order_id": "rsi2-AMD-20261103-1-stop-r2"}
    leftover_2 = {"id": "lo-2", "symbol": "AMD", "side": "sell", "qty": 5,
                  "client_order_id": "rsi2-AMD-20261103-1-stop"}
    foreign = {"id": "fx-1", "symbol": "AMD", "side": "sell", "qty": 20,
               "client_order_id": "sl_short-AMD-20261103-1"}
    broker.orders_by_coid[leftover_1["client_order_id"]] = {
        "id": "lo-1", "status": "held"}
    broker.orders_by_coid[leftover_2["client_order_id"]] = {
        "id": "lo-2", "status": "held"}
    broker.open_orders = [leftover_1, leftover_2, foreign]
    runner.process_exits(state, broker, bars, [], results,
                         open_orders=broker.open_orders)
    assert t["entry_id"] not in state["open"]
    assert set(broker.canceled) == {"lo-1", "lo-2"}  # foreign untouched
    assert results["closed"][0]["reason"] == "position_gone"


def test_stops_reanchored_to_actual_fill_price(frames):
    # FIX F: initial stop recomputes from the ACTUAL fill price (frozen
    # formula, signal-day ATR14), not the signal close.
    broker, bars = setup_broker(frames)
    sym_df = bars["AMD"]
    entry_date = str(sym_df["date"].iloc[-6])
    t = filled_trade(days_ago=5, stop=False)
    t["entry_filled"] = False
    t["atr14"] = 2.0
    t["ref_px"] = 100.0            # signal close
    t["stop_px"] = 96.0            # from signal close
    t["entry_date"] = entry_date
    state = {"open": {t["entry_id"]: t}}
    results = {"closed": [], "errors": []}
    broker.orders_by_coid[t["entry_id"]] = {
        "id": "ord-9", "status": "filled", "filled_avg_price": "105.0",
        "filled_at": "2026-11-03T15:30:00Z"}
    runner.process_exits(state, broker, bars,
                         [{"symbol": "AMD", "qty": str(t["qty"])}], results)
    # stop = 105 - 2.0*2.0 = 101
    assert t["entry_fill_px"] == 105.0
    assert t["stop_px"] == 101.0
    assert "partial_px" not in t                    # S1: no partial target
    assert t["entry_notional"] == t["qty"] * 105.0
    # FIX G: entry date is the FILL date, not the signal date
    assert t["entry_date"] == "2026-11-03"


# ------------------------------------------- round-3: S1, S2, B, C, D, J
def test_stale_stop_id_is_never_used_after_a_raise(frames):
    # fix B (stale-id case): after a chandelier raise the ORIGINAL order
    # id is dead. A later time exit must cancel the NEW id and must
    # never touch the stale one.
    broker, bars = setup_broker(frames)
    t = filled_trade(days_ago=5)
    _register_stop(broker, t)
    state = {"open": {t["entry_id"]: t}}
    results = {"closed": [], "errors": []}
    positions = [{"symbol": "AMD", "qty": "50"}]
    t["stop_px"] = 1.0
    runner.process_exits(state, broker, bars, positions, results)
    assert len(broker.replaced) == 1
    assert t["stop_order_id"] == "ord-rep-1"
    assert t["stop_client_order_id"] == "rsi2-AMD-20261103-1-stop-r2"
    # the old order is dead
    assert broker.orders_by_coid["rsi2-AMD-20261103-1-stop"]["status"] == \
        "replaced"
    # now force the 15-day time exit
    t["entry_date"] = str(bars["AMD"]["date"].iloc[-31])
    runner.process_exits(state, broker, bars, positions, results)
    assert broker.canceled == ["ord-rep-1"]      # the NEW id only
    assert "stop-1" not in broker.canceled       # never the stale id
    sells = [b for b in broker.submitted if b["side"] == "sell"]
    assert len(sells) == 1 and sells[0]["type"] == "market"
    assert t["entry_id"] not in state["open"]


def test_replaced_stop_is_terminal_for_cancel(frames):
    # fix C: a stop that was already 'replaced' needs no cancel at all —
    # it is handled.
    broker, bars = setup_broker(frames)
    t = filled_trade(days_ago=30)
    _register_stop(broker, t, status="replaced")
    state = {"open": {t["entry_id"]: t}}
    results = {"closed": [], "errors": []}
    runner.process_exits(state, broker, bars,
                         [{"symbol": "AMD", "qty": "50"}], results)
    sells = [b for b in broker.submitted if b["side"] == "sell"]
    assert len(sells) == 1
    assert t["entry_id"] not in state["open"]


def test_run_never_sends_oco_partial_or_class_orders(frames, tmp_path,
                                                      monkeypatch):
    # S1: no OCO bracket, no partial half-sell, no order_class — ever.
    monkeypatch.setattr(runner, "LOG_DIR", tmp_path)
    monkeypatch.setattr(runner, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(runner, "EVENTS_PATH", tmp_path / "swing_events.jsonl")
    monkeypatch.setattr(runner, "DEFAULT_C4_DB",
                        make_c4_db(tmp_path / "c4dir" / "live_paper.db"))
    monkeypatch.setenv("ALPACA_API_KEY", "PKTEST")
    monkeypatch.setenv("ALPACA_API_SECRET", "SECTEST")
    broker, bars = setup_broker(frames)
    monkeypatch.setattr(runner, "AlpacaPaper", lambda *a, **k: broker)
    runner.run(make_args(execute=False), clock=lambda: NOW)
    assert broker.submitted
    for body in broker.submitted:
        assert "legs" not in body
        assert "order_class" not in body
        assert body["type"] in ("market", "stop")
        assert body.get("client_order_id", "").startswith("rsi2-")
        assert "-stop-p" not in body.get("client_order_id", "")


def test_sleeve_caps_four_positions_at_25pct_each(frames):
    # S2: max 4 open positions, each at most 25% of the 15% sleeve; the
    # 5th candidate is refused and the total never exceeds the sleeve.
    syms = ("AMD", "NVDA", "TSLA", "META", "INTC")
    broker, bars = setup_broker(frames, symbols=syms)
    state = {"open": {}}
    results = {"rsi2": [], "errors": []}
    runner.process_entries(state, broker, bars, bars["SPY"], [], [], [],
                           "20261103", make_args(symbols=syms),
                           100_000.0, results, now=NOW)
    assert len(results["rsi2"]) == 4           # max 4 open positions
    sleeve = 100_000.0 * 0.15
    total = 0.0
    for r in results["rsi2"]:
        t = state["open"][r["entry_id"]]
        assert t["entry_notional"] <= sleeve * 0.25 + t["ref_px"]
        total += t["entry_notional"]
    assert total <= sleeve + 1e-6              # never more than the sleeve


def test_run_cancels_leftover_rsi2_sell_orders_when_position_gone(
        frames, tmp_path, monkeypatch):
    # fix D at run() level: the open-order list reaches process_exits, so
    # a leftover rsi2 sell order is canceled when the position is gone.
    monkeypatch.setattr(runner, "LOG_DIR", tmp_path)
    monkeypatch.setattr(runner, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(runner, "EVENTS_PATH", tmp_path / "swing_events.jsonl")
    monkeypatch.setattr(runner, "DEFAULT_C4_DB",
                        make_c4_db(tmp_path / "c4dir" / "live_paper.db"))
    monkeypatch.setenv("ALPACA_API_KEY", "PKTEST")
    monkeypatch.setenv("ALPACA_API_SECRET", "SECTEST")
    broker, bars = setup_broker(frames, symbols=("AMD", "NVDA"))
    t = filled_trade(sym="NVDA", days_ago=3)
    _register_stop(broker, t, status="canceled")
    leftover = {"id": "lo-9", "symbol": "NVDA", "side": "sell", "qty": 50,
                "client_order_id": "rsi2-NVDA-20261103-1-stop-r2"}
    broker.orders_by_coid[leftover["client_order_id"]] = {
        "id": "lo-9", "status": "held"}
    broker.open_orders = [leftover]
    monkeypatch.setattr(runner, "AlpacaPaper", lambda *a, **k: broker)
    state = {"open": {t["entry_id"]: t}}
    (tmp_path / "state.json").write_text(json.dumps(state))
    runner.run(make_args(symbols=("AMD",), execute=False), clock=lambda: NOW)
    assert "lo-9" in broker.canceled


def test_real_clock_reads_confined_to_clock_helpers():
    # fix J: a real wall-clock read may only happen in real_clock()
    # (the default clock) and _log_now() (log timestamps). Every other
    # function must take the injected clock.
    import ast
    tree = ast.parse((HERE / "runner.py").read_text())
    allowed = {"real_clock", "_log_now"}
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for sub in ast.walk(node):
            if (isinstance(sub, ast.Call)
                    and isinstance(sub.func, ast.Attribute)
                    and sub.func.attr == "now"
                    and isinstance(sub.func.value, ast.Name)
                    and sub.func.value.id == "datetime"
                    and node.name not in allowed):
                offenders.append((node.name, sub.lineno))
    assert offenders == []


def test_logic_functions_require_an_injected_clock():
    import inspect
    for fn in (runner.ymd_today, runner.last_bar_is_final,
               runner.process_entries, runner.c4_signaled_today):
        param = inspect.signature(fn).parameters["now"]
        assert param.default is inspect.Parameter.empty, fn.__name__
    daily = inspect.signature(runner.AlpacaPaper.daily_bars).parameters["now"]
    assert daily.default is inspect.Parameter.empty


def test_run_with_injected_clock_never_touches_the_real_clock(
        frames, tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "LOG_DIR", tmp_path)
    monkeypatch.setattr(runner, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(runner, "EVENTS_PATH", tmp_path / "swing_events.jsonl")
    monkeypatch.setattr(runner, "DEFAULT_C4_DB",
                        make_c4_db(tmp_path / "c4dir" / "live_paper.db"))
    monkeypatch.setenv("ALPACA_API_KEY", "PKTEST")
    monkeypatch.setenv("ALPACA_API_SECRET", "SECTEST")
    broker, bars = setup_broker(frames)
    monkeypatch.setattr(runner, "AlpacaPaper", lambda *a, **k: broker)

    def boom():
        raise AssertionError("the real clock must not be consulted")

    monkeypatch.setattr(runner, "real_clock", boom)
    runner.run(make_args(execute=False), clock=lambda: NOW)
    assert broker.submitted  # the run still worked, on the injected clock


def test_s1_partial_and_oco_code_is_gone_from_the_module():
    import rules
    import signals
    src = (HERE / "runner.py").read_text()
    assert "make_partial_client_order_id" not in src
    assert "make_oco_stop_client_order_id" not in src
    assert "partial_px" not in src
    assert '"order_class"' not in src
    assert '"legs"' not in src
    assert not hasattr(rules, "make_partial_client_order_id")
    assert not hasattr(rules, "make_oco_stop_client_order_id")
    assert not hasattr(signals, "RSI2_PARTIAL_R")


def test_time_exit_sell_uses_a_parseable_label(frames):
    # The time-exit market sell carries a label that report_pnl can
    # attribute (fix I): rsi2-<SYM>-<YYYYMMDD>-<n>-timeexit
    broker, bars = setup_broker(frames)
    t = filled_trade(days_ago=30)
    _register_stop(broker, t)
    state = {"open": {t["entry_id"]: t}}
    results = {"closed": [], "errors": []}
    runner.process_exits(state, broker, bars,
                         [{"symbol": "AMD", "qty": "50"}], results)
    sells = [b for b in broker.submitted if b["side"] == "sell"]
    assert len(sells) == 1
    coid = sells[0]["client_order_id"]
    assert coid == "rsi2-AMD-20261103-1-timeexit"
    from rules import parse_label
    assert parse_label(coid)["strategy"] == RSI2
