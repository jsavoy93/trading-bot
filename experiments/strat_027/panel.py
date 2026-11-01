"""STRAT-027 — panel loading, monthly rebalance calendar, and factor computation.

Design rules (no look-ahead):
  * The rebalance happens at the OPEN of the first trading day of each month.
  * EVERY factor input and every eligibility input uses bars STRICTLY BEFORE the
    rebalance date. Nothing dated on or after the rebalance date is read.
  * A symbol is only investable at a rebalance if it has a bar on the rebalance
    date itself (it must be tradable at that open).

Price convention: Alpaca adjustment="all" (split + dividend adjusted) OHLC.
Returns computed from these series are total returns.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"

MIN_ADV_SHARES = 1_000_000.0   # trailing 20-day mean dollar-free liquidity floor (shares/day)
MIN_PRICE = 5.0                # last close floor
MIN_HISTORY_BARS = 253         # ~1 trading year of history required (vol + momentum warm-up)
VOL_WINDOW = 252               # trading days for realized volatility
ADV_WINDOW = 20                # trading days for average daily volume


@dataclass
class Panel:
    dates: np.ndarray            # datetime64[D] sorted trading days
    symbols: np.ndarray          # symbol codes aligned to matrix columns
    open_m: np.ndarray           # (T, S) float32 adjusted open
    close_m: np.ndarray          # (T, S) float32 adjusted close
    volume_m: np.ndarray         # (T, S) float32 volume
    rebal_idx: np.ndarray        # row index of each monthly rebalance open
    rebal_dates: np.ndarray      # dates of each monthly rebalance

    @property
    def n_dates(self) -> int:
        return len(self.dates)


def load_panel(path: Path | None = None) -> Panel:
    path = path or (DATA / "panel.parquet")
    df = pd.read_parquet(path, columns=["symbol", "date", "open", "close", "volume"])
    df["date"] = pd.to_datetime(df["date"])
    dates = np.sort(df["date"].unique())
    symbols = np.sort(df["symbol"].unique())
    d_index = pd.Index(dates)
    s_index = pd.Index(symbols)
    di = d_index.get_indexer(df["date"])
    si = s_index.get_indexer(df["symbol"])
    n_t, n_s = len(dates), len(symbols)

    def build(col: str) -> np.ndarray:
        m = np.full((n_t, n_s), np.nan, dtype=np.float32)
        m[di, si] = df[col].to_numpy(dtype=np.float32)
        return m

    open_m = build("open")
    close_m = build("close")
    volume_m = build("volume")

    # Trading calendar + rebalance dates: first trading day of each month.
    cal = pd.DatetimeIndex(dates)
    is_first = np.zeros(len(cal), dtype=bool)
    is_first[0] = True
    is_first[1:] = cal[1:].to_period("M").values != cal[:-1].to_period("M").values
    rebal_idx = np.flatnonzero(is_first)
    rebal_dates = cal[rebal_idx]
    return Panel(dates, symbols, open_m, close_m, volume_m, rebal_idx, rebal_dates)


def _last_index_on_or_before(dates: np.ndarray, target: np.datetime64) -> int:
    """Index of the last trading day on or before `target` (-1 if none)."""
    i = np.searchsorted(dates, target, side="right") - 1
    return int(i)


@dataclass
class FactorFrame:
    """Factor values + eligibility for every symbol at every rebalance date."""
    dates: np.ndarray            # (R,) rebalance dates
    symbols: np.ndarray          # (S,)
    eligible: np.ndarray         # (R, S) bool
    values: dict[str, np.ndarray]   # factor name -> (R, S) float32 (nan where not eligible)


def compute_factors(panel: Panel, factor_specs: dict) -> FactorFrame:
    """Compute eligibility and the requested factors at each monthly rebalance.

    factor_specs maps name -> callable(ctx) -> (R, S) array, where ctx provides
    the look-back windows. Implemented inline below for the fixed factor set.
    """
    dates = panel.dates
    R = len(panel.rebal_idx)
    S = len(panel.symbols)

    # log returns matrix (row t = return from t-1 to t)
    with np.errstate(divide="ignore", invalid="ignore"):
        logret = np.diff(np.log(panel.close_m.astype(np.float64)), axis=0, prepend=np.nan)

    eligible = np.zeros((R, S), dtype=bool)
    values: dict[str, np.ndarray] = {}

    mom = np.full((R, S), np.nan, dtype=np.float32)
    lvol = np.full((R, S), np.nan, dtype=np.float32)

    # cumulative count of finite closes (for the history-length screen)
    close_finite_count = np.isfinite(panel.close_m).cumsum(axis=0)

    for r, i in enumerate(panel.rebal_idx):
        d = panel.dates[i]
        if i < MIN_HISTORY_BARS:
            continue

        # --- eligibility (all inputs strictly before the rebalance date) ---
        last_close = panel.close_m[i - 1]
        adv = panel.volume_m[i - ADV_WINDOW:i].mean(axis=0)
        hist = close_finite_count[i - 1]
        tradable = np.isfinite(panel.open_m[i])
        ok = (
            np.isfinite(last_close)
            & (last_close >= MIN_PRICE)
            & np.isfinite(adv)
            & (adv >= MIN_ADV_SHARES)
            & (hist >= MIN_HISTORY_BARS)
            & tradable
        )
        eligible[r] = ok

        # --- 12-1 month momentum: P(t-1m)/P(t-12m) - 1 ---
        ts = pd.Timestamp(d)
        t1 = np.datetime64(ts - pd.DateOffset(months=1))
        t12 = np.datetime64(ts - pd.DateOffset(months=12))
        j1 = _last_index_on_or_before(dates, t1)
        j12 = _last_index_on_or_before(dates, t12)
        if j1 >= 0 and j12 >= 0 and j12 < j1 < i:
            p1 = panel.close_m[j1].astype(np.float64)
            p12 = panel.close_m[j12].astype(np.float64)
            with np.errstate(divide="ignore", invalid="ignore"):
                mom[r] = np.where((p1 > 0) & (p12 > 0), p1 / p12 - 1.0, np.nan)

        # --- low volatility: trailing 252-day realized vol of daily log returns ---
        w = logret[i - VOL_WINDOW:i]
        with np.errstate(invalid="ignore"):
            lvol[r] = np.nanstd(w, axis=0) * np.sqrt(252.0)

    values["MOM12_1"] = mom
    values["LVOL"] = lvol

    for name, spec in factor_specs.items():
        if name in values:
            continue
        raise NotImplementedError(f"factor {name} is not implemented in compute_factors")

    return FactorFrame(panel.rebal_dates, panel.symbols, eligible, values)
