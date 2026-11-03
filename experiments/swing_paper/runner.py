"""STRAT-SWING-PAPER — single-shot paper swing executor (C4 + RSI(2)).

Places PAPER orders ONLY (https://paper-api.alpaca.markets) with per-strategy
client_order_id labels (see rules.py). Default is DRY-RUN: nothing is sent
unless --execute is passed. Credentials come exclusively from the
ALPACA_API_KEY / ALPACA_API_SECRET environment variables (paper keys).

Never loops, never installs timers. Run it once per day from a human-approved
schedule. Exit logic: stop orders ride with each entry; time exits and
chandelier updates are applied on subsequent runs from the persisted state
file (logs/state.json, gitignored).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd
import requests

import signals

from rules import (C4, RSI2, BudgetSlices, apply_rsi2_daily_cap,
                   make_labels, next_sequence, rsi2_overlap_blocked)

HERE = Path(__file__).resolve().parent
LOG_DIR = Path(os.environ.get("STRAT_SWING_LOG_DIR", HERE / "logs"))
STATE_PATH = LOG_DIR / "state.json"
EVENTS_PATH = LOG_DIR / "swing_events.jsonl"

PAPER_TRADING_URL = "https://paper-api.alpaca.markets"
DATA_URL = "https://data.alpaca.markets"
LOOKBACK_DAYS = 420  # ~290 trading days, same as the shadow runners
KILL_SWITCH = HERE / "KILL_SWITCH"  # halt immediately if present


def fail(msg: str, code: int = 2):
    print(f"swing-paper: {msg}", file=sys.stderr)
    sys.exit(code)


def load_credentials() -> tuple[str, str]:
    key = os.environ.get("ALPACA_API_KEY")
    sec = os.environ.get("ALPACA_API_SECRET")
    if not key or not sec:
        fail("ALPACA_API_KEY / ALPACA_API_SECRET env vars required (paper keys)")
    return key, sec


class AlpacaPaper:
    """Minimal Alpaca PAPER REST client. Trading URL is hard-locked to
    paper-api.alpaca.markets; there is no code path to the live API."""

    def __init__(self, key: str, secret: str, dry_run: bool = True):
        if "paper-api.alpaca.markets" not in PAPER_TRADING_URL:
            fail("refusing to run: trading URL is not the paper endpoint")
        self.key, self.secret = key, secret
        self.dry_run = dry_run
        self.session = requests.Session()

    def _headers(self):
        return {"APCA-API-KEY-ID": self.key, "APCA-API-SECRET-KEY": self.secret}

    # ---- reads ----
    def get_account(self) -> dict:
        r = self.session.get(f"{PAPER_TRADING_URL}/v2/account",
                             headers=self._headers(), timeout=15)
        r.raise_for_status()
        return r.json()

    def get_positions(self) -> list:
        r = self.session.get(f"{PAPER_TRADING_URL}/v2/positions",
                             headers=self._headers(), timeout=15)
        r.raise_for_status()
        return r.json()

    def get_orders(self, status: str = "open", limit: int = 500) -> list:
        r = self.session.get(f"{PAPER_TRADING_URL}/v2/orders",
                             params={"status": status, "limit": limit,
                                     "direction": "desc", "nested": "true"},
                             headers=self._headers(), timeout=15)
        r.raise_for_status()
        return r.json()

    def daily_bars(self, symbol: str) -> pd.DataFrame | None:
        end = datetime.now(timezone.utc)
        start = pd.Timestamp(end) - pd.Timedelta(days=LOOKBACK_DAYS)
        r = self.session.get(
            f"{DATA_URL}/v2/stocks/{symbol}/bars",
            params={"timeframe": "1Day", "start": start.isoformat(),
                    "end": end.isoformat(), "limit": 1000, "adjustment": "split"},
            headers=self._headers(), timeout=30)
        if r.status_code != 200:
            return None
        rows = r.json().get("bars", [])
        if not rows:
            return None
        return pd.DataFrame(rows)

    # ---- writes (suppressed in dry-run) ----
    def submit_order(self, body: dict) -> dict:
        if self.dry_run:
            return {"dry_run": True, "body": body}
        r = self.session.post(f"{PAPER_TRADING_URL}/v2/orders",
                              json=body, headers=self._headers(), timeout=15)
        if r.status_code not in (200, 201):
            return {"error": r.text}
        return r.json()

    def cancel_order(self, order_id: str) -> bool:
        if self.dry_run:
            return True
        r = self.session.delete(f"{PAPER_TRADING_URL}/v2/orders/{order_id}",
                                headers=self._headers(), timeout=15)
        return r.status_code in (200, 204)


# ------------------------------------------------------------- state
def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {"open": {}}  # open[client_order_id of entry] = trade record


def save_state(state: dict) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1))
    tmp.replace(STATE_PATH)


def log_event(evt: dict) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with EVENTS_PATH.open("a") as f:
        f.write(json.dumps(evt, default=str) + "\n")


# ------------------------------------------------------------- helpers
def mmdd_today() -> str:
    return datetime.now(timezone.utc).strftime("%m%d")


def strategy_open_notional(state: dict, strategy: str) -> float:
    return sum(t.get("entry_notional", 0.0) for t in state["open"].values()
               if t.get("strategy") == strategy)


def all_client_order_ids(api: AlpacaPaper) -> list:
    labels = []
    for o in api.get_orders(status="open") + api.get_orders(status="closed"):
        coid = o.get("client_order_id")
        if coid:
            labels.append(coid)
    return labels


def desired_entry_order(api: AlpacaPaper, symbol: str, qty: int,
                        strategy: str, mmdd: str, existing_labels: list):
    n = next_sequence(existing_labels, strategy, symbol, mmdd)
    entry_id, stop_id = make_labels(strategy, symbol, mmdd, n)
    return n, entry_id, stop_id


def run(args):
    if KILL_SWITCH.exists():
        fail("kill switch present — halting", code=3)
    key, sec = load_credentials()
    api = AlpacaPaper(key, sec, dry_run=not args.execute)

    state = load_state()
    acct = api.get_account()
    portfolio_value = float(acct["portfolio_value"])
    slices = BudgetSlices(
        portfolio_value=portfolio_value,
        short_term_bucket_pct=args.bucket_pct,
        c4_share=args.c4_share,
        rsi2_share=1.0 - args.c4_share,
    )
    positions = api.get_positions()
    open_orders = api.get_orders(status="open")
    held_symbols = {p["symbol"] for p in positions}
    existing_labels = all_client_order_ids(api)
    mmdd = mmdd_today()

    bars = {}
    for sym in args.symbols + ["SPY"]:
        b = api.daily_bars(sym)
        if b is not None:
            bars[sym] = signals.add_indicators(b)

    # ---------------------------------------------------- C4 first
    c4_buys = []
    for sym in args.symbols:
        df = bars.get(sym)
        if df is None or sym in held_symbols:
            continue
        if signals.c4_entry_signal(df):
            c4_buys.append(sym)

    results = {"c4": [], "rsi2": [], "dry_run": api.dry_run}
    for sym in c4_buys:
        df = bars[sym]
        last = len(df) - 1
        px = float(df["close"].iloc[last])  # sizing ref; entry is next open
        qty = slices.size_order(C4, px, open_notional=strategy_open_notional(state, C4))
        if qty <= 0:
            log_event({"ts": datetime.now(timezone.utc), "type": "skip",
                       "strategy": C4, "symbol": sym, "reason": "slice_exhausted"})
            continue
        n, entry_id, stop_id = desired_entry_order(
            api, sym, qty, C4, mmdd, existing_labels)
        stop_px = signals.c4_stop_price(px, float(df["atr14"].iloc[last]))
        buy = api.submit_order({
            "symbol": sym, "qty": qty, "side": "buy", "type": "market",
            "time_in_force": "day", "client_order_id": entry_id,
        })
        stop = api.submit_order({
            "symbol": sym, "qty": qty, "side": "sell", "type": "stop",
            "stop_price": round(stop_px, 2), "time_in_force": "gtc",
            "client_order_id": stop_id,
        })
        existing_labels += [entry_id, stop_id]
        state["open"][entry_id] = {
            "strategy": C4, "symbol": sym, "qty": qty, "date": mmdd,
            "entry_notional": qty * px, "stop_id": stop_id,
            "signal_date": str(df["date"].iloc[last]),
            "atr14": float(df["atr14"].iloc[last]),
            "entry_date": str(df["date"].iloc[min(last + 1, len(df) - 1)]),
        }
        results["c4"].append({"symbol": sym, "qty": qty, "entry_id": entry_id,
                              "stop_id": stop_id, "stop_px": round(stop_px, 2),
                              "buy": buy, "stop": stop})
        log_event({"ts": datetime.now(timezone.utc), "type": "buy",
                   "strategy": C4, "symbol": sym, "qty": qty,
                   "client_order_id": entry_id, "stop_client_order_id": stop_id,
                   "dry_run": api.dry_run})

    # ---------------------------------------------------- RSI(2) second
    spy_bars = bars.get("SPY")
    candidates = []
    if spy_bars is not None:
        for sym in args.symbols:
            df = bars.get(sym)
            if df is None or sym in held_symbols:
                continue
            if rsi2_overlap_blocked(positions, open_orders, sym, mmdd):
                log_event({"ts": datetime.now(timezone.utc), "type": "skip",
                           "strategy": RSI2, "symbol": sym,
                           "reason": "c4_overlap"})
                continue
            if signals.rsi2_entry_signal(df, spy_bars):
                candidates.append({"symbol": sym,
                                   "rsi2": float(df["rsi2"].iloc[-1])})
    selected = apply_rsi2_daily_cap(candidates, cap=args.rsi2_cap)

    for cand in selected:
        sym = cand["symbol"]
        df = bars[sym]
        last = len(df) - 1
        px = float(df["close"].iloc[last])
        qty = slices.size_order(RSI2, px,
                                open_notional=strategy_open_notional(state, RSI2))
        if qty <= 0:
            log_event({"ts": datetime.now(timezone.utc), "type": "skip",
                       "strategy": RSI2, "symbol": sym, "reason": "slice_exhausted"})
            continue
        n, entry_id, stop_id = desired_entry_order(
            api, sym, qty, RSI2, mmdd, existing_labels)
        init_stop, chand_stop, partial_px = signals.rsi2_stops(
            px, float(df["atr14"].iloc[last]))
        buy = api.submit_order({
            "symbol": sym, "qty": qty, "side": "buy", "type": "market",
            "time_in_force": "day", "client_order_id": entry_id,
        })
        stop = api.submit_order({
            "symbol": sym, "qty": qty, "side": "sell", "type": "stop",
            "stop_price": round(init_stop, 2), "time_in_force": "gtc",
            "client_order_id": stop_id,
        })
        half = qty // 2
        partial = None
        if half >= 1:
            partial = api.submit_order({
                "symbol": sym, "qty": half, "side": "sell", "type": "limit",
                "limit_price": round(partial_px, 2), "time_in_force": "gtc",
                "client_order_id": stop_id + "-p",
            })
        existing_labels += [entry_id, stop_id]
        state["open"][entry_id] = {
            "strategy": RSI2, "symbol": sym, "qty": qty, "date": mmdd,
            "entry_notional": qty * px, "stop_id": stop_id,
            "signal_date": str(df["date"].iloc[last]),
            "atr14": float(df["atr14"].iloc[last]),
            "entry_date": str(df["date"].iloc[min(last + 1, len(df) - 1)]),
        }
        results["rsi2"].append({"symbol": sym, "qty": qty, "rsi2": cand["rsi2"],
                                "entry_id": entry_id, "stop_id": stop_id,
                                "stop_px": round(init_stop, 2),
                                "partial_px": round(partial_px, 2),
                                "buy": buy, "stop": stop, "partial": partial})
        log_event({"ts": datetime.now(timezone.utc), "type": "buy",
                   "strategy": RSI2, "symbol": sym, "qty": qty,
                   "client_order_id": entry_id, "stop_client_order_id": stop_id,
                   "rsi2": cand["rsi2"], "dry_run": api.dry_run})

    save_state(state)
    print(json.dumps(results, indent=2, default=str))
    print(f"mode: {'DRY-RUN (nothing sent)' if api.dry_run else 'PAPER ORDERS SENT'}")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbols", nargs="+", required=True,
                   help="candidate symbols to evaluate (C4 then RSI(2))")
    p.add_argument("--execute", action="store_true",
                   help="actually submit PAPER orders (default: dry-run)")
    p.add_argument("--bucket-pct", type=float, default=0.30,
                   help="short-term bucket share of portfolio (default 0.30)")
    p.add_argument("--c4-share", type=float, default=0.50,
                   help="C4 share of the bucket; RSI(2) gets the rest")
    p.add_argument("--rsi2-cap", type=int, default=4,
                   help="max RSI(2) new buys per day (default 4)")
    args = p.parse_args(argv)
    run(args)


if __name__ == "__main__":
    main()
