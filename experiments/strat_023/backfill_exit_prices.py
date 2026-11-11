"""C4-FIX-001 — one-off, idempotent backfill of missing exit prices.

The runner used to write exit_date/exit_reason on the position at
exit-SUBMISSION time but never the actual fill: exit_price, exit time
and realized P&L were never recorded, and the exit/stop order rows stayed
status='submitted' forever. This module fills in the missing broker truth
for already-closed positions by reading the REAL Alpaca paper fills.

CLI (dry run is the DEFAULT — it writes NOTHING):

    python -m experiments.strat_023.backfill_exit_prices --db <path>
    python -m experiments.strat_023.backfill_exit_prices --db <path> --apply
    python -m experiments.strat_023.backfill_exit_prices --db <path> \
        --symbols AMD,AVTR,CRL

Safety rules:
  * dry run opens the database READ-ONLY (sqlite3 URI mode=ro) and never
    issues an UPDATE/INSERT;
  * a row that already has exit_price is skipped ("already set") — running
    twice changes nothing;
  * the Alpaca side is read-only (GET only);
  * a broker order that cannot be found leaves the row untouched (we never
    invent a fill).
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import live_paper_runner as lpr  # noqa: E402
import shadow_runner as sr       # noqa: E402

EXIT_KINDS = ("exit_sma5", "exit_time", "stop")


def open_ro(path: Path) -> sqlite3.Connection:
    """Open the database strictly READ-ONLY (dry run: no writes possible)."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def open_rw(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    lpr.migrate_positions(conn)
    return conn


def scan_rows(conn, symbols=None) -> list[sqlite3.Row]:
    q = ("SELECT id, symbol, signal_date, status, entry_price, qty,"
         " exit_date, exit_price, exit_reason FROM positions"
         " WHERE status='closed'"
         # C4-FIX-002: a canceled ENTRY never had an exit fill.
         " AND (exit_reason IS NULL OR exit_reason<>'entry_canceled')")
    params: list = []
    if symbols:
        q += " AND symbol IN (%s)" % ",".join("?" * len(symbols))
        params = list(symbols)
    q += " ORDER BY symbol"
    return list(conn.execute(q, params))


def candidate_coids(conn, symbol: str) -> list[str]:
    """Our own exit/stop client_order_ids for a symbol, from the local DB."""
    rows = conn.execute(
        "SELECT client_order_id, kind FROM orders WHERE symbol=?"
        " AND kind IN ('exit_sma5','exit_time','stop')"
        " ORDER BY created_ts", (symbol,)).fetchall()
    return [r[0] for r in rows]


def find_broker_fill(api, coids: list[str], symbol: str):
    """Return the broker order dict for a FILLED exit/stop order, or None."""
    for c in coids:
        order = None
        try:
            order = api.get_order_by_client_id(c)
        except Exception:
            order = None
        if not order:
            continue
        if str(order.get("status", "")).lower() != "filled":
            continue
        return order
    # fallback: scan the broker's order list for our prefixed orders
    try:
        for o in api.get("/v2/orders", params={"status": "all", "limit": 500}):
            if str(o.get("client_order_id", "")).startswith(lpr.COID_PREFIX) \
                    and o.get("symbol") == symbol \
                    and str(o.get("status", "")).lower() == "filled" \
                    and o.get("side") == "sell":
                return o
    except Exception:
        return None
    return None


def run(db_path: Path, apply: bool = False, symbols=None,
        api_factory=None) -> int:
    conn = (open_rw(db_path) if apply else open_ro(db_path))
    try:
        rows = scan_rows(conn, symbols)
        missing = [r for r in rows if r["exit_price"] in (None,)]
        print(f"backfill_exit_prices: db={db_path} mode="
              f"{'APPLY' if apply else 'DRY-RUN (no writes)'}")
        print(f"closed positions: {len(rows)}; "
              f"missing exit_price: {len(missing)}")
        for r in rows:
            if r["exit_price"] is not None:
                print(f"  {r['symbol']}: already set (exit_price="
                      f"{r['exit_price']}) -> skipped")
        if not missing:
            print("nothing to do")
            return 0

        api = None
        api_error = None
        if api_factory is not None:
            api = api_factory()
        else:
            try:
                key, sec = sr.load_paper_env()
                api = lpr.PaperAPI(key, sec)
            except Exception as e:      # no creds / no network
                api_error = str(e)
        if api is None:
            print("ALPACA VERIFICATION: NOT DONE "
                  f"({api_error or 'no API available'})")
            for r in missing:
                print(f"  {r['symbol']}: id={r['id']} entry_price="
                      f"{r['entry_price']} qty={r['qty']} "
                      f"exit_date={r['exit_date']} exit_reason="
                      f"{r['exit_reason']} exit_price=<missing> "
                      "(no broker data)")
            return 2

        written = 0
        for r in missing:
            coids = candidate_coids(conn, r["symbol"])
            order = find_broker_fill(api, coids, r["symbol"])
            if not order:
                print(f"  {r['symbol']}: id={r['id']} NO FILLED EXIT ORDER "
                      "FOUND ON THE BROKER -> left untouched")
                continue
            px = float(order.get("filled_avg_price") or 0)
            filled_at = order.get("filled_at") or ""
            fill_qty = order.get("filled_qty")
            if not px:
                print(f"  {r['symbol']}: fill has no average price -> "
                      "left untouched")
                continue
            exit_date = filled_at[:10] or r["exit_date"]
            pnl = None
            if r["entry_price"] is not None and r["qty"] is not None:
                pnl = (px - float(r["entry_price"])) * float(r["qty"])
            print(f"  {r['symbol']}: id={r['id']} entry_price="
                  f"{r['entry_price']} qty={r['qty']} -> exit_price={px} "
                  f"filled_qty={fill_qty} filled_at={filled_at} "
                  f"exit_date={exit_date} realized_pnl={pnl} "
                  f"(client_order_id={order.get('client_order_id')})")
            if apply:
                conn.execute(
                    "UPDATE positions SET exit_price=?, exit_date=?,"
                    " exit_time=?, realized_pnl=? WHERE id=?",
                    (px, exit_date, filled_at or None, pnl, r["id"]))
                coid_ = order.get("client_order_id")
                if coid_:
                    conn.execute("UPDATE orders SET status='filled' WHERE"
                                 " client_order_id=?", (coid_,))
            # C4-FIX-002: count the row in BOTH modes. The counter used to
            # live inside `if apply:`, so a dry run always printed
            # "DRY-RUN: 0 position(s) WOULD be updated" even when it had
            # just listed three rows it would update.
            written += 1
        if apply:
            conn.commit()
            print(f"APPLIED: {written} position(s) updated")
        else:
            print(f"DRY-RUN: {written} position(s) WOULD be updated "
                  "(nothing was written)")
        return 0
    finally:
        conn.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    # C4-FIX-002: --db is REQUIRED. It used to default to the live
    # live_paper.db, so a bare invocation could touch the live database.
    ap.add_argument("--db", required=True,
                    help="path to the live_paper.db file (REQUIRED)")
    ap.add_argument("--apply", action="store_true",
                    help="actually write (default is a dry run)")
    ap.add_argument("--symbols", default=None,
                    help="comma separated symbol filter, e.g. AMD,AVTR,CRL")
    args = ap.parse_args(argv)
    symbols = None
    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",")
                   if s.strip()]
    return run(Path(args.db), apply=args.apply, symbols=symbols)


if __name__ == "__main__":
    sys.exit(main())
