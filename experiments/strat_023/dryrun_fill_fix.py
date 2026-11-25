"""STRAT-023-C4-FILL — offline dry-run proof (no network, no live DB).

Replays the exact live DB state observed read-only on 2026-11-03 in
/root/strat-swing/experiments/strat_023/live_paper.db:

  AMD / AVTR / CRL  pending_fill, entries filled at the Nov 3 open on
  the broker but never detected locally because order lookups used
  GET /v2/orders/{client_order_id} (Alpaca accepts only its own order
  id on that path -> 404 -> silently treated as still pending).

Runs ONE pass of live_paper_runner.run_once against a temp DB seeded
with that state and a FakeAPI whose orders are already filled. With the
fix (GET /v2/orders:by_client_order_id + alpaca_order_id fallback) the
single pass must: detect all 3 fills, record fill prices + dates, place
3xATR14 stops, and open the rows. A second pass with an SMA5 breach
must submit the 3 market exits.

Usage:
  python dryrun_fill_fix.py [--with-exits]
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import live_paper_runner as lpr  # noqa: E402
from test_live_paper_runner import FakeAPI, make_bars  # noqa: E402

# (symbol, entry coid, alpaca order id, signal ATR14, fill px, fill qty)
# — copied verbatim from the read-only live DB dump 2026-11-03.
LIVE_FILL_STATE = [
    ("AMD", "sl_short-AMD-e-20261102",
     "1c2f5f29-c63d-4a65-b449-de58e959784a", 32.9896642857143,
     467.05, 22.0),
    ("AVTR", "sl_short-AVTR-e-20261102",
     "35a04df6-c159-4391-9604-66ba49fea25d", 0.562321428571428,
     15.62, 679.0),
    ("CRL", "sl_short-CRL-e-20261102",
     "75fcaf81-e7e5-48da-843d-6dbab6e7036f", 16.9818785714286,
     338.54, 31.0),
]
FILL_TS = "2026-11-03T14:30:00Z"
RUN_TS = datetime(2026, 11, 3, 21, 45, tzinfo=timezone.utc)


def seed(db) -> None:
    conn = sqlite3.connect(db)
    conn.executescript(lpr.SCHEMA)
    now = RUN_TS.isoformat()
    for sym, c, aid, atr, px, qty in LIVE_FILL_STATE:
        conn.execute(
            "INSERT INTO orders(client_order_id, symbol, kind, sleeve,"
            " side, qty, price, ref_date, alpaca_order_id, status,"
            " created_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (c, sym, "entry", "short", "buy", qty, px, "2026-11-02",
             aid, "submitted", now))
        conn.execute(
            "INSERT INTO positions(symbol, signal_date, signal_atr14,"
            " entry_order_coid, status, created_ts)"
            " VALUES (?,?,?,?,'pending_fill',?)",
            (sym, "2026-11-02", atr, c, now))
    conn.commit()
    conn.close()


def main() -> int:
    api = FakeAPI()
    for sym, c, aid, atr, px, qty in LIVE_FILL_STATE:
        api.orders[c] = {
            "id": aid, "client_order_id": c, "symbol": sym,
            "qty": str(qty), "side": "buy", "type": "market",
            "status": "filled", "filled_avg_price": str(px),
            "filled_qty": str(qty), "filled_at": FILL_TS}
        api.positions[sym] = qty

    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "dryrun.db"
        seed(db)

        print("== pass 1: fill tracking (previously dead — lookups 404'd)")
        s1 = lpr.run_once(db, api, {}, run_ts=RUN_TS)
        print("summary:", s1)
        conn = sqlite3.connect(db)
        for row in conn.execute(
                "SELECT symbol, status, entry_price, entry_date, qty"
                " FROM positions ORDER BY symbol"):
            print("position:", row)
        for row in conn.execute(
                "SELECT symbol, kind, qty, price, status FROM orders"
                " WHERE kind='stop' ORDER BY symbol"):
            print("stop order:", row)
        conn.close()
        ok1 = (s1["fills_tracked"] == 3 and s1["stops_placed"] == 3)
        print("PASS1", "OK" if ok1 else "FAILED")

        if "--with-exits" in sys.argv:
            print("\n== pass 2: SMA5-breach exits (previously unreachable)")
            bars = {}
            for sym, *_ in LIVE_FILL_STATE:
                df = make_bars()
                df.loc[df.index[-1], "c"] = float(df["c"].iloc[-1]) + 25.0
                df.loc[df.index[-1], "h"] = float(df["h"].iloc[-1]) + 25.0
                bars[sym] = df
            s2 = lpr.run_once(db, api, bars, run_ts=RUN_TS)
            print("summary:", s2)
            print("cancelled stops:", api.cancelled)
            ok2 = s2["exits_submitted"] == 3
            print("PASS2", "OK" if ok2 else "FAILED")
            return 0 if (ok1 and ok2) else 1
    return 0 if ok1 else 1


if __name__ == "__main__":
    sys.exit(main())
