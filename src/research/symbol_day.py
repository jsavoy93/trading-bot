"""STRAT-002 symbol-day deduplication with regular-session representative.

Frozen pre-registration rules (PR #98, commit e62a31e, merged as c8da017):

    INDEPENDENT UNIT:  SYMBOL × NY TRADING DAY
                       (one symbol, one NY trading date = at most one
                        primary observation)

    REPRESENTATIVE-DECISION RULE:
                       For each (NY trading date, symbol): select the LAST
                       decision whose cycle_start, converted to
                       America/New_York local time via ZoneInfo, has
                       local_time in [09:30, 16:00) ET on a weekday.
                       Symbol-days with no such regular-session decision
                       are EXCLUDED.

The representative-decision rule reuses the OBS-003
``is_regular_session_minute`` predicate. The 16:00 ET bound is
exclusive (matches OBS-003 contract).

This module does NOT inspect forward returns. It only collapses
the per-symbol, per-day decision stream into one representative
decision per (NY trading date, symbol). The forward-return
computation is performed downstream by the labeling pipeline.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from src.research.price_alignment import (
    NY_TZ,
    SESSION_OPEN_ET,
    SESSION_CLOSE_ET,
)


def ny_trading_date(cycle_start: pd.Timestamp) -> pd.Timestamp.date:
    """Return the NY-local calendar date for a decision cycle_start.

    Note: this returns the NY date even if cycle_start falls outside
    regular session hours. The representative-decision rule further
    filters to the [09:30, 16:00) ET window.
    """
    if cycle_start.tzinfo is None:
        cycle_start = cycle_start.tz_localize("UTC")
    return cycle_start.tz_convert(NY_TZ).date()


def is_regular_session_decision(cycle_start: pd.Timestamp) -> bool:
    """Frozen predicate: 09:30 ET <= local cycle_start < 16:00 ET on weekday.

    Reuses OBS-003's ``SESSION_OPEN_ET``, ``SESSION_CLOSE_ET``, and
    ``NY_TZ``. The close bound is exclusive.
    """
    if cycle_start.tzinfo is None:
        cycle_start = cycle_start.tz_localize("UTC")
    ny = cycle_start.tz_convert(NY_TZ)
    if ny.weekday() >= 5:
        return False
    t = ny.time()
    return SESSION_OPEN_ET <= t < SESSION_CLOSE_ET


def collapse_to_symbol_days(
    decisions: pd.DataFrame,
    symbol_col: str = "symbol",
    cycle_start_col: str = "cycle_start",
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Reduce a per-decision frame to one row per (NY trading date, symbol).

    For each (NY trading date, symbol):
      * Filter to regular-session decisions (predicate above).
      * If no regular-session decision exists, EXCLUDE that symbol-day
        from the output frame.
      * Otherwise, retain the LAST regular-session decision
        (largest cycle_start).

    Parameters
    ----------
    decisions:
        Per-decision DataFrame with at minimum ``symbol`` and
        ``cycle_start`` columns. cycle_start may be tz-naive (assumed
        UTC) or tz-aware.

    Returns
    -------
    (symbol_day_df, excluded_df)
        ``symbol_day_df``: one row per (NY trading date, symbol) that
        had at least one regular-session decision. The chosen row's
        cycle_start is the LAST regular-session cycle_start for that
        (NY trading date, symbol).
        ``excluded_df``: rows from the input whose (NY trading date,
        symbol) had NO regular-session decision.
    """
    if decisions.empty:
        empty = pd.DataFrame(columns=list(decisions.columns) + ["ny_trading_date"])
        return empty, decisions.copy()

    df = decisions.copy()
    if df[cycle_start_col].dt.tz is None:
        df[cycle_start_col] = df[cycle_start_col].dt.tz_localize("UTC")

    df["ny_trading_date"] = df[cycle_start_col].dt.tz_convert(NY_TZ).dt.date

    # Regular-session mask
    df["_is_reg"] = df[cycle_start_col].map(is_regular_session_decision)

    # Sort by (ny_trading_date, symbol, cycle_start) ascending so the LAST
    # decision is the one with the largest cycle_start within each group.
    df = df.sort_values(["ny_trading_date", symbol_col, cycle_start_col]).reset_index(drop=True)

    reg = df[df["_is_reg"]].copy()
    excluded = df[~df["_is_reg"]].copy()

    if reg.empty:
        return (
            reg.drop(columns=["_is_reg"]).copy(),
            excluded.drop(columns=["_is_reg"]).copy(),
        )

    # Keep the last row per (ny_trading_date, symbol)
    last_idx = reg.groupby(["ny_trading_date", symbol_col]).tail(1).index
    symbol_day = reg.loc[last_idx].drop(columns=["_is_reg"]).reset_index(drop=True)
    excluded_out = excluded.drop(columns=["_is_reg"]).reset_index(drop=True)
    return symbol_day, excluded_out


def assign_strat_002_cohort(
    rsi_value: Optional[float],
    high_rsi_cutoff: float = 55.0,
) -> Optional[str]:
    """Frozen STRAT-002 cohort classifier.

    Parameters
    ----------
    rsi_value:
        The persisted raw RSI observation (``observed_value`` in the
        decision_snapshot gate record).
    high_rsi_cutoff:
        Frozen at 55.0; do NOT optimize on holdout.

    Returns
    -------
    One of:
        "HIGH_RSI"     — rsi_value >= high_rsi_cutoff
        "COMPARISON"   — rsi_value <  high_rsi_cutoff
        None           — rsi_value is None / missing (EXCLUDED)
    """
    if rsi_value is None:
        return None
    try:
        v = float(rsi_value)
    except (TypeError, ValueError):
        return None
    if pd.isna(v):
        return None
    return "HIGH_RSI" if v >= high_rsi_cutoff else "COMPARISON"
