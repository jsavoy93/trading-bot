"""STRAT-025 setup research — backtest engine (implements PREREGISTRATION §4–§10).

Indicators + honest-stop conventions copied from the frozen STRAT-022 engine
(experiments/strat_022/run_backtest.py) so results are directly comparable.
Trade simulation strategy:
  - For each (symbol, setup, config) we enumerate hypothetical trades from every
    candidate entry signal day t (entry t+1 open, exits per §4) WITHOUT anti-overlap,
    then apply anti-overlap deterministically (iterate signal days in order, keep a
    trade only if its entry index > previous kept trade's exit index). Baseline draws
    sample random eligible days and use the same hypothetical-trade table + the same
    anti-overlap pass, enabling 1000 cheap draws.
"""
from __future__ import annotations
import numpy as np
import pandas as pd

COST = 0.0010  # 10 bps per side, single frozen decision level (PREREG §5)


def wilder_rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    for i in range(period * 2, len(close)):
        avg_gain.iloc[i] = (avg_gain.iloc[i - 1] * (period - 1) + gain.iloc[i]) / period
        avg_loss.iloc[i] = (avg_loss.iloc[i - 1] * (period - 1) + loss.iloc[i]) / period
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    rsi = rsi.where(avg_loss != 0, 100.0)
    return rsi


def load_symbol(path) -> pd.DataFrame | None:
    df = pd.read_csv(path, parse_dates=["t"])
    df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    df["date"] = pd.to_datetime(pd.to_datetime(df["t"], utc=True).dt.date)
    df = df.drop_duplicates(subset="date").sort_values("date").reset_index(drop=True)
    if len(df) < 220:
        return None
    c = df["close"]
    prev_close = c.shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev_close).abs(),
                    (df["low"] - prev_close).abs()], axis=1).max(axis=1)
    df["sma50"] = c.rolling(50).mean()
    df["sma200"] = c.rolling(200).mean()
    df["ema20"] = c.ewm(span=20, adjust=False).mean()
    df["rsi2"] = wilder_rsi(c, 2)
    df["atr14"] = tr.rolling(14).mean()
    df["vol20"] = df["volume"].rolling(20).mean()
    df["range"] = df["high"] - df["low"]
    return df


def liq_ok(df: pd.DataFrame) -> pd.Series:
    """Common point-in-time eligibility (PREREG §3)."""
    return (df["vol20"].notna() & (df["vol20"] >= 1_000_000)
            & df["close"].notna() & (df["close"] >= 5.0))


def hypothetical_trades(df: pd.DataFrame, entry_days: np.ndarray, setup: str,
                        atr_mult: float, time_stop: int) -> dict[int, tuple[int, int, float]]:
    """For each signal day t in entry_days compute the no-anti-overlap trade.
    Returns {t: (entry_i, exit_i, gross)}; skips t if t+1 open invalid."""
    n = len(df)
    opens = df["open"].values
    closes = df["close"].values
    highs = df["high"].values
    atr14 = df["atr14"].values
    sma5 = None  # not used here (only g-variant differences live in setups' exit rules)
    out = {}
    # setup-specific per-day exit extras
    if setup == "gap_fill_fade":
        fill_level = df["close"].shift(1).values  # pre-gap close, fixed per signal day
    for t in entry_days:
        e = t + 1
        if e >= n or not np.isfinite(opens[e]) or opens[e] <= 0:
            continue
        stop = opens[e] - atr_mult * atr14[t]
        exit_i, exit_px = None, None
        s_max = min(e + time_stop, n)
        for s in range(e, s_max):
            if setup == "gap_fill_fade" and s > e and closes[s] >= fill_level[t]:
                exit_i, exit_px = s, closes[s]; break
            if np.isfinite(stop) and closes[s] <= stop:
                exit_i, exit_px = s, closes[s]; break
        if exit_i is None:
            exit_i = s_max - 1
            exit_px = closes[exit_i]
        out[int(t)] = (e, int(exit_i), exit_px / opens[e] - 1.0)
    return out


def anti_overlap(hyp: dict[int, tuple[int, int, float]], entry_days_sorted) -> list[tuple]:
    """Iterate signal days in order; keep trade iff entry_i > last kept exit_i."""
    kept, last_exit = [], -1
    for t in entry_days_sorted:
        if t not in hyp:
            continue
        e, x, g = hyp[t]
        if e > last_exit:
            kept.append((e, x, g))
            last_exit = x
    return kept


def metrics(kept: list[tuple], cost: float = COST) -> dict:
    if not kept:
        return {"n": 0, "mean_net": None, "t": None, "win_rate": None, "sharpe": None}
    r = np.array([g - 2 * cost for _, _, g in kept])
    sd = r.std(ddof=1)
    return {
        "n": int(len(r)),
        "mean_net": float(r.mean()),
        "t": float(r.mean() / (sd / np.sqrt(len(r)))) if sd > 0 else None,
        "win_rate": float((r > 0).mean()),
        "sharpe": float(r.mean() / sd) if sd > 0 else None,
        "avg_win": float(r[r > 0].mean()) if (r > 0).any() else None,
        "avg_loss": float(r[r <= 0].mean()) if (r <= 0).any() else None,
    }


def baseline_draws(hyp_cache, elig_by_sym, k_by_sym, rng, n_draws: int = 1000):
    """hyp_cache: sym -> {t: (e,x,g)} hypothetical trades from ALL eligible days.
    elig_by_sym: sym -> sorted eligible t list. k_by_sym: strategy signal count per sym.
    Returns dict of pooled metrics: mean of per-draw means, p5/p95 of draw means,
    pooled per-trade mean, per-draw mean list."""
    draw_means = []
    pooled_all = []
    for _ in range(n_draws):
        pooled = []
        for sym, elig in elig_by_sym.items():
            k = k_by_sym.get(sym, 0)
            if k == 0 or not elig:
                continue
            hyp = hyp_cache[sym]
            pick = rng.choice(len(elig), size=min(k, len(elig)), replace=False)
            days = sorted(elig[i] for i in pick)
            pooled.extend(anti_overlap(hyp, days))
        if pooled:
            r = np.array([g - 2 * COST for _, _, g in pooled])
            pooled_all.append(r)
            draw_means.append(float(r.mean()))
    dm = np.array(draw_means)
    pooled = np.concatenate(pooled_all) if pooled_all else np.array([])
    return {
        "n_draws": int(len(dm)),
        "mean_of_draw_means": float(dm.mean()) if len(dm) else None,
        "p5_draw_mean": float(np.percentile(dm, 5)) if len(dm) else None,
        "p95_draw_mean": float(np.percentile(dm, 95)) if len(dm) else None,
        "pooled_mean": float(pooled.mean()) if len(pooled) else None,
        "pooled_n": int(len(pooled)),
    }
