"""STRAT-024 — REAL-order runner for the C4 dip-buy rule on the Alpaca
PAPER account only.

STANDALONE. Extends the STRAT-023 shadow runner with real (paper) order
placement. The C4 entry rule, exit rules and all thresholds are IMPORTED
FROZEN from shadow_runner.py and are NOT relaxed here.

HARD GUARDS (refuse to run, exit code 2, unless ALL true):
  1. env TRADING_BOT_PAPER_ONLY=1
  2. the trading API base URL is exactly https://paper-api.alpaca.markets
     (hardcoded; any override via STRAT024_API_BASE_URL that is not the
     paper endpoint is rejected)
  3. no kill-switch file (default experiments/strat_023/STRAT024_KILL,
     override with STRAT024_KILL_FILE). If present, NO orders of any kind
     are placed.

Sizing (conservative defaults, documented):
  SLEEVE_EQUITY_FRAC = 0.30   short-term sleeve = 30% of account equity
  MAX_POSITIONS      = 3      max concurrently open runner positions
  MAX_POSITION_PCT   = 0.10   per-position cap = 10% of account equity
  Per-position notional = min(remaining sleeve cash, equity * 10%).
  Entries are skipped when the notional cannot buy a whole share or when
  buying power is insufficient.

Order tagging / collision safety:
  Every client_order_id starts with "sl_short-" and is recorded in the
  local DB with sleeve='short'. Deterministic ids make reruns idempotent
  (checked against the local DB AND Alpaca open orders; Alpaca also
  rejects duplicate client_order_ids as a final defense).
  The runner NEVER sells shares it does not track: exit quantities are
  capped at the runner's own recorded qty, and entries are skipped for
  symbols the account already holds beyond the runner's own position.

Exits (placed as orders — C4 rules, NOT relaxed):
  - 3xATR stop: a resting sell STOP order placed immediately after the
    entry fill (stop = fill_price - 3 * ATR14 of the signal day).
  - close > SMA5 on the latest completed close: cancel the stop, submit
    a market sell (fills at next open — honest divergence from the
    backtest, which exits at the triggering close).
  - 10 trading days held: same as SMA5 path (cancel stop, market sell).
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

import shadow_runner as sr  # same directory; frozen C4 logic

HERE = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("STRAT024_DB", HERE / "live_paper.db"))
KILL_FILE = Path(os.environ.get(
    "STRAT024_KILL_FILE", HERE / "STRAT024_KILL"))

# --- paper-only trading API (hardcoded; NEVER live) ---
PAPER_API_URL = "https://paper-api.alpaca.markets"
API_BASE = os.environ.get("STRAT024_API_BASE_URL", PAPER_API_URL)

# --- conservative sleeve defaults (documented in module docstring) ---
SLEEVE_EQUITY_FRAC = 0.30
MAX_POSITIONS = 3
MAX_POSITION_PCT = 0.10
COID_PREFIX = "sl_short"
SLEEVE = "short"          # recorded on every order row
COID_MAX = 48             # Alpaca client_order_id limit


def fail(msg: str, code: int = 2):
    print(f"live_paper_runner: {msg}", file=sys.stderr)
    sys.exit(code)


def guards() -> None:
    if os.environ.get("TRADING_BOT_PAPER_ONLY") != "1":
        fail("REFUSING TO RUN: TRADING_BOT_PAPER_ONLY=1 is required "
             "(paper account only).")
    if API_BASE.rstrip("/") != PAPER_API_URL:
        fail(f"REFUSING TO RUN: trading API base URL must be exactly "
             f"{PAPER_API_URL} (got {API_BASE}).")
    if KILL_FILE.exists():
        fail(f"KILL SWITCH ACTIVE ({KILL_FILE} exists): no orders will "
             "be placed. Delete the file to re-enable.")


def fixture_guard(fixture: str | None) -> None:
    """Fixture bars + the real paper API would place real paper orders on
    synthetic data — require an explicit second opt-in for that combo."""
    if fixture and os.environ.get("STRAT024_FIXTURE_LIVE") != "1":
        fail("fixture mode with the live paper API needs STRAT024_FIXTURE_LIVE=1"
             " (it WILL submit real paper orders from synthetic bars)")


def coid(symbol: str, kind: str, ref_date: str) -> str:
    """Deterministic client_order_id: prefix, symbol, kind, compact date."""
    cid = f"{COID_PREFIX}-{symbol}-{kind}-{ref_date.replace('-', '')}"
    if len(cid) > COID_MAX:
        cid = cid[:COID_MAX]
    return cid


# ------------------------------------------------------------------ database
SCHEMA = f"""
CREATE TABLE IF NOT EXISTS orders(
  client_order_id TEXT PRIMARY KEY,
  symbol TEXT NOT NULL,
  kind TEXT NOT NULL,          -- entry|stop|exit_sma5|exit_time
  sleeve TEXT NOT NULL DEFAULT '{SLEEVE}',
  side TEXT NOT NULL,
  qty REAL NOT NULL,
  price REAL,
  ref_date TEXT NOT NULL,
  alpaca_order_id TEXT,
  status TEXT NOT NULL DEFAULT 'submitted',
  created_ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS positions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  symbol TEXT NOT NULL,
  signal_date TEXT NOT NULL,
  signal_atr14 REAL NOT NULL,
  entry_order_coid TEXT NOT NULL,
  entry_date TEXT,
  entry_price REAL,
  qty REAL,
  status TEXT NOT NULL DEFAULT 'pending_fill', -- pending_fill|open|closed
  exit_date TEXT, exit_price REAL, exit_reason TEXT,
  created_ts TEXT NOT NULL,
  UNIQUE(symbol, signal_date)
);
"""


def open_db(path: Path):
    import sqlite3

    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    return conn


# ---------------------------------------------------------------- Alpaca REST
class PaperAPI:
    def __init__(self, key: str, sec: str, session=None):
        self.headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec}
        self.base = API_BASE.rstrip("/")
        self.sess = session or requests.Session()

    def get(self, path: str, params=None):
        r = self.sess.get(self.base + path, headers=self.headers,
                          params=params, timeout=60)
        if r.status_code == 429:
            time.sleep(20)
            return self.get(path, params)
        if r.status_code == 403 or r.status_code == 401:
            fail(f"paper API auth rejected on {path} (HTTP {r.status_code})")
        r.raise_for_status()
        return r.json() if r.text else {}

    def post(self, path: str, body: dict):
        r = self.sess.post(self.base + path, headers=self.headers,
                           json=body, timeout=60)
        if r.status_code == 429:
            time.sleep(20)
            return self.post(path, body)
        return r

    def account(self) -> dict:
        return self.get("/v2/account")

    def position(self, symbol: str) -> dict | None:
        try:
            return self.get(f"/v2/positions/{symbol}")
        except Exception:
            return None

    def open_runner_orders(self) -> list[dict]:
        """Open orders tagged with our prefix (never touches main-bot
        orders, which do not carry the prefix)."""
        out = []
        for o in self.get("/v2/orders", params={"status": "open", "limit": 500}):
            if str(o.get("client_order_id", "")).startswith(COID_PREFIX):
                out.append(o)
        return out

    def submit(self, body: dict) -> dict:
        r = self.post("/v2/orders", body)
        if r.status_code >= 400:
            raise RuntimeError(f"order rejected ({r.status_code}): {r.text}")
        return r.json()

    def cancel(self, order_id: str):
        r = self.sess.delete(f"{self.base}/v2/orders/{order_id}",
                             headers=self.headers, timeout=60)
        # 409 'order is already canceled or filled' is fine
        if r.status_code not in (200, 204, 404, 409):
            raise RuntimeError(f"cancel failed ({r.status_code}): {r.text}")


# ------------------------------------------------------------------- sizing
def position_qty(equity: float, sleeve_open_notional: float,
                 pending_notional: float, price: float,
                 buying_power: float) -> float:
    """Whole-share qty for a new entry, or 0 when it must be skipped."""
    sleeve_cash = equity * SLEEVE_EQUITY_FRAC - sleeve_open_notional - pending_notional
    cap = min(sleeve_cash, equity * MAX_POSITION_PCT, buying_power)
    if not np.isfinite(price) or price <= 0 or cap < price:
        return 0.0
    return float(max(int(cap // price), 0)) or 0.0


def _fmt_qty(q: float) -> str:
    return str(int(q)) if float(q).is_integer() else str(q)


# ------------------------------------------------------------------- engine
def run_once(db_path: Path, api: PaperAPI, bars_by_symbol: dict[str, pd.DataFrame],
             run_ts: datetime | None = None) -> dict:
    run_ts = run_ts or datetime.now(timezone.utc)
    conn = open_db(db_path)
    summary = {"entries_submitted": 0, "stops_placed": 0, "exits_submitted": 0,
               "fills_tracked": 0, "skipped": 0, "symbols": len(bars_by_symbol)}
    acct = api.account()
    equity = float(acct["equity"])
    buying_power = float(acct["buying_power"])
    try:
        open_coids = {o["client_order_id"]: o for o in api.open_runner_orders()}

        def known(c: str) -> bool:
            return (conn.execute("SELECT 1 FROM orders WHERE client_order_id=?",
                                 (c,)).fetchone() is not None
                    or c in open_coids)

        def record_order(c: str, sym: str, kind: str, side: str, qty: float,
                         price, ref_date: str, alpaca_id, status="submitted"):
            conn.execute(
                "INSERT OR IGNORE INTO orders(client_order_id, symbol, kind,"
                " sleeve, side, qty, price, ref_date, alpaca_order_id, status,"
                " created_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (c, sym, kind, SLEEVE, side, qty, price, ref_date,
                 alpaca_id, status, run_ts.isoformat()))

        # ---- 1) track entry fills -> create positions + place ATR stop
        for row in conn.execute(
                "SELECT symbol, signal_date, signal_atr14, entry_order_coid"
                " FROM positions WHERE status='pending_fill'").fetchall():
            sym, sig_date, atr14, e_coid = row
            order = open_coids.get(e_coid)
            if order is None:
                try:
                    order = api.get(f"/v2/orders/{e_coid}")
                except Exception:
                    order = None
            if order is None or order.get("status") not in ("filled",):
                continue
            fill_px = float(order.get("filled_avg_price") or 0) or None
            fill_qty = float(order.get("filled_qty") or 0) or None
            fill_date = (order.get("filled_at") or "")[:10] or None
            conn.execute("UPDATE orders SET status='filled' WHERE"
                         " client_order_id=?", (e_coid,))
            conn.execute(
                "UPDATE positions SET status='open', entry_date=?,"
                " entry_price=?, qty=? WHERE symbol=? AND signal_date=?"
                " AND status='pending_fill'",
                (fill_date, fill_px, fill_qty, sym, sig_date))
            summary["fills_tracked"] += 1
            # resting 3xATR stop
            if fill_px and fill_qty:
                stop_px = round(fill_px - sr.ATR_MULT * atr14, 2)
                s_coid = coid(sym, "s", sig_date)
                if not known(s_coid) and stop_px > 0:
                    o = api.submit({
                        "symbol": sym, "qty": _fmt_qty(fill_qty),
                        "side": "sell", "type": "stop",
                        "stop_price": str(stop_px),
                        "time_in_force": "gtc",
                        "client_order_id": s_coid,
                    })
                    record_order(s_coid, sym, "stop", "sell", fill_qty,
                                 stop_px, sig_date, o.get("id"))
                    open_coids[s_coid] = o
                    summary["stops_placed"] += 1

        # ---- 2) manage open positions: SMA5 / time exits as orders
        open_pos_rows = conn.execute(
            "SELECT id, symbol, signal_date, qty, entry_order_coid"
            " FROM positions WHERE status='open'").fetchall()
        runner_qty_by_sym = {r[1]: (r[3] or 0.0) for r in open_pos_rows}
        for pid, sym, sig_date, tracked_qty, e_coid in open_pos_rows:
            raw = bars_by_symbol.get(sym)
            df = sr.add_indicators(raw.copy()) if raw is not None and len(raw) >= sr.WARMUP_BARS else None
            last_close = float(df["close"].iloc[-1]) if df is not None else None
            last_sma5 = float(df["sma5"].iloc[-1]) if df is not None else None
            last_date = (df["date"].dt.strftime("%Y-%m-%d").iloc[-1]
                         if df is not None else run_ts.date().isoformat())

            # broker-side stop already closed the position?
            acct_pos = api.position(sym)
            acct_qty = float(acct_pos["qty"]) if acct_pos else 0.0
            if acct_qty <= 0:
                conn.execute(
                    "UPDATE positions SET status='closed',"
                    " exit_date=COALESCE(exit_date, ?),"
                    " exit_reason=COALESCE(exit_reason, 'external_or_stop')"
                    " WHERE id=?",
                    (last_date, pid))
                continue

            reason = None
            if last_close is not None and last_sma5 is not None and last_close > last_sma5:
                reason = "sma5"
            else:
                e_order = api.get(f"/v2/orders/{e_coid}")
                e_fill = (e_order.get("filled_at") or "")[:10]
                if e_fill and df is not None:
                    dates = df["date"].dt.strftime("%Y-%m-%d").tolist()
                    if e_fill in dates:
                        # entry day = day 1, latest completed close is the
                        # last bar: days_held counts bars inclusive
                        days_held = len(dates) - dates.index(e_fill)
                        if days_held >= sr.TIME_STOP:
                            reason = "time"
            if reason:
                # cancel the resting stop, then market-sell OUR OWN qty only
                s_coid = coid(sym, "s", sig_date)
                stop = open_coids.get(s_coid)
                if stop is None:
                    try:
                        stop = api.get(f"/v2/orders/{s_coid}")
                    except Exception:
                        stop = None
                if stop and stop.get("status") in ("new", "held", "accepted",
                                                   "pending_new", "partially_filled"):
                    api.cancel(stop["id"])
                x_coid = coid(sym, f"x{reason}", last_date)
                sell_qty = min(tracked_qty or 0.0, acct_qty)
                if sell_qty > 0 and not known(x_coid):
                    o = api.submit({
                        "symbol": sym, "qty": _fmt_qty(sell_qty),
                        "side": "sell", "type": "market",
                        "time_in_force": "day",
                        "client_order_id": x_coid,
                    })
                    record_order(x_coid, sym, f"exit_{reason}", "sell",
                                 sell_qty, None, last_date, o.get("id"))
                    conn.execute(
                        "UPDATE positions SET exit_date=?, exit_reason=?"
                        " WHERE id=?", (last_date, reason, pid))
                    summary["exits_submitted"] += 1

        # ---- 3) new entries on the latest completed close
        open_syms = {r[0] for r in conn.execute(
            "SELECT symbol FROM positions WHERE status IN"
            " ('open','pending_fill')").fetchall()}
        if len(open_syms) < MAX_POSITIONS:
            sleeve_open = conn.execute(
                "SELECT COALESCE(SUM(qty * entry_price), 0) FROM positions"
                " WHERE status='open'").fetchone()[0]
            # entry orders still open on the broker count against the sleeve
            pending_notional = 0.0
            for qty, price in conn.execute(
                    "SELECT qty, price FROM orders WHERE kind='entry'"
                    " AND status='submitted'").fetchall():
                # approximate with the recorded reference price; market
                # entry orders reference the signal close
                pending_notional += (qty or 0) * (price or 0)
            for sym, raw in sorted(bars_by_symbol.items()):
                if len(open_syms) >= MAX_POSITIONS:
                    break
                if sym in open_syms or len(raw) < sr.WARMUP_BARS:
                    continue
                df = sr.add_indicators(raw.copy())
                if len(df) < sr.WARMUP_BARS:
                    continue
                sigs = sr.entry_signal_rows(df)
                last = len(df) - 1
                if last not in sigs.index:
                    continue
                sig_date = df["date"].dt.strftime("%Y-%m-%d").iloc[last]
                price = float(df["close"].iloc[last])
                atr14 = float(df["atr14"].iloc[last])
                # never enter a symbol the account already holds beyond us
                acct_pos = api.position(sym)
                if acct_pos and float(acct_pos.get("qty", 0)) > runner_qty_by_sym.get(sym, 0.0):
                    summary["skipped"] += 1
                    continue
                e_coid = coid(sym, "e", sig_date)
                if known(e_coid):
                    continue
                qty = position_qty(equity, sleeve_open, pending_notional,
                                   price, buying_power)
                if qty <= 0:
                    summary["skipped"] += 1
                    continue
                o = api.submit({
                    "symbol": sym, "qty": _fmt_qty(qty),
                    "side": "buy", "type": "market",
                    "time_in_force": "day",
                    "client_order_id": e_coid,
                })
                record_order(e_coid, sym, "entry", "buy", qty, price,
                             sig_date, o.get("id"))
                conn.execute(
                    "INSERT OR IGNORE INTO positions(symbol, signal_date,"
                    " signal_atr14, entry_order_coid, created_ts)"
                    " VALUES (?,?,?,?,?)",
                    (sym, sig_date, atr14, e_coid, run_ts.isoformat()))
                open_syms.add(sym)
                summary["entries_submitted"] += 1
        conn.commit()
    finally:
        conn.close()
    return summary


def main() -> int:
    guards()
    fixture = os.environ.get("STRAT023_FIXTURE_DIR")
    fixture_guard(fixture)
    if fixture:
        bars = sr.load_fixture_bars(Path(fixture))
        print(f"fixture mode: {len(bars)} symbols from {fixture}")
    else:
        key, sec = sr.load_paper_env()
        symbols = sr.universe_symbols()
        print(f"fetching {len(symbols)} symbols from Alpaca paper data API")
        bars = {}
        for i, sym in enumerate(symbols):
            df = sr.fetch_bars(sym, key, sec)
            if df is not None:
                bars[sym] = df
            if (i + 1) % 50 == 0:
                print(f"  {i+1}/{len(symbols)} fetched")
            time.sleep(0.35)
    api = PaperAPI(*sr.load_paper_env())
    summary = run_once(DB_PATH, api, bars)
    print("live paper run complete:", summary)
    print(f"db: {DB_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
