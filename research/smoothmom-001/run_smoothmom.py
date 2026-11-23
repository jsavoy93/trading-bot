#!/usr/bin/env python3
"""SMOOTHMOM-001 step 1 -- smooth vs spiky momentum on MOM 12-1.

Implements the FROZEN preregistration (research/smoothmom-001/PREREGISTRATION.md):
  * baseline : MOM 12-1 long-short, equal-weight decile legs (frozen engine)
  * V1       : double sort -- among top 20% MOM, long the smoother half; among
               bottom 20% MOM, short the smoother half
  * V2       : combined score = mean(MOM pct rank, (-ID) pct rank); long top decile,
               short bottom decile

Smoothness ID = sign(cum 12-1 return) x (% neg days - % pos days) over trading days
t-252..t-21, lower = smoother.

DEV window only (months 2021-01 .. 2024-12). Holdout rows are NEVER loaded: the panel
is read through a parquet row filter and asserted before anything else.

Research only. Writes results.json + results.md next to this file (never committed).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import engine_fixed as eng  # noqa: E402  (vendored frozen engine, unchanged)

# ---------------------------------------------------------------- constants
# Prereg section 6: t is the t-statistic of the FROZEN benchmark's monthly EXCESS
# return (strategy net minus the per-month mean of the frozen random-decile draws,
# 1000 draws, seed eng.SEED -- exactly research/volsize-001/run_volsize.py).
DEV_START = pd.Timestamp("2021-01-01")
DEV_END = pd.Timestamp("2024-12-31")
LOAD_FLOOR = pd.Timestamp("2018-01-01")   # earliest date any DEV window reads

N_RANDOM_DEFAULT = 1000
SMOKE_DRAWS = 20
SMOKE_MONTHS = 3

BONFERRONI_P = 98.75       # k=2: 100 - 2.5/2
BAR_T = 2.24
MIN_PASS_MONTHS = 24

# ID window (prereg section 4): trading days t-252 .. t-21 inclusive
ID_START_BACK = 252
ID_END_BACK = 21
ID_MIN_DAYS = 200

# V1 double sort: quintile (20%) pools, smoother half of each
POOL_HALF = 0.50

# Frozen DEV reference for the baseline (prereg section 6): 1.708%/mo, t 2.294,
# maxDD -27.7%. Tight tolerances, loud failure. The mean tolerance is 5e-5 because
# the frozen constant itself is rounded (0.017082; the exact value is
# 0.01708229936...) and the prereg publishes the reference to 3 decimals.
REF_MEAN_NET = 0.017082
REF_T_EXCESS = 2.294
REF_MAXDD = -0.277
REF_MEAN_TOL = 5e-5
REF_T_TOL = 5e-3
REF_MAXDD_TOL = 5e-3

SEED_V1 = 20261123
SEED_V2 = 20261124

DEFAULT_PANEL = Path("/tmp/panel_dev.parquet")


# ================================================================ panel (DEV only)
def _read_dev_frame(path: Path) -> pd.DataFrame:
    """Read ONLY DEV-era rows via a pyarrow row filter; assert the window after."""
    if not path.exists():
        raise SystemExit(f"panel not found: {path}")
    df = pq.read_table(
        path,
        columns=["symbol", "date", "open", "close", "volume"],
        filters=[("date", ">=", LOAD_FLOOR.to_pydatetime()),
                 ("date", "<=", DEV_END.to_pydatetime())],
    ).to_pandas()
    if df.empty:
        raise SystemExit(f"panel read produced no DEV rows: {path}")
    df["date"] = pd.to_datetime(df["date"])
    assert df["date"].min() >= LOAD_FLOOR, "loaded row before load floor"
    assert df["date"].max() <= DEV_END, "loaded row after DEV_END (holdout leak)"
    return df


def load_panel(path: Path) -> eng.Panel:
    """Rebuild the frozen engine Panel from DEV-only rows (same construction as
    engine_fixed.load_panel, but filtered at read time)."""
    df = _read_dev_frame(path)
    df = df.sort_values(["symbol", "date"], kind="mergesort").reset_index(drop=True)
    dates = np.unique(df["date"].values.astype("datetime64[ns]"))
    symbols = np.sort(df["symbol"].unique())
    di = np.searchsorted(dates, df["date"].values.astype("datetime64[ns]"))
    si = np.searchsorted(symbols.astype(str), df["symbol"].astype(str))
    n_t, n_s = len(dates), len(symbols)

    def build(col: str) -> np.ndarray:
        m = np.full((n_t, n_s), np.nan, dtype=np.float64)
        m[di, si] = df[col].to_numpy(dtype=np.float64)
        return m

    return eng.Panel(dates, symbols, build("open"), build("close"), build("volume"))


def dev_months(panel: eng.Panel) -> list[eng.MonthRow]:
    """Months with entry open >= 2021-01-01 and exit open <= 2024-12-31."""
    out = []
    for m in eng.build_months(panel):
        if m.entry_date >= DEV_START and m.exit_date <= DEV_END:
            out.append(m)
    if not out:
        raise SystemExit("no DEV months available")
    for m in out:
        assert m.decision_date <= DEV_END and m.exit_date <= DEV_END, "month past DEV_END"
    return out


# ================================================================ smoothness (ID)
def daily_ret_matrix(panel: eng.Panel) -> np.ndarray:
    """Close-to-close daily returns, (T, S); index 0 is NaN."""
    close = panel.close_m
    n_s = close.shape[1]
    with np.errstate(divide="ignore", invalid="ignore"):
        prev = np.vstack([np.full((1, n_s), np.nan), close[:-1]])
        dr = close / prev - 1.0
    dr[~np.isfinite(dr)] = np.nan
    return dr


def smooth_scores(panel: eng.Panel, md_list: list, months: list,
                  days_x_dates) -> list[np.ndarray]:
    """Per-month ID vector (S,), NaN where undefined (prereg section 4).

    Window = trading days [decision_idx-251 .. decision_idx-20] (252 days, t-252..t-21).
    Cumulative 12-1 return = close(last day of window) / close(day before window) - 1,
    i.e. first-to-last close of the same 252 days; daily returns use the close one day
    before the window start for the first day. Only data up to the decision date.
    """
    dr = days_x_dates[0]
    dates = days_x_dates[1]
    date_pos = pd.DatetimeIndex(dates).get_indexer(
        pd.DatetimeIndex([m.decision_date for m in months]))
    close = panel.close_m
    n_s = close.shape[1]
    out = []
    for r, p in enumerate(date_pos):
        assert p >= 0, "decision date not in panel"
        a = p - ID_START_BACK + 1          # t-252
        b = p - ID_END_BACK                # t-21 (inclusive)
        assert 0 <= a <= b <= p, "ID window out of range"
        idv = np.full(n_s, np.nan)
        win = dr[a:b + 1, :]
        fin = np.isfinite(win)
        n_obs = fin.sum(axis=0)
        neg = (win < 0).sum(axis=0)
        pos = (win > 0).sum(axis=0)
        with np.errstate(invalid="ignore", divide="ignore"):
            idv = (neg / n_obs) - (pos / n_obs)
        p0 = close[a - 1, :]
        p1 = close[b, :]
        ok = np.isfinite(p0) & np.isfinite(p1) & (p0 > 0) & (p1 > 0)
        cum = np.where(ok, p1 / p0 - 1.0, np.nan)
        sign = np.where(np.isfinite(cum),
                        np.where(cum > 0, 1.0, np.where(cum < 0, -1.0, np.nan)),
                        np.nan)
        idv = idv * sign
        bad = (n_obs < ID_MIN_DAYS) | ~np.isfinite(idv) | ~np.isfinite(md_list[r].mom)
        idv[bad] = np.nan
        out.append(idv)
    return out


# ================================================================ selection
def mom_pool(md: eng.MonthData) -> np.ndarray:
    """Eligible-for-MOM name indices (finite mom and ret), ascending."""
    return np.flatnonzero(np.isfinite(md.mom) & np.isfinite(md.ret))


def _leg_size(n: int) -> int:
    return int(np.floor(eng.DECILE * n))


def v1_legs(md: eng.MonthData, idv: np.ndarray):
    """Double sort. Returns (long_idx, short_idx, long_pool, short_pool)."""
    idx = mom_pool(md)
    n_leg = _leg_size(len(idx))
    empty = np.array([], dtype=int)
    if len(idx) < eng.MIN_ELIGIBLE or n_leg < 1:
        return empty, empty, empty, empty
    # frozen tie-break: stable sort keeps ascending symbol order
    asc = idx[np.argsort(md.mom[idx], kind="stable")]
    desc = idx[np.argsort(-md.mom[idx], kind="stable")]
    n_pool = max(n_leg * 2, 1)
    top = desc[:n_pool]
    bot = asc[:n_pool]
    n_half = int(np.floor(POOL_HALF * n_pool))
    if n_half < 1:
        return empty, empty, top, bot

    def smoother_half(pool: np.ndarray) -> np.ndarray:
        # lowest-ID half of the pool; NaN-ID names rank last (stable, ascending symbol)
        order = np.argsort(np.where(np.isfinite(idv[pool]), idv[pool], np.inf),
                           kind="stable")
        return np.sort(pool[order][:n_half])

    return smoother_half(top), smoother_half(bot), top, bot


def pct_rank(x: np.ndarray) -> np.ndarray:
    """Percentile rank in [0, 1]: (# strictly less + 0.5 * # equal) / n."""
    x = np.asarray(x, float)
    n = len(x)
    if n <= 1:
        return np.full(n, 0.5)
    order = np.argsort(x, kind="stable")
    xs = x[order]
    out_sorted = np.empty(n)
    i = 0
    while i < n:
        j = i
        while j + 1 < n and xs[j + 1] == xs[i]:
            j += 1
        out_sorted[i:j + 1] = ((i + j) / 2.0) / (n - 1)
        i = j + 1
    out = np.empty(n)
    out[order] = out_sorted
    return out


def v2_legs(md: eng.MonthData, idv: np.ndarray):
    """Combined score. Returns (long_idx, short_idx, top_pool, bot_pool)."""
    idx = mom_pool(md)
    n_leg = _leg_size(len(idx))
    empty = np.array([], dtype=int)
    cand = idx[np.isfinite(idv[idx])]
    asc = idx[np.argsort(md.mom[idx], kind="stable")]
    n_pool = max(n_leg * 2, 1)
    top_pool, bot_pool = asc[-n_pool:], asc[:n_pool]
    if len(idx) < eng.MIN_ELIGIBLE or n_leg < 1 or len(cand) < 2 * n_leg:
        return empty, empty, np.sort(top_pool), np.sort(bot_pool)
    mom_r = pct_rank(md.mom[cand])
    id_r = pct_rank(-idv[cand])          # (-ID): smooth winners high, smooth losers low
    score = 0.5 * (mom_r + id_r)
    order = np.argsort(score, kind="stable")   # ascending by symbol-index order
    long_idx = np.sort(cand[order[-n_leg:]])
    short_idx = np.sort(cand[order[:n_leg]])
    return long_idx, short_idx, np.sort(top_pool), np.sort(bot_pool)


# ================================================================ metrics
def sharpe(net: np.ndarray) -> float:
    net = np.asarray(net, float)
    sd = net.std(ddof=1) if len(net) > 1 else float("nan")
    if not np.isfinite(sd) or sd == 0:
        return float("nan")
    return float(net.mean() / sd * np.sqrt(12.0))


def worst_k(net: np.ndarray, k: int = 3) -> list[float]:
    return sorted(float(x) for x in np.asarray(net, float))[:k]


def summary(net, excess=None) -> dict:
    net = np.asarray(net, float)
    out = {
        "n_months": int(len(net)),
        "mean_net": float(net.mean()),
        "ann_net": eng.annualize_ret(float(net.mean())),
        "max_drawdown": eng.max_drawdown(net),
        "sharpe": sharpe(net),
        "worst3": worst_k(net, 3),
        "win_rate": float(np.mean(net > 0)),
    }
    if excess is not None:
        out["t_excess"] = eng.t_stat(np.asarray(excess, float))
    return out


def random_monthly_nets(md_list, n_draws, seed=eng.SEED) -> np.ndarray:
    """Frozen random long/short decile portfolios -- verbatim copy of
    research/volsize-001/run_volsize.py `random_monthly_nets` (the prereg's frozen
    benchmark). Returns (n_draws, n_months) monthly NET returns."""
    n_months = len(md_list)
    out = np.zeros((n_draws, n_months))
    rng = np.random.default_rng(seed)
    for d in range(n_draws):
        prev_long = np.array([], dtype=int)
        prev_short = np.array([], dtype=int)
        for r in range(n_months):
            md = md_list[r]
            elig = np.flatnonzero(np.isfinite(md.mom) & np.isfinite(md.ret))
            n_leg = int(np.floor(eng.DECILE * len(elig)))
            if len(elig) < eng.MIN_ELIGIBLE or n_leg < 1:
                out[d, r] = 0.0
                prev_long = np.array([], dtype=int)
                prev_short = np.array([], dtype=int)
                continue
            long_sel = np.sort(rng.choice(elig, size=n_leg, replace=False))
            rest = np.setdiff1d(elig, long_sel, assume_unique=True)
            short_sel = np.sort(rng.choice(rest, size=n_leg, replace=False))
            ret = md.ret
            alive = md.last_close_idx == md.exit_idx
            w = 1.0 / n_leg

            l_gross = float(np.mean(ret[long_sel]))
            l_dead = int((~alive[long_sel]).sum())
            l_open = len(long_sel) - int(np.isin(long_sel, prev_long).sum())
            l_close = len(prev_long) - int(np.isin(prev_long, long_sel).sum())
            l_cost = eng.COST_PER_SIDE * (l_open + l_close + l_dead) * w

            s_gross = float(np.mean(ret[short_sel]))
            s_borrow = eng.BORROW_PER_YEAR * float(np.mean(md.held_days[short_sel])) / eng.TRADING_DAYS_PER_YEAR
            s_dead = int((~alive[short_sel]).sum())
            s_open = len(short_sel) - int(np.isin(short_sel, prev_short).sum())
            s_close = len(prev_short) - int(np.isin(prev_short, short_sel).sum())
            s_cost = eng.COST_PER_SIDE * (s_open + s_close + s_dead) * w

            out[d, r] = (l_gross - l_cost) + (-s_gross - s_borrow - s_cost)

            prev_long = long_sel[alive[long_sel]]
            prev_short = short_sel[alive[short_sel]]
    return out


def turnover(legs: list[np.ndarray]) -> float:
    """Mean one-way name turnover per month across months (rebuild cost driver)."""
    vals = []
    prev = set()
    for leg in legs:
        cur = set(int(s) for s in leg)
        if prev or cur:
            vals.append((len(cur - prev) + len(prev - cur)) / max(len(prev) + len(cur), 1) / 2.0)
        prev = cur
    return float(np.mean(vals)) if vals else float("nan")




# ================================================================ random draws
def random_leg_nets(pre: dict, n_draws: int, seed: int) -> np.ndarray:
    """`n_draws` random same-size legs drawn from each month's candidate pool.

    V1 pools = top/bottom MOM quintile (random half); V2 pools = top/bottom decile of
    the MOM ranking. Draws are vectorized: per month each draw picks `n` distinct
    positions per pool via argpartition on a uniform matrix, then scores with the
    precomputed per-name returns. Costs / borrow follow the frozen rules (0.15%/side
    on open and full close, dead names dropped from the carried book, borrow prorated
    by trading days held).
    """
    rng = np.random.default_rng(seed)
    m = len(pre["long_n"])
    netmat = np.zeros((n_draws, m))
    prev_long = [np.zeros(0, int)] * n_draws
    prev_short = [np.zeros(0, int)] * n_draws
    for t in range(m):
        nl, ns = pre["long_n"][t], pre["short_n"][t]
        PL, PS = pre["long_pool"][t], pre["short_pool"][t]
        rl, rs = pre["long_pool_ret"][t], pre["short_pool_ret"][t]
        dl, ds = pre["long_pool_dead"][t], pre["short_pool_dead"][t]
        hl, hs = pre["long_pool_held"][t], pre["short_pool_held"][t]
        if nl == 0 or ns == 0 or len(PL) < nl or len(PS) < ns:
            prev_long = [np.zeros(0, int)] * n_draws
            prev_short = [np.zeros(0, int)] * n_draws
            continue

        U_l = rng.random((n_draws, len(PL)))
        U_s = rng.random((n_draws, len(PS)))
        pos_l = np.argpartition(U_l, nl - 1, axis=1)[:, :nl]
        pos_s = np.argpartition(U_s, ns - 1, axis=1)[:, :ns]
        names_l, names_s = PL[pos_l], PS[pos_s]
        wl, ws = 1.0 / nl, 1.0 / ns
        gross_l = rl[pos_l].mean(axis=1)
        gross_s = rs[pos_s].mean(axis=1)
        dead_l = dl[pos_l].sum(axis=1)
        dead_s = ds[pos_s].sum(axis=1)
        cost_l = np.empty(n_draws)
        cost_s = np.empty(n_draws)
        borrow_s = eng.BORROW_PER_YEAR * ws * hs[pos_s].mean(axis=1)
        new_prev_l, new_prev_s = [], []
        for d in range(n_draws):
            cur_l, cur_s = names_l[d], names_s[d]
            pv_l, pv_s = prev_long[d], prev_short[d]
            opened_l = nl - int(np.isin(cur_l, pv_l).sum())
            closed_l = len(pv_l) - int(np.isin(pv_l, cur_l).sum())
            cost_l[d] = eng.COST_PER_SIDE * (opened_l + closed_l + int(dead_l[d])) * wl
            opened_s = ns - int(np.isin(cur_s, pv_s).sum())
            closed_s = len(pv_s) - int(np.isin(pv_s, cur_s).sum())
            cost_s[d] = eng.COST_PER_SIDE * (opened_s + closed_s + int(dead_s[d])) * ws
            new_prev_l.append(cur_l[~dl[pos_l[d]]])
            new_prev_s.append(cur_s[~ds[pos_s[d]]])
        prev_long, prev_short = new_prev_l, new_prev_s
        netmat[:, t] += (gross_l - cost_l) + (-gross_s - borrow_s - cost_s)
    return netmat


def make_pool_arrays(md_list, legs, pools_long, pools_short):
    """Per-month candidate pools with returns/held days captured once."""
    out = {"long_pool": [], "short_pool": [], "long_pool_ret": [], "short_pool_ret": [],
           "long_pool_dead": [], "short_pool_dead": [],
           "long_pool_held": [], "short_pool_held": [],
           "long_n": [], "short_n": []}
    for md, L, S, PL, PS in zip(md_list, legs[0], legs[1], pools_long, pools_short):
        alive = md.last_close_idx == md.exit_idx
        PL = np.asarray(PL, int)
        PS = np.asarray(PS, int)
        out["long_pool"].append(PL)
        out["short_pool"].append(PS)
        out["long_pool_ret"].append(np.asarray(md.ret[PL], float))
        out["short_pool_ret"].append(np.asarray(md.ret[PS], float))
        out["long_pool_dead"].append(~alive[PL])
        out["short_pool_dead"].append(~alive[PS])
        out["long_pool_held"].append(np.asarray(md.held_days[PL], float))
        out["short_pool_held"].append(np.asarray(md.held_days[PS], float))
        out["long_n"].append(len(L))
        out["short_n"].append(len(S))
    return out


# ================================================================ main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="SMOOTHMOM-001 smooth vs spiky momentum (DEV research)")
    ap.add_argument("--n-random", type=int, default=N_RANDOM_DEFAULT,
                    help=f"random draws per variant (default {N_RANDOM_DEFAULT})")
    ap.add_argument("--smoke", action="store_true",
                    help=f"smoke mode: {SMOKE_MONTHS} DEV months, {SMOKE_DRAWS} draws "
                         "unless --n-random overrides")
    ap.add_argument("--panel", default=str(DEFAULT_PANEL), help="DEV parquet panel")
    ap.add_argument("--out-dir", default=None, help="output dir (default: this folder)")
    args = ap.parse_args(argv)

    t0 = time.time()
    smoke = args.smoke
    n_draws = args.n_random
    if smoke and not any(a.startswith("--n-random") for a in (argv if argv is not None
                                                              else sys.argv[1:])):
        n_draws = min(n_draws, SMOKE_DRAWS)

    panel = load_panel(Path(args.panel))
    months = dev_months(panel)
    assert max(panel.dates) <= np.datetime64(DEV_END, "ns"), "panel past DEV_END"
    if smoke:
        months = months[:SMOKE_MONTHS]
        print("SMOKE MODE: mechanics proof only -- baseline reference and pass bars are "
              "not meaningful here")

    md_list = eng.compute_month_data(panel, months)
    dr = daily_ret_matrix(panel)
    ids = smooth_scores(panel, md_list, months, (dr, panel.dates))

    # ---- baseline (frozen engine deciles) ----
    longs, shorts = [], []
    for md in md_list:
        li, si, _ = eng.select_legs(md, "MOM")
        longs.append(li)
        shorts.append(si)
    lr = eng.simulate_leg(md_list, longs, "long")
    sr = eng.simulate_leg(md_list, shorts, "short")
    base_net = eng.net_series(lr, sr)
    base_rand = random_monthly_nets(md_list, n_draws)
    base_excess = base_net - base_rand.mean(axis=0)

    # ---- baseline reference check (prereg section 6) ----
    if not smoke:
        base_md = eng.max_drawdown(base_net)
        ref_t = eng.t_stat(base_excess)
        mdiff = abs(float(base_net.mean()) - REF_MEAN_NET)
        print(f"baseline mean_net check: |{base_net.mean():.6f} - {REF_MEAN_NET}| = {mdiff:.2e}")
        ok = (mdiff <= REF_MEAN_TOL
              and abs(ref_t - REF_T_EXCESS) <= REF_T_TOL
              and abs(base_md - REF_MAXDD) <= REF_MAXDD_TOL)
        if not ok:
            msg = (f"BASELINE DOES NOT REPRODUCE: mean_net {base_net.mean():.6f} "
                   f"(ref {REF_MEAN_NET}, tol {REF_MEAN_TOL}), t {ref_t:.4f} "
                   f"(ref {REF_T_EXCESS}), maxDD {base_md:.4f} (ref {REF_MAXDD}). "
                   "Blocker -- stop, do not interpret variant results.")
            raise AssertionError(msg)

    # ---- variants ----
    v1_long, v1_short, v1_pl, v1_ps = [], [], [], []
    v2_long, v2_short, v2_pl, v2_ps = [], [], [], []
    for md, idv in zip(md_list, ids):
        L, S, PL, PS = v1_legs(md, idv)
        v1_long.append(L); v1_short.append(S); v1_pl.append(PL); v1_ps.append(PS)
        L, S, PL, PS = v2_legs(md, idv)
        v2_long.append(L); v2_short.append(S); v2_pl.append(PL); v2_ps.append(PS)

    v1_net = eng.net_series(eng.simulate_leg(md_list, v1_long, "v1l"),
                            eng.simulate_leg(md_list, v1_short, "v1s"))
    v2_net = eng.net_series(eng.simulate_leg(md_list, v2_long, "v2l"),
                            eng.simulate_leg(md_list, v2_short, "v2s"))

    pre1 = make_pool_arrays(md_list, (v1_long, v1_short), v1_pl, v1_ps)
    pre2 = make_pool_arrays(md_list, (v2_long, v2_short), v2_pl, v2_ps)
    rand1 = random_leg_nets(pre1, n_draws, SEED_V1)
    rand2 = random_leg_nets(pre2, n_draws, SEED_V2)
    rand1_pm = rand1.mean(axis=0)
    rand2_pm = rand2.mean(axis=0)

    p1 = eng.percentile_vs_random(float(v1_net.mean()), rand1.mean(axis=1))
    p2 = eng.percentile_vs_random(float(v2_net.mean()), rand2.mean(axis=1))
    p_base = eng.percentile_vs_random(float(base_net.mean()), base_rand.mean(axis=1))

    exc1 = v1_net - rand1_pm
    exc2 = v2_net - rand2_pm
    t1x = eng.t_stat(exc1)
    t2x = eng.t_stat(exc2)
    d1 = v1_net - base_net
    d2 = v2_net - base_net
    t1 = eng.t_stat(d1)
    t2 = eng.t_stat(d2)

    n_months = len(md_list)

    def bar_a(net, exc, p):
        return bool(n_months >= MIN_PASS_MONTHS and net.mean() > 0 and exc.mean() > 0
                    and eng.t_stat(exc) >= BAR_T and p >= BONFERRONI_P)

    res = {
        "meta": {
            "study": "SMOOTHMOM-001",
            "window": f"{months[0].key}..{months[-1].key}",
            "n_months": n_months,
            "n_random": int(n_draws),
            "smoke": bool(smoke),
            "runtime_sec": None,
        },
        "baseline": {**summary(base_net, base_excess),
                     "turnover": turnover(longs + shorts),
                     "percentile_vs_random": p_base},
        "v1": {**summary(v1_net, exc1), "turnover": turnover(v1_long + v1_short),
               "percentile_vs_random": p1, "random_mean": float(rand1_pm.mean())},
        "v2": {**summary(v2_net, exc2), "turnover": turnover(v2_long + v2_short),
               "percentile_vs_random": p2, "random_mean": float(rand2_pm.mean())},
        "v1_vs_baseline": {"mean": float(d1.mean()), "t": float(t1)},
        "v2_vs_baseline": {"mean": float(d2.mean()), "t": float(t2)},
        "bars": {
            "threshold_pct": BONFERRONI_P,
            "t_threshold": BAR_T,
            "min_months": MIN_PASS_MONTHS,
            "v1_a": bar_a(v1_net, exc1, p1),
            "v2_a": bar_a(v2_net, exc2, p2),
            "v1_b": bool(d1.mean() > 0 and t1 >= BAR_T),
            "v2_b": bool(d2.mean() > 0 and t2 >= BAR_T),
            "v1_c": bool(p1 >= BONFERRONI_P),
            "v2_c": bool(p2 >= BONFERRONI_P),
        },
        "determinism_sha": _sha_vectors(base_net, v1_net, v2_net),
    }
    res["meta"]["runtime_sec"] = round(time.time() - t0, 1)

    out_dir = Path(args.out_dir) if args.out_dir else HERE
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(json.dumps(res, indent=2))
    (out_dir / "results.md").write_text(_markdown(res))
    print(f"wrote {out_dir / 'results.json'} and results.md in {res['meta']['runtime_sec']}s")
    return 0


def _sha_vectors(*arrs) -> str:
    h = hashlib.sha256()
    for a in arrs:
        h.update(np.asarray(a, float).tobytes())
    return h.hexdigest()


def _markdown(res: dict) -> str:
    def row(name, s, pct):
        return (f"| {name} | {s['mean_net']*100:+.3f}% | {s['ann_net']*100:+.2f}% | "
                f"{s.get('t_excess', float('nan')):+.3f} | {pct:.2f} | "
                f"{s['max_drawdown']*100:+.2f}% | {s['sharpe']:+.3f} | "
                f"{s['win_rate']*100:.1f}% | {s['turnover']:.3f} |")

    b = res["bars"]
    lines = [
        "# SMOOTHMOM-001 results (DEV only)",
        "",
        f"window={res['meta']['window']} n_months={res['meta']['n_months']} "
        f"n_random={res['meta']['n_random']} smoke={res['meta']['smoke']} "
        f"runtime={res['meta']['runtime_sec']}s",
        "",
        "| portfolio | mean/mo | ann | t(excess) | p vs random | maxDD | Sharpe | win | turnover |",
        "|---|---|---|---|---|---|---|---|---|",
        row("baseline MOM", res["baseline"], res["baseline"]["percentile_vs_random"]),
        row("V1 double sort", res["v1"], res["v1"]["percentile_vs_random"]),
        row("V2 combined score", res["v2"], res["v2"]["percentile_vs_random"]),
        "",
        f"V1 - baseline: mean {res['v1_vs_baseline']['mean']*100:+.3f}%/mo  "
        f"t {res['v1_vs_baseline']['t']:+.3f}",
        f"V2 - baseline: mean {res['v2_vs_baseline']['mean']*100:+.3f}%/mo  "
        f"t {res['v2_vs_baseline']['t']:+.3f}",
        "",
        f"bars: V1 a={b['v1_a']} b={b['v1_b']} c={b['v1_c']} | "
        f"V2 a={b['v2_a']} b={b['v2_b']} c={b['v2_c']} "
        f"(p >= {b['threshold_pct']}, t >= {b['t_threshold']}, months >= {b['min_months']})",
        "",
        f"worst3 baseline {res['baseline']['worst3']}",
        f"worst3 V1 {res['v1']['worst3']}",
        f"worst3 V2 {res['v2']['worst3']}",
        "",
        f"determinism sha256: `{res['determinism_sha']}`",
    ]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
