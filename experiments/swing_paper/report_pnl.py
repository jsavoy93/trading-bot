"""STRAT-SWING-PAPER — per-strategy P&L report (paper account).

Reads Alpaca PAPER orders/fills and positions, attributes each order to a
strategy by client_order_id label prefix (c4-, rsi2-), and prints trades,
open positions, realized P&L, and unrealized P&L per strategy.

Usage:
  python report_pnl.py [--json] [--since YYYY-MM-DD]

Credentials come exclusively from ALPACA_API_KEY / ALPACA_API_SECRET
(paper keys). Read-only: GET requests only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from rules import C4, RSI2, parse_label  # noqa: E402

PAPER_TRADING_URL = "https://paper-api.alpaca.markets"
STRATEGIES = (C4, RSI2)


def fail(msg: str, code: int = 2):
    print(f"swing-paper-pnl: {msg}", file=sys.stderr)
    sys.exit(code)


def _headers():
    key = os.environ.get("ALPACA_API_KEY")
    sec = os.environ.get("ALPACA_API_SECRET")
    if not key or not sec:
        fail("ALPACA_API_KEY / ALPACA_API_SECRET env vars required (paper keys)")
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec}


def fetch_all_orders(since: str | None) -> list:
    headers = _headers()
    out, token = [], None
    while True:
        params = {"status": "closed", "limit": 500, "direction": "desc",
                  "nested": "true"}
        if since:
            params["after"] = since
        if token:
            params["page_token"] = token
        r = requests.get(f"{PAPER_TRADING_URL}/v2/orders", params=params,
                         headers=headers, timeout=20)
        r.raise_for_status()
        batch = r.json()
        out.extend(batch)
        token = r.headers.get("Page-Token")
        if not token or not batch:
            break
    return out


def fetch_positions() -> list:
    r = requests.get(f"{PAPER_TRADING_URL}/v2/positions",
                     headers=_headers(), timeout=20)
    r.raise_for_status()
    return r.json()


def fetch_last_quote(symbol: str) -> float | None:
    r = requests.get(f"https://data.alpaca.markets/v2/stocks/{symbol}/snapshots",
                     headers=_headers(), timeout=20)
    if r.status_code != 200:
        return None
    snap = r.json().get(symbol, {})
    qp = (snap.get("latestQuote") or {}).get("bp")
    if qp:
        return float(qp)
    tb = (snap.get("latestTrade") or {}).get("p")
    return float(tb) if tb else None


def attribute(label: str) -> str | None:
    try:
        return parse_label(label)["strategy"]
    except ValueError:
        return None


def collect(since: str | None = None) -> dict:
    orders = fetch_all_orders(since)
    fills = defaultdict(list)   # strategy -> list of fill records
    for o in orders:
        strat = attribute(o.get("client_order_id") or "")
        if strat is None:
            continue
        if o.get("status") not in ("filled", "partially_filled"):
            continue
        fills[strat].append(o)

    realized = defaultdict(float)
    trades = defaultdict(list)
    for strat in STRATEGIES:
        # FIFO match buys/sells per symbol from fill records
        by_sym = defaultdict(list)
        for o in fills[strat]:
            for leg in o.get("trades", []) or []:
                by_sym[o["symbol"]].append({
                    "side": o["side"], "qty": float(leg["qty"]),
                    "px": float(leg["price"]), "ts": leg["timestamp"],
                    "id": o.get("client_order_id"),
                })
        for sym, legs in by_sym.items():
            legs.sort(key=lambda l: l["ts"])
            buys = []  # FIFO queue of (qty, px)
            pnl = 0.0
            for leg in legs:
                if leg["side"] == "buy":
                    buys.append([leg["qty"], leg["px"]])
                else:
                    q = leg["qty"]
                    while q > 1e-9 and buys:
                        take = min(q, buys[0][0])
                        pnl += take * (leg["px"] - buys[0][1])
                        buys[0][0] -= take
                        q -= take
                        if buys[0][0] <= 1e-9:
                            buys.pop(0)
                    trades[strat].append({
                        "symbol": sym, "sell_id": leg["id"],
                        "qty": leg["qty"], "exit_px": leg["px"], "pnl": pnl,
                    })
                    pnl = 0.0  # per-trade attribution reset at each sell
        realized[strat] = sum(t["pnl"] for t in trades[strat])

    # open positions attributed by strategy label of the symbol's most
    # recent filled labeled buy
    last_buy_label = {}
    for o in orders:
        strat = attribute(o.get("client_order_id") or "")
        if strat and o["side"] == "buy" and o.get("status") in ("filled", "partially_filled"):
            last_buy_label[o["symbol"]] = (o["filled_at"], strat)
    positions = fetch_positions()
    open_by_strat = defaultdict(list)
    unrealized = defaultdict(float)
    for p in positions:
        strat = last_buy_label.get(p["symbol"], (None, None))[1]
        if strat is None:
            continue
        qty = float(p["qty"])
        avg = float(p["avg_entry_price"])
        last = fetch_last_quote(p["symbol"]) or float(p["current_price"] or avg)
        upnl = qty * (last - avg)
        unrealized[strat] += upnl
        open_by_strat[strat].append({
            "symbol": p["symbol"], "qty": qty, "avg_entry": avg,
            "last": last, "unrealized": round(upnl, 2),
        })

    return {
        "trades": {s: trades.get(s, []) for s in STRATEGIES},
        "open": {s: open_by_strat.get(s, []) for s in STRATEGIES},
        "realized": {s: round(realized.get(s, 0.0), 2) for s in STRATEGIES},
        "unrealized": {s: round(unrealized.get(s, 0.0), 2) for s in STRATEGIES},
    }


def print_report(rep: dict):
    for strat in STRATEGIES:
        print(f"\n=== strategy: {strat} ===")
        print(f"realized P&L:   ${rep['realized'][strat]:,.2f}")
        print(f"unrealized P&L: ${rep['unrealized'][strat]:,.2f}")
        print(f"open positions ({len(rep['open'][strat])}):")
        for p in rep["open"][strat]:
            print(f"  {p['symbol']:<6} qty={p['qty']:<8g} avg={p['avg_entry']:<10.2f}"
                  f" last={p['last']:<10.2f} unrealized={p['unrealized']:.2f}")
        print(f"closed trades ({len(rep['trades'][strat])}):")
        for t in rep["trades"][strat]:
            print(f"  {t['symbol']:<6} qty={t['qty']:<8g} exit={t['exit_px']:<10.2f}"
                  f" pnl={t['pnl']:.2f}  ({t['sell_id']})")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--json", action="store_true", help="emit JSON instead of table")
    p.add_argument("--since", default=None, help="only orders after YYYY-MM-DD")
    args = p.parse_args(argv)
    rep = collect(args.since)
    rep["generated_at"] = datetime.now(timezone.utc).isoformat()
    if args.json:
        print(json.dumps(rep, indent=2))
    else:
        print_report(rep)


if __name__ == "__main__":
    main()
