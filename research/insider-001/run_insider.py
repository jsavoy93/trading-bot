#!/usr/bin/env python3
"""INSIDER-001 -- insider buying (SEC Form 4) on top of MOM 12-1 (DEV research only).

Implements the FROZEN preregistration (research/insider-001/PREREGISTRATION.md):

  baseline MOM 12-1 long-short, equal-weight decile legs (frozen engine, verbatim)
  V1       MOM TILT: long leg = baseline-size decile taken from the top-20%-MOM pool,
           insider-bought names FIRST (MOM desc), then the rest by MOM rank; the short
           leg is the unchanged baseline bottom decile.
  V2       STANDALONE CLUSTER: long-only equal-weight all eligible cluster-bought names
           (>= 2 distinct insiders), excess vs the equal-weight costless universe.

Signal at decision date d: qualifying Form 4 purchases with FILING date in
[d - 90 days, d) -- filing dates strictly before d, so a filing dated d is never used.

DEV window only (entry open >= 2021-01-01, exit open <= 2024-12-31). Price rows after
2024-12-31 are never read (parquet row filter + assert after read). Insider filings
after 2024-12-31 are rejected.

Research only. Writes results.json + results.md (git-excluded, DEV numbers).

  python3 research/insider-001/run_insider.py              # full run (1000 draws)
  python3 research/insider-001/run_insider.py --smoke      # 3 months, 20 draws
  python3 research/insider-001/run_insider.py --self-test  # synthetic no-lookahead check
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "volsize-001"))
import engine_fixed as eng  # noqa: E402  frozen engine, imported verbatim (no copy)

# ---------------------------------------------------------------- constants
DEV_START = pd.Timestamp("2021-01-01")
DEV_END = pd.Timestamp("2024-12-31")
LOAD_FLOOR = pd.Timestamp("2019-01-01")   # panel starts 2019-01-02; nothing later is a choice

INSIDER_WINDOW_DAYS = 90
CLUSTER_MIN_INSIDERS = 2
POOL_FRACTION = 0.20

N_RANDOM_DEFAULT = 1000
SMOKE_DRAWS = 20
SMOKE_MONTHS = 3
CHUNK = 1000                  # draws evaluated per simulation pass (memory bound)

BONFERRONI_P = 98.75
BAR_T = 2.24
BAR_MIN_MONTHS = 24

SEED_BASELINE = 20261121       # baseline bar (same-count random lists)
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

FIX_SYMBOLS = np.array(("AAA", "BBB"), dtype=object)
FIX_DATES = pd.bdate_range("2020-01-01", "2022-12-31")


# ================================================================ panel (DEV only)
def read_dev_frame(path: Path) -> pd.DataFrame:
    """Read ONLY <= 2024-12-31 rows via a pyarrow row filter; assert the window after."""
    if not path.exists():
        raise SystemExit(f"panel not found: {path}")
    with warnings.catch_warnings():          # engine's own nanmean warnings are expected
        warnings.simplefilter("ignore", RuntimeWarning)
        table = pq.read_table(
            path, columns=["symbol", "date", "open", "close", "volume"],
            filters=[("date", ">=", LOAD_FLOOR.to_pydatetime()),
                     ("date", "<=", DEV_END.to_pydatetime())],
        )
    df = table.to_pandas()
    if df.empty:
        raise SystemExit(f"panel read produced no rows: {path}")
    df["date"] = pd.to_datetime(df["date"])
    assert df["date"].max() <= DEV_END, "loaded a price row after 2024-12-31 (holdout leak)"
    assert df["date"].min() >= LOAD_FLOOR, "loaded a price row before the load floor"
    return df


def panel_from_frame(df: pd.DataFrame) -> eng.Panel:
    """Same matrix construction as eng.load_panel, from an already-filtered frame."""
    df = df.sort_values(["symbol", "date"], kind="mergesort").reset_index(drop=True)
    dates = np.sort(df["date"].unique())
    symbols = np.sort(df["symbol"].unique())
    di = np.searchsorted(dates, df["date"].values.astype("datetime64[ns]"))
    si = np.searchsorted(np.sort(symbols.astype(str)), df["symbol"].values.astype(str))
    n_t, n_s = len(dates), len(symbols)

    def build(col: str) -> np.ndarray:
        m = np.full((n_t, n_s), np.nan, dtype=np.float64)
        m[di, si] = df[col].to_numpy(dtype=np.float64)
        return m

    return eng.Panel(dates, symbols, build("open"), build("close"), build("volume"))


def dev_months(panel: eng.Panel) -> list[eng.MonthRow]:
    out = [m for m in eng.build_months(panel)
           if m.entry_date >= DEV_START and m.exit_date <= DEV_END]
    if not out:
        raise SystemExit("no DEV months available")
    assert all(m.exit_date <= DEV_END for m in out)
    return out


# ================================================================ insider signal
def load_insider(path: Path) -> pd.DataFrame:
    df = pd.read_parquet(path)
    need = {"symbol", "insider_cik", "filing_date"}
    missing = need - set(df.columns)
    if missing:
        raise SystemExit(f"insider file missing columns: {sorted(missing)}")
    df["filing_date"] = pd.to_datetime(df["filing_date"]).dt.normalize()
    if df["filing_date"].max() > DEV_END:
        raise SystemExit("insider file has filings after 2024-12-31")
    df["symbol"] = df["symbol"].astype(str).str.upper().str.strip()
    return df.sort_values("filing_date", kind="mergesort").reset_index(drop=True)


def insider_signal(filings: pd.DataFrame, symbols: np.ndarray,
                   decision_dates: list[pd.Timestamp]) -> tuple[np.ndarray, np.ndarray]:
    """(bought, cluster) boolean (n_months, n_symbols).

    A purchase counts only if its FILING date is in [d - 90d, d). Filings on or after
    d are never used. cluster = >= CLUSTER_MIN_INSIDERS distinct insiders in the window.
    """
    sym_pos = {s: i for i, s in enumerate(symbols.astype(str))}
    pos = filings["symbol"].map(sym_pos)
    keep = pos.notna().to_numpy()
    sidx = pos[keep].to_numpy(dtype=np.int64)
    fdate = filings.loc[keep, "filing_date"].to_numpy().astype("datetime64[ns]")
    insid = filings.loc[keep, "insider_cik"].to_numpy(dtype=np.int64)
    order = np.argsort(fdate, kind="mergesort")
    sidx, fdate, insid = sidx[order], fdate[order], insid[order]

    n_m, n_s = len(decision_dates), symbols.size
    bought = np.zeros((n_m, n_s), dtype=bool)
    cluster = np.zeros((n_m, n_s), dtype=bool)
    for t, d in enumerate(decision_dates):
        lo = (pd.Timestamp(d) - pd.Timedelta(days=INSIDER_WINDOW_DAYS)).to_datetime64()
        hi = pd.Timestamp(d).to_datetime64()                 # exclusive
        a = np.searchsorted(fdate, lo, side="left")
        b = np.searchsorted(fdate, hi, side="left")
        if b <= a:
            continue
        w_s, w_i, w_d = sidx[a:b], insid[a:b], fdate[a:b]
        assert (w_d < hi).all(), "signal window includes a filing on/after d"
        assert (w_d >= lo).all(), "signal window includes a filing before d-90d"
        bought[t, np.unique(w_s)] = True
        pairs = np.unique(np.stack([w_s, w_i], axis=1), axis=0)
        syms, counts = np.unique(pairs[:, 0], return_counts=True)
        cluster[t, syms[counts >= CLUSTER_MIN_INSIDERS]] = True
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
        "win_rate": float(np.mean(net > 0)),
    }
    if excess is not None:
        ex = np.asarray(excess, float)
        out["mean_excess"] = float(ex.mean())
        out["t_excess"] = eng.t_stat(ex)
    return out


def turnover(legs) -> float:
    """Mean one-way turnover: (opened + fully closed) / names held, engine convention."""
    prev: set[int] = set()
    vals = []
    for leg in legs:
        cur = {int(s) for s in leg}
        if not cur:
            vals.append(0.0)
            prev = set()
            continue
        vals.append((len(cur - prev) + len(prev - cur)) / len(cur))
        prev = cur
    return float(np.mean(vals)) if vals else float("nan")



# ================================================================ vectorized simulator
def simulate_chunk(md_list, leg_chunks, short_net=None, prev=None):
    """Frozen cost convention for many equal-size long legs at once (vectorized).

    leg_chunks[t] : int array (C, k) -- C candidate long-leg portfolios for month t,
                    all of size k (engine convention); k == 0 -> cash month.
    short_net     : optional (n_months,) baseline short-leg net, added to every candidate.
    prev          : optional starting holding (C, k0) to carry costs across passes.
    Returns (net (C, n_months), final holding).
    Cost (identical to engine_fixed.simulate_leg): 0.15% of leg notional on names
    OPENED, 0.15% on names FULLY CLOSED (incl. names whose quote died in the month),
    nothing on names carried over; equal weight 1/k.
    """
    n_months = len(md_list)
    C = int(np.asarray(leg_chunks[0]).shape[0])
    net = np.zeros((C, n_months))
    if prev is None:
        prev = np.zeros((C, 0), dtype=np.int64)
    for t, md in enumerate(md_list):
        idx = np.ascontiguousarray(leg_chunks[t], dtype=np.int64)   # (C, k)
        k = idx.shape[1]
        if k == 0:
            prev = np.zeros((C, 0), dtype=np.int64)
            continue
        alive = np.isfinite(md.ret[idx])
        gross = np.where(alive, md.ret[idx], 0.0).mean(axis=1)      # dead -> cash
        if prev.shape[1] == 0:
            opened = np.full(C, k, dtype=np.int64)
            closed = np.zeros(C, dtype=np.int64)
        else:
            opened = (~np.isin(idx, prev)).sum(axis=1)
            closed = prev.shape[1] - np.isin(prev, idx).sum(axis=1)
        dead = (~alive).sum(axis=1)
        net[:, t] = gross - eng.COST_PER_SIDE * (opened + closed + dead) / k
        if short_net is not None:
            net[:, t] += short_net[t]
        prev = np.where(alive, idx, -1)              # dead names are fully closed
        prev = prev[:, np.all(prev >= 0, axis=0)]    # may shrink below k
    return net, prev


def short_net_series(md_list, short_legs) -> np.ndarray:
    """Baseline short leg net per month, engine-exact (short return, borrow, cost)."""
    sr = eng.simulate_leg(md_list, short_legs, "short")
    return -sr.net - sr.borrow - sr.cost


def baseline_series(md_list, longs, shorts):
    lr = eng.simulate_leg(md_list, longs, "long")
    sr = eng.simulate_leg(md_list, shorts, "short")
    return eng.net_series(lr, sr), lr, sr


# ================================================================ draw engine
class DrawSet:
    """A vectorized random strategy: per month a fixed prefix (e.g. the baseline short
    leg duplicated or the empty list) followed by a random k-subset of `eligible`."""

    def __init__(self, md_list, eligible, k, short_net=None):
        self.md_list = md_list
        self.eligible = [np.asarray(e, dtype=np.int64) for e in eligible]
        self.k = [int(x) for x in k]
        self.short_net = short_net

    def pass_chunks(self, rng, rows):
        chunks = []
        for t in range(len(self.md_list)):
            k = self.k[t]
            elig = self.eligible[t]
            if k <= 0 or len(elig) == 0:
                chunks.append(np.zeros((rows, 0), dtype=np.int64))
                continue
            kk = min(k, len(elig))
            pick = np.argsort(rng.random((rows, len(elig))), axis=1, kind="stable")[:, :kk]
            chunks.append(elig[pick])
        return chunks

    def nets(self, n_draws, seed, want_means=False):
        rng = np.random.default_rng(seed)
        fn = self.md_list.__len__
        assert fn() > 0, "empty month list"
        prev = None
        means, nets = [], []
        for start in range(0, n_draws, CHUNK):
            rows = min(CHUNK, n_draws - start)
            net, prev = simulate_chunk(self.md_list, self.pass_chunks(rng, rows),
                                       self.short_net, prev)
            if want_means:
                means.append(net.mean(axis=1))
            else:
                nets.append(net)
        if want_means:
            return np.concatenate(means)
        return np.concatenate(nets, axis=0)


# ================================================================ V1
def mom_pool(md: eng.MonthData) -> np.ndarray:
    """Top-20% MOM pool, MOM descending, ties by symbol (eligibility = engine's)."""
    elig = np.flatnonzero(np.isfinite(md.mom) & np.isfinite(md.ret))
    if len(elig) < eng.MIN_ELIGIBLE:
        return np.zeros(0, dtype=np.int64)
    vals = md.mom[elig]
    thr = np.percentile(vals, 100.0 * (1.0 - POOL_FRACTION))
    pool = elig[vals >= thr]
    return pool[np.argsort(-md.mom[pool], kind="stable")].astype(np.int64)


def v1_legs(md_list, bought):
    """V1 long leg: insider-bought names of the top k pool ranks first, then the rest
    of the top k by MOM rank. k = min(n_names, pool size), n_names = baseline size."""
    longs, ks, n_bought = [], [], []
    for t, md in enumerate(md_list):
        elig = np.flatnonzero(np.isfinite(md.mom) & np.isfinite(md.ret))
        n_names = int(np.floor(eng.DECILE * len(elig)))
        pool = mom_pool(md)
        if len(elig) < eng.MIN_ELIGIBLE or n_names < 1 or len(pool) == 0:
            longs.append(np.zeros(0, dtype=np.int64))
            ks.append(0)
            n_bought.append(0)
            continue
        k = min(n_names, len(pool))
        top = pool[:k]
        isb = bought[t, top]
        longs.append(np.sort(np.concatenate([top[isb], top[~isb]])))
        ks.append(k)
        n_bought.append(int(isb.sum()))
    return longs, ks, n_bought


class V1RandomDraws:
    """Random same-count swaps: long leg = uniform random k-subset of the SAME top-20%
    MOM pool, short leg = baseline bottom decile (fixed), engine costs."""

    def __init__(self, md_list, pools, k, short_net):
        self.md_list = md_list
        self.pools = [np.asarray(p, dtype=np.int64) for p in pools]
        self.k = [int(x) for x in k]
        self.short_net = short_net

    def nets(self, n_draws, seed, want_means=False):
        rng = np.random.default_rng(seed)
        prev = None
        means, nets = [], []
        for start in range(0, n_draws, CHUNK):
            rows = min(CHUNK, n_draws - start)
            chunks = []
            for t in range(len(self.md_list)):
                k = self.k[t]
                pool = self.pools[t]
                if k <= 0 or len(pool) == 0:
                    chunks.append(np.zeros((rows, 0), dtype=np.int64))
                    continue
                kk = min(k, len(pool))
                pick = np.argsort(rng.random((rows, len(pool))), axis=1,
                                  kind="stable")[:, :kk]
                chunks.append(pool[pick])
            net, prev = simulate_chunk(self.md_list, chunks, self.short_net, prev)
            if want_means:
                means.append(net.mean(axis=1))
            else:
                nets.append(net)
        return np.concatenate(means) if want_means else np.concatenate(nets, axis=0)


# ================================================================ V2
def base_eligible(panel: eng.Panel, m: eng.MonthRow, md: eng.MonthData) -> np.ndarray:
    """Frozen tradability screen (engine's base_eligible, prereg section 5)."""
    i, e = m.decision_idx, m.entry_idx
    dec_close = panel.close_m[i]
    with warnings.catch_warnings():          # empty slice at the panel start
        warnings.simplefilter("ignore", RuntimeWarning)
        adv = np.nanmean(panel.volume_m[i - eng.ADV_WINDOW + 1: i + 1], axis=0)
    adv_count = np.isfinite(panel.volume_m[i - eng.ADV_WINDOW + 1: i + 1]).sum(axis=0)
    entry_open = panel.open_m[e]
    with np.errstate(invalid="ignore", divide="ignore"):
        return (np.isfinite(dec_close) & (dec_close >= eng.MIN_PRICE)
                & (adv_count >= eng.ADV_WINDOW) & np.isfinite(adv)
                & (adv >= eng.MIN_ADV_SHARES)
                & np.isfinite(entry_open) & (entry_open > 0))


def universe_return(md: eng.MonthData, elig: np.ndarray) -> float:
    """Equal-weight, costless return of the eligible universe (engine fallback alive)."""
    sel = elig & np.isfinite(md.ret)
    return float(np.mean(md.ret[sel])) if sel.any() else 0.0


# ================================================================ synthetic self-test
def synth_panel() -> eng.Panel:
    n = len(FIX_DATES)
    base = np.linspace(10.0, 20.0, n)[:, None]
    open_m = np.tile(base, (1, len(FIX_SYMBOLS)))
    return eng.Panel(FIX_DATES.values.astype("datetime64[ns]"), FIX_SYMBOLS.copy(),
                     open_m, open_m.copy(), np.full_like(open_m, 2_000_000.0))


def self_test_window_cutoff() -> dict:
    """A purchase filed ON or AFTER the rebalance date is NOT used; d-1 IS used."""
    d = pd.Timestamp("2021-06-30")
    filings = pd.DataFrame({
        "symbol": ["AAA", "BBB", "AAA"],
        # AAA filed ON d and AFTER d: both must be ignored; BBB at d-1 must be used.
        "filing_date": [d, d - pd.Timedelta(days=1), d + pd.Timedelta(days=1)],
        "insider_cik": [1, 2, 3],
    })
    bought, cluster = insider_signal(filings, FIX_SYMBOLS, [d])
    aaa, bbb = 0, 1
    checks = {
        "on_date_filing_ignored": not bool(bought[0, aaa]),
        "after_date_filing_ignored": not bool(cluster[0, aaa]),
        "prior_day_filing_used": bool(bought[0, bbb]),
    }
    # window edges: exactly d-90 included, d-91 excluded
    filings2 = pd.DataFrame({
        "symbol": ["AAA", "AAA"],
        "filing_date": [d - pd.Timedelta(days=90), d - pd.Timedelta(days=91)],
        "insider_cik": [1, 1],
    })
    bought2, _ = insider_signal(filings2.iloc[:1], FIX_SYMBOLS, [d])
    filings3 = filings2.iloc[1:]
    bought3, _ = insider_signal(filings3, FIX_SYMBOLS, [d])
    checks["window_d_minus_90_used"] = bool(bought2[0, aaa])
    checks["window_d_minus_91_ignored"] = not bool(bought3[0, aaa])
    del filings2
    # cluster needs 2 distinct insiders
    f3 = pd.DataFrame({"symbol": ["BBB", "BBB"], "filing_date": [d - pd.Timedelta(days=2),
                                                                d - pd.Timedelta(days=1)],
                       "insider_cik": [7, 8]})
    _, cl3 = insider_signal(f3, FIX_SYMBOLS, [d])
    checks["two_insiders_cluster"] = bool(cl3[0, bbb])
    f4 = pd.DataFrame({"symbol": ["BBB", "BBB"], "filing_date": [d - pd.Timedelta(days=2),
                                                                d - pd.Timedelta(days=1)],
                       "insider_cik": [7, 7]})
    _, cl4 = insider_signal(f4, FIX_SYMBOLS, [d])
    checks["same_insider_not_cluster"] = not bool(cl4[0, bbb])
    # simulator vs the frozen engine, on one synthetic month: identical net for a
    # swap (open + close charged) and for a carried book (nothing charged)
    p2 = synth_panel()
    mrow = eng.build_months(p2)[:1]
    md = eng.compute_month_data(p2, mrow)
    ref = eng.simulate_leg(md, [np.array([0, 1])], "long")
    got = simulate_chunk(md, [np.array([[0, 1]])])[0]
    checks["sim_matches_engine_fresh"] = abs(float(got[0, 0]) - float(ref.net[0] - ref.cost[0])) < 1e-12
    got_c = simulate_chunk(md, [np.array([[0, 1]])], prev=np.array([[0, 1]]))[0]
    # carried book: engine convention charges nothing -> net == gross
    checks["carried_name_no_extra_cost"] = (
        abs(float(got_c[0, 0]) - float(np.mean(md[0].ret[[0, 1]]))) < 1e-12)
    passed = all(checks.values()) and checks["window_d_minus_90_used"]
    print("self_test window/cost: " + ("PASS" if passed else "FAIL")
          + " " + json.dumps(checks))
    if not passed:
        raise AssertionError(f"self-test failed: {checks}")
    return dict(checks, pass_=True)


# ================================================================ reporting
def bars_verdict(mean_net, mean_excess, t_exc, pct, n_months_held,
                 min_months=BAR_MIN_MONTHS, bar_t=BAR_T, bar_p=BONFERRONI_P) -> dict:
    tests = {
        "months_ge_min": bool(n_months_held >= min_months),
        "mean_net_gt_0": bool(mean_net > 0),
        "mean_excess_gt_0": bool(mean_excess > 0),
        "t_ge_bar": bool(np.isfinite(t_exc) and t_exc >= bar_t),
        "percentile_ge_bar": bool(pct >= bar_p),
    }
    tests["PASS"] = all(v for k, v in tests.items() if k != "PASS")
    return tests


def to_md(res: dict) -> str:
    m = res["meta"]
    lines = [f"# INSIDER-001 results ({m.get('mode', 'full')})", "",
             f"- generated: {m.get('generated_utc')}  runtime: {m.get('runtime_sec', 0):.1f}s",
             f"- months: {m.get('n_months')}  random draws: {m.get('n_draws')}",
             f"- panel: {m.get('panel')}  (rows <= 2024-12-31 only)",
             f"- insider file: {m.get('insider')} rows={m.get('insider_rows')} "
             f"filings {m.get('filing_min')} .. {m.get('filing_max')}",
             "", "## Baseline", ""]
    for k, v in res["baseline"].items():
        lines.append(f"- {k}: {v}")
    for name, var in res["variants"].items():
        lines += ["", f"## {name}", ""]
        for sec, vals in var.items():
            if sec == "bars":
                continue
            if isinstance(vals, dict):
                lines.append(f"- {sec}: " + ", ".join(f"{k}={v}" for k, v in vals.items()))
            else:
                lines.append(f"- {sec}: {vals}")
        lines.append("- bars: " + ", ".join(f"{k}={v}" for k, v in var["bars"].items()))
    return "\n".join(lines) + "\n"


# ================================================================ main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="INSIDER-001 insider buying on MOM 12-1 (DEV)")
    ap.add_argument("--n-random", type=int, default=None,
                    help=f"random draws per variant (default {N_RANDOM_DEFAULT})")
    ap.add_argument("--smoke", action="store_true",
                    help=f"smoke: first {SMOKE_MONTHS} DEV months, {SMOKE_DRAWS} draws")
    ap.add_argument("--panel", default=str(DEFAULT_PANEL))
    ap.add_argument("--insider", default=str(DEFAULT_INSIDER))
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)

    if args.self_test:
        self_test_window_cutoff()
        return 0

    t0 = time.time()
    n_draws = args.n_random if args.n_random is not None else (
        SMOKE_DRAWS if args.smoke else N_RANDOM_DEFAULT)

    panel = panel_from_frame(read_dev_frame(Path(args.panel)))
    months = dev_months(panel)
    if args.smoke:
        months = months[:SMOKE_MONTHS]
    with warnings.catch_warnings():          # empty ADV/vol windows at the panel start
        warnings.simplefilter("ignore", RuntimeWarning)
        md_list = eng.compute_month_data(panel, months)
    assert len(md_list) == len(months)

    filings = load_insider(Path(args.insider))
    bought, cluster = insider_signal(filings, panel.symbols,
                                     [m.decision_date for m in months])

    # ---- baseline -------------------------------------------------------
    base_longs, base_shorts, _ = zip(*(eng.select_legs(md, "MOM") for md in md_list))
    base_net, _, _ = baseline_series(md_list, list(base_longs), list(base_shorts))
    short_net = short_net_series(md_list, list(base_shorts))
    base_summ = summary(base_net)
    base_mean, base_t = float(base_net.mean()), eng.t_stat(base_net)
    baseline_check = {
        "mean_net": base_mean, "t": base_t,
        "mean_expected": REF_MEAN_NET, "t_expected": REF_T_EXCESS,
        "mean_ok": bool(abs(base_mean - REF_MEAN_NET) <= REF_MEAN_TOL),
        "t_ok": bool(abs(base_t - REF_T_EXCESS) <= REF_T_TOL),
        "max_drawdown": base_summ["max_drawdown"], "max_drawdown_ref": REF_MAXDD,
    }
    print(f"baseline: mean {base_mean*100:.4f}%/mo  t_exc {base_t:.4f}  "
          f"maxDD {base_summ['max_drawdown']*100:.2f}%  months {len(md_list)}", flush=True)

    # ---- V1 -------------------------------------------------------------
    v1_long, v1_k, v1_bought_n = v1_legs(md_list, bought)
    v1_net, _ = simulate_chunk(md_list, [[leg] for leg in v1_long], short_net)
    v1_net = v1_net[0]
    pools = [mom_pool(md) for md in md_list]
    v1_draws = V1RandomDraws(md_list, pools, v1_k, short_net)
    v1_draw_means = v1_draws.nets(n_draws, SEED_V1, want_means=True)
    v1_summ = summary(v1_net, v1_net - base_net)
    v1_pct = eng.percentile_vs_random(float(v1_net.mean()), v1_draw_means)
    v1_diff = v1_net - base_net
    v1_t = eng.t_stat(v1_diff)
    v1_months = int(sum(1 for leg in v1_long if len(leg)))

    # ---- V2 -------------------------------------------------------------
    elig_univ = [base_eligible(panel, m, md) for m, md in zip(months, md_list)]
    univ_ret = np.array([universe_return(md, e) for md, e in zip(md_list, elig_univ)])
    v2_long = [np.flatnonzero(cluster[t] & elig_univ[t] & np.isfinite(md.ret))
               for t, md in enumerate(md_list)]
    v2_net, _ = simulate_chunk(md_list, [[leg] for leg in v2_long])
    v2_net = v2_net[0]
    v2_excess = v2_net - univ_ret
    v2_draws = DrawSet(md_list, elig_univ, [len(l) for l in v2_long])
    v2_draw_means = v2_draws.nets(n_draws, SEED_V2, want_means=True)
    v2_pct = eng.percentile_vs_random(float(v2_net.mean()), v2_draw_means)
    v2_summ = summary(v2_net, v2_excess)
    v2_months = int(sum(1 for leg in v2_long if len(leg)))

    # correlation with baseline MOM (prereg V2 reporting)
    corr = float(np.corrcoef(v2_net, base_net)[0, 1]) if len(v2_net) > 2 else float("nan")

    # ---- baseline bar vs random same-size lists (same 1000-draw engine bar) ----
    base_draw_means = DrawSet(md_list, [np.flatnonzero(np.isfinite(md.mom) & np.isfinite(md.ret))
                                        for md in md_list],
                              [len(l) for l in base_longs]).nets(
        n_draws, SEED_BASELINE, want_means=True)
    base_pct = eng.percentile_vs_random(float(base_net.mean()), base_draw_means)

    res = {
        "meta": {
            "id": "INSIDER-001", "mode": "smoke" if args.smoke else "full",
            "generated_utc": pd.Timestamp.now(tz="UTC").tz_localize(None).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "runtime_sec": time.time() - t0, "n_months": len(md_list),
            "n_draws": n_draws, "seeds": {"baseline": SEED_BASELINE, "v1": SEED_V1,
                                          "v2": SEED_V2},
            "panel": str(args.panel), "insider": str(args.insider),
            "insider_rows": int(len(filings)),
            "filing_min": str(filings["filing_date"].min().date()),
            "filing_max": str(filings["filing_date"].max().date()),
            "dev_months": [m.key for m in months],
        },
        "baseline": {**{k: v for k, v in base_summ.items()}, "check": baseline_check,
                     "percentile_vs_random": base_pct},
        "variants": {
            "V1_mom_tilt": {
                "summary": v1_summ,
                "vs_baseline_mean": float(v1_diff.mean()), "vs_baseline_t": v1_t,
                "percentile_vs_random": v1_pct,
                "months_with_positions": v1_months,
                "names_per_month": float(np.mean(v1_k)),
                "insider_names_per_month": float(np.mean(v1_bought_n)),
                "turnover": turnover(v1_long),
                "bars": bars_verdict(v1_summ["mean_net"], v1_diff.mean(), v1_t, v1_pct,
                                     v1_months),
            },
            "V2_cluster": {
                "summary": v2_summ,
                "excess_vs_universe": v2_excess.mean(),
                "percentile_vs_random": v2_pct,
                "months_with_positions": v2_months,
                "cash_months": int(len(md_list) - v2_months),
                "names_per_month": float(np.mean([len(l) for l in v2_long])),
                "turnover": turnover(v2_long),
                "correlation_with_baseline_mom": corr,
                "bars": bars_verdict(v2_summ["mean_net"], v2_summ["mean_excess"],
                                     v2_summ["t_excess"], v2_pct, v2_months),
            },
        },
    }
    res["meta"]["runtime_sec"] = time.time() - t0
    out_dir = Path(args.out_dir) if args.out_dir else HERE
    out_dir.mkdir(parents=True, exist_ok=True)
    res = _jsonify(res)
    (out_dir / "results.json").write_text(json.dumps(res, indent=2))
    (out_dir / "results.md").write_text(to_md(res))
    print(f"V1 bars: {res['variants']['V1_mom_tilt']['bars']}")
    print(f"V2 bars: {res['variants']['V2_cluster']['bars']}")
    print(f"wrote results.json/results.md in {res['meta']['runtime_sec']:.1f}s")
    return 0


def _jsonify(o):
    if isinstance(o, dict):
        return {str(k): _jsonify(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonify(v) for v in o]
    if isinstance(o, np.ndarray):
        return _jsonify(o.tolist())
    if isinstance(o, (np.floating, float)):
        v = float(o)
        return v if np.isfinite(v) else None
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.bool_, bool)):
        return bool(o)
    if isinstance(o, pd.Timestamp):
        return o.strftime("%Y-%m-%d")
    return o


if __name__ == "__main__":
    raise SystemExit(main())
