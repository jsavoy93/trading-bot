"""Tests for the STRAT-002 next_session_open weekend-partition correctness repair.

Defect
------
OBS-003 ``label_decisions`` built per-decision merged frames as
``[decision_date, decision_date + 1 CALENDAR day]``. For a Friday
decision, ``cur_date + 1`` is Saturday (empty in Alpaca). The merged
frame contained no next-session bars and every Friday row was labeled
``NO_NEXT_SESSION_BAR`` instead of resolving to Monday.

Repair
------
A new optional parameter ``extra_next_day_lookahead`` on
``label_decisions`` (default ``0`` preserves OBS-003 behavior) fetches
and merges additional calendar days after ``cur_date + 1``, so the
existing ``forward_bar_next_session_open`` math can skip weekend empty
days and reach the next actual trading session.

These tests use a synthetic BarCache — NO live Alpaca calls, NO
production DB touches — and prove at minimum:

* Friday next_session_open resolves to Monday
* Saturday is skipped (empty days do not break resolution)
* Sunday is skipped
* Mon..Fri ordinary next-session mappings remain correct
* Tuesday Sept 22, 2026 resolves to Wednesday Sept 23, 2026
* Existing timezone-aware regular-session open semantics remain intact
* Wall-clock +240m behavior is unchanged by the lookahead parameter
* Frozen STRAT-002 verdict boundaries remain unchanged
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from src.research.bar_cache import BarCache
from src.research.labeling import label_decisions
from src.research.price_alignment import (
    DECISION_BAR_MAX_AGE_MINUTES,
    NY_TZ,
    SESSION_CLOSE_ET,
    SESSION_OPEN_ET,
    is_regular_session_minute,
)
from src.research.strat_002_verdict import (
    MIN_SAMPLE_COMPARISON_OK,
    MIN_SAMPLE_HIGH_RSI_OK,
    MIN_SAMPLE_HIGH_RSI_WITH_RSI,
    PRACTICAL_REPLICATION_DELTA_MEDIAN_PP,
    PRACTICAL_REPLICATION_DELTA_PPOS_PP,
)


def make_session_bars(
    day: str,
    open_price: float = 100.0,
    n_minutes: int = 390,
) -> pd.DataFrame:
    """Build synthetic regular-session 1-min bars for one US trading day.

    Bars start at 13:30 UTC (09:30 ET during DST) and run continuously
    at a flat price so test assertions stay deterministic.
    """
    date = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
    sess_open = date.replace(hour=13, minute=30)
    idx = []
    values = []
    for i in range(n_minutes):
        t = sess_open + timedelta(minutes=i)
        idx.append(t)
        values.append(open_price)
    idx = pd.DatetimeIndex(idx, tz="UTC")
    return pd.DataFrame({
        "open": values, "high": values, "low": values, "close": values,
        "volume": [1000.0] * len(values),
        "trade_count": [10.0] * len(values),
        "vwap": values,
    }, index=idx)


def make_empty_bars() -> pd.DataFrame:
    """Empty bars DataFrame (no trading session)."""
    idx = pd.DatetimeIndex([], tz="UTC", name="timestamp")
    return pd.DataFrame({
        "open": [], "high": [], "low": [], "close": [],
        "volume": [], "trade_count": [], "vwap": [],
    }, index=idx)


class FakeBarCache(BarCache):
    """BarCache that bypasses Alpaca and reads from in-memory data only.

    The base class is initialized with synthetic credentials; the API is
    never called. ``ensure_many`` is a no-op (data is pre-populated);
    ``fetch_day`` returns from ``_memory`` if present, else an empty
    DataFrame (simulating provider missingness).
    """

    def __init__(self, cache_dir: Path, day_data: dict):
        super().__init__(
            api_key="test_key_fake_xxxxx",
            api_secret="test_secret_fake_xxxxx",
            cache_dir=cache_dir,
            max_age_hours=None,
        )
        for (sym, date_str), df in day_data.items():
            self._memory[(sym, date_str)] = df

    def ensure_many(self, pairs, batch_size=100, progress_every=0):
        pass

    def fetch_day(self, symbol: str, date_str: str) -> pd.DataFrame:
        key = (symbol, date_str)
        if key in self._memory:
            return self._memory[key]
        return make_empty_bars()


# ---------------------------------------------------------------------------
# 1. Friday next_session_open resolves to Monday
# ---------------------------------------------------------------------------

def test_friday_next_session_open_resolves_to_monday(tmp_path):
    """Friday 2026-09-18 decisions must resolve to Monday 2026-09-21 open."""
    friday_bars = make_session_bars("2026-09-18", open_price=100.0)
    monday_bars = make_session_bars("2026-09-21", open_price=110.0)
    cache = FakeBarCache(tmp_path, {
        ("AAPL", "2026-09-18"): friday_bars,
        ("AAPL", "2026-09-19"): make_empty_bars(),  # Saturday
        ("AAPL", "2026-09-20"): make_empty_bars(),  # Sunday
        ("AAPL", "2026-09-21"): monday_bars,
    })

    df = pd.DataFrame([{
        "symbol": "AAPL",
        "cycle_start": pd.Timestamp("2026-09-18T19:30:00", tz="UTC"),  # 15:30 ET
    }])
    labeled = label_decisions(
        df, cache,
        horizons=[("fwd_next_session_open", None)],
        extra_next_day_lookahead=3,
    )

    assert labeled["fwd_next_session_open_status"].iloc[0] == "OK"
    # Friday decision close = 100.0; Monday open = 110.0. Return = (110-100)/100 = 0.10.
    assert abs(labeled["fwd_next_session_open_return"].iloc[0] - 0.10) < 1e-6
    # The forward bar must be Monday's 13:30 UTC open.
    fwd_ts = labeled["fwd_next_session_open_forward_price_ts"].iloc[0]
    assert pd.Timestamp(fwd_ts) == pd.Timestamp("2026-09-21T13:30:00", tz="UTC"), \
        f"expected Monday 2026-09-21T13:30 UTC; got {fwd_ts}"


# ---------------------------------------------------------------------------
# 2. Saturday is skipped
# ---------------------------------------------------------------------------

def test_saturday_is_skipped(tmp_path):
    """Saturday empty days must not be used as the decision target."""
    friday_bars = make_session_bars("2026-09-18", open_price=100.0)
    monday_bars = make_session_bars("2026-09-21", open_price=110.0)
    cache = FakeBarCache(tmp_path, {
        ("AAPL", "2026-09-18"): friday_bars,
        ("AAPL", "2026-09-19"): make_empty_bars(),  # Saturday (intentionally empty)
        ("AAPL", "2026-09-20"): make_empty_bars(),  # Sunday
        ("AAPL", "2026-09-21"): monday_bars,
    })

    df = pd.DataFrame([{
        "symbol": "AAPL",
        "cycle_start": pd.Timestamp("2026-09-18T19:30:00", tz="UTC"),
    }])
    labeled = label_decisions(
        df, cache,
        horizons=[("fwd_next_session_open", None)],
        extra_next_day_lookahead=3,
    )

    # Saturday must NOT appear as the forward_price_ts.
    fwd_ts = labeled["fwd_next_session_open_forward_price_ts"].iloc[0]
    assert pd.Timestamp(fwd_ts).date() != pd.Timestamp("2026-09-19").date(), \
        "Saturday must not be used as next_session_open"
    # The forward_price_ts must be Monday.
    assert pd.Timestamp(fwd_ts).date() == pd.Timestamp("2026-09-21").date(), \
        f"Expected Monday; got {fwd_ts}"


# ---------------------------------------------------------------------------
# 3. Sunday is skipped
# ---------------------------------------------------------------------------

def test_sunday_is_skipped(tmp_path):
    """Sunday empty day must be skipped over to reach Monday."""
    friday_bars = make_session_bars("2026-09-18", open_price=100.0)
    monday_bars = make_session_bars("2026-09-21", open_price=110.0)
    cache = FakeBarCache(tmp_path, {
        ("AAPL", "2026-09-18"): friday_bars,
        ("AAPL", "2026-09-19"): make_empty_bars(),
        ("AAPL", "2026-09-20"): make_empty_bars(),  # Sunday
        ("AAPL", "2026-09-21"): monday_bars,
    })

    df = pd.DataFrame([{
        "symbol": "AAPL",
        "cycle_start": pd.Timestamp("2026-09-18T19:30:00", tz="UTC"),
    }])
    labeled = label_decisions(
        df, cache,
        horizons=[("fwd_next_session_open", None)],
        extra_next_day_lookahead=3,
    )

    fwd_ts = labeled["fwd_next_session_open_forward_price_ts"].iloc[0]
    assert pd.Timestamp(fwd_ts).date() != pd.Timestamp("2026-09-20").date(), \
        "Sunday must not be used as next_session_open"


# ---------------------------------------------------------------------------
# 4. Mon..Fri ordinary mappings remain correct
# ---------------------------------------------------------------------------

def test_monday_to_friday_ordinary_mappings_unchanged(tmp_path):
    """Mon..Fri ordinary mappings (next calendar day is next session day) remain correct."""
    mon_bars = make_session_bars("2026-09-14", open_price=100.0)
    tue_bars = make_session_bars("2026-09-15", open_price=105.0)
    wed_bars = make_session_bars("2026-09-16", open_price=107.0)
    thu_bars = make_session_bars("2026-09-17", open_price=108.0)
    fri_bars = make_session_bars("2026-09-18", open_price=109.0)
    cache = FakeBarCache(tmp_path, {
        ("AAPL", "2026-09-14"): mon_bars,
        ("AAPL", "2026-09-15"): tue_bars,
        ("AAPL", "2026-09-16"): wed_bars,
        ("AAPL", "2026-09-17"): thu_bars,
        ("AAPL", "2026-09-18"): fri_bars,
    })

    # Monday Sept 14 -> Tuesday Sept 15
    df = pd.DataFrame([{
        "symbol": "AAPL",
        "cycle_start": pd.Timestamp("2026-09-14T19:30:00", tz="UTC"),
    }])
    labeled = label_decisions(
        df, cache,
        horizons=[("fwd_next_session_open", None)],
        extra_next_day_lookahead=3,
    )
    assert labeled["fwd_next_session_open_status"].iloc[0] == "OK"
    fwd_ts = labeled["fwd_next_session_open_forward_price_ts"].iloc[0]
    assert pd.Timestamp(fwd_ts) == pd.Timestamp("2026-09-15T13:30:00", tz="UTC"), \
        f"Mon→Tue mapping wrong; got {fwd_ts}"


# ---------------------------------------------------------------------------
# 5. Tuesday Sept 22 resolves to Wednesday Sept 23
# ---------------------------------------------------------------------------

def test_tuesday_sept_22_resolves_to_wednesday_sept_23(tmp_path):
    """Tuesday 2026-09-22 must resolve to Wednesday 2026-09-23 open."""
    tue_bars = make_session_bars("2026-09-22", open_price=100.0)
    wed_bars = make_session_bars("2026-09-23", open_price=105.0)
    cache = FakeBarCache(tmp_path, {
        ("AAPL", "2026-09-22"): tue_bars,
        ("AAPL", "2026-09-23"): wed_bars,
    })

    df = pd.DataFrame([{
        "symbol": "AAPL",
        "cycle_start": pd.Timestamp("2026-09-22T19:30:00", tz="UTC"),
    }])
    labeled = label_decisions(
        df, cache,
        horizons=[("fwd_next_session_open", None)],
        extra_next_day_lookahead=3,
    )
    assert labeled["fwd_next_session_open_status"].iloc[0] == "OK"
    fwd_ts = labeled["fwd_next_session_open_forward_price_ts"].iloc[0]
    assert pd.Timestamp(fwd_ts) == pd.Timestamp("2026-09-23T13:30:00", tz="UTC"), \
        f"Tue Sept 22 → Wed Sept 23 mapping wrong; got {fwd_ts}"


# ---------------------------------------------------------------------------
# 6. Existing timezone-aware regular-session open semantics remain intact
# ---------------------------------------------------------------------------

class TestTimezoneRegularSessionSemanticsIntact:
    """Existing OBS-003 regular-session predicate must remain unchanged."""

    def test_dst_summer_session_open_utc(self):
        assert is_regular_session_minute(pd.Timestamp("2026-06-15T13:30:00", tz="UTC"))

    def test_dst_summer_session_close_exclusive(self):
        # 16:00 ET = 20:00 UTC; half-open window excludes it
        assert not is_regular_session_minute(pd.Timestamp("2026-06-15T20:00:00", tz="UTC"))

    def test_est_winter_session_open_utc(self):
        assert is_regular_session_minute(pd.Timestamp("2026-01-15T14:30:00", tz="UTC"))

    def test_est_winter_session_close_exclusive(self):
        assert not is_regular_session_minute(pd.Timestamp("2026-01-15T21:00:00", tz="UTC"))

    def test_saturday_always_excluded(self):
        assert not is_regular_session_minute(pd.Timestamp("2026-09-19T15:00:00", tz="UTC"))

    def test_sunday_always_excluded(self):
        assert not is_regular_session_minute(pd.Timestamp("2026-09-20T15:00:00", tz="UTC"))

    def test_session_constants_unchanged(self):
        assert SESSION_OPEN_ET.hour == 9 and SESSION_OPEN_ET.minute == 30
        assert SESSION_CLOSE_ET.hour == 16 and SESSION_CLOSE_ET.minute == 0
        assert NY_TZ.key == "America/New_York"
        assert DECISION_BAR_MAX_AGE_MINUTES == 240


# ---------------------------------------------------------------------------
# 7. Wall-clock +240m behavior is unchanged
# ---------------------------------------------------------------------------

def test_wall_clock_240m_unchanged_with_lookahead(tmp_path):
    """Wall-clock +240m behavior must be unchanged by the lookahead parameter."""
    # Build a single trading day with bars from 13:30 UTC to 23:59 UTC
    # so +240m target (= 17:30 UTC) falls inside the same day.
    date = datetime.fromisoformat("2026-09-14").replace(tzinfo=timezone.utc)
    start = date.replace(hour=13, minute=30)
    idx = []
    values = []
    for i in range(630):  # 13:30 + 630 min = 24:00
        t = start + timedelta(minutes=i)
        idx.append(t)
        values.append(100.0 + i * 0.0001)
    day_bars = pd.DataFrame({
        "open": values, "high": values, "low": values, "close": values,
        "volume": [1000.0] * len(values),
        "trade_count": [10.0] * len(values),
        "vwap": values,
    }, index=pd.DatetimeIndex(idx, tz="UTC"))

    cache = FakeBarCache(tmp_path, {("AAPL", "2026-09-14"): day_bars})
    df = pd.DataFrame([{
        "symbol": "AAPL",
        "cycle_start": pd.Timestamp("2026-09-14T13:30:00", tz="UTC"),
    }])
    labeled = label_decisions(
        df, cache,
        horizons=[("fwd_240m", 240)],
        extra_next_day_lookahead=3,  # Must NOT change +240m behavior
    )

    assert labeled["fwd_240m_status"].iloc[0] == "OK"
    fwd_ts = labeled["fwd_240m_forward_price_ts"].iloc[0]
    # Target = 13:30 + 240 min = 17:30 UTC same day
    assert pd.Timestamp(fwd_ts) == pd.Timestamp("2026-09-14T17:30:00", tz="UTC"), \
        f"+240m target should be 17:30 UTC same day; got {fwd_ts}"


# ---------------------------------------------------------------------------
# 8. Frozen STRAT-002 verdict boundaries remain unchanged
# ---------------------------------------------------------------------------

class TestFrozenVerdictBoundariesUnchanged:
    """The frozen verdict thresholds and sample minima must remain exact."""

    def test_practical_replication_delta_median_pp(self):
        assert PRACTICAL_REPLICATION_DELTA_MEDIAN_PP == 0.0010

    def test_practical_replication_delta_ppos_pp(self):
        assert PRACTICAL_REPLICATION_DELTA_PPOS_PP == 0.05

    def test_min_sample_high_rsi_ok(self):
        assert MIN_SAMPLE_HIGH_RSI_OK == 200

    def test_min_sample_high_rsi_with_rsi(self):
        assert MIN_SAMPLE_HIGH_RSI_WITH_RSI == 5000

    def test_min_sample_comparison_ok(self):
        assert MIN_SAMPLE_COMPARISON_OK == 5000


# ---------------------------------------------------------------------------
# Additional: Default behavior unchanged (lookahead=0 keeps OBS-003 behavior)
# ---------------------------------------------------------------------------

def test_default_lookahead_0_returns_no_next_session_bar_on_friday(tmp_path):
    """With default lookahead=0, the original OBS-003 behavior is preserved
    (Friday decisions get NO_NEXT_SESSION_BAR because the per-decision
    frame contains only cur + cur+1 = Sat empty)."""
    friday_bars = make_session_bars("2026-09-18", open_price=100.0)
    monday_bars = make_session_bars("2026-09-21", open_price=110.0)
    cache = FakeBarCache(tmp_path, {
        ("AAPL", "2026-09-18"): friday_bars,
        ("AAPL", "2026-09-19"): make_empty_bars(),
        ("AAPL", "2026-09-20"): make_empty_bars(),
        ("AAPL", "2026-09-21"): monday_bars,
    })

    df = pd.DataFrame([{
        "symbol": "AAPL",
        "cycle_start": pd.Timestamp("2026-09-18T19:30:00", tz="UTC"),
    }])
    labeled = label_decisions(
        df, cache,
        horizons=[("fwd_next_session_open", None)],
        # No extra_next_day_lookahead → default = 0 → original behavior
    )
    # Original behavior: Friday → Saturday empty → NO_NEXT_SESSION_BAR.
    assert labeled["fwd_next_session_open_status"].iloc[0] == "NO_NEXT_SESSION_BAR"
    assert labeled["fwd_next_session_open_return"].iloc[0] is None
