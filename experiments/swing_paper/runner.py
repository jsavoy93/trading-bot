"""STRAT-SWING-PAPER — single-shot paper swing executor (RSI(2) ONLY).

Places PAPER orders ONLY (https://paper-api.alpaca.markets) with per-strategy
client_order_id labels (see rules.py). Default is DRY-RUN: nothing is sent
unless --execute is passed, and dry-run writes NOTHING to state.json (only
the events log). Credentials come exclusively from the ALPACA_API_KEY /
ALPACA_API_SECRET environment variables (paper keys).

SCOPE DECISION (Josh, 2026-11-03): this runner trades RSI(2) ONLY. C4 stays
on the existing strat023-c4-shadow timer (labels sl_short-..., live_paper.db).

Per-run responsibilities (idempotent; safe to re-run same day):
  1. EXITS for open RSI(2) trades: chandelier stop updates (never lower;
     each raise is a PATCH with a NEW deterministic client_order_id and
     the returned order id is stored — fix B), 15-trading-day time exit
     (cancel-confirmed-then-sell — fix C). Closed trades are removed from
     state so the sleeve money frees up.
  2. STOP-AFTER-FILL (S1): exactly ONE GTC stop for the full held qty is
     placed only AFTER a confirmed entry fill. No partial half-sell, no
     OCO brackets (removed by Josh decision S1, 2026-11-03).
  3. NEW ENTRIES (S2): RSI(2) sizes against a FIXED 15% of equity
     (configurable --sleeve-pct), max 4 open positions, each at most 25%
     of the sleeve. Independent of C4 holdings. Skips symbols held in the
     account, symbols with any open/pending order (any label, including
     sl_short-*), and symbols C4 signaled on the last completed trading
     day (ref_date comparison — fix K, run order doesn't matter). Never
     trades on an incomplete daily bar. Requires TRADING_BOT_PAPER_ONLY=1
     (same guard as the C4 shadow runner).

Clock injection (fix J): run() takes a single `clock` callable; every
logic-path read of the wall clock (ymd_today, last_bar_is_final, C4
ref-date, daily-bars window) flows through it. datetime.now() appears
only in run()'s default clock and in log_event timestamps (logging only).

Never loops, never installs timers. Run once per day from a human-approved
schedule. State: logs/state.json (gitignored).
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd
import requests

import signals
from rules import (RSI2, make_client_order_id, make_labels,
                   make_stop_replace_client_order_id,
                   make_stopgap_client_order_id,
                   make_time_exit_client_order_id, next_sequence,
                   rsi2_slice_dollars, rsi2_skip_blocked,
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

# Round-4 fix (e): the C4 same-day skip is ref_date-based and therefore
# independent of WHICH order the two runners execute in — but it can
# only see C4 rows that EXIST yet. The strat023-c4-shadow user timer
# fires at 21:15 UTC, so RSI(2) --execute runs are REQUIRED to happen
# at/after 21:15 UTC (and dry-run inspection runs are free to run any
# time). This is a scheduling contract, documented here and in README.
RSI2_MIN_RUN_UTC = (21, 15)


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

    def daily_bars(self, symbol: str, now: datetime) -> pd.DataFrame | None:
        """Daily bars for `symbol`. `now` is the INJECTED clock (fix J):
        the request window must never be derived from a hidden real-clock
        read, otherwise a test (or a backfill) could silently see a
        different market window than the one the run decided on."""
        end = now
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

    def cancel_order(self, order_id: str) -> int:
        """DELETE /v2/orders/{id}. Returns the HTTP status code so callers
        can treat 404/422 on an already-canceled order as success (fix C).
        Dry-run returns 204 (as-if canceled)."""
        if self.dry_run:
            return 204
        r = self.session.delete(f"{PAPER_TRADING_URL}/v2/orders/{order_id}",
                                headers=self._headers(), timeout=15)
        return r.status_code


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
    """Append one event to the JSONL log. The timestamp comes from the
    LOGGING clock (`_log_now`), never from a decision clock — log
    timestamps are not decision inputs (fix J)."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    evt = dict(evt)
    evt.setdefault("ts", _log_now())
    with EVENTS_PATH.open("a") as f:
        f.write(json.dumps(evt, default=str) + "\n")


# ------------------------------------------------------------- helpers
def real_clock() -> datetime:
    """The DEFAULT clock: the real wall clock. It is consulted ONLY when
    run() is called without an injected clock (fix J)."""
    return datetime.now(timezone.utc)


def _log_now() -> datetime:
    """Wall clock for LOG timestamps only — never a decision input."""
    return datetime.now(timezone.utc)


def ymd_today(now: datetime) -> str:
    """Today's YYYYMMDD label, derived from the INJECTED clock."""
    return now.strftime("%Y%m%d")


def last_bar_is_final(df_last_date, now: datetime) -> bool:
    """A daily bar is tradeable only when it is final: either it is a
    completed prior day, or it is today AND the clock is past the US
    regular-session close (>= 21:05 UTC covers EST and DST).
    `now` is the INJECTED clock (fix J)."""
    d = pd.Timestamp(df_last_date).date()
    today = now.date()
    if d < today:
        return True
    if d > today:
        return False
    return (now.hour, now.minute) >= (DAILY_BAR_FINAL_UTC_HOUR,
                                      DAILY_BAR_FINAL_UTC_MINUTE)


def finished_bars_only(df, now: datetime):
    """Round-4 fix (g): EXITS and trail raises may consume only FINISHED
    daily bars. If the frame's last bar is today's still-open partial bar
    (clock before 21:05 UTC), drop it — chandelier/trading-day/ATR reads
    must never see an intraday snapshot."""
    if df is None or len(df) == 0:
        return df
    last = df["date"].iloc[-1]
    if last_bar_is_final(last, now=now):
        return df
    d = pd.Timestamp(last).date()
    dates = pd.to_datetime(df["date"]).dt.date
    return df[dates < d]


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


def last_completed_trading_day(now: datetime) -> "datetime.date":
    """The last completed US regular-session trading day for a run at
    `now` (fix K): today when the clock is past 21:05 UTC on a weekday,
    otherwise the most recent weekday strictly before today. Holidays are
    best-effort (a holiday 'today' simply yields the prior weekday; the
    C4 ref_date match then finds nothing for that day, which is safe)."""
    d = now.date()
    if (now.hour, now.minute) >= (DAILY_BAR_FINAL_UTC_HOUR,
                                  DAILY_BAR_FINAL_UTC_MINUTE) \
            and d.weekday() < 5:
        return d
    d = d - timedelta(days=1)
    while d.weekday() >= 5:
        d = d - timedelta(days=1)
    return d


def c4_signaled_for_ref_date(symbols, ref_date, db_path=None) -> set:
    """Set of `symbols` that C4 signaled for the given ref_date, read from
    the strat023-c4-shadow live_paper.db (labels sl_short-*). Real schema:
    orders(ref_date). Matching on ref_date (NOT created_ts) makes the skip
    independent of whether RSI(2) ran before or after C4 on the day (fix
    K). FAILS LOUDLY (RuntimeError) when the db is missing or the query
    fails — never silently 'no C4 signal'."""
    if not symbols:
        return set()
    out = set()
    ref = str(ref_date)[:10]
    wanted = {str(s).strip().upper() for s in symbols}
    try:
        con = _c4_connect(db_path)
    except RuntimeError:
        raise
    try:
        rows = con.execute(
            "SELECT DISTINCT symbol FROM orders WHERE substr(ref_date,1,10)=?",
            (ref,))
        out.update(str(r[0]).strip().upper() for r in rows if r[0])
    except Exception as exc:
        raise RuntimeError(
            f"C4 ref-date query failed on {_c4_db_path(db_path)} "
            f"(expected column orders.ref_date): {exc!r}")
    finally:
        con.close()
    return out & wanted


def c4_signaled_today(symbols, now: datetime, db_path=None) -> set:
    """C4 symbols signaled on the LAST COMPLETED TRADING DAY relative to
    the INJECTED clock (fix J + K)."""
    return c4_signaled_for_ref_date(
        symbols, last_completed_trading_day(now), db_path)


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


# Fix C: cancel-then-poll contract. After a cancel request we poll the
# order status; pending_cancel is treated as "wait and retry". Orders we
# cannot CONFIRM canceled stay in place (never leave a position naked)
# and the whole time exit is retried on the next run.
# Round-4 blocker (a): if the bounded poll times out we do NOT just
# walk away — we KEEP polling for an extended window, because a cancel
# that completes after the poll window would otherwise leave the
# position naked while the runner believes the stop is still live.
CANCEL_POLL_ATTEMPTS = 4
CANCEL_POLL_EXTENDED_ATTEMPTS = 30  # ~60s more at 2s per poll
CANCEL_POLL_SLEEP_S = 2.0
# 'replaced' is terminal too: the order was superseded by a replacement
# (the stop raise), so there is nothing left to cancel.
_TERMINAL_FOR_CANCEL = ("canceled", "expired", "filled", "replaced")


def _order_status(api, client_order_id):
    if not client_order_id:
        return None
    o = api.get_order_by_client_id(client_order_id)
    return str(o.get("status")) if o else None


def _sleep_s(seconds: float) -> None:  # monkeypatched in tests
    time.sleep(seconds)


def _ensure_canceled(api, order_id, client_order_id=None) -> bool:
    """Cancel `order_id` and CONFIRM the cancellation (fix C).
    - already canceled/expired/filled  -> True (nothing to do)
    - DELETE returns 404/422            -> True ONLY if the order status
      confirms terminal (an already-canceled order rejecting the cancel
      is success; anything unverifiable is NOT)
    - DELETE 200/204                    -> poll until status is terminal;
      pending_cancel counts as wait. Unconfirmed after the retries ->
      False (caller must NOT market-sell; the stop stays in place)."""
    st = _order_status(api, client_order_id)
    if st in _TERMINAL_FOR_CANCEL:
        return True
    code = api.cancel_order(order_id)
    if code in (404, 422):
        st = _order_status(api, client_order_id)
        return st in _TERMINAL_FOR_CANCEL
    if code not in (200, 204):
        return False
    total = CANCEL_POLL_ATTEMPTS + CANCEL_POLL_EXTENDED_ATTEMPTS
    for i in range(total):
        if i == CANCEL_POLL_ATTEMPTS:
            log_event({"type": "cancel_still_pending",
                       "order_id": order_id,
                       "note": "bounded poll timed out; extending the "
                               "cancel-confirmation window (round-4 fix a)"})
        _sleep_s(CANCEL_POLL_SLEEP_S)
        st = _order_status(api, client_order_id)
        if st in _TERMINAL_FOR_CANCEL:
            return True
    return _order_status(api, client_order_id) in _TERMINAL_FOR_CANCEL


def _close_trade(state, entry_id, reason, results):
    state["open"].pop(entry_id, None)
    results["closed"].append({"entry_id": entry_id, "reason": reason})
    log_event({"type": "close", "entry_id": entry_id,
               "reason": reason})


def _leftover_rsi2_sell_orders(open_orders, symbol: str) -> list:
    """Open sell orders for `symbol` placed by THIS runner (rsi2-* labels).
    Used by fix D: when the position is gone, every leftover rsi2 sell
    order for the symbol must be canceled so it can never fire naked."""
    sym = str(symbol).strip().upper()
    out = []
    for o in open_orders or []:
        if not isinstance(o, dict):
            continue
        if str(o.get("side", "")) != "sell":
            continue
        if str(o.get("symbol", "")).strip().upper() != sym:
            continue
        coid = str(o.get("client_order_id") or "")
        if coid.startswith("rsi2-"):
            out.append(o)
    return out


def process_exits(state, api, bars, positions, results, open_orders=(),
                  now=None):
    """For every open RSI(2) trade: drop dead entries, manage the single
    GTC stop, apply the 15-day time exit. Per-trade try/except; state
    saved by caller after each mutation is done at run() level via save hook.

    `now` (round-4 fix g) is the injected clock used to decide whether
    the last daily bar is finished; run() always passes it. When omitted
    the real clock is consulted (legacy direct-call behavior)."""
    if now is None:
        now = real_clock()
    for entry_id in list(state.get("open", {}).keys()):
        try:
            t = state["open"].get(entry_id)
            if t is None:
                continue
            sym = t["symbol"]
            df = finished_bars_only(bars.get(sym), now)
            if df is None or len(df) == 0:
                continue

            # -- confirm entry fill (stop-after-fill contract) --
            if not t.get("entry_filled"):
                o = api.get_order_by_client_id(entry_id)
                if o is None:
                    continue
                st = o.get("status")
                if st in ("canceled", "expired", "rejected"):
                    # Round-4 blocker (c): a PARTIALLY FILLED entry that
                    # was then canceled is NOT a dead trade. The filled
                    # shares are a live position — keep them tracked and
                    # put a stop on them (place_stops_after_fill, next
                    # pass). Never drop filled shares on the floor.
                    fq = float(o.get("filled_qty") or 0)
                    if fq > 0:
                        t["qty"] = int(fq) if fq == int(fq) else fq
                        t["entry_filled"] = True
                        t["entry_fill_px"] = float(
                            o.get("filled_avg_price")
                            or t.get("ref_px") or 0)
                        t["entry_notional"] = (float(t["qty"])
                                                * t["entry_fill_px"])
                        fill_px = t["entry_fill_px"]
                        atr_sig = float(t.get("atr14") or 0)
                        if fill_px > 0 and atr_sig > 0 \
                                and not t.get("stop_order_id"):
                            init_stop, _ch = signals.rsi2_stops(
                                fill_px, atr_sig)
                            t["stop_px"] = round(init_stop, 2)
                        filled_at = str(o.get("filled_at") or "")
                        if filled_at:
                            t["entry_date"] = filled_at[:10]
                        log_event({"type": "partial_entry_kept",
                                   "entry_id": entry_id,
                                   "status": st,
                                   "filled_qty": fq})
                        # fall through to normal management below
                    else:
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
                        init_stop, _ch = signals.rsi2_stops(fill_px, atr_sig)
                        t["stop_px"] = round(init_stop, 2)
                        t["entry_notional"] = float(t.get("qty", 0)) * fill_px
                    # FIX G: the 15-day time exit counts from the FILL
                    # date, not the signal date.
                    filled_at = str(o.get("filled_at") or "")
                    if filled_at:
                        t["entry_date"] = filled_at[:10]
                else:  # still pending — nothing to manage yet
                    continue

            # -- FIX D: position gone / stop filled -> close, free slot --
            # stop_filled is detected via the CURRENT stop client id (a
            # raised stop has a new id after every PATCH — fix B), and any
            # leftover rsi2 sell orders for the symbol are canceled.
            if held_qty(positions, sym) <= 0:
                reason = "position_gone"
                unconfirmed = []
                for o in _leftover_rsi2_sell_orders(open_orders, sym):
                    if not _ensure_canceled(api, o.get("id"),
                                            o.get("client_order_id")):
                        unconfirmed.append(o.get("id"))
                        log_event({"type": "exit_error",
                                   "entry_id": entry_id,
                                   "error": f"leftover sell cancel "
                                            f"unconfirmed for {o.get('id')}"})
                if unconfirmed:
                    # Round-4 fix (f): an unconfirmed cleanup cancel means
                    # we do NOT know the leftover sell is gone. Keep the
                    # trade tracked and retry the cleanup next run — never
                    # close the book on an order we cannot account for.
                    results["errors"].append(
                        {"entry_id": entry_id,
                         "error": "leftover sell cancel unconfirmed: "
                                  + ",".join(str(x) for x in unconfirmed)
                                  + "; trade kept open for retry"})
                    continue
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
                # FIX C: confirm THE stop is actually canceled BEFORE the
                # market sell; a failed/unconfirmed cancel must NOT be
                # followed by a market sell (never leave a position naked,
                # never double-sell). Retry on the next run instead.
                stop_ok = True
                if t.get("stop_order_id") and not _ensure_canceled(
                        api, t["stop_order_id"],
                        t.get("stop_client_order_id")):
                    stop_ok = False
                    log_event({"type": "exit_error",
                               "entry_id": entry_id,
                               "error": f"cancel unconfirmed for "
                                        f"{t.get('stop_order_id')}; "
                                        f"market sell skipped; stop left "
                                        f"in place"})
                if not stop_ok:
                    results["errors"].append(
                        {"entry_id": entry_id,
                         "error": "time_exit: cancel unconfirmed; no sell "
                                  "sent; stop left in place"})
                    continue
                sell_qty = int(min(float(t["qty"]), held_qty(positions, sym)))
                if sell_qty <= 0:
                    _close_trade(state, entry_id, "time_exit_no_position", results)
                    continue
                resp = api.submit_order({
                    "symbol": sym, "qty": sell_qty, "side": "sell",
                    "type": "market", "time_in_force": "day",
                    "client_order_id": make_time_exit_client_order_id(
                        RSI2, sym, t["date"], t["n"]),
                })
                if resp.get("error"):
                    # Round-4 fix (d): the stop was already CONFIRMED
                    # canceled for this time exit — a rejected market
                    # sell must not leave the position naked. Re-place a
                    # fresh GTC stop at the last stop price IMMEDIATELY.
                    gen = int(t.get("stop_gen", 1)) + 1
                    stop_coid = make_stop_replace_client_order_id(
                        RSI2, sym, t["date"], t["n"], gen)
                    stop_resp = api.submit_order({
                        "symbol": sym, "qty": sell_qty, "side": "sell",
                        "type": "stop",
                        "stop_price": round(float(t.get("stop_px") or 0), 2),
                        "time_in_force": "gtc",
                        "client_order_id": stop_coid,
                    })
                    log_event({"type": "time_exit_sell_error",
                               "entry_id": entry_id,
                               "error": resp["error"]})
                    if stop_resp.get("error") or not stop_resp.get("id"):
                        results["errors"].append(
                            {"entry_id": entry_id,
                             "error": "time_exit sell failed AND stop "
                                      "re-place failed: POSITION "
                                      "UNPROTECTED — manual attention: "
                                      + str(resp["error"])[:120]})
                    else:
                        t["stop_order_id"] = stop_resp["id"]
                        t["stop_client_order_id"] = stop_coid
                        t["stop_gen"] = gen
                        log_event({"type": "stop_replaced_after_sell_error",
                                   "entry_id": entry_id,
                                   "stop_order_id": stop_resp["id"],
                                   "stop_client_order_id": stop_coid})
                        results["errors"].append(
                            {"entry_id": entry_id,
                             "error": "time_exit sell failed; fresh GTC "
                                      "stop re-placed: "
                                      + str(resp["error"])[:120]})
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
                    # FIX B: a stop raise is a PATCH that carries a NEW
                    # deterministic client_order_id (...-stop-r<k>) and a
                    # replace returns a NEW order id — store BOTH from the
                    # response, never keep a stale id.
                    gen = int(t.get("stop_gen", 1)) + 1
                    new_coid = make_stop_replace_client_order_id(
                        RSI2, sym, t["date"], t["n"], gen)
                    resp = api.replace_order(t["stop_order_id"], {
                        "stop_price": new_px,
                        "client_order_id": new_coid})
                    if resp.get("error"):
                        log_event({"type": "stop_update_error",
                                   "entry_id": entry_id, "error": resp["error"]})
                    elif not resp.get("id"):
                        log_event({"type": "stop_update_error",
                                   "entry_id": entry_id,
                                   "error": "replace returned no order id; "
                                            "keeping previous id/price"})
                    else:
                        t["stop_order_id"] = resp["id"]
                        t["stop_client_order_id"] = (
                            resp.get("client_order_id") or new_coid)
                        t["stop_gen"] = gen
                        t["stop_px"] = new_px
                        log_event({"type": "stop_update",
                                   "entry_id": entry_id, "new_stop": new_px,
                                   "stop_order_id": t["stop_order_id"],
                                   "stop_client_order_id":
                                       t["stop_client_order_id"]})
        except Exception as exc:  # never let one symbol kill the run
            log_event({"type": "exit_symbol_error",
                       "entry_id": entry_id, "error": repr(exc)})
            results["errors"].append({"entry_id": entry_id,
                                      "error": repr(exc)})


def _last_finished_close(bars, sym, now) -> float | None:
    """Last FINISHED daily close for `sym` (round-4 fix g: today's
    partial bar is never a pricing input)."""
    df = finished_bars_only((bars or {}).get(sym), now)
    if df is None or len(df) == 0:
        return None
    return float(df["close"].iloc[-1])


def place_stops_after_fill(state, api, positions, open_orders, results,
                           bars=None, now=None):
    """S1: place exactly ONE GTC stop for the FULL held qty of trades
    whose entry is confirmed filled but which have no stop yet. Total
    open sell qty for a symbol NEVER exceeds held qty.

    Round-4 blocker (b): a stop rejected because price gapped at/below
    the planned stop price must never be retried at the same impossible
    price forever, and the shares must never be left unprotected:
      - current price <= planned stop  -> SELL AT MARKET (-stopgap label)
      - otherwise                      -> re-price (never above plan and
                                           never above current price) and
                                           re-place with a fresh label"""
    if now is None:
        now = real_clock()
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
            stop_qty = int(min(float(t["qty"]), held - already))
            if stop_qty <= 0:
                continue
            stop_coid = make_labels(RSI2, sym, t["date"], t["n"])[1]
            planned = round(float(t["stop_px"]), 2)
            stop_resp = api.submit_order({
                "symbol": sym, "qty": stop_qty, "side": "sell",
                "type": "stop", "stop_price": planned,
                "time_in_force": "gtc",
                "client_order_id": stop_coid,
            })
            if stop_resp.get("error"):
                # Round-4 blocker (b): rejected stop. Decide using the
                # last FINISHED close — never retry a dead price forever.
                cur_px = _last_finished_close(bars, sym, now)
                if cur_px is not None and cur_px <= planned:
                    # price gapped to/below the stop: sell at market now
                    gap_resp = api.submit_order({
                        "symbol": sym, "qty": stop_qty, "side": "sell",
                        "type": "market", "time_in_force": "day",
                        "client_order_id": make_stopgap_client_order_id(
                            RSI2, sym, t["date"], t["n"]),
                    })
                    if gap_resp.get("error"):
                        log_event({"type": "stop_error",
                                   "entry_id": entry_id,
                                   "error": gap_resp["error"]})
                        results["errors"].append(
                            {"entry_id": entry_id,
                             "error": "stopgap sell failed: POSITION "
                                      "UNPROTECTED — manual attention: "
                                      + str(gap_resp["error"])[:120]})
                    else:
                        log_event({"type": "stop_gap_market_sell",
                                   "entry_id": entry_id, "symbol": sym,
                                   "qty": stop_qty,
                                   "planned_stop": planned,
                                   "last_close": cur_px})
                        _close_trade(state, entry_id,
                                     "stop_gap_market_sell", results)
                else:
                    # re-price: keep the plan unless it sits above the
                    # current price, then never above (cur - 0.02)
                    new_px = planned if cur_px is None else round(
                        min(planned, cur_px - 0.02), 2)
                    if new_px <= 0:
                        results["errors"].append(
                            {"entry_id": entry_id,
                             "error": "stop rejected and re-price "
                                      f"invalid ({new_px}); stop NOT "
                                      "placed — manual attention: "
                                      + str(stop_resp["error"])[:120]})
                        continue
                    gen = int(t.get("stop_gen", 1)) + 1
                    retry_coid = make_stop_replace_client_order_id(
                        RSI2, sym, t["date"], t["n"], gen)
                    retry_resp = api.submit_order({
                        "symbol": sym, "qty": stop_qty, "side": "sell",
                        "type": "stop", "stop_price": new_px,
                        "time_in_force": "gtc",
                        "client_order_id": retry_coid,
                    })
                    if retry_resp.get("error") or not retry_resp.get("id"):
                        log_event({"type": "stop_error",
                                   "entry_id": entry_id,
                                   "error": str(retry_resp.get("error")
                                                 or "no order id")})
                        results["errors"].append(
                            {"entry_id": entry_id,
                             "error": "stop rejected and re-priced retry "
                                      "also failed: POSITION UNPROTECTED "
                                      "— manual attention: "
                                      + str(stop_resp["error"])[:120]})
                    else:
                        t["stop_order_id"] = retry_resp["id"]
                        t["stop_client_order_id"] = retry_coid
                        t["stop_gen"] = gen
                        t["stop_px"] = new_px
                        log_event({"type": "stop_replaced_after_reject",
                                   "entry_id": entry_id, "symbol": sym,
                                   "stop_px": new_px,
                                   "stop_client_order_id": retry_coid})
                        results["stops_placed"].append(
                            {"entry_id": entry_id, "symbol": sym,
                             "stop_qty": stop_qty, "repriced": True})
                continue
            if not stop_resp.get("id"):
                log_event({"type": "stop_error",
                           "entry_id": entry_id,
                           "error": "stop submit returned no order id"})
                continue
            t["stop_order_id"] = stop_resp["id"]
            t["stop_client_order_id"] = stop_coid
            t["stop_gen"] = 1
            open_orders = list(open_orders) + [
                {"symbol": sym, "side": "sell", "qty": stop_qty}]
            log_event({"type": "stop_placed",
                       "entry_id": entry_id, "symbol": sym,
                       "qty": stop_qty, "dry_run": api.dry_run})
            results["stops_placed"].append(
                {"entry_id": entry_id, "symbol": sym, "stop_qty": stop_qty})
        except Exception as exc:
            log_event({"type": "stop_symbol_error",
                       "entry_id": entry_id, "error": repr(exc)})
            results["errors"].append({"entry_id": entry_id, "error": repr(exc)})


# ------------------------------------------------------------- entries
def process_entries(state, api, bars, spy_bars, positions, open_orders,
                    existing_labels, ymd, args, portfolio_value, results,
                    now: datetime):
    """now (fix J): the injected clock snapshot used for bar-finality
    checks and the C4 ref-date (last completed trading day)."""
    slice_dollars = rsi2_slice_dollars(portfolio_value, args.sleeve_pct)
    # S2 (Josh, 2026-11-03): the sleeve is a FIXED share of equity.
    # The old subtract-C4 logic was REMOVED — RSI(2) sizing depends only
    # on its own sleeve and its own open positions.
    c4_db = getattr(args, "c4_db", None)
    c4_today = c4_signaled_today(args.symbols, db_path=c4_db, now=now)
    if c4_today:
        log_event({"type": "c4_same_day_skip",
                   "symbols": sorted(c4_today)})

    candidates = []
    for sym in args.symbols:
        try:
            df = bars.get(sym)
            if df is None or len(df) == 0:
                continue
            if rsi2_skip_blocked(positions, open_orders, sym, c4_today):
                log_event({"type": "skip", "strategy": RSI2,
                           "symbol": sym, "reason": "held_or_pending_or_c4"})
                continue
            if not last_bar_is_final(df["date"].iloc[-1], now=now):
                log_event({"type": "skip", "strategy": RSI2,
                           "symbol": sym, "reason": "daily_bar_not_final"})
                continue
            if spy_bars is None or not last_bar_is_final(
                    spy_bars["date"].iloc[-1], now=now):
                continue
            if signals.rsi2_entry_signal(df, spy_bars):
                candidates.append({"symbol": sym,
                                   "rsi2": float(df["rsi2"].iloc[-1])})
        except Exception as exc:
            log_event({"type": "entry_symbol_error",
                       "symbol": sym, "error": repr(exc)})
            results["errors"].append({"symbol": sym, "error": repr(exc)})

    room = args.max_open - open_rsi2_count(state)
    if room <= 0:
        log_event({"type": "skip", "reason": "max_open_reached"})
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
                log_event({"type": "skip", "strategy": RSI2,
                           "symbol": sym, "reason": "slice_or_cap_exhausted"})
                continue
            # idempotency re-check: symbol may have gained a pending order
            # earlier in this run or between fetch and submit
            if symbol_has_open_order(open_orders, sym) or sym in (
                    t["symbol"] for t in state["open"].values()):
                continue
            n = next_sequence(existing_labels, RSI2, sym, ymd)
            entry_id = make_client_order_id(RSI2, sym, ymd, n)
            init_stop, _chand = signals.rsi2_stops(
                px, float(df["atr14"].iloc[last]))
            resp = api.submit_order({
                "symbol": sym, "qty": qty, "side": "buy", "type": "market",
                "time_in_force": "day", "client_order_id": entry_id,
            })
            existing_labels.append(entry_id)
            if resp.get("error"):
                # order failed: record NOTHING in state, log the failure
                log_event({"type": "buy_error",
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
                                    "stop_px": t["stop_px"]})
            log_event({"type": "buy", "strategy": RSI2,
                       "symbol": sym, "qty": qty, "client_order_id": entry_id,
                       "rsi2": cand["rsi2"], "dry_run": api.dry_run})
            # persist immediately: one bad symbol must not lose earlier orders
            if not api.dry_run:
                save_state(state)
            open_orders = list(open_orders) + [
                {"symbol": sym, "side": "buy", "qty": qty,
                 "client_order_id": entry_id}]
        except Exception as exc:
            log_event({"type": "entry_symbol_error",
                       "symbol": cand.get("symbol"), "error": repr(exc)})
            results["errors"].append({"symbol": cand.get("symbol"),
                                      "error": repr(exc)})


def run(args, clock=None):
    """One bounded daily run.

    `clock` is a callable returning the current UTC datetime (fix J).
    It is the SINGLE source of "now" for every logic decision:
    ymd_today, last_bar_is_final, the C4 same-day ref-date check and the
    daily-bars request window. The default clock reads the real wall
    clock; tests inject a fixed clock. datetime.now() is otherwise used
    only for log-event timestamps (logging, never a decision input)."""
    if KILL_SWITCH.exists():
        fail("kill switch present — halting", code=3)
    if args.execute and os.environ.get("TRADING_BOT_PAPER_ONLY") != "1":
        fail("refusing to execute: TRADING_BOT_PAPER_ONLY=1 is required "
             "for --execute (paper-only guard, same as C4 shadow runner)")
    key, sec = load_credentials()
    api = AlpacaPaper(key, sec, dry_run=not args.execute)

    # fix J: ONE injected clock drives every time-dependent decision.
    now = (clock or real_clock)()

    # Round-4 fix (e): C4 shadow writes today's ref_date rows at 21:15
    # UTC. Refuse an --execute run in the 21:05-21:15 window so the C4
    # same-day skip can never miss rows that C4 has not written yet.
    if args.execute and (now.hour, now.minute) < RSI2_MIN_RUN_UTC:
        fail("--execute must run at/after 21:15 UTC (C4 shadow timer "
             "fires 21:15 UTC; earlier runs could double-trade a symbol "
             "C4 has not signaled yet — see RSI2_MIN_RUN_UTC / README)")

    state = load_state()
    acct = api.get_account()
    portfolio_value = float(acct["portfolio_value"])
    positions = api.get_positions()
    open_orders = api.get_orders(status="open")
    existing_labels = all_client_order_ids(api)
    ymd = ymd_today(now)

    bars = {}
    for sym in set(args.symbols) | {t["symbol"] for t in state["open"].values()} | {"SPY"}:
        try:
            b = api.daily_bars(sym, now=now)
            if b is not None:
                bars[sym] = signals.add_indicators(b)
        except Exception as exc:
            log_event({"type": "bars_error", "symbol": sym,
                       "error": repr(exc)})

    results = {"rsi2": [], "stops_placed": [], "closed": [], "errors": [],
               "dry_run": api.dry_run}

    # fix D: the open-order list is needed to cancel leftover rsi2 sell
    # orders when a position is gone. Round-4 fix (g): the injected clock
    # reaches the exits so today's partial bar is never a decision input.
    process_exits(state, api, bars, positions, results,
                  open_orders=open_orders, now=now)
    if not api.dry_run:
        save_state(state)
    place_stops_after_fill(state, api, positions, open_orders, results,
                           bars=bars, now=now)
    if not api.dry_run:
        save_state(state)
    process_entries(state, api, bars, bars.get("SPY"), positions, open_orders,
                    existing_labels, ymd, args, portfolio_value, results,
                    now=now)
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
    p.add_argument("--sleeve-pct", type=float, default=0.15,
                   help="RSI(2) sleeve as a fixed share of portfolio equity "
                   "(S2, default 0.15; independent of C4 holdings)")
    p.add_argument("--rsi2-cap", type=int, default=4,
                   help="max RSI(2) new buys per day (default 4)")
    p.add_argument("--max-open", type=int, default=4,
                   help="max open RSI(2) positions (default 4)")
    p.add_argument("--per-position-cap", type=float, default=0.25,
                   help="per-position cap as a share of the RSI(2) sleeve "
                   "(S2, default 0.25 — 4 positions exactly fill the sleeve)")
    p.add_argument("--c4-db", default=None,
                   help="path to the C4 shadow live_paper.db used for the "
                   "ref-date skip (default /root/strat-swing/experiments/"
                   "strat_023/live_paper.db; fails loudly if missing)")
    args = p.parse_args(argv)
    run(args)


if __name__ == "__main__":
    main()
