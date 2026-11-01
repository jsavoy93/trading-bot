"""STRAT-027 — study driver: factor selection, portfolio simulation, benchmarks, metrics.

Run order (frozen in PREREGISTRATION.md):
  1. load the price panel
  2. compute factors + eligibility at every monthly rebalance
  3. development window: evaluate every pre-declared factor individually
  4. holdout window: single scripted run, every pre-declared factor
  5. random-portfolio null (1,000 draws, seed 20261101) and SPY benchmark
  6. write results JSON

No parameter is tuned anywhere in this file. Selection sizes and cost rates come
from PREREGISTRATION.md.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from engine import (
    SimResult,
    equal_weight,
    holding_returns,
    metrics,
    simulate,
    tstat_paired,
    tstat_vs_random,
)
from fundamentals import fundamental_factors, load_fundamentals
from panel import compute_factors, load_panel

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
FACTOR_CACHE = HERE / "data" / "fundamental_factors.npz"

# ---- frozen pre-registration constants -------------------------------------
DEV_START = np.datetime64("2021-01-01")
HOLDOUT_START = np.datetime64("2025-01-01")
N_NAMES = 30
POSITION_CAP = 0.08
COST_BPS = 10.0
COST_RATE = COST_BPS / 10_000.0
N_RANDOM = 1000
SEED = 20261101

PRICE_FACTORS = [
    ("MOM12_1", "higher", "12-1 month momentum (skip most recent month)"),
    ("LVOL", "lower", "trailing 252-day realized volatility (lower is better)"),
]
FUND_FACTORS = [
    ("QUALITY", "higher", "return on equity from the latest 10-K filed on or before the rebalance"),
    ("VALUE", "higher", "earnings yield (FY net income / market cap) from the latest 10-K filed on or before the rebalance"),
]
SENSITIVITY_N = [10, 50]
SENSITIVITY_COST_BPS = [5.0, 20.0]


def select_names(values: np.ndarray, eligible: np.ndarray, r: int, n: int,
                 direction: str) -> np.ndarray:
    """Top-n eligible names by factor value at rebalance r (deterministic tie-break)."""
    vals = values[r].astype(np.float64)
    ok = eligible[r] & np.isfinite(vals)
    idx = np.flatnonzero(ok)
    if idx.size == 0:
        return np.array([], dtype=int)
    v = vals[idx]
    order = np.lexsort((idx, -v)) if direction == "higher" else np.lexsort((idx, v))
    return np.sort(idx[order][: min(n, idx.size)])


def beta_diag(y: np.ndarray, x: np.ndarray) -> dict:
    """OLS of strategy period returns on SPY period returns (diagnostic, not a pass bar)."""
    y = np.asarray(y, float)
    x = np.asarray(x, float)
    X = np.column_stack([np.ones(len(x)), x])
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ coef
    dof = max(len(y) - 2, 1)
    s2 = float(resid @ resid) / dof
    cov = s2 * np.linalg.inv(X.T @ X)
    se_a = float(np.sqrt(cov[0, 0]))
    ss_tot = float(((y - y.mean()) ** 2).sum())
    return {
        "alpha_per_period": float(coef[0]),
        "beta": float(coef[1]),
        "t_alpha": float(coef[0] / se_a) if se_a > 0 else float("nan"),
        "r_squared": float(1.0 - (resid @ resid) / ss_tot) if ss_tot > 0 else float("nan"),
    }


def random_portfolios(eligible: np.ndarray, n: int, n_random: int, seed: int,
                       n_periods: int):
    """1,000 random portfolios: at each rebalance, n names drawn from that date's
    factor-eligible universe (without replacement), equal weight, no cap."""
    rng = np.random.default_rng(seed)
    R = eligible.shape[0]
    out = []
    for _ in range(n_random):
        sels, wts = [], []
        for r in range(min(R, n_periods)):
            pool = np.flatnonzero(eligible[r])
            if pool.size == 0:
                sels.append(np.array([], dtype=int))
                wts.append(np.array([]))
                continue
            take = min(n, pool.size)
            pick = np.sort(rng.choice(pool, size=take, replace=False))
            sels.append(pick)
            wts.append(equal_weight(pick.size, 1.0))
        out.append((sels, wts))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--windows", choices=["dev", "all"], default="dev",
                    help="dev = development window only; all = development + ONE-SHOT holdout")
    ap.add_argument("--out", default="_results.json")
    ap.add_argument("--sensitivity", action="store_true", help="run the declared N/cost sensitivity grid")
    args = ap.parse_args()

    RESULTS.mkdir(parents=True, exist_ok=True)
    panel = load_panel()
    ff = compute_factors(panel, {})
    R = len(panel.rebal_idx)
    dates = panel.rebal_dates
    print(f"panel: {panel.n_dates} trading days, {len(panel.symbols)} symbols, "
          f"{R} monthly rebalance dates {pd.Timestamp(dates[0]).date()}..{pd.Timestamp(dates[-1]).date()}")

    # close strictly before each rebalance (used for market cap in the VALUE factor)
    last_close_m = np.vstack([panel.close_m[i - 1] for i in panel.rebal_idx]).astype(np.float64)

    fund = load_fundamentals()
    factor_values = dict(ff.values)
    factor_list = list(PRICE_FACTORS)
    if fund is not None:
        q, v = fundamental_factors(fund, panel.symbols, dates, last_close_m, cache=FACTOR_CACHE)
        factor_values["QUALITY"] = q
        factor_values["VALUE"] = v
        factor_list += FUND_FACTORS
        print(f"fundamentals panel loaded: {len(fund)} rows, {fund['symbol'].nunique()} symbols")
    else:
        print("WARNING: no fundamentals panel -> QUALITY/VALUE not tested (data audit reason required)")

    # last-finite-close index map (for the delisting fallback)
    close_idx = np.where(np.isfinite(panel.close_m), np.arange(panel.n_dates)[:, None], -1)
    last_finite_close_idx = np.maximum.accumulate(close_idx, axis=0).astype(np.int32)
    asset_ret = holding_returns(panel.open_m, panel.close_m, last_finite_close_idx, panel.rebal_idx)
    print(f"asset return matrix: {asset_ret.shape}")

    rows_all = list(range(R - 1))
    dev_rows = [r for r in rows_all if dates[r] >= DEV_START and dates[r] < HOLDOUT_START]
    hold_rows = [r for r in rows_all if dates[r] >= HOLDOUT_START]
    print(f"dev periods: {len(dev_rows)} ({pd.Timestamp(dates[dev_rows[0]]).date()}"
          f"..{pd.Timestamp(dates[dev_rows[-1]]).date()})")
    print(f"holdout periods: {len(hold_rows)} ({pd.Timestamp(dates[hold_rows[0]]).date()}"
          f"..{pd.Timestamp(dates[hold_rows[-1]]).date()})")

    elig_sizes = ff.eligible.sum(axis=1)
    print("base eligible universe size: dev avg %.0f min %d max %d | holdout avg %.0f"
          % (elig_sizes[dev_rows].mean(), elig_sizes[dev_rows].min(), elig_sizes[dev_rows].max(),
             elig_sizes[hold_rows].mean()))

    # ---- SPY benchmark ----
    spy_pos = int(np.flatnonzero(panel.symbols == "SPY")[0])
    spy_period = asset_ret[:, spy_pos]

    results = {
        "meta": {
            "panel_min_date": str(pd.Timestamp(panel.dates.min()).date()),
            "panel_max_date": str(pd.Timestamp(panel.dates.max()).date()),
            "panel_symbols": int(len(panel.symbols)),
            "n_rebalance_dates": int(R),
            "dev_periods": len(dev_rows),
            "holdout_periods": len(hold_rows),
            "dev_start": str(pd.Timestamp(dates[dev_rows[0]]).date()),
            "dev_end": str(pd.Timestamp(dates[dev_rows[-1]]).date()),
            "holdout_start": str(pd.Timestamp(dates[hold_rows[0]]).date()),
            "holdout_end": str(pd.Timestamp(dates[hold_rows[-1]]).date()),
            "n_names": N_NAMES,
            "position_cap": POSITION_CAP,
            "cost_bps_per_side": COST_BPS,
            "n_random": N_RANDOM,
            "seed": SEED,
            "base_eligible_avg_dev": float(elig_sizes[dev_rows].mean()),
            "base_eligible_avg_holdout": float(elig_sizes[hold_rows].mean()),
            "windows_run": args.windows,
            "fundamentals_available": fund is not None,
        },
        "factors": {},
    }

    windows = [("dev", dev_rows)]
    if args.windows == "all":
        windows.append(("holdout", hold_rows))

    for fname, direction, desc in factor_list:
        vals = factor_values[fname]
        # factor-specific eligible set: base screens AND a finite factor value
        f_elig = ff.eligible & np.isfinite(vals)
        f_sizes = f_elig.sum(axis=1)
        sels, wts = [], []
        for r in rows_all:
            pick = select_names(vals, f_elig, r, N_NAMES, direction)
            sels.append(pick)
            wts.append(equal_weight(pick.size, POSITION_CAP) if pick.size else np.array([]))
        res = simulate(sels, wts, asset_ret, COST_RATE, label=fname)

        print(f"building {N_RANDOM} random portfolios for {fname} ...")
        rand_sels = random_portfolios(f_elig, N_NAMES, N_RANDOM, SEED, len(rows_all))
        rand_net = np.zeros((N_RANDOM, len(rows_all)))
        rand_gross = np.zeros((N_RANDOM, len(rows_all)))
        rand_turn = np.zeros((N_RANDOM, len(rows_all)))
        for k, (rs, rw) in enumerate(rand_sels):
            r_res = simulate(rs, rw, asset_ret, COST_RATE)
            rand_net[k] = r_res.net
            rand_gross[k] = r_res.gross
            rand_turn[k] = r_res.turnover

        block = {
            "direction": direction,
            "description": desc,
            "factor_eligible_avg_dev": float(f_sizes[dev_rows].mean()),
            "factor_eligible_min_dev": int(f_sizes[dev_rows].min()),
            "factor_eligible_avg_holdout": float(f_sizes[hold_rows].mean()),
        }
        for window, rows in windows:
            s_net = np.array([res.net[r] for r in rows])
            s_gross = np.array([res.gross[r] for r in rows])
            s_turn = np.array([res.turnover[r] for r in rows])
            rn = rand_net[:, rows]
            rg = rand_gross[:, rows]
            spy_w = spy_period[rows]
            strat = metrics(s_net, periods_per_year=12.0, turnover=s_turn)
            strat_gross = metrics(s_gross, periods_per_year=12.0)
            spy = metrics(spy_w, periods_per_year=12.0)
            t_gross = tstat_vs_random(s_gross, rg)
            t_net = tstat_vs_random(s_net, rn)
            rn_ann = np.array([metrics(rn[k], periods_per_year=12.0)["ann_return"] for k in range(rn.shape[0])])
            rn_sharpe = np.array([metrics(rn[k], periods_per_year=12.0)["sharpe"] for k in range(rn.shape[0])])
            rn_dd = np.array([metrics(rn[k], periods_per_year=12.0)["max_drawdown"] for k in range(rn.shape[0])])
            rg_ann = np.array([metrics(rg[k], periods_per_year=12.0)["ann_return"] for k in range(rg.shape[0])])
            rn_turn = np.array([rand_turn[:, rows][k].mean() for k in range(rn.shape[0])])
            rand_betas = np.array([beta_diag(rn[k], spy_w)["beta"] for k in range(rn.shape[0])])
            block[window] = {
                "strategy_net": strat,
                "strategy_gross": strat_gross,
                "spy": spy,
                "tstat_vs_random_gross": t_gross,
                "tstat_vs_random_net": t_net,
                "tstat_vs_spy_paired_net": tstat_paired(s_net, spy_w),
                "tstat_vs_spy_paired_gross": tstat_paired(s_gross, spy_w),
                "beta_diag_vs_spy": beta_diag(s_net, spy_w),
                "random_beta_mean": float(rand_betas.mean()),
                "random_beta_std": float(rand_betas.std(ddof=1)),
                "random_net_ann_return_mean": float(rn_ann.mean()),
                "random_net_ann_return_std": float(rn_ann.std(ddof=1)),
                "random_net_ann_return_p95": float(np.percentile(rn_ann, 95)),
                "random_net_ann_return_max": float(rn_ann.max()),
                "random_gross_ann_return_mean": float(rg_ann.mean()),
                "random_gross_ann_return_p95": float(np.percentile(rg_ann, 95)),
                "random_net_sharpe_mean": float(rn_sharpe.mean()),
                "random_net_sharpe_p95": float(np.percentile(rn_sharpe, 95)),
                "random_net_maxdd_mean": float(rn_dd.mean()),
                "random_net_maxdd_p95": float(np.percentile(rn_dd, 95)),
                "random_mean_period_turnover": float(rn_turn.mean()),
                "n_periods": len(rows),
                "holdings_first": [str(panel.symbols[i]) for i in sels[rows[0]]],
                "holdings_last": [str(panel.symbols[i]) for i in sels[rows[-1]]],
                "n_holdings_first": int(len(sels[rows[0]])),
                "n_holdings_last": int(len(sels[rows[-1]])),
            }
        results["factors"][fname] = block
        d = block["dev"]
        print(f"\n[{fname}] DEV net ann {d['strategy_net']['ann_return']:.2%} "
              f"vol {d['strategy_net']['ann_vol']:.2%} sharpe {d['strategy_net']['sharpe']:.2f} "
              f"maxDD {d['strategy_net']['max_drawdown']:.2%} | SPY {d['spy']['ann_return']:.2%} "
              f"sharpe {d['spy']['sharpe']:.2f} | z_gross {d['tstat_vs_random_gross']['z_unpaired_mean']:.2f} "
              f"(pct {d['tstat_vs_random_gross']['percentile_of_random_means']:.1f}) "
              f"t_paired {d['tstat_vs_random_gross']['t_paired_periods']:.2f} "
              f"beta {d['beta_diag_vs_spy']['beta']:.2f} "
              f"turn {d['strategy_net']['ann_turnover']:.2f} | elig avg {block['factor_eligible_avg_dev']:.0f}")

    results["spy"] = {
        "dev": metrics(spy_period[dev_rows], periods_per_year=12.0),
        "holdout": metrics(spy_period[hold_rows], periods_per_year=12.0),
    }

    out = RESULTS / args.out
    out.write_text(json.dumps(results, indent=2, default=str))
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
