"""STRAT-SWING-PAPER — single-shot paper swing executor (RSI(2) ONLY).

Places PAPER orders ONLY (https://paper-api.alpaca.markets) with per-strategy
client_order_id labels (see rules.py). Default is DRY-RUN: nothing is sent
unless --execute is passed, and dry-run writes NOTHING to state.json (only
the events log). Credentials come exclusively from the ALPACA_API_KEY /
ALPACA_API_SECRET environment variables (paper keys).

SCOPE DECISION (Josh, 2026-11-03): this runner trades RSI(2) ONLY. C4 stays
on the existing strat023-c4-shadow timer (labels sl_short-..., live_paper.db).

Per-run responsibilities (idempotent; safe to re-run same day):
  1. EXITS for open RSI(2) trades: chandelier stop updates (never lower),
     15-trading-day time exit. Closed trades are removed from state so the
     slice money frees up.
  2. STOP-AFTER-FILL: sell stops/partials are placed only AFTER a confirmed
     entry fill; total open sell qty for a symbol never exceeds held qty.
  3. NEW ENTRIES: skip symbols held in the account, symbols with any
     open/pending order (any label, including sl_short-*), and symbols C4
     signaled the same day (best-effort read from live_paper.db). Never
     trades on an incomplete daily bar. Requires TRADING_BOT_PAPER_ONLY=1
     (same guard as the C4 shadow runner).

Never loops, never installs timers. Run once per day from a human-approved
schedule. State: logs/state.json (gitignored).
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd
import requests

import signals
from rules import (RSI2, make_client_order_id, make_labels,
                   make_oco_stop_client_order_id, make_partial_client_order_id,
                   next_sequence, rsi2_slice_dollars, rsi2_skip_blocked,
                   size_rsi2_position, symbol_has_open_order)

HERE = Path(__file__).resolve().parent
LOG_DIR = Path(os.environ.get("STRAT_SWING_LOG_DIR", HERE / "logs"))
STATE_PATH = LOG_DIR / "state.json"
EVENTS_PATH = LOG_DIR / "swing_events.jsonl"
DEFAULT_C4_DB = Path("/root/strat-swing/experiments/strat_023/live_paper.db")

PAPER_TRADING_URL = "https://paper-api.alpaca.markets"
DATA_URL = "https://data.alpaca.markets"
LOOKBACK_DAYS = 420  # ~290 trading days, same as the shadow runners
KILL_SWITCH = HERE / "KILL_SWITCH"  # halt immediately if present

# A US equity daily bar is final only after the regular-session close.
# 16:00 ET == 20:00/21:00 UTC (DST/EST). We require 21:05 UTC before a
# same-day bar may be treated as completed.
DAILY_BAR_FINAL_UTC_HOUR = 21
DAILY_BAR_FINAL_UTC_MINUTE = 5


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
        self.key, self.secret = key, sec = key, secret
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

    def get_order_by_client_id(self, client_order_id: str) -> dict | None:
        r = self.session.get(
            f"{PAPER_TRADING_URL}/v2/orders:by_client_order_id",
            params={"client_order_id": client_order_id},
            headers=self._headers(), timeout=15)
        if r.status_code != 200:
            return None
        return r.json()

    def daily_bars(self, symbol: str) -> pd.DataFrame | None:
        end = datetime.now(timezone.utc)
        start = pd.Timestamp(end) - pd.Timedelta(days=LOOKBACK_DAYS)
        r = self.session.get(
            f"{DATA_URL}/v2/stocks/{symbol}/bars",
            params={"timeframe": "1Day", "start": start.isoformat(),
                    "end": end.isoformat(), "limit": 1000,
                    "adjustment": "split"},
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
            return {"dry_run": True, "body": body,
                    "id": f"dry-{body.get('client_order_id', 'x')}",
                    "status": "dry_run"}
        r = self.session.post(f"{PAPER_TRADING_URL}/v2/orders",
                              json=body, headers=self._headers(), timeout=15)
        if r.status_code not in (200, 201):
            return {"error": r.text}
        return r.json()

    def replace_order(self, order_id: str, patch: dict) -> dict:
        """PATCH /v2/orders/{id} — used to raise a stop price (chandelier)."""
        if self.dry_run:
            return {"dry_run": True, **patch}
        r = self.session.patch(f"{PAPER_TRADING_URL}/v2/orders/{order_id}",
                               json=patch, headers=self._headers(), timeout=15)
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
    return {"open": {}}  # open[entry_client_order_id] = trade record


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
def ymd_today() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def last_bar_is_final(df_last_date, now: datetime | None = None) -> bool:
    """A daily bar is tradeable only when it is final: either it is a
    completed prior day, or it is today AND the clock is past the US
    regular-session close (>= 21:05 UTC covers EST and DST)."""
    now = now or now_utc()
    d = pd.Timestamp(df_last_date).date()
    today = now.date()
    if d < today:
        return True
    if d > today:
        return False
    return (now.hour, now.minute) >= (DAILY_BAR_FINAL_UTC_HOUR,
                                      DAILY_BAR_FINAL_UTC_MINUTE)


def rsi2_open_notional(state: dict) -> float:
    return sum(t.get("entry_notional", 0.0)
               for t in state.get("open", {}).values())


def open_rsi2_count(state: dict) -> int:
    return len(state.get("open", {}))


def all_client_order_ids(api) -> list:
    labels = []
    try:
        orders = api.get_orders(status="open") + api.get_orders(status="closed")
    except Exception:
        return []
    for o in orders:
        coid = o.get("client_order_id")
        if coid:
            labels.append(coid)
    return labels


def _c4_db_path(db_path=None) -> Path:
    return Path(db_path or os.environ.get("STRAT_SWING_C4_DB", DEFAULT_C4_DB))


def _c4_connect(db_path=None):
    """Open the strat023-c4-shadow live_paper.db READ-ONLY. FAILS LOUDLY
    (RuntimeError) when the db is missing or unreadable: a missing C4
    signal source must never be silently treated as 'no C4 signal' (PR
    #135 round-2 review, fix A)."""
    path = _c4_db_path(db_path)
    if not path.exists():
        raise RuntimeError(
            f"C4 shadow DB not found at {path} — refusing to guess. "
            f"Set STRAT_SWING_C4_DB or --c4-db to the live_paper.db used by "
            f"strat023-c4-shadow (columns: orders.created_ts, orders.ref_date, "
            f"positions.status).")
    uri = f"file:{path}?mode=ro&immutable=0"
    try:
        return sqlite3.connect(uri, uri=True, timeout=5)
    except Exception as exc:
        raise RuntimeError(f"cannot open C4 shadow DB {path} read-only: {exc!r}")


def c4_signaled_today(symbols, db_path=None) -> set:
    """Set of `symbols` that C4 signaled TODAY, read from the
    strat023-c4-shadow live_paper.db (labels sl_short-*). Real schema:
    orders(created_ts, ref_date). FAILS LOUDLY (RuntimeError) when the db
    is missing or the query fails — never silently 'no C4 signal'."""
    if not symbols:
        return set()
    out = set()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    wanted = {str(s).strip().upper() for s in symbols}
    try:
        con = _c4_connect(db_path)
    except RuntimeError:
        raise
    try:
        rows = con.execute(
            "SELECT DISTINCT symbol FROM orders WHERE "
            "substr(created_ts,1,10)=? OR substr(ref_date,1,10)=?",
            (today, today))
        out.update(str(r[0]).strip().upper() for r in rows if r[0])
    except Exception as exc:
        raise RuntimeError(
            f"C4 same-day query failed on {_c4_db_path(db_path)} "
            f"(expected columns orders.created_ts / orders.ref_date): {exc!r}")
    finally:
        con.close()
    return out & wanted


def c4_open_notional(db_path=None) -> float:
    """Dollar value of currently-open C4 positions (positions.status='open'),
    read from the C4 shadow DB. The RSI(2) runner and C4 share the 30%
    short-term bucket, so C4's open notional must be subtracted before
    RSI(2) sizing (PR #135 round-2 review, fix H). FAILS LOUDLY."""
    try:
        con = _c4_connect(db_path)
    except RuntimeError:
        raise
    try:
        rows = con.execute(
            "SELECT COALESCE(qty,0), COALESCE(entry_price,0) FROM positions "
            "WHERE status='open'")
        return sum(float(q or 0) * float(p or 0) for q, p in rows)
    except Exception as exc:
        raise RuntimeError(
            f"C4 open-notional query failed on {_c4_db_path(db_path)} "
            f"(expected positions.qty/entry_price/status): {exc!r}")
    finally:
        con.close()


# ------------------------------------------------------- exits (each run)
def held_qty(positions, symbol: str) -> float:
    sym = str(symbol).strip().upper()
    for p in positions or []:
        if str(p.get("symbol", "")).strip().upper() == sym:
            return float(p.get("qty", 0) or 0)
    return 0.0


def open_sell_qty(open_orders, symbol: str) -> float:
    sym = str(symbol).strip().upper()
    q = 0.0
    for o in open_orders or []:
        if str(o.get("side", "")) != "sell":
            continue
        if str(o.get("symbol", "")).strip().upper() != sym:
            continue
        q += float(o.get("qty", 0) or 0)
    return q


def trading_days_since(df, entry_date) -> int:
    """Number of completed daily bars strictly AFTER the entry bar."""
    entry = pd.Timestamp(entry_date).date()
    dates = [pd.Timestamp(d).date() for d in df["date"]]
    return sum(1 for d in dates if d > entry)


def chandelier_stop(df, entry_date, atr14_now: float) -> float | None:
    """max(close since entry) - 3.0*ATR14(current bar)."""
    entry = pd.Timestamp(entry_date).date()
    closes = [float(c) for c, d in zip(df["close"], df["date"])
              if pd.Timestamp(d).date() >= entry]
    if not closes:
        return None
    return max(closes) - signals.RSI2_TRAIL_ATR * float(atr14_now)


def _ensure_canceled(api, order_id, client_order_id=None) -> bool:
    """Cancel `order_id` and CONFIRM the cancellation (via the order's
    status when a client_order_id is known). Returns False when the
    cancel failed or the order is still live afterwards."""
    if not api.cancel_order(order_id):
        return False
    if client_order_id:
        o = api.get_order_by_client_id(client_order_id)
        if o is not None and str(o.get("status")) not in (
                "canceled", "expired", "filled"):
            return False
    return True


def _close_trade(state, entry_id, reason, results):
    state["open"].pop(entry_id, None)
    results["closed"].append({"entry_id": entry_id, "reason": reason})
    log_event({"ts": now_utc(), "type": "close", "entry_id": entry_id,
               "reason": reason})


def process_exits(state, api, bars, positions, results):
    """For every open RSI(2) trade: drop dead entries, manage stops,
    apply the 15-day time exit. Per-trade try/except; state saved by
    caller after each mutation is done at run() level via save hook."""
    for entry_id in list(state.get("open", {}).keys()):
        try:
            t = state["open"].get(entry_id)
            if t is None:
                continue
            sym = t["symbol"]
            df = bars.get(sym)
            if df is None or len(df) == 0:
                continue

            # -- confirm entry fill (stop-after-fill contract) --
            if not t.get("entry_filled"):
                o = api.get_order_by_client_id(entry_id)
                if o is None:
                    continue
                st = o.get("status")
                if st in ("canceled", "expired", "rejected"):
                    _close_trade(state, entry_id, f"entry_{st}", results)
                    continue
                if st in ("filled",):
                    t["entry_filled"] = True
                    t["entry_fill_px"] = float(
                        o.get("filled_avg_price") or t.get("ref_px") or 0)
                    t["entry_order_id"] = o.get("id")
                    # FIX F: stops/target re-anchored to the ACTUAL fill
                    # price (frozen formulas, signal-day ATR14), not the
                    # signal close. Only while no stop has been placed.
                    fill_px = t["entry_fill_px"]
                    atr_sig = float(t.get("atr14") or 0)
                    if fill_px > 0 and atr_sig > 0 and not t.get("stop_order_id"):
                        init_stop, _ch, partial_px = signals.rsi2_stops(
                            fill_px, atr_sig)
                        t["stop_px"] = round(init_stop, 2)
                        t["partial_px"] = round(partial_px, 2)
                        t["entry_notional"] = float(t.get("qty", 0)) * fill_px
                    # FIX G: the 15-day time exit counts from the FILL
                    # date, not the signal date.
                    filled_at = str(o.get("filled_at") or "")
                    if filled_at:
                        t["entry_date"] = filled_at[:10]
                else:  # still pending — nothing to manage yet
                    continue

            # -- FIX D: position gone / stop filled -> close, free slot --
            if held_qty(positions, sym) <= 0:
                reason = "position_gone"
                scoid = t.get("stop_client_order_id")
                if scoid:
                    so = api.get_order_by_client_id(scoid)
                    if so is not None and so.get("status") == "filled":
                        reason = "stop_filled"
                _close_trade(state, entry_id, reason, results)
                continue

            # -- time exit: 15 trading days after entry bar --
            days = trading_days_since(df, t["entry_date"])
            if days >= signals.RSI2_TIME_EXIT:
                # FIX C: confirm every protective sell order is actually
                # canceled BEFORE the market sell — a failed cancel must
                # NOT be followed by a market sell (double-order risk).
                cancels = [
                    (t.get("stop_order_id"), t.get("stop_client_order_id")),
                    (t.get("partial_order_id"), t.get("partial_client_order_id")),
                    (t.get("oco_order_id"), None),
                ]
                cancel_ok = True
                for oid, coid in cancels:
                    if not oid:
                        continue
                    if not _ensure_canceled(api, oid, coid):
                        cancel_ok = False
                        log_event({"ts": now_utc(), "type": "exit_error",
                                   "entry_id": entry_id,
                                   "error": f"cancel failed for {oid}; "
                                            f"market sell skipped"})
                if not cancel_ok:
                    results["errors"].append(
                        {"entry_id": entry_id,
                         "error": "time_exit: cancel failed; no sell sent"})
                    continue
                sell_qty = int(min(float(t["qty"]), held_qty(positions, sym)))
                if sell_qty <= 0:
                    _close_trade(state, entry_id, "time_exit_no_position", results)
                    continue
                resp = api.submit_order({
                    "symbol": sym, "qty": sell_qty, "side": "sell",
                    "type": "market", "time_in_force": "day",
                    "client_order_id": make_client_order_id(
                        RSI2, sym, t["date"], t["n"]) + "-timeexit",
                })
                if resp.get("error"):
                    log_event({"ts": now_utc(), "type": "exit_error",
                               "entry_id": entry_id, "error": resp["error"]})
                    continue
                _close_trade(state, entry_id, "time_exit_15d", results)
                continue

            # -- chandelier trail: raise stop, never lower --
            if t.get("stop_order_id") and t.get("entry_filled"):
                ch = chandelier_stop(df, t["entry_date"],
                                     float(df["atr14"].iloc[-1]))
                cur = float(t.get("stop_px", 0) or 0)
                if ch is not None and ch > cur + 0.01:
                    new_px = round(ch, 2)
                    resp = api.replace_order(t["stop_order_id"],
                                             {"stop_price": new_px})
                    if resp.get("error"):
                        log_event({"ts": now_utc(), "type": "stop_update_error",
                                   "entry_id": entry_id, "error": resp["error"]})
                    else:
                        # FIX B: a replace returns a NEW order id — always
                        # store the id from the response.
                        t["stop_order_id"] = (resp.get("id")
                                              or t["stop_order_id"])
                        t["stop_px"] = new_px
                        log_event({"ts": now_utc(), "type": "stop_update",
                                   "entry_id": entry_id, "new_stop": new_px})
        except Exception as exc:  # never let one symbol kill the run
            log_event({"ts": now_utc(), "type": "exit_symbol_error",
                       "entry_id": entry_id, "error": repr(exc)})
            results["errors"].append({"entry_id": entry_id,
                                      "error": repr(exc)})


def place_stops_after_fill(state, api, positions, open_orders, results):
    """Place the labeled stop (+ partial limit) for trades whose entry is
    confirmed filled but which have no stop yet. Total open sell qty for a
    symbol NEVER exceeds held qty."""
    for entry_id in list(state.get("open", {}).keys()):
        try:
            t = state.get("open", {}).get(entry_id)
            if t is None or t.get("stop_order_id"):
                continue
            if not t.get("entry_filled"):
                continue
            sym = t["symbol"]
            held = held_qty(positions, sym)
            if held <= 0:
                continue  # position gone; exits/next pass will clean up
            already = open_sell_qty(open_orders, sym)
            room = int(held - already)
            if room <= 0:
                continue
            # FIX E: the whole position is always protected. The
            # partial-exit half goes out as an OCO bracket (limit target +
            # stop for the SAME half), and the remainder gets a plain
            # stop. Total open sell qty (existing + OCO half + stop) never
            # exceeds held qty.
            half = int(t["qty"] // 2)
            partial_qty = min(half, max(0, room // 2))
            stop_qty = min(int(t["qty"]) - partial_qty, room - partial_qty)
            if stop_qty <= 0 and partial_qty <= 0:
                continue
            stop_coid = make_labels(RSI2, sym, t["date"], t["n"])[1]
            partial_coid = make_partial_client_order_id(
                RSI2, sym, t["date"], t["n"])
            oco_stop_coid = make_oco_stop_client_order_id(
                RSI2, sym, t["date"], t["n"])
            stop_resp = None
            if stop_qty >= 1:
                stop_resp = api.submit_order({
                    "symbol": sym, "qty": stop_qty, "side": "sell",
                    "type": "stop", "stop_price": round(float(t["stop_px"]), 2),
                    "time_in_force": "gtc",
                    "client_order_id": stop_coid,
                })
                if stop_resp.get("error"):
                    log_event({"ts": now_utc(), "type": "stop_error",
                               "entry_id": entry_id,
                               "error": stop_resp["error"]})
                    results["errors"].append(
                        {"entry_id": entry_id,
                         "error": "stop: " + str(stop_resp["error"])[:200]})
                    stop_resp = None
                else:
                    t["stop_order_id"] = stop_resp.get("id")
                    t["stop_client_order_id"] = stop_coid
                    open_orders = list(open_orders) + [
                        {"symbol": sym, "side": "sell", "qty": stop_qty}]
                    log_event({"ts": now_utc(), "type": "stop_placed",
                               "entry_id": entry_id, "symbol": sym,
                               "qty": stop_qty, "dry_run": api.dry_run})
            if partial_qty >= 1:
                # OCO bracket for the partial half: limit target OR stop,
                # whichever hits first. The half is never left naked.
                p_resp = api.submit_order({
                    "symbol": sym, "qty": partial_qty, "side": "sell",
                    "type": "market", "time_in_force": "gtc",
                    "order_class": "oco",
                    "legs": [
                        {"symbol": sym, "qty": partial_qty, "side": "sell",
                         "type": "limit",
                         "limit_price": round(float(t["partial_px"]), 2),
                         "time_in_force": "gtc",
                         "client_order_id": partial_coid},
                        {"symbol": sym, "qty": partial_qty, "side": "sell",
                         "type": "stop",
                         "stop_price": round(float(t["stop_px"]), 2),
                         "time_in_force": "gtc",
                         "client_order_id": oco_stop_coid},
                    ],
                })
                if p_resp.get("error"):
                    log_event({"ts": now_utc(), "type": "partial_error",
                               "entry_id": entry_id,
                               "error": p_resp["error"]})
                    results["errors"].append(
                        {"entry_id": entry_id,
                         "error": "oco: " + str(p_resp["error"])[:200]})
                else:
                    t["oco_order_id"] = p_resp.get("id")
                    t["partial_client_order_id"] = partial_coid
                    open_orders.append({"symbol": sym, "side": "sell",
                                        "qty": partial_qty})
                    log_event({"ts": now_utc(), "type": "oco_placed",
                               "entry_id": entry_id, "symbol": sym,
                               "qty": partial_qty, "dry_run": api.dry_run})
            results["stops_placed"].append(
                {"entry_id": entry_id, "symbol": sym,
                 "stop_qty": stop_qty, "oco_qty": partial_qty})
        except Exception as exc:
            log_event({"ts": now_utc(), "type": "stop_symbol_error",
                       "entry_id": entry_id, "error": repr(exc)})
            results["errors"].append({"entry_id": entry_id, "error": repr(exc)})


# ------------------------------------------------------------- entries
def process_entries(state, api, bars, spy_bars, positions, open_orders,
                    existing_labels, ymd, args, portfolio_value, results):
    slice_dollars = rsi2_slice_dollars(portfolio_value, args.bucket_pct)
    # FIX H: RSI(2) and C4 share the 30% short-term bucket — subtract the
    # value of currently-open C4 positions so combined exposure respects
    # the bucket. Both C4 reads fail loudly (fix A).
    c4_db = getattr(args, "c4_db", None)
    c4_held = c4_open_notional(c4_db)
    if c4_held > 0:
        slice_dollars = max(0.0, slice_dollars - c4_held)
        log_event({"ts": now_utc(), "type": "c4_bucket_usage",
                   "c4_open_notional": c4_held,
                   "rsi2_slice_after": slice_dollars})
    c4_today = c4_signaled_today(args.symbols, db_path=c4_db)
    if c4_today:
        log_event({"ts": now_utc(), "type": "c4_same_day_skip",
                   "symbols": sorted(c4_today)})

    candidates = []
    for sym in args.symbols:
        try:
            df = bars.get(sym)
            if df is None or len(df) == 0:
                continue
            if rsi2_skip_blocked(positions, open_orders, sym, c4_today):
                log_event({"ts": now_utc(), "type": "skip", "strategy": RSI2,
                           "symbol": sym, "reason": "held_or_pending_or_c4"})
                continue
            if not last_bar_is_final(df["date"].iloc[-1]):
                log_event({"ts": now_utc(), "type": "skip", "strategy": RSI2,
                           "symbol": sym, "reason": "daily_bar_not_final"})
                continue
            if spy_bars is None or not last_bar_is_final(spy_bars["date"].iloc[-1]):
                continue
            if signals.rsi2_entry_signal(df, spy_bars):
                candidates.append({"symbol": sym,
                                   "rsi2": float(df["rsi2"].iloc[-1])})
        except Exception as exc:
            log_event({"ts": now_utc(), "type": "entry_symbol_error",
                       "symbol": sym, "error": repr(exc)})
            results["errors"].append({"symbol": sym, "error": repr(exc)})

    room = args.max_open - open_rsi2_count(state)
    if room <= 0:
        log_event({"ts": now_utc(), "type": "skip", "reason": "max_open_reached"})
        return
    cap = min(args.rsi2_cap, room)
    from rules import apply_rsi2_daily_cap
    selected = apply_rsi2_daily_cap(candidates, cap=cap)

    for cand in selected:
        try:
            sym = cand["symbol"]
            df = bars[sym]
            last = len(df) - 1
            px = float(df["close"].iloc[last])
            qty = size_rsi2_position(
                px, slice_dollars,
                open_notional=rsi2_open_notional(state),
                per_position_cap_pct=args.per_position_cap,
                open_positions=open_rsi2_count(state),
                max_open_positions=args.max_open)
            if qty <= 0:
                log_event({"ts": now_utc(), "type": "skip", "strategy": RSI2,
                           "symbol": sym, "reason": "slice_or_cap_exhausted"})
                continue
            # idempotency re-check: symbol may have gained a pending order
            # earlier in this run or between fetch and submit
            if symbol_has_open_order(open_orders, sym) or sym in (
                    t["symbol"] for t in state["open"].values()):
                continue
            n = next_sequence(existing_labels, RSI2, sym, ymd)
            entry_id = make_client_order_id(RSI2, sym, ymd, n)
            init_stop, _chand, partial_px = signals.rsi2_stops(
                px, float(df["atr14"].iloc[last]))
            resp = api.submit_order({
                "symbol": sym, "qty": qty, "side": "buy", "type": "market",
                "time_in_force": "day", "client_order_id": entry_id,
            })
            existing_labels.append(entry_id)
            if resp.get("error"):
                # order failed: record NOTHING in state, log the failure
                log_event({"ts": now_utc(), "type": "buy_error",
                           "strategy": RSI2, "symbol": sym,
                           "client_order_id": entry_id,
                           "error": str(resp["error"])[:300]})
                results["errors"].append(
                    {"symbol": sym,
                     "error": "buy: " + str(resp["error"])[:200]})
                continue
            t = {
                "strategy": RSI2, "symbol": sym, "qty": qty, "n": n,
                "date": ymd, "ref_px": px,
                "entry_notional": qty * px,
                "stop_px": round(init_stop, 2),
                "partial_px": round(partial_px, 2),
                "entry_id": entry_id,
                "entry_order_id": resp.get("id"),
                "entry_filled": False,
                "signal_date": str(df["date"].iloc[last]),
                "atr14": float(df["atr14"].iloc[last]),
                "entry_date": str(df["date"].iloc[min(last + 1, len(df) - 1)]),
            }
            state["open"][entry_id] = t
            results["rsi2"].append({"symbol": sym, "qty": qty,
                                    "rsi2": cand["rsi2"], "entry_id": entry_id,
                                    "stop_px": t["stop_px"],
                                    "partial_px": t["partial_px"]})
            log_event({"ts": now_utc(), "type": "buy", "strategy": RSI2,
                       "symbol": sym, "qty": qty, "client_order_id": entry_id,
                       "rsi2": cand["rsi2"], "dry_run": api.dry_run})
            # persist immediately: one bad symbol must not lose earlier orders
            if not api.dry_run:
                save_state(state)
            open_orders = list(open_orders) + [
                {"symbol": sym, "side": "buy", "qty": qty,
                 "client_order_id": entry_id}]
        except Exception as exc:
            log_event({"ts": now_utc(), "type": "entry_symbol_error",
                       "symbol": cand.get("symbol"), "error": repr(exc)})
            results["errors"].append({"symbol": cand.get("symbol"),
                                      "error": repr(exc)})


def run(args):
    if KILL_SWITCH.exists():
        fail("kill switch present — halting", code=3)
    if args.execute and os.environ.get("TRADING_BOT_PAPER_ONLY") != "1":
        fail("refusing to execute: TRADING_BOT_PAPER_ONLY=1 is required "
             "for --execute (paper-only guard, same as C4 shadow runner)")
    key, sec = load_credentials()
    api = AlpacaPaper(key, sec, dry_run=not args.execute)

    state = load_state()
    acct = api.get_account()
    portfolio_value = float(acct["portfolio_value"])
    positions = api.get_positions()
    open_orders = api.get_orders(status="open")
    existing_labels = all_client_order_ids(api)
    ymd = ymd_today()

    bars = {}
    for sym in set(args.symbols) | {t["symbol"] for t in state["open"].values()} | {"SPY"}:
        try:
            b = api.daily_bars(sym)
            if b is not None:
                bars[sym] = signals.add_indicators(b)
        except Exception as exc:
            log_event({"ts": now_utc(), "type": "bars_error", "symbol": sym,
                       "error": repr(exc)})

    results = {"rsi2": [], "stops_placed": [], "closed": [], "errors": [],
               "dry_run": api.dry_run}

    process_exits(state, api, bars, positions, results)
    if not api.dry_run:
        save_state(state)
    place_stops_after_fill(state, api, positions, open_orders, results)
    if not api.dry_run:
        save_state(state)
    process_entries(state, api, bars, bars.get("SPY"), positions, open_orders,
                    existing_labels, ymd, args, portfolio_value, results)
    # entries fill later intraday; stops are placed on the NEXT run after
    # the fill is confirmed (stop-after-fill contract, spec item 4)

    if not api.dry_run:
        save_state(state)
    print(json.dumps(results, indent=2, default=str))
    print(f"mode: {'DRY-RUN (nothing sent, nothing persisted to state)' if api.dry_run else 'PAPER ORDERS SENT'}")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbols", nargs="+", required=True,
                   help="candidate symbols to evaluate (RSI(2) only)")
    p.add_argument("--execute", action="store_true",
                   help="actually submit PAPER orders (default: dry-run; "
                        "dry-run persists nothing to state.json)")
    p.add_argument("--bucket-pct", type=float, default=0.30,
                   help="short-term bucket share of portfolio (default 0.30)")
    p.add_argument("--rsi2-cap", type=int, default=4,
                   help="max RSI(2) new buys per day (default 4)")
    p.add_argument("--max-open", type=int, default=4,
                   help="max open RSI(2) positions (default 4)")
    p.add_argument("--per-position-cap", type=float, default=0.10,
                   help="per-position cap as a share of the slice budget "
                        "(default 0.10)")
    p.add_argument("--c4-db", default=None,
                   help="path to the C4 shadow live_paper.db used for the "
                        "same-day skip and the 30% bucket overlap "
                        "(default /root/strat-swing/experiments/"
                        "strat_023/live_paper.db; fails loudly if missing)")
    args = p.parse_args(argv)
    run(args)


if __name__ == "__main__":
    main()
