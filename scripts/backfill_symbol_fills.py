#!/usr/bin/env python3
"""LEDGER-BACKFILL-001: backfill FILLED trades rows from REAL Alpaca
PAPER fill history for symbols the main bot owns at the broker but
cannot prove from its own ledger.

Why this exists (PR #139, REBAL-60-30-10): after a deploy, the bot only
sells / stop-losses shares it can PROVE it owns — FILLED trades rows
with filled_qty, read through allocation_policy.own_net_qty_from_ledger_rows
(SmartTradingBot._proven_own_qty). Symbols whose fills never landed in
the ledger (e.g. CMA, bought 2025-12-08 before ledger persistence was
reliable) can never be sold or protected. This tool backfills those
FILLS from the broker's own order history — never inventing a quantity
or a price.

Safety contract (deliberately stricter than EXEC-004's reconciler):

- PAPER ONLY. Refuses any ALPACA_BASE_URL that is not a paper endpoint.
  There is NO live escape flag in this tool, by design.
- READ-ONLY against the broker: get_orders(status=all) and
  get_all_positions() only. No submit/cancel/replace call exists here.
- Swing-runner fills are NEVER backfilled into the main ledger. A fill
  is swing-owned when its client_order_id starts with a configured
  swing label prefix (default 'sl_short-' for C4, 'rsi2-' for RSI(2))
  OR its order id / client_order_id appears in a runner SQLite DB
  (default /root/strat-swing/experiments/strat_023/live_paper.db,
  opened read-only via a mode=ro URI).
- REFUSES instead of guessing: for every requested symbol the projected
  ledger net (existing FILLED rows + planned backfill rows, computed
  with the bot's OWN allocation_policy.own_net_qty_from_ledger_rows)
  must equal the expected broker quantity (live broker position by
  default, --expect SYM=QTY override). Any mismatch => NOTHING is
  written, exit code 3, and a discrepancy report explains the gap
  (e.g. the only fill is swing-labelled).
- Idempotent: fills whose broker_order_id is already present in the
  ledger are skipped, so a re-run inserts nothing.
- Dry run by default. Writes only with BOTH --apply and --db, and a
  timestamped backup copy of the DB is taken before any write.
- Rows are inserted through the bot's own log_trade() (schema-validated,
  sleeve-tagged 'S' like every main-bot ledger write) and carry a note
  marking them as a backfill.

Usage:
    python scripts/backfill_symbol_fills.py --db /tmp/copy/trading_bot.db
        # dry run (default): report + planned rows, write nothing
    python scripts/backfill_symbol_fills.py --db /tmp/copy/trading_bot.db --apply
        # timestamped backup, then insert the planned FILLED rows
    python scripts/backfill_symbol_fills.py --db ... --symbols CMA --apply
        # restrict to a subset of the default AAAA,CMA targets

Applying this to the PRODUCTION ledger requires Josh's explicit
approval (separate from merging this PR).
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotenv import load_dotenv  # noqa: E402

from core.allocation_policy import (  # noqa: E402
    DEFAULT_SWING_ORDER_LABEL_PREFIXES,
    own_net_qty_from_ledger_rows,
)

BACKFILL_NOTE = (
    "LEDGER-BACKFILL-001: broker-authoritative backfill "
    "from Alpaca paper fill history"
)
DEFAULT_SYMBOLS = "AAAA,CMA"
DEFAULT_RUNNER_DB = "/root/strat-swing/experiments/strat_023/live_paper.db"
DEFAULT_ORDER_LIMIT = 500

#: Verdicts for one broker fill.
INCLUDED = "INCLUDE"            # main-bot fill, missing from the ledger
EXCLUDED_LABEL = "EXCLUDE_LABEL"      # swing-labelled (client_order_id prefix)
EXCLUDED_RUNNER_DB = "EXCLUDE_RUNNER_DB"  # order id/coid recorded in a runner DB
ALREADY_RECORDED = "ALREADY_RECORDED"    # broker_order_id already in the ledger

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_DISCREPANCY = 3
EXIT_WRITE_FAILED = 4

_QTY_EPS = 1e-9


# ---------------------------------------------------------------------------
# Pure planning functions (unit-testable without any broker or DB)
# ---------------------------------------------------------------------------

def _value(raw: object) -> str:
    """Alpaca str-mixin enums str() as 'OrderSide.BUY'; use .value."""
    return str(getattr(raw, "value", raw) or "")


def normalize_order(order: object) -> Dict:
    """Map an Alpaca order (object or mapping) to the planner's dict."""
    def field(name: str, default=None):
        if isinstance(order, dict):
            return order.get(name, default)
        return getattr(order, name, default)

    return {
        "id": str(field("id", "") or ""),
        "client_order_id": str(field("client_order_id", "") or ""),
        "symbol": str(field("symbol", "") or "").upper(),
        "side": _value(field("side", "")).lower(),
        "status": _value(field("status", "")).lower(),
        "qty": float(field("qty", 0.0) or 0.0),
        "filled_qty": float(field("filled_qty", 0.0) or 0.0),
        "filled_avg_price": float(field("filled_avg_price", 0.0) or 0.0),
        "created_at": str(field("created_at", "") or ""),
        "filled_at": str(field("filled_at", "") or ""),
    }


def is_filled(order: Dict) -> bool:
    """A fill worth ledgering: broker status 'filled' with a real qty."""
    return order.get("status") == "filled" and float(order.get("filled_qty", 0.0)) > 0


def classify_fill(fill: Dict, recorded_ids: Set[str], prefixes: Sequence[str],
                  runner_ids: Set[str]) -> Tuple[str, str]:
    """Verdict + human reason for one fill (swing fills are NEVER included)."""
    oid = str(fill.get("id", "") or "")
    coid = str(fill.get("client_order_id", "") or "")
    if oid and oid in recorded_ids:
        return ALREADY_RECORDED, f"broker_order_id {oid} already in the ledger"
    if coid and coid.startswith(tuple(prefixes)):
        return EXCLUDED_LABEL, f"client_order_id {coid!r} carries a swing prefix"
    if oid and oid in runner_ids:
        return EXCLUDED_RUNNER_DB, f"order id {oid} recorded in a swing runner DB"
    if coid and coid in runner_ids:
        return EXCLUDED_RUNNER_DB, f"client_order_id {coid!r} recorded in a swing runner DB"
    return INCLUDED, "main-bot fill missing from the ledger"


def signed_qty(fill: Dict) -> float:
    qty = float(fill.get("filled_qty", 0.0) or 0.0)
    return -qty if str(fill.get("side", "")).lower() == "sell" else qty


def build_backfill_row(fill: Dict) -> Dict:
    """Map an included fill to a FILLED trades row (schema keys only).

    Mirrors scripts/reconcile_ledger_with_broker.py (EXEC-004): no pnl
    is fabricated, sleeve is tagged 'S' (the SmartBot sleeve every
    main-bot ledger write uses), and the note marks the row as a
    backfill so it can never be confused with a live submission.
    """
    return {
        "session_id": None,  # a backfill is not part of a bot session
        "symbol": str(fill["symbol"]).upper(),
        "side": str(fill["side"]).upper(),
        "qty": float(fill["filled_qty"]),
        "price": float(fill["filled_avg_price"]) or None,
        "pnl": None,  # unknown for a backfill; never fabricated
        "signal": str(fill["side"]).upper(),
        "rsi": None,
        "order_time": fill.get("created_at") or datetime.now(timezone.utc).isoformat(),
        "status": "FILLED",
        "broker_order_id": fill["id"],
        "filled_qty": float(fill["filled_qty"]),
        "avg_fill_price": float(fill["filled_avg_price"]) or None,
        "sleeve": "S",
        "notes": BACKFILL_NOTE,
    }


def verify_projection(ledger_rows: List[Dict], planned_rows: List[Dict],
                      expected: Dict[str, float]) -> Tuple[bool, Dict[str, Dict]]:
    """Fail-closed quantity check using the bot's OWN ownership math.

    For every requested symbol the projected net (existing FILLED ledger
    rows + planned backfill rows via own_net_qty_from_ledger_rows) must
    equal the expected broker quantity exactly. Anything else is a
    discrepancy: the caller must STOP, never guess.
    """
    projected = own_net_qty_from_ledger_rows(list(ledger_rows or []) + list(planned_rows or []))
    per_symbol: Dict[str, Dict] = {}
    ok = True
    for sym, want in expected.items():
        got = float(projected.get(str(sym).upper(), 0.0) or 0.0)
        match = abs(got - float(want)) <= _QTY_EPS
        ok = ok and match
        per_symbol[str(sym).upper()] = {
            "expected": float(want), "projected": got, "match": match,
        }
    return ok, per_symbol


# ---------------------------------------------------------------------------
# Read-only plumbing (broker, runner DB, ledger)
# ---------------------------------------------------------------------------

def make_paper_client():
    """Paper TradingClient from the SAME env vars the bot uses.

    PAPER ONLY: a non-paper ALPACA_BASE_URL is a hard refusal — this
    tool has no live escape flag. Credentials are never printed.
    """
    from alpaca.trading.client import TradingClient

    base = (os.getenv("ALPACA_BASE_URL", "") or "https://paper-api.alpaca.markets").strip()
    if "paper" not in base.lower():
        raise SystemExit(
            "Refusing to run: ALPACA_BASE_URL is not a paper endpoint. "
            "This backfill tool is paper-only by design (LEDGER-BACKFILL-001)."
        )
    key = os.getenv("ALPACA_API_KEY") or os.getenv("ALPACA_API_KEY_ID")
    secret = os.getenv("ALPACA_API_SECRET") or os.getenv("ALPACA_API_SECRET_KEY")
    if not key or not secret:
        raise SystemExit(
            "Missing ALPACA_API_KEY / ALPACA_API_SECRET "
            "(the same variables the bot uses)."
        )
    host = base.rstrip("/")
    if host.endswith("/v2"):  # alpaca-py appends /v2 itself
        host = host[: -len("/v2")]
    return TradingClient(key, secret, paper=True, url_override=host or None)


def fetch_filled_orders(client, symbols: Sequence[str],
                        order_limit: int = DEFAULT_ORDER_LIMIT) -> List[Dict]:
    """REAL fill history: every order (status=all) for SYMBOLS, paginated.

    Returns normalized FILLED orders only. Read-only: get_orders only.
    """
    from alpaca.trading.enums import QueryOrderStatus
    from alpaca.trading.requests import GetOrdersRequest

    limit = max(1, min(int(order_limit), 500))
    collected: List[Dict] = []
    seen: Set[str] = set()
    after: Optional[str] = None
    while True:
        params = dict(
            symbols=[str(s).upper() for s in symbols],
            status=QueryOrderStatus.ALL,
            limit=limit,
            nested=False,
        )
        if after:
            params["after"] = after
        batch = [normalize_order(o) for o in client.get_orders(GetOrdersRequest(**params))]
        fresh = [o for o in batch if o["id"] and o["id"] not in seen]
        for o in fresh:
            seen.add(o["id"])
        collected.extend(fresh)
        if len(batch) < limit or not fresh:
            break
        after = min(o["created_at"] for o in fresh if o["created_at"]) if any(
            o["created_at"] for o in fresh
        ) else None
        if after is None:
            break
    return [o for o in collected if is_filled(o)]


def fetch_positions_qty(client) -> Dict[str, float]:
    """Current broker qty per symbol (read-only)."""
    out: Dict[str, float] = {}
    for p in client.get_all_positions() or []:
        sym = str(getattr(p, "symbol", "") or "").upper()
        if sym:
            out[sym] = out.get(sym, 0.0) + float(getattr(p, "qty", 0.0) or 0.0)
    return out


def runner_db_identifiers(db_path: str) -> Set[str]:
    """Order ids + client_order_ids recorded in a swing runner DB.

    Read-only mode=ro URI (the runner DB is foreign state). A missing or
    unreadable DB yields an empty set with a warning — attribution then
    falls back to order labels, and the quantity verification still
    guards every write.
    """
    ids: Set[str] = set()
    if not db_path or not Path(db_path).exists():
        return ids
    import sqlite3

    uri = "file:{}?mode=ro".format(str(db_path).replace("?", "%3f").replace("#", "%23"))
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=5)
        try:
            for row in conn.execute(
                "SELECT alpaca_order_id, client_order_id FROM orders"
            ):
                for value in row:
                    if value:
                        ids.add(str(value))
        finally:
            conn.close()
    except Exception as e:  # never block on foreign-state read failures
        print(f"  WARNING: could not read runner DB {db_path!r} ({e}); "
              f"swing attribution falls back to order labels only.")
    return ids


def read_ledger_rows(db_path: Path) -> List[Dict]:
    """Existing trades rows, read via a READ-ONLY connection (dry runs
    must never touch the target DB, not even its schema)."""
    import sqlite3

    uri = "file:{}?mode=ro".format(str(db_path).replace("?", "%3f").replace("#", "%23"))
    conn = sqlite3.connect(uri, uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT symbol, side, status, filled_qty, broker_order_id FROM trades"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Write path (only under --apply, only after a backup)
# ---------------------------------------------------------------------------

def backup_db(db_path: Path, backup_dir: Optional[str] = None) -> Path:
    """Timestamped byte copy of the DB; NEVER overwrite an existing file."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest_dir = Path(backup_dir) if backup_dir else db_path.parent
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{db_path.name}.ledger-backup-{ts}.db"
    n = 1
    while dest.exists():  # same-second rerun: pick a fresh name
        dest = dest_dir / f"{db_path.name}.ledger-backup-{ts}-{n}.db"
        n += 1
    shutil.copy2(db_path, dest)
    if not dest.exists() or dest.stat().st_size != db_path.stat().st_size:
        raise SystemExit(f"Backup verification failed for {dest}; refusing to write.")
    return dest


def apply_rows(db_path: Path, rows: List[Dict]) -> int:
    """Insert backfill rows through the bot's own log_trade (validated).

    Same module-repoint dance as scripts/reconcile_ledger_with_broker.py:
    importing database.sqlite_db instantiates a singleton against
    DB_PATH, so repoint it BEFORE import so only the requested DB is
    touched.
    """
    sys.modules.pop("database.sqlite_db", None)
    from database import sqlite_db as sd  # noqa: W0404 (deliberate re-import)

    sd.DB_PATH = db_path
    sd.sqlite_db = sd.SQLiteDB()
    inserted = 0
    for row in rows:
        if sd.sqlite_db.log_trade(None, row):
            inserted += 1
    return inserted


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_expect(pairs: Sequence[str]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for pair in pairs or []:
        sym, sep, qty = pair.partition("=")
        if not sep or not sym.strip():
            raise SystemExit(f"Invalid --expect {pair!r}; expected SYM=QTY")
        try:
            out[sym.strip().upper()] = float(qty)
        except ValueError:
            raise SystemExit(f"Invalid --expect {pair!r}; QTY must be numeric")
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--db", required=True,
                        help="Path to the trading_bot.db to plan against / write to")
    parser.add_argument("--symbols", default=DEFAULT_SYMBOLS,
                        help=f"Comma-separated target symbols (default {DEFAULT_SYMBOLS})")
    parser.add_argument("--apply", action="store_true",
                        help="Write the planned rows (default: dry run, write nothing)")
    parser.add_argument("--runner-db", default=DEFAULT_RUNNER_DB,
                        help=f"Swing runner SQLite DB (default {DEFAULT_RUNNER_DB}); "
                             f"read-only; missing file => label-only attribution")
    parser.add_argument("--expect", action="append", default=[], metavar="SYM=QTY",
                        help="Override the expected broker qty for a symbol "
                             "(default: live broker position; 0 when not held)")
    parser.add_argument("--backup-dir", default=None,
                        help="Directory for the timestamped pre-write backup "
                             "(default: alongside the DB)")
    parser.add_argument("--order-limit", type=int, default=DEFAULT_ORDER_LIMIT,
                        help=f"Broker orders page size cap (default {DEFAULT_ORDER_LIMIT})")
    parser.add_argument("--env-file", default=None,
                        help="Optional .env to load credentials from (variables are "
                             "never printed or committed)")
    args = parser.parse_args(argv)

    load_dotenv(args.env_file) if args.env_file else load_dotenv()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if not symbols:
        raise SystemExit("--symbols is empty after parsing")

    db_path = Path(args.db).resolve()
    if not db_path.exists():
        raise SystemExit(f"Database not found: {db_path}")

    client = make_paper_client()
    fills = fetch_filled_orders(client, symbols, order_limit=args.order_limit)
    positions = fetch_positions_qty(client)
    expected = {s: float(positions.get(s, 0.0) or 0.0) for s in symbols}
    expected.update(_parse_expect(args.expect))

    ledger_rows = read_ledger_rows(db_path)
    recorded_ids = {
        str(r["broker_order_id"]) for r in ledger_rows if r.get("broker_order_id")
    }
    runner_ids = runner_db_identifiers(args.runner_db)
    prefixes = DEFAULT_SWING_ORDER_LABEL_PREFIXES

    print("LEDGER-BACKFILL-001 — Alpaca PAPER fill backfill (read-only broker access)")
    print(f"  db={db_path}  symbols={','.join(symbols)}  apply={args.apply}")
    print(f"  runner-db={args.runner_db} ({len(runner_ids)} known runner identifiers)")
    print(f"  swing label prefixes: {', '.join(prefixes)}")
    print()

    # Classify every REAL fill of the target symbols and report it.
    by_symbol: Dict[str, List[Dict]] = {s: [] for s in symbols}
    for fill in sorted(fills, key=lambda f: f.get("created_at") or ""):
        sym = str(fill.get("symbol", "")).upper()
        if sym not in by_symbol:  # defensive: broker filter echoed back
            continue
        verdict, reason = classify_fill(fill, recorded_ids, prefixes, runner_ids)
        entry = dict(fill, verdict=verdict, reason=reason)
        by_symbol[sym].append(entry)
        print(f"  {verdict:17} {fill.get('created_at', '')} "
              f"{fill['side'].upper():4} {fill['filled_qty']:>10g} {sym:6} "
              f"@ {fill['filled_avg_price']:.6g}  order={fill['id']} "
              f"coid={fill['client_order_id'] or '-'}")
        print(f"                    {reason}")
    print()

    planned_rows: List[Dict] = []
    for sym in symbols:
        included = [e for e in by_symbol[sym] if e["verdict"] == INCLUDED]
        included_net = sum(signed_qty(e) for e in included)
        excluded_net = sum(
            signed_qty(e) for e in by_symbol[sym]
            if e["verdict"] in (EXCLUDED_LABEL, EXCLUDED_RUNNER_DB)
        )
        planned_rows.extend(build_backfill_row(e) for e in included)
        print(f"  {sym}: included fills net={included_net:+g}  "
              f"excluded (swing) net={excluded_net:+g}  "
              f"expected broker qty={expected[sym]:+g}")

    ok, per_symbol = verify_projection(ledger_rows, planned_rows, expected)
    print()
    for sym, r in per_symbol.items():
        print(f"  projected ledger net {sym}: {r['projected']:+g} "
              f"(expected {r['expected']:+g}) -> "
              f"{'OK' if r['match'] else 'DISCREPANCY'}")

    if not ok:
        print(
            "\nDISCREPANCY: the (non-swing) fill history does not add up to "
            "the expected broker quantity for every symbol.\n"
            "Nothing was written. Do NOT guess: inspect the fills above. If "
            "the gap is provably swing-runner-owned or already covered by the "
            "ledger, re-run with --expect SYM=QTY (documented) or restrict "
            "--symbols to the symbols that reconcile.\n"
            "Applying anything to the production ledger needs Josh's approval."
        )
        return EXIT_DISCREPANCY

    if not planned_rows:
        print("\nNothing to backfill (all target symbols already reconcile).")
        return EXIT_OK

    print(f"\nPlanned rows ({len(planned_rows)}):")
    for row in planned_rows:
        print(f"  {row['symbol']} {row['side']} filled_qty={row['filled_qty']:g} "
              f"avg_fill_price={row['avg_fill_price']} "
              f"broker_order_id={row['broker_order_id']} note={row['notes']}")

    if not args.apply:
        print("\nDRY RUN - nothing written. Re-run with --apply to backfill.")
        return EXIT_OK

    backup = backup_db(db_path, args.backup_dir)
    print(f"\nBackup written: {backup}")
    inserted = apply_rows(db_path, planned_rows)
    print(f"APPLIED: {inserted}/{len(planned_rows)} rows inserted (status FILLED).")

    # Post-write proof: the ledger must now show exactly the expected net.
    after_rows = read_ledger_rows(db_path)
    ok_after, _ = verify_projection(after_rows, [], expected)
    if inserted != len(planned_rows) or not ok_after:
        print("ERROR: post-write verification failed — inspect the DB and the "
              "log_trade warnings above before retrying.")
        return EXIT_WRITE_FAILED
    print("Post-write verification: ledger net matches the expected quantities.")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
