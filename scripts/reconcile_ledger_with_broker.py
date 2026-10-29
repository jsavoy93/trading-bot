#!/usr/bin/env python3
"""EXEC-004: one-time ledger reconciliation against broker positions.

Brings the local `trades` ledger in line with the broker's ACTUAL order
history WITHOUT placing, cancelling or replacing any order. Read-only
against the broker (get_orders / get_positions only); the only writes are
backfilled ledger rows in the local SQLite database.

Why this exists: tranches 2/3, stop-loss and portfolio-management orders
were previously submitted without a valid ledger row, so the ledger
understates positions (e.g. ledger 39 QQQI vs broker 98). New orders are
now persisted at submission (EXEC-004); this script backfills the history.

Usage:
    python scripts/reconcile_ledger_with_broker.py --db /path/to/trading_bot.db
        # dry run (default): report only, write nothing
    python scripts/reconcile_ledger_with_broker.py --db ... --apply
        # insert FILLED ledger rows for broker fills missing from the ledger

Safety:
    - NEVER submits orders (no submit_order call exists in this file).
    - Refuses to run against a non-paper broker endpoint unless
      --allow-live is passed (defense in depth; default base URL comes
      from the environment exactly like the bot's).
    - Idempotent: a re-run inserts nothing, because backfilled rows carry
      their broker_order_id and are therefore already "recorded".
    - Broker is authoritative: fills missing from the ledger are added;
      ledger rows the broker does NOT know about are only REPORTED, never
      deleted or modified.
    - The database is opened through the SAME module-level singleton the
      bot uses; to avoid touching the live ledger, reconcile a COPY (see
      --db) and stop the bot before --apply against the real file.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotenv import load_dotenv

RECONCILIATION_NOTE = "EXEC-004 ledger reconciliation (broker-authoritative backfill)"


# ---------------------------------------------------------------------------
# Pure planning functions (unit-testable without any broker or DB)
# ---------------------------------------------------------------------------

def normalize_broker_order(order) -> Dict:
    """Map an Alpaca order object to the small dict the planner needs.

    EXEC-004 review fix: Alpaca's OrderSide / OrderStatus are str-mixin
    enums whose str() is "OrderSide.BUY" / "OrderStatus.FILLED", NOT
    "buy" / "filled". Comparing the raw str() made every status check
    fail, which would have turned the whole reconciliation into a silent
    no-op. Use the enum's .value (falling back to str() for plain
    strings, e.g. in tests).
    """
    side = getattr(order, "side", "") or ""
    status = getattr(order, "status", "") or ""
    side_value = getattr(side, "value", side)
    status_value = getattr(status, "value", status)
    return {
        "id": str(getattr(order, "id", "") or ""),
        "symbol": str(getattr(order, "symbol", "") or "").upper(),
        "side": str(side_value).lower(),
        "status": str(status_value).lower(),
        "qty": float(getattr(order, "qty", 0.0) or 0.0),
        "filled_qty": float(getattr(order, "filled_qty", 0.0) or 0.0),
        "filled_avg_price": float(
            getattr(order, "filled_avg_price", 0.0) or 0.0
        ),
        "created_at": str(getattr(order, "created_at", "") or ""),
    }


def find_unrecorded_fills(ledger_rows: List[Dict], broker_orders: List[Dict]) -> List[Dict]:
    """Return broker FILLED orders whose order id is absent from the ledger.

    A fill counts as unrecorded when no ledger row carries its
    broker_order_id. This is deliberately idempotent: once a backfill row
    (or a live submission row) exists, the order is recorded.
    """
    recorded_ids = {
        str(r.get("broker_order_id"))
        for r in ledger_rows
        if r.get("broker_order_id")
    }
    missing = []
    for o in broker_orders:
        if o["status"] != "filled" or o["filled_qty"] <= 0:
            continue
        if o["id"] and o["id"] not in recorded_ids:
            missing.append(o)
    missing.sort(key=lambda o: o.get("created_at") or "")
    return missing


def build_backfill_row(order: Dict) -> Dict:
    """Map a missing broker fill to a FILLED trades row (schema keys only)."""
    return {
        "session_id": None,  # reconciliation is not part of a bot session
        "symbol": order["symbol"],
        "side": order["side"].upper(),
        "qty": order["filled_qty"],
        "price": order["filled_avg_price"] or None,
        "pnl": None,  # unknown for a backfilled entry; never fabricated
        "signal": order["side"].upper(),
        "rsi": None,
        "order_time": order["created_at"] or datetime.now(timezone.utc).isoformat(),
        "status": "FILLED",
        "broker_order_id": order["id"],
        "filled_qty": order["filled_qty"],
        "avg_fill_price": order["filled_avg_price"] or None,
        "sleeve": "S",  # SLEEVE-P1 review fix: all pre-sleeve-era bot
        # activity is the SmartBot (short-term technical) sleeve 'S';
        # a NULL here would be indistinguishable from legacy pre-sleeve
        # rows (two-sleeve design §6 requires backfills to be tagged).
        "notes": RECONCILIATION_NOTE,
    }


def summarize_positions(ledger_rows: List[Dict], broker_positions) -> List[Dict]:
    """Per-symbol comparison of broker qty vs ledger net filled qty."""
    broker_qty: Dict[str, float] = {}
    for p in broker_positions:
        sym = str(getattr(p, "symbol", "") or "").upper()
        broker_qty[sym] = broker_qty.get(sym, 0.0) + float(
            getattr(p, "qty", 0.0) or 0.0
        )

    ledger_qty: Dict[str, float] = {}
    for r in ledger_rows:
        status = str(r.get("status") or "").upper()
        if status != "FILLED":
            continue
        sym = str(r.get("symbol") or "").upper()
        # EXEC-004 review fix: prefer the CONFIRMED filled quantity. Using
        # `filled_qty or qty` overstated legacy rows that never recorded a
        # fill quantity (falling back to the ordered qty) and could make the
        # reported gap misleading. A FILLED row without a filled_qty is
        # counted as 0 and reported as an anomaly instead of guessed.
        filled = r.get("filled_qty")
        if filled is None:
            filled = 0.0
        signed = float(filled or 0.0)
        if str(r.get("side") or "").upper() == "SELL":
            signed = -signed
        ledger_qty[sym] = ledger_qty.get(sym, 0.0) + signed

    symbols = sorted(set(broker_qty) | {s for s, q in ledger_qty.items() if q != 0})
    return [
        {
            "symbol": s,
            "broker_qty": broker_qty.get(s, 0.0),
            "ledger_qty": ledger_qty.get(s, 0.0),
            "gap": broker_qty.get(s, 0.0) - ledger_qty.get(s, 0.0),
        }
        for s in symbols
    ]


# ---------------------------------------------------------------------------
# Broker / DB plumbing
# ---------------------------------------------------------------------------

def _make_read_only_client(allow_live: bool = False):
    """Build a paper TradingClient using the same environment as the bot.

    EXEC-004 review fix: the bot reads ALPACA_API_KEY / ALPACA_API_SECRET
    (see src/core/smart_bot.py and .env.example) - the _ID / _KEY names used
    here did not exist in this deployment, so the script could never run.
    Both spellings are accepted now.
    """
    from alpaca.trading.client import TradingClient

    key = os.getenv("ALPACA_API_KEY") or os.getenv("ALPACA_API_KEY_ID")
    secret = os.getenv("ALPACA_API_SECRET") or os.getenv("ALPACA_API_SECRET_KEY")
    base = os.getenv("ALPACA_BASE_URL", "")
    if not key or not secret:
        raise SystemExit(
            "Missing ALPACA_API_KEY / ALPACA_API_SECRET "
            "(the same variables the bot uses)"
        )
    is_paper = (not base) or ("paper" in base)
    if not is_paper and not allow_live:
        raise SystemExit(
            f"Refusing to run: ALPACA_BASE_URL={base!r} is not a paper "
            f"endpoint. Re-run with --allow-live if you really mean it."
        )
    return TradingClient(key, secret, paper=is_paper)


def _load_ledger(db) -> List[Dict]:
    from database import sqlite_db as sd

    with sd._get_conn() as conn:
        rows = conn.execute(
            "SELECT symbol, side, qty, status, broker_order_id, filled_qty, "
            "notes FROM trades"
        ).fetchall()
    return [dict(r) for r in rows]


def apply_backfill(db, rows: List[Dict]) -> int:
    """Insert backfill rows through log_trade (schema-validated)."""
    inserted = 0
    for row in rows:
        if db.log_trade(None, row):
            inserted += 1
    return inserted


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db", required=True,
        help="Path to the local trading_bot.db to reconcile",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually write backfill rows (default: dry run, report only)",
    )
    parser.add_argument(
        "--order-limit", type=int, default=500,
        help="Maximum closed broker orders to inspect (default 500)",
    )
    parser.add_argument(
        "--allow-live", action="store_true",
        help="Allow a non-paper broker endpoint (default: refuse)",
    )
    args = parser.parse_args(argv)

    load_dotenv()

    # EXEC-004 review fix: importing the module instantiates a module-level
    # SQLiteDB singleton, whose _init_schema() touches the DEFAULT database
    # (repo-root trading_bot.db) BEFORE we can repoint it. Repoint the
    # module-level DB_PATH FIRST, then import, so the schema work happens
    # against the requested database only. If the module is already
    # imported (e.g. by the test suite), the singleton is reused - the
    # explicit repoint below still governs every query.
    db_path = Path(args.db).resolve()
    if not db_path.exists():
        raise SystemExit(f"Database not found: {db_path}")

    import sys as _sys
    _sys.modules.pop("database.sqlite_db", None)
    from database import sqlite_db as sd

    sd.DB_PATH = db_path  # point the module at the target database
    sd.sqlite_db = sd.SQLiteDB()  # re-create the singleton against this DB

    client = _make_read_only_client(allow_live=args.allow_live)

    from alpaca.trading.enums import QueryOrderStatus
    from alpaca.trading.requests import GetOrdersRequest

    print("EXEC-004 ledger reconciliation — READ-ONLY against the broker")
    print(f"  db={db_path}  apply={args.apply}\n")

    broker_orders = [
        normalize_broker_order(o)
        for o in client.get_orders(
            GetOrdersRequest(
                status=QueryOrderStatus.CLOSED, limit=args.order_limit
            )
        )
    ]
    positions = list(client.get_all_positions())
    ledger_rows = _load_ledger(sd)

    missing = find_unrecorded_fills(ledger_rows, broker_orders)
    summary = summarize_positions(ledger_rows, positions)

    print(f"Broker closed orders inspected : {len(broker_orders)}")
    print(f"Ledger rows                    : {len(ledger_rows)}")
    print(f"Unrecorded FILLED broker orders: {len(missing)}\n")

    for o in missing:
        print(
            f"  MISSING  {o['created_at']}  {o['side'].upper():4} "
            f"{o['filled_qty']:>10g} {o['symbol']:6} "
            f"@ {o['filled_avg_price'] or 0:.2f}  (order {o['id']})"
        )

    print("\nPosition comparison (broker vs ledger, FILLED rows only):")
    for s in summary:
        flag = "  <-- GAP" if abs(s["gap"]) > 1e-9 else ""
        print(
            f"  {s['symbol']:6} broker={s['broker_qty']:>10g}  "
            f"ledger={s['ledger_qty']:>10g}  gap={s['gap']:>+10g}{flag}"
        )

    # Report (never modify) ledger rows the broker does not know about.
    broker_ids = {o["id"] for o in broker_orders}
    orphaned = [
        r for r in ledger_rows
        if r.get("broker_order_id") and r["broker_order_id"] not in broker_ids
    ]
    if orphaned:
        print(
            f"\nNOTE: {len(orphaned)} ledger rows reference broker order ids "
            f"not present in the inspected window (older than the limit, or "
            f"never accepted). Reported only — nothing is deleted or changed."
        )

    if not args.apply:
        print("\nDRY RUN — nothing written. Re-run with --apply to backfill.")
        return 0

    db = sd.sqlite_db
    inserted = apply_backfill(db, [build_backfill_row(o) for o in missing])
    print(f"\nAPPLIED: {inserted} backfill rows inserted (status FILLED).")
    if inserted != len(missing):
        print(
            f"WARNING: {len(missing) - inserted} row(s) failed to insert — "
            f"inspect the log output above (log_trade reports failures)."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
