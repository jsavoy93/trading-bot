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
  SLEEVE_EQUITY_FRAC = 0.15   short-term sleeve = 15% of account equity
                               (Josh, 2026-11-03: capped DOWN from 30% to a
                               FIXED share of equity; configurable through
                               STRAT024_SLEEVE_EQUITY_FRAC)
  MAX_POSITIONS      = 3      max concurrently open runner positions
  MAX_POSITION_PCT   = 0.10   per-position cap = 10% of account equity
  Per-position notional = min(remaining sleeve cash, equity * 10%).
  Entries are skipped when the notional cannot buy a whole share or when
  buying power is insufficient.

  The sleeve fraction is a POSITION-SIZING parameter only. No entry/exit
  threshold, no RSI/ATR/time-stop parameter and no guard is relaxed by
  this change.

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
  - close > SMA5 on a HOLDING-DAY close: cancel the stop, submit
    a market sell (fills at next open — honest divergence from the
    backtest, which exits at the triggering close).
  - 10 trading days held: same as SMA5 path (cancel stop, market sell).

C4-FIX-003 (2026-11-12) — entry-day SMA5 + stop restoration:
  * The SMA5 exit is evaluated ONLY on bars strictly AFTER the entry
    bar (the frozen C4 rule: `s > entry_i`). The entry day is derived
    from positions.entry_date, with the entry order's filled_at DATE as
    the fallback — NEVER the signal_date (the signal day is the day
    BEFORE the fill). Every post-entry bar is scanned, so a missing
    session can never suppress an SMA5 exit. The XOM incident
    (2026-11-11 entry, entry-day close 166.71 > SMA5 165.416) replaced
    the protective stop with an SMA5 sell on the very day of the entry.
  * ATR protection stays a RESTING 3xATR stop; no close-based ATR
    market exit was added.
  * RE-PROTECT: every open position that still holds shares must have
    a working exit order OR a working 3xATR stop. When neither exists
    (the stop is missing at the broker, or a submitted exit was
    canceled / expired / rejected) a NEW 3xATR stop is restored with a
    FRESH client_order_id — a known id is NEVER reused.
  * A canceled stop's LOCAL order row is marked status='canceled' in
    the same pass, and a position that is still open never keeps a
    stale exit_date / exit_reason.
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

# --- sleeve sizing parameter (S3, Josh 2026-11-03) ----------------------
DEFAULT_SLEEVE_EQUITY_FRAC = 0.15
SLEEVE_FRAC_ENV = "STRAT024_SLEEVE_EQUITY_FRAC"


def _sleeve_equity_frac() -> float:
    """The C4 sleeve as a FIXED share of account equity.

    Default 15% (Josh, 2026-11-03 — capped down from 30%). Override with
    STRAT024_SLEEVE_EQUITY_FRAC. A malformed or out-of-range value FAILS
    LOUDLY (ValueError at import) rather than silently falling back to a
    different cap."""
    raw = os.environ.get(SLEEVE_FRAC_ENV)
    if raw is None or str(raw).strip() == "":
        return DEFAULT_SLEEVE_EQUITY_FRAC
    try:
        val = float(str(raw).strip())
    except (TypeError, ValueError):
        raise ValueError(
            f"{SLEEVE_FRAC_ENV} must be a number, got {raw!r}")
    if not (0.0 < val <= 1.0):
        raise ValueError(
            f"{SLEEVE_FRAC_ENV} must be within (0, 1], got {val!r}")
    return val


# --- conservative sleeve defaults (documented in module docstring) ---
# S3 (Josh, 2026-11-03): the C4 sleeve is a FIXED 15% of account equity
# (was 30%) and is configurable via STRAT024_SLEEVE_EQUITY_FRAC. The
# value is read once at import time; a bad value fails loudly instead of
# silently falling back to a different cap.
SLEEVE_EQUITY_FRAC = _sleeve_equity_frac()
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


def unique_coid(symbol: str, kind: str, ref_date: str, attempt: int = 1) -> str:
    """A client_order_id that NEVER collides with an earlier one.

    Attempt 1 keeps the historical deterministic format
    ("sl_short-<sym>-<kind>-<yyyymmdd>"). When that id is already known
    (local DB row OR still open at the broker) a "-rN" suffix is added so
    a replacement order never reuses an id Alpaca would reject or that
    the local ledger would mistake for the old order (C4-FIX-003)."""
    if attempt <= 1:
        return coid(symbol, kind, ref_date)
    suffix = f"-r{attempt}"
    base = f"{COID_PREFIX}-{symbol}-{kind}-{ref_date.replace('-', '')}"
    return (base[: max(8, COID_MAX - len(suffix))] + suffix)[:COID_MAX]


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
  exit_time TEXT,          -- C4-FIX-001: ISO timestamp of the exit FILL
  realized_pnl REAL,       -- C4-FIX-001: (exit_price - entry_price) * qty
  created_ts TEXT NOT NULL,
  UNIQUE(symbol, signal_date)
);
"""

# columns added after the first release; added in place so the EXISTING
# live_paper.db keeps working.
MIGRATION_COLUMNS = (("exit_time", "TEXT"), ("realized_pnl", "REAL"))


def migrate_positions(conn) -> None:
    """Safe in-place migration: add the C4-FIX-001 columns to an EXISTING
    positions table only when they are missing (PRAGMA table_info)."""
    existing = {r[1] for r in conn.execute("PRAGMA table_info(positions)")}
    for name, decl in MIGRATION_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE positions ADD COLUMN {name} {decl}")


def open_db(path: Path):
    import sqlite3

    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    migrate_positions(conn)
    conn.commit()
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

    def get_order_by_client_id(self, client_order_id: str) -> dict | None:
        """GET /v2/orders:by_client_order_id — the only supported way to
        look an order up by client_order_id (GET /v2/orders/{id} accepts
        the Alpaca order id ONLY; passing a client_order_id there 404s,
        which is why fills were never detected before STRAT-023-C4-FILL).
        Returns None on 404; unexpected API errors raise loudly instead
        of being silently mistaken for 'still pending'."""
        r = self.sess.get(
            self.base + "/v2/orders:by_client_order_id",
            headers=self.headers,
            params={"client_order_id": client_order_id}, timeout=60)
        if r.status_code == 429:
            time.sleep(20)
            return self.get_order_by_client_id(client_order_id)
        if r.status_code in (401, 403):
            fail(f"paper API auth rejected on orders:by_client_order_id "
                 f"(HTTP {r.status_code})")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json() if r.text else None

    def get_order(self, order_id: str) -> dict | None:
        """GET /v2/orders/{alpaca order id}. Returns None on 404; raises
        loudly on unexpected API errors."""
        r = self.sess.get(f"{self.base}/v2/orders/{order_id}",
                          headers=self.headers, timeout=60)
        if r.status_code == 429:
            time.sleep(20)
            return self.get_order(order_id)
        if r.status_code in (401, 403):
            fail(f"paper API auth rejected on /v2/orders/{order_id} "
                 f"(HTTP {r.status_code})")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json() if r.text else None

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


# ------------------------------------------------------- exit reconciliation
EXIT_KINDS = ("exit_sma5", "exit_time", "stop")
PENDING_ORDER_STATUSES = ("new", "held", "accepted", "pending_new",
                           "partially_filled", "pending_cancel")

# Terminal broker statuses for an order that will NEVER fill. For an
# ENTRY order this used to leave the position row stuck at
# status='pending_fill' forever, holding a slot and sleeve notional for a
# position that does not exist (C4-FIX-002).
TERMINAL_NOT_FILLED_STATUSES = ("canceled", "cancelled", "expired",
                                 "rejected")


def is_terminal_not_filled(status) -> bool:
    """True for a broker status that can never become filled."""
    return str(status or "").lower() in TERMINAL_NOT_FILLED_STATUSES


def mark_entry_canceled(conn, pid, coid_: str, status, exit_date=None) -> bool:
    """Terminal-not-filled ENTRY order: close the position row as canceled
    with NO P&L (no exit_price, realized_pnl NULL) and mark the orders row
    with the broker's terminal status.

    Idempotent: a row that is no longer 'pending_fill' is left alone.
    Returns True when a position row was actually closed.
    """
    conn.execute("UPDATE orders SET status=? WHERE client_order_id=?",
                 (str(status or "canceled").lower(), coid_))
    cur = conn.execute(
        "UPDATE positions SET status='closed', exit_date=?,"
        " exit_reason='entry_canceled', exit_price=NULL,"
        " realized_pnl=NULL WHERE id=? AND status='pending_fill'",
        (exit_date, pid))
    return cur.rowcount > 0


def reconcile_exit_orders(conn, api, lookup_order, summary) -> int:
    """Reconcile our exit/stop orders against the broker.

    For every OPEN position, look up our exit ('exit_sma5'/'exit_time')
    and resting 'stop' orders and apply the broker truth:

      * filled   -> orders row status='filled'; the position is closed
                    with exit_price (the broker's average fill price),
                    exit_date/exit_time from filled_at and
                    realized_pnl = (exit_price - entry_price) * qty
      * canceled -> orders row status='canceled' (a canceled stop must
                    never stay 'submitted')
      * still working / unknown -> nothing is written (we never invent a
                    fill, and we never crash on a missing order)

    Returns the number of exit/stop fills recorded in this pass.
    """
    fills = 0
    positions = conn.execute(
        "SELECT id, symbol, qty, entry_price, exit_reason FROM positions"
        " WHERE status='open'").fetchall()
    for pid, sym, qty, entry_price, _prev_reason in positions:
        rows = conn.execute(
            "SELECT client_order_id, kind FROM orders"
            " WHERE symbol=? AND kind IN ('exit_sma5','exit_time','stop')"
            " AND status='submitted'", (sym,)).fetchall()
        for coid_, kind in rows:
            try:
                order = lookup_order(coid_)
            except Exception:
                continue          # broker hiccup: leave it pending
            if not order:
                continue          # not found on the broker: leave pending
            status = str(order.get("status") or "").lower()
            if status in TERMINAL_NOT_FILLED_STATUSES:
                # canceled / cancelled / expired / rejected: the order row
                # must carry the broker's terminal status, never stay
                # 'submitted' forever (C4-FIX-002).
                conn.execute("UPDATE orders SET status=? WHERE"
                             " client_order_id=?", (status, coid_))
                continue
            if status != "filled":
                continue          # still working -> leave pending
            fill_px = float(order.get("filled_avg_price") or 0) or None
            if not fill_px:
                continue
            filled_at = order.get("filled_at") or ""
            exit_date = filled_at[:10] or None
            exit_time = filled_at or None
            conn.execute("UPDATE orders SET status='filled' WHERE"
                         " client_order_id=?", (coid_,))
            pnl = None
            if entry_price is not None and qty is not None:
                # C4-FIX-002: the realized P&L follows the FILLED quantity
                # (the broker truth), not the ordered quantity.
                fill_qty = order.get("filled_qty")
                q = float(fill_qty) if fill_qty not in (None, "") \
                    else float(qty)
                pnl = (fill_px - float(entry_price)) * q
            reason = {"exit_sma5": "sma5", "exit_time": "time",
                      "stop": "stop"}.get(kind, "stop")
            conn.execute(
                "UPDATE positions SET status='closed', exit_price=?,"
                " exit_date=COALESCE(?, exit_date), exit_time=?,"
                " realized_pnl=?, exit_reason=COALESCE(exit_reason, ?)"
                " WHERE id=?",
                (fill_px, exit_date, exit_time, pnl, reason, pid))
            fills += 1
    summary["exit_fills"] += fills
    return fills


# ------------------------------------------------------------------- engine
def run_once(db_path: Path, api: PaperAPI, bars_by_symbol: dict[str, pd.DataFrame],
             run_ts: datetime | None = None) -> dict:
    run_ts = run_ts or datetime.now(timezone.utc)
    conn = open_db(db_path)
    summary = {"entries_submitted": 0, "stops_placed": 0, "exits_submitted": 0,
               "fills_tracked": 0, "exit_fills": 0, "skipped": 0,
               "entries_canceled": 0, "stops_replaced": 0,
               "symbols": len(bars_by_symbol)}
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

        def lookup_order(c: str) -> dict | None:
            """Find one of OUR orders by client_order_id via
            /v2/orders:by_client_order_id, falling back to the
            alpaca_order_id stored in the local orders table. Returns
            None ONLY when the order genuinely cannot be found on the
            broker; unexpected API errors propagate (fail loudly)
            rather than being mistaken for a pending order."""
            order = api.get_order_by_client_id(c)
            if order is not None:
                return order
            row = conn.execute(
                "SELECT alpaca_order_id FROM orders"
                " WHERE client_order_id=?", (c,)).fetchone()
            if row and row[0]:
                return api.get_order(row[0])
            return None

        # ---- 1) track entry fills -> create positions + place ATR stop
        def fresh_coid(sym, kind, ref_date, max_attempts=25):
            """The first client_order_id for (symbol, kind, date) that is
            NOT already known locally or still open at the broker. A
            replacement order therefore NEVER reuses a known id."""
            for attempt in range(1, max_attempts + 1):
                c = unique_coid(sym, kind, ref_date, attempt)
                if not known(c):
                    return c
            return None

        def place_stop(sym, sig_date, fill_px, fill_qty, atr14,
                       replaced=False):
            """Resting 3xATR protective stop for the FILLED quantity only.

            The stop id is derived with fresh_coid(): the historical
            deterministic id is used when it is still free, otherwise a
            '-rN' variant is used so the replacement never reuses an id
            the broker or the local DB already knows (C4-FIX-003)."""
            if not (fill_px and fill_qty):
                return None
            stop_px = round(fill_px - sr.ATR_MULT * atr14, 2)
            if stop_px <= 0:
                return None
            s_coid = fresh_coid(sym, "s", sig_date)
            if s_coid is None:
                return None
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
            if replaced:
                summary["stops_replaced"] += 1
            return s_coid

        for row in conn.execute(
                "SELECT id, symbol, signal_date, signal_atr14,"
                " entry_order_coid FROM positions"
                " WHERE status='pending_fill'").fetchall():
            pid, sym, sig_date, atr14, e_coid = row
            order = open_coids.get(e_coid)
            if order is None:
                order = lookup_order(e_coid)
            if order is None:
                continue
            status = str(order.get("status") or "").lower()
            fill_qty = float(order.get("filled_qty") or 0) or None
            # the STRICT fill quantity: 0.0 when the broker says zero
            # filled (it must NOT fall back to the ORDERED quantity).
            strict_fill_qty = (0.0 if order.get("filled_qty") in (None, "")
                               else float(order.get("filled_qty") or 0))
            if status in TERMINAL_NOT_FILLED_STATUSES:
                # C4-FIX-002: the entry order died on the broker
                # (canceled / expired / rejected). A ZERO fill closes the
                # position row as canceled (no P&L) which frees its slot
                # and its sleeve notional; a PARTIAL fill keeps the FILLED
                # shares only (and stops them).
                if strict_fill_qty > 0:
                    fill_qty = strict_fill_qty
                    fill_px = float(order.get("filled_avg_price") or 0) or None
                    fill_date = (order.get("filled_at") or "")[:10] or None
                    conn.execute("UPDATE orders SET status='partially_filled'"
                                 " WHERE client_order_id=?", (e_coid,))
                    conn.execute(
                        "UPDATE positions SET status='open', entry_date=?,"
                        " entry_price=?, qty=? WHERE id=?",
                        (fill_date, fill_px, fill_qty, pid))
                    summary["fills_tracked"] += 1
                    place_stop(sym, sig_date, fill_px, fill_qty, atr14)
                else:
                    mark_entry_canceled(conn, pid, e_coid, status,
                                        run_ts.date().isoformat())
                    summary["entries_canceled"] += 1
                continue
            if status != "filled":
                continue
            fill_px = float(order.get("filled_avg_price") or 0) or None
            fill_date = (order.get("filled_at") or "")[:10] or None
            conn.execute("UPDATE orders SET status='filled' WHERE"
                         " client_order_id=?", (e_coid,))
            conn.execute(
                "UPDATE positions SET status='open', entry_date=?,"
                " entry_price=?, qty=? WHERE id=?",
                (fill_date, fill_px, fill_qty, pid))
            summary["fills_tracked"] += 1
            # resting 3xATR stop
            place_stop(sym, sig_date, fill_px, fill_qty, atr14)

        # ---- 1b) reconcile exit/stop orders with the broker (must run
        # BEFORE the 'broker position qty <= 0' branch so a filled stop
        # closes the position with the REAL fill price)
        reconcile_exit_orders(conn, api, lookup_order, summary)

        # ---- 2) manage open positions: SMA5 / time exits as orders
        open_pos_rows = conn.execute(
            "SELECT id, symbol, signal_date, qty, entry_order_coid,"
            " entry_date, entry_price, signal_atr14"
            " FROM positions WHERE status='open'").fetchall()
        runner_qty_by_sym = {r[1]: (r[3] or 0.0) for r in open_pos_rows}

        def working_order_exists(sym: str, kinds: tuple) -> bool:
            """True when at least one of OUR orders of these kinds is still
            WORKING at the broker.

            An order the broker does not know any more (never created,
            expired and purged, canceled and purged) is NOT working - that
            is exactly the XOM situation (local row 'submitted', no live
            order at the broker). A broker hiccup is treated as 'working'
            so a transient error never places a duplicate protective stop.
            """
            placeholders = ",".join("?" * len(kinds))
            rows = conn.execute(
                "SELECT client_order_id FROM orders WHERE symbol=? AND"
                f" kind IN ({placeholders}) AND status='submitted'",
                (sym, *kinds)).fetchall()
            for (c,) in rows:
                o = open_coids.get(c)
                if o is None:
                    try:
                        o = lookup_order(c)
                    except Exception:
                        return True          # broker hiccup -> assume alive
                if o is None:
                    continue                 # the broker does not know it
                if str(o.get("status") or "").lower() in PENDING_ORDER_STATUSES:
                    return True
            return False

        for (pid, sym, sig_date, tracked_qty, e_coid, entry_date,
             entry_price, atr14) in open_pos_rows:
            raw = bars_by_symbol.get(sym)
            df = sr.add_indicators(raw.copy()) if raw is not None and len(raw) >= sr.WARMUP_BARS else None
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

            # ---- the ENTRY DAY (frozen C4 rule): the entry bar is never
            # an SMA5 exit bar. positions.entry_date is authoritative;
            # the entry order's filled_at DATE is the fallback. The
            # signal_date is NEVER used (it is the day BEFORE the fill).
            if not entry_date:
                e_order = lookup_order(e_coid) or {}
                entry_date = (e_order.get("filled_at") or "")[:10] or None

            reason = None
            if df is not None and entry_date:
                bar_dates = df["date"].dt.strftime("%Y-%m-%d").tolist()
                # Bars STRICTLY AFTER the entry bar are scanned: a missing
                # session must never suppress an SMA5 exit, so EVERY
                # post-entry bar counts (the backtest would have exited on
                # the FIRST such breach).
                for i, d in enumerate(bar_dates):
                    if d <= entry_date:
                        continue
                    if (float(df["close"].iloc[i])
                            > float(df["sma5"].iloc[i])):
                        reason = "sma5"
                        break
                if reason is None:
                    # Time stop: entry day = day 1 (inclusive) and the
                    # trigger is the same session count as before
                    # (TIME_STOP sessions AFTER the entry bar). The rule
                    # is unchanged - only the entry-day SOURCE changed.
                    if entry_date in bar_dates:
                        days_held = len(bar_dates) - bar_dates.index(entry_date)
                        if days_held >= sr.TIME_STOP:
                            reason = "time"
            if reason and working_order_exists(
                    sym, ("exit_sma5", "exit_time")):
                # an exit order for this position is ALREADY working: never
                # cancel the stop again and never submit a second exit.
                reason = None
            if reason and conn.execute(
                    "SELECT 1 FROM orders WHERE symbol=? AND kind IN"
                    " ('exit_sma5','exit_time') AND ref_date=? LIMIT 1",
                    (sym, last_date)).fetchone():
                # an exit order for THIS session already exists (it may have
                # been canceled / expired / rejected): never spam a second
                # exit for the same session - the position is protected by
                # the re-protect path below instead.
                reason = None
            if reason:
                # cancel the resting stop(s), then market-sell OUR OWN qty
                # only. A canceled stop's LOCAL row is marked canceled in
                # the same pass (it must never stay 'submitted').
                stop_rows = conn.execute(
                    "SELECT client_order_id FROM orders WHERE symbol=?"
                    " AND kind='stop' AND status='submitted'",
                    (sym,)).fetchall()
                for (s_row,) in stop_rows:
                    stop = open_coids.get(s_row)
                    if stop is None:
                        try:
                            stop = lookup_order(s_row)
                        except Exception:
                            continue     # broker hiccup: leave it alone
                    if stop is None:
                        # the broker does not know this order at all: it
                        # cannot protect the position -> mark it canceled
                        conn.execute("UPDATE orders SET status='canceled'"
                                     " WHERE client_order_id=?", (s_row,))
                        continue
                    b_status = str(stop.get("status") or "").lower()
                    if b_status in PENDING_ORDER_STATUSES:
                        api.cancel(stop["id"])
                        conn.execute("UPDATE orders SET status='canceled'"
                                     " WHERE client_order_id=?", (s_row,))
                    elif is_terminal_not_filled(b_status):
                        conn.execute("UPDATE orders SET status=?"
                                     " WHERE client_order_id=?",
                                     (b_status, s_row))
                x_coid = fresh_coid(sym, f"x{reason}", last_date)
                sell_qty = min(tracked_qty or 0.0, acct_qty)
                if x_coid is not None and sell_qty > 0:
                    o = api.submit({
                        "symbol": sym, "qty": _fmt_qty(sell_qty),
                        "side": "sell", "type": "market",
                        "time_in_force": "day",
                        "client_order_id": x_coid,
                    })
                    record_order(x_coid, sym, f"exit_{reason}", "sell",
                                 sell_qty, None, last_date, o.get("id"))
                    open_coids[x_coid] = o
                    conn.execute(
                        "UPDATE positions SET exit_date=?, exit_reason=?"
                        " WHERE id=?", (last_date, reason, pid))
                    summary["exits_submitted"] += 1

            # ---- 2b) RE-PROTECT: an open position with shares still held
            # MUST have a working exit OR a working 3xATR stop.
            #  * a canceled / expired / rejected exit leaves the position
            #    unprotected -> a NEW 3xATR stop is restored under a
            #    FRESH client_order_id (a known id is never reused);
            #  * a missing stop at the broker is repaired the same way;
            #  * the stale exit markers are cleared because the position
            #    is still open (the exit never happened).
            has_working_exit = working_order_exists(
                sym, ("exit_sma5", "exit_time"))
            has_working_stop = working_order_exists(sym, ("stop",))
            if not has_working_exit:
                conn.execute(
                    "UPDATE positions SET exit_date=NULL, exit_reason=NULL"
                    " WHERE id=? AND status='open'", (pid,))
            if not has_working_exit and not has_working_stop:
                if entry_price and tracked_qty and atr14:
                    place_stop(sym, sig_date, entry_price, tracked_qty,
                               atr14, replaced=True)

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
                # C4-FIX-001: this entry consumes sleeve budget for the
                # REST of the run, so the next candidate is sized against
                # the remaining sleeve (never more than 15% of equity in
                # total, and never more than 10% of equity per name).
                pending_notional += (qty or 0) * (price or 0)
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
