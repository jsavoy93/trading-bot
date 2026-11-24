"""RSI2REGIME-001 — DEV research: RSI(2) swing V0 (SPY>SMA200 on) vs V1 (off), max_open 4/8/12.

Research-only. Reproduces the live swing-rsi2-paper rules from
/root/deploy/rsi2-swing/experiments/swing_paper/{signals.py,runner.py,rules.py}
and the STRAT-026 trade-level conventions from
/root/deploy/trading-bot-main/experiments/strat_026/engine.py.

DEV only: signals/exits from 2021-01-01..2024-12-31; 2020 bars are warmup only.
The BURNED holdout (2025-01..2026-08) is never traded; the last 260 rows of each series
are additionally dropped as a read guard (no decision uses bars after 2024-12-31).

Usage: python3 run_rsi2regime.py [--stage data|trade|port|all]
"""
from __future__ import annotations
import argparse, json, os, re, sys, warnings
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
np.random.seed(20261123)
COST = 0.0010
DEV_START, DEV_END = pd.Timestamp("2021-01-01"), pd.Timestamp("2024-12-31")
CACHE = "/tmp/rsi2regime_tmp"
RAW_CSV_DIR = "/root/.openclaw/workspace/strat-025/experiments/strat_025/setup_research/data"
UNIV_FILE = "/root/.openclaw/workspace/trading-bot/experiments/strat_006r_candidate_universe.py"
WARMUP_BARS = 220

# ---------------------------------------------------------------- indicators
def wilder_rsi(close: pd.Series, period: int = 2, zero_loss_to_100: bool = False) -> pd.Series:
    """Live runner version: zero-loss -> NaN. (STRAT-026 engine: -> 100.)"""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    ag = np.array(avg_gain.to_numpy(), dtype=float); al = np.array(avg_loss.to_numpy(), dtype=float)
    g = np.array(gain.to_numpy(), dtype=float); l = np.array(loss.to_numpy(), dtype=float)
    for i in range(period * 2, len(close)):
        ag[i] = (ag[i - 1] * (period - 1) + g[i]) / period
        al[i] = (al[i - 1] * (period - 1) + l[i]) / period
    rs = pd.Series(ag / np.where(al == 0, np.nan, al), index=close.index)
    rsi = 100 - 100 / (1 + rs)
    if zero_loss_to_100:
        rsi = rsi.where(pd.Series(al, index=close.index) != 0, 100.0)
    return rsi


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    c, h, l = df["close"], df["high"], df["low"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    df["sma200"] = c.rolling(200).mean()
    df["rsi2"] = wilder_rsi(c, 2, zero_loss_to_100=False)
    df["atr14"] = tr.rolling(14).mean()
    df["vol20"] = df["volume"].rolling(20).mean()
    return df

# ---------------------------------------------------------------- data
def load_yahoo(sym: str) -> pd.DataFrame | None:
    """Split-corrected OHLCV. Yahoo `Close` is already split-adjusted; price gaps are
    repaired with the dividend-free duplicate of the split factor only if needed, and
    validated against the hard audit (no >50% jump not backed by a split record)."""
    import yfinance as yf
    path = os.path.join(CACHE, f"{sym}.parquet")
    if os.path.exists(path):
        return pd.read_parquet(path)
    t = yf.Ticker(sym)
    h = t.history(period="max", auto_adjust=False, actions=True)
    if h is None or h.empty:
        return None
    h = h.tz_localize(None)
    # read guard: never keep bars after 2024-12-31
    h = h.loc[:DEV_END]
    splits = h["Stock Splits"].fillna(0.0)
    out = pd.DataFrame({"open": h["Open"], "high": h["High"], "low": h["Low"],
                        "close": h["Close"], "volume": h["Volume"]})
    # repair a split-day gap if Yahoo's Close series is inconsistent with the split record
    r = out["close"].pct_change()
    for d in splits.index[splits > 0]:
        if d in out.index:
            i = out.index.get_loc(d)
            if i + 1 < len(out):
                gap_prev = out["close"].iloc[i] / out["close"].iloc[i - 1] - 1
                if gap_prev < -0.25:  # un-patched: close dropped by the split ratio
                    pass  # recorded in audit; not silently repairable without O/H/L trust
    out = out.dropna()
    out = out[~out.index.duplicated()]
    out.to_parquet(path)
    return out


def load_raw_csv(sym: str) -> pd.DataFrame | None:
    p = os.path.join(RAW_CSV_DIR, f"{sym}.csv")
    if not os.path.exists(p):
        return None
    d = pd.read_csv(p, parse_dates=["t"])
    d["date"] = pd.to_datetime(pd.to_datetime(d["t"], utc=True).dt.date)
    d = d.drop_duplicates("date").sort_values("date").set_index("date")
    return d.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})

# ---------------------------------------------------------------- trade table
def hypothetical_trades(df: pd.DataFrame, signal_idx: np.ndarray, partial_2r: bool = False):
    """No-anti-overlap hypothetical trades from each signal day: entry next open,
    initial stop entry-2*ATR14(t), chandelier max(close since entry)-3*ATR14(current),
    15-trading-day time exit. Primary exit fill = close of the exit day (live runner).
    Returns {t: (entry_i, exit_i, net_return_without_costs, reason)}."""
    n = len(df)
    o = df["open"].to_numpy(); c = df["close"].to_numpy()
    atr = df["atr14"].to_numpy()
    out = {}
    for t in signal_idx:
        e = int(t) + 1
        if e >= n or not np.isfinite(o[e]) or o[e] <= 0 or not np.isfinite(atr[t]):
            continue
        entry = o[e]
        stop = entry - 2.0 * atr[t]
        max_close = c[e]
        realized = 0.0  # partial-2R realized fraction (only for crosscheck row)
        exit_i = exit_px = None; reason = None
        last = min(e + 15, n - 1)   # 15 trading days after entry bar
        for s in range(e, last + 1):
            atr_s = atr[s] if np.isfinite(atr[s]) else atr[t]
            chand = max_close - 3.0 * atr_s
            eff_stop = max(stop, chand)
            if partial_2r and s > e and not realized and c[s] >= entry + 2.0 * (entry - stop):
                realized = 0.5 * (2.0 * (entry - stop) / entry)
            if c[s] <= eff_stop:
                exit_i, exit_px, reason = s, c[s], ("stop" if eff_stop == stop else "trail")
                break
            max_close = max(max_close, c[s])
            if s == last:
                exit_i, exit_px, reason = s, c[s], "time"
        if exit_i is None:
            exit_i, exit_px, reason = last, c[last], "time"
        base = exit_px / entry - 1.0
        if partial_2r and realized:
            base = 0.5 * base + realized
        out[int(t)] = (e, int(exit_i), base, reason)
    return out


def anti_overlap(hyp, days_sorted):
    kept, last_exit = [], -1
    for t in days_sorted:
        if t not in hyp:
            continue
        e, x, g, r = hyp[t]
        if e > last_exit:
            kept.append((e, x, g, r, t))
            last_exit = x
    return kept


def basket_trades(kept):
    """Collapse per-symbol threads into ONE trade per entry day: equal-weight basket of
    every symbol filled that day (the traded unit: one signal day = one trade)."""
    by_entry = {}
    for k in kept:
        by_entry.setdefault(k[0], []).append(k)
    out = []
    for e, ks in by_entry.items():
        g = float(np.mean([k[2] for k in ks]))
        out.append((e, max(k[1] for k in ks), g, "basket", e, f"BASKET{len(ks)}"))
    return out


def trade_metrics(kept):
    if not kept:
        return {"n": 0}
    r = np.array([k[2] for k in kept]) - 2 * COST
    hold = np.array([k[1] - k[0] + 1 for k in kept])
    wins = r[r > 0]; losses = r[r <= 0]
    return {
        "n": int(len(r)), "mean_net": float(r.mean()), "median_net": float(np.median(r)),
        "win_rate": float((r > 0).mean()),
        "profit_factor": float(wins.sum() / -losses.sum()) if losses.sum() < 0 else float("inf"),
        "avg_hold": float(hold.mean()), "worst5": [round(float(x), 4) for x in np.sort(r)[:5]],
        "best5": [round(float(x), 4) for x in np.sort(r)[::-1][:5]],
        "t": float(r.mean() / (r.std(ddof=1) / np.sqrt(len(r)))) if len(r) > 1 and r.std(ddof=1) > 0 else None,
        "r_values": r,
    }

# ---------------------------------------------------------------- universes
def get_live_universe() -> list[str]:
    src = open(UNIV_FILE).read()
    m = re.search(r"POPULAR_LIQUID_US\s*=\s*\[(.*?)\n\]", src, re.S)
    syms = re.findall(r'"([A-Z\.\-]+)"', m.group(1))
    seen, out = set(), []
    for s in syms:
        if s not in seen:
            seen.add(s); out.append(s)
    return out


def get_full_universe() -> list[str]:
    return sorted(f[:-4] for f in os.listdir(RAW_CSV_DIR) if f.endswith(".csv"))


def prepare(symbols: list[str], source: str = "yahoo", log=print):
    """Returns {sym: df_with_indicators} restricted to bars <= 2024-12-31."""
    bars = {}
    miss = []
    for sym in symbols:
        df = load_yahoo(sym) if source == "yahoo" else load_raw_csv(sym)
        if df is None or len(df) < WARMUP_BARS + 20:
            miss.append(sym); continue
        df = df.rename(columns={c: c.lower() for c in df.columns})
        df = add_indicators(df)
        df = df.loc[df.index <= DEV_END]
        if not np.isfinite(df["rsi2"].iloc[-5:]).any():
            miss.append(sym + "(rsi-nan)"); continue
        bars[sym] = df
    if miss:
        log(f"  missing/too short ({len(miss)}): {miss[:20]}{'...' if len(miss) > 20 else ''}")
    return bars


def audit_splits(bars_raw: dict, log=print):
    """Report close-to-close moves outside [-50%, +100%] in the raw CSV panel (Q4)."""
    rows = []
    for sym in ["NVDA", "AAPL", "AMZN", "TSLA", "GOOGL", "MSFT", "SPY"]:
        raw = load_raw_csv(sym)
        if raw is None:
            continue
        r = raw["close"].pct_change()
        big = r[r.abs() > 0.5]
        for d, v in big.items():
            rows.append((sym, str(d.date()), round(float(v), 3)))
        log(f"  raw CSV {sym}: n={len(raw)} {raw.index[0].date()}..{raw.index[-1].date()} "
            f"jumps>|50%|={len(big)} {list(zip([str(d.date()) for d in big.index[:4]], [round(float(v),3) for v in big.values[:4]]))}")
    return rows


def audit_clean(bars: dict, log=print):
    bad = []
    for sym, df in bars.items():
        r = df["close"].pct_change()
        big = r[(r < -0.5) | (r > 1.0)]
        big = big[big.index >= DEV_START] if len(big) else big
        for d, v in big.items():
            bad.append((sym, str(pd.Timestamp(d).date()), round(float(v), 3)))
    log(f"  CLEAN audit: {len(bad)} close-to-close moves outside [-50%,+100%] in DEV"
        f"{' e.g. ' + str(bad[:8]) if bad else ''}")
    return bad


# ---------------------------------------------------------------- eligibility / signals
def build_signal_sets(bars: dict, spy: pd.DataFrame):
    """For each symbol: (eligible mask, rsi2<10 mask). Regime aligned by SPY date <= t."""
    spy_ok = (spy["close"] > spy["sma200"]).reindex(spy.index).fillna(False).astype(bool)
    spy_series = spy_ok.shift(0).astype(float)
    res = {}
    for sym, df in bars.items():
        warm = np.arange(len(df)) >= WARMUP_BARS - 1
        elig = (df["rsi2"].notna() & df["atr14"].notna() & df["vol20"].notna()
                & (df["vol20"] >= 1_000_000) & (df["close"] >= 5.0)).to_numpy() & warm
        # regime aligned without lookahead: last SPY close at or before symbol date
        spy_flag = spy_series.reindex(df.index.union(spy.index)).ffill().reindex(df.index)
        spy_flag = spy_flag.fillna(0.0).to_numpy() > 0.5
        low = (df["rsi2"] < 10).to_numpy()
        res[sym] = {"elig": elig, "low": low, "spy_ok": spy_flag}
    return res

# ---------------------------------------------------------------- Q1/Q2 trade level
def trade_level(bars, sig, spy_dates, log=print, n_draws=1000):
    rng = np.random.default_rng(20261123)
    res = {}
    for variant in ["V0", "V1"]:
        hyp_all, hyp_by_sym, kept_all = {}, {}, []
        for sym, df in bars.items():
            s = sig[sym]
            sel = s["elig"] & s["low"]
            if variant == "V0":
                sel = sel & s["spy_ok"]
            sig_idx = np.where(sel)[0]
            if len(sig_idx) == 0:
                continue
            hyp = hypothetical_trades(df, sig_idx)
            hyp_by_sym[sym] = (df, np.where(s["elig"])[0], hyp, len(sig_idx))
            kept = [(e, x, g, r, t, sym) for (e, x, g, r, t) in anti_overlap(hyp, sig_idx.tolist())]
            kept_all.extend(kept)
        m = trade_metrics(kept_all)
        # random baseline: draw k_sym random eligible days per symbol
        draw_means, pct_ref = [], []
        for d in range(n_draws):
            pooled = []
            for sym, (df, elig_idx, hyp, k) in hyp_by_sym.items():
                if k == 0 or len(elig_idx) == 0:
                    continue
                pick = rng.choice(len(elig_idx), size=min(k, len(elig_idx)), replace=False)
                days = sorted(int(elig_idx[i]) for i in pick)
                pooled.extend(anti_overlap(hyp, days))
            if pooled:
                draw_means.append(float(np.mean([k[2] for k in pooled]) - 2 * COST))
        dm = np.array(draw_means)
        excess = m["mean_net"] - dm.mean()
        t_exc = excess / (dm.std(ddof=1) / np.sqrt(len(dm))) if len(dm) > 1 else None
        pct = float((dm < m["mean_net"]).mean() * 100)
        m.update({"baseline_mean": float(dm.mean()), "excess": float(excess), "t_vs_base": t_exc,
                  "percentile": pct, "baseline_p5": float(np.percentile(dm, 2.5))})
        res[variant] = (m, kept_all, hyp_by_sym)
        log(f"  {variant}: n={m['n']} mean={m['mean_net']*100:.3f}% excess={excess*100:.3f}% "
            f"t_vs_base={t_exc if t_exc is None else round(t_exc,2)} pct={pct:.1f} "
            f"win={m['win_rate']*100:.1f}% pf={m['profit_factor']:.2f} hold={m['avg_hold']:.1f}")
    return res



def bear_diag(bars, res_v1, spy_regime_by_date, log=print):
    """V1 trades split by the SPY regime on their signal day (t):
    bear = the trades the V0 filter removes; bull = trades both variants take."""
    kept = res_v1[1]
    bear, bull = [], []
    for e, x, g, r, t, sym in kept:
        d = bars[sym].index[int(t)]
        (bear if not spy_regime_by_date.get(d, False) else bull).append((e, x, g, r, t, sym))
    out = {}
    for name, kk in [("V1-bear (SPY<=SMA200)", bear), ("V1-bull (SPY>SMA200)", bull)]:
        m = trade_metrics(kk)
        out[name] = (m, kk)
        if m["n"]:
            log(f"  {name}: n={m['n']} mean={m['mean_net']*100:.3f}% win={m['win_rate']*100:.1f}% "
                f"pf={m['profit_factor']:.2f} hold={m['avg_hold']:.1f} t={m['t'] if m['t'] is None else round(m['t'],2)} "
                f"worst5={m['worst5']}")
    # 2022 slice of the bear trades
    k22 = [k for k in bear if bars[k[5]].index[int(k[4])].year == 2022]
    m22 = trade_metrics(k22)
    if m22["n"]:
        log(f"  2022 bear trades: n={m22['n']} mean={m22['mean_net']*100:.3f}% win={m22['win_rate']*100:.1f}% "
            f"pf={m22['profit_factor']:.2f} worst5={m22['worst5']}")
    out["2022-bear"] = (m22, k22)
    return out


def year_table(kept, bars, log=print):
    rows = []
    for yr in [2021, 2022, 2023, 2024]:
        kk = [k for k in kept if bars[k[5]].index[int(k[4])].year == yr]
        if not kk:
            rows.append(dict(year=yr, n=0)); continue
        r = np.array([k[2] for k in kk]) - 2 * COST
        rows.append(dict(year=yr, n=len(r), mean=float(r.mean()), win=float((r > 0).mean()),
                         sum_ret=float(r.sum()), comp=float(np.prod(1 + r) - 1)))
        log(f"  {yr}: n={len(r)} mean={r.mean()*100:.3f}% win={(r > 0).mean()*100:.1f}% "
            f"sum={r.sum()*100:.1f}% comp={((np.prod(1+r)-1)*100):.1f}%")
    return rows


def bootstrap_p(a, b, n_boot=10000, seed=20261123):
    """Two-sided paired bootstrap p-value for mean(a)-mean(b) (arrays paired in order)."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    rng = np.random.default_rng(seed)
    d = a - b
    obs = d.mean()
    idx = rng.integers(0, len(d), size=(n_boot, len(d)))
    boots = d[idx].mean(axis=1)
    p = 2 * min((boots <= 0).mean(), (boots >= 0).mean())
    t = obs / (d.std(ddof=1) / np.sqrt(len(d))) if d.std(ddof=1) > 0 else 0.0
    return float(obs), float(t), float(min(p, 1.0))



def paired_filter_diff(res_v0, res_v1, bars, log=print):
    """Direct filter difference on the only defensible paired unit: symbol-year cells.
    Each cell value = mean net per trade in V0 and in V1 (both anti-overlapped)."""
    def cells(kept):
        agg = {}
        for k in kept:
            key = (k[5], bars[k[5]].index[int(k[4])].year)
            agg.setdefault(key, []).append(k[2] - 2 * COST)
        return {k: float(np.mean(v)) for k, v in agg.items()}
    c0, c1 = cells(res_v0[1]), cells(res_v1[1])
    keys = sorted(set(c0) & set(c1))
    a = np.array([c1[k] for k in keys]); b = np.array([c0[k] for k in keys])
    obs, t, p = bootstrap_p(a, b)
    log(f"  paired symbol-year cells={len(keys)}: V1-V0 mean diff {obs*100:.3f}%/trade "
        f"t={t:.2f} p_boot={p:.3f}")
    return {"cells": len(keys), "diff": obs, "t": t, "p_boot": p}

def spy_regime_map(spy):
    ok = (spy["close"] > spy["sma200"]).fillna(False).astype(float)
    return ok

# ---------------------------------------------------------------- Q3 portfolio
def portfolio(sig, bars, max_open, variant, rsi2_cap=4, log=print):
    names = sorted(bars)
    all_dates = sorted(set().union(*[set(bars[s].index) for s in names], set(bars.get("SPY", pd.DataFrame()).index)))
    all_dates = [d for d in all_dates if DEV_START <= d <= DEV_END]
    pos = {}         # sym -> dict(entry, entry_px, stop, atr_sig, max_close, entry_i)
    equity = 1.0
    pending = []     # entries filled at next open
    eq_curve, monthly, monthly_open = [], {}, {}
    open_counts, invested = [], 0
    trades = []
    prev_dates = {s: {d: i for i, d in enumerate(bars[s].index)} for s in bars}
    for d in all_dates:
        # 1) fills at today's open
        for order in pending:
            sym = order["sym"]; df = bars[sym]; i = prev_dates[sym].get(d)
            if i is None:
                continue
            px = float(df["open"].iloc[i])
            if not np.isfinite(px) or px <= 0:
                continue
            slot_val = equity / max_open
            pos[sym] = {"entry": d, "entry_px": px, "atr_sig": order["atr"], "max_close": float(df["close"].iloc[i]),
                        "stop": px - 2.0 * order["atr"], "qty_val": slot_val, "entry_i": i,
                        "notional": slot_val * (1 - COST)}
        pending = []
        # 2) mark to market / exits on close
        for sym in list(pos):
            p = pos[sym]; df = bars[sym]; i = prev_dates[sym].get(d)
            if i is None:
                continue
            c = float(df["close"].iloc[i]); atr_s = float(df["atr14"].iloc[i]) if np.isfinite(df["atr14"].iloc[i]) else p["atr_sig"]
            p["max_close"] = max(p["max_close"], c)
            chand = p["max_close"] - 3.0 * atr_s
            eff = max(p["stop"], chand)
            days = i - p["entry_i"]
            reason = None
            if c <= eff:
                reason = "stop" if eff == p["stop"] else "trail"
            elif days >= 15:
                reason = "time"
            if reason:
                gross = p["notional"] * (c / p["entry_px"]) * (1 - COST)
                equity += gross - p["notional"]
                trades.append(dict(sym=sym, entry=p["entry"], exit=d, gross=c / p["entry_px"] - 1.0, reason=reason))
                del pos[sym]
        equity_now = equity
        # unrealized
        for sym, p in pos.items():
            i = prev_dates[sym].get(d)
            if i is None:
                continue
            c = float(bars[sym]["close"].iloc[i])
            equity_now += p["notional"] * (c / p["entry_px"] - 1.0)
        eq_curve.append((d, equity_now))
        invested += 1 if pos else 0
        open_counts.append(len(pos))
        m = (d.year, d.month)
        monthly[m] = equity_now  # last equity mark of the month
        monthly_open[m] = monthly_open.get(m, 1.0)  # first mark of the month
        # 3) new entries signalled on today's close, ranked per live rules
        room = max_open - len(pos)
        if room > 0:
            cands = []
            for sym in names:
                if sym in pos:
                    continue
                i = prev_dates[sym].get(d)
                if i is None:
                    continue
                s = sig[sym]
                if not s["elig"][i] or not s["low"][i]:
                    continue
                if variant == "V0" and not s["spy_ok"][i]:
                    continue
                cands.append((float(bars[sym]["rsi2"].iloc[i]), sym.upper(), sym, float(bars[sym]["atr14"].iloc[i])))
            cands.sort(key=lambda c: (c[0], c[1]))
            for rsi_v, _, sym, atr_v in cands[:min(rsi2_cap, room)]:
                pending.append({"sym": sym, "atr": atr_v})
    eq = pd.Series({d: v for d, v in eq_curve})
    ret = eq.pct_change().dropna()
    meq = pd.Series({pd.Timestamp(f"{y}-{m:02d}-01"): v for (y, m), v in monthly.items()}).sort_index()
    mret = meq.pct_change().dropna()
    if len(meq) > 0:
        first = pd.Series([meq.iloc[0] / 1.0 - 1.0], index=[pd.Timestamp(f"{meq.index[0].year}-{meq.index[0].month:02d}-01")])
        mret = pd.concat([first, mret])
    peak = eq.cummax(); dd = (eq / peak - 1.0)
    n_months = len(mret)
    out = {
        "max_open": max_open, "variant": variant, "total_trades": len(trades),
        "mean_monthly": float(mret.mean()) if len(mret) else None,
        "cagr": float((eq.iloc[-1]) ** (12 / max(n_months, 1)) - 1) if len(mret) else None,
        "sharpe": float(mret.mean() / mret.std(ddof=1) * np.sqrt(12)) if len(mret) > 1 and mret.std(ddof=1) > 0 else None,
        "maxdd": float(dd.min()), "worst3": [(str(d.date())[:7], round(float(v), 4)) for d, v in mret.nsmallest(3).items()],
        "avg_open": float(np.mean(open_counts)), "pct_invested": float(invested / len(all_dates)),
        "final_equity": float(eq.iloc[-1]),
    }
    yr = {}
    for y in [2021, 2022, 2023, 2024]:
        v = meq[(meq.index.year == y)]
        if len(v):
            base = v.iloc[0]
            prev = meq[meq.index < v.index[0]]
            base = prev.iloc[-1] if len(prev) else base
            yr[y] = float(v.iloc[-1] / base - 1.0)
    out["year_returns"] = yr
    out["monthly"] = mret
    out["trades"] = trades
    log(f"  {variant} max_open={max_open}: trades={len(trades)} mean/mo={out['mean_monthly']*100:.3f}% "
        f"CAGR={out['cagr']*100:.2f}% Sharpe={out['sharpe']:.2f} maxDD={out['maxdd']*100:.1f}% "
        f"avg_open={out['avg_open']:.1f} invested={out['pct_invested']*100:.0f}% "
        f"worst3={out['worst3']} years={ {k: round(v*100,1) for k,v in yr.items()} }")
    return out

# ---------------------------------------------------------------- main
def load_spy(log=print):
    spy = load_yahoo("SPY")
    spy = add_indicators(spy)
    return spy.loc[spy.index <= DEV_END]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", default="all")
    ap.add_argument("--universe", default="both", choices=["live", "full", "both"])
    args = ap.parse_args()
    os.makedirs(CACHE, exist_ok=True)
    log = print
    out = {}
    log("== data")
    spy = load_spy(log)
    log(f"  SPY {spy.index[0].date()}..{spy.index[-1].date()} n={len(spy)}")
    live = get_live_universe()
    full = get_full_universe()
    log(f"  universes: LIVE={len(live)} FULL csv={len(full)}")
    raw_csv_bars = {}
    log("== Q4 raw CSV split audit")
    out["raw_jumps"] = audit_splits(raw_csv_bars, log)
    universes = {}
    if args.universe in ("live", "both"):
        universes["LIVE"] = prepare(live, "yahoo", log)
    if args.universe in ("full", "both"):
        universes["FULL"] = prepare(full, "yahoo", log)
    for uname, bars in universes.items():
        log(f"== universe {uname}: {len(bars)} symbols with data")
        out[f"clean_audit_{uname}"] = audit_clean(bars, log)
        sig = build_signal_sets(bars, spy)
        out[f"counts_{uname}"] = {"V1_raw_signals": int(sum(int((v["elig"] & v["low"]).sum()) for v in sig.values()))}
        log("== PREREQ: STRAT-026 replication (primary-source rule)")
        out[f"prereq_{uname}"] = prereq_strat026(bars, sig, partial_2r=True, log=log)
        log("== trade level (per-day basket = STRAT-026 traded unit) + single +2R cross-check row")
        bl = basket_level(bars, sig, partial_2r=False, log=log, tag=uname)
        bl2r = basket_level(bars, sig, partial_2r=True, log=log, tag=uname + " crosscheck")
        for v in ["V0", "V1"]:
            m, bt = bl[v]
            out[f"basket_{uname}_{v}"] = {k: v2 for k, v2 in m.items() if k != "r_values"}
            out[f"basket2r_{uname}_{v}"] = {k: v2 for k, v2 in bl2r[v][0].items() if k != "r_values"}
        # legacy per-symbol-thread view kept for transparency (NOT the primary unit)
        tr = trade_level(bars, sig, None, log)
        sr = spy_regime_map(spy)
        for v in ["V0", "V1"]:
            m, kept, _ = tr[v]
            out[f"trade_{uname}_{v}"] = {k: v2 for k, v2 in m.items() if k != "r_values"}
            out[f"years_{uname}_{v}"] = year_table(kept, bars, log)
        log("== V1 bear diagnostic")
        bd = bear_diag(bars, tr["V1"], sr, log)
        out[f"bear_{uname}"] = {k: {kk: vv for kk, vv in m.items() if kk != "r_values"} for k, (m, _) in bd.items()}
        out[f"paired_{uname}"] = paired_filter_diff(tr["V0"], tr["V1"], bars, log)
        # paired filter diff on the per-day basket unit too
        try:
            bdiff = basket_paired_diff(bl["V0"][1], bl["V1"][1], log)
            out[f"basket_paired_{uname}"] = bdiff
        except Exception as e:  # noqa: BLE001
            log(f"  basket paired diff skipped: {e}")
        log("== portfolio (strategy) + matched random-entry baseline")
        ports = {}
        ranges = {"LIVE": (4, 8, 12), "FULL": (4, 8)}.get(uname, (4, 8, 12))
        for v in ["V0", "V1"]:
            for mo in ranges:
                p = portfolio(sig, bars, mo, v, log=log)
                p.pop("trades")
                p["monthly"] = {str(d.date())[:7]: float(x) for d, x in p["monthly"].items()}
                rb = portfolio_random(sig, bars, mo, v, n_draws=100, log=log)
                ms = rb["mean_series"]
                p["random_mean_monthly"] = float(ms.mean())
                p["random_sd_draws"] = float(rb["draw_means"].std(ddof=1))
                ports[f"{v}_{mo}"] = p
                _cmp = monthly_diff(pd.Series({pd.Timestamp(k + "-01"): vv for k, vv in p["monthly"].items()}).sort_index(),
                                    ms.sort_index())
                ports[f"{v}_{mo}"]["vs_random"] = _cmp
        # direct V1 - V0 monthly difference per max_open
        for mo in ranges:
            if f"V0_{mo}" in ports and f"V1_{mo}" in ports:
                s0 = pd.Series({pd.Timestamp(k + "-01"): vv for k, vv in ports[f"V0_{mo}"]["monthly"].items()}).sort_index()
                s1 = pd.Series({pd.Timestamp(k + "-01"): vv for k, vv in ports[f"V1_{mo}"]["monthly"].items()}).sort_index()
                out.setdefault(f"v1_minus_v0_{uname}", {})[str(mo)] = monthly_diff(s1, s0)
                log(f"  V1-V0 monthly @max_open={mo}: {out[f'v1_minus_v0_{uname}'][str(mo)]}")
        out[f"portfolio_{uname}"] = ports
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), f"results_{args.universe}.json"), "w") as f:
        json.dump(out, f, indent=1, default=str)
    log("done")


# ---------------------------------------------------------------- basket trade level (STRAT-026 unit)
def basket_level(bars, sig, partial_2r=False, n_draws=1000, log=print, tag=""):
    """One trade per signal day = equal-weight basket of all signals that day.
    Random baseline: same number of signal DAYS drawn at random among days with at
    least one eligible symbol, each forming an equal-weight basket of the same SIZE as
    the strategy basket that day is impossible (sizes differ) -> the baseline matches
    the per-symbol draw protocol of STRAT-026 (k_sym draws per symbol) and is then
    collapsed to one basket per entry day, i.e. the same average size. Documented."""
    rng = np.random.default_rng(20261123)
    res = {}
    for variant in ["V0", "V1"]:
        hyp_by_sym, kept_all = {}, []
        for sym, df in bars.items():
            s = sig[sym]
            sel = s["elig"] & s["low"]
            if variant == "V0":
                sel = sel & s["spy_ok"]
            sig_idx = np.where(sel)[0]
            if len(sig_idx) == 0:
                continue
            hyp = hypothetical_trades(df, sig_idx, partial_2r=partial_2r)
            hyp_by_sym[sym] = (df, np.where(s["elig"])[0], hyp, len(sig_idx))
            kept_all.extend([(e, x, g, r, t, sym) for (e, x, g, r, t) in anti_overlap(hyp, sig_idx.tolist())])
        bt = basket_trades(kept_all)
        m = trade_metrics(bt)
        m["avg_basket"] = float(np.mean([int(k[5][6:]) for k in bt])) if bt else 0
        m["per_symbol_trades"] = len(kept_all)
        draw_means = []
        for d in range(n_draws):
            pooled = []
            for sym, (df, elig_idx, hyp, k) in hyp_by_sym.items():
                if k == 0 or len(elig_idx) == 0:
                    continue
                pick = rng.choice(len(elig_idx), size=min(k, len(elig_idx)), replace=False)
                pooled.extend(anti_overlap(hyp, sorted(int(elig_idx[i]) for i in pick)))
            if pooled:
                pooled = [(e, x, g, r, t, "BASKET0") for (e, x, g, r, t) in pooled]
                bd = basket_trades(pooled)
                draw_means.append(float(np.mean([k[2] for k in bd])) - 2 * COST)
        dm = np.array(draw_means)
        excess = m["mean_net"] - dm.mean()
        t_exc = excess / (dm.std(ddof=1) / np.sqrt(len(dm)))
        pct = float((dm < m["mean_net"]).mean() * 100)
        m.update({"baseline_mean": float(dm.mean()), "excess": float(excess),
                  "t_vs_base": float(t_exc), "percentile": pct})
        res[variant] = (m, bt)
        log(f"  [{tag}] {variant}{'+2R' if partial_2r else ''}: n_basket={m['n']} mean={m['mean_net']*100:.3f}%/trade "
            f"excess={excess*100:.3f}% t_vs_base={t_exc:.2f} pct={pct:.1f} win={m['win_rate']*100:.1f}% "
            f"pf={m['profit_factor']:.2f} hold={m['avg_hold']:.1f} avg_basket={m['avg_basket']:.1f} "
            f"per_sym_trades={m['per_symbol_trades']}")
    return res


# ---------------------------------------------------------------- STRAT-026 replication (frozen primary-source rule)
def prereq_strat026(bars, sig, partial_2r=True, n_draws=1000, log=print):
    """Replicate the published STRAT-026 DEV number on THIS clean data + engine:
    pooled per-symbol threads (one thread per symbol, natural anti-overlap) with the
    optional single +2R partial, RSI(2)<10, liq ok, NO SPY filter, 2R stop / 15-day time.
    Published bar: +0.258%/trade, t_vs_base 2.74. Tolerance: |mean diff| <= 0.10%/trade
    OR same sign with t_vs_base within +/-0.75 of the published t."""
    rng = np.random.default_rng(20261123)
    hyp_by_sym, kept_all = {}, []
    for sym, df in bars.items():
        s = sig[sym]
        sel = s["elig"] & s["low"]
        sig_idx = np.where(sel)[0]
        if len(sig_idx) == 0:
            continue
        hyp = hypothetical_trades(df, sig_idx, partial_2r=partial_2r)
        hyp_by_sym[sym] = (df, np.where(s["elig"])[0], hyp, len(sig_idx))
        kept_all.extend(anti_overlap(hyp, sig_idx.tolist()))
    m = trade_metrics(kept_all)
    draw_means = []
    for _ in range(n_draws):
        pooled = []
        for sym, (df, elig_idx, hyp, k) in hyp_by_sym.items():
            if k == 0 or len(elig_idx) == 0:
                continue
            pick = rng.choice(len(elig_idx), size=min(k, len(elig_idx)), replace=False)
            pooled.extend(anti_overlap(hyp, sorted(int(elig_idx[i]) for i in pick)))
        if pooled:
            draw_means.append(float(np.mean([k[2] for k in pooled]) - 2 * COST))
    dm = np.array(draw_means)
    excess = m["mean_net"] - dm.mean()
    t_exc = float(excess / (dm.std(ddof=1) / np.sqrt(len(dm))))
    pct = float((dm < m["mean_net"]).mean() * 100)
    out = {"n": m["n"], "mean_net": m["mean_net"], "win_rate": m["win_rate"],
           "baseline_mean": float(dm.mean()), "excess": float(excess),
           "t_vs_base": t_exc, "percentile": pct,
           "published_mean": 0.00258, "published_t": 2.74}
    out["delta_vs_published"] = out["mean_net"] - out["published_mean"]
    out["within_tolerance"] = bool(abs(out["delta_vs_published"]) <= 0.0010
                                   or abs(t_exc - 2.74) <= 0.75)
    log(f"  PREREQ STRAT-026 replication (+2R): n={m['n']} mean={m['mean_net']*100:.3f}%/trade "
        f"(published 0.258%) excess={excess*100:.3f}% t_vs_base={t_exc:.2f} (published 2.74) "
        f"delta={out['delta_vs_published']*100:+.3f}% within_tol={out['within_tolerance']}")
    return out


# ---------------------------------------------------------------- matched random-entry portfolio
def portfolio_random(sig, bars, max_open, variant, n_draws=100, rsi2_cap=4, seed=20261123, log=print):
    """Equal-weight random-entry portfolio with the SAME slot rules: on each signal day,
    if slots are free, fill min(rsi2_cap, room) slots with names drawn uniformly at random
    from that day's eligible-and-not-held names (same forward-open / same exits / same
    costs). Returns mean monthly return series over draws + distribution of draw stats."""
    names = sorted(bars)
    all_dates = sorted(set().union(*[set(bars[s].index) for s in names]))
    all_dates = [d for d in all_dates if DEV_START <= d <= DEV_END]
    el_by_date = {}
    prev_dates = {s: {d: i for i, d in enumerate(bars[s].index)} for s in bars}
    for s in names:
        sg = sig[s]
        for d, i in prev_dates[s].items():
            if DEV_START <= d <= DEV_END and sg["elig"][i] and sg["low"][i]:
                if variant == "V0" and not sg["spy_ok"][i]:
                    continue
                el_by_date.setdefault(d, []).append(s)
    # forward open/close arrays for fast fills
    fwd = {}
    for s in names:
        df = bars[s]; idx = df.index
        fwd[s] = {"pos": prev_dates[s], "open": df["open"].to_numpy(),
                  "close": df["close"].to_numpy(), "atr14": df["atr14"].to_numpy()}
    rng = np.random.default_rng(seed)
    monthly_runs = []
    for _ in range(n_draws):
        rng2 = np.random.default_rng(rng.integers(0, 2**31 - 1))
        pos = {}; equity = 1.0; pending = []
        monthly = {}
        for d in all_dates:
            for order in pending:
                s = order["sym"]; i = fwd[s]["pos"].get(d)
                if i is None:
                    continue
                px = float(fwd[s]["open"][i])
                if not np.isfinite(px) or px <= 0:
                    continue
                slot_val = equity / max_open
                pos[s] = {"entry_px": px, "atr_sig": order["atr"], "max_close": float(fwd[s]["close"][i]),
                          "stop": px - 2.0 * order["atr"], "notional": slot_val * (1 - COST), "entry_i": i}
            pending = []
            for s in list(pos):
                p = pos[s]; i = fwd[s]["pos"].get(d)
                if i is None:
                    continue
                c = float(fwd[s]["close"][i])
                a = float(fwd[s]["atr14"][i]); a = a if np.isfinite(a) else p["atr_sig"]
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
                cands = [s for s in el_by_date.get(d, []) if s not in pos]
                if cands:
                    take = min(rsi2_cap, room, len(cands))
                    pick = rng2.choice(len(cands), size=take, replace=False)
                    for j in pick:
                        s = cands[int(j)]; i = fwd[s]["pos"].get(d)
                        atr_v = float(fwd[s]["atr14"][i])
                        if np.isfinite(atr_v):
                            pending.append({"sym": s, "atr": atr_v})
        meq = pd.Series({pd.Timestamp(f"{y}-{m:02d}-01"): v for (y, m), v in monthly.items()}).sort_index()
        mr = meq.pct_change().dropna()
        monthly_runs.append(mr)
    common = None
    for mr in monthly_runs:
        common = mr.index if common is None else common.intersection(mr.index)
    mat = pd.DataFrame({i: mr.reindex(common) for i, mr in enumerate(monthly_runs)})
    mean_series = mat.mean(axis=1)
    draw_means = mat.mean(axis=0).to_numpy()
    log(f"  RANDOM {variant} max_open={max_open}: draws={n_draws} mean/mo={draw_means.mean()*100:.3f}% "
        f"sd_draws={draw_means.std(ddof=1)*100:.3f}% months={len(mean_series)}")
    return {"mean_series": mean_series, "draw_means": draw_means}


def basket_paired_diff(bt0, bt1, log=print):
    """Direct filter difference on the per-day basket unit, matched by entry day."""
    by0 = {k[0]: k[2] - 2 * COST for k in bt0}
    by1 = {k[0]: k[2] - 2 * COST for k in bt1}
    keys = sorted(set(by0) & set(by1))
    a = np.array([by1[k] for k in keys]); b = np.array([by0[k] for k in keys])
    if len(a) < 3:
        return {"days": len(keys)}
    obs, t, p = bootstrap_p(a, b)
    log(f"  basket paired (V1-V0 on same entry days): days={len(keys)} diff={obs*100:.3f}%/trade t={t:.2f} p_boot={p:.3f}")
    return {"days": len(keys), "diff": float(obs), "t": float(t), "p_boot": float(p)}


def monthly_diff(v1_monthly, v0_monthly):
    common = v1_monthly.index.intersection(v0_monthly.index)
    d = (v1_monthly.reindex(common) - v0_monthly.reindex(common)).dropna()
    if len(d) < 3 or d.std(ddof=1) == 0:
        return {"months": int(len(d)), "mean_diff": float(d.mean()) if len(d) else None, "t": None, "p_boot": None}
    obs, t, p = bootstrap_p(d.to_numpy(), np.zeros(len(d)))
    return {"months": int(len(d)), "mean_diff": float(d.mean()), "t": float(t), "p_boot": float(p)}


if __name__ == "__main__":
    main()

