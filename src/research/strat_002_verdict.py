"""STRAT-002 between-group comparison and frozen verdict logic.

Frozen pre-registration (PR #98, commit e62a31e, merged as c8da017):

    PRIMARY BETWEEN-GROUP EFFECT:
        delta_median     = median(returns_high_rsi)
                          - median(returns_comparison)
                          on next_session_open horizon
        delta_p_positive = p_positive(high_rsi)
                          - p_positive(comparison)

    SUPPORTING METRICS (reported, not decisive):
        absolute high-RSI next_session_open median AND p_positive
        absolute comparison median AND p_positive
        per-date per-cohort n/median/p_positive (diagnostic only)

    PRACTICAL-REPLICATION THRESHOLDS (frozen before execution):
        delta_median     > +0.10 percentage points
        delta_p_positive > +5  percentage points

    VERDICT CRITERIA (mutually exclusive; precedence top-down):
        UNDERPOWERED                — min_sample not met
        REPLICATED                  — min_sample met AND both deltas pass
        DIRECTIONALLY CONSISTENT BUT WEAK
                                   — min_sample met AND NOT REPLICATED
                                     AND (delta_median > 0 OR
                                          delta_p_positive > 0)
        FAILED TO REPLICATE         — min_sample met AND both deltas <= 0

    MINIMUM SAMPLE:
        n_high_rsi_OK               >= 200
        n_high_rsi_with_RSI         >= 5000
        n_comparison_OK             >= 5000

This module is the single source of truth for STRAT-002 verdict
classification. It does NOT load data, fetch bars, or touch the
production DB. It only consumes prepared per-symbol-day return
vectors.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Iterable, Optional

import math

import pandas as pd


# Frozen thresholds (PR #98 / commit e62a31e)
# Returns are expressed as decimals (e.g., 0.005 = +0.5%).
# +0.10 percentage points = 0.0010 in return-decimal units.
# +5 percentage points = 0.05 in p_positive fraction units (p_positive
# is already a fraction 0..1).
PRACTICAL_REPLICATION_DELTA_MEDIAN_PP = 0.0010  # +0.10 percentage points in return-decimal units
PRACTICAL_REPLICATION_DELTA_PPOS_PP = 0.05     # +5 percentage points in p_positive fraction units

MIN_SAMPLE_HIGH_RSI_OK = 200
MIN_SAMPLE_HIGH_RSI_WITH_RSI = 5000
MIN_SAMPLE_COMPARISON_OK = 5000


@dataclass
class GroupStats:
    """Summary statistics for one cohort on one horizon."""
    cohort: str
    horizon: str
    n_total: int
    n_with_RSI: int
    n_OK: int
    median_return: Optional[float]
    mean_return: Optional[float]
    p_positive: Optional[float]
    n_unique_symbols: int
    missingness_no_persisted_rsi: int = 0
    missingness_no_regular_session: int = 0
    missingness_no_reference_bar: int = 0
    missingness_no_target_bar: int = 0
    missingness_stale_reference: int = 0
    missingness_other: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class BetweenGroupDelta:
    """Between-group delta metrics on one horizon."""
    horizon: str
    median_high: Optional[float]
    median_comparison: Optional[float]
    delta_median: Optional[float]
    p_positive_high: Optional[float]
    p_positive_comparison: Optional[float]
    delta_p_positive: Optional[float]
    n_high_OK: int
    n_comparison_OK: int
    n_high_with_RSI: int
    n_comparison_with_RSI: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Verdict:
    """Frozen STRAT-002 verdict result."""
    verdict: str  # REPLICATED | DIRECTIONALLY CONSISTENT BUT WEAK | FAILED TO REPLICATE | UNDERPOWERED
    horizon: str
    primary: BetweenGroupDelta
    high_stats: GroupStats
    comparison_stats: GroupStats
    delta_median_threshold_pp: float = PRACTICAL_REPLICATION_DELTA_MEDIAN_PP
    delta_p_positive_threshold_pp: float = PRACTICAL_REPLICATION_DELTA_PPOS_PP
    min_sample_high_rsi_OK: int = MIN_SAMPLE_HIGH_RSI_OK
    min_sample_high_rsi_with_RSI: int = MIN_SAMPLE_HIGH_RSI_WITH_RSI
    min_sample_comparison_OK: int = MIN_SAMPLE_COMPARISON_OK
    coverage_flag: str = ""  # "" or "INCONCLUSIVE_DUE_TO_COVERAGE"

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


def _cohort_returns(
    df: pd.DataFrame,
    cohort_col: str,
    cohort_value: str,
    horizon_col: str,
    status_col: str,
) -> pd.Series:
    """Return the OK return vector for one cohort on one horizon."""
    if df.empty:
        return pd.Series(dtype=float)
    mask = (df[cohort_col] == cohort_value) & (df[status_col] == "OK")
    return df.loc[mask, horizon_col].astype(float).dropna()


def _group_stats(
    df: pd.DataFrame,
    cohort_value: str,
    cohort_col: str,
    horizon_col: str,
    status_col: str,
    horizon_name: str,
    return_status_col: Optional[str] = None,
) -> GroupStats:
    """Compute GroupStats for one cohort on one horizon."""
    cohort_df = df[df[cohort_col] == cohort_value] if cohort_col in df.columns else df.iloc[0:0]
    rets = _cohort_returns(df, cohort_col, cohort_value, horizon_col, status_col)
    n_OK = int(len(rets))
    n_total = int(len(cohort_df))
    median_return = float(rets.median()) if n_OK else None
    mean_return = float(rets.mean()) if n_OK else None
    p_positive = float((rets > 0).mean()) if n_OK else None
    if "symbol" in cohort_df.columns:
        n_unique_symbols = int(cohort_df["symbol"].nunique())
    else:
        n_unique_symbols = 0
    # Per-reason missingness — count rows in the cohort with non-OK status
    miss = {k: 0 for k in [
        "missingness_no_persisted_rsi",
        "missingness_no_regular_session",
        "missingness_no_reference_bar",
        "missingness_no_target_bar",
        "missingness_stale_reference",
        "missingness_other",
    ]}
    if return_status_col and return_status_col in cohort_df.columns and n_total:
        non_ok = cohort_df[cohort_df[return_status_col] != "OK"]
        for _, row in non_ok.iterrows():
            status = str(row.get(return_status_col, ""))
            if status == "MISSING_REFERENCE_BAR":
                miss["missingness_no_reference_bar"] += 1
            elif status == "HORIZON_BEYOND_AVAILABLE_BARS":
                miss["missingness_no_target_bar"] += 1
            elif status == "NO_NEXT_SESSION_BAR":
                miss["missingness_no_target_bar"] += 1
            elif status == "STALE_REFERENCE_BAR":
                miss["missingness_stale_reference"] += 1
            else:
                miss["missingness_other"] += 1
    return GroupStats(
        cohort=cohort_value,
        horizon=horizon_name,
        n_total=n_total,
        n_with_RSI=n_total,
        n_OK=n_OK,
        median_return=median_return,
        mean_return=mean_return,
        p_positive=p_positive,
        n_unique_symbols=n_unique_symbols,
        **miss,
    )


def compute_between_group_delta(
    df: pd.DataFrame,
    horizon_col: str,
    status_col: str,
    cohort_col: str = "strat_002_cohort",
    horizon_name: Optional[str] = None,
) -> BetweenGroupDelta:
    """Compute the between-group delta metrics on one horizon.

    Parameters
    ----------
    df:
        Per-symbol-day frame with cohort_col, horizon_col (return
        values), and status_col (e.g. 'next_session_open_status').
    """
    horizon_name = horizon_name or horizon_col
    high_rets = _cohort_returns(df, cohort_col, "HIGH_RSI", horizon_col, status_col)
    comp_rets = _cohort_returns(df, cohort_col, "COMPARISON", horizon_col, status_col)

    def _med(s):
        return float(s.median()) if len(s) else None
    def _ppos(s):
        return float((s > 0).mean()) if len(s) else None

    median_h = _med(high_rets)
    median_c = _med(comp_rets)
    ppos_h = _ppos(high_rets)
    ppos_c = _ppos(comp_rets)

    delta_median = (median_h - median_c) if (median_h is not None and median_c is not None) else None
    delta_ppos = (ppos_h - ppos_c) if (ppos_h is not None and ppos_c is not None) else None

    # n_with_RSI: per-symbol-day rows in each cohort
    high_n = int((df[cohort_col] == "HIGH_RSI").sum()) if cohort_col in df.columns else 0
    comp_n = int((df[cohort_col] == "COMPARISON").sum()) if cohort_col in df.columns else 0

    return BetweenGroupDelta(
        horizon=horizon_name,
        median_high=median_h,
        median_comparison=median_c,
        delta_median=delta_median,
        p_positive_high=ppos_h,
        p_positive_comparison=ppos_c,
        delta_p_positive=delta_ppos,
        n_high_OK=len(high_rets),
        n_comparison_OK=len(comp_rets),
        n_high_with_RSI=high_n,
        n_comparison_with_RSI=comp_n,
    )


def compute_verdict(
    df: pd.DataFrame,
    horizon_col: str,
    status_col: str,
    cohort_col: str = "strat_002_cohort",
    horizon_name: Optional[str] = None,
    coverage_threshold_pct: float = 50.0,
) -> Verdict:
    """Compute the frozen STRAT-002 verdict for one horizon.

    Verdict precedence (top-down; first match wins):
        1. UNDERPOWERED
        2. REPLICATED
        3. DIRECTIONALLY CONSISTENT BUT WEAK
        4. FAILED TO REPLICATE
    """
    horizon_name = horizon_name or horizon_col
    primary = compute_between_group_delta(
        df, horizon_col, status_col, cohort_col, horizon_name
    )
    high_stats = _group_stats(
        df, "HIGH_RSI", cohort_col, horizon_col, status_col, horizon_name,
        return_status_col=status_col,
    )
    comp_stats = _group_stats(
        df, "COMPARISON", cohort_col, horizon_col, status_col, horizon_name,
        return_status_col=status_col,
    )

    # 1. UNDERPOWERED check
    if (primary.n_high_OK < MIN_SAMPLE_HIGH_RSI_OK
        or primary.n_high_with_RSI < MIN_SAMPLE_HIGH_RSI_WITH_RSI
        or primary.n_comparison_OK < MIN_SAMPLE_COMPARISON_OK):
        verdict = "UNDERPOWERED"
    # 2. REPLICATED check
    elif (primary.delta_median is not None
          and primary.delta_p_positive is not None
          and primary.delta_median > PRACTICAL_REPLICATION_DELTA_MEDIAN_PP
          and primary.delta_p_positive > PRACTICAL_REPLICATION_DELTA_PPOS_PP):
        verdict = "REPLICATED"
    # 3. DIRECTIONALLY CONSISTENT BUT WEAK
    elif ((primary.delta_median is not None and primary.delta_median > 0)
          or (primary.delta_p_positive is not None and primary.delta_p_positive > 0)):
        verdict = "DIRECTIONALLY CONSISTENT BUT WEAK"
    # 4. FAILED TO REPLICATE
    else:
        verdict = "FAILED TO REPLICATE"

    # Optional coverage flag — if either cohort lost >50% to non-OK outcomes.
    coverage_flag = ""
    if primary.n_high_with_RSI > 0:
        high_ok_pct = 100.0 * primary.n_high_OK / primary.n_high_with_RSI
        if high_ok_pct < coverage_threshold_pct:
            coverage_flag = "INCONCLUSIVE_DUE_TO_COVERAGE"
    if primary.n_comparison_with_RSI > 0:
        comp_ok_pct = 100.0 * primary.n_comparison_OK / primary.n_comparison_with_RSI
        if comp_ok_pct < coverage_threshold_pct:
            coverage_flag = "INCONCLUSIVE_DUE_TO_COVERAGE"

    return Verdict(
        verdict=verdict,
        horizon=horizon_name,
        primary=primary,
        high_stats=high_stats,
        comparison_stats=comp_stats,
        coverage_flag=coverage_flag,
    )


def per_date_diagnostic(
    df: pd.DataFrame,
    horizon_col: str,
    status_col: str,
    cohort_col: str = "strat_002_cohort",
    date_col: str = "ny_trading_date",
) -> pd.DataFrame:
    """Per-trading-date per-cohort diagnostic (no verdict per date)."""
    if df.empty:
        return pd.DataFrame()
    rows = []
    for date, sub_date in df.groupby(date_col):
        for cohort in ("HIGH_RSI", "COMPARISON"):
            sub = sub_date[sub_date[cohort_col] == cohort]
            rets_ok = sub.loc[sub[status_col] == "OK", horizon_col].astype(float).dropna()
            n_ok = len(rets_ok)
            n_total = len(sub)
            median_v = float(rets_ok.median()) if n_ok else None
            ppos_v = float((rets_ok > 0).mean()) if n_ok else None
            n_unique_sym = int(sub["symbol"].nunique()) if "symbol" in sub.columns else 0
            rows.append({
                "ny_trading_date": str(date),
                "cohort": cohort,
                "n_total": n_total,
                "n_OK": n_ok,
                "median_return": median_v,
                "p_positive": ppos_v,
                "n_unique_symbols": n_unique_sym,
            })
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    # Add per-date deltas (informational; not part of verdict)
    pivot_med = out.pivot(index="ny_trading_date", columns="cohort", values="median_return")
    pivot_ppos = out.pivot(index="ny_trading_date", columns="cohort", values="p_positive")
    pivot_n_ok = out.pivot(index="ny_trading_date", columns="cohort", values="n_OK")
    delta_med = pivot_med["HIGH_RSI"] - pivot_med["COMPARISON"]
    delta_ppos = pivot_ppos["HIGH_RSI"] - pivot_ppos["COMPARISON"]
    out = out.merge(
        delta_med.rename("delta_median").reset_index(),
        on="ny_trading_date",
        how="left",
    ).merge(
        delta_ppos.rename("delta_p_positive").reset_index(),
        on="ny_trading_date",
        how="left",
    )
    return out
