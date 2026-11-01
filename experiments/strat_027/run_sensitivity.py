"""STRAT-027 — declared sensitivity grid (portfolio size x cost).

Declared in PREREGISTRATION.md §8 as "reported, never used for selection".
Runs after the main study, in the same one-shot session, and writes its own
results file. It never feeds the pass bar or the headline claim.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from engine import equal_weight, holding_returns, metrics, simulate, tstat_vs_random
from fundamentals import fundamental_factors, load_fundamentals
from panel import compute_factors, load_panel
from run_study import (
    COST_BPS,
    FUND_FACTORS,
    N_NAMES,
    N_RANDOM,
    POSITION_CAP,
    PRICE_FACTORS,
    SEED,
    select_names,
)

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
FACTOR_CACHE = HERE / "data" / "fundamental_factors.npz"

DEV_START = np.datetime64("2021-01-01")
HOLDOUT_START = np.datetime64("2025-01-01")
SENS_N = [10, 30, 50]
SENS_COST_BPS = [5.0, 10.0, 20.0]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="_results_sensitivity.json")
    ap.add_argument("--windows", choices=["dev", "all"], default="all")
    args = ap.parse_args()

    panel = load_panel()
    ff = compute_factors(panel, {})
    R = len(panel.rebal_idx)
    dates = panel.rebal_dates
    last_close_m = np.vstack([panel.close_m[i - 1] for i in panel.rebal_idx]).astype(np.float64)

    factor_values = dict(ff.values)
    factor_list = list(PRICE_FACTORS)
    fund = load_fundamentals()
    if fund is not None:
        q, v = fundamental_factors(fund, panel.symbols, dates, last_close_m, cache=FACTOR_CACHE)
        factor_values["QUALITY"] = q
        factor_values["VALUE"] = v
        factor_list += FUND_FACTORS

    close_idx = np.where(np.isfinite(panel.close_m), np.arange(panel.n_dates)[:, None], -1)
    lfci = np.maximum.accumulate(close_idx, axis=0).astype(np.int32)
    asset_ret = holding_returns(panel.open_m, panel.close_m, lfci, panel.rebal_idx)

    rows_all = list(range(R - 1))
    dev_rows = [r for r in rows_all if dates[r] >= DEV_START and dates[r] < HOLDOUT_START]
    hold_rows = [r for r in rows_all if dates[r] >= HOLDOUT_START]
    windows = [("dev", dev_rows)] + ([("holdout", hold_rows)] if args.windows == "all" else [])

    spy_pos = int(np.flatnonzero(panel.symbols == "SPY")[0])
    spy_period = asset_ret[:, spy_pos]

    out: dict = {"meta": {"n_random": N_RANDOM, "seed": SEED, "windows": args.windows}, "grid": {}}

    for fname, direction, _desc in factor_list:
        vals = factor_values[fname]
        f_elig = ff.eligible & np.isfinite(vals)
        for n in SENS_N:
            # random cloud for this N (same seed, same factor-eligible universe)
            rng = np.random.default_rng(SEED)
            rand_net = np.zeros((N_RANDOM, len(rows_all)))
            rand_gross = np.zeros((N_RANDOM, len(rows_all)))
            for k in range(N_RANDOM):
                sels, wts = [], []
                for r in range(len(rows_all)):
                    pool = np.flatnonzero(f_elig[r])
                    take = min(n, pool.size)
                    if take == 0:
                        sels.append(np.array([], dtype=int))
                        wts.append(np.array([]))
                        continue
                    pick = np.sort(rng.choice(pool, size=take, replace=False))
                    sels.append(pick)
                    wts.append(equal_weight(pick.size, 1.0))
                rr = simulate(sels, wts, asset_ret, COST_BPS / 10_000.0)
                rand_net[k] = rr.net
                rand_gross[k] = rr.gross

            sels, wts = [], []
            for r in range(len(rows_all)):
                pick = select_names(vals, f_elig, r, n, direction)
                sels.append(pick)
                wts.append(equal_weight(pick.size, POSITION_CAP) if pick.size else np.array([]))

            for cost_bps in SENS_COST_BPS:
                res = simulate(sels, wts, asset_ret, cost_bps / 10_000.0)
                key = f"{fname}|N{n}|cost{cost_bps:g}"
                blk = {}
                for window, rows in windows:
                    s_net = np.array([res.net[r] for r in rows])
                    s_gross = np.array([res.gross[r] for r in rows])
                    s_turn = np.array([res.turnover[r] for r in rows])
                    rn = rand_net[:, rows]
                    rg = rand_gross[:, rows]
                    m = metrics(s_net, periods_per_year=12.0, turnover=s_turn)
                    blk[window] = {
                        "strategy_net": m,
                        "spy": metrics(spy_period[rows], periods_per_year=12.0),
                        "tstat_vs_random_gross": tstat_vs_random(s_gross, rg),
                        "tstat_vs_random_net": tstat_vs_random(s_net, rn),
                        "random_net_ann_return_p95": float(np.percentile(
                            [metrics(rn[k], periods_per_year=12.0)["ann_return"] for k in range(rn.shape[0])], 95)),
                    }
                out["grid"][key] = blk
                d = blk["dev"]
                print(f"{key:34s} DEV ann {d['strategy_net']['ann_return']:7.2%} "
                      f"sharpe {d['strategy_net']['sharpe']:5.2f} "
                      f"maxDD {d['strategy_net']['max_drawdown']:7.2%} "
                      f"turn {d['strategy_net']['ann_turnover']:6.2f} "
                      f"z_gross {d['tstat_vs_random_gross']['z_unpaired_mean']:6.2f}")

    path = RESULTS / args.out
    path.write_text(json.dumps(out, indent=2, default=str))
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
