"""STRAT-027 — post-hoc DIAGNOSTICS on the development window.

These are NOT part of the frozen pass bar and they do not select anything. They are run
BEFORE the one-shot holdout, to check that the development result is not an artefact of
(a) stale fundamentals, (b) a handful of names, or (c) a single calendar year.

Diagnostics:
  1. FRESHNESS — the VALUE factor restricted to names whose newest public 10-K was
     filed within 400 days of the rebalance. If the edge survives with fresh
     fundamentals only, it is not an artefact of stale earnings.
  2. CALENDAR-YEAR BREAKDOWN — strategy vs SPY per calendar year.
  3. CONCENTRATION — how much of the total return came from the ten best and ten worst
     individual holdings.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from engine import equal_weight, holding_returns, metrics, simulate, tstat_vs_random
from fundamentals import load_fundamentals
from panel import compute_factors, load_panel
from run_study import (
    COST_RATE,
    N_NAMES,
    N_RANDOM,
    POSITION_CAP,
    SEED,
    select_names,
)

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
FACTOR_CACHE = HERE / "data" / "fundamental_factors.npz"

DEV_START = np.datetime64("2021-01-01")
HOLDOUT_START = np.datetime64("2025-01-01")
FRESH_DAYS = 400


def main() -> int:
    panel = load_panel()
    ff = compute_factors(panel, {})
    fund = load_fundamentals()
    R = len(panel.rebal_idx)
    dates = panel.rebal_dates
    last_close = np.vstack([panel.close_m[i - 1] for i in panel.rebal_idx]).astype(float)

    z = np.load(FACTOR_CACHE)
    value = z["value"]
    quality = z["quality"]

    close_idx = np.where(np.isfinite(panel.close_m), np.arange(panel.n_dates)[:, None], -1)
    lfci = np.maximum.accumulate(close_idx, axis=0).astype(np.int32)
    asset_ret = holding_returns(panel.open_m, panel.close_m, lfci, panel.rebal_idx)

    rows_all = list(range(R - 1))
    dev_rows = [r for r in rows_all if dates[r] >= DEV_START and dates[r] < HOLDOUT_START]
    spy_pos = int(np.flatnonzero(panel.symbols == "SPY")[0])
    spy_period = asset_ret[:, spy_pos]

    out: dict = {"meta": {"fresh_days": FRESH_DAYS, "diagnostics": "development window only"}}

    # ---------- age (in days) of the newest public 10-K, per symbol and rebalance ----------
    # NOTE: the panel/fund datetimes are microsecond resolution; normalise to ns so the
    # arithmetic against pd.Timestamp(...).value (ns) is correct.
    filed_by_sym = {}
    for sym, g in fund.groupby("symbol", sort=False):
        filed_ns = g["filed"].astype("datetime64[ns]").astype("int64").unique()
        filed_by_sym[sym] = np.sort(filed_ns)
    sym_pos = {s: k for k, s in enumerate(panel.symbols)}
    age = np.full((R, len(panel.symbols)), np.inf, dtype=np.float64)
    for sym, filed in filed_by_sym.items():
        k = sym_pos.get(sym)
        if k is None:
            continue
        for r in range(R):
            d = pd.Timestamp(dates[r]).value
            j = np.searchsorted(filed, d, side="right") - 1
            if j >= 0:
                age[r, k] = (d - filed[j]) / 86_400_000_000_000.0  # ns per day

    # ---------- 1. freshness-restricted VALUE ----------
    fresh_elig = ff.eligible & np.isfinite(value) & (age <= FRESH_DAYS)
    print(f"fresh VALUE eligible (filed <= {FRESH_DAYS}d old): dev avg "
          f"{fresh_elig[dev_rows].sum(axis=1).mean():.0f} of "
          f"{(ff.eligible & np.isfinite(value))[dev_rows].sum(axis=1).mean():.0f}")
    sels = [select_names(value, fresh_elig, r, N_NAMES, "higher") for r in rows_all]
    wts = [equal_weight(s.size, POSITION_CAP) if s.size else np.array([]) for s in sels]
    res = simulate(sels, wts, asset_ret, COST_RATE)

    rng = np.random.default_rng(SEED)
    rand_net = np.zeros((N_RANDOM, len(rows_all)))
    rand_gross = np.zeros((N_RANDOM, len(rows_all)))
    for k in range(N_RANDOM):
        rs, rw = [], []
        for r in range(len(rows_all)):
            pool = np.flatnonzero(fresh_elig[r])
            take = min(N_NAMES, pool.size)
            if take == 0:
                rs.append(np.array([], dtype=int))
                rw.append(np.array([]))
                continue
            pick = np.sort(rng.choice(pool, size=take, replace=False))
            rs.append(pick)
            rw.append(equal_weight(pick.size, 1.0))
        rr = simulate(rs, rw, asset_ret, COST_RATE)
        rand_net[k] = rr.net
        rand_gross[k] = rr.gross

    s_net = np.array([res.net[r] for r in dev_rows])
    s_gross = np.array([res.gross[r] for r in dev_rows])
    s_turn = np.array([res.turnover[r] for r in dev_rows])
    out["value_fresh_400d_dev"] = {
        "eligible_avg_dev": float(fresh_elig[dev_rows].sum(axis=1).mean()),
        "strategy_net": metrics(s_net, periods_per_year=12.0, turnover=s_turn),
        "strategy_gross": metrics(s_gross, periods_per_year=12.0),
        "spy": metrics(spy_period[dev_rows], periods_per_year=12.0),
        "tstat_vs_random_gross": tstat_vs_random(s_gross, rand_gross[:, dev_rows]),
        "tstat_vs_random_net": tstat_vs_random(s_net, rand_net[:, dev_rows]),
        "random_net_ann_return_p95": float(np.percentile(
            [metrics(rand_net[k][dev_rows], periods_per_year=12.0)["ann_return"]
             for k in range(N_RANDOM)], 95)),
    }

    # ---------- 2. calendar-year breakdown for VALUE (as reported in the study) ----------
    val_elig = ff.eligible & np.isfinite(value)
    sels = [select_names(value, val_elig, r, N_NAMES, "higher") for r in rows_all]
    wts = [equal_weight(s.size, POSITION_CAP) if s.size else np.array([]) for s in sels]
    res = simulate(sels, wts, asset_ret, COST_RATE)
    per_name: dict[str, float] = {}
    for r in dev_rows:
        ret = asset_ret[r][sels[r]]
        for s, rr in zip(sels[r], ret):
            per_name[str(panel.symbols[s])] = per_name.get(str(panel.symbols[s]), 0.0) + float(rr) / len(dev_rows)

    yearly = []
    for year in sorted({pd.Timestamp(dates[r]).year for r in dev_rows}):
        yr_rows = [r for r in dev_rows if pd.Timestamp(dates[r]).year == year]
        s_y = np.array([res.net[r] for r in yr_rows])
        spy_y = spy_period[yr_rows]
        yearly.append({
            "year": year,
            "months": len(yr_rows),
            "strategy_net_total": float(np.prod(1 + s_y) - 1),
            "spy_total": float(np.prod(1 + spy_y) - 1),
            "strategy_vol_ann": float(np.std(s_y, ddof=1) * np.sqrt(12)),
            "strategy_sharpe": float(metrics(s_y, periods_per_year=12.0)["sharpe"]),
        })
    out["value_yearly_dev"] = yearly

    # ---------- 3. concentration ----------
    contrib = sorted(per_name.items(), key=lambda kv: -kv[1])
    total = float(sum(per_name.values()))
    top10 = sum(v for _, v in contrib[:10])
    bot10 = sum(v for _, v in contrib[-10:])
    out["value_concentration_dev"] = {
        "n_names_held": len(per_name),
        "sum_of_single_name_contributions": total,
        "top10_contribution": top10,
        "bottom10_contribution": bot10,
        "top10_share_of_total": (top10 / total) if total else float("nan"),
        "top10_names": [n for n, _ in contrib[:10]],
        "bottom10_names": [n for n, _ in contrib[-10:]],
        "top10_values": [round(v, 4) for _, v in contrib[:10]],
        "bottom10_values": [round(v, 4) for _, v in contrib[-10:]],
    }

    path = RESULTS / "_diagnostics_dev.json"
    path.write_text(json.dumps(out, indent=2, default=str))
    print(json.dumps(out, indent=2, default=str)[:4000])
    print(f"\nwrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
