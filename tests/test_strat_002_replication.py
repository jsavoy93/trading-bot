"""Deterministic synthetic tests for STRAT-002 frozen specification.

These tests verify the FROZEN specification from PR #98 (commit e62a31e,
merged as c8da017) BEFORE running against the holdout. They use synthetic
data only — no production DB, no Alpaca, no holds on the holdout.

Rules covered:

1.  One observation per symbol-day (per NY trading date).
2.  LAST regular-session decision selected (not first).
3.  Pre-market decisions excluded (cycle_start < 09:30 ET).
4.  16:00 ET decisions excluded (close bound is exclusive).
5.  RSI = 55 belongs to HIGH-RSI (>= boundary).
6.  RSI < 55 belongs to COMPARISON.
7.  Missing RSI excluded from both cohorts.
8.  Weekend excluded (Sat/Sun cycle_start).
9.  next_session_open horizon semantics (forward).
10. +240m wall-clock horizon semantics (forward).
11. Between-group delta calculations.
12. Verdict classification boundaries (UNDERPOWERED / REPLICATED /
    DIRECTIONALLY CONSISTENT BUT WEAK / FAILED TO REPLICATE).

If any test fails: fix implementation only. Do NOT inspect holdout.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from src.research.symbol_day import (
    assign_strat_002_cohort,
    collapse_to_symbol_days,
    is_regular_session_decision,
    ny_trading_date,
)
from src.research.strat_002_verdict import (
    MIN_SAMPLE_COMPARISON_OK,
    MIN_SAMPLE_HIGH_RSI_OK,
    MIN_SAMPLE_HIGH_RSI_WITH_RSI,
    PRACTICAL_REPLICATION_DELTA_MEDIAN_PP,
    PRACTICAL_REPLICATION_DELTA_PPOS_PP,
    compute_between_group_delta,
    compute_verdict,
    per_date_diagnostic,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NY_TZ = ZoneInfo("America/New_York")


def _ts(iso_utc: str) -> pd.Timestamp:
    """Parse a UTC ISO string into a tz-aware pandas Timestamp."""
    return pd.Timestamp(iso_utc).tz_convert("UTC") if "Z" in iso_utc or "+" in iso_utc \
        else pd.Timestamp(iso_utc).tz_localize("UTC")


def _make_decision_frame(rows: list[dict]) -> pd.DataFrame:
    """Build a per-decision frame with at least symbol + cycle_start."""
    df = pd.DataFrame(rows)
    df["cycle_start"] = pd.to_datetime(df["cycle_start"], utc=True)
    return df


# ---------------------------------------------------------------------------
# 1. One observation per symbol-day
# ---------------------------------------------------------------------------

def test_one_observation_per_symbol_day():
    """Multiple cycles for the same (date, symbol) collapse to ONE row."""
    rows = [
        {"symbol": "AAPL", "cycle_start": "2026-09-14T13:35:00Z"},
        {"symbol": "AAPL", "cycle_start": "2026-09-14T14:30:00Z"},
        {"symbol": "AAPL", "cycle_start": "2026-09-14T15:45:00Z"},
        {"symbol": "AAPL", "cycle_start": "2026-09-14T19:30:00Z"},  # 15:30 ET
        {"symbol": "MSFT", "cycle_start": "2026-09-14T13:30:00Z"},
    ]
    df = _make_decision_frame(rows)
    sd, excluded = collapse_to_symbol_days(df)
    assert len(sd) == 2, f"expected 2 symbol-days, got {len(sd)}"
    aapl = sd[sd["symbol"] == "AAPL"].iloc[0]
    # LAST regular-session decision for AAPL on 2026-09-14 is 19:30 UTC = 15:30 ET
    assert aapl["cycle_start"] == _ts("2026-09-14T19:30:00Z"), \
        f"expected 19:30 UTC (15:30 ET) last; got {aapl['cycle_start']}"


# ---------------------------------------------------------------------------
# 2. LAST regular-session decision selected
# ---------------------------------------------------------------------------

def test_last_regular_session_decision_selected_not_first():
    """The decision with the LARGEST cycle_start within the regular session wins."""
    rows = [
        {"symbol": "AAPL", "cycle_start": "2026-09-14T13:35:00Z"},  # 09:35 ET
        {"symbol": "AAPL", "cycle_start": "2026-09-14T15:30:00Z"},  # 11:30 ET
        {"symbol": "AAPL", "cycle_start": "2026-09-14T19:30:00Z"},  # 15:30 ET
    ]
    df = _make_decision_frame(rows)
    sd, _ = collapse_to_symbol_days(df)
    assert len(sd) == 1
    assert sd.iloc[0]["cycle_start"] == _ts("2026-09-14T19:30:00Z")


# ---------------------------------------------------------------------------
# 3. Pre-market decisions excluded
# ---------------------------------------------------------------------------

def test_premarket_decision_excluded():
    """Cycle_start at 12:00 UTC = 08:00 ET (before 09:30 ET open) is EXCLUDED."""
    rows = [
        {"symbol": "AAPL", "cycle_start": "2026-09-14T12:00:00Z"},  # 08:00 ET
    ]
    df = _make_decision_frame(rows)
    sd, excluded = collapse_to_symbol_days(df)
    assert len(sd) == 0, "pre-market decision should not produce a symbol-day"
    assert len(excluded) == 1


# ---------------------------------------------------------------------------
# 4. 16:00 ET decision excluded (close bound exclusive)
# ---------------------------------------------------------------------------

def test_1600_et_decision_excluded():
    """Cycle_start at 20:00 UTC = 16:00 ET (close) is EXCLUDED (exclusive)."""
    rows = [
        {"symbol": "AAPL", "cycle_start": "2026-09-14T20:00:00Z"},  # 16:00 ET
    ]
    df = _make_decision_frame(rows)
    sd, excluded = collapse_to_symbol_days(df)
    assert len(sd) == 0, "16:00 ET exact should not produce a symbol-day"
    assert len(excluded) == 1


def test_1559_et_decision_included():
    """Cycle_start at 19:59 UTC = 15:59 ET (1 minute before close) is INCLUDED."""
    rows = [
        {"symbol": "AAPL", "cycle_start": "2026-09-14T19:59:00Z"},  # 15:59 ET
    ]
    df = _make_decision_frame(rows)
    sd, _ = collapse_to_symbol_days(df)
    assert len(sd) == 1


# ---------------------------------------------------------------------------
# 5. RSI = 55 belongs to HIGH-RSI
# ---------------------------------------------------------------------------

def test_rsi_55_is_high_rsi():
    """Boundary case: RSI exactly equal to the cutoff belongs to HIGH_RSI."""
    assert assign_strat_002_cohort(55.0) == "HIGH_RSI"
    assert assign_strat_002_cohort(55.0, high_rsi_cutoff=55.0) == "HIGH_RSI"


def test_rsi_54_99_is_comparison():
    """Just below cutoff is COMPARISON."""
    assert assign_strat_002_cohort(54.99) == "COMPARISON"
    assert assign_strat_002_cohort(0.0) == "COMPARISON"
    assert assign_strat_002_cohort(100.0) == "HIGH_RSI"


# ---------------------------------------------------------------------------
# 6. RSI < 55 belongs to COMPARISON (covered above)
# ---------------------------------------------------------------------------

def test_rsi_54_is_comparison():
    assert assign_strat_002_cohort(54.0) == "COMPARISON"


# ---------------------------------------------------------------------------
# 7. Missing RSI excluded
# ---------------------------------------------------------------------------

def test_missing_rsi_excluded():
    assert assign_strat_002_cohort(None) is None
    assert assign_strat_002_cohort(float("nan")) is None
    assert assign_strat_002_cohort(pd.NA) is None


# ---------------------------------------------------------------------------
# 8. Weekend excluded
# ---------------------------------------------------------------------------

def test_weekend_cycle_start_excluded():
    """Saturday 14:00 ET and Sunday 14:00 ET both fall outside regular session."""
    rows = [
        {"symbol": "AAPL", "cycle_start": "2026-09-12T18:00:00Z"},  # Sat 14:00 ET
        {"symbol": "AAPL", "cycle_start": "2026-09-13T18:00:00Z"},  # Sun 14:00 ET
    ]
    df = _make_decision_frame(rows)
    sd, excluded = collapse_to_symbol_days(df)
    assert len(sd) == 0
    assert len(excluded) == 2


def test_sept_14_2026_is_monday():
    """Sanity check: Sept 14, 2026 is a Monday (eligible)."""
    d = ny_trading_date(_ts("2026-09-14T13:30:00Z"))
    assert d.isoformat() == "2026-09-14"
    assert d.weekday() == 0  # Monday


# ---------------------------------------------------------------------------
# 9. next_session_open semantics — covered by OBS-003 tests already;
#    here we only assert that the column name lookup is correct.
# ---------------------------------------------------------------------------

def test_next_session_open_column_mapping():
    """Frozen: primary horizon fwd_next_session_open maps to
    fwd_next_session_open_return and fwd_next_session_open_status columns."""
    from src.research.run_strat_002 import HORIZON_RETURN_COL, HORIZON_STATUS_COL
    assert HORIZON_RETURN_COL["fwd_next_session_open"] == "fwd_next_session_open_return"
    assert HORIZON_STATUS_COL["fwd_next_session_open"] == "fwd_next_session_open_status"


# ---------------------------------------------------------------------------
# 10. +240m wall-clock semantics
# ---------------------------------------------------------------------------

def test_fwd_240m_column_mapping():
    from src.research.run_strat_002 import HORIZON_RETURN_COL, HORIZON_STATUS_COL
    assert HORIZON_RETURN_COL["fwd_240m"] == "fwd_240m_return"
    assert HORIZON_STATUS_COL["fwd_240m"] == "fwd_240m_status"


# ---------------------------------------------------------------------------
# 11. Between-group delta calculations
# ---------------------------------------------------------------------------

def _make_labeled_frame(rows: list[dict]) -> pd.DataFrame:
    """Build a per-symbol-day labeled frame for verdict tests."""
    df = pd.DataFrame(rows)
    for col in ("fwd_next_session_open_return", "fwd_240m_return"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def test_between_group_delta_basic():
    """HIGH_RSI median - COMPARISON median = delta_median."""
    rows = [
        # HIGH_RSI: returns +0.20, +0.30, +0.40 → median +0.30
        {"symbol": "A", "strat_002_cohort": "HIGH_RSI",
         "fwd_next_session_open_return": 0.002,
         "fwd_next_session_open_status": "OK",
         "ny_trading_date": "2026-09-14"},
        {"symbol": "B", "strat_002_cohort": "HIGH_RSI",
         "fwd_next_session_open_return": 0.003,
         "fwd_next_session_open_status": "OK",
         "ny_trading_date": "2026-09-15"},
        {"symbol": "C", "strat_002_cohort": "HIGH_RSI",
         "fwd_next_session_open_return": 0.004,
         "fwd_next_session_open_status": "OK",
         "ny_trading_date": "2026-09-16"},
        # COMPARISON: returns +0.10, +0.15, +0.20 → median +0.15
        {"symbol": "D", "strat_002_cohort": "COMPARISON",
         "fwd_next_session_open_return": 0.001,
         "fwd_next_session_open_status": "OK",
         "ny_trading_date": "2026-09-14"},
        {"symbol": "E", "strat_002_cohort": "COMPARISON",
         "fwd_next_session_open_return": 0.0015,
         "fwd_next_session_open_status": "OK",
         "ny_trading_date": "2026-09-15"},
        {"symbol": "F", "strat_002_cohort": "COMPARISON",
         "fwd_next_session_open_return": 0.002,
         "fwd_next_session_open_status": "OK",
         "ny_trading_date": "2026-09-16"},
    ]
    df = _make_labeled_frame(rows)
    delta = compute_between_group_delta(
        df,
        horizon_col="fwd_next_session_open_return",
        status_col="fwd_next_session_open_status",
    )
    assert delta.n_high_OK == 3
    assert delta.n_comparison_OK == 3
    assert abs(delta.median_high - 0.003) < 1e-9
    assert abs(delta.median_comparison - 0.0015) < 1e-9
    assert abs(delta.delta_median - 0.0015) < 1e-9  # +0.15 pp
    assert delta.p_positive_high == 1.0
    assert delta.p_positive_comparison == 1.0
    assert abs(delta.delta_p_positive) < 1e-9


def test_between_group_delta_handles_missing():
    """Rows with non-OK status are excluded from OK counts."""
    rows = [
        {"symbol": "A", "strat_002_cohort": "HIGH_RSI",
         "fwd_next_session_open_return": 0.002,
         "fwd_next_session_open_status": "OK",
         "ny_trading_date": "2026-09-14"},
        {"symbol": "A2", "strat_002_cohort": "HIGH_RSI",
         "fwd_next_session_open_return": None,
         "fwd_next_session_open_status": "HORIZON_BEYOND_AVAILABLE_BARS",
         "ny_trading_date": "2026-09-14"},
        {"symbol": "D", "strat_002_cohort": "COMPARISON",
         "fwd_next_session_open_return": 0.001,
         "fwd_next_session_open_status": "OK",
         "ny_trading_date": "2026-09-14"},
    ]
    df = _make_labeled_frame(rows)
    delta = compute_between_group_delta(
        df, "fwd_next_session_open_return", "fwd_next_session_open_status"
    )
    assert delta.n_high_OK == 1
    assert delta.n_comparison_OK == 1
    assert delta.n_high_with_RSI == 2


# ---------------------------------------------------------------------------
# 12. Verdict classification boundaries
# ---------------------------------------------------------------------------

def _big_frame(high_rets, comp_rets, n_per_cohort_with_rsi=None):
    """Build a frame with the given return vectors for both cohorts."""
    n_high_with_rsi = n_per_cohort_with_rsi or len(high_rets)
    n_comp_with_rsi = n_per_cohort_with_rsi or len(comp_rets)
    rows = []
    # HIGH_RSI
    for i, r in enumerate(high_rets):
        rows.append({"symbol": f"H{i}", "strat_002_cohort": "HIGH_RSI",
                     "fwd_next_session_open_return": r,
                     "fwd_next_session_open_status": "OK",
                     "ny_trading_date": "2026-09-14"})
    # Pad HIGH_RSI with non-OK rows to reach n_high_with_rsi
    for i in range(len(high_rets), n_high_with_rsi):
        rows.append({"symbol": f"HX{i}", "strat_002_cohort": "HIGH_RSI",
                     "fwd_next_session_open_return": None,
                     "fwd_next_session_open_status": "MISSING_DECISION_BAR",
                     "ny_trading_date": "2026-09-14"})
    # COMPARISON
    for i, r in enumerate(comp_rets):
        rows.append({"symbol": f"C{i}", "strat_002_cohort": "COMPARISON",
                     "fwd_next_session_open_return": r,
                     "fwd_next_session_open_status": "OK",
                     "ny_trading_date": "2026-09-14"})
    for i in range(len(comp_rets), n_comp_with_rsi):
        rows.append({"symbol": f"CX{i}", "strat_002_cohort": "COMPARISON",
                     "fwd_next_session_open_return": None,
                     "fwd_next_session_open_status": "MISSING_DECISION_BAR",
                     "ny_trading_date": "2026-09-14"})
    return _make_labeled_frame(rows)


def test_verdict_replicated():
    """Both deltas pass practical-replication thresholds → REPLICATED."""
    # Practical-replication thresholds are in PERCENTAGE POINTS:
    #   delta_median > +0.10 pp = +0.001 in return-decimal units
    #   delta_p_positive > +5 pp = +0.05 in fractional units
    # HIGH median = +0.005 (+0.50 pp), COMP median = 0 (+0.00 pp)
    # → delta_median = +0.50 pp (PASSES +0.10 pp threshold)
    # HIGH ppos = 80%, COMP ppos = 50%
    # → delta_p_positive = +30 pp (PASSES +5 pp threshold)
    high_rets = [-0.001] * 1000 + [0.005] * 4000  # median=0.005, ppos=80/100
    comp_rets = [-0.001] * 2500 + [0.002] * 2500  # median≈0.0005, ppos=50/100
    df = _big_frame(high_rets, comp_rets,
                     n_per_cohort_with_rsi=5000)  # well above min sample
    v = compute_verdict(df, "fwd_next_session_open_return",
                        "fwd_next_session_open_status")
    assert v.verdict == "REPLICATED", f"got {v.verdict}; delta_median={v.primary.delta_median}, delta_p_positive={v.primary.delta_p_positive}"


def test_verdict_directionally_consistent_but_weak():
    """Direction correct but magnitude below threshold → DIRECTIONALLY CONSISTENT BUT WEAK."""
    # Need: positive direction but BOTH deltas below threshold.
    # HIGH median = +0.03pp (+0.0003), COMP median = +0.01pp (+0.0001) → delta = +0.02pp (BELOW +0.10pp)
    # HIGH ppos = 53%, COMP ppos = 50% → delta = +3pp (BELOW +5pp)
    high_rets = [-0.001] * 2350 + [0.0003] * 2650  # median = 0.0003, ppos=53/100
    comp_rets = [-0.001] * 2500 + [0.0001] * 2500  # median ≈ 0, ppos=50/100
    df = _big_frame(high_rets, comp_rets, n_per_cohort_with_rsi=5000)
    v = compute_verdict(df, "fwd_next_session_open_return",
                        "fwd_next_session_open_status")
    assert v.verdict == "DIRECTIONALLY CONSISTENT BUT WEAK", \
        f"got {v.verdict}; delta_median={v.primary.delta_median}, delta_p_positive={v.primary.delta_p_positive}"


def test_verdict_failed():
    """Both deltas non-positive → FAILED TO REPLICATE."""
    # HIGH median = -0.5pp (-0.005), COMP median = +0.5pp (+0.005) → delta = -1.0pp
    # HIGH ppos = 40%, COMP ppos = 60% → delta_ppos = -20pp
    high_rets = [-0.005] * 3000 + [0.001] * 2000  # median=-0.005, ppos=40/100
    comp_rets = [0.005] * 2750 + [-0.001] * 2250  # median≈0.002, ppos=55/100
    df = _big_frame(high_rets, comp_rets, n_per_cohort_with_rsi=5000)
    v = compute_verdict(df, "fwd_next_session_open_return",
                        "fwd_next_session_open_status")
    assert v.verdict == "FAILED TO REPLICATE", \
        f"got {v.verdict}; delta_median={v.primary.delta_median}, delta_p_positive={v.primary.delta_p_positive}"


def test_verdict_underpowered_high_rsi():
    """n_high_rsi_OK < 200 → UNDERPOWERED (regardless of deltas)."""
    high_rets = [0.005] * 50 + [-0.005] * 50  # 100 OK (below 200)
    comp_rets = [0.001] * 5000 + [-0.001] * 0
    df = _big_frame(high_rets, comp_rets, n_per_cohort_with_rsi=5000)
    v = compute_verdict(df, "fwd_next_session_open_return",
                        "fwd_next_session_open_status")
    assert v.verdict == "UNDERPOWERED", f"got {v.verdict}"


def test_verdict_underpowered_comparison():
    """n_comparison_OK < 5000 → UNDERPOWERED."""
    high_rets = [0.005] * 5000
    comp_rets = [0.001] * 3000 + [-0.001] * 0  # 3000 OK (below 5000)
    df = _big_frame(high_rets, comp_rets, n_per_cohort_with_rsi=10000)
    v = compute_verdict(df, "fwd_next_session_open_return",
                        "fwd_next_session_open_status")
    assert v.verdict == "UNDERPOWERED"


def test_verdict_precedence_underpowered_beats_replicated():
    """If sample is too small, UNDERPOWERED wins even with strong deltas."""
    # 100 high-RSI (below 200) with great performance
    high_rets = [0.01] * 100
    # 5000 comparison with terrible performance
    comp_rets = [-0.01] * 5000
    df = _big_frame(high_rets, comp_rets, n_per_cohort_with_rsi=10000)
    v = compute_verdict(df, "fwd_next_session_open_return",
                        "fwd_next_session_open_status")
    assert v.verdict == "UNDERPOWERED", \
        f"expected UNDERPOWERED precedence; got {v.verdict}"


def test_verdict_precedence_replicated_beats_dcbw():
    """If deltas pass thresholds, REPLICATED wins over DCBW (DCBW is for sub-threshold)."""
    high_rets = [-0.001] * 1000 + [0.005] * 4000  # median=0.005, ppos=80%
    comp_rets = [-0.001] * 2500 + [0.001] * 2500  # median=0, ppos=50%
    df = _big_frame(high_rets, comp_rets, n_per_cohort_with_rsi=5000)
    v = compute_verdict(df, "fwd_next_session_open_return",
                        "fwd_next_session_open_status")
    # delta_median > +0.10pp and delta_p_positive > +5pp → REPLICATED
    assert v.verdict == "REPLICATED"


# ---------------------------------------------------------------------------
# Frozen threshold constants
# ---------------------------------------------------------------------------

def test_frozen_threshold_constants():
    """The frozen threshold values must be exactly these constants.

    Units: returns are decimals (0.005 = +0.5%); p_positive is a fraction.
    So +0.10 percentage points = 0.0010 in decimal return units.
    And +5 percentage points = 0.05 in p_positive fraction units.
    """
    assert PRACTICAL_REPLICATION_DELTA_MEDIAN_PP == 0.0010
    assert PRACTICAL_REPLICATION_DELTA_PPOS_PP == 0.05
    assert MIN_SAMPLE_HIGH_RSI_OK == 200
    assert MIN_SAMPLE_HIGH_RSI_WITH_RSI == 5000
    assert MIN_SAMPLE_COMPARISON_OK == 5000


# ---------------------------------------------------------------------------
# Per-date diagnostic
# ---------------------------------------------------------------------------

def test_per_date_diagnostic_per_date_only():
    rows = [
        {"symbol": "A", "strat_002_cohort": "HIGH_RSI",
         "fwd_next_session_open_return": 0.005, "fwd_next_session_open_status": "OK",
         "ny_trading_date": "2026-09-14"},
        {"symbol": "B", "strat_002_cohort": "COMPARISON",
         "fwd_next_session_open_return": 0.001, "fwd_next_session_open_status": "OK",
         "ny_trading_date": "2026-09-14"},
        {"symbol": "C", "strat_002_cohort": "HIGH_RSI",
         "fwd_next_session_open_return": -0.002, "fwd_next_session_open_status": "OK",
         "ny_trading_date": "2026-09-15"},
    ]
    df = _make_labeled_frame(rows)
    out = per_date_diagnostic(df, "fwd_next_session_open_return",
                              "fwd_next_session_open_status")
    assert len(out) == 4  # 2 dates x 2 cohorts
    # Per-date delta_median on 2026-09-14 = 0.005 - 0.001 = 0.004
    sub = out[out["ny_trading_date"] == "2026-09-14"]
    high = sub[sub["cohort"] == "HIGH_RSI"].iloc[0]
    comp = sub[sub["cohort"] == "COMPARISON"].iloc[0]
    assert high["median_return"] == 0.005
    assert comp["median_return"] == 0.001
    # delta_median column should match
    assert any(out["delta_median"].abs() > 0)
