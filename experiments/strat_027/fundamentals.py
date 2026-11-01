"""STRAT-027 — point-in-time fundamental factors (QUALITY, VALUE) from the fact-level panel.

The panel (built by build_fundamentals.py) is FACT LEVEL: every filing version of every
XBRL fact is kept, together with the public release date (`filed`).

POINT-IN-TIME RULE
  At a rebalance date d we may only use facts whose `filed` date is <= d. For each symbol:
    * fiscal_year_end  = the LATEST fiscal year end whose annual NetIncomeLoss was
                         already public on or before d
    * net_income       = the newest publicly-known version of that annual NetIncomeLoss
    * stockholders_equity = the newest publicly-known version of StockholdersEquity at
                         that same fiscal year end (missing -> factor unavailable)
    * shares_outstanding   = the newest publicly-known version of
                         CommonStockSharesOutstanding

  QUALITY = net_income / stockholders_equity          (return on equity; higher is better)
  VALUE   = net_income / (price * shares_outstanding)  (earnings yield; higher is better)

  The source dataset contains ANNUAL filings only, so both factors are step functions
  that change once a year per company. That is a property of the data and is disclosed.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
FUND_PATH = DATA / "fundamentals_pit.parquet"

NI_TAG = "NetIncomeLoss"
EQ_TAG = "StockholdersEquity"
SH_TAG = "CommonStockSharesOutstanding"


def load_fundamentals(path: Path | None = None):
    path = path or FUND_PATH
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    df["filed"] = pd.to_datetime(df["filed"]).dt.normalize()
    df["ddate"] = pd.to_datetime(df["ddate"]).dt.normalize()
    return df


def _group_versions(sub: pd.DataFrame):
    """Group a fact table by fiscal period end.

    Returns (dperiods, first_filed, filed_lists, value_lists) where each entry of
    filed_lists/value_lists is the version history of one fiscal period, sorted by the
    public release date.
    """
    groups = []
    for ddate, g in sub.groupby("ddate"):
        g = g.sort_values("filed")
        groups.append((pd.Timestamp(ddate), g["filed"].astype("int64").to_numpy(),
                        g["value"].to_numpy(dtype=float)))
    groups.sort(key=lambda t: t[0])
    if not groups:
        return [], np.array([]), [], []
    dperiods = np.array([g[0] for g in groups], dtype="datetime64[ns]")
    first_filed = np.array([g[1][0] for g in groups], dtype="int64")
    filed_lists = [g[1] for g in groups]
    value_lists = [g[2] for g in groups]
    return dperiods, first_filed, filed_lists, value_lists


def fundamental_factors(fund, symbols: np.ndarray, rebal_dates: np.ndarray,
                        last_close: np.ndarray, cache: Path | None = None):
    """Return (QUALITY, VALUE) arrays of shape (R, S), NaN where unavailable.

    `cache` (optional) is a .npz path: the resolved arrays are cached there so the
    study and the sensitivity grid do not each pay the as-of resolution cost.
    """
    if cache is not None and cache.exists():
        try:
            z = np.load(cache, allow_pickle=False)
            if (z["symbols"].shape == symbols.shape and z["dates"].shape == rebal_dates.shape
                    and np.array_equal(z["symbols"].astype(str), symbols.astype(str))
                    and np.array_equal(z["dates"], rebal_dates)):
                return z["quality"], z["value"]
        except Exception as exc:  # stale or unreadable cache -> recompute
            print(f"fundamental factor cache unusable ({exc}); recomputing")
    R = len(rebal_dates)
    S = len(symbols)
    quality = np.full((R, S), np.nan, dtype=np.float32)
    value = np.full((R, S), np.nan, dtype=np.float32)
    if fund is None:
        return quality, value

    sym_pos = {s: k for k, s in enumerate(symbols)}
    dates_ord = pd.DatetimeIndex(rebal_dates).astype("int64").to_numpy()
    px = np.asarray(last_close, dtype=np.float64)     # (R, S) close strictly before each rebalance
    reb_dates = pd.DatetimeIndex(rebal_dates)

    for sym, grp in fund.groupby("symbol", sort=False):
        k = sym_pos.get(sym)
        if k is None:
            continue
        ni_sub = grp[grp["tag"] == NI_TAG]
        if ni_sub.empty:
            continue
        ni_d, ni_first, ni_filed, ni_val = _group_versions(ni_sub)
        eq_d, eq_first, eq_filed, eq_val = _group_versions(grp[grp["tag"] == EQ_TAG])
        eq_index = {pd.Timestamp(d): i for i, d in enumerate(eq_d)}
        # shares: sort by (filed, ddate) so that, among versions filed on the same day,
        # the one with the LATEST fact date wins (most recent share count known at d)
        sh_sub = grp[grp["tag"] == SH_TAG].sort_values(["filed", "ddate"])
        sh_filed = sh_sub["filed"].astype("int64").to_numpy()
        sh_val = sh_sub["value"].to_numpy(dtype=float)

        for r in range(R):
            d = dates_ord[r]
            # fiscal year in force: the newest annual period already public at d
            avail = np.flatnonzero(ni_first <= d)
            if avail.size == 0:
                continue
            gi = avail[-1]                       # ni_d is sorted by fiscal period
            j = np.searchsorted(ni_filed[gi], d, side="right") - 1
            if j < 0:
                continue
            ni_v = ni_val[gi][j]
            if not np.isfinite(ni_v):
                continue
            fye = pd.Timestamp(ni_d[gi])
            # equity at the same fiscal year end, as known at d
            ei = eq_index.get(fye)
            if ei is not None and eq_first[ei] <= d:
                je = np.searchsorted(eq_filed[ei], d, side="right") - 1
                if je >= 0:
                    eq_v = eq_val[ei][je]
                    if np.isfinite(eq_v) and eq_v > 0:
                        quality[r, k] = ni_v / eq_v
            # shares: newest public disclosure
            if sh_filed.size:
                js = np.searchsorted(sh_filed, d, side="right") - 1
                if js >= 0:
                    sh_v = sh_val[js]
                    p = px[r, k]
                    if np.isfinite(sh_v) and sh_v > 0 and np.isfinite(p) and p > 0:
                        value[r, k] = ni_v / (p * sh_v)
    if cache is not None:
        np.savez_compressed(cache, quality=quality, value=value,
                            symbols=np.asarray(symbols, dtype="U16"),
                            dates=np.asarray(rebal_dates, dtype="datetime64[ns]"))
    return quality, value
