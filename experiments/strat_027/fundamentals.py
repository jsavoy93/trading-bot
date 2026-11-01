"""STRAT-027 — point-in-time fundamental factors (QUALITY, VALUE).

The panel is produced by the data audit / build step and contains one row per
10-K filing fact set:

    symbol, cik, fiscal_period_end, form, filed, net_income, stockholders_equity,
    assets, revenue_contract, revenues_total, shares_outstanding, cash, ...

POINT-IN-TIME RULE: at a rebalance date d we use, for each symbol, the most
recent filing whose `filed` date is <= d. Nothing filed after d is ever used.

QUALITY = ROE   = net_income (FY) / stockholders_equity (FY end)      [higher is better]
VALUE   = earnings yield = net_income (FY) / market_cap(d)
          market_cap(d) = adjusted close at d-1 * shares_outstanding (from the latest filing)
                                                                                        [higher is better]
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
FUND_PATH = DATA / "fundamentals_pit.parquet"


def load_fundamentals(path: Path | None = None):
    path = path or FUND_PATH
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    df["filed"] = pd.to_datetime(df["filed"]).dt.normalize()
    df = df.sort_values(["symbol", "filed"]).reset_index(drop=True)
    return df


def fundamental_factors(fund, symbols: np.ndarray, rebal_dates: np.ndarray,
                        last_close: np.ndarray):
    """Return (QUALITY, VALUE) arrays of shape (R, S), NaN where unavailable."""
    R = len(rebal_dates)
    S = len(symbols)
    quality = np.full((R, S), np.nan, dtype=np.float32)
    value = np.full((R, S), np.nan, dtype=np.float32)
    if fund is None:
        return quality, value

    sym_pos = {s: k for k, s in enumerate(symbols)}
    dates_ord = pd.DatetimeIndex(rebal_dates).astype("int64").to_numpy()
    px = last_close.astype(np.float64)          # (R, S) close strictly before each rebalance

    for sym, grp in fund.groupby("symbol", sort=False):
        k = sym_pos.get(sym)
        if k is None:
            continue
        g = grp.sort_values("filed")
        filed_ord = g["filed"].astype("int64").to_numpy()
        ni = g["net_income"].to_numpy(dtype=float)
        eq = g["stockholders_equity"].to_numpy(dtype=float)
        sh = g["shares_outstanding"].to_numpy(dtype=float)
        for r in range(R):
            idx = np.searchsorted(filed_ord, dates_ord[r], side="right") - 1
            if idx < 0:
                continue
            n_i, e_i, s_i = ni[idx], eq[idx], sh[idx]
            if np.isfinite(n_i) and np.isfinite(e_i) and e_i > 0 and n_i is not None:
                quality[r, k] = n_i / e_i
            p = px[r, k]
            if np.isfinite(n_i) and np.isfinite(s_i) and s_i > 0 and np.isfinite(p) and p > 0:
                value[r, k] = n_i / (p * s_i)
    return quality, value
