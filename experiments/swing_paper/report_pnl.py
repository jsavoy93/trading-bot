"""STRAT-SWING-PAPER — per-strategy P&L report (paper account).

Reads Alpaca PAPER closed orders + fills and positions, attributes each
order to a strategy by client_order_id label (rsi2-..., including -stop and
-stop-p variants), and prints trades, open positions, realized P&L, and
unrealized P&L per strategy.

P&L facts (fixed per PR #135 review, round 3 fix I):
  * Orders carry NO `trades` field on Alpaca v2. Fills are quantified via
    filled_qty / filled_avg_price on each order (and the account activities
    FILL feed is available as a cross-check via --activities).
  * Closed orders are fetched with nested=false (we never need nested
    leg objects; rsi2 orders are simple market/stop orders).
  * Orders paginate with `after` windows + id de-dup; activities paginate
    with direction=asc + `page_token` per the Alpaca docs, de-duped by
    activity id (equal timestamps re-fetched under >= semantics are
    collapsed by id).
  * Partially-filled then canceled orders ARE included (filled_qty > 0).
  * Open positions are attributed by LABELED fills only (net labeled
    buys-sells per symbol per strategy), never by "last buyer".

Usage:
  python report_pnl.py [--json] [--since YYYY-MM-DD] [--activities]

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

from rules import RSI2, parse_label  # noqa: E402

PAPER_TRADING_URL = "https://paper-api.alpaca.markets"
STRATEGIES = (RSI2,)
PAGE = 500


def fail(msg: str, code: int = 2):
    print(f"swing-paper-pnl: {msg}", file=sys.stderr)
    sys.exit(code)


def _headers():
    key = os.environ.get("ALPACA_API_KEY")
    sec = os.environ.get("ALPACA_API_SECRET")
    if not key or not sec:
        fail("ALPACA_API_KEY / ALPACA_API_SECRET env vars required (paper keys)")
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec}


def _is_fill(order: dict) -> bool:
    """A fill for P&L purposes: filled or partially_filled, or canceled
    with a partial fill (filled_qty > 0)."""
    qty = float(order.get("filled_qty") or 0)
    if qty <= 0:
        return False
    return order.get("status") in ("filled", "partially_filled", "canceled")


def fetch_all_orders(since: str | None) -> list:
    """Paginate closed orders with after windows (nested=false — fix I).
    The window advances by the FULL timestamp of the last order; orders
    sharing that timestamp may be re-served by the API (>= semantics) and
    are collapsed by id de-dup, never double-counted."""
    headers = _headers()
    out, seen = [], set()
    after = since
    while True:
        params = {"status": "closed", "limit": PAGE, "direction": "asc",
                  "nested": "false"}
        if after:
            params["after"] = after
        r = requests.get(f"{PAPER_TRADING_URL}/v2/orders", params=params,
                         headers=headers, timeout=20)
        r.raise_for_status()
        batch = r.json()
        for o in batch:
            oid = o.get("id")
            if oid is None or oid not in seen:
                if oid is not None:
                    seen.add(oid)
                out.append(o)
        if len(batch) < PAGE:
            break
        # advance the window past the last order we already have
        last_ts = batch[-1].get("submitted_at") or batch[-1].get("created_at")
        if not last_ts or last_ts == after:
            break  # cannot advance further — terminate instead of looping
        after = last_ts
    return out


def fetch_fill_activities(since: str | None = None) -> list:
    """Account activities FILL feed (optional cross-check). Paginated
    per the Alpaca docs (fix I): direction=asc with `page_token` set to
    the id of the last activity of the previous page (after only on the
    first request). De-duped by activity id, so activities sharing an
    equal timestamp are never counted twice."""
    headers = _headers()
    out, seen = [], set()
    page_token = None
    while True:
        params = {"activity_types": "FILL", "page_size": 100,
                  "direction": "asc"}
        if since and page_token is None:
            params["after"] = since
        if page_token is not None:
            params["page_token"] = page_token
        r = requests.get(f"{PAPER_TRADING_URL}/v2/account/activities",
                         params=params, headers=headers, timeout=20)
        r.raise_for_status()
        batch = r.json()
        for a in batch:
            aid = a.get("id")
            if aid is not None:
                if aid in seen:
                    continue
                seen.add(aid)
            out.append(a)
        if len(batch) < 100:
            break
        token = batch[-1].get("id")
        if not token or token == page_token:
            break  # cannot advance — terminate instead of looping
        page_token = token
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


def _fill_legs(order: dict) -> list:
    """Quantify an order's fills via filled_qty/filled_avg_price.
    Returns [] when unfilled; a single leg otherwise."""
    qty = float(order.get("filled_qty") or 0)
    if qty <= 0:
        return []
    px = float(order.get("filled_avg_price") or 0)
    return [{"side": order.get("side"), "qty": qty, "px": px,
             "ts": order.get("filled_at") or order.get("submitted_at") or "",
             "id": order.get("client_order_id"),
             "kind": "fill"}]


def _fifo_close(buys: list, sell_qty: float, sell_px: float) -> float:
    """Close `sell_qty` against the FIFO `buys` queue [[qty, px], ...];
    returns realized pnl. Mutates `buys` (leftover = open inventory)."""
    q, pnl = sell_qty, 0.0
    while q > 1e-9 and buys:
        take = min(q, buys[0][0])
        pnl += take * (sell_px - buys[0][1])
        buys[0][0] -= take
        q -= take
        if buys[0][0] <= 1e-9:
            buys.pop(0)
    return pnl


def collect_from_orders(orders: list, since: str | None = None) -> dict:
    fills = defaultdict(list)   # strategy -> list of fill orders
    for o in orders:
        strat = attribute(o.get("client_order_id") or "")
        if strat is None:
            continue
        if not _is_fill(o):
            continue
        fills[strat].append(o)

    realized = defaultdict(float)
    trades = defaultdict(list)
    leftovers = defaultdict(dict)  # strategy -> symbol -> FIFO buys left
    for strat in STRATEGIES:
        by_sym = defaultdict(list)
        for o in fills[strat]:
            for leg in _fill_legs(o):
                by_sym[o["symbol"]].append(leg)
        for sym in sorted(by_sym):
            legs = sorted(by_sym[sym], key=lambda l: l["ts"])
            buys = []  # FIFO queue of [qty, px]
            for leg in legs:
                if leg["side"] == "buy":
                    buys.append([leg["qty"], leg["px"]])
                else:
                    pnl = _fifo_close(buys, leg["qty"], leg["px"])
                    trades[strat].append({
                        "symbol": sym, "sell_id": leg["id"],
                        "qty": leg["qty"], "exit_px": leg["px"],
                        "pnl": round(pnl, 2),
                    })
                    realized[strat] += pnl
            leftovers[strat][sym] = buys
        realized[strat] = realized.get(strat, 0.0)

    # Open positions attributed by LABELED FILLS ONLY: net labeled
    # buys-minus-sells per (strategy, symbol). A position with no labeled
    # net-positive strategy is NOT attributed to any strategy here.
    net = defaultdict(dict)  # strategy -> symbol -> net qty
    for strat in STRATEGIES:
        by_sym = defaultdict(float)
        for o in fills[strat]:
            signed = float(o.get("filled_qty") or 0) * (
                1 if o.get("side") == "buy" else -1)
            by_sym[o["symbol"]] += signed
        net[strat] = dict(by_sym)

    positions = fetch_positions()
    open_by_strat = defaultdict(list)
    unrealized = defaultdict(float)
    for p in positions:
        claimants = [(net[s].get(p["symbol"], 0.0), s)
                     for s in STRATEGIES if net[s].get(p["symbol"], 0.0) > 0]
        if not claimants:
            continue  # unlabeled position — not attributed by last buyer
        _, strat = max(claimants)  # deterministic: largest net labeled qty
        # FIX I(3): use the strategy-LABELED net qty (capped by what the
        # account actually holds), not the whole-account quantity.
        qty = min(net[strat].get(p["symbol"], 0.0), float(p["qty"]))
        # avg entry from the strategy's own labeled FIFO leftovers
        inv = leftovers[strat].get(p["symbol"]) or []
        inv_qty = sum(q for q, _px in inv)
        avg = (sum(q * px for q, px in inv) / inv_qty) if inv_qty > 0 \
            else float(p["avg_entry_price"] or 0)
        last = fetch_last_quote(p["symbol"]) or float(p["current_price"] or avg)
        upnl = qty * (last - avg)
        unrealized[strat] += upnl
        open_by_strat[strat].append({
            "symbol": p["symbol"], "qty": qty, "avg_entry": round(avg, 2),
            "last": last, "unrealized": round(upnl, 2),
            "account_qty": float(p["qty"]),
        })

    return {
        "trades": {s: trades.get(s, []) for s in STRATEGIES},
        "open": {s: open_by_strat.get(s, []) for s in STRATEGIES},
        "realized": {s: round(realized.get(s, 0.0), 2) for s in STRATEGIES},
        "unrealized": {s: round(unrealized.get(s, 0.0), 2) for s in STRATEGIES},
    }


def collect_from_activities(activities: list, order_id_map: dict | None = None) -> dict:
    """Cross-check path: realized P&L from the FILL activity feed.
    FIX I(2): the activities feed provides symbol/side/qty/price and the
    ALPACA order_id — it does NOT provide `pl` or `client_order_id`.
    Attribution therefore goes through order_id -> client_order_id (from
    the fetched orders) and realized P&L is computed by FIFO from the
    fill legs themselves."""
    id_map = order_id_map or {}
    fills_by_strat = defaultdict(lambda: defaultdict(list))
    for a in activities or []:
        kind = str(a.get("activity_type") or a.get("type") or "").upper()
        if kind and kind != "FILL":
            continue
        coid = a.get("client_order_id") or id_map.get(a.get("order_id")) or ""
        strat = attribute(coid)
        if strat is None:
            continue
        fills_by_strat[strat][a.get("symbol")].append({
            "side": a.get("side"), "qty": float(a.get("qty") or 0),
            "px": float(a.get("price") or 0),
            "ts": a.get("transaction_time") or a.get("timestamp") or "",
            "id": coid,
        })
    realized = defaultdict(float)
    trades = defaultdict(list)
    for strat in STRATEGIES:
        for sym, legs in fills_by_strat.get(strat, {}).items():
            buys = []
            for leg in sorted(legs, key=lambda l: l["ts"]):
                if leg["side"] == "buy":
                    buys.append([leg["qty"], leg["px"]])
                else:
                    pnl = _fifo_close(buys, leg["qty"], leg["px"])
                    realized[strat] += pnl
                    trades[strat].append({
                        "symbol": sym, "qty": leg["qty"], "px": leg["px"],
                        "pnl": round(pnl, 2), "ts": leg["ts"],
                        "id": leg["id"],
                    })
    return {
        "trades": {s: trades.get(s, []) for s in STRATEGIES},
        "realized": {s: round(realized.get(s, 0.0), 2) for s in STRATEGIES},
    }


def collect(since: str | None = None, use_activities: bool = False) -> dict:
    orders = fetch_all_orders(since)
    rep = collect_from_orders(orders, since)
    if use_activities:
        id_map = {o.get("id"): o.get("client_order_id") for o in orders}
        rep["activities_check"] = collect_from_activities(
            fetch_fill_activities(since), id_map)
    return rep


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
    if "activities_check" in rep:
        ac = rep["activities_check"]
        for strat in STRATEGIES:
            print(f"[activities FILL cross-check] {strat}: "
                  f"realized=${ac['realized'][strat]:,.2f} "
                  f"fills={len(ac['trades'][strat])}")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--json", action="store_true", help="emit JSON instead of table")
    p.add_argument("--since", default=None, help="only orders after YYYY-MM-DD")
    p.add_argument("--activities", action="store_true",
                   help="add activities FILL-feed realized cross-check")
    args = p.parse_args(argv)
    rep = collect(args.since, use_activities=args.activities)
    rep["generated_at"] = datetime.now(timezone.utc).isoformat()
    if args.json:
        print(json.dumps(rep, indent=2))
    else:
        print_report(rep)


if __name__ == "__main__":
    main()
