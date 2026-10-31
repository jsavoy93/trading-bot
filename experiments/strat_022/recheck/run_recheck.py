"""STRAT-023 Task A — survivorship-bias recheck of STRAT-022 config C4.

Runs the SAME C4 backtest (entry: close > SMA200 AND Wilder RSI(2) < 10, with
20d-mean volume >= 1,000,000 and close >= $5; execution next open; exits:
close > SMA5, 3xATR stop off entry open, 10-day time stop, close-checked) on:
  A) the ORIGINAL 379-symbol universe using the ORIGINAL Alpaca CSVs
     (replication control — must reproduce STRAT-022 numbers), and
  B) original universe + delisted additions (data_delisted/, built from the
     DATA-001 parquet, symbols whose bar series terminates mid-history).
Random baseline: identical engine/costs, entries uniform over eligible days
excluding all RSI(2)<10 signal days, count matched per symbol, seed 20221110.
Costs: 10 bps per side (net = gross - 20 bps) applied identically to strategy
and baseline. Periods: dev 2021-01-01..2024-12-31, holdout 2025-01-01..2026-10-28.
"""
from __future__ import annotations
import json, sys
from pathlib import Path
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from run_backtest import wilder_rsi, run_trades, signal_indices, eligible_indices  # noqa: E402

SEED = 20221110
ENTRY_RSI, ATR_MULT, TIME_STOP = 10, 3.0, 10  # C4 — fixed, not relaxed
COST = 0.0010  # per side


def load_symbol(sym: str, folder: Path) -> pd.DataFrame | None:
    f = folder / f"{sym}.csv"
    df = pd.read_csv(f, parse_dates=["t"], keep_default_na=False)
    for col in ("o", "h", "l", "c", "v"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    df["date"] = pd.to_datetime(pd.to_datetime(df["t"], utc=True).dt.date)
    df = df.dropna(subset=["close"]).drop_duplicates(subset="date").sort_values("date").reset_index(drop=True)
    if len(df) < 220:
        return None
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


def stats(trades):
    if not trades:
        return {"n": 0}
    rets = np.array([tr["gross"] - 2 * COST for tr in trades])
    mean, sd = rets.mean(), rets.std(ddof=1)
    return {
        "n": int(len(rets)),
        "mean_net_pct": float(mean * 100),
        "t_stat": float(mean / (sd / np.sqrt(len(rets)))) if sd > 0 else 0.0,
        "win_rate": float((rets > 0).mean()),
    }


def run_universe(frames: dict, period: str, tag: str):
    start = pd.Timestamp("2021-01-01") if period == "dev" else pd.Timestamp("2025-01-01")
    end = pd.Timestamp("2024-12-31") if period == "dev" else pd.Timestamp("2026-10-28")
    # signal days for C4; exclusion set for baseline = union of all-config signal
    # days (RSI<10 superset of RSI<5) — matches original engine semantics.
    sig = {}
    excl = {}
    for s, df in frames.items():
        sig[s] = signal_indices(df, ENTRY_RSI, start, end)
        excl[s] = set(sig[s])
    rng = np.random.default_rng(SEED)
    strat_trades, base_trades, strat_trades_delisted, base_trades_delisted = [], [], [], []
    for s in sorted(frames):
        df = frames[s]
        st = run_trades(df, s, sig[s], ATR_MULT, TIME_STOP)
        strat_trades.extend(st)
        if s in DELISTED:
            strat_trades_delisted.extend(st)
        elig = eligible_indices(df, start, end, excl[s])
        k = len(sig[s])
        if k > 0 and elig:
            pick = rng.choice(len(elig), size=min(k, len(elig)), replace=False)
            bt = run_trades(df, s, [elig[i] for i in sorted(pick)], ATR_MULT, TIME_STOP)
            base_trades.extend(bt)
            if s in DELISTED:
                base_trades_delisted.extend(bt)
    out = {"symbols": len(frames), "strat": stats(strat_trades), "baseline": stats(base_trades)}
    if strat_trades_delisted or base_trades_delisted:
        out["delisted_only_strat"] = stats(strat_trades_delisted)
        out["delisted_only_baseline"] = stats(base_trades_delisted)
    print(tag, json.dumps(out))
    return out


if __name__ == "__main__":
    orig_dir = HERE.parent / "data"
    dl_dir = HERE / "data_delisted"
    DELISTED = {f.stem for f in dl_dir.glob("*.csv")}
    frames_orig, frames_all = {}, {}
    for f in sorted(orig_dir.glob("*.csv")):
        df = load_symbol(f.stem, orig_dir)
        if df is not None:
            frames_orig[f.stem] = df
            frames_all[f.stem] = df
    n_orig = len(frames_orig)
    for f in sorted(dl_dir.glob("*.csv")):
        df = load_symbol(f.stem, dl_dir)
        if df is not None:
            frames_all[f.stem] = df
    print(f"loaded orig={n_orig} orig+delisted={len(frames_all)} (+{len(frames_all)-n_orig} delisted)")
    results = {"c4": {"entry_rsi": ENTRY_RSI, "atr_mult": ATR_MULT, "time_stop": TIME_STOP,
                      "cost_per_side_bps": 10, "seed": SEED}}
    for period in ("dev", "holdout"):
        results[period] = {
            "A_original_universe": run_universe(frames_orig, period, f"{period} A(orig)"),
            "B_with_delisted": run_universe(frames_all, period, f"{period} B(orig+delisted)"),
        }
    (HERE / "_results_recheck.json").write_text(json.dumps(results, indent=1))
    print("wrote", HERE / "_results_recheck.json")
