"""STRAT-026 — LOG-ONLY shadow runner for the rsi2_regime (RSI(2) dip-buy) strategy.

STANDALONE. This runner:
  - NEVER places orders — not even paper orders. It only logs hypothetical
    signals/trades to a JSONL file. There is NO order-submission code path:
    the only network call is a READ (HTTP GET) of daily bars from the Alpaca
    PAPER data API, reusing the strat_023 fetch conventions.
  - NEVER imports the main bot, its brokerage client, or anything in src/.
  - Owns its OWN kill-switch file (KILL_SWITCH below), fully separate from
    strat023-c4-shadow's guard. If the file exists, the run halts
    immediately (exit code 3) without fetching or logging anything.

Strategy (frozen from experiments/strat_026/run_research.py, S1a rsi2_regime,
the STRAT-026 research record — parameters MUST NOT be relaxed):
  Entry signal (on completed daily close t):
    Wilder RSI(2)_t < 10
    AND liquidity: vol20 >= 1,000,000 AND close >= $5
    AND regime filter ON: SPY close_t > SPY SMA200_t (trend filter).
  Entry execution: NEXT trading day's OPEN (t+1), detected on next run.
  Pro exits (evaluated on each holding day s >= t+1, close-checked stops):
    initial stop  entry_open - 2.0 * ATR14(t)              (close-checked)
    chandelier    max(close since entry) - 3.0 * ATR14(t)  (close-checked;
                  chandelier updated AFTER the day-s stop check — no lookahead)
    effective stop on day s = max(initial stop, chandelier stop)
    partial       50% of the position at +2R (risk = 2.0 * ATR14(t)) when the
                  intraday HIGH touches entry + 2R (filled at exactly 2R)
    time exit     15 trading days after entry day
  Anti-overlap: one open hypothetical position per symbol.

Costs: 10 bps per side (net = gross - 20 bps), matching the research record.

Storage (all gitignored):
  logs/shadow_events.jsonl — one JSON line per event (signal/entry/exit/kill)
  logs/state.json          — pending signals + open hypothetical positions
"""

from __future__ import annotations

import json
import os
import sys
import time
from bisect import bisect_right as _bisect_right
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
LOG_DIR = Path(os.environ.get("STRAT026_LOG_DIR", HERE / "logs"))
EVENTS_PATH = LOG_DIR / "shadow_events.jsonl"
STATE_PATH = LOG_DIR / "state.json"

# --- STRAT-026 frozen parameters (from the research record; DO NOT relax) ---
ENTRY_RSI = 10          # RSI(2) must be BELOW this to enter
INIT_STOP_ATR = 2.0     # initial stop = entry - 2.0 * ATR14(signal day)
TRAIL_ATR = 3.0         # chandelier = max(close since entry) - 3.0 * ATR14(t)
PARTIAL_R = 2.0         # 50% partial profit at +2R (intraday high touch)
TIME_EXIT = 15          # trading days after entry day
COST_PER_SIDE = 0.0010  # 10 bps per side
MIN_VOL20 = 1_000_000
MIN_CLOSE = 5.0
WARMUP_BARS = 220       # SMA200 + slack (same convention as strat_022/023)

# --- Own kill-switch file (SEPARATE from strat023-c4-shadow's guard) ---
KILL_SWITCH = HERE / "KILL_SWITCH"

# Data source: READ-ONLY Alpaca PAPER data API (same conventions as
# experiments/strat_023/shadow_runner.py). GET only; no order endpoints.
ALPACA_DATA_URL = "https://data.alpaca.markets"
UNIVERSE_FILE = Path(os.environ.get(
    "STRAT026_UNIVERSE_FILE",
    "/root/.openclaw/workspace/trading-bot/experiments/strat_006r_candidate_universe.py",
))
LOOKBACK_DAYS = 420  # calendar days of daily bars per fetch (~290 trading days)


def fail(msg: str, code: int = 2):
    print(f"strat026-shadow: {msg}", file=sys.stderr)
    sys.exit(code)


# ------------------------------------------------------------- event log
def append_event(evt: dict) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with EVENTS_PATH.open("a") as f:
        f.write(json.dumps(evt, default=str) + "\n")


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {"pending": {}, "open": {}}  # keyed by symbol


def save_state(state: dict) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1))
    tmp.replace(STATE_PATH)


# ---------------------------------------------------------- indicators
def wilder_rsi(close: pd.Series, period: int) -> pd.Series:
    """Bit-identical to the frozen STRAT-022/025/026 research engines.

    NOTE: the STRAT-026 research engine does NOT substitute 100 for a
    zero-average-loss window (RSI is left undefined/NaN there), so this
    implementation deliberately omits that substitution.
    """
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    for i in range(period * 2, len(close)):
        avg_gain.iloc[i] = (avg_gain.iloc[i - 1] * (period - 1) + gain.iloc[i]) / period
        avg_loss.iloc[i] = (avg_loss.iloc[i - 1] * (period - 1) + loss.iloc[i]) / period
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    return 100 - 100 / (1 + rs)


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """df columns t,o,h,l,c,v. Adds date/sma200/rsi2/atr14/vol20.
    Same formulas as the research engine load_symbol()."""
    df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    df["date"] = pd.to_datetime(pd.to_datetime(df["t"], utc=True).dt.date)
    df = df.drop_duplicates(subset="date").sort_values("date").reset_index(drop=True)
    c = df["close"]
    prev_close = c.shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    df["sma200"] = c.rolling(200).mean()
    df["rsi2"] = wilder_rsi(c, 2)
    df["atr14"] = tr.rolling(14).mean()
    df["vol20"] = df["volume"].rolling(20).mean()
    return df


def entry_signal(df: pd.DataFrame, regime_on: bool) -> bool:
    """STRAT-026 S1a entry predicate on the LATEST completed close.
    regime_on = SPY close > SPY SMA200 on the same date (trend filter)."""
    last = len(df) - 1
    if last < WARMUP_BARS - 1:
        return False
    row_ok = (
        df["sma200"].notna() & df["rsi2"].notna() & df["atr14"].notna() & df["vol20"].notna()
        & (df["rsi2"] < ENTRY_RSI)
        & (df["vol20"] >= MIN_VOL20)
        & (df["close"] >= MIN_CLOSE)
    )
    return bool(row_ok.iloc[last]) and regime_on


def pro_exit_scan(df: pd.DataFrame, entry_i: int, entry_px: float, atr_signal: float,
                  took_partial_in: bool = False):
    """Pro-exit scan from the entry day forward, mirroring
    run_research.pro_exits_trade exactly (close-checked stops, no-lookahead
    chandelier, 2R high-touch partial, time exit).
    Returns (exit_i, exit_px, reason, took_partial, gross) or
    (None, None, None, took_partial, None) if data ends while still open."""
    n = len(df)
    closes, highs = df["close"].values, df["high"].values
    risk = INIT_STOP_ATR * atr_signal
    init_stop = entry_px - risk
    partial_px = entry_px + PARTIAL_R * risk
    took_partial = took_partial_in
    chandelier = closes[entry_i]
    end = min(entry_i + TIME_EXIT, n)
    exit_i, reason = None, None
    for s in range(entry_i, end):
        if not took_partial and highs[s] >= partial_px:
            took_partial = True
        stop = max(init_stop, chandelier - TRAIL_ATR * atr_signal)
        if closes[s] <= stop:
            exit_i, reason = s, ("init_stop" if stop == init_stop else "trail_stop")
            break
        chandelier = max(chandelier, closes[s])
    if exit_i is None:
        if n < entry_i + TIME_EXIT:
            return None, None, None, took_partial, None  # still open
        exit_i, reason = end - 1, "time"
    exit_px = float(closes[exit_i])
    gross = exit_px / entry_px - 1.0
    if took_partial:
        gross = 0.5 * (partial_px / entry_px - 1.0) + 0.5 * gross
    return exit_i, exit_px, reason, took_partial, float(gross)


# --------------------------------------------------------------- engine
def run_once(bars_by_symbol: dict, spy_bars: pd.DataFrame, log_dir: Path = LOG_DIR,
             run_ts: datetime | None = None) -> dict:
    """One LOG-ONLY shadow pass. Writes events to <log_dir>/shadow_events.jsonl
    and persists pending/open state to <log_dir>/state.json. Never orders."""
    global EVENTS_PATH, STATE_PATH
    run_ts = run_ts or datetime.now(timezone.utc)
    old_events, old_state = EVENTS_PATH, STATE_PATH
    EVENTS_PATH, STATE_PATH = log_dir / "shadow_events.jsonl", log_dir / "state.json"
    try:
        state = load_state()
        summary = {"new_signals": 0, "entries": 0, "exits": 0, "still_open": 0,
                   "blocked_regime_off": 0, "symbols": len(bars_by_symbol)}

        spy = add_indicators(spy_bars.copy())
        # Regime lookup: the latest SPY bar at or BEFORE the symbol's signal
        # date (no lookahead). Exact-date match in the normal case.
        spy_dates = list(spy["date"])
        spy_regime = (spy["close"] > spy["sma200"]).astype(bool).tolist()

        def regime_at(date) -> bool:
            """True when SPY close > SPY SMA200 on the latest SPY bar at or
            before `date` (no future data used)."""
            idx = _bisect_right(spy_dates, date) - 1
            if idx < 0:
                return False
            return bool(spy_regime[idx])

        for sym, raw in sorted(bars_by_symbol.items()):
            if len(raw) < WARMUP_BARS:
                continue
            if sym == "SPY":
                # SPY is the regime benchmark, not a tradable symbol here.
                continue
            df = add_indicators(raw.copy())
            dates = df["date"].dt.strftime("%Y-%m-%d").tolist()
            last = len(df) - 1
            last_date = dates[last]

            # regime filter for the latest close
            reg_on = regime_at(df["date"].iloc[last])

            # 1) resolve pending signals: hypothetical entry at next open
            pend = state["pending"].get(sym)
            if pend and pend["signal_date"] in dates:
                idx = dates.index(pend["signal_date"])
                e = idx + 1
                if state["open"].get(sym):
                    append_event({"ts": run_ts.isoformat(), "type": "signal_cancelled",
                                  "symbol": sym, "signal_date": pend["signal_date"],
                                  "reason": "position_already_open"})
                    state["pending"].pop(sym, None)
                elif e < len(df) and np.isfinite(df["open"].iloc[e]) and df["open"].iloc[e] > 0:
                    entry_px = float(df["open"].iloc[e])
                    state["open"][sym] = {
                        "signal_date": pend["signal_date"],
                        "signal_rsi2": pend["rsi2"], "atr14": pend["atr14"],
                        "entry_date": dates[e], "entry_price": entry_px,
                        "took_partial": False,
                    }
                    state["pending"].pop(sym, None)
                    append_event({"ts": run_ts.isoformat(), "type": "entry",
                                  "symbol": sym, "signal_date": pend["signal_date"],
                                  "entry_date": dates[e], "entry_price": round(entry_px, 4),
                                  "atr14_signal": round(pend["atr14"], 4),
                                  "reason": "rsi2<%d_and_spy_above_sma200" % ENTRY_RSI})
                    summary["entries"] += 1

            # 2) manage open hypothetical positions: pro exits
            pos = state["open"].get(sym)
            if pos and pos["entry_date"] in dates:
                e_idx = dates.index(pos["entry_date"])
                exit_i, exit_px, reason, took_p, gross = pro_exit_scan(
                    df, e_idx, pos["entry_price"], pos["atr14"],
                    took_partial_in=pos.get("took_partial", False))
                if exit_i is not None:
                    net = gross - 2 * COST_PER_SIDE
                    append_event({"ts": run_ts.isoformat(), "type": "exit",
                                  "symbol": sym, "entry_date": pos["entry_date"],
                                  "entry_price": round(pos["entry_price"], 4),
                                  "exit_date": dates[exit_i],
                                  "exit_price": round(exit_px, 4),
                                  "exit_reason": reason, "took_partial": took_p,
                                  "gross_ret": round(gross, 6),
                                  "net_ret": round(net, 6),
                                  "cost_bps": COST_PER_SIDE * 1e4})
                    state["open"].pop(sym, None)
                    summary["exits"] += 1
                else:
                    if took_p != pos.get("took_partial", False):
                        pos["took_partial"] = True
                        append_event({"ts": run_ts.isoformat(), "type": "partial",
                                      "symbol": sym, "entry_date": pos["entry_date"],
                                      "price": round(pos["entry_price"]
                                                     + PARTIAL_R * INIT_STOP_ATR * pos["atr14"], 4),
                                      "reason": "2R_high_touch_50pct"})
                    summary["still_open"] += 1

            # 3) new entry signal on the LATEST completed close only
            if sym not in state["open"] and sym not in state["pending"]:
                if entry_signal(df, reg_on):
                    state["pending"][sym] = {
                        "signal_date": last_date,
                        "rsi2": float(df["rsi2"].iloc[last]),
                        "atr14": float(df["atr14"].iloc[last]),
                    }
                    append_event({"ts": run_ts.isoformat(), "type": "signal",
                                  "symbol": sym, "signal_date": last_date,
                                  "rsi2": round(float(df["rsi2"].iloc[last]), 2),
                                  "close": round(float(df["close"].iloc[last]), 4),
                                  "spy_above_sma200": reg_on,
                                  "reason": "rsi2<%d_and_spy_above_sma200" % ENTRY_RSI})
                    summary["new_signals"] += 1
                elif (bool(df["rsi2"].notna().iloc[last])
                      and float(df["rsi2"].iloc[last]) < ENTRY_RSI
                      and not reg_on):
                    summary["blocked_regime_off"] += 1

        save_state(state)
        return summary
    finally:
        EVENTS_PATH, STATE_PATH = old_events, old_state


# ---------------------------------------------------------- data fetch
def fetch_bars(symbol: str, key: str, sec: str, session=None) -> pd.DataFrame | None:
    """READ-ONLY daily bars from the Alpaca PAPER data API (GET only).
    Same conventions as strat_023. No order endpoint is ever contacted.
    ``session`` (a requests-like session) may be injected by tests."""
    import requests

    start = (datetime.now(timezone.utc) - pd.Timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec}
    bars, token = [], None
    own = session is None
    sess = session if session is not None else requests.Session()
    try:
        while True:
            p = {"symbols": symbol, "timeframe": "1Day", "start": start,
                 "limit": 10000, "adjustment": "raw"}
            if token:
                p["page_token"] = token
            r = sess.get(f"{ALPACA_DATA_URL}/v2/stocks/bars", headers=headers,
                         params=p, timeout=60)
            if r.status_code == 429:
                time.sleep(20)
                continue
            if r.status_code != 200:
                return None
            j = r.json()
            raw = j.get("bars", [])
            if isinstance(raw, dict):
                raw = raw.get(symbol, [])
            bars.extend(raw)
            token = j.get("next_page_token")
            if not token:
                break
    finally:
        if own:
            sess.close()
    if not bars:
        return None
    return pd.DataFrame(bars)[["t", "o", "h", "l", "c", "v"]]


def load_paper_env() -> tuple[str, str]:
    """Paper credentials from the main workspace .env (read-only), same as
    strat_023. NEVER live credentials."""
    env_path = Path("/root/.openclaw/workspace/trading-bot/.env")
    key, sec = os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_API_SECRET")
    if key and sec:
        return key, sec
    if not env_path.exists():
        fail("paper credentials unavailable (no .env and no ALPACA_* env vars)")
    for line in env_path.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip().strip('"')
            if k == "ALPACA_API_KEY":
                key = v
            elif k == "ALPACA_API_SECRET":
                sec = v
    if not (key and sec):
        fail("paper credentials missing from .env")
    return key, sec


def universe_symbols() -> list[str]:
    """Same universe source as strat_023: STRAT-006R curated liquid list."""
    import importlib.util

    if not UNIVERSE_FILE.exists():
        fail(f"universe file missing: {UNIVERSE_FILE}")
    spec = importlib.util.spec_from_file_location("strat006r_cand", UNIVERSE_FILE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return sorted({s for s in mod.POPULAR_LIQUID_US if s})


def main() -> int:
    # 1) OWN kill-switch (separate from strat023-c4-shadow)
    if KILL_SWITCH.exists():
        append_event({"ts": datetime.now(timezone.utc).isoformat(),
                      "type": "kill", "reason": "kill_switch_file_present"})
        fail("REFUSING TO RUN: kill-switch file present "
             f"({KILL_SWITCH}). Remove it to resume shadow logging.", code=3)
    # 2) LOG-ONLY guard: this runner has no order code path at all; the env
    # guard exists so it can never be wired into execution by accident.
    if os.environ.get("STRAT026_LOG_ONLY") != "1":
        fail("REFUSING TO RUN: set STRAT026_LOG_ONLY=1 to confirm LOG-ONLY "
             "shadow mode. This runner never places orders (not even paper).")
    fixture = os.environ.get("STRAT026_FIXTURE_DIR")
    if fixture:
        fdir = Path(fixture)
        bars = {f.stem: pd.read_csv(f) for f in sorted(fdir.glob("*.csv"))}
        spy = pd.read_csv(fdir / "SPY.csv")
        print(f"fixture mode: {len(bars)} symbols + SPY from {fixture}")
    else:
        key, sec = load_paper_env()
        symbols = universe_symbols()
        print(f"fetching {len(symbols)} symbols + SPY (READ-ONLY) from Alpaca paper data API")
        spy = fetch_bars("SPY", key, sec)
        if spy is None:
            fail("could not fetch SPY bars (regime filter unavailable)")
        bars = {}
        for i, sym in enumerate(symbols):
            df = fetch_bars(sym, key, sec)
            if df is not None:
                bars[sym] = df
            if (i + 1) % 50 == 0:
                print(f"  {i+1}/{len(symbols)} fetched")
            time.sleep(0.35)
    summary = run_once(bars, spy)
    print("shadow run complete (LOG-ONLY, no orders):", summary)
    print(f"events: {EVENTS_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
