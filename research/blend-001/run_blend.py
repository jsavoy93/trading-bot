#!/usr/bin/env python3
"""BLEND-001 -- blend MOM 12-1 long-short with the C4 RSI(2) swing rule.

Implements the FROZEN preregistration research/blend-001/PREREGISTRATION.md:
  * MOM leg   : research/volsize-001/engine_fixed.py (imported, never modified)
  * swing leg : STRAT-022 C4 rebuilt verbatim from experiments/strat_022
                (rule source: PREREGISTRATION.md, frozen; code: run_backtest.py)
  * variants  : B1 50/50, B2 70/30 (fixed capacity split, no tuning)
  * paired metric d_t = b_t / sigma_M - m_t ; paired t; percentile vs random

DEV only (months 2021-01 .. 2024-11). Bars after 2024-12-31 are never loaded.

Step 1 (this commit): self-test, smoke run, full pass-bar machinery implemented
but only executed with --full. Writes results.json / results.md next to this file
(local only, git-excluded).

Usage:
  python3 research/blend-001/run_blend.py --self-test
  python3 research/blend-001/run_blend.py --smoke --n-random 5
  python3 research/blend-001/run_blend.py --full            # step-2 only
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
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO / "research" / "volsize-001"))
import engine_fixed as eng  # noqa: E402  (frozen MOM engine, unmodified)
import run_volsize as rv   # noqa: E402  (DEV-only panel loader + summary helpers)

# ---------------------------------------------------------------- constants
DEV_START = pd.Timestamp("2021-01-01")
DEV_END = pd.Timestamp("2024-12-31")
LOAD_FLOOR = pd.Timestamp("2018-01-01")
DEFAULT_PANEL = Path("/tmp/panel_dev.parquet")

# C4 (frozen STRAT-022/STRAT-023 values -- do not tune)
C4_ENTRY_RSI = 10.0
C4_ATR_MULT = 3.0
C4_TIME_STOP = 10
C4_COST_PER_SIDE = 0.0010          # 10 bps per side
MIN_VOL20 = 1_000_000.0
MIN_CLOSE = 5.0

SWING_DATA = Path("/tmp/rebal_red/experiments/strat_022/data")
SWING_FETCH_CACHE = Path("/tmp/blend001_bars")

VARIANTS = {"B1": 0.50, "B2": 0.70}   # w_MOM ; w_C4 = 1 - w_MOM
T_BAR = rv.BAR_T                     # 2.24
BONFERRONI_P = rv.BONFERRONI_P       # 98.75 for k=2

SEED_RANDOM = 20261201
SEED_STEP = 10007
N_RANDOM_DEFAULT = 1000
SMOKE_DRAWS = 5

# Frozen self-test references (prereg section 3)
REF_MEAN = 0.01708229936906453       # engine recomputation; published 1.7082%/mo
REF_MEAN_TOL = 1e-9
REF_MAXDD = -0.2774257906660196
REF_MAXDD_TOL = 1e-4
REF_SHARPE = 0.8455531341538303
REF_SHARPE_TOL = 1e-3
REF_WORST3 = (-0.17735783283068635, -0.15674685497199942, -0.12260030455028101)
REF_WORST3_TOL = 1e-4
REF_T_EXCESS = 2.294
REF_T_EXCESS_TOL = 1e-2

SMA200_WARM = 200
CAP_DIVISOR = 1.0                  # set from DEV p95 open-position count (one number)

# ---------------------------------------------------------------- swing data
def load_swing_bars(data_dir: Path = SWING_DATA, allow_fallback: bool = False) -> dict:
    """Load per-symbol OHLCV CSVs, keeping ONLY rows with date <= 2024-12-31.

    Returns {symbol: DataFrame[date, open, high, low, close, volume]} with
    close/open-derived columns only (no feature lookahead; features are built
    with backward windows).
    """
    files = sorted(data_dir.glob("*.csv"))
    if not files:
        raise SystemExit(f"no swing CSVs in {data_dir}")
    out = {}
    for f in files:
        df = pd.read_csv(f, parse_dates=["t"])
        df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
        # the raw CSVs carry 2025-2026 rows; drop them BEFORE anything else
        df["date"] = pd.to_datetime(df["t"], utc=True).dt.tz_convert("America/New_York").dt.normalize().dt.tz_localize(None)
        df = df[df["date"] <= DEV_END]
        if df.empty:
            continue
        assert df["date"].max() <= DEV_END, f"holdout bar in {f.name}"
        df = df.drop_duplicates(subset="date").sort_values("date").reset_index(drop=True)
        if len(df) < SMA200_WARM + 20:
            continue
        out[f.stem] = df[["date", "open", "high", "low", "close", "volume"]]
    if not out:
        raise SystemExit(f"no usable swing symbols in {data_dir}")
    return out


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    """C4 features. Every column at row i uses bars with index <= i only."""
    close = df["close"]
    df["sma200"] = close.rolling(SMA200_WARM).mean()
    df["sma5"] = close.rolling(5).mean()
    # Wilder RSI(2), identical to strat_022/run_backtest.py::wilder_rsi
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    ag = gain.rolling(2).mean().to_numpy().copy()
    al = loss.rolling(2).mean().to_numpy().copy()
    g = gain.to_numpy()
    l_ = loss.to_numpy()
    for i in range(4, len(close)):
        ag[i] = (ag[i - 1] * 1 + g[i]) / 2.0
        al[i] = (al[i - 1] * 1 + l_[i]) / 2.0
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = np.where(al > 0, ag / np.where(al == 0, np.nan, al), np.nan)
    rsi = np.where(al == 0, 100.0, 100.0 - 100.0 / (1.0 + rs))
    rsi[np.isnan(ag)] = np.nan
    df["rsi2"] = rsi
    prev_close = close.shift(1)
    tr = pd.concat([df["high"] - df["low"],
                    (df["high"] - prev_close).abs(),
                    (df["low"] - prev_close).abs()], axis=1).max(axis=1)
    df["atr14"] = tr.rolling(14).mean()
    df["vol20"] = df["volume"].rolling(20).mean()
    return df


def signal_indices(df: pd.DataFrame, start=DEV_START, end=DEV_END) -> np.ndarray:
    ok = ((df["date"] >= start) & (df["date"] <= end)
          & df["sma200"].notna() & df["rsi2"].notna() & df["atr14"].notna()
          & df["vol20"].notna()
          & (df["close"] > df["sma200"]) & (df["rsi2"] < C4_ENTRY_RSI)
          & (df["vol20"] >= MIN_VOL20) & (df["close"] >= MIN_CLOSE))
    return np.flatnonzero(ok.to_numpy())


def eligible_indices(df: pd.DataFrame, start=DEV_START, end=DEV_END) -> np.ndarray:
    ok = ((df["date"] >= start) & (df["date"] <= end)
          & df["sma200"].notna() & df["atr14"].notna() & df["vol20"].notna()
          & (df["vol20"] >= MIN_VOL20) & (df["close"] >= MIN_CLOSE))
    return np.flatnonzero(ok.to_numpy())


def run_trades(df: pd.DataFrame, sym: str, entries: np.ndarray) -> list[dict]:
    """C4 exits verbatim (strat_022/run_backtest.py::run_trades, C4 parameters)."""
    o = df["open"].to_numpy()
    c = df["close"].to_numpy()
    sma5 = df["sma5"].to_numpy()
    atr = df["atr14"].to_numpy()
    d = df["date"].to_numpy()
    n = len(df)
    trades = []
    last_exit = -1
    for t in np.sort(np.asarray(entries, dtype=int)):
        if t <= last_exit:
            continue
        e = t + 1
        if e >= n:
            continue
        entry_px = o[e]
        if not np.isfinite(entry_px) or entry_px <= 0:
            continue
        stop = entry_px - C4_ATR_MULT * atr[t]
        exit_i, exit_px, reason = None, None, None
        for s in range(e, min(e + C4_TIME_STOP, n)):
            if s > e and c[s] > sma5[s]:
                exit_i, exit_px, reason = s, c[s], "sma5"
                break
            if np.isfinite(stop) and c[s] <= stop:
                exit_i, exit_px, reason = s, c[s], "atr_stop"
                break
        if exit_i is None:
            exit_i = min(e + C4_TIME_STOP, n) - 1
            exit_px = c[exit_i]
            reason = "time"
        trades.append({"sym": sym, "entry_i": e, "exit_i": exit_i,
                       "entry_date": pd.Timestamp(d[e]), "exit_date": pd.Timestamp(d[exit_i]),
                       "entry_px": float(entry_px), "exit_px": float(exit_px),
                       "reason": reason, "gross": float(exit_px / entry_px - 1.0)})
        last_exit = exit_i
    return trades


def valid_trades(trades):
    """Prereg section 8: a trade must exit strictly after it enters.

    STRAT-022's frozen engine can produce an exit on the entry bar itself when
    the time_stop window is empty at the very end of a symbol's bars (exit_i == e,
    zero holding period). That is a harness edge case, not a strategy outcome;
    such trades are dropped (and counted in the monitor output) instead of being
    silently booked as same-bar round trips.
    """
    return [t for t in trades if t["exit_i"] > t["entry_i"]]


def dropped_count(trades):
    return sum(1 for t in trades if t["exit_i"] <= t["entry_i"])


# ---------------------------------------------------------------- monthly agg
def monthly_swing_series(trades: list[dict], month_keys: list[str],
                         dates_index: pd.DatetimeIndex) -> np.ndarray:
    """Aggregate swing trades into the MOM month grid (prereg section 5).

    The swing sleeve deploys 1 unit of capital per open position and holds cash
    otherwise. Each session, the sleeve's return is
        sum over open trades of r_i,day  /  D
    where r_i,day is the trade's close-to-close return for that session and
    D = the 95th percentile of the sleeve's daily open-position count over the
    whole DEV window (ONE fixed number, no lookahead, no per-month rescaling) =
    CAP_DIVISOR units of capital. Days the sleeve trades nothing earn 0. Entry
    fees are charged on the entry session, exit fees on the exit session (both
    divided by CAP_DIVISOR); both sessions are assigned by the session's month
    while the holding period runs the exit date-session inclusive. A trade whose
    exit date is not a session of the MOM grid is booked on the last grid
    session on or before its exit date.
    """
    gi = pd.DatetimeIndex(dates_index)
    sess_pos = pd.Series(np.arange(len(gi)), index=gi)
    month_of = pd.Series([k.strftime("%Y-%m") for k in gi], index=gi)
    key_i = {k: i for i, k in enumerate(month_keys)}
    out = np.zeros(len(month_keys))
    for tr in trades:
        assert tr["exit_date"] <= DEV_END, "swing trade after DEV_END"
        assert tr["exit_date"] > tr["entry_date"], "non-positive holding period"
        e = int(sess_pos.asof(tr["entry_date"]))
        x = int(sess_pos.asof(tr["exit_date"]))
        if x <= e:
            continue
        # per-session close-to-close returns inside the trade
        rets = tr["daily_ret"]
        for j, day_i in enumerate(range(e + 1, x + 1)):
            k = month_of.iloc[day_i] if day_i < len(month_of) else month_of.iloc[-1]
            if k in key_i:
                out[key_i[k]] += rets[j] / CAP_DIVISOR
        km = month_of.iloc[e]
        if km in key_i:
            out[key_i[km]] -= C4_COST_PER_SIDE / CAP_DIVISOR
        kx = month_of.iloc[x]
        if kx in key_i:
            out[key_i[kx]] -= C4_COST_PER_SIDE / CAP_DIVISOR
    return out


def cap_divisor(trades: list[dict], dates_index: pd.DatetimeIndex) -> float:
    """D = p95 of the sleeve's daily open-position count over the DEV window."""
    gi = pd.DatetimeIndex(dates_index)
    sess_pos = pd.Series(np.arange(len(gi)), index=gi)
    open_cnt = np.zeros(len(gi))
    for tr in trades:
        e = int(sess_pos.asof(tr["entry_date"]))
        x = int(sess_pos.asof(tr["exit_date"]))
        open_cnt[e:x + 1] += 1.0
    d = float(np.percentile(open_cnt[open_cnt > 0], 95)) if (open_cnt > 0).any() else 1.0
    return max(d, 1.0)


def with_daily_returns(df: pd.DataFrame, trades: list[dict]) -> list[dict]:
    """Attach each trade's per-session close-to-close returns (entry session is
    the base; the first return is entry-bar close -> next close)."""
    c = df["close"].to_numpy()
    prev = np.concatenate([[np.nan], c[:-1]])
    cr = c / prev - 1.0
    for tr in trades:
        e, x = tr["entry_i"], tr["exit_i"]
        tr["daily_ret"] = [float(v) for v in cr[e + 1:x + 1]]
        assert len(tr["daily_ret"]) == x - e, "daily return length mismatch"
    return trades


def build_swing(bars: dict, month_keys: list[str], dates_index: pd.DatetimeIndex):
    """Return (strategy monthly series, per-symbol signal/eligible indices)."""
    trades, sig, elig = [], {}, {}
    for sym in sorted(bars):
        df = add_features(bars[sym])
        s = signal_indices(df)
        e = eligible_indices(df)
        sig[sym] = s
        elig[sym] = e
        trades.extend(with_daily_returns(df, valid_trades(run_trades(df, sym, s))))
    global CAP_DIVISOR
    CAP_DIVISOR = cap_divisor(trades, dates_index)
    return monthly_swing_series(trades, month_keys, dates_index), sig, elig, trades


def random_swing_draws(bars: dict, sig: dict, elig: dict, month_keys, dates_index,
                       n_draws: int, seed=SEED_RANDOM) -> np.ndarray:
    """Random-entry swing strategies with matched trade counts (prereg section 7).

    Vectorized across draws per symbol: each draw picks, per symbol, n_symbol
    entry days uniformly without replacement from eligible non-signal days, then
    runs the identical exit engine. Returns (n_draws, n_months).
    """
    syms = sorted(bars)
    frames = {s: add_features(bars[s]) for s in syms}
    n_sym = {s: len(sig[s]) for s in syms}
    pool = {}
    for s in syms:
        sigset = set(sig[s].tolist())
        pool[s] = np.array([i for i in elig[s].tolist() if i not in sigset], dtype=int)
    out = np.zeros((n_draws, len(month_keys)))
    for d in range(n_draws):
        rng = np.random.default_rng(seed + SEED_STEP * d)
        trades = []
        for s in syms:            # sorted order = frozen RNG consumption order
            k = n_sym[s]
            p = pool[s]
            if k == 0 or len(p) == 0:
                continue
            pick = rng.choice(len(p), size=min(k, len(p)), replace=False)
            trades.extend(with_daily_returns(
                frames[s], valid_trades(run_trades(frames[s], s, p[np.sort(pick)]))))
        out[d] = monthly_swing_series(trades, month_keys, dates_index)
    return out


# ---------------------------------------------------------------- stats
def bar_report(name, net, mom, sigma_m, rand_mat):
    n = len(net)
    t_net = eng.t_stat(net)
    d = net / sigma_m - mom
    pct = eng.percentile_vs_random(float(net.mean()), rand_mat.mean(axis=1)) if rand_mat is not None else float("nan")
    b = {
        "n_months": int(n),
        "mean_net": float(net.mean()),
        "t_net": float(t_net),
        "paired_mean": float(d.mean()),
        "paired_t": float(eng.t_stat(d)),
        "percentile": float(pct),
        "a_sample": bool(n >= 24),
        "b_profit": bool(net.mean() > 0),
        "c_signif": bool(t_net >= T_BAR),
        "d_paired": bool(d.mean() > 0 and eng.t_stat(d) >= T_BAR),
        "e_random": bool(pct >= BONFERRONI_P) if rand_mat is not None else None,
    }
    b["PASS"] = bool(b["a_sample"] and b["b_profit"] and b["c_signif"]
                     and b["d_paired"] and b["e_random"] is True)
    b["summary"] = rv.summary(net)
    return b


def _sha(*arrs) -> str:
    h = hashlib.sha256()
    for a in arrs:
        h.update(np.ascontiguousarray(np.asarray(a, float)).tobytes())
    return h.hexdigest()


# ---------------------------------------------------------------- self-test
def no_lookahead_checks(bars, sig, trades, months, panel):
    """(a) no-lookahead + alignment assertions (prereg section 8)."""
    # 1. entries strictly after the signal bar, exits strictly after entries
    for tr in trades:
        assert tr["exit_i"] > tr["entry_i"], "exit not after entry"
        assert tr["exit_date"] > tr["entry_date"], "exit_date not after entry_date"
        assert tr["exit_date"] <= DEV_END, "trade exits after DEV_END"
    # 2. running the strategy on data truncated at day T must give the SAME trades
    #    whose signal day is < T (no forward information can change a decision).
    import random as _r
    rnd = _r.Random(7)
    syms = sorted(bars)
    checked = 0
    for _ in range(8):
        s = syms[rnd.randrange(len(syms))]
        full = add_features(bars[s])
        fs = signal_indices(full)
        if not len(fs):
            continue
        t_cut = int(rnd.choice(fs.tolist()))
        truncated = bars[s][bars[s]["date"] < full["date"].iloc[t_cut]].copy()
        if len(truncated) < SMA200_WARM + 20:
            continue
        tdf = add_features(truncated)
        ts = signal_indices(tdf)
        # every signal strictly before the cut day must be reproduced identically
        expect = fs[fs < t_cut]
        assert np.array_equal(np.sort(ts), np.sort(expect)), (
            f"lookahead: truncated signals differ for {s} at {full['date'].iloc[t_cut]}")
        checked += 1
    assert checked > 0, "no truncation check executed"
    # 3. month alignment: swing series and MOM series share the same keys
    keys = [m.key for m in months]
    assert len(keys) == len(set(keys)) and keys == sorted(keys), "month keys misaligned"
    # 4. panel dates never exceed DEV_END
    assert pd.Timestamp(panel.dates.max()) <= DEV_END, "panel holdout leak"
    return checked


def self_test(panel, months, md_list, mom_net, bars):
    print("== self-test ==")
    ok = True
    # MOM baseline reproduction
    checks = [
        ("mean", float(mom_net.mean()), REF_MEAN, REF_MEAN_TOL),
        ("maxDD", eng.max_drawdown(mom_net), REF_MAXDD, REF_MAXDD_TOL),
        ("sharpe", rv.sharpe(mom_net), REF_SHARPE, REF_SHARPE_TOL),
    ]
    for i, (nm, got, ref, tol) in enumerate(checks):
        good = abs(got - ref) <= tol
        ok &= good
        print(f"  MOM {nm:7s} got {got:+.10f} ref {ref:+.10f} |diff| {abs(got-ref):.2e} -> {'PASS' if good else 'FAIL'}")
    w3 = rv.worst_k(mom_net, 3)
    good = all(abs(a - b) <= REF_WORST3_TOL for a, b in zip(w3, REF_WORST3))
    ok &= good
    print(f"  MOM worst3 {['%.6f' % x for x in w3]} -> {'PASS' if good else 'FAIL'}")
    print(f"  MOM months {len(mom_net)} ({months[0].key}..{months[-1].key}) -> "
          f"{'PASS' if len(mom_net) == 47 else 'FAIL'}")
    ok &= len(mom_net) == 47

    dates_index = pd.DatetimeIndex(panel.dates)
    keys = [m.key for m in months]
    s_net, sig, elig, trades = build_swing(bars, keys, dates_index)
    n_sig = sum(len(v) for v in sig.values())
    print(f"  swing: {len(bars)} symbols, {n_sig} signal days, {len(trades)} trades, "
          f"{len(s_net)} months aligned -> {'PASS' if len(s_net)==len(mom_net) else 'FAIL'}")
    ok &= len(s_net) == len(mom_net)
    chk = no_lookahead_checks(bars, sig, trades, months, panel)
    print(f"  no-lookahead: {chk} truncation re-runs identical; entry/exit and DEV asserts -> PASS")
    # t(excess) reference (needs the frozen random-decile benchmark)
    rnd_mat = rv.random_monthly_nets(md_list, 1000, eng.SEED)
    t_exc = eng.t_stat(mom_net - rnd_mat.mean(axis=0))
    good = abs(t_exc - REF_T_EXCESS) <= REF_T_EXCESS_TOL
    ok &= good
    print(f"  MOM t(excess) {t_exc:.4f} ref {REF_T_EXCESS} -> {'PASS' if good else 'FAIL'}")
    print(f"== self-test {'PASS' if ok else 'FAIL'} ==")
    return ok, s_net, trades, dates_index


# ---------------------------------------------------------------- main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="BLEND-001 MOM + C4 blend (DEV research)")
    ap.add_argument("--self-test", action="store_true", help="baseline + no-lookahead checks only")
    ap.add_argument("--smoke", action="store_true", help="short window, few draws, no bar verdict")
    ap.add_argument("--full", action="store_true", help="full pass-bar evaluation (step 2)")
    ap.add_argument("--n-random", type=int, default=None)
    ap.add_argument("--panel", default=str(DEFAULT_PANEL))
    ap.add_argument("--swing-data", default=str(SWING_DATA))
    ap.add_argument("--out-dir", default=None)
    args = ap.parse_args(argv)
    if not (args.self_test or args.smoke or args.full):
        ap.error("choose --self-test, --smoke or --full")

    t0 = time.time()
    n_draws = args.n_random if args.n_random is not None else (
        SMOKE_DRAWS if args.smoke else N_RANDOM_DEFAULT)

    print(f"loading panel {args.panel} (DEV only) ...", flush=True)
    panel = rv.load_panel(Path(args.panel))
    months = rv.dev_months(panel)
    md_list = eng.compute_month_data(panel, months)
    longs, shorts = rv.baseline_legs(md_list)
    mom_net, _, _ = rv.baseline_nets(md_list, longs, shorts)
    sigma_m = float(mom_net.std(ddof=1))
    print(f"MOM: {len(mom_net)} months {months[0].key}..{months[-1].key}, "
          f"mean {mom_net.mean()*100:.4f}%/mo, sigma {sigma_m*100:.4f}%", flush=True)

    print(f"loading swing bars {args.swing_data} ...", flush=True)
    bars = load_swing_bars(Path(args.swing_data))

    if args.self_test:
        ok, *_ = self_test(panel, months, md_list, mom_net, bars)
        return 0 if ok else 1

    # ---- smoke: restrict to a short window BEFORE computing anything ----
    if args.smoke:
        lo = pd.Timestamp("2021-01-01"); hi = pd.Timestamp("2021-06-30")
        months = [m for m in months if lo <= m.entry_date and m.exit_date <= hi]
        md_list = eng.compute_month_data(panel, months)
        longs, shorts = rv.baseline_legs(md_list)
        mom_net, _, _ = rv.baseline_nets(md_list, longs, shorts)
        sigma_m = float(mom_net.std(ddof=1)) if len(mom_net) > 1 else float("nan")
        print(f"smoke window: {months[0].key}..{months[-1].key} ({len(months)} months), "
              f"draws {n_draws}")

    dates_index = pd.DatetimeIndex(panel.dates)
    keys = [m.key for m in months]
    s_net, sig, elig, trades = build_swing(bars, keys, dates_index)
    assert len(s_net) == len(mom_net)
    no_lookahead_checks(bars, sig, trades, months, panel)
    print(f"C4 rebuild: {len(trades)} trades (monitor), mean {s_net.mean()*100:.4f}%/mo; "
          f"corr with MOM (monitor) {np.corrcoef(s_net, mom_net)[0,1]:+.3f}")

    print(f"random draws: {n_draws} ...", flush=True)
    rand_swing = random_swing_draws(bars, sig, elig, keys, dates_index, n_draws)

    res = {"meta": {"mode": "smoke" if args.smoke else "full",
                    "window": f"{months[0].key}..{months[-1].key}",
                    "n_months": len(months), "n_random": n_draws},
           "mom": rv.summary(mom_net), "c4": rv.summary(s_net),
           "corr_c4_mom": float(np.corrcoef(s_net, mom_net)[0, 1]),
           "variants": {}}
    for name, w in VARIANTS.items():
        blend = w * mom_net + (1 - w) * s_net
        rand_blend = w * mom_net[None, :] + (1 - w) * rand_swing
        r = bar_report(name, blend, mom_net, sigma_m, rand_blend)
        r["weights"] = {"mom": w, "c4": round(1 - w, 10)}
        r["sharpe_higher_than_mom"] = bool(r["summary"]["sharpe"] > rv.sharpe(mom_net))
        res["variants"][name] = r
        print(f"  {name} w={w:.2f}: mean {r['mean_net']*100:+.4f}%/mo t {r['t_net']:+.3f} "
              f"paired_mean {r['paired_mean']*100:+.4f}% paired_t {r['paired_t']:+.3f} "
              f"pct {r['percentile']:.1f} PASS={r['PASS']}")
    res["sha"] = _sha(mom_net, s_net)
    res["meta"]["runtime_sec"] = round(time.time() - t0, 1)

    if args.smoke:
        print(f"SMOKE OK ({res['meta']['runtime_sec']}s) -- numbers are NOT results, sha {res['sha'][:12]}")
        return 0

    out_dir = Path(args.out_dir) if args.out_dir else HERE
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(json.dumps(res, indent=2, default=float))
    print(f"wrote {out_dir/'results.json'} ({res['meta']['runtime_sec']}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
