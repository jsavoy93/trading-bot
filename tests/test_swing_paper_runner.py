"""Runner tests for STRAT-SWING-PAPER with a FakeBroker — no network.

Covers the PR #135 review items: same-stock overlap (C4 position / pending
sl_short-* order / C4 same-day db signal), sizing caps, dry-run writes
nothing but a log, order-error handling (no trade recorded), rerun
idempotency, exits (chandelier raise + 15-day time exit), stop-after-fill
(total sell qty never exceeds held qty), the TRADING_BOT_PAPER_ONLY guard,
and the incomplete-daily-bar refusal.
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
from rules import RSI2, make_client_order_id  # noqa: E402


# ------------------------------------------------------------ fake broker
class FakeBroker:
    dry_run = False

    def __init__(self, positions=None, open_orders=None, orders_by_coid=None,
                 fail_symbols=(), portfolio_value=100_000.0,
                 bars=None, fail_cancels=()):
        self.positions = positions or []
        self.open_orders = open_orders or []
        self.orders_by_coid = orders_by_coid or {}
        self.fail_symbols = set(fail_symbols)
        self.fail_cancels = set(fail_cancels)
        self._pv = portfolio_value
        self.bars = bars or {}
        self.submitted = []       # list of submitted bodies
        self.replaced = []        # list of (order_id, patch)
        self.canceled = []
        self._rep_n = 0

    def get_account(self):
        return {"portfolio_value": str(self._pv)}

    def get_positions(self):
        return self.positions

    def get_orders(self, status="open", limit=500):
        return self.open_orders if status == "open" else []

    def get_order_by_client_id(self, coid):
        return self.orders_by_coid.get(coid)

    def daily_bars(self, symbol):
        return self.bars.get(symbol)

    def submit_order(self, body):
        if body["symbol"] in self.fail_symbols:
            return {"error": "forbidden short selling (test)"}
        self.submitted.append(body)
        return {"id": f"ord-{len(self.submitted)}", "status": "accepted",
                "client_order_id": body.get("client_order_id")}

    def replace_order(self, order_id, patch):
        # models Alpaca: a replace returns a NEW order id (fix B)
        self._rep_n += 1
        new_id = f"ord-rep-{self._rep_n}"
        self.replaced.append((order_id, patch))
        return {"id": new_id, **patch}

    def cancel_order(self, order_id):
        if order_id in self.fail_cancels:
            return False
        self.canceled.append(order_id)
        return True


# ------------------------------------------------------------ synthetic bars
def _dates(n, end=None):
    end = end or (datetime.now(timezone.utc).date() - timedelta(days=1))
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
    base = dict(symbols=list(symbols), bucket_pct=0.30, rsi2_cap=4,
                max_open=4, per_position_cap=0.10, c4_db=None)
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


# ------------------------------------------------------------ entries
def test_entries_per_position_cap_and_state(frames, tmp_path, monkeypatch):
    broker, bars = setup_broker(frames)
    state = {"open": {}}
    results = {"rsi2": [], "errors": []}
    runner.process_entries(state, broker, bars, bars["SPY"], [], [],
                           [], "20261103", make_args(), 100_000.0, results)
    assert len(results["rsi2"]) == 1
    t = state["open"][results["rsi2"][0]["entry_id"]]
    px = t["ref_px"]
    # slice = 100k*0.30 = 30k; per-position cap 10% = 3k
    assert t["qty"] * px <= 3000 + px
    assert t["qty"] * px > 0
    # entry market buy submitted; NO sell orders at entry time
    sides = [b["side"] for b in broker.submitted]
    assert sides == ["buy"]


def test_order_error_records_no_trade(frames):
    broker, bars = setup_broker(frames, symbols=("AMD", "NVDA"),
                                fail_symbols={"NVDA"})
    state = {"open": {}}
    results = {"rsi2": [], "errors": []}
    runner.process_entries(state, broker, bars, bars["SPY"], [], [],
                           [], "20261103",
                           make_args(symbols=("AMD", "NVDA")), 100_000.0,
                           results)
    assert [r["symbol"] for r in results["rsi2"]] == ["AMD"]
    assert len(results["errors"]) == 1
    assert all(t["symbol"] == "AMD" for t in state["open"].values())
    assert not any(b.get("symbol") == "NVDA" for b in broker.submitted)


def test_rerun_idempotent_pending_order_skipped(frames):
    broker, bars = setup_broker(frames)
    state = {"open": {}}
    results = {"rsi2": [], "errors": []}
    runner.process_entries(state, broker, bars, bars["SPY"], [], [],
                           [], "20261103", make_args(), 100_000.0, results)
    assert len(results["rsi2"]) == 1
    entry_id = results["rsi2"][0]["entry_id"]
    # simulate the pending order now visible on the account
    broker.open_orders = [{"symbol": "AMD", "side": "buy",
                           "client_order_id": entry_id}]
    state2 = {"open": {}}
    results2 = {"rsi2": [], "errors": []}
    runner.process_entries(state2, broker, bars, bars["SPY"], [],
                           broker.open_orders, [entry_id], "20261103",
                           make_args(), 100_000.0, results2)
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
                           100_000.0, results)
    assert results["rsi2"] == []
    assert broker.submitted == []


def test_max_open_blocks_new_entries(frames):
    broker, bars = setup_broker(frames)
    state = {"open": {f"rsi2-XXX-20261101-{i}": {"symbol": f"X{i}", "entry_notional": 1}
                      for i in range(4)}}
    results = {"rsi2": [], "errors": []}
    runner.process_entries(state, broker, bars, bars["SPY"], [], [],
                           [], "20261103", make_args(max_open=4),
                           100_000.0, results)
    assert results["rsi2"] == []
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
    # dry-run submit returns dry-run stub: nothing "sent"
    assert broker.submitted  # decisions still simulated
    assert all(b.get("client_order_id", "").startswith("rsi2-")
               for b in broker.submitted)


def test_execute_requires_paper_only_env(monkeypatch, tmp_path):
    monkeypatch.delenv("TRADING_BOT_PAPER_ONLY", raising=False)
    args = make_args(execute=True)
    with pytest.raises(SystemExit) as e:
        runner.run(args)
    assert e.value.code == 2


# ------------------------------------------------------------ bar finality
def test_incomplete_daily_bar_refused_injected_clock():
    # FIX J: no clock-dependent skip — the clock is injected via now=.
    sig_day = datetime(2026, 11, 3, tzinfo=timezone.utc).date()
    before_close = datetime(2026, 11, 3, 15, 0, tzinfo=timezone.utc)
    after_close = datetime(2026, 11, 3, 21, 30, tzinfo=timezone.utc)
    assert runner.last_bar_is_final(sig_day, now=before_close) is False
    assert runner.last_bar_is_final(sig_day, now=after_close) is True
    assert runner.last_bar_is_final(sig_day - timedelta(days=1),
                                    now=before_close) is True
    assert runner.last_bar_is_final(sig_day + timedelta(days=1),
                                    now=before_close) is False


# ------------------------------------------------------------ exits
def filled_trade(sym="AMD", qty=50, days_ago=5, stop=True):
    sym_df = signals.add_indicators(uptrend_bars())
    entry_date = str(sym_df["date"].iloc[-1 - days_ago])
    t = {
        "strategy": RSI2, "symbol": sym, "qty": qty, "n": 1, "date": "20261103",
        "ref_px": 100.0, "entry_notional": qty * 100.0, "stop_px": 90.0,
        "partial_px": 110.0, "entry_id": make_client_order_id(RSI2, sym, "20261103", 1),
        "entry_order_id": "ord-1", "entry_filled": True, "entry_fill_px": 100.0,
        "signal_date": entry_date, "atr14": 2.0, "entry_date": entry_date,
    }
    if stop:
        t["stop_order_id"] = "stop-1"
        t["stop_client_order_id"] = make_client_order_id(
            RSI2, sym, "20261103", 1) + "-stop"
        t["partial_order_id"] = "part-1"
        t["partial_client_order_id"] = make_client_order_id(
            RSI2, sym, "20261103", 1) + "-stop-p"
    return t


def test_time_exit_closes_trade_and_frees_slice(frames):
    broker, bars = setup_broker(frames)
    sym_df = bars["AMD"]
    t = filled_trade(days_ago=30)  # > 15 trading days ago
    state = {"open": {t["entry_id"]: t}}
    results = {"closed": [], "errors": []}
    positions = [{"symbol": "AMD", "qty": "50"}]
    runner.process_exits(state, broker, bars, positions, results)
    assert t["entry_id"] not in state["open"]          # slice money freed
    sells = [b for b in broker.submitted if b["side"] == "sell"]
    assert len(sells) == 1 and sells[0]["type"] == "market"
    assert set(broker.canceled) == {"stop-1", "part-1"}


def test_chandelier_raises_stop_never_lowers(frames):
    broker, bars = setup_broker(frames)
    t = filled_trade(days_ago=3, stop=True)
    t["stop_px"] = 1.0  # far below any chandelier
    state = {"open": {t["entry_id"]: t}}
    results = {"closed": [], "errors": []}
    positions = [{"symbol": "AMD", "qty": "50"}]
    runner.process_exits(state, broker, bars, positions, results)
    assert len(broker.replaced) == 1
    new_px = broker.replaced[0][1]["stop_price"]
    assert new_px > 1.0
    assert t["stop_px"] == new_px
    # FIX B: a replace returns a NEW order id — state must track it
    assert t["stop_order_id"] == "ord-rep-1"
    assert t["stop_order_id"] != "stop-1"
    # a second pass with an already-high stop does not lower it
    runner.process_exits(state, broker, bars, positions, results)
    assert len(broker.replaced) == 1


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
    bad = filled_trade(sym="ZZZZ", days_ago=3)  # no bars for ZZZZ
    state = {"open": {t["entry_id"]: t, bad["entry_id"]: bad}}
    results = {"closed": [], "errors": []}
    runner.process_exits(state, broker, bars,
                         [{"symbol": "AMD", "qty": "50"}], results)
    assert t["entry_id"] in state["open"]  # good trade still managed


# ------------------------------------------------------------ stop-after-fill
def _plain_stops(submitted):
    return [b for b in submitted if b.get("type") == "stop"]


def _ocos(submitted):
    return [b for b in submitted if b.get("order_class") == "oco"]


def test_stop_placed_only_after_fill_and_never_oversells():
    broker = FakeBroker()
    t = filled_trade(days_ago=2, stop=False)
    t["entry_filled"] = False
    state = {"open": {t["entry_id"]: t}}
    results = {"stops_placed": [], "errors": []}
    # not filled yet: nothing placed
    runner.place_stops_after_fill(state, broker, [{"symbol": "AMD", "qty": "50"}], [], results)
    assert broker.submitted == []
    # now filled, held 50, existing sell 30 -> room 20: OCO half gets 10,
    # plain stop covers the other 10; total sells = 30+10+10 = held 50
    t["entry_filled"] = True
    open_orders = [{"symbol": "AMD", "side": "sell", "qty": 30}]
    runner.place_stops_after_fill(state, broker, [{"symbol": "AMD", "qty": "50"}],
                                  open_orders, results)
    stops = _plain_stops(broker.submitted)
    ocos = _ocos(broker.submitted)
    assert len(stops) == 1 and stops[0]["qty"] == 10
    assert len(ocos) == 1 and ocos[0]["qty"] == 10
    assert 30 + 10 + 10 == 50  # never exceeds held


def test_stop_plus_partial_total_never_exceeds_held():
    broker = FakeBroker()
    t = filled_trade(days_ago=2, stop=False)
    t["entry_filled"] = True
    state = {"open": {t["entry_id"]: t}}
    results = {"stops_placed": [], "errors": []}
    held = 50
    runner.place_stops_after_fill(state, broker,
                                  [{"symbol": "AMD", "qty": str(held)}], [], results)
    stops = _plain_stops(broker.submitted)
    ocos = _ocos(broker.submitted)
    assert len(stops) == 1 and len(ocos) == 1  # plain stop + OCO bracket
    total_sell = stops[0]["qty"] + ocos[0]["qty"]
    assert total_sell <= held  # 1.5x bug fixed
    assert stops[0]["qty"] == 25
    assert ocos[0]["qty"] == 25  # half, within room
    assert stops[0]["client_order_id"].endswith("-stop")


def test_partial_half_is_oco_bracket_whole_position_protected():
    # FIX E: the partial-exit half must be an OCO (limit + stop), never a
    # naked limit; the remainder keeps the plain stop. Every share is
    # behind a stop at all times.
    broker = FakeBroker()
    t = filled_trade(days_ago=2, stop=False)
    t["entry_filled"] = True
    t["stop_px"] = 90.0
    t["partial_px"] = 110.0
    state = {"open": {t["entry_id"]: t}}
    results = {"stops_placed": [], "errors": []}
    runner.place_stops_after_fill(state, broker,
                                  [{"symbol": "AMD", "qty": "50"}], [], results)
    ocos = _ocos(broker.submitted)
    assert len(ocos) == 1
    legs = ocos[0]["legs"]
    limit_leg = next(l for l in legs if l["type"] == "limit")
    stop_leg = next(l for l in legs if l["type"] == "stop")
    assert limit_leg["limit_price"] == 110.0
    assert stop_leg["stop_price"] == 90.0
    assert limit_leg["qty"] == stop_leg["qty"] == 25
    assert limit_leg["client_order_id"].endswith("-stop-p")
    assert stop_leg["client_order_id"].endswith("-stop-oco")
    plain = _plain_stops(broker.submitted)[0]
    # protection covers the ENTIRE position
    assert plain["qty"] + stop_leg["qty"] == 50
    # state records the ids needed to cancel/replace later
    rec = state["open"][t["entry_id"]]
    assert rec["stop_order_id"] and rec["oco_order_id"]
    assert rec["stop_client_order_id"].endswith("-stop")


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
    assert "oco_order_id" not in rec


# ------------------------------------------------------------ C4 same-day db
def test_c4_signaled_today_from_live_paper_db(_c4_db):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    con = sqlite3.connect(_c4_db)
    con.execute(
        "INSERT INTO orders (client_order_id, symbol, kind, side, qty, "
        "ref_date, created_ts) VALUES (?,?,?,?,?,?,?)",
        ("sl_short-AMD-e-20261103", "AMD", "entry", "buy", 10, today,
         today + "T21:19:05Z"))
    con.execute(
        "INSERT INTO orders (client_order_id, symbol, kind, side, qty, "
        "ref_date, created_ts) VALUES (?,?,?,?,?,?,?)",
        ("sl_short-QQQ-e-2020", "QQQ", "entry", "buy", 10, "2020-01-01",
         "2020-01-01T21:19:05Z"))
    con.commit()
    con.close()
    out = runner.c4_signaled_today(["AMD", "QQQ", "TSLA"])
    assert out == {"AMD"}


def test_c4_db_missing_or_bad_fails_loudly(tmp_path, monkeypatch):
    # FIX A: a missing or unreadable C4 DB must ABORT — never silently
    # behave as "no C4 signal".
    monkeypatch.setattr(runner, "DEFAULT_C4_DB", tmp_path / "nope.db")
    with pytest.raises(RuntimeError, match="C4 shadow DB not found"):
        runner.c4_signaled_today(["AMD"])
    with pytest.raises(RuntimeError, match="C4 shadow DB not found"):
        runner.c4_open_notional()
    bad = tmp_path / "bad.db"
    bad.write_text("not a database")
    with pytest.raises(RuntimeError, match="C4 same-day query failed"):
        runner.c4_signaled_today(["AMD"], db_path=bad)


def test_missing_c4_db_aborts_entries(tmp_path, monkeypatch, frames):
    monkeypatch.setattr(runner, "DEFAULT_C4_DB", tmp_path / "nope.db")
    broker, bars = setup_broker(frames)
    state = {"open": {}}
    results = {"rsi2": [], "errors": []}
    with pytest.raises(RuntimeError):
        runner.process_entries(state, broker, bars, bars["SPY"], [], [],
                               [], "20261103", make_args(), 100_000.0, results)
    assert broker.submitted == []  # nothing traded on a guessed skip set


# ------------------------------------------------------------ dotted symbols
def test_dotted_symbol_label_flow():
    coid = make_client_order_id(RSI2, "BRK.B", "20261103", 1)
    assert coid == "rsi2-BRK.B-20261103-1"
    assert len(make_client_order_id(RSI2, "BF.B", "20261103", 123)) <= 48


# ------------------------------------------- round-2 fixes C, D, F, G, H
def test_time_exit_cancel_failure_skips_market_sell(frames):
    # FIX C: if the protective orders cannot be canceled, NO market sell
    # is submitted (no double orders).
    broker, bars = setup_broker(frames)
    t = filled_trade(days_ago=30)
    state = {"open": {t["entry_id"]: t}}
    results = {"closed": [], "errors": []}
    broker.fail_cancels = {"stop-1"}
    positions = [{"symbol": "AMD", "qty": "50"}]
    runner.process_exits(state, broker, bars, positions, results)
    assert t["entry_id"] in state["open"]        # trade stays open
    assert [b for b in broker.submitted if b["side"] == "sell"] == []
    assert any("cancel failed" in e["error"] for e in results["errors"])


def test_time_exit_cancel_unconfirmed_skips_market_sell(frames):
    # FIX C (confirmation half): cancel returns True but the order is
    # still live -> treat as NOT canceled, no market sell.
    broker, bars = setup_broker(frames)
    t = filled_trade(days_ago=30)
    state = {"open": {t["entry_id"]: t}}
    results = {"closed": [], "errors": []}
    broker.orders_by_coid[t["stop_client_order_id"]] = {
        "id": "stop-1", "status": "held_open_forever"}
    positions = [{"symbol": "AMD", "qty": "50"}]
    runner.process_exits(state, broker, bars, positions, results)
    assert [b for b in broker.submitted if b["side"] == "sell"] == []
    assert t["entry_id"] in state["open"]


def test_stop_filled_closes_trade_and_frees_slot(frames):
    # FIX D: position gone and the stop order filled -> close in DB.
    broker, bars = setup_broker(frames)
    t = filled_trade(days_ago=3)
    state = {"open": {t["entry_id"]: t}}
    results = {"closed": [], "errors": []}
    broker.orders_by_coid[t["stop_client_order_id"]] = {
        "id": "stop-1", "status": "filled"}
    runner.process_exits(state, broker, bars, [], results)  # no position
    assert t["entry_id"] not in state["open"]
    assert results["closed"][0]["reason"] == "stop_filled"


def test_position_gone_without_stop_fill_closes_trade(frames):
    # FIX D: position gone for any other reason -> still close + free slot
    broker, bars = setup_broker(frames)
    t = filled_trade(days_ago=3)
    state = {"open": {t["entry_id"]: t}}
    results = {"closed": [], "errors": []}
    runner.process_exits(state, broker, bars, [], results)
    assert t["entry_id"] not in state["open"]
    assert results["closed"][0]["reason"] == "position_gone"


def test_stops_reanchored_to_actual_fill_price(frames):
    # FIX F: initial stop / partial target recompute from the ACTUAL fill
    # price (frozen formulas, signal-day ATR14), not the signal close.
    broker, bars = setup_broker(frames)
    sym_df = bars["AMD"]
    entry_date = str(sym_df["date"].iloc[-6])
    t = filled_trade(days_ago=5, stop=False)
    t["entry_filled"] = False
    t["atr14"] = 2.0
    t["ref_px"] = 100.0            # signal close
    t["stop_px"] = 96.0            # from signal close
    t["partial_px"] = 108.0
    t["entry_date"] = entry_date
    state = {"open": {t["entry_id"]: t}}
    results = {"closed": [], "errors": []}
    broker.orders_by_coid[t["entry_id"]] = {
        "id": "ord-9", "status": "filled", "filled_avg_price": "105.0",
        "filled_at": "2026-11-03T15:30:00Z"}
    runner.process_exits(state, broker, bars,
                         [{"symbol": "AMD", "qty": str(t["qty"])}], results)
    # stop = 105 - 2.0*2.0 = 101 ; partial = 105 + 2*(105-101) = 113
    assert t["entry_fill_px"] == 105.0
    assert t["stop_px"] == 101.0
    assert t["partial_px"] == 113.0
    assert t["entry_notional"] == t["qty"] * 105.0
    # FIX G: entry date is the FILL date, not the signal date
    assert t["entry_date"] == "2026-11-03"


def test_c4_open_notional_reduces_rsi2_slice(_c4_db, frames):
    # FIX H: C4's open positions draw from the shared 30% bucket, so the
    # RSI(2) slice must shrink by the C4 notional.
    broker, bars = setup_broker(frames)
    con = sqlite3.connect(_c4_db)
    con.execute(
        "INSERT INTO positions (symbol, signal_date, signal_atr14, "
        "entry_order_coid, entry_date, entry_price, qty, status, created_ts) "
        "VALUES ('QQQ','2026-11-01',1.0,'sl_short-QQQ-e-1','2026-11-01',"
        "100.0,50,'open','2026-11-01T21:00:00Z')")
    con.commit(); con.close()
    state = {"open": {}}
    results = {"rsi2": [], "errors": []}
    runner.process_entries(state, broker, bars, bars["SPY"], [], [], [],
                           "20261103", make_args(c4_db=str(_c4_db)),
                           100_000.0, results)
    assert len(results["rsi2"]) == 1
    t = state["open"][results["rsi2"][0]["entry_id"]]
    # slice = 30k - 5k C4 = 25k ; per-position cap 10% -> 2.5k
    assert t["entry_notional"] <= 2500 + t["ref_px"]
    # combined exposure respects the 30% bucket (5k C4 + RSI(2) slice)
    assert 5000.0 + t["entry_notional"] <= 30_000.0 + t["ref_px"]


def test_c4_open_notional_read_fail_loudly(_c4_db, frames, monkeypatch):
    # FIX H + A: a broken C4 positions table aborts sizing (no guessing).
    con = sqlite3.connect(_c4_db)
    con.execute("DROP TABLE positions")
    con.commit(); con.close()
    broker, bars = setup_broker(frames)
    with pytest.raises(RuntimeError):
        runner.process_entries({"open": {}}, broker, bars, bars["SPY"],
                               [], [], [], "20261103",
                               make_args(c4_db=str(_c4_db)), 100_000.0,
                               {"rsi2": [], "errors": []})
