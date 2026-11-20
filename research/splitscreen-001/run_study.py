"""LONGSHORT-001 — run the frozen dollar-neutral long/short study.

Usage:  python3 run_study.py [--panel PATH] [--out DIR]

Frozen order of operations (PREREGISTRATION.md):
  1. simulate + score the DEV window;
  2. ONLY if the variant passes every dev bar, simulate + score the HOLDOUT once;
  3. a variant that fails dev is REJECTED at dev and its holdout is never computed.

RESEARCH ONLY: reads a read-only parquet panel, writes JSON under
experiments/longshort_001/results/. No broker, no database, no orders.
"""
from __future__ import annotations

import argparse
import json
import platform
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

import engine as E

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"

DEV_START = pd.Period("2021-01", "M")
DEV_END = pd.Period("2024-12", "M")
HOLD_START = pd.Period("2025-01", "M")
PANEL_LAST = pd.Timestamp("2026-09-29")

VARIANTS = ["MOM", "VOL"]


# ------------------------------------------------------------------ random
def random_monthly_nets(md_list, elig_list, n_leg_list, n_draws, seed):
    """1000 random long/short portfolios with identical rules to the strategy.

    Returns array (n_draws, n_months) of monthly NET returns.
    """
    n_months = len(md_list)
    out = np.zeros((n_draws, n_months))
    rng = np.random.default_rng(seed)
    for d in range(n_draws):
        prev_long = np.array([], dtype=int)
        prev_short = np.array([], dtype=int)
        for r in range(n_months):
            elig = elig_list[r]
            n_leg = n_leg_list[r]
            if len(elig) < E.MIN_ELIGIBLE or n_leg < 1:
                out[d, r] = 0.0
                prev_long = np.array([], dtype=int)
                prev_short = np.array([], dtype=int)
                continue
            long_sel = np.sort(rng.choice(elig, size=n_leg, replace=False))
            rest = np.setdiff1d(elig, long_sel, assume_unique=True)
            short_sel = np.sort(rng.choice(rest, size=n_leg, replace=False))
            md = md_list[r]
            ret = md.ret
            alive = md.last_close_idx == md.exit_idx
            w = 1.0 / n_leg

            l_gross = float(np.mean(ret[long_sel]))
            l_dead = int((~alive[long_sel]).sum())
            l_open = len(long_sel) - int(np.isin(long_sel, prev_long).sum())
            l_close = len(prev_long) - int(np.isin(prev_long, long_sel).sum())
            l_cost = E.COST_PER_SIDE * (l_open + l_close + l_dead) * w

            s_gross = float(np.mean(ret[short_sel]))
            s_borrow = E.BORROW_PER_YEAR * float(np.mean(md.held_days[short_sel])) / E.TRADING_DAYS_PER_YEAR
            s_dead = int((~alive[short_sel]).sum())
            s_open = len(short_sel) - int(np.isin(short_sel, prev_short).sum())
            s_close = len(prev_short) - int(np.isin(prev_short, short_sel).sum())
            s_cost = E.COST_PER_SIDE * (s_open + s_close + s_dead) * w

            out[d, r] = (l_gross - l_cost) + (-s_gross - s_borrow - s_cost)

            prev_long = long_sel[alive[long_sel]]
            prev_short = short_sel[alive[short_sel]]
    return out


# ------------------------------------------------------------------ metrics
def window_metrics(net, excess, rand_mean, long_pnl, short_pnl, draw_means):
    n = len(net)
    mean_net = float(np.mean(net))
    mean_excess = float(np.mean(excess))
    mean_rand = float(np.mean(rand_mean))
    vol = float(np.std(net, ddof=1))
    ann_vol = vol * float(np.sqrt(12))
    vol_excess = float(np.std(excess, ddof=1))
    ann_vol_excess = vol_excess * float(np.sqrt(12))
    ann_net = E.annualize_ret(mean_net)
    ann_rand = E.annualize_ret(mean_rand)
    ann_excess = E.annualize_ret(mean_excess)
    return {
        "n_months": n,
        "mean_monthly_net": mean_net,
        "annualized_net": ann_net,
        "mean_monthly_random": mean_rand,
        "annualized_random": ann_rand,
        "mean_monthly_excess": mean_excess,
        "annualized_excess": ann_excess,
        "monthly_vol": vol,
        "annualized_vol": float(ann_vol),
        "monthly_vol_excess": vol_excess,
        "annualized_vol_excess": float(ann_vol_excess),
        "sharpe_net": float(ann_net / ann_vol) if ann_vol > 0 else float("nan"),
        "sharpe_excess": float(ann_excess / ann_vol_excess) if ann_vol_excess > 0 else float("nan"),
        "max_drawdown": E.max_drawdown(net),
        "monthly_win_rate": float(np.mean(net > 0)),
        "mean_long_leg_contribution": float(np.mean(long_pnl)),
        "mean_short_leg_contribution": float(np.mean(short_pnl)),
        "t_stat_excess": E.t_stat(excess),
        "percentile_vs_random": E.percentile_vs_random(mean_net, draw_means),
    }


def holdout_allowed(bars: dict) -> bool:
    """FROZEN RULE: the holdout is computed ONLY for a variant that passes every
    dev bar. A dev failure means the holdout is never computed."""
    return bool(bars.get("ALL_PASS", False))


def dev_bars(m):
    bars = {
        "n_months>=24": bool(m["n_months"] >= 24),
        "mean_net>0": bool(m["mean_monthly_net"] > 0),
        "mean_excess>0": bool(m["mean_monthly_excess"] > 0),
        "percentile>=97.5": bool(m["percentile_vs_random"] >= 97.5),
        "t_stat>=2.24": bool(m["t_stat_excess"] >= 2.24),
    }
    bars["ALL_PASS"] = all(bars.values())
    return bars


def holdout_bars(m, k):
    from scipy import stats
    cut_pct = 100.0 * (1.0 - 0.05 / k)
    t_cut = 2.0 if k == 1 else float(stats.t.ppf(1 - 0.05 / (2 * k), m["n_months"] - 1))
    bars = {
        "n_months>=12": bool(m["n_months"] >= 12),
        "mean_net>0": bool(m["mean_monthly_net"] > 0),
        "mean_excess>0": bool(m["mean_monthly_excess"] > 0),
        "percentile>=%.1f" % cut_pct: bool(m["percentile_vs_random"] >= cut_pct),
        "t_stat>=%.4f" % t_cut: bool(m["t_stat_excess"] >= t_cut),
    }
    bars["ALL_PASS"] = all(bars.values())
    return bars, cut_pct, t_cut


# ------------------------------------------------------------------ main
def compute_holdout(md_list, dev_rows, hold_rows, legs_long, legs_short,
                    elig_list, n_leg_list, dev_passed, keys):
    """Compute the HOLDOUT window.

    FROZEN RULE: the holdout is computed ONLY when the variant passed every dev
    bar. When ``dev_passed`` is False this returns ``(None, None)`` WITHOUT
    simulating or scoring anything, so no holdout return can ever leak into the
    results of a dev-failing variant.

    Returns (metrics_dict, monthly_dict) or (None, None).
    """
    if not dev_passed:
        return None, None

    all_idx = list(dev_rows) + list(hold_rows)
    md_all = [md_list[i] for i in all_idx]
    long_all = E.simulate_leg(md_all, [legs_long[i] for i in all_idx], "long")
    short_all = E.simulate_leg(md_all, [legs_short[i] for i in all_idx], "short")
    net_all = E.net_series(long_all, short_all)
    draws_all = random_monthly_nets(md_all, [elig_list[i] for i in all_idx],
                                    [n_leg_list[i] for i in all_idx], E.N_DRAWS, E.SEED)
    rand_all = draws_all.mean(axis=0)
    h = np.arange(len(dev_rows), len(all_idx))
    hnet = net_all[h]
    hrand = rand_all[h]
    hm = window_metrics(hnet, hnet - hrand, hrand,
                        (long_all.net - long_all.cost)[h],
                        (-short_all.net - short_all.borrow - short_all.cost)[h],
                        draws_all[:, h].mean(axis=1))
    hm["bars"] = {}
    monthly = {
        "months": [keys[i] for i in hold_rows],
        "net": [float(x) for x in hnet],
        "excess": [float(x) for x in (hnet - hrand)],
        "random_mean": [float(x) for x in hrand],
        "long_leg": [float(x) for x in (long_all.net - long_all.cost)[h]],
        "short_leg": [float(x) for x in (-short_all.net - short_all.borrow - short_all.cost)[h]],
    }
    return hm, monthly


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", default=str(E.PANEL_DEFAULT))
    ap.add_argument("--out", default=str(RESULTS))
    args = ap.parse_args()
    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)

    print("loading panel ...", flush=True)
    panel = E.load_panel(Path(args.panel))
    months = E.build_months(panel)
    print(f"panel: {panel.dates.size} trading days, {panel.symbols.size} symbols", flush=True)

    usable = [m for m in months
              if m.exit_date <= PANEL_LAST
              and E.month_end_index(panel, m.period - 13) >= 0]
    print(f"usable months: {len(usable)} ({usable[0].key} .. {usable[-1].key})", flush=True)

    md_list = E.compute_month_data(panel, usable)
    keys = [m.key for m in usable]
    dev_rows = [i for i, m in enumerate(usable) if DEV_START <= m.period <= DEV_END]
    hold_rows = [i for i, m in enumerate(usable)
                 if m.period >= HOLD_START and m.exit_date <= PANEL_LAST]

    sha = subprocess.run(["sha256sum", str(args.panel)], capture_output=True, text=True).stdout.split()[0]
    results = {
        "seed": E.SEED,
        "n_draws": E.N_DRAWS,
        "panel_path": str(args.panel),
        "panel_sha256": sha,
        "python": platform.python_version(),
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "cost_per_side": E.COST_PER_SIDE,
        "borrow_per_year": E.BORROW_PER_YEAR,
        "months_used": {"total": len(usable), "dev": len(dev_rows), "holdout": len(hold_rows)},
        "dev_keys": [keys[i] for i in dev_rows],
        "holdout_keys": [keys[i] for i in hold_rows],
        "variants": {},
    }

    dev_passers = []
    for variant in VARIANTS:
        print(f"=== variant {variant} ===", flush=True)
        legs_long, legs_short, elig_list, n_leg_list = [], [], [], []
        for md in md_list:
            lg, sh, elig = E.select_legs(md, variant)
            legs_long.append(lg)
            legs_short.append(sh)
            elig_list.append(np.flatnonzero(elig))
            n_leg_list.append(len(lg))

        # ---------------- DEV ----------------
        md_dev = [md_list[i] for i in dev_rows]
        long_dev = E.simulate_leg(md_dev, [legs_long[i] for i in dev_rows], "long")
        short_dev = E.simulate_leg(md_dev, [legs_short[i] for i in dev_rows], "short")
        net_dev = E.net_series(long_dev, short_dev)
        long_dev_pnl = long_dev.net - long_dev.cost
        short_dev_pnl = -short_dev.net - short_dev.borrow - short_dev.cost

        print("running 1000 random draws (dev window) ...", flush=True)
        draws_dev = random_monthly_nets(md_dev, [elig_list[i] for i in dev_rows],
                                        [n_leg_list[i] for i in dev_rows], E.N_DRAWS, E.SEED)
        rand_dev = draws_dev.mean(axis=0)
        dev_m = window_metrics(net_dev, net_dev - rand_dev, rand_dev,
                               long_dev_pnl, short_dev_pnl, draws_dev.mean(axis=1))
        dev_m["bars"] = dev_bars(dev_m)

        vres = {"variant": variant, "windows": {"dev": dev_m}, "monthly": {}}
        vres["monthly"]["dev"] = {
            "months": [keys[i] for i in dev_rows],
            "net": [float(x) for x in net_dev],
            "excess": [float(x) for x in (net_dev - rand_dev)],
            "random_mean": [float(x) for x in rand_dev],
            "long_leg": [float(x) for x in long_dev_pnl],
            "short_leg": [float(x) for x in short_dev_pnl],
        }
        dev_passed = holdout_allowed(dev_m["bars"])
        if dev_passed:
            dev_passers.append(variant)

        # ---------------- HOLDOUT (only for dev passers, run once) ----------------
        hm, hold_monthly = compute_holdout(
            md_list, dev_rows, hold_rows, legs_long, legs_short,
            elig_list, n_leg_list, dev_passed, keys)
        if hm is not None:
            print("dev PASSED -> computing the holdout (once)", flush=True)
            vres["windows"]["holdout"] = hm
            vres["monthly"]["holdout"] = hold_monthly
        else:
            print("dev FAILED -> holdout NOT computed (frozen rule)", flush=True)
            vres["holdout_status"] = "NOT COMPUTED (variant failed the dev bar; frozen rule)"

        vres["_legs_long"] = [x.tolist() for x in legs_long]
        vres["_legs_short"] = [x.tolist() for x in legs_short]
        vres["leg_sizes"] = {
            "long": [int(len(x)) for x in legs_long],
            "short": [int(len(x)) for x in legs_short],
            "eligible": [int(len(x)) for x in elig_list],
        }
        results["variants"][variant] = vres
        print(json.dumps(vres["windows"], indent=2, default=str), flush=True)

    # ---- holdout bars for the passers (k = number of dev passers) ----
    k = len(dev_passers)
    if k:
        for variant in dev_passers:
            hm = results["variants"][variant]["windows"].get("holdout")
            if hm is None:
                continue
            bars, cut_pct, t_cut = holdout_bars(hm, k)
            hm["bars"] = bars
            hm["holdout_cutoffs"] = {"percentile_cut": cut_pct, "t_cut": t_cut, "k": k}
    results["dev_passers"] = dev_passers
    results["final_verdict"] = {
        v: ("PASS" if results["variants"][v]["windows"]["dev"]["bars"]["ALL_PASS"]
            and "holdout" in results["variants"][v]["windows"]
            and results["variants"][v]["windows"]["holdout"]["bars"].get("ALL_PASS")
            else "REJECTED")
        for v in VARIANTS
    }

    # ---- sample months for the independent hand check (dev window only) ----
    samples = {}
    for variant in VARIANTS:
        vres = results["variants"][variant]
        lg_all = vres["_legs_long"]
        sh_all = vres["_legs_short"]
        md_dev = [md_list[i] for i in dev_rows]
        long_dev = E.simulate_leg(md_dev, [lg_all[i] for i in dev_rows], "long")
        short_dev = E.simulate_leg(md_dev, [sh_all[i] for i in dev_rows], "short")
        net_dev = E.net_series(long_dev, short_dev)
        smp = {}
        picks = [("dev_first", dev_rows[0]),
                 ("dev_mid", dev_rows[len(dev_rows) // 2]),
                 ("dev_last", dev_rows[-1])]
        for label, i in picks:
            m = usable[i]
            d = md_list[i]
            legs = {"long": lg_all[i], "short": sh_all[i]}
            entry = {}
            for side in ("long", "short"):
                idxs = legs[side]
                entry[side] = {
                    "names": [str(panel.symbols[s]) for s in idxs],
                    "entry_open": [float(panel.open_m[m.entry_idx, s]) for s in idxs],
                    "exit_price": [float(panel.open_m[m.exit_idx, s]) if np.isfinite(panel.open_m[m.exit_idx, s])
                                   else float(panel.close_m[int(d.last_close_idx[s]), s]) for s in idxs],
                    "ret": [float(d.ret[s]) for s in idxs],
                    "held_days": [int(d.held_days[s]) for s in idxs],
                    "alive": [bool(d.last_close_idx[s] == d.exit_idx) for s in idxs],
                }
            smp[label] = {
                "month_key": m.key,
                "decision_date": str(m.decision_date.date()),
                "entry_date": str(m.entry_date.date()),
                "exit_date": str(m.exit_date.date()),
                "n_leg": len(legs["long"]),
                **entry,
                "net": float(net_dev[dev_rows.index(i)]),
                "cost_long": float(long_dev.cost[dev_rows.index(i)]),
                "cost_short": float(short_dev.cost[dev_rows.index(i)]),
                "borrow": float(short_dev.borrow[dev_rows.index(i)]),
            }
        samples[variant] = smp
    results["sample_months"] = samples

    (outdir / "results.json").write_text(json.dumps(results, indent=2, default=str))
    print("wrote", outdir / "results.json", flush=True)
    print(json.dumps(results["final_verdict"], indent=2), flush=True)


if __name__ == "__main__":
    main()
