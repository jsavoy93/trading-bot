"""RSI2RANDOM-001 — audit of the RSI2REGIME-001 random-portfolio baseline (research only).

Reuses ONLY the RSI2REGIME-001 data loader / indicator / universe code via importlib (so the
bars are byte-identical to the audited study); every statistic below is recomputed here so
the audited formulas can be compared with the corrected ones.

Defects reproduced and fixed
  D1  basket_level(): t = excess / (sd(draw_means)/sqrt(n_draws)) — i.e. the standard error
      of the mean of 1000 draws, not the draw-to-draw spread. Inflates t by sqrt(1000)=31.6.
      e.g. basket V0 t_vs_base 31.34 -> corrected 31.34/31.62 = 0.99 (percentile 83.5).
  D2  portfolio_random(): eligible names per day = elig & low (RSI2<10) on EVERY day, so
      the "random" portfolio only ever buys names that are in the RSI(2) signal state.
  D3  monthly_diff(): t of the monthly strategy-minus-(mean random) spread uses the monthly
      time-series dispersion as its standard error; the sampling uncertainty of the random
      baseline itself is ignored.

Fair baseline used here (Q1/Q2/Q4)
  Same universe (live 353), same eligibility (elig: RSI2/ATR/vol20 finite, vol20>=1M,
  close>=$5, warmup, DEV 2021-01-01..2024-12-31), same S1a exits (2xATR stop, 3xATR
  chandelier, 15-day time exit, no +2R partial), same 10 bps/side, entry open t+1,
  same number of entries per symbol, same slot cap / max_open / rsi2_cap portfolio mechanics,
  and an EXPOSURE-MATCHED variant that only allows random entries on days the strategy
  itself has a fill. No lookahead (all masks use bars <= t only; the only lookahead mask
  is the deliberate cheat), no survivorship change vs the audited study (universe file
  unchanged - documented in Q1).

Usage: python3 research/rsi2random-001/run_rsi2random.py [--quick]
"""
from __future__ import annotations
import importlib.util, json, os, sys
import numpy as np
import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.join(REPO, "research", "rsi2regime-001", "run_rsi2regime.py")
CACHE = "/tmp/rsi2random_tmp"
RSI2_CAP = 4
SEED = 20261123

def load_base():
    spec = importlib.util.spec_from_file_location("rsi2regime_harness", SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    os.makedirs(CACHE, exist_ok=True)
    mod.CACHE = CACHE
    return mod

H = load_base()
COST = H.COST
DEV_START, DEV_END = H.DEV_START, H.DEV_END
QUICK = "--quick" in sys.argv

LOG = []
def log(*a):
    s = " ".join(str(x) for x in a)
    LOG.append(s)
    print(s, flush=True)


# ------------------------------------------------------------------ engine (self-contained)
def make_trades(df, signal_idx, n_universe_extra=0):
    """S1a trades, no anti-overlap. Same mechanics as H.hypothetical_trades (verified against
    it in check_engine()). Returns dict t -> (entry_i, exit_i, gross, reason)."""
    n = len(df)
    o = df["open"].to_numpy(); c = df["close"].to_numpy(); atr = df["atr14"].to_numpy()
    out = {}
    for t in signal_idx:
        t = int(t); e = t + 1
        if e >= n or not np.isfinite(o[e]) or o[e] <= 0 or not np.isfinite(atr[t]):
            continue
        entry = o[e]; stop = entry - 2.0 * atr[t]; max_close = c[e]
        last = min(e + 15, n - 1); exit_i = exit_px = None; reason = None
        for s in range(e, last + 1):
            a = atr[s] if np.isfinite(atr[s]) else atr[t]
            eff = max(stop, max_close - 3.0 * a)
            if c[s] <= eff:
                exit_i, exit_px = s, c[s]; reason = "stop" if eff == stop else "trail"; break
            max_close = max(max_close, c[s])
            if s == last:
                exit_i, exit_px, reason = s, c[s], "time"
        if exit_i is None:
            exit_i, exit_px, reason = last, c[last], "time"
        out[t] = (e, int(exit_i), exit_px / entry - 1.0, reason)
    return out


def keeped(hyp, days):
    """anti-overlap: one position per symbol at a time (identical to H.anti_overlap)."""
    kept, last = [], -1
    for t in days:
        if t not in hyp:
            continue
        e, x, g, r = hyp[t]
        if e > last:
            kept.append(t); last = x
    return kept


class Book:
    """Everything needed to price any entry mask for one symbol."""
    def __init__(self, bars, sig):
        self.bars = bars; self.sig = sig
        self.dates = {s: df.index for s, df in bars.items()}
        self.elig = {s: np.where(sig[s]["elig"])[0] for s in bars}
        self.elig_set = {s: set(self.elig[s].tolist()) for s in bars}
        self.hyp = {s: make_trades(bars[s], self.elig[s]) for s in bars}  # trade from EVERY eligible day
        self.n = {s: len(self.elig[s]) for s in bars}

    def day_means(self, mask_by_sym, restrict_days=None):
        """One value per calendar entry day: mean net return of that day's anti-overlapped fills."""
        by_day = {}
        for s, df in self.bars.items():
            m = mask_by_sym.get(s)
            if m is None:
                continue
            idx = sorted(set(np.where(m)[0].tolist()) & self.elig_set[s])
            if not idx:
                continue
            kept = keeped(self.hyp[s], idx)
            for t in kept:
                e, x, g, r = self.hyp[s][t]
                d = self.dates[s][e]
                if restrict_days is not None and d not in restrict_days:
                    continue
                by_day.setdefault(d, []).append(g - 2 * COST)
        return {d: float(np.mean(v)) for d, v in by_day.items()}


def compare(book, strat_mask, draw_fn, n_draws, tag, strat_days=None):
    """Draw random entry masks `n_draws` times, compare day-basket means.
    t_corrected = (strat_mean - mean(draw means)) / sd(draw means)   <- the honest statistic
    t_reported  = same numerator / (sd(draw means)/sqrt(n_draws))    <- RSI2REGIME-001 formula
    Also returns the empirical percentile of the strategy within the draw distribution."""
    hs = book.day_means(strat_mask)
    strat_days = set(hs) if strat_days is None else set(strat_days)
    s_vals = np.array(list(hs.values())) if hs else np.array([0.0])
    if strat_days:
        s_vals = np.array([hs.get(d, np.nan) for d in strat_days])
        s_vals = s_vals[np.isfinite(s_vals)]
    m = float(s_vals.mean())
    means, sizes = [], []
    for k in range(n_draws):
        dm = book.day_means(draw_fn(k, strat_days))
        if dm:
            v = np.array(list(dm.values()))
            means.append(float(v.mean())); sizes.append(len(v))
    dmn = np.array(means)
    sd = dmn.std(ddof=1)
    excess = m - dmn.mean()
    o = {"n_fills": int(len(s_vals)), "n_days": int(len(strat_days)), "mean_net": m,
         "baseline_mean": float(dmn.mean()), "baseline_sd_draws": float(sd),
         "baseline_p5": float(np.percentile(dmn, 5)), "baseline_p95": float(np.percentile(dmn, 95)),
         "excess": float(excess),
         "t_corrected": float(excess / sd) if sd > 0 else None,
         "t_reported_faulty": float(excess / (sd / np.sqrt(len(dmn)))) if sd > 0 else None,
         "percentile": float((dmn < m).mean() * 100),
         "draw_days_mean": float(np.mean(sizes)) if sizes else None,
         "n_draws": len(dmn)}
    log(f"  [{tag}] fills={o['n_fills']} days={o['n_days']} strat={m*100:+.3f}% "
        f"base={o['baseline_mean']*100:+.3f}% sd_draws={sd*100:.3f}% excess={excess*100:+.3f}% "
        f"t_corr={o['t_corrected']:+.2f} t_faulty={o['t_reported_faulty']:+.1f} pct={o['percentile']:.1f}")
    return o, dmn


def per_symbol_random_masks(book, k_by_sym, seed):
    """Same protocol as RSI2REGIME-001 basket_level: per symbol draw k_sym eligible days
    without replacement. k_sym = number of signal days that symbol has."""
    rng = np.random.default_rng(seed)
    out = {}
    for s in book.bars:
        k = int(k_by_sym.get(s, 0))
        k = k if k > 0 else int((sig[s]["elig"] & sig[s]["low"]).sum())
        pool = book.elig[s]
        m = np.zeros(len(book.bars[s]), bool)
        if k and len(pool):
            m[rng.choice(pool, size=min(k, len(pool)), replace=False)] = True
        out[s] = m
    return out


def matched_days_random_masks(book, k_by_sym, allowed_days, seed):
    """Exposure-matched: draw k_sym entry days from the symbol's own strategy entry days."""
    rng = np.random.default_rng(seed)
    out = {}
    for s in book.bars:
        k = int(k_by_sym.get(s, 0))
        k = k if k > 0 else int((sig[s]["elig"] & sig[s]["low"]).sum())
        pool = np.array([i for i in book.elig[s] if book.dates[s][i] in allowed_days])
        m = np.zeros(len(book.bars[s]), bool)
        if k and len(pool):
            m[rng.choice(pool, size=min(k, len(pool)), replace=False)] = True
        out[s] = m
    return out


def mask_from_signal(book, sig, variant="V1", extra=None):
    out = {}
    for s in book.bars:
        d = sig[s]
        sel = d["elig"] & d["low"]
        if variant == "V0":
            sel = sel & d["spy_ok"]
        if extra is not None:
            e = extra.get(s)
            if e is not None:
                sel = sel & e
            else:
                sel = np.zeros_like(sel)
        out[s] = sel
    return out


def portfolio_run(book, entry_days_by_sym, max_open, rsi2_cap=4, variant="V1", seed=None,
                  random_pick=False):
    """Daily slot engine replicating the audited RSI2REGIME-001 portfolio() mechanics:
    signal at close t -> fill at open t+1 if a slot is free (cap rsi2_cap per day, max_open
    total), 2xATR stop / 3xATR chandelier / 15-day time exit at close, 10 bps/side,
    equal-weight slots. random_pick=True: instead of taking every candidate, pick
    min(rsi2_cap, room) uniformly at random (the random portfolio)."""
    rng = np.random.default_rng(seed if seed is not None else SEED)
    names = sorted(book.bars)
    all_dates = sorted(set().union(*[set(book.bars[s].index) for s in names]))
    all_dates = [d for d in all_dates if DEV_START <= d <= DEV_END]
    fwd = {s: {"pos": {d: i for i, d in enumerate(book.bars[s].index)},
               "open": book.bars[s]["open"].to_numpy(), "close": book.bars[s]["close"].to_numpy(),
               "atr14": book.bars[s]["atr14"].to_numpy()} for s in names}
    cand = {}
    for s, days in entry_days_by_sym.items():
        for d in days:
            cand.setdefault(d, []).append(s)
    for d in cand:
        cand[d].sort()
    pos = {}; equity = 1.0; pending = []; monthly = {}
    for d in all_dates:
        for order in pending:
            s = order["sym"]; i = fwd[s]["pos"].get(d)
            if i is None:
                continue
            px = float(fwd[s]["open"][i])
            if not np.isfinite(px) or px <= 0:
                continue
            pos[s] = {"entry_px": px, "atr": order["atr"], "max_close": float(fwd[s]["close"][i]),
                      "stop": px - 2.0 * order["atr"], "notional": (equity / max_open) * (1 - COST),
                      "entry_i": i}
        pending = []
        for s in list(pos):
            p = pos[s]; i = fwd[s]["pos"].get(d)
            if i is None:
                continue
            c = float(fwd[s]["close"][i]); a = float(fwd[s]["atr14"][i])
            a = a if np.isfinite(a) else p["atr"]
            p["max_close"] = max(p["max_close"], c)
            eff = max(p["stop"], p["max_close"] - 3.0 * a)
            if c <= eff or (i - p["entry_i"]) >= 15:
                equity += p["notional"] * (c / p["entry_px"]) * (1 - COST) - p["notional"]
                del pos[s]
        eq_now = equity
        for s, p in pos.items():
            i = fwd[s]["pos"].get(d)
            if i is not None:
                eq_now += p["notional"] * (float(fwd[s]["close"][i]) / p["entry_px"] - 1.0)
        monthly[(d.year, d.month)] = eq_now
        room = max_open - len(pos)
        if room > 0:
            cd = [s for s in cand.get(d, []) if s not in pos]
            if cd:
                take = min(rsi2_cap, room, len(cd))
                if random_pick:
                    pick = rng.choice(len(cd), size=take, replace=False)
                    sel = [cd[int(j)] for j in pick]
                else:
                    sel = cd[:take]
                for s in sel:
                    i = fwd[s]["pos"].get(d)
                    if i is None:
                        continue
                    a = float(fwd[s]["atr14"][i])
                    if np.isfinite(a):
                        pending.append({"sym": s, "atr": a})
    meq = pd.Series({pd.Timestamp(f"{y}-{m:02d}-01"): v for (y, m), v in monthly.items()}).sort_index()
    mr = meq.pct_change().dropna()
    return {"monthly": mr, "cagr": float((1 + mr).prod() ** (12 / max(len(mr), 1)) - 1) if len(mr) else None,
            "mean_monthly": float(mr.mean()) if len(mr) else None,
            "sharpe": float(mr.mean() / mr.std(ddof=1) * np.sqrt(12)) if len(mr) > 1 and mr.std() else None,
            "maxdd": float(((1 + mr).cumprod() / (1 + mr).cumprod().cummax() - 1).min()) if len(mr) else None}


def entry_days(book, mask_by_sym):
    """Calendar days on which a mask produces at least one (anti-overlapped) fill."""
    days = {}
    for s in book.bars:
        m = mask_by_sym.get(s)
        if m is None:
            continue
        idx = sorted(set(np.where(m)[0].tolist()) & book.elig_set[s])
        for t in keeped(book.hyp[s], idx):
            days.setdefault(book.dates[s][book.hyp[s][t][0]], set()).add(s)
    return days


# ------------------------------------------------------------------ data
def get_data():
    spy = H.load_spy(log)
    bars = H.prepare(H.get_live_universe(), "yahoo", log)
    log(f"LIVE universe: {len(bars)} symbols; SPY {spy.index[0].date()}..{spy.index[-1].date()}")
    sig = H.build_signal_sets(bars, spy)
    return spy, bars, sig


def check_engine(book):
    """Verify the local trade engine is bit-identical to the audited H.hypothetical_trades."""
    worst = 0.0; n = 0
    for s in book.bars:
        ref = H.hypothetical_trades(book.bars[s], book.elig[s])
        for t, (e, x, g, r) in ref.items():
            mine = book.hyp.get(int(t))
            if mine is None:
                continue
            if mine[0] != e or mine[1] != x:
                raise SystemExit(f"ENGINE MISMATCH {s} t={t}: {mine[:2]} vs {(e, x)}")
            worst = max(worst, abs(mine[2] - g)); n += 1
    log(f"  engine check: {n} trades identical in entry/exit index; max gross-return diff {worst:.2e}")
    return {"trades_checked": n, "max_return_diff": worst}


def main():
    out = {"meta": {"cost_per_side": COST, "seed": SEED, "dev": [str(DEV_START.date()), str(DEV_END.date())],
                    "quick": QUICK}}
    spy, bars, sig = get_data()
    book = Book(bars, sig)
    out["engine_check"] = check_engine(book)
    N = 200 if QUICK else 1000          # day-basket draws
    NP = 20 if QUICK else 60            # portfolio draws (full daily simulation, slower)
    k_by_sym = {s: int((sig[s]["elig"] & sig[s]["low"]).sum()) for s in bars}

    # ---------------- Q1: the audited defect, reproduced exactly on the audited formula
    log("== Q1. reproduce RSI2REGIME-001 basket_level() for V0/V1 (same protocol, faulty t)")
    q1 = {}
    for v in ["V0", "V1"]:
        sm = mask_from_signal(book, sig, v)
        k_v = {s: int((sig[s]["elig"] & sig[s]["low"]).sum()) for s in bars}
        o, dmn = compare(book, sm, lambda k, sd: per_symbol_random_masks(book, k_v, SEED + k),
                         N, f"Q1 basket {v} per-symbol random")
        o["k_by_sym_source"] = "strategy signal-day count"
        q1[v] = o
        out.setdefault("q1_draws", {})[v] = {"mean": float(dmn.mean()), "sd": float(dmn.std(ddof=1))}
    out["q1_basket"] = q1

    # Q1b: fixed-formula check against the published numbers
    #     published: V0 excess 0.0023074, t_vs_base 31.3416, pct 83.5; V1 excess 0.0006015, 10.6993, 63.2
    out["q1_published"] = {"V0": {"excess": 0.0023074227353308283, "t_vs_base": 31.34162690271813, "percentile": 83.5},
                           "V1": {"excess": 0.0006014725749841419, "t_vs_base": 10.699293603626796, "percentile": 63.2}}
    for v in q1:
        p = out["q1_published"][v]; o = q1[v]
        log(f"  Q1 {v}: published excess {p['excess']*100:.3f}% t {p['t_vs_base']:.2f} pct {p['percentile']} "
            f"| recomputed excess {o['excess']*100:.3f}% t_corr {o['t_corrected']:.2f} pct {o['percentile']:.1f} "
            f"| ratio t_pub/t_corr = {p['t_vs_base']/(o['t_corrected'] or np.nan):.1f} (sqrt(n_draws)={np.sqrt(1000):.1f})")

    # ---------------- Q1c: the D2 defect - what portfolio_random() actually draws from
    log("== Q1c. audited portfolio_random() eligibility (elig & low every day) vs free-universe")
    stats = {}
    for s in bars:
        el = sig[s]["elig"]; low = el & sig[s]["low"]
        stats[s] = (int(low.sum()), int(el.sum()))
    tot_low = sum(a for a, b in stats.values()); tot_el = sum(b for a, b in stats.values())
    stats_v0 = {s: (int((sig[s]["elig"] & sig[s]["low"] & sig[s]["spy_ok"]).sum()),
                    int((sig[s]["elig"] & sig[s]["spy_ok"]).sum())) for s in bars}
    tot_low0 = sum(a for a, b in stats_v0.values()); tot_el0 = sum(b for a, b in stats_v0.values())
    out["q1_exposure"] = {
        "V1_day_pool_share_rsi2_lt10": tot_low / tot_el, "V1_pool_days": tot_low, "V1_all_elig_days": tot_el,
        "V0_day_pool_share_rsi2_lt10": tot_low0 / tot_el0, "V0_pool_days": tot_low0, "V0_all_elig_days": tot_el0,
        "note": "portfolio_random() builds el_by_date from elig & low only -> the 'random' portfolio can only buy names already in RSI(2)<10 state; it is not an unconditional random portfolio."}
    log(f"  D2: V1 random daily pool = {tot_low}/{tot_el} name-days ({100*tot_low/tot_el:.2f}%) are RSI2<10; "
        f"V0 {tot_low0}/{tot_el0} ({100*tot_low0/tot_el0:.2f}%)")

    # ---------------- Q2: the six books, corrected t, both draw protocols
    log("== Q2. six portfolio books: strategy vs (a) audited random protocol, (b) fair free-universe random")
    six = {}
    for v in ["V0", "V1"]:
        for mo in (4, 8, 12):
            sm = mask_from_signal(book, sig, v)
            st = portfolio_run(book, entry_days_strat(book, sm), mo, variant=v)
            # (a) audited random: pool = elig & low & (spy_ok if V0) every day
            pool_days = {}
            for s in bars:
                d = sig[s]; m = d["elig"] & d["low"]
                if v == "V0":
                    m = m & d["spy_ok"]
                for i in np.where(m)[0]:
                    pool_days.setdefault(book.dates[s][i], []).append(s)
            runs = []
            for k in range(NP):
                pick = {}
                rng = np.random.default_rng(SEED + k)
                for dd, names in pool_days.items():
                    take = min(4, len(names))
                    for j in rng.choice(len(names), size=take, replace=False):
                        pick.setdefault(dd, set()).add(names[int(j)])
                runs.append(portfolio_run(book, pick, mo, variant=v, seed=None))
            dm = np.array([r["mean_monthly"] for r in runs])
            sm_mean = st["mean_monthly"]; sd = dm.std(ddof=1)
            ex = sm_mean - dm.mean()
            # monthly-series t as RSI2REGIME quotes it (monthly_diff-style)
            mm = st["monthly"]
            t_monthly = ex / (mm.std(ddof=1) / np.sqrt(len(mm)))
            six[f"{v}_mo{mo}"] = {"strategy": {k: st[k] for k in ("cagr", "mean_monthly", "sharpe", "maxdd")},
                                  "audited_random_mean_monthly": float(dm.mean()),
                                  "audited_random_sd_draws": float(sd),
                                  "excess_monthly": float(ex), "t_corrected": float(ex / sd) if sd else None,
                                  "t_monthly_series_faulty": float(t_monthly),
                                  "percentile_strategy_vs_draws": float((dm < sm_mean).mean() * 100),
                                  "n_draws": int(len(dm))}
            log(f"  {v} mo{mo}: strat {sm_mean*100:+.3f}%/mo cagr {st['cagr']*100:+.1f}% dd {st['maxdd']*100:.1f}% "
                f"rand {dm.mean()*100:+.3f}% sd {sd*100:.3f}% t_corr {ex/sd if sd else 0:+.2f} "
                f"pct {(dm<sm_mean).mean()*100:.0f} t_monthly_faulty {t_monthly:+.2f}")
    out["q2_six_books_audited_random"] = six

    # fair free-universe random (no RSI(2) condition, same slot rules and number of fills)
    log("== Q2b. fair random portfolio: free universe (elig only), same slots, same daily fill count")
    fair = {}
    for v in ["V0", "V1"]:
        sm = mask_from_signal(book, sig, v)
        ed = entry_days_strat(book, sm)
        fills_per_day = {d: len(names) for d, names in ed.items()}
        for mo in (8,):
            st = portfolio_run(book, ed, mo, variant=v)
            runs = []
            for k in range(NP):
                pick = {}
                rng = np.random.default_rng(SEED + 1000 + k)
                for d, nfill in fills_per_day.items():
                    if not nfill:
                        continue
                    cand = [s for s in free_universe_day(book, sig, d, v)]
                    if not cand:
                        continue
                    # match the strategy: obey the max_open cap and the per-day rsi2_cap
                    room = mo - len(pick.get(d, ()))
                    take = min(nfill, len(cand), max(room, 0), RSI2_CAP)
                    if not take:
                        continue
                    for j in rng.choice(len(cand), size=take, replace=False):
                        pick.setdefault(d, set()).add(cand[int(j)])
                runs.append(portfolio_run(book, pick, mo, variant=v, seed=None))
            dm = np.array([r["mean_monthly"] for r in runs])
            sd = dm.std(ddof=1); ex = st["mean_monthly"] - dm.mean()
            fair[f"{v}_mo{mo}"] = {"strategy_mean_monthly": st["mean_monthly"],
                                   "fair_random_mean_monthly": float(dm.mean()),
                                   "fair_random_sd_draws": float(sd), "excess_monthly": float(ex),
                                   "t_corrected": float(ex / sd) if sd else None,
                                   "percentile": float((dm < st["mean_monthly"]).mean() * 100)}
            log(f"  FAIR {v} mo{mo}: strat {st['mean_monthly']*100:+.3f}%/mo free-rand {dm.mean()*100:+.3f}% "
                f"sd {sd*100:.3f}% t_corr {ex/sd if sd else 0:+.2f} pct {(dm<st['mean_monthly']).mean()*100:.0f}")
    out["q2b_fair_random_portfolio"] = fair
    write(out)
    log("  (partial results written)")

    # ---------------- Q3: sanity checks on the day-basket harness
    log("== Q3. sanity checks (bad entry must lose, lookahead cheat must crush)")
    checks = {}
    bad = {s: (bars[s]["rsi2"].to_numpy() > 90.0) for s in bars}
    o, _ = compare(book, bad, lambda k, sd: ctrl_masks(book, sig, SEED + 7, rng_seed=k), N, "BAD rsi2>90 (vs fair random)")
    checks["bad_rsi2_gt90"] = o
    up = {s: ((bars[s]["close"] / bars[s]["close"].shift(2) - 1.0).to_numpy() > 0.08) for s in bars}
    o, _ = compare(book, up, lambda k, sd: ctrl_masks(book, sig, SEED + 8, rng_seed=k), N, "BAD +8% in 2d (vs fair random)")
    checks["bad_after_big_up_day"] = o
    look = {s: ((bars[s]["close"].shift(-5) / bars[s]["close"] - 1.0).to_numpy() >= 0.05) for s in bars}
    o, _ = compare(book, look, lambda k, sd: ctrl_masks(book, sig, SEED + 9, rng_seed=k), N, "LOOKAHEAD fwd5d>=+5%")
    checks["lookahead_fwd5d_ge5pct"] = o
    look2 = {s: ((bars[s]["close"].shift(-1) / bars[s]["open"] - 1.0).to_numpy() >= 0.02) for s in bars}
    o, _ = compare(book, look2, lambda k, sd: ctrl_masks(book, sig, SEED + 10, rng_seed=k), N, "LOOKAHEAD next-day open->close>=+2%")
    checks["lookahead_next_day"] = o
    # control: a genuine random entry must sit at ~0 excess / ~50 pct
    o, _ = compare(book, ctrl_masks(book, sig, SEED + 11, rng_seed=0),
                   lambda k, sd: ctrl_masks(book, sig, SEED + 11, rng_seed=k + 1), N,
                   "CONTROL fair random vs fair random")
    checks["control_random"] = o
    out["q3_sanity"] = checks
    write(out)

    # ---------------- Q4: exit rules vs entry signal
    log("== Q4. decomposition: S1a exits vs entry")
    dec = {}
    sm_v1 = mask_from_signal(book, sig, "V1")
    o_sig, _ = compare(book, sm_v1, lambda k, sd: ctrl_masks(book, sig, SEED + 500, rng_seed=k), N, "RSI2 entries + S1a exits")
    rnd = ctrl_masks(book, sig, SEED + 21, rng_seed=0)
    o_rnd, _ = compare(book, rnd, lambda k, sd: ctrl_masks(book, sig, SEED + 21, rng_seed=k + 1), N, "RANDOM entries + S1a exits")
    dec["s1a_exits"] = {"rsi2_entries": o_sig, "random_entries": o_rnd}
    # time-only exit (no stop/trail, hold the 15-day window)
    def time_only(mask_by_sym):
        r = []
        for s, df in bars.items():
            m = mask_by_sym.get(s)
            if m is None:
                continue
            idx = sorted(set(np.where(m)[0].tolist()) & book.elig_set[s])
            c = df["close"].to_numpy(); o_ = df["open"].to_numpy()
            last = -1
            for i in idx:
                if i + 1 > last:
                    e = i + 1
                    if e >= len(c):
                        continue
                    x = min(e + 15, len(c) - 1)
                    r.append(c[x] / o_[e] - 1.0); last = x
        return np.array(r) - 2 * COST
    t_sig = time_only(sm_v1)
    t_rnd = []
    for k in range(min(N, 200)):
        t_rnd.extend(time_only(ctrl_masks(book, sig, SEED + 30 + k, rng_seed=k)).tolist())
    t_rnd = np.array(t_rnd)
    dec["time_only_15d"] = {"rsi2_entries": {"n": int(len(t_sig)), "mean": float(t_sig.mean())},
                            "random_entries": {"n": int(len(t_rnd)), "mean": float(t_rnd.mean())}}
    log(f"  time-only 15d: rsi2 n={len(t_sig)} mean {t_sig.mean()*100:+.3f}% | random n={len(t_rnd)} mean {t_rnd.mean()*100:+.3f}%")
    # buy & hold equal weight over DEV
    bnh = []
    for s, df in bars.items():
        d0 = df.index[df.index >= DEV_START]
        if len(d0) < 5:
            continue
        c = df["close"]
        bnh.append(float(c.iloc[-1] / c.loc[d0[0]] - 1.0))
    bnh = np.array(bnh)
    dec["buy_and_hold_ew"] = {"n": int(len(bnh)), "mean_total": float(bnh.mean()),
                              "median_total": float(np.median(bnh)), "pct_positive": float((bnh > 0).mean()),
                              "years": 4.0, "annualized_mean": float((1 + bnh).mean() ** (1 / 4) - 1)}
    log(f"  B&H EW {len(bnh)} names: mean total {bnh.mean()*100:+.0f}% median {np.median(bnh)*100:+.0f}% "
        f"positive {100*(bnh>0).mean():.0f}%")
    # exit-reason mix for both entry types (how much the stops/trail truncate)
    def reason_mix(mask_by_sym):
        cnt = {}
        for s in bars:
            m = mask_by_sym.get(s)
            if m is None:
                continue
            idx = sorted(set(np.where(m)[0].tolist()) & book.elig_set[s])
            for t in keeped(book.hyp[s], idx):
                cnt[book.hyp[s][t][3]] = cnt.get(book.hyp[s][t][3], 0) + 1
        tot = sum(cnt.values()) or 1
        return {k: v / tot for k, v in cnt.items()} | {"_n": int(tot)}
    dec["exit_reasons"] = {"rsi2": reason_mix(sm_v1), "random": reason_mix(rnd)}
    log(f"  exit mix rsi2 {dec['exit_reasons']['rsi2']} random {dec['exit_reasons']['random']}")
    out["q4_decomposition"] = dec

    # ---------------- unmatched-exit A/B: what do the stops add?
    log("== Q4b. S1a exits vs a 15-day time exit on the same entries (day baskets)")
    def exits_ab(mask_by_sym, tag):
        hs_a = book.day_means(mask_by_sym)
        r_t = time_only(mask_by_sym)
        # per-entry means: average net return of the same fills with a time exit
        log(f"  {tag}: S1a day-basket mean {np.mean(list(hs_a.values()))*100:+.3f}% "
            f"time-only trade mean {r_t.mean()*100:+.3f}% (n={len(r_t)})")
        return {"s1a_day_mean": float(np.mean(list(hs_a.values()))) if hs_a else None,
                "time_only_mean": float(r_t.mean()) if len(r_t) else None, "n": int(len(r_t))}
    out["q4b_exits_ab"] = {"rsi2": exits_ab(sm_v1, "rsi2"), "random": exits_ab(rnd, "random")}

    # ---------------- universe provenance (survivorship)
    out["universe"] = {"n_symbols": len(bars), "source_file": H.UNIV_FILE,
                       "note": ("same universe file as RSI2REGIME-001; it is the CURRENT live "
                                "353-symbol list, so any name that left the market before today is "
                                "absent -> survivorship bias is SHARED by strategy and random, so it "
                                "does not favour the strategy, but it does inflate both absolute numbers.")}
    write(out)
    log("done -> results_rsi2random.json")


def free_universe_day(book, sig, d, variant):
    out = []
    for s in book.bars:
        dt = book.dates[s]
        i = dt.get_loc(d) if d in dt else -1
        if i is None or i < 0:
            continue
        sg = sig[s]
        if not sg["elig"][i]:
            continue
        if variant == "V0" and not sg["spy_ok"][i]:
            continue
        out.append(s)
    return sorted(out)


def entry_days_strat(book, mask_by_sym):
    """{calendar day: set(symbols)} of realised anti-overlapped fills."""
    days = {}
    for s in book.bars:
        m = mask_by_sym.get(s)
        if m is None:
            continue
        idx = sorted(set(np.where(m)[0].tolist()) & book.elig_set[s])
        for t in keeped(book.hyp[s], idx):
            days.setdefault(book.dates[s][book.hyp[s][t][0]], set()).add(s)
    return days


def ctrl_masks(book, sig, seed, rng_seed=0):
    """Unconditional random entry mask: per symbol, the same number of entry days as RSI2<10
    signals, drawn from ALL eligible days (no RSI(2) condition) - the fair baseline."""
    rng = np.random.default_rng(seed + rng_seed)
    out = {}
    for s in book.bars:
        k = int((sig[s]["elig"] & sig[s]["low"]).sum())
        pool = book.elig[s]
        m = np.zeros(len(book.bars[s]), bool)
        if k and len(pool):
            m[rng.choice(pool, size=min(k, len(pool)), replace=False)] = True
        out[s] = m
    return out


def per_symbol_random_masks_fair(book, sig, k):
    return ctrl_masks(book, sig, SEED + 500, rng_seed=k)


def write(out):
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results_rsi2random.json")
    with open(p, "w") as f:
        json.dump(out, f, indent=1, default=str)
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "run_log.txt"), "w") as f:
        f.write("\n".join(LOG) + "\n")


if __name__ == "__main__":
    main()
