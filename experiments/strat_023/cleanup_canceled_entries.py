"""C4-FIX-002 — one-off cleanup of CANCELED/EXPIRED/REJECTED entry rows.

An entry order that the broker reports as canceled (or expired/rejected)
used to leave its position row stuck at status='pending_fill' forever.
Such a row holds a MAX_POSITIONS slot and sleeve notional for a position
that does not exist.

This module applies the same terminal handling as the live runner
(live_paper_runner.mark_entry_canceled) but as a ONE-OFF BATCH command.

CLI (dry run is the DEFAULT — it writes NOTHING):

    python -m experiments.strat_023.cleanup_canceled_entries --db <path>
    python -m experiments.strat_023.cleanup_canceled_entries --db <path> --apply

Safety rules:
  * --db is REQUIRED (no default) so a bare invocation can never touch the
    live database;
  * dry run opens the database READ-ONLY (sqlite3 URI mode=ro);
  * the Alpaca side is read-only (GET only);
  * an order that cannot be found on the broker leaves the row untouched
    (we never invent a fill);
  * a partially filled order is left untouched (the runner owns that case);
  * idempotent: a second run finds nothing to do.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import live_paper_runner as lpr  # noqa: E402
import shadow_runner as sr       # noqa: E402

TERMINAL = lpr.TERMINAL_NOT_FILLED_STATUSES


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


def candidates(conn) -> list[sqlite3.Row]:
    """Pending entry positions whose entry order is still 'submitted'."""
    return list(conn.execute(
        "SELECT p.id AS id, p.symbol AS symbol, p.signal_date AS signal_date,"
        " p.entry_order_coid AS entry_order_coid, o.alpaca_order_id"
        " AS alpaca_order_id FROM positions p LEFT JOIN orders o"
        " ON o.client_order_id = p.entry_order_coid"
        " WHERE p.status='pending_fill' ORDER BY p.id"))


def lookup(api, conn, coid_: str, alpaca_order_id):
    """Read-only broker lookup for one of OUR entry orders."""
    try:
        order = api.get_order_by_client_id(coid_)
    except Exception:
        order = None
    if order:
        return order
    if alpaca_order_id:
        try:
            return api.get_order(alpaca_order_id)
        except Exception:
            return None
    return None


def run(db_path: Path, apply: bool = False, api_factory=None) -> int:
    conn = (open_rw(db_path) if apply else open_ro(db_path))
    try:
        rows = candidates(conn)
        print(f"cleanup_canceled_entries: db={db_path} mode="
              f"{'APPLY' if apply else 'DRY-RUN (no writes)'}")
        print(f"pending_fill entry positions: {len(rows)}")
        if not rows:
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
            for r in rows:
                print(f"  {r['symbol']}: id={r['id']} coid="
                      f"{r['entry_order_coid']} (no broker data)")
            return 2

        changed = 0
        for r in rows:
            coid_ = r["entry_order_coid"]
            order = lookup(api, conn, coid_, r["alpaca_order_id"])
            if not order:
                print(f"  {r['symbol']}: id={r['id']} coid={coid_} NOT FOUND "
                      "ON THE BROKER -> left untouched")
                continue
            status = str(order.get("status") or "").lower()
            fill_qty = float(order.get("filled_qty") or 0) or 0.0
            if status not in TERMINAL:
                print(f"  {r['symbol']}: id={r['id']} coid={coid_} "
                      f"status={status} -> not terminal, left untouched")
                continue
            if fill_qty > 0:
                print(f"  {r['symbol']}: id={r['id']} coid={coid_} "
                      f"status={status} filled_qty={fill_qty} "
                      "-> partial fill, left untouched (runner owns it)")
                continue
            exit_date = (order.get("updated_at") or order.get("created_at")
                         or "")[:10] or None
            print(f"  {r['symbol']}: id={r['id']} coid={coid_} "
                  f"status={status} filled_qty=0 -> "
                  f"{'CLOSE as canceled' if apply else 'WOULD close as canceled'}"
                  f" (no P&L, exit_reason=entry_canceled)")
            # count the row in BOTH modes (the dry run must report how
            # many rows WOULD be closed, C4-FIX-002)
            changed += 1
            if apply:
                lpr.mark_entry_canceled(conn, r["id"], coid_, status,
                                        exit_date)
        if apply:
            conn.commit()
            print(f"APPLIED: {changed} position(s) closed as canceled")
        else:
            print(f"DRY-RUN: {changed} position(s) WOULD be closed as "
                  "canceled (nothing was written)")
        return 0
    finally:
        conn.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    # --db is REQUIRED: no default, so a bare invocation can never touch
    # the live live_paper.db.
    ap.add_argument("--db", required=True,
                    help="path to the live_paper.db file (REQUIRED)")
    ap.add_argument("--apply", action="store_true",
                    help="actually write (default is a dry run)")
    args = ap.parse_args(argv)
    return run(Path(args.db), apply=args.apply)


if __name__ == "__main__":
    sys.exit(main())
