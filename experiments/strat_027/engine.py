"""STRAT-027 — portfolio engine: construction, costs, turnover, metrics, null model.

Portfolio contract (frozen in PREREGISTRATION.md):
  * Rebalance at the OPEN of the first trading day of each month.
  * Hold the selected names (equal weight, single-name cap) until the next rebalance open.
  * Turnover at a rebalance = sum |w_target - w_drifted| (one-way, after drift).
  * Cost at a rebalance = turnover * cost_rate (bps per side of traded notional).
  * Delisting rule: if a held name has no bar at the next rebalance open, it is
    liquidated at its LAST AVAILABLE close (same rule as the STRAT-022 engine).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class SimResult:
    label: str
    period_dates: list = field(default_factory=list)
    gross: np.ndarray = field(default_factory=lambda: np.zeros(0))
    net: np.ndarray = field(default_factory=lambda: np.zeros(0))
    turnover: np.ndarray = field(default_factory=lambda: np.zeros(0))
    holdings: list = field(default_factory=list)
    weights: list = field(default_factory=list)


def holding_returns(open_m: np.ndarray, close_m: np.ndarray,
                    last_finite_close_idx: np.ndarray,
                    rebal_idx: np.ndarray) -> np.ndarray:
    """(R-1, S) asset return from rebalance r open to rebalance r+1 open.

    If a symbol has no bar at the next rebalance open (delisted / halted), the
    return is computed against its last available close before that date.
    """
    R = len(rebal_idx)
    S = open_m.shape[1]
    out = np.full((max(R - 1, 0), S), np.nan, dtype=np.float64)
    for r in range(R - 1):
        i0, i1 = rebal_idx[r], rebal_idx[r + 1]
        p0 = open_m[i0].astype(np.float64)
        p1 = open_m[i1].astype(np.float64)
        ret = np.full(S, np.nan)
        ok = np.isfinite(p0) & np.isfinite(p1) & (p0 > 0)
        ret[ok] = p1[ok] / p0[ok] - 1.0
        # delisting / halt fallback: last available close before i1
        need = ~ok & np.isfinite(p0) & (p0 > 0)
        if need.any():
            j = last_finite_close_idx[i1 - 1]           # (S,) last row with a finite close
            last_px = close_m[j, np.arange(S)].astype(np.float64)
            good = need & (j >= i0) & np.isfinite(last_px) & (last_px > 0)
            ret[good] = last_px[good] / p0[good] - 1.0
            ret[need & ~good] = 0.0                     # no price at all -> flat (documented)
        out[r] = ret
    return out


def simulate(selections: list[np.ndarray], weights: list[np.ndarray],
             asset_ret: np.ndarray, cost_rate: float,
             label: str = "") -> SimResult:
    """Simulate a fixed selection schedule.

    selections[r]: symbol column indices held during period r
    weights[r]:    target weights for those symbols at rebalance r (sums to <= 1)
    asset_ret[r]:  (S,) per-asset return for period r
    """
    periods = len(selections)
    res = SimResult(label=label)
    prev_sel = None
    prev_w = None
    prev_gross = None
    for r in range(periods):
        sel = np.asarray(selections[r], dtype=int)
        w = np.asarray(weights[r], dtype=float)
        a = asset_ret[r]
        gross = float(np.sum(w * a[sel]))
        if prev_sel is None:
            turnover = float(np.sum(w))          # from cash
        else:
            drift = prev_w * (1.0 + asset_ret[r - 1][prev_sel])
            denom = 1.0 + prev_gross
            drift = drift / denom if denom != 0 else drift
            prev_map = {int(s): float(drift[k]) for k, s in enumerate(prev_sel)}
            new_map = {int(s): float(w[k]) for k, s in enumerate(sel)}
            turnover = 0.0
            for s in set(prev_map) | set(new_map):
                turnover += abs(new_map.get(s, 0.0) - prev_map.get(s, 0.0))
            turnover = float(turnover)
        cost = turnover * cost_rate
        res.period_dates.append(r)
        res.gross.append(gross)
        res.net.append(gross - cost)
        res.turnover.append(turnover)
        res.holdings.append(sel.copy())
        res.weights.append(w.copy())
        prev_sel, prev_w, prev_gross = sel, w, gross
    res.gross = np.array(res.gross)
    res.net = np.array(res.net)
    res.turnover = np.array(res.turnover)
    return res


def cap_weights(w: np.ndarray, cap: float) -> np.ndarray:
    """Apply a single-name cap with proportional redistribution (no cash)."""
    w = np.array(w, dtype=float)
    if cap <= 0 or cap >= 1:
        return w
    for _ in range(500):
        over = w > cap + 1e-12
        if not over.any():
            break
        excess = w[over].sum() - cap * over.sum()
        w[over] = cap
        free = ~over
        tot = w[free].sum()
        if not free.any() or tot <= 0:
            break
        w[free] = w[free] + excess * (w[free] / tot)
    return w


def equal_weight(n: int, cap: float) -> np.ndarray:
    """Equal weights with a single-name cap, redistributed among uncapped names."""
    w = np.ones(n) / n
    return cap_weights(w, cap)


def metrics(period_returns: np.ndarray, dates=None, periods_per_year: float | None = None,
            turnover: np.ndarray | None = None) -> dict:
    r = np.asarray(period_returns, dtype=float)
    n = len(r)
    if n == 0:
        return {}
    cum = float(np.prod(1.0 + r))
    if periods_per_year is None:
        periods_per_year = 12.0
    ann_ret = cum ** (periods_per_year / n) - 1.0 if cum > 0 else -1.0
    ann_vol = float(np.std(r, ddof=1) * np.sqrt(periods_per_year)) if n > 1 else float("nan")
    sharpe = (ann_ret / ann_vol) if ann_vol and ann_vol > 0 else float("nan")
    eq = np.cumprod(1.0 + r)
    peak = np.maximum.accumulate(eq)
    max_dd = float(np.min(eq / peak - 1.0))
    out = {
        "periods": n,
        "total_return": cum - 1.0,
        "ann_return": float(ann_ret),
        "ann_vol": float(ann_vol),
        "sharpe": float(sharpe),
        "max_drawdown": max_dd,
        "mean_period_return": float(np.mean(r)),
        "std_period_return": float(np.std(r, ddof=1)) if n > 1 else float("nan"),
        "worst_period": float(np.min(r)),
        "best_period": float(np.max(r)),
        "positive_periods": int(np.sum(r > 0)),
    }
    if turnover is not None and len(turnover):
        out["mean_period_turnover"] = float(np.mean(turnover))
        out["ann_turnover"] = float(np.mean(turnover) * periods_per_year)
    return out


def tstat_vs_random(strategy_period: np.ndarray, random_periods: np.ndarray) -> dict:
    """Compare the strategy's period returns against the random-portfolio cloud.

    random_periods: (n_random, n_periods) period return matrix.
    Returns unpaired z-score of the mean, paired t-stat against the random mean
    path, and the percentile rank of the strategy's mean.
    """
    s = np.asarray(strategy_period, dtype=float)
    R = np.atleast_2d(random_periods)
    if R.shape[1] != len(s):
        raise ValueError("period alignment mismatch")
    rand_means = R.mean(axis=1)
    mu = rand_means.mean()
    sd = rand_means.std(ddof=1)
    z_unpaired = (s.mean() - mu) / sd if sd > 0 else float("nan")
    pct = float((rand_means < s.mean()).mean() * 100.0)
    diff = s - R.mean(axis=0)
    t_paired = float(diff.mean() / (diff.std(ddof=1) / np.sqrt(len(diff)))) if diff.std(ddof=1) > 0 else float("nan")
    return {
        "random_mean": float(mu),
        "random_std": float(sd),
        "z_unpaired_mean": float(z_unpaired),
        "t_paired_periods": t_paired,
        "percentile_of_random_means": pct,
        "n_random": int(R.shape[0]),
    }


def tstat_paired(a: np.ndarray, b: np.ndarray) -> float:
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    d = a - b
    if len(d) < 2 or d.std(ddof=1) == 0:
        return float("nan")
    return float(d.mean() / (d.std(ddof=1) / np.sqrt(len(d))))
