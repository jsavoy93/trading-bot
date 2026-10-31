"""STRAT-023 — Paper-only daily shadow runner for the STRAT-022 C4 dip-buy rule.

STANDALONE. This runner:
  - NEVER places orders through the main bot account.
  - NEVER imports or uses the main bot's brokerage client (no src/ imports).
  - Only READS public daily bars from the Alpaca PAPER data API
    (same endpoint/credentials convention as experiments/strat_022/download_data.py).

Strategy (frozen from experiments/strat_022/PREREGISTRATION.md, config C4):
  Entry signal (on completed daily close t):
    close_t > SMA200_t  AND  Wilder RSI(2)_t < 10
    AND liquidity: trailing 20-day mean volume >= 1,000,000 AND close >= $5.
  Entry execution: NEXT trading day's OPEN (t+1), detected on the next run.
    (Matches PR 116 prereg section 4. Chosen over close-fill to mirror the
    backtest exactly; documented here per task spec.)
  Exits (whichever occurs first, evaluated on CLOSE of each holding day s >= t+1):
    1. close_s > SMA5_s            -> exit at that close   (reason 'sma5')
    2. close_s <= entry_open - 3.0 * ATR14_signal_day -> exit at that close ('atr_stop')
    3. held 10 trading days (entry day = day 1)          -> exit at that close ('time')
  Anti-overlap: one open position per symbol; new signals ignored while open.

Costs: 10 bps per side (net return = gross - 2 * 0.0010), matching the
10 bps cost level reported in PR 116.

Storage: its OWN SQLite db at experiments/strat_023/shadow.db (gitignored).
Tables:
  signals(symbol, signal_date, rsi2, close, sma200, atr14, status, created_ts)
    status: 'pending' -> 'filled' (or 'cancelled' if next open unavailable)
  trades(id, symbol, signal_date, entry_date, entry_price, exit_date,
         exit_price, exit_reason, gross_ret, net_ret, cost_bps, created_ts)
    exit_* NULL while the position is open.

HARD GUARD: refuses to run (exit code 2) unless env TRADING_BOT_PAPER_ONLY=1.
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("STRAT023_DB", HERE / "shadow.db"))

# --- C4 parameters (frozen; MUST NOT be relaxed) ---
ENTRY_RSI = 10        # RSI(2) must be BELOW this to enter
ATR_MULT = 3.0
TIME_STOP = 10        # trading days, entry day = day 1
COST_PER_SIDE = 0.0010  # 10 bps
MIN_VOL20 = 1_000_000
MIN_CLOSE = 5.0
WARMUP_BARS = 220     # need SMA200 + slack, same as strat_022 load_symbol

# Data source: same conventions as experiments/strat_022/download_data.py
ALPACA_DATA_URL = "https://data.alpaca.markets"  # PAPER data API only
UNIVERSE_FILE = Path(
    "/root/.openclaw/workspace/trading-bot/experiments/strat_006r_candidate_universe.py"
)
LOOKBACK_DAYS = 420   # calendar days of daily bars per fetch (~290 trading days)


def fail(msg: str, code: int = 2) -> "None":
    print(f"shadow_runner: {msg}", file=sys.stderr)
    sys.exit(code)


def load_paper_env() -> tuple[str, str]:
    """Paper credentials from the main workspace .env (read-only), same as
    experiments/strat_022/download_data.py. NEVER live credentials."""
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
    """Same universe source as strat_022: STRAT-006R curated liquid list."""
    import importlib.util

    if not UNIVERSE_FILE.exists():
        fail(f"universe file missing: {UNIVERSE_FILE}")
    spec = importlib.util.spec_from_file_location("strat006r_cand", UNIVERSE_FILE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return sorted({s for s in mod.POPULAR_LIQUID_US if s})


# ---------------------------------------------------------------- indicators
def wilder_rsi(close: pd.Series, period: int) -> pd.Series:
    """Bit-identical to experiments/strat_022/run_backtest.wilder_rsi."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    avg_gain = avg_gain.copy()
    avg_loss = avg_loss.copy()
    for i in range(period * 2, len(close)):
        avg_gain.iloc[i] = (avg_gain.iloc[i - 1] * (period - 1) + gain.iloc[i]) / period
        avg_loss.iloc[i] = (avg_loss.iloc[i - 1] * (period - 1) + loss.iloc[i]) / period
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    rsi = rsi.where(avg_loss != 0, 100.0)
    return rsi


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """df columns: t,o,h,l,c,v (strat_022 CSV format). Adds sma200/sma5/rsi2/
    atr14/vol20/date. Same formulas as run_backtest.load_symbol."""
    df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    df["date"] = pd.to_datetime(pd.to_datetime(df["t"], utc=True).dt.date)
    df = df.drop_duplicates(subset="date").sort_values("date").reset_index(drop=True)
    df["sma200"] = df["close"].rolling(200).mean()
    df["sma5"] = df["close"].rolling(5).mean()
    df["rsi2"] = wilder_rsi(df["close"], 2)
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    df["atr14"] = tr.rolling(14).mean()
    df["vol20"] = df["volume"].rolling(20).mean()
    return df


def entry_signal_rows(df: pd.DataFrame) -> pd.DataFrame:
    """C4 entry conditions on each completed close (same predicate as
    run_backtest.signal_indices with entry_rsi=10)."""
    ok = (
        df["sma200"].notna() & df["rsi2"].notna() & df["atr14"].notna() & df["vol20"].notna()
        & (df["close"] > df["sma200"])
        & (df["rsi2"] < ENTRY_RSI)
        & (df["vol20"] >= MIN_VOL20)
        & (df["close"] >= MIN_CLOSE)
    )
    return df[ok]


# ---------------------------------------------------------------- data fetch
def fetch_bars(symbol: str, key: str, sec: str) -> pd.DataFrame | None:
    """Daily bars, last LOOKBACK_DAYS calendar days, adjustment=raw — the
    strat_022/download_data.py conventions."""
    import requests

    start = (datetime.now(timezone.utc) - pd.Timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec}
    bars, token = [], None
    with requests.Session() as sess:
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
    if not bars:
        return None
    return pd.DataFrame(bars)[["t", "o", "h", "l", "c", "v"]]


def load_fixture_bars(fixture_dir: Path) -> dict[str, pd.DataFrame]:
    """CSVs in strat_022 data format (t,o,h,l,c,v) — used by tests and manual
    dry-runs; no network."""
    out = {}
    for f in sorted(fixture_dir.glob("*.csv")):
        out[f.stem] = pd.read_csv(f)
    return out


# ------------------------------------------------------------------ database
SCHEMA = """
CREATE TABLE IF NOT EXISTS signals(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  symbol TEXT NOT NULL,
  signal_date TEXT NOT NULL,
  rsi2 REAL, close REAL, sma200 REAL, atr14 REAL,
  status TEXT NOT NULL DEFAULT 'pending',   -- pending|filled|cancelled
  created_ts TEXT NOT NULL,
  UNIQUE(symbol, signal_date)
);
CREATE TABLE IF NOT EXISTS trades(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  symbol TEXT NOT NULL,
  signal_date TEXT NOT NULL,
  entry_date TEXT NOT NULL,
  entry_price REAL NOT NULL,
  exit_date TEXT, exit_price REAL, exit_reason TEXT,
  gross_ret REAL, net_ret REAL, cost_bps REAL NOT NULL,
  created_ts TEXT NOT NULL
);
"""


def open_db(path: Path):
    import sqlite3

    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    return conn


# ------------------------------------------------------------------- engine
def evaluate_exit(df: pd.DataFrame, entry_i: int, entry_px: float, stop: float):
    """C4 exit scan from entry day forward. Mirrors run_backtest.run_trades:
    SMA5 exit only checked for s > entry day; ATR stop from entry day; time
    stop caps at TIME_STOP trading days (entry day = day 1).
    Returns (exit_i, exit_px, reason) for the triggering bar, or the time-stop
    bar's close, or (None, None, None) if the data ends while still open (a
    live shadow position may remain open across runs — unlike the backtest,
    which force-closed at the last bar)."""
    n = len(df)
    for s in range(entry_i, min(entry_i + TIME_STOP, n)):
        c = df["close"].iloc[s]
        if s > entry_i and c > df["sma5"].iloc[s]:
            return s, float(c), "sma5"
        if np.isfinite(stop) and c <= stop:
            return s, float(c), "atr_stop"
    if n < entry_i + TIME_STOP:
        return None, None, None  # data ends before time stop -> still open
    exit_i = entry_i + TIME_STOP - 1
    return exit_i, float(df["close"].iloc[exit_i]), "time"


def run_once(db_path: Path, bars_by_symbol: dict[str, pd.DataFrame],
             run_ts: datetime | None = None) -> dict:
    """One shadow pass over the given bars. Returns a summary dict."""
    run_ts = run_ts or datetime.now(timezone.utc)
    conn = open_db(db_path)
    summary = {"filled": 0, "cancelled": 0, "closed": 0, "new_signals": 0,
               "still_open": 0, "symbols": len(bars_by_symbol)}
    try:
        for sym, raw in sorted(bars_by_symbol.items()):
            if len(raw) < WARMUP_BARS:
                continue
            df = add_indicators(raw.copy())
            if len(df) < WARMUP_BARS:
                continue
            dates = df["date"].dt.strftime("%Y-%m-%d").tolist()

            # 1) resolve pending signals: fill at next day's open if it exists
            pend = conn.execute(
                "SELECT id, signal_date FROM signals WHERE symbol=? AND status='pending'",
                (sym,)).fetchall()
            open_trade = conn.execute(
                "SELECT id, entry_date FROM trades WHERE symbol=? AND exit_date IS NULL",
                (sym,)).fetchone()
            for sig_id, sig_date in pend:
                idx = dates.index(sig_date) if sig_date in dates else None
                if idx is None:
                    conn.execute("UPDATE signals SET status='cancelled' WHERE id=?",
                                 (sig_id,))
                    summary["cancelled"] += 1
                    continue
                e = idx + 1
                if open_trade:
                    # anti-overlap: position already open -> signal cancelled
                    conn.execute("UPDATE signals SET status='cancelled' WHERE id=?",
                                 (sig_id,))
                    summary["cancelled"] += 1
                    continue
                if e >= len(df):
                    continue  # next open not observed yet; keep pending
                entry_px = float(df["open"].iloc[e])
                if not np.isfinite(entry_px) or entry_px <= 0:
                    conn.execute("UPDATE signals SET status='cancelled' WHERE id=?",
                                 (sig_id,))
                    summary["cancelled"] += 1
                    continue
                conn.execute(
                    "INSERT INTO trades(symbol, signal_date, entry_date, entry_price,"
                    " cost_bps, created_ts) VALUES (?,?,?,?,?,?)",
                    (sym, sig_date, dates[e], entry_px, COST_PER_SIDE * 1e4,
                     run_ts.isoformat()))
                conn.execute("UPDATE signals SET status='filled' WHERE id=?", (sig_id,))
                summary["filled"] += 1
                open_trade = (sig_id, dates[e])

            # 2) manage open trades: evaluate C4 exits bar-by-bar
            if open_trade:
                trade_id = conn.execute(
                    "SELECT id FROM trades WHERE symbol=? AND exit_date IS NULL",
                    (sym,)).fetchone()
                if trade_id:
                    trade_id = trade_id[0]
                    row = conn.execute(
                        "SELECT signal_date, entry_date, entry_price FROM trades WHERE id=?",
                        (trade_id,)).fetchone()
                    sig_date, entry_date, entry_px = row
                    e_idx = dates.index(entry_date) if entry_date in dates else None
                    if e_idx is None:
                        conn.execute("UPDATE trades SET exit_date=?, exit_price=?,"
                                     " exit_reason='data_gap' WHERE id=?",
                                     (dates[-1], float(df["close"].iloc[-1]), trade_id))
                        summary["closed"] += 1
                    else:
                        sig_idx = dates.index(sig_date)
                        stop = entry_px - ATR_MULT * df["atr14"].iloc[sig_idx]
                        exit_i, exit_px, reason = evaluate_exit(df, e_idx, entry_px, stop)
                        if exit_i is not None:
                            gross = exit_px / entry_px - 1.0
                            net = gross - 2 * COST_PER_SIDE
                            conn.execute(
                                "UPDATE trades SET exit_date=?, exit_price=?,"
                                " exit_reason=?, gross_ret=?, net_ret=? WHERE id=?",
                                (dates[exit_i], exit_px, reason, gross, net, trade_id))
                            summary["closed"] += 1
                            open_trade = None
                        else:
                            summary["still_open"] += 1

            # 3) new entry signals on the LATEST completed close only
            if not open_trade:
                last = len(df) - 1
                sigs = entry_signal_rows(df)
                if last in sigs.index:
                    sig_date = dates[last]
                    exists = conn.execute(
                        "SELECT 1 FROM signals WHERE symbol=? AND signal_date=?",
                        (sym, sig_date)).fetchone()
                    if not exists:
                        conn.execute(
                            "INSERT INTO signals(symbol, signal_date, rsi2, close,"
                            " sma200, atr14, status, created_ts)"
                            " VALUES (?,?,?,?,?,?, 'pending', ?)",
                            (sym, sig_date, float(df['rsi2'].iloc[last]),
                             float(df['close'].iloc[last]),
                             float(df['sma200'].iloc[last]),
                             float(df['atr14'].iloc[last]), run_ts.isoformat()))
                        summary["new_signals"] += 1
        conn.commit()
    finally:
        conn.close()
    return summary


def main() -> int:
    if os.environ.get("TRADING_BOT_PAPER_ONLY") != "1":
        fail("REFUSING TO RUN: set TRADING_BOT_PAPER_ONLY=1 to confirm paper-only "
             "shadow mode. This runner never places real orders; the guard exists "
             "so it can never be wired into live execution by accident.")
    fixture = os.environ.get("STRAT023_FIXTURE_DIR")
    if fixture:
        bars = load_fixture_bars(Path(fixture))
        print(f"fixture mode: {len(bars)} symbols from {fixture}")
    else:
        key, sec = load_paper_env()
        symbols = universe_symbols()
        print(f"fetching {len(symbols)} symbols from Alpaca PAPER data API")
        bars = {}
        for i, sym in enumerate(symbols):
            df = fetch_bars(sym, key, sec)
            if df is not None:
                bars[sym] = df
            if (i + 1) % 50 == 0:
                print(f"  {i+1}/{len(symbols)} fetched")
            time.sleep(0.35)
    summary = run_once(DB_PATH, bars)
    print("shadow run complete:", summary)
    print(f"db: {DB_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
