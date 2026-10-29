"""STRAT-022 backtest engine — implements experiments/strat_022/PREREGISTRATION.md exactly.

Dev: entry dates 2021-01-01..2024-12-31. Holdout: 2025-01-01..2026-10-28 (locked by CLI flag).
Grid: 8 configs (ENTRY_RSI {10,5} x ATR_MULT {2,3} x TIME_STOP {5,10}).
Costs 5bps and 10bps per side. Random baseline: matched entries, same exits, seed 20221110.
"""
from __future__ import annotations
import json, sys
from pathlib import Path
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
SEED = 20221110
CONFIGS = [
    ("C1", 10, 2.0, 5), ("C2", 10, 2.0, 10),
    ("C3", 10, 3.0, 5), ("C4", 10, 3.0, 10),
    ("C5", 5, 2.0, 5),  ("C6", 5, 2.0, 10),
    ("C7", 5, 3.0, 5),  ("C8", 5, 3.0, 10),
]


def wilder_rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    # Wilder recursion after seed
    avg_gain = avg_gain.copy()
    avg_loss = avg_loss.copy()
    for i in range(period * 2, len(close)):
        avg_gain.iloc[i] = (avg_gain.iloc[i - 1] * (period - 1) + gain.iloc[i]) / period
        avg_loss.iloc[i] = (avg_loss.iloc[i - 1] * (period - 1) + loss.iloc[i]) / period
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    rsi = rsi.where(avg_loss != 0, 100.0)
    return rsi


def load_symbol(sym: str) -> pd.DataFrame | None:
    f = DATA / f"{sym}.csv"
    if not f.exists():
        return None
    df = pd.read_csv(f, parse_dates=["t"])
    df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    df["date"] = pd.to_datetime(pd.to_datetime(df["t"], utc=True).dt.date)
    df = df.drop_duplicates(subset="date").sort_values("date").reset_index(drop=True)
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


def run_trades(df: pd.DataFrame, sym: str, entries: list[int], atr_mult: float, time_stop: int) -> list[dict]:
    """entries: signal indices (day t). Execution next open. One position at a time.
    Returns list of trades with daily position series dates for equity aggregation."""
    trades = []
    n = len(df)
    last_exit = -1
    for t in entries:
        if t <= last_exit:  # anti-overlap: signal while in trade -> ignored
            continue
        e = t + 1
        if e >= n:
            continue
        entry_px = df["open"].iloc[e]
        if not np.isfinite(entry_px) or entry_px <= 0:
            continue
        stop = entry_px - atr_mult * df["atr14"].iloc[t]
        exit_i, exit_px, reason = None, None, None
        for s in range(e, min(e + time_stop, n)):
            c = df["close"].iloc[s]
            if s > e and c > df["sma5"].iloc[s]:
                exit_i, exit_px, reason = s, c, "sma5"; break
            if np.isfinite(stop) and c <= stop:
                exit_i, exit_px, reason = s, c, "atr_stop"; break
        if exit_i is None:
            exit_i = min(e + time_stop, n) - 1
            exit_px = df["close"].iloc[exit_i]
            reason = "time"
        trades.append({
            "sym": sym,
            "entry_date": df["date"].iloc[e], "exit_date": df["date"].iloc[exit_i],
            "entry_i": e, "exit_i": exit_i, "entry_px": entry_px, "exit_px": exit_px,
            "reason": reason,
            "gross": exit_px / entry_px - 1.0,
        })
        last_exit = exit_i
    return trades


def signal_indices(df: pd.DataFrame, entry_rsi: int, start: pd.Timestamp, end: pd.Timestamp) -> list[int]:
    ok = (
        (df["date"] >= start) & (df["date"] <= end)
        & df["sma200"].notna() & df["rsi2"].notna() & df["atr14"].notna()
        & df["vol20"].notna()
        & (df["close"] > df["sma200"]) & (df["rsi2"] < entry_rsi)
        & (df["vol20"] >= 1_000_000) & (df["close"] >= 5.0)
    )
    return df.index[ok].tolist()


def eligible_indices(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp,
                     exclude: set[int]) -> list[int]:
    ok = (
        (df["date"] >= start) & (df["date"] <= end)
        & df["sma200"].notna() & df["atr14"].notna() & df["vol20"].notna()
        & (df["vol20"] >= 1_000_000) & (df["close"] >= 5.0)
    )
    return [i for i in df.index[ok] if i not in exclude]


# trade-level + equity metrics live in metrics_equity()


def main(period: str):
    assert period in ("dev", "holdout")
    start = pd.Timestamp("2021-01-01") if period == "dev" else pd.Timestamp("2025-01-01")
    end = pd.Timestamp("2024-12-31") if period == "dev" else pd.Timestamp("2026-10-28")

    sym_files = sorted(DATA.glob("*.csv"))
    frames = {}
    for f in sym_files:
        df = load_symbol(f.stem)
        if df is not None:
            frames[f.stem] = df
    print(f"loaded {len(frames)} symbols")

    # union trading dates for daily equity
    all_dates = sorted({d for df in frames.values() for d in df["date"]})
    dates_index = pd.DatetimeIndex(all_dates)

    # signal sets per config; excluded = union of all signal days (for baseline)
    sig = {cid: {} for cid, *_ in CONFIGS}
    all_signal_idx = {s: set() for s in frames}
    for cid, rsi_e, _, _ in CONFIGS:
        for s, df in frames.items():
            idx = signal_indices(df, rsi_e, start, end)
            sig[cid][s] = idx
            all_signal_idx[s].update(idx)

    rng = np.random.default_rng(SEED)
    results = {"period": period, "window": [str(start.date()), str(end.date())],
               "symbols": len(frames), "configs": {}}
    for cid, rsi_e, atr_m, ts in CONFIGS:
        strat_trades, base_trades = [], []
        for s, df in frames.items():
            st = run_trades(df, s, sig[cid][s], atr_m, ts)
            strat_trades.extend(st)
            # baseline: matched count random eligible non-signal days
            elig = eligible_indices(df, start, end, all_signal_idx[s])
            k = len(sig[cid][s])
            if k > 0 and elig:
                pick = rng.choice(len(elig), size=min(k, len(elig)), replace=False)
                bt = run_trades(df, s, [elig[i] for i in sorted(pick)], atr_m, ts)
                base_trades.extend(bt)
        cfg = {"params": {"entry_rsi": rsi_e, "atr_mult": atr_m, "time_stop": ts}}
        cfg["strat"] = {}
        cfg["baseline"] = {}
        for cost_bps, key in ((0.0005, "net5bps"), (0.0010, "net10bps")):
            cfg["strat"][key] = metrics_equity(strat_trades, frames, dates_index, cost_bps)
            cfg["baseline"][key] = metrics_equity(base_trades, frames, dates_index, cost_bps)
        results["configs"][cid] = cfg
        print(cid, "strat n=", cfg["strat"]["net10bps"]["n"],
              "mean10bps=", round(cfg["strat"]["net10bps"].get("mean_net", 0) * 100, 3),
              "base mean=", round(cfg["baseline"]["net10bps"].get("mean_net", 0) * 100, 3))
    out = HERE / f"_results_{period}.json"
    out.write_text(json.dumps(results, default=str, indent=1))
    print("wrote", out)


def metrics_equity(trades, frames, dates_index, cost):
    """trade-level metrics + daily equal-weight equity across symbols."""
    if not trades:
        return {"n": 0}
    rets = np.array([tr["gross"] - 2 * cost for tr in trades])
    wins, losses = rets[rets > 0], rets[rets <= 0]
    mean, sd = rets.mean(), rets.std(ddof=1)
    daily = {}
    per_sym = {}
    for tr in trades:
        per_sym.setdefault(tr["sym"], []).append(tr)
    for sym, trs in per_sym.items():
        df = frames[sym]
        d_arr = df["date"].values
        c_arr = df["close"].values
        for tr in trs:
            for s in range(tr["entry_i"], tr["exit_i"] + 1):
                d = pd.Timestamp(d_arr[s])
                r = c_arr[s] / c_arr[s - 1] - 1.0 if s > 0 else 0.0
                dd_ = daily.setdefault(d, [0.0, 0])
                dd_[0] += r
                dd_[1] += 1
    ser = pd.Series(0.0, index=dates_index)
    cnt = {}
    for d, (r, c) in daily.items():
        if d in ser.index and c > 0:
            ser[d] = r / c
            cnt[d] = c
    # costs: each entry/exit books cost on one position; weight 1/cnt that day
    for tr in trades:
        for d in (tr["entry_date"], tr["exit_date"]):
            if d in ser.index and cnt.get(d):
                ser[d] -= cost / cnt[d]
    eq = np.log1p(ser).cumsum()
    dd = float((eq - eq.cummax()).min())
    dsd = ser.std(ddof=1)
    return {
        "n": int(len(rets)),
        "win_rate": float((rets > 0).mean()),
        "avg_win": float(wins.mean()) if len(wins) else 0.0,
        "avg_loss": float(losses.mean()) if len(losses) else 0.0,
        "mean_net": float(mean),
        "t_stat": float(mean / (sd / np.sqrt(len(rets)))) if sd > 0 else 0.0,
        "per_trade_sharpe": float(mean / sd) if sd > 0 else 0.0,
        "ann_sharpe": float(ser.mean() / dsd * np.sqrt(252)) if dsd > 0 else 0.0,
        "max_dd": dd,
    }


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "dev")
