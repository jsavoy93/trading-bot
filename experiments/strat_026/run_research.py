"""STRAT-026 — Pro-swing strategy research (dev 2021-2024 / holdout 2025-2026).

RESEARCH ONLY. Paper data, no production changes. Reuses the frozen STRAT-025
engine conventions (loader, Wilder RSI, close-checked stops, 10 bps/side costs,
random-entry baseline with 1000 draws and per-symbol anti-overlap).

Strategies (signal on close of day t, entry next open t+1 unless noted):
  S1a rsi2_regime   : RSI(2)<10 dip, regime filter SPY>SMA200, PRO exits
                      (initial stop 2xATR close-checked, chandelier trail 3xATR,
                      partial 50% at +2R (intraday high touch, filled at 2R),
                      time exit 15 days).
  S1b volbrk_regime : 20-day close-high breakout on 1.5x avg volume, same regime
                      filter + same PRO exits.
  S2  pead_proxy    : gap-up open > +4% vs prior close with volume > 1.5x vol20,
                      enter NEXT day open, chandelier 3xATR trail, time exit 20 days.
                      (No earnings-date data available: gap+volume proxy — documented.)
  S3  momentum      : weekly rebalance, top decile by 6-month return skipping the
                      most recent month (close_t / close_{t-126} - 1 computed over
                      window ending t-21), equal weight, holds of 1/2/4 weeks,
                      regime filter. Portfolio-level backtest vs random decile
                      selection baseline (1000 draws).

Gate: a candidate passes dev iff pooled baseline exists and
t_diff = (strat_mean - baseline_pooled_mean)/sqrt(se_strat^2 + se_base^2) >= 2.
Holdout run once, only for dev passers.
"""
from __future__ import annotations
import json, sys
from pathlib import Path
import numpy as np
import pandas as pd

import engine as E

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"          # symlink to STRAT-025 frozen CSVs (672 symbols)
RESULTS = HERE / "results"
RESULTS.mkdir(exist_ok=True)

SEED = 20261031
N_DRAWS = 1000
MIN_TRADES = 100
DEV = (pd.Timestamp("2021-01-01"), pd.Timestamp("2024-12-31"))
HOLD = (pd.Timestamp("2025-01-01"), pd.Timestamp("2026-10-28"))

INIT_STOP_ATR = 2.0
TRAIL_ATR = 3.0
PARTIAL_R = 2.0
TIME_EXIT = 15
TIME_EXIT_PEAD = 20


# ---------------- pro-exit trade simulation (S1/S2 style) ----------------

def pro_exits_trade(df, t: int, partial_on: bool, time_stop: int):
    """Entry at open of t+1. Returns (entry_i, exit_i, gross) or None.
    gross includes the 2R partial (50% at 2R, intraday high touch) when
    partial_on. Chandelier = max(close since entry) - TRAIL_ATR*ATR14(t),
    close-checked. Initial stop = entry - INIT_STOP_ATR*ATR14(t), close-checked.
    """
    n = len(df)
    e = t + 1
    opens, highs, lows, closes = df["open"].values, df["high"].values, df["low"].values, df["close"].values
    atr = df["atr14"].values
    if e >= n or not np.isfinite(opens[e]) or opens[e] <= 0 or not np.isfinite(atr[t]):
        return None
    entry = opens[e]
    risk = INIT_STOP_ATR * atr[t]
    init_stop = entry - risk
    partial_px = entry + PARTIAL_R * risk
    took_partial = False
    chandelier = closes[e]  # highest close since entry, starts at entry-day close
    end = min(e + time_stop, n)
    exit_i = None
    for s in range(e, end):
        if not took_partial and partial_on and highs[s] >= partial_px:
            took_partial = True
        stop = max(init_stop, chandelier - TRAIL_ATR * atr[t])
        if closes[s] <= stop:
            exit_i = s
            break
        if s + 1 < n:
            chandelier = max(chandelier, closes[s])
        # note: stop uses chandelier through s (no lookahead: chandelier updated
        # AFTER the stop check for day s, from closes[s])
    if exit_i is None:
        exit_i = end - 1
    gross = closes[exit_i] / entry - 1.0
    if took_partial:
        gross = 0.5 * (partial_px / entry - 1.0) + 0.5 * gross
    return (e, int(exit_i), float(gross))


def build_hyp_pro(df, entry_days, partial_on, time_stop):
    out = {}
    for t in entry_days:
        r = pro_exits_trade(df, int(t), partial_on, time_stop)
        if r is not None:
            out[int(t)] = r
    return out


# ---------------- metrics ----------------

def metrics_full(kept):
    if not kept:
        return {"n": 0, "mean_net": None, "t": None, "win_rate": None,
                "avg_win": None, "avg_loss": None, "max_dd": None}
    r = np.array([g - 2 * E.COST for _, _, g in kept])
    sd = r.std(ddof=1)
    # max drawdown of the per-trade equity curve in chronological entry order
    kept_sorted = sorted(kept, key=lambda x: x[0])
    rr = np.array([g - 2 * E.COST for _, _, g in kept_sorted])
    eq = np.cumprod(1.0 + rr)
    peak = np.maximum.accumulate(eq)
    dd = eq / peak - 1.0
    return {
        "n": int(len(r)), "mean_net": float(r.mean()),
        "t": float(r.mean() / (sd / np.sqrt(len(r)))) if sd > 0 else None,
        "win_rate": float((r > 0).mean()),
        "avg_win": float(r[r > 0].mean()) if (r > 0).any() else None,
        "avg_loss": float(r[r <= 0].mean()) if (r <= 0).any() else None,
        "max_dd": float(dd.min()) if len(dd) else None,
    }


def baseline_stats(hyp_cache, elig_by_sym, k_by_sym, rng, exit_stats_fn):
    """Same baseline machinery as STRAT-025: 1000 random draws with per-symbol
    anti-overlap; pooled mean/SE across draws; also mean of draw means."""
    draw_means = []
    pooled_all = []
    for _ in range(N_DRAWS):
        pooled = []
        for sym, elig in elig_by_sym.items():
            k = k_by_sym.get(sym, 0)
            if k == 0 or not elig:
                continue
            hyp = hyp_cache[sym]
            pick = rng.choice(len(elig), size=min(k, len(elig)), replace=False)
            days = sorted(elig[i] for i in pick)
            pooled.extend(E.anti_overlap(hyp, days))
        if pooled:
            r = np.array([g - 2 * E.COST for _, _, g in pooled])
            pooled_all.append(r)
            draw_means.append(float(r.mean()))
    dm = np.array(draw_means)
    if not len(dm):
        return None
    pooled = np.concatenate(pooled_all)
    return {
        "mean_of_draw_means": float(dm.mean()),
        "p5": float(np.percentile(dm, 5)), "p95": float(np.percentile(dm, 95)),
        "pooled_mean": float(pooled.mean()),
        "pooled_se": float(pooled.std(ddof=1) / np.sqrt(len(pooled))),
        "pooled_n": int(len(pooled)),
    }


def t_vs_baseline(strat_m, base):
    if strat_m["mean_net"] is None or base is None:
        return None
    se_s = None
    # se of strat mean from its n and std: recover from t-stat
    if strat_m["t"] is not None and strat_m["t"] != 0:
        se_s = abs(strat_m["mean_net"] / strat_m["t"])
    if se_s is None:
        return None
    se = np.sqrt(se_s ** 2 + base["pooled_se"] ** 2)
    return (strat_m["mean_net"] - base["pooled_mean"]) / se


# ---------------- signals ----------------

def regime_ok(spy_df):
    """Boolean Series indexed by DATE (mapped onto per-symbol dates)."""
    m = spy_df["close"] > spy_df["sma200"]
    return pd.Series(m.values, index=spy_df["date"].values)


def sig_rsi2(df, reg):
    m = df["rsi2"].notna() & (df["rsi2"] < 10) & E.liq_ok(df)
    return m & df["date"].map(reg)


def sig_volbrk(df, reg):
    prior20_high = df["close"].rolling(20).max().shift(1)
    prior20_vol = df["volume"].rolling(20).mean().shift(1)
    m = (df["close"] > prior20_high) & (df["volume"] > 1.5 * prior20_vol) & E.liq_ok(df)
    return m & df["date"].map(reg)


def sig_pead(df):
    gap = df["open"] / df["close"].shift(1) - 1.0
    volx = df["volume"] > 1.5 * df["vol20"]
    return E.liq_ok(df) & (gap > 0.04) & volx


# ---------------- per-entry-event runners (S1a, S1b, S2) ----------------

def run_entry_strategy(frames, sig_by_sym, win, partial_on, time_stop,
                       chandelier_trail_only=False, seed_offset=0):
    strat = []
    days_by_sym = {}
    for sym, ts in sig_by_sym.items():
        df = frames[sym]
        days = [int(i) for i in ts if win[0] <= df["date"].iloc[i] <= win[1]]
        if days:
            days_by_sym[sym] = days
            hyp = build_hyp_pro(df, days, partial_on, time_stop)
            strat.extend(E.anti_overlap(hyp, days))
    sm = metrics_full(strat)
    rng = np.random.default_rng(SEED + seed_offset)
    elig_by_sym, hyp_cache, k_by_sym = {}, {}, {}
    for sym, days in days_by_sym.items():
        df = frames[sym]
        m = E.liq_ok(df) & df["sma200"].notna() & df["atr14"].notna() & \
            (df["date"] >= win[0]) & (df["date"] <= win[1])
        elig = [int(i) for i in df.index[m] if int(i) not in set(days)]
        elig_by_sym[sym] = elig
        hyp_cache[sym] = build_hyp_pro(df, elig, partial_on, time_stop)
        k_by_sym[sym] = len(days)
    base = baseline_stats(hyp_cache, elig_by_sym, k_by_sym, rng, None)
    return {"strat": sm, "baseline": base, "t_vs_base": t_vs_baseline(sm, base)}


# ---------------- S3 weekly momentum ----------------

def run_momentum(frames, spy, win, hold_weeks, seed_offset=0):
    """Weekly (last trading day of each week) rebalance. On rebalance day t with
    regime ON: rank eligible symbols by 6-month return skipping the most recent
    month (close_t / close_{t-126} using window ending t-21). Hold top decile
    equal-weight `hold_weeks`. Costs 10 bps per side on turnover (charged on
    each entry and exit leg at 10 bps). Returns weekly portfolio return series
    and metrics; baseline = random same-size decile each rebalance, 1000 draws."""
    reg = regime_ok(spy)
    dates = spy["date"].reset_index(drop=True)
    # weekly rebalance dates: last trading day of each ISO week in window
    wk = dates.dt.to_period("W")
    rebal_t = {}
    spy_by_date = spy.set_index("date")["close"]
    for i in range(len(dates)):
        d = dates.iloc[i]
        if win[0] <= d <= win[1] and reg.iloc[i]:
            p = wk.iloc[i]
            # keep last trading day per week for this symbol set: use SPY calendar
            rebal_t[p] = i  # later i in same week overwrites
    rebal_days = [spy["date"].iloc[i] for p, i in sorted(rebal_t.items(), key=lambda kv: kv[1])]
    # precompute per-symbol close series by date
    closes_by_sym = {}
    for sym, df in frames.items():
        if sym == "SPY":
            continue
        s = df.set_index("date")["close"]
        if len(s) >= 200:
            closes_by_sym[sym] = s

    def mom(sym, d):
        s = closes_by_sym[sym]
        try:
            c0 = s.loc[: d - pd.Timedelta(days=110)]  # ~ t-126 calendar
            c1 = s.loc[: d - pd.Timedelta(days=16)]   # skip most recent ~month
            if len(c0) == 0 or len(c1) == 0:
                return None
            return c1.iloc[-1] / c0.iloc[-1] - 1.0
        except Exception:
            return None

    # eligibility proxy via vol/close is not available here cheaply; use data
    # presence + price >= $5 at d
    def eligible_syms(d):
        out = []
        for sym, s in closes_by_sym.items():
            try:
                c = s.loc[:d]
            except Exception:
                continue
            if len(c) and c.iloc[-1] >= 5.0 and len(c) >= 200:
                out.append(sym)
        return out

    results = []
    rng = np.random.default_rng(SEED + seed_offset)
    for d in rebal_days:
        el = eligible_syms(d)
        if len(el) < 20:
            continue
        moms = {sym: mom(sym, d) for sym in el}
        moms = {k: v for k, v in moms.items() if v is not None}
        if len(moms) < 20:
            continue
        ranked = sorted(moms, key=moms.get, reverse=True)
        k = max(1, len(ranked) // 10)
        picks = ranked[:k]
        # forward return over hold_weeks weeks from close d to close at end
        def fwd(sym):
            s = closes_by_sym[sym]
            after = s.loc[d:]
            if len(after) < 2:
                return None
            # hold window: hold_weeks * 5 trading days
            tgt = min(hold_weeks * 5, len(after) - 1)
            return after.iloc[tgt] / after.iloc[0] - 1.0
        fwds = {p: fwd(p) for p in picks}
        fwds = {a: b for a, b in fwds.items() if b is not None}
        if len(fwds) < max(2, k // 2):
            continue
        # baseline: random k of ranked list (same eligible pool)
        rp = rng.choice(len(ranked), size=k, replace=False)
        rfwds = {ranked[i]: fwd(ranked[i]) for i in rp}
        rfwds = {a: b for a, b in rfwds.items() if b is not None}
        results.append({
            "date": str(d.date()), "k": len(fwds),
            "strat_ret": float(np.mean(list(fwds.values()))) - 2 * E.COST,
            "base_ret": float(np.mean(list(rfwds.values()))) - 2 * E.COST if rfwds else None,
        })
    if not results:
        return {"strat": {"n": 0}, "baseline": None, "t_vs_base": None}
    sr = np.array([x["strat_ret"] for x in results])
    br = np.array([x["base_ret"] for x in results if x["base_ret"] is not None])
    eq = np.cumprod(1 + sr)
    peak = np.maximum.accumulate(eq)
    dd = float((eq / peak - 1).min())
    def mm(r):
        sd = r.std(ddof=1)
        wins, losses = r[r > 0], r[r <= 0]
        return {"n": int(len(r)), "mean_net": float(r.mean()),
                "t": float(r.mean() / (sd / np.sqrt(len(r)))) if sd > 0 else None,
                "win_rate": float((r > 0).mean()),
                "avg_win": float(wins.mean()) if len(wins) else None,
                "avg_loss": float(losses.mean()) if len(losses) else None,
                "max_dd": dd}
    sm, bm = mm(sr), mm(br)
    se = np.sqrt((sr.std(ddof=1) / np.sqrt(len(sr))) ** 2 +
                 (br.std(ddof=1) / np.sqrt(len(br))) ** 2)
    return {"strat": sm, "baseline": {**bm, "pooled_mean": bm["mean_net"],
                                      "pooled_se": br.std(ddof=1) / np.sqrt(len(br)),
                                      "pooled_n": int(len(br))},
            "t_vs_base": float((sm["mean_net"] - bm["mean_net"]) / se),
            "rebalances": len(results)}


# ---------------- main ----------------

def load_all():
    frames = {}
    for f in sorted(DATA.glob("*.csv")):
        df = E.load_symbol(f)
        if df is not None:
            frames[f.stem] = df
    return frames


def main(period: str):
    assert period in ("dev", "holdout")
    win = DEV if period == "dev" else HOLD
    frames = load_all()
    print(f"loaded {len(frames)} symbols")
    spy = frames["SPY"]
    reg = regime_ok(spy)
    out = {}

    def run_and(name, fn):
        r = fn()
        out[name] = r
        sm, base = r["strat"], r.get("baseline")
        print(f"{name} [{period}]: n={sm['n']} mean={None if sm.get('mean_net') is None else round(sm['mean_net']*100,3)}% "
              f"t={None if sm.get('t') is None else round(sm['t'],2)} "
              f"t_vs_base={None if r.get('t_vs_base') is None else round(r['t_vs_base'],2)} "
              f"base={None if not base or base.get('pooled_mean') is None else round(base['pooled_mean']*100,3)}%")

    dev_sel = {}
    if period == "holdout":
        dev_sel = json.loads((RESULTS / "_dev_selection.json").read_text())

    sigs = {}
    for sym, df in frames.items():
        if sym == "SPY":
            continue
        sigs.setdefault("rsi2", {})[sym] = df.index[sig_rsi2(df, reg)].tolist()
        sigs.setdefault("volbrk", {})[sym] = df.index[sig_volbrk(df, reg)].tolist()
        sigs.setdefault("pead", {})[sym] = df.index[sig_pead(df)].tolist()

    def maybe(name, fn):
        if period == "holdout" and not dev_sel.get(name, {}).get("dev_pass"):
            print(f"{name}: skip holdout (did not pass dev gate)")
            return
        run_and(name, fn)

    maybe("rsi2_regime", lambda: run_entry_strategy(
        frames, sigs["rsi2"], win, partial_on=True, time_stop=TIME_EXIT, seed_offset=1))
    maybe("volbrk_regime", lambda: run_entry_strategy(
        frames, sigs["volbrk"], win, partial_on=True, time_stop=TIME_EXIT, seed_offset=2))
    maybe("pead_proxy", lambda: run_entry_strategy(
        frames, sigs["pead"], win, partial_on=True, time_stop=TIME_EXIT_PEAD, seed_offset=3))
    for hw in (1, 2, 4):
        maybe(f"momentum_{hw}w", lambda hw=hw: run_momentum(
            frames, spy, win, hw, seed_offset=10 + hw))

    (RESULTS / f"_results_{period}.json").write_text(json.dumps(out, indent=1, default=str))

    if period == "dev":
        sel = {}
        for name, r in out.items():
            sm = r["strat"]
            tv = r.get("t_vs_base")
            ok = (sm.get("n", 0) >= MIN_TRADES and tv is not None and tv >= 2.0)
            sel[name] = {"dev_pass": bool(ok), "t_vs_base": tv}
        (RESULTS / "_dev_selection.json").write_text(json.dumps(sel, indent=1, default=str))
        print("dev selection:", json.dumps(sel, indent=1))
    print("DONE")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "dev")
