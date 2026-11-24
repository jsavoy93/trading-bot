"""RSI2RANDOM-001 — audit of the RSI2REGIME-001 random-portfolio baseline (research only).

Reuses the RSI2REGIME-001 harness (same data loader, same indicators, same S1a exits)
via importlib, then:
  A. recomputes the trade-level t_vs_base properly from the spread of the random draws;
  B. recomputes the portfolio-level strategy-vs-random t from the spread of the 100 draws
     (fixing the sqrt(1/n_draws) uncertainty-vs-noise-variance error in monthly_diff);
  C. runs sanity checks on the harness: a BAD entry (RSI2 > 90) that must lose, and a
     LOOKAHEAD cheat entry (forward 5-day return ranking) that must crush random;
  D. decomposes the DEV return: exit-only (random entries, S1a exits), time-only exit with
     RSI2 entries, buy-and-hold, and matched-exposure random draws.

DEV window 2021-01-01..2024-12-31 only; the holdout is never loaded.
Cache /tmp/rsi2random_tmp (shared with RSI2REGIME via a symlink-free re-fetch would
double-download, so we point the module's CACHE at /tmp/rsi2regime_tmp when it exists).

Usage: python3 research/rsi2random-001/run_rsi2random.py
"""
from __future__ import annotations
import importlib.util, json, os, sys
import numpy as np
import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.join(REPO, "research", "rsi2regime-001", "run_rsi2regime.py")

def load_harness():
    spec = importlib.util.spec_from_file_location("rsi2regime_harness", SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # separate cache dir for this job (deleted before reporting); same loader code
    os.makedirs("/tmp/rsi2random_tmp", exist_ok=True)
    mod.CACHE = "/tmp/rsi2random_tmp"
    return mod

H = load_harness()
COST = H.COST
DEV_START, DEV_END = H.DEV_START, H.DEV_END
SEED = 20261123
N_DRAWS_TRADE = 1000
N_DRAWS_PORT = 100

LOG = []
def log(*a):
    s = " ".join(str(x) for x in a)
    LOG.append(s)
    print(s, flush=True)


# ---------------------------------------------------------------- data (same path as RSI2REGIME-001)
def get_data():
    spy = H.load_spy(log)
    bars = H.prepare(H.get_live_universe(), "yahoo", log)
    log(f"LIVE universe: {len(bars)} symbols with usable DEV data; SPY {spy.index[0].date()}..{spy.index[-1].date()}")
    sig = H.build_signal_sets(bars, spy)
    return spy, bars, sig


def signal_mask(sig, sym, variant="V1", extra=None):
    s = sig[sym]
    sel = s["elig"].copy()
    if variant == "V0":
        sel &= s["spy_ok"]
    if extra is not None:
        sel = sel & extra
    return sel


def carry_masks(bars, sig, name, extra_by_sym):
    """Return a sig dict whose 'low' mask is replaced by `extra_by_sym` (so the unmodified
    RSI2REGIME basket_level()/portfolio engines can be reused verbatim as the harness
    for alternative entry rules)."""
    out = {}
    for sym in sig:
        d = dict(sig[sym])
        d["low"] = extra_by_sym.get(sym, np.zeros(len(d["elig"]), bool))
        out[sym] = d
    return out


def basket_stats(bars, sig_variant, n_draws=N_DRAWS_TRADE, seed=SEED, tag=""):
    """Reimpl of RSI2REGIME-001 basket_level() for ONE variant, returning both the reported
    t (spread/sqrt(n_draws)) and the corrected t (spread alone), plus the draw distribution."""
    rng = np.random.default_rng(seed)
    hyp_by_sym, kept_all = {}, []
    for sym, df in bars.items():
        s = sig_variant[sym]
        sel = s["elig"] & s["low"]
        sig_idx = np.where(sel)[0]
        if len(sig_idx) == 0:
            continue
        hyp = H.hypothetical_trades(df, sig_idx, partial_2r=False)
        hyp_by_sym[sym] = (df, np.where(s["elig"])[0], hyp, len(sig_idx))
        kept_all.extend([(e, x, g, r, t, sym) for (e, x, g, r, t) in H.anti_overlap(hyp, sig_idx.tolist())])
    dates = {s: df.index for s, df in bars.items()}
    bt = H.basket_trades(kept_all, dates)
    m = H.trade_metrics(bt)
    draw_means, draw_sizes, draw_mix = [], [], []
    for _ in range(n_draws):
        pooled = []
        for sym, (df, elig_idx, hyp, k) in hyp_by_sym.items():
            if k == 0 or len(elig_idx) == 0:
                continue
            pick = rng.choice(len(elig_idx), size=min(k, len(elig_idx)), replace=False)
            pooled.extend([(e, x, g, r, t, sym) for (e, x, g, r, t) in
                           H.anti_overlap(hyp, sorted(int(elig_idx[i]) for i in pick))])
        if pooled:
            by_day = {}
            for (e, x, g, r, t, sym) in pooled:
                by_day.setdefault(dates[sym][e], []).append(g)
            # same basket construction as the strategy side: a calendar entry day with its
            # realized (not anti-overlapped) fills
            draw_means.append(float(np.mean([np.mean(v) for v in by_day.values()])) - 2 * COST)
            draw_sizes.append(len(by_day))
            draw_mix.append(float(np.mean([len(v) for v in by_day.values()])))
    dm = np.array(draw_means)
    excess = m["mean_net"] - dm.mean()
    sd = dm.std(ddof=1)
    t_reported = excess / (sd / np.sqrt(len(dm)))       # RSI2REGIME-001 formula
    t_corrected = excess / sd                           # uncertainty of the baseline mean
    pct = float((dm < m["mean_net"]).mean() * 100)
    out = {"n": int(m["n"]), "mean_net": float(m["mean_net"]), "baseline_mean": float(dm.mean()),
           "baseline_sd_draws": float(sd), "excess": float(excess),
           "t_reported": float(t_reported), "t_corrected": float(t_corrected), "percentile": pct,
           "avg_basket": float(np.mean([int(k[5][6:]) for k in bt])) if bt else 0.0,
           "draw_basket_mean": float(np.mean(draw_sizes)) if draw_sizes else None,
           "draw_mix_mean": float(np.mean(draw_mix)) if draw_mix else None,
           "win_rate": m.get("win_rate"), "avg_hold": m.get("avg_hold")}
    log(f"  [{tag}] n={out['n']} mean={out['mean_net']*100:.3f}%/trade base={out['baseline_mean']*100:.3f}% "
        f"excess={out['excess']*100:.3f}% sd_draws={out['baseline_sd_draws']*100:.3f}% "
        f"t_reported={out['t_reported']:.2f} t_corrected={out['t_corrected']:.2f} pct={out['percentile']:.1f} "
        f"basket={out['avg_basket']:.1f}vs{out['draw_basket_mean']} mix={out['draw_mix_mean']}")
    return out, bt, dm


def main():
    out = {}
    spy, bars, sig = get_data()
    dates = {s: df.index for s, df in bars.items()}

    # ---------------- A/Q2: corrected trade-level t for the six books is portfolio-level;
    # here do the two basket-level rows quoted in RSI2REGIME-001 (V0/V1) + the two +2R rows.
    log("== A. trade-level basket rows (V0, V1) recomputed")
    res = {}
    for v in ["V0", "V1"]:
        sigv = carry_masks(bars, sig, v, {sym: sig[sym]["low"] for sym in sig}) if v == "V0" else sig
        # V0: restrict eligibility to SPY>SMA200 days (same as basket_level does)
        if v == "V0":
            sigv = {sym: {**sig[sym], "elig": sig[sym]["elig"] & sig[sym]["spy_ok"]} for sym in sig}
            for sym in sigv:
                sigv[sym]["spy_ok"] = np.ones(len(sigv[sym]["elig"]), bool)
        o, bt, dm = basket_stats(bars, sigv, tag=f"basket {v}")
        out[f"basket_recomputed_{v}"] = o
        out[f"basket_draws_{v}"] = {"mean": float(dm.mean()), "sd": float(dm.std(ddof=1)),
                                    "p1": float(np.percentile(dm, 1)), "p99": float(np.percentile(dm, 99)),
                                    "min": float(dm.min()), "max": float(dm.max())}

    # ---------------- B/Q2: portfolio-level corrected t for the six books
    log("== B. portfolio-level: strategy vs random, 100 draws, corrected t")
    port = {}
    for v in ["V0", "V1"]:
        for mo in (4, 8, 12):
            p = H.portfolio(sig, bars, mo, v, log=log)
            p.pop("trades", None)
            p["monthly"] = {str(d.date())[:7]: float(x) for d, x in p["monthly"].items()}
            rb = H.portfolio_random(sig, bars, mo, v, n_draws=N_DRAWS_PORT, log=log)
            sstrat = pd.Series({pd.Timestamp(k + "-01"): x for k, x in p["monthly"].items()}).sort_index()
            ms = rb["mean_series"].sort_index()
            common = sstrat.index.intersection(ms.index)
            d = (sstrat.reindex(common) - ms.reindex(common)).dropna()
            sd_draws = float(rb["draw_means"].std(ddof=1))
            mean_diff = float(d.mean())
            t_reported = mean_diff / (d.std(ddof=1) / np.sqrt(len(d)))   # monthly_diff()
            t_corrected = mean_diff / sd_draws                            # draw-spread
            pctile = float((rb["draw_means"] < p["mean_monthly"]).mean() * 100)
            z_monthly_se = mean_diff / (d.std(ddof=1) / np.sqrt(len(d)))
            row = {"mean_monthly": p["mean_monthly"], "cagr": p.get("cagr"), "sharpe": p.get("sharpe"),
                   "maxdd": p.get("maxdd"), "total_trades": p.get("total_trades"),
                   "random_mean_monthly": float(ms.mean()), "random_sd_draws": sd_draws,
                   "mean_diff_monthly": mean_diff, "t_reported": float(t_reported),
                   "t_corrected": float(t_corrected), "pctile_strat_vs_draws": pctile,
                   "months": int(len(d))}
            port[f"{v}_{mo}"] = row
            log(f"  PORT {v} mo{mo}: strat={p['mean_monthly']*100:.4f}%/mo rand={ms.mean()*100:.4f}% "
                f"diff={mean_diff*100:.4f}%/mo sd_draws={sd_draws*100:.4f}% t_rep={t_reported:.2f} "
                f"t_corr={t_corrected:.2f} pctile={pctile:.0f}")
    out["portfolio_recomputed"] = port

    # ---------------- helpers shared by C (sanity) and D (decomposition)
    def thread_returns(mask_by_sym):
        """Per-symbol anti-overlapped S1a threads (same unit as RSI2REGIME-001's
        per-symbol view), net of costs. Used only where both sides of a comparison use it."""
        keep, rets = [], []
        for sym, df in bars.items():
            m = mask_by_sym[sym]
            idx = np.where(m & sig[sym]["elig"])[0]
            if len(idx) == 0:
                continue
            hyp = H.hypothetical_trades(df, idx)   # bars already truncated at DEV_END
            for (e, x, g, r, t) in H.anti_overlap(hyp, idx.tolist()):
                keep.append((sym, dates[sym][e]))
                rets.append(g - 2 * COST)
        return np.array(rets), keep

    def day_means(mask_by_sym):
        """One value per filled calendar entry day: mean realized net return of that day's fills."""
        rets, keep = thread_returns(mask_by_sym)
        by_day = {}
        for r, (sym, d) in zip(rets, keep):
            by_day.setdefault(d, []).append(r)
        return {d: float(np.mean(v)) for d, v in by_day.items()}

    def rule_stats(mask_by_sym, n_draws=N_DRAWS_TRADE, seed=SEED, tag="", exposure_matched=False):
        """Strategy day-baskets vs random day-baskets, reported t and corrected t.
        Random protocol = RSI2REGIME-001: per symbol draw k_sym eligible days without
        replacement, apply anti-overlap, then one value per entry day.
        exposure_matched=True: also draws ONLY on the strategy's own entry days."""
        rng = np.random.default_rng(seed)
        hs = day_means(mask_by_sym)
        strat = np.array(list(hs.values()))
        strat_days = set(hs)
        hyp_by_sym = {}
        for sym, df in bars.items():
            idx = np.where(mask_by_sym[sym] & sig[sym]["elig"])[0]
            if len(idx) == 0:
                continue
            hyp_by_sym[sym] = (np.where(sig[sym]["elig"])[0], H.hypothetical_trades(df, np.where(sig[sym]["elig"])[0]),
                               len(np.where(mask_by_sym[sym] & sig[sym]["elig"])[0]))
        draws = []
        for _ in range(n_draws):
            by_day = {}
            all_days = []
            for sym, (elig_idx, hyp_all, k) in hyp_by_sym.items():
                if k == 0 or len(elig_idx) == 0:
                    continue
                pool = elig_idx
                if exposure_matched:
                    pool = np.array([i for i in elig_idx if dates[sym][i] in strat_days])
                    if len(pool) == 0:
                        continue
                pick = rng.choice(len(pool), size=min(k, len(pool)), replace=False)
                for i in pick:
                    t = int(pool[i])
                    if t not in hyp_all:
                        continue
                    e, x, g, r = hyp_all[t]
                    if e >= len(dates[sym]) or dates[sym][e] > DEV_END:
                        continue
                    by_day.setdefault(dates[sym][e], []).append(g - 2 * COST)
            if by_day:
                # anti-overlap per symbol is already applied through hyp_all (one trade at a time)
                draws.append(float(np.mean([np.mean(v) for v in by_day.values()])))
        dm = np.array(draws)
        m = float(strat.mean())
        excess = m - dm.mean()
        sd = dm.std(ddof=1)
        o = {"n_days": int(len(strat)), "mean_net": m, "baseline_mean": float(dm.mean()),
             "baseline_sd_draws": float(sd), "excess": float(excess),
             "t_reported": float(excess / (sd / np.sqrt(len(dm)))),
             "t_corrected": float(excess / sd),
             "percentile": float((dm < m).mean() * 100), "n_draws": int(len(dm)),
             "exposure_matched": exposure_matched}
        log(f"  [{tag}] days={o['n_days']} mean={m*100:.3f}% base={dm.mean()*100:.3f}% "
            f"sd_draws={sd*100:.3f}% t_rep={o['t_reported']:.2f} t_corr={o['t_corrected']:.2f} "
            f"pct={o['percentile']:.1f}")
        return o, dm

    log("== C. sanity checks (harness must fail BAD and pass LOOKAHEAD)")
    checks = {}
    bad = {sym: (bars[sym]["rsi2"].to_numpy() > 90.0) for sym in bars}
    o, _ = rule_stats(bad, tag="BAD rsi2>90"); checks["bad_rsi2_gt90"] = o
    up = {}
    for sym in bars:
        c = bars[sym]["close"]
        up[sym] = ((c / c.shift(2) - 1.0).to_numpy() > 0.08)
    o, _ = rule_stats(up, tag="BAD after +8%/2d"); checks["bad_after_big_up_day"] = o
    look = {}
    for sym in bars:
        c = bars[sym]["close"]
        look[sym] = ((c.shift(-5) / c - 1.0).to_numpy() >= 0.05)   # deliberate lookahead
    o, _ = rule_stats(look, tag="LOOKAHEAD fwd5d>=5%"); checks["lookahead_fwd5d_ge5pct"] = o
    # control: a truly random entry under the same protocol must sit at ~0 excess, pct ~50
    ctrl = {}
    rngc = np.random.default_rng(SEED + 1)
    for sym in bars:
        elig = np.where(sig[sym]["elig"])[0]
        mask = np.zeros(len(sig[sym]["elig"]), bool)
        k = int((sig[sym]["elig"] & sig[sym]["low"]).sum())
        if k and len(elig):
            mask[rngc.choice(elig, size=min(k, len(elig)), replace=False)] = True
        ctrl[sym] = mask
    o, _ = rule_stats(ctrl, tag="CONTROL random"); checks["control_random_entries"] = o
    out["sanity"] = checks

    log("== C2. exposure-matched random baseline for the real S1a entry")
    o, _ = rule_stats({sym: sig[sym]["low"] for sym in sig}, tag="S1a exposure-matched", exposure_matched=True)
    out["s1a_exposure_matched"] = o

    # ---------------- D/Q4: decomposition
    log("== D. decomposition: exits vs entry")
    dec = {}
    o_sig, _ = rule_stats({sym: sig[sym]["low"] for sym in sig}, tag="RSI2 entries + S1a exits")
    rng_ = np.random.default_rng(SEED)
    ctrl2 = {}
    for sym in bars:
        elig = np.where(sig[sym]["elig"])[0]
        mask = np.zeros(len(sig[sym]["elig"]), bool)
        k = int((sig[sym]["elig"] & sig[sym]["low"]).sum())
        if k and len(elig):
            mask[rng_.choice(elig, size=min(k, len(elig)), replace=False)] = True
        ctrl2[sym] = mask
    o_rnd, _ = rule_stats(ctrl2, tag="RANDOM entries + S1a exits")
    dec["s1a_exits"] = {"rsi2_entries": o_sig, "random_entries": o_rnd}

    def time_only_returns(mask_by_sym):
        r = []
        for sym, df in bars.items():
            idx = np.where(mask_by_sym[sym] & sig[sym]["elig"])[0]
            keep, last = [], -1
            for i in idx:
                if i + 1 > last:
                    keep.append(i); last = i + 15
            c = df["close"].to_numpy(); o = df["open"].to_numpy()
            for i in keep:
                e = i + 1
                if e >= len(c):
                    continue
                x = min(e + 14, len(c) - 1)
                r.append(c[x] / o[e] - 1.0)
        return np.array(r) - 2 * COST
    t_sig = time_only_returns({sym: sig[sym]["low"] for sym in sig})
    t_rand = time_only_returns(ctrl2)
    dec["time_only_exit"] = {"rsi2_entries": {"n": int(len(t_sig)), "mean": float(t_sig.mean())},
                             "random_entries": {"n": int(len(t_rand)), "mean": float(t_rand.mean())}}
    log(f"  TIME-ONLY exit (no stops): rsi2 n={len(t_sig)} mean={t_sig.mean()*100:.3f}% | "
        f"random n={len(t_rand)} mean={t_rand.mean()*100:.3f}%")
    bnh = []
    for sym, df in bars.items():
        d0 = df.index[df.index >= DEV_START]
        if len(d0) < 5:
            continue
        c = df["close"]
        bnh.append(float(c.iloc[-1] / c.loc[d0[0]] - 1.0))
    bnh = np.array(bnh)
    dec["buy_and_hold_equal_weight"] = {"n": int(len(bnh)), "mean_total": float(bnh.mean()),
                                        "median_total": float(np.median(bnh)),
                                        "pct_positive": float((bnh > 0).mean())}
    log(f"  BUY&HOLD EW {len(bnh)} names: mean {bnh.mean()*100:.1f}% median {np.median(bnh)*100:.1f}% "
        f"positive {100*(bnh>0).mean():.0f}%")
    out["decomposition"] = dec

    # ---------------- survivorship / universe lookahead
    log("== E. universe provenance")
    out["universe"] = {"n_symbols": len(bars), "source_file": H.UNIV_FILE}
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "results_rsi2random.json"), "w") as f:
        json.dump(out, f, indent=1, default=str)
    log("done -> results_rsi2random.json")


if __name__ == "__main__":
    main()
