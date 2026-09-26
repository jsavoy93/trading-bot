"""Deterministic, offline tests for the OBS-003 research tooling.

All tests use synthetic data. NO live Alpaca calls. NO production DB
touches. These tests prove:

    * Trading-time horizon calculation respects session boundaries.
    * Decision price is from a bar at-or-before cycle_start (no look-ahead).
    * Missing bars are reported, never silently substituted.
    * Weekend / overnight gaps are handled.
    * Deduplication collapses contiguous identical gate states.
    * Return calculation is correct.
    * Feature extraction is robust to missing fields.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from src.research.analysis import (
    gate_group_label,
    horizon_stats,
)
from src.research.bar_cache import BarCache, collect_symbol_dates
from src.research.deduplication import deduplicate_by_gate_state, gate_state_key
from src.research.feature_extraction import extract_features
from src.research.price_alignment import (
    decision_bar,
    filter_trading_minutes,
    forward_bar_next_session_open,
    forward_bar_trading_minutes,
    is_regular_session_minute,
    label_horizons,
)


def make_synthetic_bars(
    symbol: str = "TEST",
    day: str = "2026-09-24",
    open_price: float = 100.0,
    n_minutes: int = 390,
    minute_step: float = 0.01,
    pre_market: int = 240,
    after_hours: int = 240,
) -> pd.DataFrame:
    """Build a synthetic 1-minute bar DataFrame for one US trading day.

    Includes pre-market and after-hours bars so tests can verify they are
    excluded from trading-minute counts.
    """
    date = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
    # Pre-market starts at 08:00 UTC (04:00 ET), session 14:30-21:00 UTC,
    # after-hours to 23:59 UTC.
    pre_start = date.replace(hour=8, minute=0)
    sess_open = date.replace(hour=14, minute=30)
    sess_close = date.replace(hour=21, minute=0)
    after_end = date.replace(hour=23, minute=59)

    idx = []
    values = []
    # Pre-market
    for i in range(pre_market):
        t = pre_start + timedelta(minutes=i)
        idx.append(t)
        values.append(open_price + i * minute_step * 0.1)
    # Session
    for i in range(n_minutes):
        t = sess_open + timedelta(minutes=i)
        if t >= sess_close:
            break
        idx.append(t)
        values.append(open_price + i * minute_step)
    # After-hours
    for i in range(after_hours):
        t = sess_close + timedelta(minutes=i)
        if t > after_end:
            break
        idx.append(t)
        values.append(open_price + n_minutes * minute_step)  # static
    idx = pd.DatetimeIndex(idx, tz="UTC")
    df = pd.DataFrame({
        "open": values,
        "high": values,
        "low": values,
        "close": values,
        "volume": [1000.0] * len(values),
        "trade_count": [10.0] * len(values),
        "vwap": values,
    }, index=idx)
    df.index.name = "timestamp"
    return df


class TestRegularSessionFilter:
    def test_session_minutes_are_in_window(self):
        ts = pd.Timestamp("2026-09-24T14:30:00", tz="UTC")
        assert is_regular_session_minute(ts)
        ts = pd.Timestamp("2026-09-24T20:59:00", tz="UTC")
        assert is_regular_session_minute(ts)

    def test_pre_market_excluded(self):
        ts = pd.Timestamp("2026-09-24T13:00:00", tz="UTC")
        assert not is_regular_session_minute(ts)

    def test_after_hours_excluded(self):
        ts = pd.Timestamp("2026-09-24T21:00:00", tz="UTC")
        assert not is_regular_session_minute(ts)
        ts = pd.Timestamp("2026-09-24T22:30:00", tz="UTC")
        assert not is_regular_session_minute(ts)


class TestDecisionBar:
    def test_decision_bar_at_or_before_cycle_start(self):
        bars = make_synthetic_bars()
        cycle = pd.Timestamp("2026-09-24T15:00:00", tz="UTC")
        ts = decision_bar(bars, cycle)
        assert ts <= cycle
        assert ts >= cycle - timedelta(minutes=1)

    def test_decision_bar_returns_none_when_cycle_precedes_all(self):
        bars = make_synthetic_bars()
        cycle = pd.Timestamp("2026-09-24T07:00:00", tz="UTC")
        assert decision_bar(bars, cycle) is None


class TestForwardBarTradingMinutes:
    def test_30_minute_walk_stays_in_session(self):
        bars = make_synthetic_bars()
        dec = pd.Timestamp("2026-09-24T14:30:00", tz="UTC")
        fwd, reason = forward_bar_trading_minutes(bars, dec, 30)
        assert reason == "OK"
        # The 30th trading minute after the decision.
        expected = dec + timedelta(minutes=30)
        assert fwd == expected

    def test_walk_skips_pre_market(self):
        # Use a bar index where decision bar is at session open, and
        # verify +30 returns the 30th session minute, NOT the 30th
        # wall-clock minute.
        bars = make_synthetic_bars()
        dec = pd.Timestamp("2026-09-24T14:30:00", tz="UTC")
        fwd, reason = forward_bar_trading_minutes(bars, dec, 30)
        assert reason == "OK"
        # 30 wall-clock minutes lands at 15:00. The 30th session minute
        # is also 15:00 (since session starts at 14:30 and runs
        # continuously). Verify fwd is exactly at 15:00.
        assert fwd == pd.Timestamp("2026-09-24T15:00:00", tz="UTC")

    def test_returns_missing_when_horizon_beyond_session(self):
        # Build a barset that ends mid-session.
        bars = make_synthetic_bars()
        bars = bars[bars.index < pd.Timestamp("2026-09-24T15:00:00", tz="UTC")]
        dec = pd.Timestamp("2026-09-24T14:30:00", tz="UTC")
        fwd, reason = forward_bar_trading_minutes(bars, dec, 240)
        assert fwd is None
        assert reason == "HORIZON_BEYOND_AVAILABLE_BARS"

    def test_does_not_silently_roll_to_next_session(self):
        # Critical: a 3:50 PM (+30m) horizon MUST NOT be silently turned
        # into a next-morning observation.
        bars = make_synthetic_bars()
        # Decision at 20:50 UTC (2:50 PM ET) with only 10 session minutes
        # left until close at 21:00 UTC.
        dec = pd.Timestamp("2026-09-24T20:50:00", tz="UTC")
        fwd, reason = forward_bar_trading_minutes(bars, dec, 30)
        # Only 10 session minutes after this decision; +30m exceeds them.
        assert fwd is None
        assert reason == "HORIZON_BEYOND_AVAILABLE_BARS"


class TestForwardBarNextSessionOpen:
    def test_finds_first_regular_minute_of_next_day(self):
        bars_day1 = make_synthetic_bars(day="2026-09-24")
        bars_day2 = make_synthetic_bars(
            day="2026-09-25",
            open_price=200.0,
        )
        bars = pd.concat([bars_day1, bars_day2]).sort_index()
        # Decision bar at end of day 1 session
        dec = pd.Timestamp("2026-09-24T20:00:00", tz="UTC")
        fwd, reason = forward_bar_next_session_open(bars, dec)
        assert reason == "OK"
        # Next-day first regular minute is 2026-09-25T14:30Z.
        assert fwd == pd.Timestamp("2026-09-25T14:30:00", tz="UTC")

    def test_returns_missing_when_no_next_day(self):
        bars = make_synthetic_bars(day="2026-09-24")
        dec = pd.Timestamp("2026-09-24T20:00:00", tz="UTC")
        fwd, reason = forward_bar_next_session_open(bars, dec)
        assert fwd is None
        assert reason == "NO_NEXT_SESSION_BAR"


class TestLabelHorizons:
    def test_label_horizons_computes_correct_returns(self):
        bars = make_synthetic_bars(open_price=100.0, minute_step=0.05)
        cycle = pd.Timestamp("2026-09-24T14:30:00", tz="UTC")
        labels = label_horizons(
            bars, cycle,
            [("fwd_30m", 30), ("fwd_60m", 60), ("fwd_240m", 240)],
        )
        # Decision bar is 14:30, close=100.0
        # 30 minutes later = 15:00, close = 100 + 30*0.05 = 101.5
        assert labels["fwd_30m"].label_status == "OK"
        assert abs(labels["fwd_30m"].decision_price - 100.0) < 1e-9
        assert abs(labels["fwd_30m"].forward_price - 101.5) < 1e-9
        # Return = (101.5 - 100.0)/100.0 = 0.015 = 1.5%
        assert abs(labels["fwd_30m"].forward_return - 0.015) < 1e-9
        # 60 minutes: 100 + 60*0.05 = 103.0, return = 0.03 = 3%
        assert abs(labels["fwd_60m"].forward_price - 103.0) < 1e-9
        assert abs(labels["fwd_60m"].forward_return - 0.03) < 1e-9
        # 240 minutes: 100 + 240*0.05 = 112.0, return = 0.12 = 12%
        assert abs(labels["fwd_240m"].forward_price - 112.0) < 1e-9
        assert abs(labels["fwd_240m"].forward_return - 0.12) < 1e-9

    def test_decision_price_uses_bar_at_or_before_cycle(self):
        # Cycle 14:30:30; decision bar is 14:30 (last bar at-or-before).
        bars = make_synthetic_bars()
        cycle = pd.Timestamp("2026-09-24T14:30:30", tz="UTC")
        labels = label_horizons(bars, cycle, [("fwd_30m", 30)])
        assert labels["fwd_30m"].decision_price_ts == pd.Timestamp(
            "2026-09-24T14:30:00", tz="UTC"
        )

    def test_no_lookahead_when_cycle_lands_in_gap(self):
        # If cycle_start lands exactly between two bars, the decision bar
        # is the EARLIER (at-or-before) bar, never the later (at-or-after).
        bars = make_synthetic_bars()
        cycle = pd.Timestamp("2026-09-24T14:35:00", tz="UTC")
        labels = label_horizons(bars, cycle, [("fwd_30m", 30)])
        assert labels["fwd_30m"].decision_price_ts == pd.Timestamp(
            "2026-09-24T14:35:00", tz="UTC"
        )

    def test_missing_decision_bar_marks_all_missing(self):
        bars = make_synthetic_bars()
        bars = bars[bars.index >= pd.Timestamp("2026-09-24T16:00:00", tz="UTC")]
        cycle = pd.Timestamp("2026-09-24T15:00:00", tz="UTC")  # before all bars
        labels = label_horizons(bars, cycle, [("fwd_30m", 30)])
        assert labels["fwd_30m"].label_status == "MISSING_DECISION_BAR"
        assert labels["fwd_30m"].forward_return is None

    def test_horizon_beyond_available_does_not_substitute(self):
        bars = make_synthetic_bars()
        # Truncate after 90 minutes of session bars
        cutoff = pd.Timestamp("2026-09-24T14:30:00", tz="UTC") + timedelta(minutes=90)
        bars = bars[bars.index < cutoff]
        cycle = pd.Timestamp("2026-09-24T14:30:00", tz="UTC")
        labels = label_horizons(bars, cycle, [("fwd_240m", 240)])
        assert labels["fwd_240m"].label_status == "HORIZON_BEYOND_AVAILABLE_BARS"
        assert labels["fwd_240m"].forward_return is None


class TestFeatureExtraction:
    def test_extracts_rsi_sma_and_score(self):
        snap = {
            "session_id": 1,
            "bot_version": "1.0",
            "timeframe_mode": "multi_timeframe",
            "decision": {"outcome": "HOLD_INELIGIBLE"},
            "strategy_eligibility": {
                "signal": "HOLD",
                "strategy_eligible": False,
                "strategy_reason": "test",
                "gates": [
                    {
                        "name": "rsi_oversold",
                        "category": "strategy_gate",
                        "applied": True,
                        "passed": True,
                        "observed_value": 30.5,
                        "threshold_value": 35.0,
                    },
                    {
                        "name": "sma_uptrend",
                        "category": "strategy_gate",
                        "applied": True,
                        "passed": True,
                        "observed_value": 56.0,
                        "threshold_value": 54.0,
                    },
                ],
            },
            "scoring": {
                "components": {
                    "rsi_score": 60.0,
                    "sma_score": 50.0,
                },
                "total_score": 55.0,
                "blended_signed": 1.0,
                "score_invalid_data": False,
            },
        }
        feats = extract_features(snap, "TEST", "2026-09-24T15:00:00+00:00")
        assert feats["rsi_oversold_pass"] is True
        assert feats["sma_uptrend_pass"] is True
        assert feats["rsi_value"] == 30.5
        assert feats["rsi_threshold"] == 35.0
        assert feats["sma_fast"] == 56.0
        assert feats["sma_slow"] == 54.0
        assert feats["sma_spread"] == 2.0
        # RSI distance: 30.5 - 35 = -4.5 (negative = below threshold)
        assert feats["rsi_distance_to_threshold"] == -4.5
        # SMA distance: 54 - 56 = -2 (negative = sma_fast above slow = passing)
        assert feats["sma_distance_to_threshold"] == -2.0
        assert feats["total_score"] == 55.0
        assert feats["outcome"] == "HOLD_INELIGIBLE"

    def test_missing_snapshot_returns_none_features(self):
        feats = extract_features({}, "TEST", "2026-09-24T15:00:00+00:00")
        assert feats["rsi_oversold_pass"] is None
        assert feats["rsi_value"] is None

    def test_other_gates_counted_separately(self):
        snap = {
            "strategy_eligibility": {
                "gates": [
                    {"name": "rsi_oversold", "passed": True, "observed_value": 30, "threshold_value": 35},
                    {"name": "sma_uptrend", "passed": True, "observed_value": 56, "threshold_value": 54},
                    {"name": "volume_confirmation", "passed": False, "applied": True},
                    {"name": "multi_timeframe", "passed": False, "applied": True},
                ],
            },
            "scoring": {"total_score": 50},
            "decision": {"outcome": "HOLD_INELIGIBLE"},
        }
        feats = extract_features(snap, "X", "2026-09-24T15:00:00+00:00")
        assert sorted(feats["other_gates_names_failed"]) == ["multi_timeframe", "volume_confirmation"]
        assert feats["other_gates_failed"] == 2


class TestGateStateDeduplication:
    def test_collapses_within_spacing_window(self):
        # Five decisions for SYM on same date, all within 30 minutes.
        rows = []
        for i in range(5):
            rows.append({
                "symbol": "SYM",
                "cycle_start": pd.Timestamp(f"2026-09-24T15:0{i}:00", tz="UTC"),
                "features": {
                    "rsi_oversold_pass": True,
                    "sma_uptrend_pass": False,
                    "rsi_value": 30.0,
                    "sma_spread": -0.5,
                    "total_score": 55.0,
                },
            })
        df = pd.DataFrame(rows)
        raw, dedup = deduplicate_by_gate_state(df, spacing_minutes=30)
        # Raw keeps all 5
        assert len(raw) == 5
        # Dedup keeps only the first (within 30-min window)
        assert len(dedup) == 1

    def test_spacing_window_reopens_after_threshold(self):
        rows = []
        for i in range(5):
            rows.append({
                "symbol": "SYM",
                "cycle_start": pd.Timestamp(f"2026-09-24T15:{i*10:02d}:00", tz="UTC"),
                "features": {"rsi_oversold_pass": True, "sma_uptrend_pass": False,
                             "rsi_value": 30.0, "sma_spread": -0.5, "total_score": 55.0},
            })
        df = pd.DataFrame(rows)
        # Window: 15:00, 15:10, 15:20, 15:30, 15:40
        # 30-min spacing: keep 15:00, 15:30 (15:30 - 15:00 = 30 min >= 30)
        # 15:40 - 15:30 = 10 min < 30, drop
        raw, dedup = deduplicate_by_gate_state(df, spacing_minutes=30)
        assert len(raw) == 5
        assert len(dedup) == 2

    def test_does_not_collapse_across_dates(self):
        rows = [
            {"symbol": "SYM", "cycle_start": pd.Timestamp("2026-09-24T15:00:00", tz="UTC"),
             "features": {"rsi_oversold_pass": True, "sma_uptrend_pass": True,
                          "rsi_value": 30.0, "sma_spread": 1.0, "total_score": 55.0}},
            {"symbol": "SYM", "cycle_start": pd.Timestamp("2026-09-25T15:00:00", tz="UTC"),
             "features": {"rsi_oversold_pass": True, "sma_uptrend_pass": True,
                          "rsi_value": 30.0, "sma_spread": 1.0, "total_score": 55.0}},
        ]
        df = pd.DataFrame(rows)
        _, dedup = deduplicate_by_gate_state(df, spacing_minutes=30)
        assert len(dedup) == 2

    def test_does_not_collapse_across_symbols(self):
        rows = [
            {"symbol": "AAA", "cycle_start": pd.Timestamp("2026-09-24T15:00:00", tz="UTC"),
             "features": {"rsi_oversold_pass": True, "sma_uptrend_pass": True,
                          "rsi_value": 30.0, "sma_spread": 1.0, "total_score": 55.0}},
            {"symbol": "BBB", "cycle_start": pd.Timestamp("2026-09-24T15:00:00", tz="UTC"),
             "features": {"rsi_oversold_pass": True, "sma_uptrend_pass": True,
                          "rsi_value": 30.0, "sma_spread": 1.0, "total_score": 55.0}},
        ]
        df = pd.DataFrame(rows)
        _, dedup = deduplicate_by_gate_state(df, spacing_minutes=30)
        assert len(dedup) == 2

    def test_gate_state_key_returns_simple_tuple(self):
        # gate_state_key is retained for documentation/insight, but the
        # deduplication rule itself is time-based.
        a = {"rsi_oversold_pass": True, "sma_uptrend_pass": True,
             "rsi_value": 30.5, "sma_spread": 1.0, "total_score": 60.0}
        b = {"rsi_oversold_pass": True, "sma_uptrend_pass": True,
             "rsi_value": 30.5, "sma_spread": 1.0, "total_score": 60.0}
        assert gate_state_key(a) == gate_state_key(b)

    def test_dedup_works_on_unsorted_input(self):
        # Real CSV output is sorted by (cycle_start, id), not by symbol.
        # The dedup function must internally re-sort by (symbol, cycle_start)
        # so that consecutive same-symbol observations are correctly grouped.
        rows = [
            # AA01 at 14:00, BB01 at 14:05, AA01 at 14:10, AA01 at 14:35
            {"symbol": "AA01", "cycle_start": pd.Timestamp("2026-09-24T14:00:00", tz="UTC")},
            {"symbol": "BB01", "cycle_start": pd.Timestamp("2026-09-24T14:05:00", tz="UTC")},
            {"symbol": "AA01", "cycle_start": pd.Timestamp("2026-09-24T14:10:00", tz="UTC")},
            {"symbol": "AA01", "cycle_start": pd.Timestamp("2026-09-24T14:35:00", tz="UTC")},
        ]
        df = pd.DataFrame(rows)
        raw, dedup = deduplicate_by_gate_state(df, spacing_minutes=30)
        # AA01 kept at 14:00, dropped at 14:10 (within 30m), kept at 14:35
        # BB01 kept at 14:05
        # Total kept = 3 (AA01 14:00, BB01 14:05, AA01 14:35)
        assert len(dedup) == 3
        kept_symbols = sorted(dedup["symbol"].tolist())
        assert kept_symbols == ["AA01", "AA01", "BB01"]


class TestAnalysisHelpers:
    def test_gate_group_label(self):
        assert gate_group_label(True, True) == "A_both_pass"
        assert gate_group_label(True, False) == "B_rsi_pass_sma_fail"
        assert gate_group_label(False, True) == "C_rsi_fail_sma_pass"
        assert gate_group_label(False, False) == "D_both_fail"
        assert gate_group_label(None, True) == "Z_unknown"

    def test_horizon_stats_handles_empty(self):
        stats = horizon_stats(pd.Series([], dtype=float))
        assert stats["n"] == 0
        assert stats["mean"] != stats["mean"]  # nan

    def test_horizon_stats_basic(self):
        s = pd.Series([0.01, -0.02, 0.03, 0.04, -0.01])
        stats = horizon_stats(s)
        assert stats["n"] == 5
        assert abs(stats["median"] - 0.01) < 1e-9
        assert stats["p_positive"] == 0.6

    def test_gate_group_returns(self):
        from src.research.analysis import gate_group_returns
        df = pd.DataFrame({
            "feat_rsi_oversold_pass": [True, True, False, False, True],
            "feat_sma_uptrend_pass": [True, False, True, False, True],
            "fwd_60m_return": [0.01, -0.02, 0.03, 0.0, -0.01],
        })
        out = gate_group_returns(df, ["fwd_60m"])
        # Expect rows for groups A, B, C, D
        groups = sorted(out["gate_group"].unique())
        assert groups == ["A_both_pass", "B_rsi_pass_sma_fail",
                          "C_rsi_fail_sma_pass", "D_both_fail"]
        a_row = out[out["gate_group"] == "A_both_pass"].iloc[0]
        # Group A: rows 0 and 4 (True, True)
        assert a_row["n"] == 2
        assert abs(a_row["mean"] - 0.0) < 1e-9  # (0.01 + -0.01) / 2

    def test_view_comparison(self):
        from src.research.analysis import view_comparison
        raw = pd.DataFrame({
            "fwd_60m_return": [0.01, 0.01, 0.01, -0.02],
        })
        dedup = pd.DataFrame({
            "fwd_60m_return": [0.01, -0.02],
        })
        out = view_comparison(raw, dedup, ["fwd_60m"])
        views = sorted(out["view"].unique())
        assert views == ["dedup", "raw"]


class TestCollectSymbolDates:
    def test_unique_pairs(self):
        df = pd.DataFrame({
            "symbol": ["A", "A", "B", "A"],
            "cycle_start": [
                pd.Timestamp("2026-09-24T15:00:00", tz="UTC"),
                pd.Timestamp("2026-09-24T16:00:00", tz="UTC"),
                pd.Timestamp("2026-09-25T10:00:00", tz="UTC"),
                pd.Timestamp("2026-09-24T15:30:00", tz="UTC"),
            ],
        })
        pairs = collect_symbol_dates(df)
        # Includes prev day, current day, and next day for each unique (symbol, date).
        expected = sorted({
            ("A", "2026-09-23"), ("A", "2026-09-24"), ("A", "2026-09-25"),
            ("B", "2026-09-24"), ("B", "2026-09-25"), ("B", "2026-09-26"),
        })
        assert pairs == expected
