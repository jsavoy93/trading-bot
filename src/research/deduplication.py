"""Deduplication rules for repeated decisions during the same market state.

SmartBot evaluates the same symbol many times per trading day. Two
decisions for the same symbol separated by 30 seconds are usually
observing essentially the same market state and gate outcome.

This module defines a SIMPLE, DEFENSIBLE deduplication rule:

    For each symbol on each UTC trading date, keep at most one observation
    every DEDUP_MIN_SPACING_MINUTES minutes. The first observation in
    each spacing window is retained; subsequent ones are dropped from
    the deduplicated view.

    Spacing is measured in wall-clock minutes from the kept observation's
    ``cycle_start``. A kept observation opens a new spacing window.

This is intentionally simple:
    - No complex statistics
    - No clustering
    - No semantic equivalence beyond time proximity
    - All raw observations remain available for the raw view

The rationale:

    "SmartBot evaluates the same symbols repeatedly. Repeated cycles
    during essentially the same market state should not dominate
    conclusions."

A symbol that produces 50 identical HOLD_INELIGIBLE decisions during a
quiet 30-minute window should not look like 50 independent observations.
By spacing deduplicated observations at least ``DEDUP_MIN_SPACING_MINUTES``
minutes apart, we collapse rapid-fire decisions into a smaller,
representative event stream.

The deduplicated view retains at most one observation per
``DEDUP_MIN_SPACING_MINUTES`` minutes per (symbol, UTC trading date).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

import pandas as pd


DEDUP_MIN_SPACING_MINUTES = 30


def gate_state_key(features: dict, include_score_band: bool = True) -> tuple:
    """Compute a coarse gate-state tuple.

    Retained for the unit tests but no longer used for deduplication
    (the deduplication rule is now time-based).
    """
    rsi_pass = features.get("rsi_oversold_pass")
    sma_pass = features.get("sma_uptrend_pass")
    rsi_val = features.get("rsi_value")
    sma_spread = features.get("sma_spread")
    score = features.get("total_score")
    return (rsi_pass, sma_pass, rsi_val, sma_spread, score)


def _ensure_utc(ts):
    if hasattr(ts, "tzinfo") and ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts


def deduplicate_by_gate_state(
    df: pd.DataFrame,
    symbol_col: str = "symbol",
    ts_col: str = "cycle_start",
    spacing_minutes: int = DEDUP_MIN_SPACING_MINUTES,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split a decision-level frame into raw + deduplicated views.

    The deduplication rule (TIME-BASED):
        Within (symbol, UTC trading date), keep at most one observation
        every ``spacing_minutes`` minutes. The first observation in each
        spacing window is retained.

    Returns
    -------
    (raw_df, dedup_df)
        ``raw_df`` is a copy of the input with no rows dropped.
        ``dedup_df`` is the deduplicated view.
    """
    raw = df.reset_index(drop=True).copy()
    if raw.empty:
        return raw, raw.copy()

    # IMPORTANT: sort by (symbol, cycle_start) so consecutive rows for the
    # same symbol appear together. Without this sort, the
    # ``prev_sym != sym`` short-circuit would always be true and the
    # rule would never collapse anything.
    raw = raw.sort_values([symbol_col, ts_col]).reset_index(drop=True)

    keep_mask = []
    prev_sym = None
    prev_date = None
    prev_kept_ts = None
    spacing_td = pd.Timedelta(minutes=spacing_minutes)

    for sym, ts in zip(raw[symbol_col], raw[ts_col].map(_ensure_utc)):
        date = ts.tz_convert("UTC").date()
        if (
            sym != prev_sym
            or date != prev_date
            or prev_kept_ts is None
            or (ts - prev_kept_ts) >= spacing_td
        ):
            keep_mask.append(True)
            prev_sym = sym
            prev_date = date
            prev_kept_ts = ts
        else:
            keep_mask.append(False)

    raw["_keep_in_dedup"] = keep_mask
    dedup = raw[raw["_keep_in_dedup"]].drop(columns=["_keep_in_dedup"]).copy()
    raw_view = raw.drop(columns=["_keep_in_dedup"]).copy()
    return raw_view, dedup


def _ensure_utc(ts):
    if hasattr(ts, "tzinfo") and ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts
