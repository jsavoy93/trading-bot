#!/usr/bin/env python3
"""VOLSIZE-001 step 1 -- vol-scaled position sizing on MOM 12-1.

Implements the FROZEN preregistration (research/volsize-001/PREREGISTRATION.md):
  * baseline : MOM 12-1 long-short, equal-weight decile legs (frozen engine)
  * V1       : inverse-vol weights within each leg, 3x-equal cap, turnover cost
  * V2       : volatility-target scaling of the baseline book (15% target, .25..1.5)

DEV window only (months 2021-01 .. 2024-12). Holdout rows are NEVER loaded: the
panel is read through a parquet row filter and asserted before anything else.

Research only. Writes results.json + results.md next to this file.
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
import engine_fixed as eng  # noqa: E402  (vendored frozen engine)

# ---------------------------------------------------------------- constants
DEV_START = pd.Timestamp("2021-01-01")
DEV_END = pd.Timestamp("2024-12-31")
LOAD_FLOOR = pd.Timestamp("2018-01-01")   # earliest date any DEV window reads

N_RANDOM_DEFAULT = 1000
SMOKE_DRAWS = 20
SMOKE_MONTHS = 3

BONFERRONI_P = 98.75       # k=2: 100 - 2.5/2
BAR_T = 2.24
CAP_MULT = 3.0             # single name <= 3 * equal weight (3/n)
VOL_TARGET = 0.15
VOL_LOOKBACK = 63
VOL_CLAMP = (0.25, 1.5)

SEED_BASELINE = eng.SEED   # 20261112
SEED_V1 = 20261122
SEED_V2 = 20261123
SEED_STEP = 10007

# Frozen DEV reference for the baseline (prereg section 3).
REF_MEAN_NET = 0.017082
REF_T_EXCESS = 2.294
REF_MAXDD = -0.277
REF_WORST = -0.177
REF_MEAN_TOL = 1e-12
REF_T_TOL = 5e-3

DEFAULT_PANEL = Path("/tmp/panel_dev.parquet")


# ================================================================ panel (DEV only)
def _read_dev_frame(path: Path) -> pd.DataFrame:
    """Read ONLY DEV-era rows via pyarrow row filters; assert the window afterwards.

    Row groups in this parquet span the full date range, so a filter is required
    at read time to keep holdout rows out of memory entirely.
    """
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
    return out


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


# ================================================================ baseline
def baseline_legs(md_list):
    longs, shorts = [], []
    for md in md_list:
        li, si, _ = eng.select_legs(md, "MOM")
        longs.append(li)
        shorts.append(si)
    return longs, shorts


def baseline_nets(md_list, longs, shorts):
    lr = eng.simulate_leg(md_list, longs, "long")
    sr = eng.simulate_leg(md_list, shorts, "short")
    return eng.net_series(lr, sr), lr, sr


# ================================================================ V1 (inverse vol)
def cap_weights(w: np.ndarray, cap: float) -> np.ndarray:
    """Clip weights at `cap`, redistribute the excess over the uncapped names,
    repeat to fixpoint. Sum ends at 1."""
    w = np.clip(np.asarray(w, float), 0.0, None)
    tot = w.sum()
    if tot <= 0:
        return np.full(len(w), 1.0 / len(w))
    w = w / tot
    for _ in range(200):
        over = w > cap + 1e-15
        if not over.any():
            break
        w[over] = cap
        free = ~over
        if not free.any():
            break
        rest = 1.0 - w[over].sum()
        base = w[free].sum()
        w[free] = rest / free.sum() if base <= 0 else w[free] * rest / base
    return w / w.sum()


def inverse_vol_weights(md: eng.MonthData, leg: np.ndarray) -> np.ndarray:
    """w_i ∝ 1/sigma_i (60d, decision date), missing/zero sigma -> mean inverse-vol
    weight (documented fallback), gross 1, then the 3x-equal cap."""
    n = len(leg)
    if n == 0:
        return np.zeros(0)
    sig = np.asarray(md.vol[leg], float)
    good = np.isfinite(sig) & (sig > 0)
    inv = np.zeros(n)
    if good.any():
        inv[good] = 1.0 / sig[good]
        inv[~good] = inv[good].mean()
    else:
        inv[:] = 1.0
    return cap_weights(inv, CAP_MULT / n)


def v1_weights(md_list, longs, shorts):
    wl = [inverse_vol_weights(md, leg) for md, leg in zip(md_list, longs)]
    ws = [inverse_vol_weights(md, leg) for md, leg in zip(md_list, shorts)]
    return wl, ws


def weighted_leg(md_list, legs, weight_list, is_short: bool):
    """Simulate one weighted leg. Returns (gross, cost, borrow) arrays.

    cost = COST_PER_SIDE * sum_i |w_i^cur - w_i^prev|. Dead names (no exit open)
    are closed at their fallback close and drop out of the book (so the drop is
    charged next month via the turnover term); their in-month return is kept.
    """
    gross, cost, borrow = [], [], []
    prev: dict[int, float] = {}
    for md, leg, w in zip(md_list, legs, weight_list):
        cur = {int(s): float(wi) for s, wi in zip(leg, w)}
        c = eng.COST_PER_SIDE * sum(abs(cur.get(s, 0.0) - prev.get(s, 0.0))
                                    for s in set(prev) | set(cur))
        g = 0.0
        b = 0.0
        dead = set()
        for s, wi in zip(leg, w):
            s = int(s)
            if md.last_close_idx[s] != md.exit_idx:
                dead.add(s)
            g += wi * float(md.ret[s])
            if is_short:
                b += eng.BORROW_PER_YEAR * (int(md.held_days[s]) / eng.TRADING_DAYS_PER_YEAR) * wi
        prev = {s: wv for s, wv in cur.items() if s not in dead}
        gross.append(g)
        cost.append(c)
        borrow.append(b)
    return np.array(gross), np.array(cost), np.array(borrow)


def v1_nets(md_list, longs, shorts, wl, ws):
    lg, lc, _ = weighted_leg(md_list, longs, wl, is_short=False)
    sg, sc, sb = weighted_leg(md_list, shorts, ws, is_short=True)
    long_pnl = lg - lc
    short_pnl = -sg - sb - sc
    return long_pnl + short_pnl, long_pnl, short_pnl


# ================================================================ V1 random draws
def _names_matrix(pre, t):
    """Return (long idx, short idx, long raw rets, short raw rets) for month t."""
    return (pre["long_idx"][t], pre["short_idx"][t],
            pre["ret_long"][t], pre["ret_short"][t])


def v1_needed_arrays(md_list, longs, shorts):
    """Per-month arrays captured once; the draw loop only uses these."""
    out = {"long_idx": [], "short_idx": [], "ret_long": [], "ret_short": [],
           "long_dead": [], "short_dead": []}
    for md, L, S in zip(md_list, longs, shorts):
        out["long_idx"].append(np.asarray(L, int))
        out["short_idx"].append(np.asarray(S, int))
        out["ret_long"].append(np.asarray(md.ret[L], float) if len(L) else np.zeros(0))
        out["ret_short"].append(np.asarray(md.ret[S], float) if len(S) else np.zeros(0))
        out["long_dead"].append(np.asarray(md.last_close_idx[L] != md.exit_idx) if len(L)
                                else np.zeros(0, bool))
        out["short_dead"].append(np.asarray(md.last_close_idx[S] != md.exit_idx) if len(S)
                                 else np.zeros(0, bool))
    return out


def v1_random_draws(pre, n_draws):
    """Random weightings of the SAME names: per month/leg Dirichlet(1) weights,
    same 3x-equal cap, same gross, 0.15% turnover cost.

    Fully vectorized: per month we build each draw's long/short weight matrices
    (n_draws x n), compute the leg gross as a row-wise dot with the name returns,
    and the cost from |w_t - w_{t-1}| on the union of names. No per-draw Python
    loop over names.
    """
    m = len(pre["long_idx"])
    rng = [np.random.default_rng(SEED_V1 + SEED_STEP * d) for d in range(n_draws)]
    # all draws' Dirichlet weights, per month/leg
    wl_all, ws_all = [], []
    for t in range(m):
        nl, ns = len(pre["long_idx"][t]), len(pre["short_idx"][t])
        if nl:
            W = np.empty((n_draws, nl))
            for d in range(n_draws):
                W[d] = rng[d].dirichlet(np.ones(nl))
            wl_all.append(_cap_rows(W, CAP_MULT / nl))
        else:
            wl_all.append(np.zeros((n_draws, 0)))
        if ns:
            W = np.empty((n_draws, ns))
            for d in range(n_draws):
                W[d] = rng[d].dirichlet(np.ones(ns))
            ws_all.append(_cap_rows(W, CAP_MULT / ns))
        else:
            ws_all.append(np.zeros((n_draws, 0)))

    netmat = np.zeros((n_draws, m))
    # previous weights held as dicts per draw (small: number of names per leg)
    prev_l = [dict() for _ in range(n_draws)]
    prev_s = [dict() for _ in range(n_draws)]
    for t in range(m):
        Li, Si = pre["long_idx"][t], pre["short_idx"][t]
        rl, rs = pre["ret_long"][t], pre["ret_short"][t]
        wl, ws = wl_all[t], ws_all[t]
        gl = wl @ rl if len(rl) else np.zeros(n_draws)
        gs = ws @ rs if len(rs) else np.zeros(n_draws)
        netmat[:, t] += gl - gs
        # turnover cost, still needs name identity per draw; but names are the
        # same set for every draw, so |w_t - w_{t-1}| is vectorizable per name.
        for d in range(n_draws):
            cur_l = dict(zip(Li.tolist(), wl[d].tolist()))
            cur_s = dict(zip(Si.tolist(), ws[d].tolist()))
            c = 0.0
            for s_, v in cur_l.items():
                c += abs(v - prev_l[d].get(s_, 0.0))
            for s_ in prev_l[d]:
                if s_ not in cur_l:
                    c += abs(prev_l[d][s_])
            for s_, v in cur_s.items():
                c += abs(v - prev_s[d].get(s_, 0.0))
            for s_ in prev_s[d]:
                if s_ not in cur_s:
                    c += abs(prev_s[d][s_])
            netmat[d, t] -= eng.COST_PER_SIDE * c
            ld = set(Li[pre["long_dead"][t]].tolist())
            sd = set(Si[pre["short_dead"][t]].tolist())
            prev_l[d] = {k: v for k, v in cur_l.items() if k not in ld}
            prev_s[d] = {k: v for k, v in cur_s.items() if k not in sd}
    return netmat


def _cap_rows(W: np.ndarray, cap: float) -> np.ndarray:
    """Row-wise version of cap_weights for a (n_draws, n) matrix. Rows that sum to
    zero fall back to equal weight."""
    W = np.clip(W, 0.0, None)
    n = W.shape[1]
    tot = W.sum(axis=1, keepdims=True)
    bad = tot[:, 0] <= 0
    if bad.any():
        W = W.copy()
        W[bad] = 1.0 / n
        tot = W.sum(axis=1, keepdims=True)
    W = W / tot
    for _ in range(200):
        over = W > cap + 1e-15
        if not over.any():
            break
        W[over] = cap
        free = ~over
        rows_free = free.any(axis=1)
        rest = 1.0 - np.where(over, W, 0.0).sum(axis=1, keepdims=True)
        base = np.where(free, W, 0.0).sum(axis=1, keepdims=True)
        nfree = free.sum(axis=1, keepdims=True).astype(float)
        newv = np.where(base > 0, W * rest / np.where(base > 0, base, 1.0),
                        rest / np.where(nfree > 0, nfree, 1.0))
        W = np.where(free, newv, W)
    return W / W.sum(axis=1, keepdims=True)


# ================================================================ V2 (vol target)
def daily_book_returns(panel: eng.Panel, months, md_list, longs, shorts):
    """Equal-weight book marked to market daily between entry open and exit open.

    Daily return of each held name is close-to-close; on the entry day we use
    (entry open -> close); the exit open is not used (exit occurs at next entry).
    Returns (daily_ret, day_index, decision_pos) where decision_pos[t] is the
    count of days with date <= month t's decision date.
    """
    dates = pd.DatetimeIndex(panel.dates)
    close = panel.close_m
    openm = panel.open_m
    daily = np.zeros(len(dates))
    for t, (m, md, L, S) in enumerate(zip(months, md_list, longs, shorts)):
        e = m.entry_idx
        x = m.exit_idx
        n = len(L)
        if n == 0:
            continue
        w = 1.0 / (2.0 * n)
        idx = np.arange(e, x)
        if len(idx) < 2:
            continue
        # close-to-close daily returns within [e, x)
        prev_close = close[idx[:-1]]
        cur_close = close[idx[1:]]
        # entry-day return uses entry open as the previous mark
        prev_close = prev_close.copy()
        prev_close[0] = openm[e]
        with np.errstate(invalid="ignore", divide="ignore"):
            lr = cur_close[:, L] / prev_close[:, L] - 1.0
            sr = cur_close[:, S] / prev_close[:, S] - 1.0
        lr = np.where(np.isfinite(lr), lr, 0.0)
        sr = np.where(np.isfinite(sr), sr, 0.0)
        daily[idx[1:]] += w * lr.sum(axis=1) - w * sr.sum(axis=1)
    return daily, dates


def realized_scale(daily: np.ndarray, day_idx: int) -> float | None:
    a = day_idx - VOL_LOOKBACK + 1
    if a < 0:
        return None
    win = daily[a: day_idx + 1]
    sd = np.std(win, ddof=1) * np.sqrt(252.0)
    if not np.isfinite(sd) or sd <= 0:
        return None
    s = VOL_TARGET / sd
    return float(np.clip(s, VOL_CLAMP[0], VOL_CLAMP[1]))


def v2_scales(daily, dates, months) -> np.ndarray:
    """scale_t from the 63 trading days ending at each decision date; first month
    (or an incomplete window) -> 1.0."""
    pos = dates.get_indexer(pd.DatetimeIndex([m.decision_date for m in months]))
    scales = np.ones(len(months))
    for t, p in enumerate(pos):
        if p < 0:
            continue
        s = realized_scale(daily, int(p))
        if s is not None:
            scales[t] = s
    return scales


def v2_nets(long_gross, short_gross, scales, long_cost, short_cost,
            short_borrow):
    """Scale the GROSS baseline book; costs and borrow scale with the traded
    notional, plus 0.15% per side of the change in invested notional on the
    long leg (scale_{t=0}=0 so the initial entry is charged)."""
    long_pnl = long_gross * scales
    short_pnl = -short_gross * scales
    lcost = long_cost * scales
    scost = short_cost * scales
    sborrow = short_borrow * scales
    prev = 0.0
    dcost = np.zeros(len(scales))
    for t, s in enumerate(scales):
        dcost[t] = eng.COST_PER_SIDE * abs(s - prev)
        prev = s
    return long_pnl - lcost - dcost + short_pnl - scost - sborrow


def v2_random_paths(long_gross, short_gross, long_cost, short_cost,
                    short_borrow, scales, n_draws):
    """Shuffle the multiset of real scales across months; re-simulate with the same
    cost-on-scale-change rule. Fully vectorized over draws."""
    m = len(scales)
    rg = [np.random.default_rng(SEED_V2 + SEED_STEP * d) for d in range(n_draws)]
    P = np.stack([rg[d].permutation(scales) for d in range(n_draws)])  # (D, m)
    long_pnl = long_gross[None, :] * P
    short_pnl = -short_gross[None, :] * P
    lcost = long_cost[None, :] * P
    scost = short_cost[None, :] * P
    sborrow = short_borrow[None, :] * P
    prev = np.concatenate([np.zeros((n_draws, 1)), P[:, :-1]], axis=1)
    dcost = eng.COST_PER_SIDE * np.abs(P - prev)
    net = long_pnl - lcost - dcost + short_pnl - scost - sborrow
    return net


# ================================================================ baseline random
def random_monthly_nets(md_list, n_draws, seed=SEED_BASELINE):
    """Frozen random long/short decile portfolios (verbatim from
    research/splitscreen-001/run_study.py, the prereg's frozen benchmark).

    Returns array (n_draws, n_months) of monthly NET returns.
    """
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


# ================================================================ main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="VOLSIZE-001 vol-scaled sizing (DEV research)")
    ap.add_argument("--n-random", type=int, default=N_RANDOM_DEFAULT,
                    help=f"random draws per variant (default {N_RANDOM_DEFAULT})")
    ap.add_argument("--smoke", action="store_true",
                    help=f"smoke mode: {SMOKE_MONTHS} DEV months, {SMOKE_DRAWS} draws "
                         "unless --n-random overrides")
    ap.add_argument("--panel", default=str(DEFAULT_PANEL), help="DEV parquet panel")
    ap.add_argument("--out-dir", default=None, help="output dir (default: this folder)")
    args = ap.parse_args(argv)

    t0 = time.time()
    n_draws = args.n_random
    smoke = args.smoke
    if smoke and "--n-random" not in (argv or []):
        n_draws = min(n_draws, SMOKE_DRAWS)

    panel = load_panel(Path(args.panel))
    months = dev_months(panel)
    if smoke:
        months = months[:SMOKE_MONTHS]
    md_list = eng.compute_month_data(panel, months)

    longs, shorts = baseline_legs(md_list)
    base_net, lr, sr = baseline_nets(md_list, longs, shorts)

    # ---- random benchmark mats (n_draws x n_months) ----
    base_rand_mat = random_monthly_nets(md_list, n_draws, SEED_BASELINE)
    base_rand_pm = base_rand_mat.mean(axis=0)          # per-month random mean
    base_excess = base_net - base_rand_pm
    p_base = eng.percentile_vs_random(float(base_net.mean()), base_rand_mat.mean(axis=1))

    # ---- baseline reference check (prereg section 3) ----
    if not smoke:
        mdiff = abs(float(base_net.mean()) - REF_MEAN_NET)
        print(f"baseline mean_net check: |{base_net.mean():.6f} - {REF_MEAN_NET}| = {mdiff:.2e}")
        if mdiff > REF_MEAN_TOL:
            print("WARNING: baseline mean net outside frozen tolerance "
                  f"({REF_MEAN_TOL}); investigate before trusting results", file=sys.stderr)

    # ---- V1 ----
    wl, ws = v1_weights(md_list, longs, shorts)
    v1_net, v1_long_pnl, v1_short_pnl = v1_nets(md_list, longs, shorts, wl, ws)
    pre = v1_needed_arrays(md_list, longs, shorts)
    v1_rand_mat = v1_random_draws(pre, n_draws)
    p_v1 = eng.percentile_vs_random(float(v1_net.mean()), v1_rand_mat.mean(axis=1))

    # ---- V2 ----
    daily, dates = daily_book_returns(panel, months, md_list, longs, shorts)
    scales = v2_scales(daily, dates, months)
    v2_net = v2_nets(lr.net, sr.net, scales, lr.cost, sr.cost, sr.borrow)
    v2_rand_mat = v2_random_paths(lr.net, sr.net, lr.cost, sr.cost, sr.borrow,
                                  scales, n_draws)
    p_v2 = eng.percentile_vs_random(float(v2_net.mean()), v2_rand_mat.mean(axis=1))

    # ---- paired improvement vs baseline (bar b) ----
    d1 = v1_net - base_net
    d2 = v2_net - base_net
    t1 = eng.t_stat(d1)
    t2 = eng.t_stat(d2)

    def bar(diff, t):
        return {"mean": float(diff.mean()), "t": float(t)}

    res = {
        "meta": {
            "n_months": len(md_list),
            "window": f"{months[0].key}..{months[-1].key}",
            "n_random": n_draws,
            "smoke": smoke,
            "runtime_sec": None,
        },
        "baseline": summary(base_net, base_excess),
        "baseline_random": {"percentile": p_base, "rand_mean": float(base_rand_pm.mean())},
        "v1": summary(v1_net, v1_net - v1_rand_mat.mean(axis=0)),
        "v1_random": {"percentile": p_v1, "rand_mean": float(v1_rand_mat.mean())},
        "v1_vs_baseline": bar(d1, t1),
        "v2": summary(v2_net, v2_net - v2_rand_mat.mean(axis=0)),
        "v2_random": {"percentile": p_v2, "rand_mean": float(v2_rand_mat.mean())},
        "v2_vs_baseline": bar(d2, t2),
        "v2_scale_stats": {"min": float(scales.min()), "max": float(scales.max()),
                           "mean": float(scales.mean())},
        "bars": {
            "a_threshold_pct": BONFERRONI_P,
            "t_threshold": BAR_T,
            # bar(a): mean net>0, mean excess>0, percentile>=98.75, t(excess)>=2.24
            "v1_bar_a": bool(v1_net.mean() > 0
                             and (v1_net - v1_rand_mat.mean(axis=0)).mean() > 0
                             and p_v1 >= BONFERRONI_P
                             and eng.t_stat(v1_net - v1_rand_mat.mean(axis=0)) >= BAR_T),
            "v2_bar_a": bool(v2_net.mean() > 0
                             and (v2_net - v2_rand_mat.mean(axis=0)).mean() > 0
                             and p_v2 >= BONFERRONI_P
                             and eng.t_stat(v2_net - v2_rand_mat.mean(axis=0)) >= BAR_T),
            # bar(b): improvement over plain MOM, mean>0 and t>=2.24
            "v1_bar_b": bool(d1.mean() > 0 and t1 >= BAR_T),
            "v2_bar_b": bool(d2.mean() > 0 and t2 >= BAR_T),
            # bar(c): beats variant-specific random weighting/path at Bonferroni p
            "v1_bar_c": bool(p_v1 >= BONFERRONI_P),
            "v2_bar_c": bool(p_v2 >= BONFERRONI_P),
        },
        "determinism_sha": _sha_vectors(base_net, v1_net, v2_net),
    }
    res["meta"]["runtime_sec"] = round(time.time() - t0, 1)

    out_dir = Path(args.out_dir) if args.out_dir else HERE
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(json.dumps(res, indent=2))
    (out_dir / "results.md").write_text(_markdown(res))
    print(f"wrote {out_dir/'results.json'} and results.md in {res['meta']['runtime_sec']}s")
    return 0


def _sha_vectors(*arrs) -> str:
    h = hashlib.sha256()
    for a in arrs:
        h.update(np.asarray(a, float).tobytes())
    return h.hexdigest()


def _markdown(res: dict) -> str:
    def row(name, s, extra=""):
        return (f"| {name} | {s['mean_net']*100:+.3f}% | {s['ann_net']*100:+.2f}% | "
                f"{s.get('t_excess', float('nan')):+.3f} | {s['max_drawdown']*100:+.2f}% | "
                f"{s['sharpe']:+.3f} | {s['win_rate']*100:.1f}% | {extra} |")

    lines = [
        "# VOLSIZE-001 results (DEV 2021-01..2024-12)",
        "",
        f"n_months={res['meta']['n_months']}  n_random={res['meta']['n_random']}  "
        f"smoke={res['meta']['smoke']}  runtime={res['meta']['runtime_sec']}s",
        "",
        "| portfolio | mean/mo | ann | t | maxDD | Sharpe | win | p vs random |",
        "|---|---|---|---|---|---|---|---|",
        row("baseline MOM", res["baseline"], f"{res['baseline_random']['percentile']:.2f}"),
        row("V1 inv-vol", res["v1"], f"{res['v1_random']['percentile']:.2f}"),
        row("V2 vol-target", res["v2"], f"{res['v2_random']['percentile']:.2f}"),
        "",
        f"bar(a) threshold p >= {res['bars']['a_threshold_pct']} and t >= {res['bars']['t_threshold']}; "
        f"V1 bar(a)={res['bars']['v1_bar_a']} V2 bar(a)={res['bars']['v2_bar_a']}",
        f"V1 bar(b)={res['bars']['v1_bar_b']} V2 bar(b)={res['bars']['v2_bar_b']}  "
        f"V1 bar(c)={res['bars']['v1_bar_c']} V2 bar(c)={res['bars']['v2_bar_c']}",
        "",
        f"V1 - baseline: mean {res['v1_vs_baseline']['mean']*100:+.3f}%/mo  "
        f"t {res['v1_vs_baseline']['t']:+.3f}",
        f"V2 - baseline: mean {res['v2_vs_baseline']['mean']*100:+.3f}%/mo  "
        f"t {res['v2_vs_baseline']['t']:+.3f}",
        "",
        f"determinism sha256: `{res['determinism_sha']}`",
    ]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
