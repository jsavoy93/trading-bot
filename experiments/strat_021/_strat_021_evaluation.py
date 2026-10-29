#!/usr/bin/env python
"""STRAT-021 frozen evaluation (PREREG A/B/C). Seeded, reproducible.

Sleeves: MOM (STRAT-013-R2 Q5 momentum), LVOL (STRAT-015-R2 Q1 low vol),
GP (STRAT-017 frozen Q5 gross profitability). Benchmark: SPY.
No sweeps, no re-optimization. Seed 21029.
"""
import json
import numpy as np
import pandas as pd
import pyarrow.parquet as pq

SEED = 21029
MAIN = "/root/.openclaw/workspace/trading-bot"
OUT = "/root/.openclaw/workspace/trading-bot-s021/experiments/strat_021"
YEARS = list(range(2017, 2026))  # formation years; holding Jul(Y)..Jun(Y+1)
COST_PER_SIDE = 0.002

# ---------------- Load monthly research ----------------
mr = pd.read_csv(f"{MAIN}/research_data/alpaca_daily_2016_2026/transfer/monthly_research.csv.gz",
                 keep_default_na=False,
                 usecols=["symbol", "year_month", "month_last_adjusted_close",
                          "month_median_daily_dollar_volume"])
mr["month_last_adjusted_close"] = pd.to_numeric(mr["month_last_adjusted_close"], errors="coerce")
mr["month_median_daily_dollar_volume"] = pd.to_numeric(mr["month_median_daily_dollar_volume"], errors="coerce")

# wide price and dollar-volume matrices
px = mr.pivot(index="symbol", columns="year_month", values="month_last_adjusted_close")
rets = px.pct_change(axis=1)  # monthly simple returns; first col NaN
dv = mr.pivot(index="symbol", columns="year_month", values="month_median_daily_dollar_volume")

mom = pd.read_csv(f"{MAIN}/experiments/strat_019/_strat_013_momentum_ticker_level.csv.gz")
vol = pd.read_csv(f"{MAIN}/experiments/strat_019/_strat_015_volatility_ticker_level.csv.gz")

def months(y):
    return [f"{y}-{m:02d}" if m >= 7 else f"{y+1}-{m:02d}" for m in [7,8,9,10,11,12,1,2,3,4,5,6]]

def liquidity_floor(tickers, y):
    """Exclude bottom decile by June(Y) median daily dollar volume within set."""
    col = f"{y}-06"
    s = dv.loc[dv.index.intersection(tickers), col].dropna()
    if len(s) < 10:
        return list(s.index)
    thr = np.percentile(s.values, 10)
    keep = s[s > thr].index  # strictly above 10th percentile => exclude bottom decile
    return list(keep)

def qcut_q(df, col, q, labels_desc=False):
    """Assign quintiles 1(low)..5(high) on col; return df with 'quintile'."""
    d = df.dropna(subset=[col]).copy()
    d["quintile"] = pd.qcut(d[col].rank(method="first"), 5, labels=[1,2,3,4,5])
    return d

selections = {}   # sleeve -> year -> list of tickers
elig_counts = {}

for y in YEARS:
    # MOM
    m = mom[mom.formation_year == y].dropna(subset=["momentum_12_1"])
    tick = [t for t in m.ticker if t in px.index]
    m = m[m.ticker.isin(tick)]
    elig = liquidity_floor(tick, y)
    me = m[m.ticker.isin(elig)]
    me = qcut_q(me, "momentum_12_1", 5)
    selections.setdefault("MOM", {})[y] = sorted(me[me.quintile == 5].ticker.tolist())
    elig_counts.setdefault("MOM", {})[y] = {"factor_valid": len(m), "post_liq": len(me)}

    # LVOL
    v = vol[(vol.formation_year == y) & (vol.n_valid_months >= 9)].dropna(subset=["avg_vol_12mo"])
    v = v[v.ticker.isin(px.index)]
    eligv = liquidity_floor(list(v.ticker), y)
    ve = v[v.ticker.isin(eligv)]
    ve = qcut_q(ve, "avg_vol_12mo", 5)
    selections.setdefault("LVOL", {})[y] = sorted(ve[ve.quintile == 1].ticker.tolist())
    elig_counts.setdefault("LVOL", {})[y] = {"factor_valid": len(v), "post_liq": len(ve)}

    # GP (STRAT-017 frozen: XNYS-only eligible, valid GP, quintiles on GP_A)
    co = pd.read_csv(f"{MAIN}/experiments/strat_017/_cohort_{y}.csv.gz")
    co = co[co.primary_exchange == "XNYS"]
    co = co.dropna(subset=["GP", "GP_A"])
    co = co[co.ticker.isin(px.index)]
    co = qcut_q(co, "GP_A", 5)
    selections.setdefault("GP", {})[y] = sorted(co[co.quintile == 5].ticker.tolist())
    elig_counts.setdefault("GP", {})[y] = {"xnys_valid_gp": len(co)}

# ---------------- Sleeve monthly returns with renormalization ----------------
def sleeve_series(sleeve):
    rows = []
    prev_members = None
    for y in YEARS:
        members = selections[sleeve][y]
        # turnover
        if prev_members is None:
            frac = 1.0
        else:
            old = set(prev_members); new = set(members)
            w = 1.0/len(old); wn = 1.0/len(new)
            frac = 0.5*(w*len(old-new) + wn*len(new-old))
        cost = COST_PER_SIDE * frac
        for i, mo in enumerate(months(y)):
            sub = rets.loc[rets.index.intersection(members), mo].dropna()
            r = float(sub.mean()) if len(sub) else np.nan
            if i == 0 and not np.isnan(r):
                r -= cost
            rows.append({"year_month": mo, "gross": float(rets.loc[rets.index.intersection(members), mo].dropna().mean()) if len(sub) else np.nan,
                         "net": r, "n_available": int(len(sub)), "n_members": len(members),
                         "formation_year": y, "one_way_turnover": frac, "cost_drag": cost})
        prev_members = members
    return pd.DataFrame(rows).set_index("year_month")

series = {s: sleeve_series(s) for s in ["MOM", "LVOL", "GP"]}

# ---------------- SPY from daily parquet ----------------
t = pq.read_table(f"{MAIN}/research_data/alpaca_daily_2016_2026/transfer/daily_bars_part_005.parquet",
                  columns=["symbol", "timestamp", "close"])
d = t.to_pandas()
d = d[d.symbol == "SPY"].copy()
d["ym"] = d.timestamp.dt.strftime("%Y-%m")
spy_close = d.groupby("ym").close.last()
spy_ret = spy_close.pct_change()

all_months = [mo for y in YEARS for mo in months(y)]  # 2017-07 .. 2026-06 (108 months)
spy = spy_ret.reindex(all_months)

# ---------------- Metrics ----------------
def metrics(r, name):
    r = r.dropna()
    n = len(r)
    cum = float((1 + r).prod() - 1)
    ann = float((1 + r).prod() ** (12 / n) - 1)
    vol = float(r.std(ddof=1) * np.sqrt(12))
    sharpe = ann / vol if vol > 0 else np.nan
    wealth = (1 + r).cumprod()
    dd = float((wealth / wealth.cummax() - 1).min())
    return {"series": name, "n_months": n, "cumulative_net_return": cum,
            "annualized_net_return": ann, "annualized_vol": vol,
            "sharpe_rf0": sharpe, "max_drawdown": dd,
            "mean_monthly_net_return": float(r.mean())}

def block_bootstrap_ci(x, n_boot=10000, block=6, seed=SEED):
    rng = np.random.default_rng(seed)
    x = np.asarray(x, dtype=float)
    x = x[~np.isnan(x)]
    n = len(x)
    p = 1.0 / block
    means = np.empty(n_boot)
    for b in range(n_boot):
        idx = np.empty(n, dtype=int)
        idx[0] = rng.integers(n)
        for i in range(1, n):
            if rng.random() < p:
                idx[i] = rng.integers(n)
            else:
                idx[i] = (idx[i-1] + 1) % n
        means[b] = x[idx].mean()
    lo, hi = np.percentile(means, [2.5, 97.5])
    return float(lo), float(hi)

net = pd.DataFrame({s: series[s]["net"] for s in series})
net["SPY"] = spy

mets = {s: metrics(net[s], s) for s in ["MOM", "LVOL", "GP", "SPY"]}
for s in ["MOM", "LVOL", "GP"]:
    lo, hi = block_bootstrap_ci(net[s].values)
    mets[s]["mean_monthly_bootstrap_ci95"] = [lo, hi]
    spread = (net[s] - net["SPY"]).dropna().values
    lo2, hi2 = block_bootstrap_ci(spread)
    mets[s]["spread_vs_spy_mean_monthly"] = float(spread.mean())
    mets[s]["spread_vs_spy_bootstrap_ci95"] = [lo2, hi2]
    mets[s]["turnover_mean"] = float(series[s]["one_way_turnover"].mean())
    mets[s]["turnover_per_formation"] = {str(y): float(series[s].loc[series[s].formation_year == y, "one_way_turnover"].iloc[0]) for y in YEARS}
    mets[s]["member_counts"] = {str(y): int(series[s].loc[series[s].formation_year == y, "n_members"].iloc[0]) for y in YEARS}

# correlations
corr = net.corr(method="pearson")

# pass/fail per PREREG C section 5
spy_dd = mets["SPY"]["max_drawdown"]
verdicts = {}
for s in ["MOM", "LVOL", "GP"]:
    sp = mets[s]["spread_vs_spy_mean_monthly"]
    ci_lo = mets[s]["spread_vs_spy_bootstrap_ci95"][0]
    dd = mets[s]["max_drawdown"]
    c1 = ci_lo > -0.001
    c2 = sp > 0
    c3 = abs(dd) <= abs(spy_dd) * 1.25  # compare drawdown MAGNITUDES (maxDD values are negative)
    verdicts[s] = {
        "c1_ci_lower_gt_-0.10pct": {"value_ci_lower": ci_lo, "pass": bool(c1)},
        "c2_positive_spread_point": {"value": sp, "pass": bool(c2)},
        "c3_maxdd_le_1.25x_spy": {"sleeve_dd": dd, "spy_dd_x1.25": spy_dd*1.25, "pass": bool(c3)},
        "overall": "PASS" if (c1 and c2 and c3) else "FAIL",
    }

# ---------------- Write outputs ----------------
results = {
    "task": "STRAT-021 frozen evaluation",
    "seed": SEED,
    "cost_model": "20bps per side on one-way formation turnover, charged in July",
    "months": all_months,
    "metrics": mets,
    "pairwise_correlations_monthly_net": corr.round(4).to_dict(),
    "verdicts": verdicts,
    "eligibility_counts": elig_counts,
    "selections_member_lists": {s: {str(y): selections[s][y] for y in YEARS} for s in selections},
    "notes": [
        "GP_A in STRAT-017 cohort files is a continuous GP/Assets ratio, NOT a quintile label; quintiles recomputed on GP_A over XNYS-only valid-GP eligible sets per frozen STRAT-017 rule; Q5 = highest GP_A.",
        "Daily parquet part_005 has no adjusted-close column (open/high/low/close/volume/trade_count/vwap only); SPY benchmark computed from last raw close per month (dividends not reinvested).",
        "Liquidity floor: excluded tickers with June(Y) median daily dollar volume <= 10th percentile of the factor-eligible set (strict >).",
        "Quintiles via rank(method='first') qcut for deterministic tie handling.",
        "Missing member months excluded and weights renormalized among available members (no price forward-fill).",
        "monthly_research read with keep_default_na=False per repo CSV consumer contract (literal 'NA' ticker).",
    ],
}
with open(f"{OUT}/_strat_021_results.json", "w") as f:
    json.dump(results, f, indent=2, default=float)

net.round(6).to_csv(f"{OUT}/_strat_021_monthly_returns.csv", index_label="year_month")

for s in ["MOM", "LVOL", "GP", "SPY"]:
    m = mets[s]
    print(f"{s}: ann_net={m['annualized_net_return']:.4f} vol={m['annualized_vol']:.4f} "
          f"sharpe={m['sharpe_rf0']:.3f} maxDD={m['max_drawdown']:.4f} cum={m['cumulative_net_return']:.4f}")
for s in ["MOM", "LVOL", "GP"]:
    m = mets[s]
    print(f"{s} spread mean={m['spread_vs_spy_mean_monthly']:.5f} CI=({m['spread_vs_spy_bootstrap_ci95'][0]:.5f},{m['spread_vs_spy_bootstrap_ci95'][1]:.5f}) "
          f"turnover_mean={m['turnover_mean']:.3f} members={m['member_counts']}")
    print(f"{s} verdict: {verdicts[s]['overall']}")
print(corr.round(3))
