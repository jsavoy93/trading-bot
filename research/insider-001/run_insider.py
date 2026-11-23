#!/usr/bin/env python3
"""INSIDER-001 -- insider buying (SEC Form 4) on top of MOM 12-1 (DEV research).

Implements the FROZEN preregistration (research/insider-001/PREREGISTRATION.md):
  * baseline : MOM 12-1 long-short, equal-weight decile legs (frozen engine)
  * V1       : MOM TILT -- long leg = baseline-size decile taken from the top-20% MOM
               pool, insider-bought names first (by MOM), rest by MOM rank; short leg
               unchanged MOM bottom decile
  * V2       : STANDALONE CLUSTER -- long-only equal-weight all eligible cluster-bought
               names, excess vs the equal-weight eligible universe

Signal at decision date d: qualifying Form 4 purchases with filing date in
[d - 90 days, d) (filing dates strictly before d -- no lookahead); cluster = >= 2
distinct insiders.

DEV window only (months 2021-01 .. 2024-12). Panel rows after 2024-12-31 are never
loaded (parquet row filter + assert); insider filings after 2024-12-31 are rejected.

Research only. Writes results.json + results.md next to this file (git-excluded).
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
sys.path.insert(0, str(HERE.parent / "volsize-001"))
import engine_fixed as eng  # noqa: E402  (vendored frozen engine, verbatim)

# ---------------------------------------------------------------- constants
DEV_START = pd.Timestamp("2021-01-01")
DEV_END = pd.Timestamp("2024-12-31")
LOAD_FLOOR = pd.Timestamp("2018-01-01")

INSIDER_WINDOW_DAYS = 90
CLUSTER_MIN_INSIDERS = 2
POOL_FRACTION = 0.20          # top 20% by MOM 12-1

N_RANDOM_DEFAULT = 1000
SMOKE_DRAWS = 20
SMOKE_MONTHS = 3

BONFERRONI_P = 98.75          # k=2: 100 - 2.5/2
BAR_T = 2.24
BAR_MIN_MONTHS = 24

SEED_V1 = 20261123
SEED_V2 = 20261124

DEFAULT_PANEL = Path("/tmp/panel_dev.parquet")
DEFAULT_INSIDER = HERE / "data" / "insider_purchases.parquet"

# frozen DEV reference (prereg section 3)
REF_MEAN_NET = 0.01708
REF_T_EXCESS = 2.294
REF_MAXDD = -0.277
REF_MEAN_TOL = 1e-6
REF_T_TOL = 1e-2

# synthetic self-test fixtures (phase A: no parquet needed)
FIX_DATES = pd.DatetimeIndex(pd.bdate_range("2020-01-01", "2022-12-31"))
FIX_SYMBOLS = ("AAA", "BBB")


# ================================================================ panel (DEV only)
def _read_dev_frame(path: Path) -> pd.DataFrame:
    """Read ONLY DEV-era rows via pyarrow row filters; assert the window afterwards."""
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


def panel_from_frame(df: pd.DataFrame) -> eng.Panel:
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


def load_panel(path: Path) -> eng.Panel:
    return panel_from_frame(_read_dev_frame(path))


def dev_months(panel: eng.Panel) -> list[eng.MonthRow]:
    out = [m for m in eng.build_months(panel)
           if m.entry_date >= DEV_START and m.exit_date <= DEV_END]
    if not out:
        raise SystemExit("no DEV months available")
    return out


# ================================================================ insider signal
def load_insider(path: Path) -> pd.DataFrame:
    df = pd.read_parquet(path)
    need = {"symbol", "insider_cik", "filing_date"}
    missing = need - set(df.columns)
    if missing:
        raise SystemExit(f"insider file missing columns: {sorted(missing)}")
    df["filing_date"] = pd.to_datetime(df["filing_date"]).dt.normalize()
    assert df["filing_date"].max() <= DEV_END, "insider filing after 2024-12-31"
    df["symbol"] = df["symbol"].astype(str).str.upper().str.strip()
    return df.sort_values("filing_date").reset_index(drop=True)


def insider_signal(filings: pd.DataFrame, symbols: np.ndarray,
                   decision_dates: list[pd.Timestamp]) -> tuple[np.ndarray, np.ndarray]:
    """Return (insider_bought, cluster_bought) boolean arrays (months x symbols).

    A purchase counts only when its FILING date is in [d - 90d, d). Filings on or
    after d are never used. cluster = >= 2 distinct insiders in the same window.
    """
    sym_pos = {s: i for i, s in enumerate(symbols.astype(str))}
    sym_idx = filings["symbol"].map(sym_pos).to_numpy()
    keep = np.isfinite(sym_idx)
    sidx = sym_idx[keep].astype(int)
    fdate = filings["filing_date"].to_numpy()[keep].astype("datetime64[ns]")
    insid = filings["insider_cik"].to_numpy()[keep].astype(np.int64)
    order = np.argsort(fdate, kind="mergesort")
    sidx, fdate, insid = sidx[order], fdate[order], insid[order]

    n_m, n_s = len(decision_dates), len(symbols)
    bought = np.zeros((n_m, n_s), dtype=bool)
    cluster = np.zeros((n_m, n_s), dtype=bool)
    nan = np.datetime64("NaT")
    for t, d in enumerate(decision_dates):
        lo = pd.Timestamp(d - pd.Timedelta(days=INSIDER_WINDOW_DAYS)).to_datetime64()
        hi = pd.Timestamp(d).to_datetime64()          # exclusive
        a = np.searchsorted(fdate, lo, side="left")
        b = np.searchsorted(fdate, hi, side="left")
        if b <= a:
            continue
        win_s, win_i = sidx[a:b], insid[a:b]
        if (fdate[a:b] >= hi).any() or (fdate[a:b] < lo).any():
            raise AssertionError("signal window includes a filing on/after d")
        bought[t, np.unique(win_s)] = True
        pairs = pd.DataFrame({"s": win_s, "i": win_i}).drop_duplicates()
        counts = pairs.groupby("s").size()
        cluster[t, counts[counts >= CLUSTER_MIN_INSIDERS].index.to_numpy()] = True
    return bought, cluster


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
        "monthly_sd": float(net.std(ddof=1)) if len(net) > 1 else float("nan"),
        "win_rate": float(np.mean(net > 0)),
    }
    if excess is not None:
        ex = np.asarray(excess, float)
        out["mean_excess"] = float(ex.mean())
        out["t_excess"] = eng.t_stat(ex)
    return out


# ================================================================ baseline
def baseline_legs(md_list):
    longs, shorts = [], []
    for md in md_list:
        li, si, _ = eng.select_legs(md, "MOM")
        longs.append(li)
        shorts.append(si)
    return longs, shorts


def leg_turnover(legs) -> float:
    """Mean one-way turnover per month: (opened + closed) / names, engine convention."""
    prev = set()
    vals = []
    for leg in legs:
        cur = set(int(s) for s in leg)
        if not cur:
            vals.append(0.0)
            prev = set()
            continue
        vals.append((len(cur - prev) + len(prev - cur)) / len(cur))
        prev = cur
    return float(np.mean(vals)) if vals else float("nan")


# ================================================================ V1 (MOM tilt)
def mom_pool(md: eng.MonthData, frac: float = POOL_FRACTION) -> np.ndarray:
    """Top `frac` of eligible MOM names (MOM >= quantile), descending MOM, ties by symbol."""
    elig = np.flatnonzero(np.isfinite(md.mom) & np.isfinite(md.ret))
    if len(elig) < eng.MIN_ELIGIBLE:
        return np.array([], dtype=int)
    k = int(np.ceil(len(elig) * frac))
    if k < 1:
        k = 1
    values = md.mom[elig]
    order = elig[np.argsort(-values, kind="stable")]   # desc, ties by symbol
    return order[:k]


def v1_legs(md_list, shorts, bought) -> tuple[list[np.ndarray], list[int], list[int]]:
    """Long leg = top n_names of the pool with insider-bought first (by MOM), then
    the rest by MOM rank. Returns (longs, n_names, n_insider_taken)."""
    longs, sizes, taken = [], [], []
    for t, md in enumerate(md_list):
        elig = np.flatnonzero(np.isfinite(md.mom) & np.isfinite(md.ret))
        n_names = int(np.floor(eng.DECILE * len(elig)))
        pool = mom_pool(md)
        if len(pool) == 0 or n_names < 1:
            longs.append(np.array([], dtype=int))
            sizes.append(0)
            taken.append(0)
            continue
        k = min(n_names, len(pool))
        ins = pool[bought[t, pool]]
        chosen = list(ins[:k])
        chosen_set = set(chosen)
        for s in pool:
            if len(chosen) >= k:
                break
            if s not in chosen_set:
                chosen.append(int(s))
        longs.append(np.sort(np.array(chosen, dtype=int)))
        sizes.append(k)
        taken.append(int(min(len(ins), k)))
    return longs, sizes, taken


def v1_random_draws(longs, buys, shorts, draws, n_draws, seed):
    """Random same-count swaps into the long leg from the SAME pool, short leg fixed.
    Returns (n_draws, n_months) net matrix, fully vectorized."""
    rng = np.random.default_rng(seed)
    buy = buys.to_numpy(float)                       # (n_d, n_m) boolean-ish
    out = np.zeros((n_draws, len(longs)))
    prev = np.zeros((n_draws, buy.shape[0]), dtype=bool)
    for t, leg in enumerate(longs):
        k = len(leg)
        if k == 0:
            prev = np.zeros((n_draws, buy.shape[0]), dtype=bool)
            continue
        idx = rng.random((n_draws, buy.shape[0]))
        idx = np.argsort(idx, axis=1)[:, :k]         # uniform k-subset of the pool
        alive = buys.iloc[t].to_numpy(bool)
        # cost: opened + closed + dead, engine convention, / k
        opened = ~prev | (prev & False)
        was = prev[:, idx]                            # (D,k) held last month?
        opened_n = (~was).sum(axis=1)
        closed_n = (prev.sum(axis=1) - was.sum(axis=1))
        dead_n = (~alive[idx]).sum(axis=1)
        cost = eng.COST_PER_SIDE * (opened_n + closed_n + dead_n) / k
        ret_long = buy.iloc[t]["ret_long"].to_numpy(float)
        g = ret_long[idx].mean(axis=1)
        out[:, t] = g - cost
        prev = np.zeros_like(prev)
        np.put_along_axis(prev, idx, was & alive[idx], axis=1)
    return out


# ---------------------------------------------------------------- V2 (cluster)
def universe_masks(panel: eng.Panel, months, md_list):
    """Eligible-universe returns per month (costless, equal weight)."""
    return [np.isfinite(md.mom) | (np.isfinite(panel.close_m[m.decision_idx])
            & (panel.close_m[m.decision_idx] >= eng.MIN_PRICE))
            for md, m in zip(md_list, months)]


def universe_returns(md_list, elig_list) -> np.ndarray:
    """Equal-weight costless return of the eligible universe each month; NaN-free
    per-name returns (engine fallback) are required."""
    out = []
    for md, elig in zip(md_list, elig_list):
        sel = elig & np.isfinite(md.ret)
        out.append(float(np.mean(md.ret[sel])) if sel.any() else 0.0)
    return np.array(out)


def v2_legs(md_list, elig_list, cluster):
    legs = [np.sort(np.flatnonzero(cluster[t] & elig_list[t] & np.isfinite(md.ret)))
            for t, md in enumerate(md_list)]
    return legs


def v2_random_draws(buy, eligs, cluster_counts, n_draws, seed, draws):
    """Random same-count picks from the eligible universe each month, long-only
    equal weight, 0.15%/side on opens and full closes. Vectorized per month."""
    rng = np.random.default_rng(seed)
    buy = buy.to_numpy(float) if hasattr(buy, "to_numpy") else np.asarray(buy, float)
    n_d, n_m = buy.shape
    codes = np.zeros((n_d, n_m), dtype=bool)
    prev = np.zeros_like(codes)
    out = np.zeros((draws, n_m))
    for t in range(n_m):
        k = int(cluster_counts[t])
        if k == 0:
            prev = np.zeros_like(prev)
            continue
        if k > n_d:
            k = n_d
        pick = rng.random((draws, n_d))
        idx = np.argsort(pick, axis=1)[:, :k]
        was = prev[:, idx]
        opened = (~was).sum(axis=1)
        closed = prev.sum(axis=1) - was.sum(axis=1)
        cost = eng.COST_PER_SIDE * (opened + closed) / k
        out[:, t] = buy[idx].mean(axis=1) - cost
        prev = np.zeros_like(prev)
        np.put_along_axis(prev, idx, np.ones_like(was), axis=1)
    return out


# ================================================================ self-test
def self_test_window_cutoff() -> dict:
    """Synthetic check: a purchase filed ON or AFTER the rebalance date is not used."""
    n = len(FIX_DATES)
    open_m = np.tile(np.linspace(10.0, 20.0, n)[:, None], (1, 2))
    close_m = open_m.copy()
    volume_m = np.full((n, 2), 2_000_000.0)
    panel = eng.Panel(FIX_DATES.values.astype("datetime64[ns]"),
                      np.array(FIX_SYMBOLS, dtype=object), open_m, close_m, volume_m)
    d = pd.Timestamp("2021-06-30")
    filing_day = d
    filings = pd.DataFrame({
        "symbol": ["AAA", "BBB"],
        # AAA filed ON d (must be ignored); BBB filed 1 day before d (must be used)
        "filing_date": [filing_day, d - pd.Timedelta(days=1)],
        "insider_cik": [1, 2],
    })
    bought, _ = insider_signal(filings, np.array(FIX_SYMBOLS, dtype=object), [d])
    aaa = int(np.flatnonzero(panel.symbols == "AAA")[0])
    bbb = int(np.flatnonzero(panel.symbols == "BBB")[0])
    ok = (not bought[0, aaa]) and bought[0, bbb]
    if not ok:
        raise AssertionError(
            f"lookahead guard failed: AAA(on d)={bought[0, aaa]} BBB(d-1)={bought[0, bbb]}")
    print("self_test: purchase filed ON the rebalance date is NOT used; d-1 IS used: PASS")
    return {"on_or_after_date_ignored": True, "prior_day_used": True, "pass": True}


# ================================================================ main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="INSIDER-001 insider buying on MOM 12-1 (DEV)")
    ap.add_argument("--n-random", type=int, default=N_RANDOM_DEFAULT,
                    help=f"random draws per variant (default {N_RANDOM_DEFAULT})")
    ap.add_argument("--smoke", action="store_true",
                    help=f"smoke mode: {SMOKE_MONTHS} DEV months, {SMOKE_DRAWS} draws "
                         "unless --n-random overrides")
    ap.add_argument("--panel", default=str(DEFAULT_PANEL), help="DEV parquet panel")
    ap.add_argument("--insider", default=str(DEFAULT_INSIDER), help="insider purchases parquet")
    ap.add_argument("--out-dir", default=None, help="output dir (default: this folder)")
    ap.add_argument("--self-test", action="store_true", help="run only the synthetic check")
    args = ap.parse_args(argv)

    if args.self_test:
        self_test_window_cutoff()
        return 0

    t0 = time.time()
    n_draws = args.n_random
    smoke = args.smoke
    if smoke and (argv is None or "--n-random" not in argv):
        n_draws = min(n_draws, SMOKE_DRAWS)

    panel = load_panel(Path(args.panel))
    months = dev_months(panel)
    if smoke:
        months = months[:SMOKE_MONTHS]
    md_list = eng.compute_month_data(panel, months)
    n_m, n_s = len(md_list), panel.symbols.size

    filings = load_insider(Path(args.insider))
    decision_dates = [m.decision_date for m in months]
    bought_m, cluster_m = insider_signal(filings, panel.symbols, decision_dates)

    # ---- baseline ----
    base_longs, base_shorts = baseline_legs(md_list)
    lr = eng.simulate_leg(md_list, base_longs, "long")
    sr = eng.simulate_leg(md_list, base_shorts, "short")
    base_net = eng.net_series(lr, sr)
    base_summ = summary(base_net)
    print(f"baseline: mean_net {base_net.mean()*100:.4f}%/mo  "
          f"maxDD {base_summ['max_drawdown']*100:.2f}%  n={n_m}", flush=True)

    elig_list = [np.isfinite(md.mom) & np.isfinite(md.ret) for md in md_list]
    n_elig = np.array([int(e.sum()) for e in elig_list])

    # ---- V1 ----
    v1_longs, v1_sizes, v1_taken = v1_legs(md_list, base_shorts, bought_m)
    v1_legs_effective = [longs if len(longs) else np.array([], dtype=int)
                         for longs in v1_longs]
    # short leg is the baseline set, but restricted to the shared month count
    lr1 = eng.simulate_leg(md_list, v1_legs_effective, "long")
    sr1 = eng.simulate_leg(md_list, base_shorts, "short")
    v1_net = eng.net_series(lr1, sr1)

    v1_buy = pd.DataFrame({
        "ret_long": [np.asarray(md.ret[longs], float) if len(longs) else np.zeros(0)
                     for md, longs in zip(md_list, v1_longs)],
        "pool": [mom_pool(md) if len(longs) else np.array([], dtype=int)
                 for md, longs, md2 in zip(md_list, v1_longs, md_list)],
    }).set_index(pd.RangeIndex(n_m))

    # v1_random_draws works from long-leg membership and the top-20% MOM pool returns
    pool_sets = [mom_pool(md) for md in md_list]


    v1_rand = v1_random_matrix(md_list, base_shorts, v1_sizes, base_long_n_of(md_list),
                               bought_m, n_draws)
    v1_rand_means = v1_rand.mean(axis=1) if v1_rand.size else np.zeros(n_draws)
    v1_excess = v1_net - v1_rand_means[:n_m] if v1_rand.size else np.zeros(n_m)

    # ---- V2 ----
    elig_univ = [np.isfinite(md.mom) | (np.isfinite(panel.open_m[mm.entry_idx])
                 & (panel.open_m[mm.entry_idx] > 0)
                 & np.isfinite(panel.close_m[mm.decision_idx])
                 & (panel.close_m[mm.decision_idx] >= eng.MIN_PRICE)
                 & (np.nanmean(panel.volume_m[mm.decision_idx - eng.ADV_WINDOW + 1:
                                               mm.decision_idx + 1], axis=0)
                    >= eng.MIN_ADV_SHARES))
                 for md, mm in zip(md_list, months)]
    elig_univ = [np.asarray(e) | np.isfinite(md.ret) for e, md in zip(elig_univ, md_list)]
    univ_ret = universe_returns(md_list, elig_univ)
    v2_longs = v2_legs(md_list, elig_univ, cluster_m)
    v2_lr = eng.simulate_leg(md_list, v2_longs, "long")
    v2_net = v2_lr.net - v2_lr.cost
    v2_excess = v2_net - univ_ret
    v2_counts = np.array([int(cluster_m[t].sum() & 0) if False else len(v2_longs[t])
                          for t in range(n_m)])

    print(f"V1: mean {v1_net.mean()*100:.3f}%  V2: mean {v2_net.mean()*100:.3f}%",
          flush=True)

    res = _assemble(n_m, n_draws, smoke, t0)
    out_dir = Path(args.out_dir) if args.out_dir else HERE
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(json.dumps(_jsonify(res), indent=2))
    (out_dir / "results.md").write_text(_markdown(res))
    print(f"wrote {out_dir/'results.json'} and results.md in {res['meta']['runtime_sec']}s")
    return 0


def _jsonify(o):
    if isinstance(o, dict):
        return {k: _jsonify(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonify(v) for v in o]
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    return o


def base_long_n_of(md_list):
    return [len(eng.select_legs(md, "MOM")[0]) for md in md_list]


def _assemble(*a, **k):
    raise NotImplementedError


def _markdown(res):
    raise NotImplementedError


if __name__ == "__main__":
    raise SystemExit(main())
