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
                 bars=None):
        self.positions = positions or []
        self.open_orders = open_orders or []
        self.orders_by_coid = orders_by_coid or {}
        self.fail_symbols = set(fail_symbols)
        self._pv = portfolio_value
        self.bars = bars or {}
        self.submitted = []       # list of submitted bodies
        self.replaced = []        # list of (order_id, patch)
        self.canceled = []

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
        self.replaced.append((order_id, patch))
        return {"id": order_id, **patch}

    def cancel_order(self, order_id):
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
                max_open=4, per_position_cap=0.10)
    base.update(kw)
    return SimpleNamespace(**base)


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
    monkeypatch.setattr(runner, "DEFAULT_C4_DB", tmp_path / "no-live-paper.db")
    monkeypatch.setenv("ALPACA_API_KEY", "PKTEST")
    monkeypatch.setenv("ALPACA_API_SECRET", "SECTEST")
    monkeypatch.delenv("TRADING_BOT_PAPER_ONLY", raising=False)
    broker, bars = setup_broker(frames)
    broker.dry_run = True
    monkeypatch.setattr(runner, "AlpacaPaper", lambda *a, **k: broker)
    runner.run(make_args(execute=False))
    assert not (tmp_path / "state.json").exists()
    logs = [p for p in tmp_path.iterdir()]
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
def test_incomplete_daily_bar_refused():
    today = datetime.now(timezone.utc)
    if (today.hour, today.minute) >= (21, 5):
        pytest.skip("after close — same-day bar is final now")
    assert runner.last_bar_is_final(today.date()) is False
    assert runner.last_bar_is_final(today.date() - timedelta(days=1)) is True


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
        t["partial_order_id"] = "part-1"
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
    runner.process_exits(state, broker, bars, [], results)
    assert len(broker.replaced) == 1
    new_px = broker.replaced[0][1]["stop_price"]
    assert new_px > 1.0
    assert t["stop_px"] == new_px
    # a second pass with an already-high stop does not lower it
    runner.process_exits(state, broker, bars, [], results)
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
    runner.process_exits(state, broker, bars, [], results)
    assert t["entry_id"] in state["open"]  # good trade still managed


# ------------------------------------------------------------ stop-after-fill
def test_stop_placed_only_after_fill_and_never_oversells():
    broker = FakeBroker()
    t = filled_trade(days_ago=2, stop=False)
    t["entry_filled"] = False
    state = {"open": {t["entry_id"]: t}}
    results = {"stops_placed": [], "errors": []}
    # not filled yet: nothing placed
    runner.place_stops_after_fill(state, broker, [{"symbol": "AMD", "qty": "50"}], [], results)
    assert broker.submitted == []
    # now filled, held 50, existing sell 30 -> room 20: partial gets 10,
    # stop covers the other 10; total sells = 30+10+10 = held 50
    t["entry_filled"] = True
    open_orders = [{"symbol": "AMD", "side": "sell", "qty": 30}]
    runner.place_stops_after_fill(state, broker, [{"symbol": "AMD", "qty": "50"}],
                                  open_orders, results)
    sells = [b for b in broker.submitted if b["side"] == "sell"]
    assert len(sells) == 2
    stops = [s for s in sells if s["type"] == "stop"]
    partials = [s for s in sells if s["type"] == "limit"]
    assert stops[0]["qty"] == 10
    assert partials[0]["qty"] == 10
    assert stops[0]["client_order_id"].endswith("-stop")
    assert 30 + sum(s["qty"] for s in sells) == 50  # never exceeds held


def test_stop_plus_partial_total_never_exceeds_held():
    broker = FakeBroker()
    t = filled_trade(days_ago=2, stop=False)
    t["entry_filled"] = True
    state = {"open": {t["entry_id"]: t}}
    results = {"stops_placed": [], "errors": []}
    held = 50
    runner.place_stops_after_fill(state, broker,
                                  [{"symbol": "AMD", "qty": str(held)}], [], results)
    sells = [b for b in broker.submitted if b["side"] == "sell"]
    assert len(sells) == 2  # stop + partial
    assert sum(s["qty"] for s in sells) <= held  # 1.5x bug fixed
    stops = [s for s in sells if s["type"] == "stop"]
    partials = [s for s in sells if s["type"] == "limit"]
    assert stops[0]["qty"] == 25
    assert partials[0]["qty"] == 25  # half, within room
    assert partials[0]["client_order_id"].endswith("-stop-p")


def test_stop_submit_error_no_state_crash():
    broker = FakeBroker(fail_symbols={"AMD"})
    t = filled_trade(days_ago=2, stop=False)
    t["entry_filled"] = True
    state = {"open": {t["entry_id"]: t}}
    results = {"stops_placed": [], "errors": []}
    runner.place_stops_after_fill(state, broker,
                                  [{"symbol": "AMD", "qty": "50"}], [], results)
    assert results["errors"]
    assert "stop_order_id" not in state["open"][t["entry_id"]]


# ------------------------------------------------------------ C4 same-day db
def test_c4_signaled_today_from_live_paper_db(tmp_path, monkeypatch):
    db = tmp_path / "live_paper.db"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE orders (symbol TEXT, client_order_id TEXT, "
                "created_at TEXT, submitted_at TEXT, filled_at TEXT)")
    con.execute("INSERT INTO orders VALUES (?,?,?,?,?)",
                ("AMD", "sl_short-AMD-20261103-1", today + "T15:00:00Z",
                 None, None))
    con.execute("INSERT INTO orders VALUES (?,?,?,?,?)",
                ("QQQ", "sl_short-QQQ-20261103-1", "2020-01-01T15:00:00Z",
                 None, None))
    con.commit()
    con.close()
    out = runner.c4_signaled_today(["AMD", "QQQ", "TSLA"], db_path=db)
    assert out == {"AMD"}


def test_c4_db_missing_or_bad_returns_empty(tmp_path):
    assert runner.c4_signaled_today(["AMD"], db_path=tmp_path / "nope.db") == set()
    bad = tmp_path / "bad.db"
    bad.write_text("not a database")
    assert runner.c4_signaled_today(["AMD"], db_path=bad) == set()


# ------------------------------------------------------------ dotted symbols
def test_dotted_symbol_label_flow():
    coid = make_client_order_id(RSI2, "BRK.B", "20261103", 1)
    assert coid == "rsi2-BRK.B-20261103-1"
    assert len(make_client_order_id(RSI2, "BF.B", "20261103", 123)) <= 48
