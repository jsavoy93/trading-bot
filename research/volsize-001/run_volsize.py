"""VOLSIZE-001: vol-scaled sizing on MOM 12-1, DEV 2021-01..2024-12 only.

Baseline reproduction + V1 (inverse-vol weights) + V2 (vol-target scaling) + bars (a)(b)(c)
+ drawdown / worst-3-months. Mechanics frozen in PREREGISTRATION.md (commit 22247e3).

Never reads data dated after 2024-12-31: the panel is loaded with a parquet row filter.
RESEARCH ONLY. Exit month <= 2024-12.
"""
import hashlib, json, math, sys, time
from pathlib import Path
import numpy as np, pandas as pd, scipy.stats as st

HERE = Path(__file__).resolve().parents[2]          # worktree root
sys.path.insert(0, str(HERE / "scripts_holdout"))
sys.path.insert(0, str(HERE / "research" / "splitscreen-001"))
import engine_fixed as E
import run_study as R

PANEL = Path("/root/research/holdout-mom-001/scripts_holdout/panel_holdout.parquet")
PANEL_DEV = Path("/tmp/panel_dev.parquet")
DEV_START, DEV_END = pd.Period("2021-01", "M"), pd.Period("2024-12", "M")
CUTOFF = pd.Timestamp("2024-12-31")
BAR_T, BAR_PCT_A, BAR_PCT_C = 2.24, 97.5, 98.75
N_DRAWS = 1000
SEED_BASE = E.SEED          # 20261112 baseline random benchmark
SEED_V1 = 20261122
SEED_V2 = 20261123
STRIDE = 10007
TARGET_VOL = 0.15
CAP_MULT = 3.0
VOL_DAYS, VT_DAYS = 60, 63
TARGET_MEAN = 0.01708229936906453          # reference 1.7082%/mo
TARGET_T = 2.294


# ------------------------------------------------------------------ data
def load_ctx():
    sha = hashlib.sha256(PANEL.read_bytes()).hexdigest()
    assert sha.startswith("4387206b") and sha.endswith("c071"), sha
    panel = E.load_panel(PANEL_DEV)
    assert pd.Timestamp(panel.dates.max()) <= CUTOFF
    months = E.build_months(panel)
    mds = E.compute_month_data(panel, months)
    rows = [i for i, m in enumerate(months)
            if DEV_START <= m.period <= DEV_END and m.exit_date <= CUTOFF]
    keys = [months[i].key for i in rows]
    dec = [months[i].decision_date for i in rows]
    assert max(d.date() for d in dec) < pd.Timestamp("2025-01-01").date()
    assert max(months[i].exit_date for i in rows) <= CUTOFF
    legs = [E.select_legs(mds[i], "MOM") for i in rows]
    L = [np.sort(lg) for lg, sh, el in legs]
    S = [np.sort(sh) for lg, sh, el in legs]
    elig = [np.flatnonzero(el) for lg, sh, el in legs]
    return sha, panel, months, [mds[i] for i in rows], keys, dec, L, S, elig, [(months[i].entry_idx, months[i].exit_idx) for i in rows]


# ------------------------------------------------------------------ book simulator (weights + turnover)
def simulate_book(md_list, legs, weights, label):
    """Generic leg simulator: weights[r] is an array of per-name weights aligned to
    legs[r]. Cost = 0.15% * sum|w_cur - w_prev| (engine convention for equal weights).
    Delisted names (last_close_idx != exit_idx) are treated as closed at their exit.
    Returns (gross_net, cost, borrow, detail)."""
    n = len(legs)
    gross = np.zeros(n); cost = np.zeros(n); borrow = np.zeros(n)
    prev = {}
    detail = []
    for r, (md, leg) in enumerate(zip(md_list, legs)):
        w = weights[r]
        leg = np.asarray(leg, dtype=int)
        if len(leg) == 0:
            # flat month: every name in the book is closed (engine charges COST*w_last)
            if prev:
                w_prev = np.mean(list(prev.values()))
                c = E.COST_PER_SIDE * len(prev) * w_prev
            else:
                c = 0.0
            g = 0.0; b = 0.0; dead = {}; alive = {}
        else:
            cur = {int(s): float(w[k]) for k, s in enumerate(leg)}
            dead_now = {s for s in cur if md.last_close_idx[s] != md.exit_idx}
            alive = {s: v for s, v in cur.items() if s not in dead_now}
            g = float(sum(cur[s] * float(md.ret[s]) for s in cur))
            # Frozen engine convention: opened names cost COST*w_cur; names that leave the
            # book this month (or die) cost COST*w_cur of this month. Turnover from a
            # weight change on a name held in both months is charged as |dw| excess only
            # (V1); for equal weights it is exactly zero.
            opened = sum(1 for s in cur if s not in prev)
            closed = sum(1 for s in prev if s not in cur) + len(dead_now)
            c = E.COST_PER_SIDE * (opened + closed) * (1.0 / len(cur))
            c += E.COST_PER_SIDE * sum(min(abs(cur[s] - prev[s]), 1.0 / len(cur))
                                      for s in cur if s in prev)
            b = float(sum(E.BORROW_PER_YEAR * (int(md.held_days[s]) / E.TRADING_DAYS_PER_YEAR) * cur[s] for s in cur))
            dead = {s: v for s, v in cur.items() if s not in alive}
        gross[r] = g; cost[r] = c; borrow[r] = b
        detail.append({"n": len(leg), "gross": g, "cost": c, "borrow": b, "dead_weights": dead})
        prev = alive if len(leg) else {}
    return gross, cost, borrow, detail


def equal_weights(legs):
    return [np.full(len(l), 1.0 / len(l)) if len(l) else np.zeros(0) for l in legs]


def net_from(gross_l, cost_l, borrow_l, gross_s, cost_s, borrow_s):
    return (gross_l - cost_l) + (-gross_s - borrow_s - cost_s)


# ------------------------------------------------------------------ metrics
def series_metrics(net, draw_means, rand_mean=None):
    net = np.asarray(net, float)
    if rand_mean is None:
        rand_mean = draw_means.mean(axis=0)
    excess = net - rand_mean
    m = R.window_metrics(net, excess, rand_mean, np.zeros(len(net)), np.zeros(len(net)), draw_means.mean(axis=1))
    return m


def paired(diff):
    diff = np.asarray(diff, float)
    t = st.ttest_1samp(diff, 0.0)
    return {"mean": float(diff.mean()), "t": float(t.statistic), "p": float(t.pvalue),
            "months_up": int((diff > 0).sum()), "months_down": int((diff < 0).sum()),
            "pass": bool(diff.mean() > 0 and t.statistic >= BAR_T)}


def dev_bars_a(m):
    """Bars (a) with the VOLSIZE-001 preregistered floors."""
    b = R.dev_bars(m)
    b["ALL_PASS"] = bool(b["n_months>=24"] and b["mean_net>0"] and b["mean_excess>0"]
                         and b["percentile>=97.5"] and b["t>=2.24"])
    return b


def summ(m):
    return {k: float(m[k]) for k in ("mean_monthly_net", "annualized_net", "t_stat_excess",
                                     "percentile_vs_random", "sharpe_net", "max_drawdown",
                                     "monthly_win_rate", "monthly_vol", "annualized_vol",
                                     "mean_monthly_excess")} | {"n_months": int(m["n_months"])}


def worst(net, keys, k=3):
    return [[kk, float(v)] for kk, v in sorted(zip(keys, map(float, net)), key=lambda x: x[1])[:k]]


def capture_ratio(net, keys, k=5):
    """Mean of the worst k baseline months for each series (crash-risk comparison)."""
    order = [i for i, _ in sorted(enumerate(net), key=lambda x: x[1])[:k]]
    return float(np.mean(net[order])), [keys[i] for i in order]


# ------------------------------------------------------------------ V1 weights
def cap_renorm(x, cap):
    x = x.copy()
    if x.sum() <= 0:
        return np.full(len(x), 1.0 / len(x))
    x = x / x.sum() * 1.0
    for _ in range(100):
        over = x > cap + 1e-15
        if not over.any():
            break
        x[over] = cap
        free = ~over
        if not free.any():
            break
        x[free] = x[free] / x[free].sum() * (1.0 - cap * over.sum())
    return x


def invol_weights(md, leg):
    n = len(leg)
    if n == 0:
        return np.zeros(0)
    sig = np.array([md.vol[s] if np.isfinite(md.vol[s]) and md.vol[s] > 0 else np.nan for s in leg])
    ok = np.isfinite(sig)
    if not ok.any():
        return np.full(n, 1.0 / n)
    x = np.zeros(n)
    x[ok] = 1.0 / sig[ok]
    if (~ok).any():                      # documented fallback: equal within the no-vol part
        x[~ok] = x[ok].mean() if ok.any() else 1.0
    return cap_renorm(x, CAP_MULT / n)


# ------------------------------------------------------------------ V2 daily series + scale
def strategy_daily_returns(panel, L, S, month_idx, dec, entry_exit):
    """Daily close-to-close return of the equal-weight book held in each month, indexed on
    the panel calendar. Only rows up to CUTOFF. Returns (cal, daily)."""
    close = panel.close_m
    cal = pd.DatetimeIndex(panel.dates)
    daily = np.zeros(len(cal))
    for r, (lg, sh) in enumerate(zip(L, S)):
        e, x = entry_exit[r]
        n = len(lg)
        if n == 0:
            continue
        w = 0.5 / n
        idx = list(lg) + list(sh)
        sign = np.array([1.0] * n + [-1.0] * n)
        seg = close[e:x + 1][:, idx]
        with np.errstate(invalid="ignore", divide="ignore"):
            prev = np.vstack([seg[:1], seg[:-1]])
            dr = seg / prev - 1.0
        dr[0] = 0.0
        dr = np.nan_to_num(dr, nan=0.0, posinf=0.0, neginf=0.0)
        daily[e:x] = (dr[:-1] * (w * sign)).sum(axis=1)   # return realised on day t+1 for t in [e, x)
    return cal, daily


def vol_target_scales(cal, daily, dec):
    s = []
    for d in dec:
        i = int(np.searchsorted(cal.values, np.datetime64(d), side="right")) - 1   # last close <= dec
        assert cal[i].date() <= d.date()
        if i + 1 < VT_DAYS:
            s.append(1.0)
            continue
        win = daily[i - VT_DAYS + 1: i + 1]
        rv = float(np.std(win, ddof=1)) * math.sqrt(E.TRADING_DAYS_PER_YEAR)
        if not np.isfinite(rv) or rv <= 0:
            s.append(1.0)
        else:
            s.append(float(min(1.5, max(0.25, TARGET_VOL / rv))))
    return np.array(s)


def v2_net(base_long_pnl, base_short_pnl, base_net, scales):
    """Exposure scaling + cost on change of invested notional (0.15% per side)."""
    out = np.zeros(len(scales))
    prev = 0.0
    for t, k in enumerate(scales):
        out[t] = base_net[t] * k - E.COST_PER_SIDE * abs(k - prev)
        prev = k
    return out


# ------------------------------------------------------------------ main
def main():
    t0 = time.time()
    sha, panel, months, mds, keys, dec, L, S, elig, ee = load_ctx()
    n_m = len(keys)
    print(f"panel sha {sha[:8]}..{sha[-4:]} | DEV months {n_m} {keys[0]}..{keys[-1]} "
          f"| max decision {max(d.date() for d in dec)} | max exit {max(e for _, e in ee)}"
          f" (idx) date {pd.Timestamp(panel.dates[max(e for _, e in ee)]).date()}", flush=True)
    cal = pd.DatetimeIndex(panel.dates)
    assert cal.max() <= CUTOFF

    # ---- baseline reproduction
    eqL, eqS = equal_weights(L), equal_weights(S)
    gl, cl, bl, _ = simulate_book(mds, L, eqL, "long")
    gs, cs, bs, _ = simulate_book(mds, S, eqS, "short")
    net0 = net_from(gl, cl, bl, gs, cs, bs)
    net0b = E.net_series(E.simulate_leg(mds, L, "long"), E.simulate_leg(mds, S, "short"))
    max_abs = float(np.max(np.abs(net0 - net0b)))
    print(f"CHECK book-simulator vs frozen simulate_leg: max abs monthly diff {max_abs:.3e}", flush=True)
    assert max_abs < 1e-12, "book simulator does not reproduce the frozen engine"

    nleg0 = [len(x) for x in L]
    draws_mom = R.random_monthly_nets(mds, elig, nleg0, N_DRAWS, SEED_BASE)
    rand = draws_mom.mean(axis=0)
    m0 = series_metrics(net0, draws_mom, rand)
    print(f"baseline mean {m0['mean_monthly_net']*100:.4f}%/mo t {m0['t_stat_excess']:.4f} "
          f"pct {m0['percentile_vs_random']:.2f} maxDD {m0['max_drawdown']*100:.2f}% "
          f"worst {worst(net0, keys)} | reference 1.7082 / 2.294", flush=True)
    dev_ok = abs(m0["mean_monthly_net"] - TARGET_MEAN) < 1e-12 and abs(m0["t_stat_excess"] - TARGET_T) < 5e-3
    print(f"BASELINE REPRODUCTION {'MATCH' if dev_ok else 'MISMATCH -> BLOCKER'}", flush=True)
    if not dev_ok:
        raise SystemExit("baseline reproduction failed")

    out = {"panel_sha256": sha, "prereg_commit": "22247e3", "n_months": n_m, "keys": keys,
           "baseline": summ(m0), "baseline_worst3": worst(net0, keys),
           "baseline_monthly": dict(zip(keys, map(float, net0))),
           "baseline_vs_engine_max_abs_diff": max_abs}

    # ---- V1
    wL = [invol_weights(mds[r], L[r]) for r in range(n_m)]
    wS = [invol_weights(mds[r], S[r]) for r in range(n_m)]
    for r in range(n_m):
        for w, leg in ((wL[r], L[r]), (wS[r], S[r])):
            if len(leg):
                assert abs(w.sum() - 1.0) < 1e-9 and w.max() <= CAP_MULT / len(leg) + 1e-12
    gl1, cl1, bl1, _ = simulate_book(mds, L, wL, "long")
    gs1, cs1, bs1, _ = simulate_book(mds, S, wS, "short")
    net1 = net_from(gl1, cl1, bl1, gs1, cs1, bs1)
    m1 = series_metrics(net1, draws_mom)
    bars1a = dev_bars_a(m1)
    b1b = paired(net1 - net0)
    pct1 = E.percentile_vs_random(float(net1.mean()), draws_mom.mean(axis=1))

    # V1 bar (c): 1000 random weightings of the same names, same cap scheme
    n_draws_v1 = N_DRAWS
    draw_means1 = np.zeros(n_draws_v1)
    for d in range(n_draws_v1):
        rng = np.random.default_rng(SEED_V1 + STRIDE * d)
        rwL = [cap_renorm(rng.dirichlet(np.ones(len(l))), CAP_MULT / len(l)) if len(l) else np.zeros(0) for l in L]
        rwS = [cap_renorm(rng.dirichlet(np.ones(len(l))), CAP_MULT / len(l)) if len(l) else np.zeros(0) for l in S]
        a = simulate_book(mds, L, rwL, "")
        b = simulate_book(mds, S, rwS, "")
        draw_means1[d] = net_from(*a[:3], *b[:3]).mean()
    pct1c = E.percentile_vs_random(float(net1.mean()), draw_means1)

    out["V1"] = {"metrics": summ(m1), "bar_a": bars1a, "bar_b": b1b,
                 "bar_c": {"percentile": pct1c, "threshold": BAR_PCT_C,
                           "pass": bool(pct1c >= BAR_PCT_C), "n_draws": n_draws_v1,
                           "random_mean": float(draw_means1.mean())},
                 "verdict": "PASS" if (bars1a["ALL_PASS"] and b1b["pass"] and pct1c >= BAR_PCT_C) else "FAIL",
                 "worst3": worst(net1, keys),
                 "worst_baseline_5_mean": capture_ratio(net1, keys)[0],
                 "monthly": dict(zip(keys, map(float, net1)))}
    print(f"V1: a={bars1a['ALL_PASS']} mean {m1['mean_monthly_net']*100:.3f}%/mo "
          f"t {m1['t_stat_excess']:.3f} pctA {m1['percentile_vs_random']:.1f} maxDD "
          f"{m1['max_drawdown']*100:.2f}% | b {b1b['mean']*100:+.3f}% t {b1b['t']:.2f} "
          f"| c pct {pct1c:.2f} | {out['V1']['verdict']} [{round(time.time()-t0)}s]", flush=True)

    # ---- V2
    cald, daily = strategy_daily_returns(panel, L, S, None, dec, ee)
    scales = vol_target_scales(cald, daily, dec)
    bl_pnl, br_pnl = gl - cl, (-gs - bs - cs)
    net2 = v2_net(bl_pnl, br_pnl, net0, scales)
    m2 = series_metrics(net2, draws_mom)
    bars2a = dev_bars_a(m2)
    b2b = paired(net2 - net0)
    pct2a = E.percentile_vs_random(float(net2.mean()), draws_mom.mean(axis=1))
    draw_means2 = np.zeros(N_DRAWS)
    for d in range(N_DRAWS):
        rng = np.random.default_rng(SEED_V2 + STRIDE * d)
        draw_means2[d] = v2_net(bl_pnl, br_pnl, net0, rng.permutation(scales)).mean()
    pct2c = E.percentile_vs_random(float(net2.mean()), draw_means2)
    out["V2"] = {"metrics": summ(m2), "bar_a": bars2a, "bar_b": b2b,
                 "bar_c": {"percentile": pct2c, "threshold": BAR_PCT_C,
                           "pass": bool(pct2c >= BAR_PCT_C), "n_draws": N_DRAWS,
                           "random_mean": float(draw_means2.mean())},
                 "verdict": "PASS" if (bars2a["ALL_PASS"] and b2b["pass"] and pct2c >= BAR_PCT_C) else "FAIL",
                 "worst3": worst(net2, keys),
                 "worst_baseline_5_mean": capture_ratio(net2, keys)[0],
                 "scales": dict(zip(keys, map(float, scales))),
                 "scale_stats": {"min": float(scales.min()), "max": float(scales.max()),
                                 "mean": float(scales.mean()),
                                 "n_clamped_lo": int((scales <= 0.2500001).sum()),
                                 "n_clamped_hi": int((scales >= 1.4999999).sum())},
                 "monthly": dict(zip(keys, map(float, net2)))}
    print(f"V2: a={bars2a['ALL_PASS']} mean {m2['mean_monthly_net']*100:.3f}%/mo "
          f"t {m2['t_stat_excess']:.3f} pctA {m2['percentile_vs_random']:.1f} maxDD "
          f"{m2['max_drawdown']*100:.2f}% | b {b2b['mean']*100:+.3f}% t {b2b['t']:.2f} "
          f"| c pct {pct2c:.2f} | {out['V2']['verdict']} scales {out['V2']['scale_stats']}", flush=True)

    # ---- Q3 comparison table
    def crash(net):
        return {"sharpe": series_metrics(net, draws_mom)["sharpe_net"]
                if net is net0 else
                (m1 if net is net1 else m2)["sharpe_net"],
                "max_drawdown": E.max_drawdown(net),
                "worst3": worst(net, keys),
                "worst_month": float(net.min())}
    q3 = {"baseline": crash(net0), "V1": crash(net1), "V2": crash(net2)}
    # worst-3-month calendar agreement baseline vs variants
    w3b = [k for k, _ in q3["baseline"]["worst3"]]
    q3["V1"]["worst3_same_months_as_baseline"] = [k for k, _ in q3["V1"]["worst3"] if k in w3b]
    q3["V2"]["worst3_same_months_as_baseline"] = [k for k, _ in q3["V2"]["worst3"] if k in w3b]
    # worst 5 baseline months: mean strategy return in them
    order5 = [i for i, _ in sorted(enumerate(net0), key=lambda x: x[1])[:5]]
    q3["worst_baseline_5_months"] = {"months": [keys[i] for i in order5],
                                     "baseline_mean": float(net0[order5].mean()),
                                     "V1_mean": float(net1[order5].mean()),
                                     "V2_mean": float(net2[order5].mean())}
    # drawdown episode dates
    def dd_episode(net):
        eq = np.cumprod(1 + net); peak = np.maximum.accumulate(np.concatenate([[1.0], eq]))[1:]
        dd = eq / peak - 1; i = int(np.argmin(dd)); j = int(np.argmax(eq[:i + 1])) if i > 0 else 0
        return {"trough": keys[i], "peak_run": keys[j], "dd": float(dd[i])}
    q3["dd_episode_baseline"] = dd_episode(net0)
    q3["dd_episode_V1"] = dd_episode(net1)
    q3["dd_episode_V2"] = dd_episode(net2)
    out["Q3"] = q3

    out["determinism_sha256"] = hashlib.sha256(
        np.round(np.concatenate([net0, net1, net2]), 12).tobytes()).hexdigest()
    out["runtime_s"] = round(time.time() - t0, 1)
    p = HERE / "research" / "volsize-001" / "results_volsize.json"
    p.write_text(json.dumps(out, indent=1, default=float))
    print("WROTE", p, flush=True)
    print(json.dumps({"Q3": q3, "det": out["determinism_sha256"]}, indent=1, default=float), flush=True)


if __name__ == "__main__":
    main()
