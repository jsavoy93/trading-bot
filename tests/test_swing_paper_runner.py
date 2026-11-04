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
(order_class/type/qty/side/time_in_force and, for bracket/oco/oto,
take_profit/stop_loss OBJECTS — never a `legs` array) and validates
PATCH replace bodies — malformed requests are REJECTED, not accepted
silently.

Round 4 (PR #135) covers Josh's blockers of 2026-11-04:
  (a) stuck pending_cancel: the cancel poll window is EXTENDED; a cancel
      that completes after the bounded window still leads to the sell
  (b) a stop rejected because price gapped to/below it: sell at market
      (-stopgap) when current <= planned; otherwise re-price and re-place
  (c) a partially filled then canceled entry keeps the FILLED shares
      tracked and puts a stop on them
  (d) a rejected time-exit market sell (stop already canceled) triggers
      an immediate fresh GTC stop re-place
  (f) an unconfirmed cleanup cancel keeps the trade tracked
  (g) exits/trail raises consume only FINISHED daily bars
  (h) fake broker matches the Alpaca v2 docs (take_profit/stop_loss
      objects; PATCH replace body validation)
  (e) --execute runs are refused before 21:15 UTC (C4 timer contract)
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
                   make_stop_replace_client_order_id,
                   make_stopgap_client_order_id)


# ------------------------------------------------------------ fake broker
VALID_TYPES = {"market", "limit", "stop", "stop_limit", "trailing_stop"}
VALID_CLASSES = {"simple", "bracket", "oco", "oto"}
VALID_TIF = {"day", "gtc", "opg", "cls", "ioc", "fok"}
TERMINAL = ("canceled", "expired", "filled")


def validate_order_body(body: dict) -> None:
    """Reject malformed order payloads per the Alpaca v2 order docs.

    Alpaca order docs (docs.alpaca.markets/docs/orders-at-alpaca):
      * `order_class` is simple (default) | bracket | oco | oto.
      * bracket/oco/oto carry the top-level entry order (symbol, side,
        qty, type, time_in_force) plus `stop_loss` and/or `take_profit`
        OBJECTS — NOT a `legs` array (the v2 API has no legs field;
        bracket = entry + stop_loss (+ optional take_profit);
        oco = take_profit AND stop_loss; oto = exactly one of them).
      * stop_loss: {"stop_price": X} (optional "limit_price" makes the
        stop a stop-limit); take_profit: {"limit_price": Y}.
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
    if "legs" in body:
        raise ValueError("Alpaca v2 has no `legs` field: bracket/oco/oto "
                         "orders use take_profit/stop_loss objects")
    typ = body.get("type")
    if typ not in VALID_TYPES:
        raise ValueError(f"bad order type {typ!r}")
    if typ == "market" and ("stop_price" in body or "limit_price" in body):
        raise ValueError("market order must not carry stop/limit price")
    _validate_simple_price_fields(body)
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
    if oclass in ("bracket", "oco", "oto"):
        tp, sl = body.get("take_profit"), body.get("stop_loss")
        for name, obj in (("take_profit", tp), ("stop_loss", sl)):
            if obj is None:
                continue
            if not isinstance(obj, dict):
                raise ValueError(f"{name} must be an object, got {type(obj)!r}")
            if "limit_price" in obj and float(obj["limit_price"] or 0) <= 0:
                raise ValueError(f"{name}.limit_price must be > 0")
            if "stop_price" in obj and float(obj["stop_price"] or 0) <= 0:
                raise ValueError(f"{name}.stop_price must be > 0")
            if name == "take_profit" and "limit_price" not in obj:
                raise ValueError("take_profit requires limit_price")
            if name == "stop_loss" and "stop_price" not in obj:
                raise ValueError("stop_loss requires stop_price")
        if oclass == "bracket" and sl is None:
            raise ValueError("bracket requires a stop_loss object")
        if oclass == "oco" and (tp is None or sl is None):
            raise ValueError("oco requires take_profit AND stop_loss objects")
        if oclass == "oto" and not ((tp is None) != (sl is None)):
            raise ValueError("oto requires exactly one of take_profit / "
                             "stop_loss")


def _validate_simple_price_fields(body: dict) -> None:
    typ = body.get("type")
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


# PATCH /v2/orders/{id} replace bodies per the Alpaca v2 docs: only
# these fields may be patched; client_order_id is additionally accepted
# here because the runner's stop raises deliberately tag the
# replacement with a fresh deterministic id (fix B).
REPLACE_FIELDS = {"qty", "time_in_force", "limit_price", "stop_price",
                  "trail_price", "trail_percent", "client_order_id"}


def validate_replace_patch(patch: dict) -> None:
    if not isinstance(patch, dict):
        raise ValueError(f"replace body must be a dict, got {type(patch)!r}")
    for k, v in patch.items():
        if k not in REPLACE_FIELDS:
            raise ValueError(f"field {k!r} may not be patched")
        if k == "qty" and float(v or 0) <= 0:
            raise ValueError("patched qty must be > 0")
        if k in ("limit_price", "stop_price", "trail_price") \
                and float(v or 0) <= 0:
            raise ValueError(f"patched {k} must be > 0")
        if k == "time_in_force" and v not in VALID_TIF:
            raise ValueError(f"bad time_in_force {v!r}")
        if k == "trail_percent" and not (0 < float(v) <= 100):
            raise ValueError("trail_percent must be within (0, 100]")
        if k == "client_order_id":
            s = str(v)
            if not s or len(s) > 48:
                raise ValueError("client_order_id must be 1-48 chars")


class FakeBroker:
    dry_run = False

    def __init__(self, positions=None, open_orders=None, orders_by_coid=None,
                 fail_symbols=(), portfolio_value=100_000.0,
                 bars=None, fail_cancels=(), cancel_polls_until_done=0,
                 unknown_cancel_code=404, reject_stop_above=None,
                 fail_stop_first=0, fail_market_sells=False):
        self.positions = positions or []
        self.open_orders = open_orders or []
        self.orders_by_coid = orders_by_coid or {}
        self.fail_symbols = set(fail_symbols)
        self.fail_cancels = set(fail_cancels)
        self.cancel_polls_until_done = cancel_polls_until_done
        self.unknown_cancel_code = unknown_cancel_code
        self.reject_stop_above = reject_stop_above   # (b): reject stops
        self._stop_fail_remaining = int(fail_stop_first)
        self.fail_market_sells = fail_market_sells   # (d): reject sells
        self._pv = portfolio_value
        self.bars = bars or {}
        self.submitted = []       # list of submitted bodies
        # ROUND-6: every body the runner ASKED the broker to submit,
        # including the ones the fake rejects. A reused
        # client_order_id is rejected by Alpaca, so the labels that were
        # ATTEMPTED must all be unique.
        self.submit_attempts = []
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
        self.submit_attempts.append(dict(body))
        if body["symbol"] in self.fail_symbols:
            return {"error": "forbidden short selling (test)"}
        if (self.fail_market_sells and body.get("side") == "sell"
                and body.get("type") == "market"):
            return {"error": "market sell rejected (test)"}
        if body.get("type") == "stop":
            if (self.reject_stop_above is not None
                    and float(body.get("stop_price", 0) or 0)
                    >= self.reject_stop_above):
                return {"error": "stop price must be below the current "
                                "price (test gap rejection)"}
            if self._stop_fail_remaining > 0:
                self._stop_fail_remaining -= 1
                return {"error": "stop rejected (transient test rejection)"}
        self.submitted.append(body)
        oid = f"ord-{len(self.submitted)}"
        self.orders_by_coid[body.get("client_order_id") or oid] = {
            "id": oid, "status": "accepted",
            "client_order_id": body.get("client_order_id")}
        return {"id": oid, "status": "accepted",
                "client_order_id": body.get("client_order_id")}

    def replace_order(self, order_id, patch):
        # models Alpaca: a replace returns a NEW order id and the OLD
        # order ends up 'replaced' (fix B). Round-4 (h): the PATCH body
        # is validated like the real API would.
        validate_replace_patch(patch)
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
         "time_in_force": "day", "order_class": "oco"},    # oco w/o tp/sl
        {"symbol": "AMD", "qty": 10, "side": "sell", "type": "market",
         "time_in_force": "gtc",
         "legs": [{"symbol": "AMD", "qty": 10, "side": "sell",
                   "type": "limit", "limit_price": 110,
                   "time_in_force": "gtc"}]},               # legs (v2 has none)
        {"symbol": "AMD", "qty": 10, "side": "buy", "type": "market",
         "time_in_force": "day", "order_class": "bracket",
         "take_profit": {"limit_price": 110}},               # bracket w/o stop_loss
        {"symbol": "AMD", "qty": 10, "side": "buy", "type": "market",
         "time_in_force": "day", "order_class": "bracket",
         "stop_loss": {"limit_price": 90}},                  # stop_loss w/o stop_price
    ]
    for body in bad:
        with pytest.raises(ValueError):
            broker.submit_order(body)
    assert broker.submitted == []


def test_fake_broker_accepts_v2_bracket_oco_objects():
    # round-4 (h): v2-style bracket/oco bodies (take_profit/stop_loss
    # OBJECTS, no legs) validate cleanly.
    broker = FakeBroker()
    broker.submit_order({
        "symbol": "AMD", "qty": 10, "side": "buy", "type": "market",
        "time_in_force": "day", "order_class": "bracket",
        "stop_loss": {"stop_price": 90},
        "take_profit": {"limit_price": 110}})
    broker.submit_order({
        "symbol": "AMD", "qty": 10, "side": "sell", "type": "limit",
        "limit_price": 110, "time_in_force": "gtc", "order_class": "oco",
        "stop_loss": {"stop_price": 90},
        "take_profit": {"limit_price": 110}})
    assert len(broker.submitted) == 2


def test_fake_broker_validates_replace_patch_bodies():
    # round-4 (h): PATCH replace bodies are validated like the real API.
    broker = FakeBroker()
    with pytest.raises(ValueError):     # unknown field
        broker.replace_order("o1", {"symbol": "AMD"})
    with pytest.raises(ValueError):     # qty <= 0
        broker.replace_order("o1", {"qty": 0})
    with pytest.raises(ValueError):     # stop_price <= 0
        broker.replace_order("o1", {"stop_price": -5})
    with pytest.raises(ValueError):     # bad tif
        broker.replace_order("o1", {"time_in_force": "whenever"})
    with pytest.raises(ValueError):     # client_order_id too long
        broker.replace_order("o1", {"client_order_id": "x" * 49})
    assert broker.replaced == []
    broker.replace_order("o1", {"stop_price": 95.5,
                                 "client_order_id": "rsi2-AMD-20261103-1-stop-r2"})
    assert len(broker.replaced) == 1


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


# ------------------------------------------- round-4 fixes (a)-(g)
def test_time_exit_cancel_completes_after_bounded_poll_window(frames):
    # blocker (a): the cancel completes AFTER the original bounded poll
    # window (4 attempts). The runner must KEEP polling (extended window)
    # and, once canceled is confirmed, place the market sell — never
    # leave a position believing a stop is live when it is not.
    broker, bars = setup_broker(frames, cancel_polls_until_done=8)
    t = filled_trade(days_ago=30)
    _register_stop(broker, t, status="held")
    state = {"open": {t["entry_id"]: t}}
    results = {"closed": [], "errors": []}
    positions = [{"symbol": "AMD", "qty": "50"}]
    runner.process_exits(state, broker, bars, positions, results)
    sells = [b for b in broker.submitted if b["side"] == "sell"]
    assert len(sells) == 1 and sells[0]["type"] == "market"
    assert t["entry_id"] not in state["open"]
    assert results["errors"] == []


def test_time_exit_cancel_never_completes_keeps_stop(frames):
    # blocker (a), negative case: even the extended window cannot confirm
    # the cancel — the stop is treated as still live: NO market sell, the
    # trade stays tracked and the time exit retries next run.
    broker, bars = setup_broker(frames, cancel_polls_until_done=10_000)
    t = filled_trade(days_ago=30)
    _register_stop(broker, t, status="held")
    state = {"open": {t["entry_id"]: t}}
    results = {"closed": [], "errors": []}
    positions = [{"symbol": "AMD", "qty": "50"}]
    runner.process_exits(state, broker, bars, positions, results)
    assert [b for b in broker.submitted if b["side"] == "sell"] == []
    assert t["entry_id"] in state["open"]
    assert any("unconfirmed" in e["error"] for e in results["errors"])


def test_time_exit_sell_rejected_replaces_stop_immediately(frames):
    # blocker (d): the stop was CONFIRMED canceled, then the time-exit
    # market sell is rejected. A fresh GTC stop must be re-placed at once;
    # the position is never left naked.
    broker, bars = setup_broker(frames, fail_market_sells=True)
    t = filled_trade(days_ago=30)
    _register_stop(broker, t, status="held")
    state = {"open": {t["entry_id"]: t}}
    results = {"closed": [], "errors": []}
    positions = [{"symbol": "AMD", "qty": "50"}]
    runner.process_exits(state, broker, bars, positions, results)
    assert t["entry_id"] in state["open"]      # trade NOT closed
    stops = [b for b in broker.submitted if b.get("type") == "stop"]
    assert len(stops) == 1                     # the re-placed GTC stop
    assert stops[0]["time_in_force"] == "gtc"
    assert stops[0]["qty"] == 50
    assert stops[0]["stop_price"] == 90.0
    assert t["stop_order_id"] == "ord-1"       # fresh id stored (the
    # failed sell never got an id; the re-placed stop is the 1st live one)
    assert t["stop_gen"] == 2
    assert any("re-placed" in e["error"] or "UNPROTECTED" in e["error"]
               for e in results["errors"])


def test_stop_gap_below_planned_sells_at_market(frames):
    # blocker (b): the stop is rejected because the price gapped to/below
    # the planned stop. Current (finished) close <= planned stop -> the
    # shares are sold at MARKET with a -stopgap label.
    # ROUND-5: the trade is NOT closed on submit — it stays open until
    # the sell is CONFIRMED filled.
    broker, bars = setup_broker(frames)
    cur = float(bars["AMD"]["close"].iloc[-1])
    broker.reject_stop_above = cur           # gap: stop above market
    t = filled_trade(days_ago=2, stop=False)
    t["stop_px"] = round(cur + 10.0, 2)        # planned above market -> gap
    t["entry_filled"] = True
    state = {"open": {t["entry_id"]: t}}
    results = {"stops_placed": [], "closed": [], "errors": []}
    runner.place_stops_after_fill(
        state, broker, [{"symbol": "AMD", "qty": "50"}], [], results,
        bars=bars, now=NOW)
    gap = [b for b in broker.submitted if b.get("type") == "market"]
    assert len(gap) == 1 and gap[0]["side"] == "sell"
    assert gap[0]["client_order_id"] == "rsi2-AMD-20261103-1-stopgap"
    # round-5: the trade stays OPEN until the sell is confirmed filled
    assert t["entry_id"] in state["open"]
    assert results["closed"] == []
    assert t["stopgap_pending"]["client_order_id"] == \
        "rsi2-AMD-20261103-1-stopgap"


def test_stopgap_sell_confirmed_filled_closes_trade(frames):
    # round-5: the -stopgap market sell is CONFIRMED filled -> the trade
    # is closed (reason stop_gap_market_sell) and the sleeve frees up.
    broker, bars = setup_broker(frames)
    t = filled_trade(days_ago=2, stop=False)
    t["entry_filled"] = True
    t["stopgap_client_order_id"] = "rsi2-AMD-20261103-1-stopgap"
    t["stopgap_gen"] = 1
    t["stopgap_pending"] = {"client_order_id": "rsi2-AMD-20261103-1-stopgap",
                            "qty": 50, "submitted_reason": "stop_gap"}
    state = {"open": {t["entry_id"]: t}}
    results = {"stops_placed": [], "closed": [], "errors": []}
    broker.orders_by_coid["rsi2-AMD-20261103-1-stopgap"] = {
        "id": "ord-gap", "status": "filled"}
    runner.reconcile_stops(state, broker, [{"symbol": "AMD", "qty": "50"}],
                           bars, results, now=NOW)
    assert t["entry_id"] not in state["open"]
    assert results["closed"][0]["reason"] == "stop_gap_market_sell"


def test_stopgap_sell_canceled_protects_shares_again(frames):
    # round-5: the -stopgap market sell was CANCELED -> the shares must
    # be protected again (fresh GTC stop), and the trade stays open.
    broker, bars = setup_broker(frames)
    t = filled_trade(days_ago=2, stop=False)
    t["entry_filled"] = True
    t["stop_px"] = 90.0
    t["stopgap_client_order_id"] = "rsi2-AMD-20261103-1-stopgap"
    t["stopgap_gen"] = 1
    t["stopgap_pending"] = {"client_order_id": "rsi2-AMD-20261103-1-stopgap",
                            "qty": 50, "submitted_reason": "stop_gap"}
    state = {"open": {t["entry_id"]: t}}
    results = {"stops_placed": [], "closed": [], "errors": []}
    broker.orders_by_coid["rsi2-AMD-20261103-1-stopgap"] = {
        "id": "ord-gap", "status": "canceled"}
    runner.reconcile_stops(state, broker, [{"symbol": "AMD", "qty": "50"}],
                           bars, results, now=NOW)
    assert t["entry_id"] in state["open"]          # NOT closed
    stops = [b for b in broker.submitted if b.get("type") == "stop"]
    assert len(stops) == 1
    assert stops[0]["time_in_force"] == "gtc"
    assert stops[0]["qty"] == 50
    assert stops[0]["stop_price"] == 90.0
    assert t["stop_order_id"] == "ord-1"
    assert not t.get("stopgap_pending")
    assert any("re-protected" in e["error"] for e in results["errors"])


def test_stopgap_sell_rejected_protects_shares_again(frames):
    # round-5: a REJECTED -stopgap submission is handled like a canceled
    # one — the trade stays open and the shares get protected again.
    broker, bars = setup_broker(frames)
    t = filled_trade(days_ago=2, stop=False)
    t["entry_filled"] = True
    t["stop_px"] = 90.0
    t["stopgap_client_order_id"] = "rsi2-AMD-20261103-1-stopgap"
    t["stopgap_gen"] = 1
    t["stopgap_pending"] = {"client_order_id": "rsi2-AMD-20261103-1-stopgap",
                            "qty": 50, "submitted_reason": "stop_gap"}
    state = {"open": {t["entry_id"]: t}}
    results = {"stops_placed": [], "closed": [], "errors": []}
    broker.orders_by_coid["rsi2-AMD-20261103-1-stopgap"] = {
        "id": "ord-gap", "status": "rejected"}
    runner.reconcile_stops(state, broker, [{"symbol": "AMD", "qty": "50"}],
                           bars, results, now=NOW)
    assert t["entry_id"] in state["open"]
    stops = [b for b in broker.submitted if b.get("type") == "stop"]
    assert len(stops) == 1 and stops[0]["time_in_force"] == "gtc"
    assert any("re-protected" in e["error"] for e in results["errors"])


def test_stopgap_submission_rejected_keeps_trade_open(frames):
    # round-5: the -stopgap market sell is REJECTED at submit time -> the
    # trade must NOT be closed and the shares stay tracked for
    # re-protection (no live stop exists).
    broker, bars = setup_broker(frames)
    cur = float(bars["AMD"]["close"].iloc[-1])
    broker.reject_stop_above = cur
    broker.fail_market_sells = True
    t = filled_trade(days_ago=2, stop=False)
    t["stop_px"] = round(cur + 10.0, 2)
    t["entry_filled"] = True
    state = {"open": {t["entry_id"]: t}}
    results = {"stops_placed": [], "closed": [], "errors": []}
    runner.place_stops_after_fill(
        state, broker, [{"symbol": "AMD", "qty": "50"}], [], results,
        bars=bars, now=NOW)
    assert t["entry_id"] in state["open"]      # NOT closed
    assert results["closed"] == []
    assert results["errors"]
    assert "stop_order_id" not in t             # no (dead) stop id kept


def test_stopgap_pending_blocks_a_second_stop(frames):
    # round-5: while the -stopgap market sell is pending it IS the
    # protection — place_stops_after_fill must not add a second sell.
    broker, bars = setup_broker(frames)
    t = filled_trade(days_ago=2, stop=False)
    t["entry_filled"] = True
    t["stopgap_pending"] = {"client_order_id": "rsi2-AMD-20261103-1-stopgap",
                            "qty": 50}
    state = {"open": {t["entry_id"]: t}}
    results = {"stops_placed": [], "closed": [], "errors": []}
    runner.place_stops_after_fill(
        state, broker, [{"symbol": "AMD", "qty": "50"}], [], results,
        bars=bars, now=NOW)
    assert broker.submitted == []


def test_stop_rejected_with_price_above_plan_reprices(frames):
    # blocker (b): the stop is rejected but the current finished close is
    # still ABOVE the planned stop -> re-price (never above the plan and
    # never above current) and re-place with a fresh deterministic label.
    broker, bars = setup_broker(frames, fail_stop_first=1)
    t = filled_trade(days_ago=2, stop=False)
    t["stop_px"] = 90.0
    t["entry_filled"] = True
    state = {"open": {t["entry_id"]: t}}
    results = {"stops_placed": [], "closed": [], "errors": []}
    runner.place_stops_after_fill(
        state, broker, [{"symbol": "AMD", "qty": "50"}], [], results,
        bars=bars, now=NOW)
    stops = [b for b in broker.submitted if b.get("type") == "stop"]
    assert len(stops) == 1                     # the re-priced retry
    assert stops[0]["stop_price"] == 90.0      # plan kept (below market)
    assert stops[0]["client_order_id"] == \
        "rsi2-AMD-20261103-1-stop-r2"
    assert t["stop_order_id"] and t["stop_gen"] == 2
    assert results["errors"] == []


def test_partial_fill_then_canceled_keeps_filled_shares(frames):
    # blocker (c): the entry partially filled (20 of 50) and was then
    # canceled. The FILLED shares must stay tracked with a stop on them,
    # not dropped.
    broker, bars = setup_broker(frames)
    t = filled_trade(days_ago=3, stop=False)
    t["entry_filled"] = False
    t["qty"] = 50
    state = {"open": {t["entry_id"]: t}}
    broker.orders_by_coid[t["entry_id"]] = {
        "id": "ord-1", "status": "canceled", "filled_qty": "20",
        "filled_avg_price": "98.0", "filled_at": "2026-10-28T15:30:00Z"}
    results = {"closed": [], "errors": []}
    runner.process_exits(state, broker, bars, [], results)
    assert t["entry_id"] in state["open"]      # NOT dropped
    assert t["qty"] == 20 and t["entry_filled"] is True
    assert t["entry_notional"] == 20 * 98.0
    # stop on the filled shares (next pass, S1 machinery)
    results2 = {"stops_placed": [], "errors": []}
    runner.place_stops_after_fill(
        state, broker, [{"symbol": "AMD", "qty": "20"}], [], results2,
        bars=bars, now=NOW)
    stops = [b for b in broker.submitted if b.get("type") == "stop"]
    assert len(stops) == 1 and stops[0]["qty"] == 20


def test_fully_unfilled_canceled_entry_still_dropped(frames):
    # blocker (c), negative case: zero fill + cancel -> dead trade.
    broker, bars = setup_broker(frames)
    t = filled_trade(days_ago=3, stop=False)
    t["entry_filled"] = False
    state = {"open": {t["entry_id"]: t}}
    broker.orders_by_coid[t["entry_id"]] = {
        "id": "ord-1", "status": "canceled", "filled_qty": "0"}
    results = {"closed": [], "errors": []}
    runner.process_exits(state, broker, bars, [], results)
    assert t["entry_id"] not in state["open"]


def test_leftover_cancel_unconfirmed_keeps_trade_tracked(frames):
    # fix (f): a cleanup cancel we cannot confirm means the trade is NOT
    # closed — the runner keeps tracking the order and retries next run.
    broker, bars = setup_broker(frames)
    t = filled_trade(days_ago=3)
    _register_stop(broker, t, status="canceled")
    state = {"open": {t["entry_id"]: t}}
    results = {"closed": [], "errors": []}
    leftover = {"id": "lo-1", "symbol": "AMD", "side": "sell", "qty": 10,
                "client_order_id": "rsi2-AMD-20261103-1-stop-r2"}
    broker.orders_by_coid[leftover["client_order_id"]] = {
        "id": "lo-1", "status": "held"}
    broker.open_orders = [leftover]
    broker.fail_cancels = {"lo-1"}             # cancel hard-fails
    runner.process_exits(state, broker, bars, [], results,
                         open_orders=broker.open_orders)
    assert t["entry_id"] in state["open"]      # kept tracked (fix f)
    assert any("unconfirmed" in e["error"] and "kept open" in e["error"]
               for e in results["errors"])


def test_exits_and_trail_ignore_today_partial_bar(frames):
    # fix (g): during market hours today's partial daily bar must NOT
    # feed exits — the chandelier is computed from FINISHED bars only.
    raw = uptrend_bars()
    raw.loc[raw.index[-1], "c"] = float(raw["c"].iloc[-1]) * 1.10  # spike
    raw.loc[raw.index[-1], "h"] = float(raw["h"].iloc[-1]) * 1.10
    raw.loc[raw.index[-1], "l"] = float(raw["l"].iloc[-1]) * 1.10
    raw.loc[raw.index[-1], "o"] = float(raw["o"].iloc[-1]) * 1.10
    bars = {s: signals.add_indicators(b) for s, b in
            (("AMD", raw), ("SPY", spy_bars()))}
    before_close = datetime(2026, 11, 3, 15, 0, tzinfo=timezone.utc)
    after_close = NOW
    t = filled_trade(days_ago=1, stop=True)  # entry AFTER the drops: the
    # finished-bars chandelier peaks at the last pre-spike close, so only
    # today's spiked bar can lift it above the parked stop.
    # Park the stop exactly at the FINISHED-bars chandelier: no raise
    # while today's partial bar must be ignored...
    dfx = runner.finished_bars_only(bars["AMD"], before_close)
    ch = runner.chandelier_stop(dfx, t["entry_date"],
                                float(dfx["atr14"].iloc[-1]))
    t["stop_px"] = round(float(ch), 2)
    _register_stop(broker := FakeBroker(), t)
    state = {"open": {t["entry_id"]: t}}
    results = {"closed": [], "errors": []}
    runner.process_exits(state, broker, bars,
                         [{"symbol": "AMD", "qty": "50"}], results,
                         now=before_close)
    assert broker.replaced == []               # partial bar NOT used
    # ...but once today's bar is final (after 21:05 UTC) the spike DOES
    # raise the stop.
    parked = t["stop_px"]
    _register_stop(broker, t)
    runner.process_exits(state, broker, bars,
                         [{"symbol": "AMD", "qty": "50"}], results,
                         now=after_close)
    assert len(broker.replaced) == 1
    assert broker.replaced[0][1]["stop_price"] > parked


def test_finished_bars_only_drops_incomplete_today_row():
    df = pd.DataFrame({"date": pd.bdate_range(
        end=pd.Timestamp("2026-11-03"), periods=3),
        "close": [1.0, 2.0, 3.0]})
    before = datetime(2026, 11, 3, 15, 0, tzinfo=timezone.utc)
    after = datetime(2026, 11, 3, 21, 30, tzinfo=timezone.utc)
    out = runner.finished_bars_only(df, before)
    assert len(out) == 2 and float(out["close"].iloc[-1]) == 2.0
    assert runner.finished_bars_only(df, after) is df


def test_execute_refused_until_c4_run_finished(tmp_path, monkeypatch):
    # round-5: the --execute gate is no longer a bare clock check — the
    # runner refuses until TODAY's C4 run has actually FINISHED.
    monkeypatch.setattr(runner, "LOG_DIR", tmp_path)
    monkeypatch.setattr(runner, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(runner, "EVENTS_PATH", tmp_path / "swing_events.jsonl")
    monkeypatch.setattr(runner, "DEFAULT_C4_DB",
                        make_c4_db(tmp_path / "c4dir" / "live_paper.db"))
    monkeypatch.setenv("ALPACA_API_KEY", "PKTEST")
    monkeypatch.setenv("ALPACA_API_SECRET", "SECTEST")
    monkeypatch.setenv("TRADING_BOT_PAPER_ONLY", "1")
    # C4 has NOT finished (empty journal, no C4 rows) -> refuse.
    monkeypatch.setattr(runner, "_c4_journal_text", lambda since: "")
    with pytest.raises(SystemExit) as e:
        runner.run(make_args(execute=True),
                   clock=lambda: datetime(2026, 11, 3, 21, 30,
                                          tzinfo=timezone.utc))
    assert e.value.code == 2


def test_execute_allowed_after_c4_run_finished(tmp_path, monkeypatch):
    # round-5: once the C4 run has completed, the evening RSI(2) run is
    # allowed (the gate is evidence-based, not clock-based).
    monkeypatch.setattr(runner, "LOG_DIR", tmp_path)
    monkeypatch.setattr(runner, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(runner, "EVENTS_PATH", tmp_path / "swing_events.jsonl")
    monkeypatch.setattr(runner, "DEFAULT_C4_DB",
                        make_c4_db(tmp_path / "c4dir" / "live_paper.db"))
    monkeypatch.setenv("ALPACA_API_KEY", "PKTEST")
    monkeypatch.setenv("ALPACA_API_SECRET", "SECTEST")
    monkeypatch.setenv("TRADING_BOT_PAPER_ONLY", "1")
    monkeypatch.setattr(
        runner, "_c4_journal_text",
        lambda since: "live paper run complete: {'entries_submitted': 0}\n")
    # a fake broker: the run must get past the gate
    fake = FakeBroker(bars={"AMD": uptrend_bars(), "SPY": spy_bars()})
    monkeypatch.setattr(runner, "AlpacaPaper", lambda *a, **k: fake)
    runner.run(make_args(execute=False),
               clock=lambda: datetime(2026, 11, 3, 21, 30,
                                      tzinfo=timezone.utc))
    assert runner.c4_run_finished_today(
        datetime(2026, 11, 3, 21, 30, tzinfo=timezone.utc),
        db_path=runner.DEFAULT_C4_DB,
        journal_text="live paper run complete: {}") is True


def test_c4_run_finished_gate_uses_journal_and_db(tmp_path, monkeypatch):
    # round-5: journal is the primary evidence (it also covers
    # zero-signal C4 days, where the DB stays silent); the DB is the
    # fallback (rows written today for today's ref_date).
    now = datetime(2026, 11, 3, 21, 30, tzinfo=timezone.utc)
    db = make_c4_db(tmp_path / "c4.db")
    # 1) no evidence at all -> not finished
    assert runner.c4_run_finished_today(now, db_path=db,
                                        journal_text="") is False
    # 2) journal says the C4 run completed -> finished
    assert runner.c4_run_finished_today(
        now, db_path=db,
        journal_text="live paper run complete: {'skipped': 0}") is True
    assert runner.c4_run_finished_today(
        now, db_path=db,
        journal_text="Finished strat023-c4-shadow.service") is True
    # 3) DB fallback: a row for TODAY's ref_date written in today's
    #    post-close window -> finished
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO orders (client_order_id, symbol, kind, side, qty, "
        "ref_date, created_ts) VALUES "
        "('sl_short-AMD-e-1','AMD','entry','buy',10,'2026-11-03',"
        "'2026-11-03T21:16:00+00:00')")
    con.commit()
    con.close()
    assert runner.c4_run_finished_today(now, db_path=db,
                                        journal_text="") is True
    # 4) a row written EARLIER today (manual backfill run) does NOT
    #    count as today's scheduled run
    db2 = make_c4_db(tmp_path / "c4b.db")
    con = sqlite3.connect(db2)
    con.execute(
        "INSERT INTO orders (client_order_id, symbol, kind, side, qty, "
        "ref_date, created_ts) VALUES "
        "('sl_short-AMD-e-1','AMD','entry','buy',10,'2026-11-03',"
        "'2026-11-03T02:26:09+00:00')")
    con.commit()
    con.close()
    assert runner.c4_run_finished_today(now, db_path=db2,
                                        journal_text="") is False


def test_c4_evidence_window_starts_at_2100_utc():
    now = datetime(2026, 11, 3, 21, 30, tzinfo=timezone.utc)
    win = runner._c4_evidence_window_start(now)
    assert win == datetime(2026, 11, 3, 21, 0, tzinfo=timezone.utc)


def test_execute_gate_refuses_when_c4_missing_and_dry_run_unaffected(
        tmp_path, monkeypatch):
    # round-5: a missing/unreadable C4 DB must not silently pass the
    # execute gate; the dry-run path is unaffected.
    monkeypatch.setattr(runner, "LOG_DIR", tmp_path)
    monkeypatch.setattr(runner, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(runner, "EVENTS_PATH", tmp_path / "swing_events.jsonl")
    monkeypatch.setattr(runner, "DEFAULT_C4_DB", tmp_path / "nope.db")
    monkeypatch.setenv("ALPACA_API_KEY", "PKTEST")
    monkeypatch.setenv("ALPACA_API_SECRET", "SECTEST")
    monkeypatch.setenv("TRADING_BOT_PAPER_ONLY", "1")
    monkeypatch.setattr(runner, "_c4_journal_text", lambda since: "")
    with pytest.raises(SystemExit) as e:
        runner.run(make_args(execute=True),
                   clock=lambda: datetime(2026, 11, 3, 21, 30,
                                          tzinfo=timezone.utc))
    assert e.value.code == 2


def test_stopgap_label_parses_for_attribution():
    from rules import parse_label
    coid = make_stopgap_client_order_id(RSI2, "AMD", "20261103", 1)
    assert coid == "rsi2-AMD-20261103-1-stopgap"
    parsed = parse_label(coid)
    assert parsed["strategy"] == RSI2 and parsed["kind"] == "stopgap"


def test_stopgap_replace_label_parses_for_attribution():
    # round-5: the re-protection label '-stopgap-r<gen>' must stay
    # parseable so report_pnl can attribute the fill to RSI(2).
    from rules import make_stopgap_replace_client_order_id, parse_label
    coid = make_stopgap_replace_client_order_id(RSI2, "AMD", "20261103", 1, 2)
    assert coid == "rsi2-AMD-20261103-1-stopgap-r2"
    assert len(coid) <= 48
    parsed = parse_label(coid)
    assert parsed["strategy"] == RSI2 and parsed["kind"] == "stopgap"
    assert parsed["symbol"] == "AMD" and parsed["n"] == 1


# ------------------------------------------- round-5: live stop reconciliation
def test_reconcile_replaces_canceled_stop_while_shares_held(frames):
    # round-5 item 1: the broker CANCELED the stop while we still hold
    # the shares -> a fresh GTC stop is placed immediately, at the
    # current stop price, with a FRESH client_order_id.
    broker, bars = setup_broker(frames)
    t = filled_trade(days_ago=3)
    _register_stop(broker, t, status="canceled")
    state = {"open": {t["entry_id"]: t}}
    results = {"stops_placed": [], "closed": [], "errors": []}
    runner.reconcile_stops(state, broker, [{"symbol": "AMD", "qty": "50"}],
                           bars, results, now=NOW)
    assert t["entry_id"] in state["open"]        # NOT closed
    stops = [b for b in broker.submitted if b.get("type") == "stop"]
    assert len(stops) == 1
    assert stops[0]["time_in_force"] == "gtc"
    assert stops[0]["qty"] == 50
    assert stops[0]["stop_price"] == 90.0
    assert stops[0]["client_order_id"] == \
        "rsi2-AMD-20261103-1-stop-r2"
    assert t["stop_order_id"] == "ord-1"
    assert t["stop_gen"] == 2


def test_reconcile_replaces_expired_and_rejected_stops(frames):
    # round-5 item 1: expired / rejected stops are dead stops too.
    for dead in ("expired", "rejected"):
        broker, bars = setup_broker(frames)
        t = filled_trade(days_ago=3)
        _register_stop(broker, t, status=dead)
        state = {"open": {t["entry_id"]: t}}
        results = {"stops_placed": [], "closed": [], "errors": []}
        runner.reconcile_stops(state, broker,
                               [{"symbol": "AMD", "qty": "50"}], bars,
                               results, now=NOW)
        stops = [b for b in broker.submitted if b.get("type") == "stop"]
        assert len(stops) == 1, dead
        assert stops[0]["client_order_id"] == "rsi2-AMD-20261103-1-stop-r2"


def test_reconcile_handles_missing_stop_order_id(frames):
    # round-5 item 1: a MISSING stop order id (e.g. the stop was never
    # placed or the state lost it) is reconciled too — a fresh GTC stop
    # is placed while the shares are held.
    broker, bars = setup_broker(frames)
    t = filled_trade(days_ago=3, stop=False)
    t["entry_filled"] = True
    state = {"open": {t["entry_id"]: t}}
    results = {"stops_placed": [], "closed": [], "errors": []}
    runner.reconcile_stops(state, broker, [{"symbol": "AMD", "qty": "50"}],
                           bars, results, now=NOW)
    stops = [b for b in broker.submitted if b.get("type") == "stop"]
    assert len(stops) == 1
    assert stops[0]["time_in_force"] == "gtc"
    assert stops[0]["qty"] == 50
    assert stops[0]["stop_price"] == 90.0
    assert t["stop_order_id"] == "ord-1"


def test_reconcile_sells_at_market_when_price_at_or_below_stop(frames):
    # round-5 item 1: the price is at/below the stop price -> the
    # shares are sold at MARKET (a stop above the market would be
    # rejected). The trade stays open until the sell is filled.
    # ROUND-6: a RE-PROTECTION sell (from reconcile_stops) always
    # carries the generation ('-stopgap-r2'); the plain '-stopgap'
    # label belongs to the FIRST gap sell (place_stops_after_fill).
    broker, bars = setup_broker(frames)
    cur = float(bars["AMD"]["close"].iloc[-1])
    t = filled_trade(days_ago=3)
    t["stop_px"] = round(cur + 5.0, 2)      # stop above the market
    _register_stop(broker, t, status="canceled")
    state = {"open": {t["entry_id"]: t}}
    results = {"stops_placed": [], "closed": [], "errors": []}
    runner.reconcile_stops(state, broker, [{"symbol": "AMD", "qty": "50"}],
                           bars, results, now=NOW)
    sells = [b for b in broker.submitted if b["side"] == "sell"]
    assert len(sells) == 1
    assert sells[0]["type"] == "market"
    assert sells[0]["client_order_id"] == "rsi2-AMD-20261103-1-stopgap-r2"
    assert t["entry_id"] in state["open"]    # not closed on submit
    assert results["closed"] == []


def test_reconcile_leaves_live_stop_alone(frames):
    # round-5 item 1: a LIVE (held) stop is never touched — no churn,
    # no duplicate stop.
    broker, bars = setup_broker(frames)
    t = filled_trade(days_ago=3)
    _register_stop(broker, t, status="held")
    state = {"open": {t["entry_id"]: t}}
    results = {"stops_placed": [], "closed": [], "errors": []}
    runner.reconcile_stops(state, broker, [{"symbol": "AMD", "qty": "50"}],
                           bars, results, now=NOW)
    assert broker.submitted == []
    assert t["stop_order_id"] == "stop-1"


def test_reconcile_ignores_trade_without_shares(frames):
    # round-5 item 1: no shares held -> nothing to protect (the exit
    # path closes the trade).
    broker, bars = setup_broker(frames)
    t = filled_trade(days_ago=3)
    _register_stop(broker, t, status="canceled")
    state = {"open": {t["entry_id"]: t}}
    results = {"stops_placed": [], "closed": [], "errors": []}
    runner.reconcile_stops(state, broker, [], bars, results, now=NOW)
    assert broker.submitted == []
    assert t["entry_id"] in state["open"]


def test_reconcile_catches_cancel_completed_after_poll_window(frames):
    # round-5 item 1 (THE gap): the time exit canceled the stop, the
    # extended poll window returned False (the order was still
    # 'pending_cancel'), the runner walked away believing the stop was
    # live — and the broker COMPLETED the cancel afterwards. The next
    # run's reconciliation must see the dead stop and protect the
    # shares again.
    broker, bars = setup_broker(frames, cancel_polls_until_done=35)
    t = filled_trade(days_ago=30)
    _register_stop(broker, t, status="held")
    state = {"open": {t["entry_id"]: t}}
    results = {"closed": [], "errors": []}
    positions = [{"symbol": "AMD", "qty": "50"}]
    # 1) the time exit: the cancel can NOT be confirmed inside the
    #    extended window -> NO market sell, the trade stays tracked.
    runner.process_exits(state, broker, bars, positions, results)
    assert t["entry_id"] in state["open"]
    assert [b for b in broker.submitted if b["side"] == "sell"] == []
    assert any("unconfirmed" in e["error"] for e in results["errors"])
    # the stop is still 'pending_cancel' when the window gave up
    stop_order = broker.orders_by_coid[t["stop_client_order_id"]]
    assert stop_order["status"] == "pending_cancel"
    # 2) the broker completed the cancel AFTER the window (the fake
    #    flips to 'canceled' on the next status lookup)
    results2 = {"stops_placed": [], "closed": [], "errors": []}
    runner.reconcile_stops(state, broker, positions, bars, results2,
                           now=NOW)
    assert stop_order["status"] == "canceled"   # the late completion
    # the dead stop is replaced at once — the position is never left
    # naked because the cancel completed late.
    stops = [b for b in broker.submitted if b.get("type") == "stop"]
    assert len(stops) == 1
    assert stops[0]["time_in_force"] == "gtc"
    assert stops[0]["qty"] == 50
    assert stops[0]["stop_price"] == 90.0
    assert stops[0]["client_order_id"] == "rsi2-AMD-20261103-1-stop-r2"
    assert t["entry_id"] in state["open"]
    assert t["stop_order_id"] == "ord-1"


def test_run_reconciles_stops_before_exits(frames, tmp_path, monkeypatch):
    # round-5 item 1 at run() level: reconciliation happens at the START
    # of every run (before exits/entries).
    monkeypatch.setattr(runner, "LOG_DIR", tmp_path)
    monkeypatch.setattr(runner, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(runner, "EVENTS_PATH", tmp_path / "swing_events.jsonl")
    monkeypatch.setattr(runner, "DEFAULT_C4_DB",
                        make_c4_db(tmp_path / "c4dir" / "live_paper.db"))
    monkeypatch.setenv("ALPACA_API_KEY", "PKTEST")
    monkeypatch.setenv("ALPACA_API_SECRET", "SECTEST")
    broker, bars = setup_broker(frames)
    t = filled_trade(days_ago=3)
    _register_stop(broker, t, status="canceled")
    state = {"open": {t["entry_id"]: t}}
    (tmp_path / "state.json").write_text(json.dumps(state))
    broker.positions = [{"symbol": "AMD", "qty": "50"}]
    monkeypatch.setattr(runner, "AlpacaPaper", lambda *a, **k: broker)
    runner.run(make_args(symbols=("AMD",), execute=False), clock=lambda: NOW)
    stops = [b for b in broker.submitted if b.get("type") == "stop"]
    assert len(stops) == 1                    # the re-placed GTC stop
    assert stops[0]["client_order_id"] == "rsi2-AMD-20261103-1-stop-r2"


# --------------------------- round-6: unique stopgap labels (no reused id)
def _gap_labels(broker):
    """All market-sell client_order_ids the fake broker was ASKED for
    (rejected ones included — a reused client_order_id is rejected by
    Alpaca, so an attempted label counts as used)."""
    return [b["client_order_id"] for b in broker.submit_attempts
            if b.get("type") == "market" and b.get("side") == "sell"]


def test_stopgap_labels_unique_after_repeated_reprotect_failures(frames):
    # ROUND-6 (Josh, 2026-11-04): Alpaca REJECTS a reused
    # client_order_id. The round-5 code only used the '-stopgap-r<gen>'
    # label for gen == 2 and fell back to the plain '-stopgap' label for
    # gen >= 3 -> the third and later protective sells re-sent a label
    # the broker had already seen -> rejected -> the shares stayed
    # unprotected.
    # Here the FIRST protective sell comes from place_stops_after_fill
    # (a gap-rejected stop), then three consecutive reconciliations
    # fail. EVERY label must be unique.
    broker, bars = setup_broker(frames)
    cur = float(bars["AMD"]["close"].iloc[-1])
    broker.reject_stop_above = cur        # the stop is rejected (gap)
    broker.fail_market_sells = True       # every market sell is rejected
    t = filled_trade(days_ago=2, stop=False)
    t["stop_px"] = round(cur + 10.0, 2)   # planned above the market -> gap
    t["entry_filled"] = True
    state = {"open": {t["entry_id"]: t}}
    positions = [{"symbol": "AMD", "qty": "50"}]

    # 1) the first protective sell: place_stops_after_fill
    results = {"stops_placed": [], "closed": [], "errors": []}
    runner.place_stops_after_fill(state, broker, positions, [], results,
                                  bars=bars, now=NOW)
    assert _gap_labels(broker) == ["rsi2-AMD-20261103-1-stopgap"]
    assert t["stopgap_gen"] == 1

    # 2..4) three consecutive reconciliation attempts, every one failing
    for _ in range(3):
        r = {"stops_placed": [], "closed": [], "errors": []}
        runner.reconcile_stops(state, broker, positions, bars, r, now=NOW)
        # the trade is NEVER closed on a submit
        assert t["entry_id"] in state["open"]
        assert r["closed"] == []

    labels = _gap_labels(broker)
    assert len(labels) == 4, labels
    # every label is UNIQUE (Alpaca rejects a reused client_order_id)
    assert len(set(labels)) == len(labels), labels
    # and the generations are strictly increasing, never the plain label
    # again after the first attempt
    assert labels == [
        "rsi2-AMD-20261103-1-stopgap",
        "rsi2-AMD-20261103-1-stopgap-r2",
        "rsi2-AMD-20261103-1-stopgap-r3",
        "rsi2-AMD-20261103-1-stopgap-r4",
    ], labels
    # the generation counter is persisted even though every submit failed
    assert t["stopgap_gen"] == 4


def test_stopgap_labels_unique_when_first_sell_comes_from_reconcile(frames):
    # ROUND-6: the same uniqueness guarantee when the FIRST protective
    # sell comes from reconcile_stops (the stop was canceled while the
    # shares were held) — not from place_stops_after_fill.
    broker, bars = setup_broker(frames)
    cur = float(bars["AMD"]["close"].iloc[-1])
    broker.fail_market_sells = True       # every market sell is rejected
    t = filled_trade(days_ago=3)
    t["stop_px"] = round(cur + 10.0, 2)   # planned above the market -> gap
    _register_stop(broker, t, status="canceled")
    state = {"open": {t["entry_id"]: t}}
    positions = [{"symbol": "AMD", "qty": "50"}]

    # four consecutive reconciliation attempts, every one failing
    for _ in range(4):
        r = {"stops_placed": [], "closed": [], "errors": []}
        runner.reconcile_stops(state, broker, positions, bars, r, now=NOW)
        assert t["entry_id"] in state["open"]
        assert r["closed"] == []

    labels = _gap_labels(broker)
    assert len(labels) == 4, labels
    assert len(set(labels)) == len(labels), labels
    assert labels == [
        "rsi2-AMD-20261103-1-stopgap-r2",
        "rsi2-AMD-20261103-1-stopgap-r3",
        "rsi2-AMD-20261103-1-stopgap-r4",
        "rsi2-AMD-20261103-1-stopgap-r5",
    ], labels
    assert t["stopgap_gen"] == 5


def test_stopgap_generation_persisted_when_submit_fails(frames):
    # ROUND-6: a FAILED submit still consumed the label at the broker.
    # The generation counter must be persisted so the next run uses the
    # NEXT generation and never re-sends the dead label.
    broker, bars = setup_broker(frames)
    cur = float(bars["AMD"]["close"].iloc[-1])
    broker.reject_stop_above = cur          # the stop is rejected (gap)
    broker.fail_market_sells = True
    t = filled_trade(days_ago=2, stop=False)
    t["stop_px"] = round(cur + 10.0, 2)
    t["entry_filled"] = True
    state = {"open": {t["entry_id"]: t}}
    positions = [{"symbol": "AMD", "qty": "50"}]
    r = {"stops_placed": [], "closed": [], "errors": []}
    runner.place_stops_after_fill(state, broker, positions, [], r,
                                  bars=bars, now=NOW)
    assert _gap_labels(broker) == ["rsi2-AMD-20261103-1-stopgap"]
    assert t["stopgap_gen"] == 1
    assert t["stopgap_client_order_id"] == "rsi2-AMD-20261103-1-stopgap"
    assert t["stopgap_failed"] is True
    # the next run must NOT re-send '-stopgap'
    r2 = {"stops_placed": [], "closed": [], "errors": []}
    runner.reconcile_stops(state, broker, positions, bars, r2, now=NOW)
    assert _gap_labels(broker) == ["rsi2-AMD-20261103-1-stopgap",
                                   "rsi2-AMD-20261103-1-stopgap-r2"]


def test_stopgap_replace_label_used_for_every_generation(frames):
    # ROUND-6: '_reprotect_shares' NEVER falls back to the plain
    # '-stopgap' label — every generation >= 2 is '-stopgap-r<gen>'.
    for gen in range(2, 6):
        t = filled_trade(days_ago=3)
        t["stopgap_gen"] = gen - 1
        t["stopgap_client_order_id"] = "rsi2-AMD-20261103-1-stopgap"
        broker, bars = setup_broker(frames)
        cur = float(bars["AMD"]["close"].iloc[-1])
        t["stop_px"] = round(cur + 10.0, 2)
        _register_stop(broker, t, status="canceled")
        state = {"open": {t["entry_id"]: t}}
        r = {"stops_placed": [], "closed": [], "errors": []}
        runner.reconcile_stops(state, broker, [{"symbol": "AMD", "qty": "50"}],
                               bars, r, now=NOW)
        labels = _gap_labels(broker)
        assert len(labels) == 1, (gen, labels)
        assert labels[0] == f"rsi2-AMD-20261103-1-stopgap-r{gen}", (gen, labels)
        assert t["stopgap_gen"] == gen


# ------------------ round-6: the execute gate uses the US/Eastern DATE
def test_us_eastern_trading_date_is_stable_across_the_utc_rollover():
    # ROUND-6: 6 PM CST is 00:00 UTC on the NEXT UTC day, but it is
    # still 7 PM ET on the SAME trading day. The gate must use the
    # US/Eastern trading date, not the UTC calendar date.
    assert runner.us_eastern_trading_date(
        datetime(2026, 11, 3, 21, 30, tzinfo=timezone.utc)) \
        == datetime(2026, 11, 3).date()
    assert runner.us_eastern_trading_date(
        datetime(2026, 11, 4, 0, 0, tzinfo=timezone.utc)) \
        == datetime(2026, 11, 3).date()
    assert runner.us_eastern_trading_date(
        datetime(2026, 11, 4, 1, 30, tzinfo=timezone.utc)) \
        == datetime(2026, 11, 3).date()
    # after the ET date rolls over it moves on
    assert runner.us_eastern_trading_date(
        datetime(2026, 11, 4, 21, 30, tzinfo=timezone.utc)) \
        == datetime(2026, 11, 4).date()


def test_c4_evidence_window_is_anchored_on_the_us_eastern_trading_date():
    # ROUND-6: the evidence window starts at 21:00 UTC of the US/Eastern
    # TRADING DATE. For an evening run (00:00 UTC on the next UTC day)
    # that is the PREVIOUS day's 21:00 UTC — the window that actually
    # contains the C4 run for the session being traded.
    assert runner._c4_evidence_window_start(
        datetime(2026, 11, 3, 21, 30, tzinfo=timezone.utc)) \
        == datetime(2026, 11, 3, 21, 0, tzinfo=timezone.utc)
    assert runner._c4_evidence_window_start(
        datetime(2026, 11, 4, 0, 0, tzinfo=timezone.utc)) \
        == datetime(2026, 11, 3, 21, 0, tzinfo=timezone.utc)
    assert runner._c4_evidence_window_start(
        datetime(2026, 11, 4, 21, 30, tzinfo=timezone.utc)) \
        == datetime(2026, 11, 4, 21, 0, tzinfo=timezone.utc)


def test_execute_allowed_in_the_evening_after_the_utc_date_rolls_over(
        tmp_path, monkeypatch):
    # ROUND-6 (Josh, 2026-11-04): the execute gate refused runs at
    # 6 PM CST or later because the UTC date had already rolled over to
    # the next day. The gate must be anchored on the US/Eastern trading
    # date, so an evening run on the SAME trading day is allowed once
    # C4 has finished for that session.
    monkeypatch.setattr(runner, "LOG_DIR", tmp_path)
    monkeypatch.setattr(runner, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(runner, "EVENTS_PATH", tmp_path / "swing_events.jsonl")
    monkeypatch.setattr(runner, "DEFAULT_C4_DB",
                        make_c4_db(tmp_path / "c4dir" / "live_paper.db"))
    monkeypatch.setenv("ALPACA_API_KEY", "PKTEST")
    monkeypatch.setenv("ALPACA_API_SECRET", "SECTEST")
    monkeypatch.setenv("TRADING_BOT_PAPER_ONLY", "1")

    # The C4 unit finished its run for the 2026-11-03 session at
    # 21:16 UTC. The journal is filtered by '--since <window start>',
    # so the fake journal only reports the completion when the window
    # the runner asked for actually CONTAINS the 21:16 UTC entry.
    c4_finished_at = datetime(2026, 11, 3, 21, 16, tzinfo=timezone.utc)

    def fake_journal(since):
        if since <= c4_finished_at:
            return "live paper run complete: {'entries_submitted': 0}\n"
        return ""

    monkeypatch.setattr(runner, "_c4_journal_text", fake_journal)
    fake = FakeBroker(bars={"AMD": uptrend_bars(), "SPY": spy_bars()})
    monkeypatch.setattr(runner, "AlpacaPaper", lambda *a, **k: fake)
    # 6 PM CST == 00:00 UTC on 2026-11-04 (7 PM ET on 2026-11-03)
    runner.run(make_args(execute=False),
               clock=lambda: datetime(2026, 11, 4, 0, 0,
                                      tzinfo=timezone.utc))
    # the gate sees the C4 run of the 2026-11-03 session
    assert runner.c4_run_finished_today(
        datetime(2026, 11, 4, 0, 0, tzinfo=timezone.utc),
        db_path=runner.DEFAULT_C4_DB,
        journal_text="live paper run complete: {}") is True
    # the window the runner asked the journal for must be the one that
    # contains the C4 run of the trading day (2026-11-03 21:16 UTC)
    assert runner._c4_evidence_window_start(
        datetime(2026, 11, 4, 0, 0, tzinfo=timezone.utc)) \
        == datetime(2026, 11, 3, 21, 0, tzinfo=timezone.utc)


def test_execute_refused_in_the_evening_when_the_window_is_the_next_day(
        tmp_path, monkeypatch):
    # ROUND-6 (negative): with the OLD UTC-date anchoring the window
    # would start at 2026-11-04 21:00 UTC, which does NOT contain the
    # C4 run of the 2026-11-03 session -> the evening run was refused.
    # This test pins the regression: the journal is filtered by the
    # window, and the window must contain the C4 run.
    c4_finished_at = datetime(2026, 11, 3, 21, 16, tzinfo=timezone.utc)
    now = datetime(2026, 11, 4, 0, 0, tzinfo=timezone.utc)
    win = runner._c4_evidence_window_start(now)
    assert win <= c4_finished_at, (
        "the evidence window must contain the C4 run of the trading day "
        "the evening run belongs to")
    assert win.date() == datetime(2026, 11, 3).date()


def test_execute_gate_still_refuses_before_c4_finished_for_the_session(
        tmp_path, monkeypatch):
    # ROUND-6: anchoring on the US/Eastern trading date must NOT open
    # the gate for a session whose C4 run has not happened yet (e.g.
    # 15:00 ET on the trading day -> the C4 timer fires at 16:15 ET).
    monkeypatch.setattr(runner, "LOG_DIR", tmp_path)
    monkeypatch.setattr(runner, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(runner, "EVENTS_PATH", tmp_path / "swing_events.jsonl")
    monkeypatch.setattr(runner, "DEFAULT_C4_DB",
                        make_c4_db(tmp_path / "c4dir" / "live_paper.db"))
    monkeypatch.setenv("ALPACA_API_KEY", "PKTEST")
    monkeypatch.setenv("ALPACA_API_SECRET", "SECTEST")
    monkeypatch.setenv("TRADING_BOT_PAPER_ONLY", "1")
    monkeypatch.setattr(runner, "_c4_journal_text", lambda since: "")
    # 2026-11-03 15:00 ET == 20:00 UTC: the session has NOT closed yet
    with pytest.raises(SystemExit) as e:
        runner.run(make_args(execute=True),
                   clock=lambda: datetime(2026, 11, 3, 20, 0,
                                          tzinfo=timezone.utc))
    assert e.value.code == 2


def test_stopgap_generation_never_resets_between_paths(frames):
    # ROUND-6: place_stops_after_fill and _reprotect_shares share the
    # SAME generation counter. A re-protection (gen >= 2) must never be
    # reset to the plain '-stopgap' label by a later
    # place_stops_after_fill pass (the trade has no stop id after a
    # failed re-protection, so that pass DOES run again) — a reused
    # client_order_id is rejected by Alpaca.
    broker, bars = setup_broker(frames)
    cur = float(bars["AMD"]["close"].iloc[-1])
    broker.reject_stop_above = cur          # the stop is rejected (gap)
    broker.fail_market_sells = True         # every market sell is rejected
    t = filled_trade(days_ago=2, stop=False)
    t["stop_px"] = round(cur + 10.0, 2)
    t["entry_filled"] = True
    state = {"open": {t["entry_id"]: t}}
    positions = [{"symbol": "AMD", "qty": "50"}]

    # 1) the first gap sell from place_stops_after_fill -> plain label
    r = {"stops_placed": [], "closed": [], "errors": []}
    runner.place_stops_after_fill(state, broker, positions, [], r,
                                  bars=bars, now=NOW)
    assert _gap_labels(broker) == ["rsi2-AMD-20261103-1-stopgap"]
    assert t["stopgap_gen"] == 1

    # 2) the re-protection from reconcile_stops -> '-stopgap-r2'
    r = {"stops_placed": [], "closed": [], "errors": []}
    runner.reconcile_stops(state, broker, positions, bars, r, now=NOW)
    assert _gap_labels(broker)[-1] == "rsi2-AMD-20261103-1-stopgap-r2"
    assert t["stopgap_gen"] == 2

    # 3) place_stops_after_fill runs again (no stop id on the trade) —
    #    it must NOT reset the counter to the plain label
    r = {"stops_placed": [], "closed": [], "errors": []}
    runner.place_stops_after_fill(state, broker, positions, [], r,
                                  bars=bars, now=NOW)
    labels = _gap_labels(broker)
    assert len(set(labels)) == len(labels), labels
    assert labels[-1] != "rsi2-AMD-20261103-1-stopgap", labels
    assert t["stopgap_gen"] >= 3, t["stopgap_gen"]
