"""DB-GROWTH-FIX-3 — market calendar coverage.

Verifies the DST-correct ``America/New_York`` calendar used by the
database-growth controls:

* weekends are closed;
* NYSE full-day holidays are closed;
* before the 09:30 open is closed;
* after the 16:00 close is closed;
* 13:00 early-close days close at 13:00;
* the calendars carry explicit 2026 and 2027 entries;
* daylight-saving transitions do not shift the session boundaries.
"""

from datetime import datetime, timedelta, timezone

import pytest

from src.core import market_calendar as mc


def _ny(year, month, day, hour=0, minute=0):
    """Naive NY wall time (interpreted as US/Eastern by the helper)."""
    return datetime(year, month, day, hour, minute)


def _utc(year, month, day, hour=0, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


class TestCalendarBasics:
    def test_regular_session_is_open(self):
        assert mc.is_market_open(_ny(2026, 11, 10, 10, 0)) is True
        assert mc.market_close_reason(_ny(2026, 11, 10, 10, 0)) is None

    def test_open_and_close_boundaries(self):
        # 09:29:59 -> closed, 09:30:00 -> open
        assert mc.is_market_open(_ny(2026, 11, 10, 9, 29)) is False
        assert mc.market_close_reason(_ny(2026, 11, 10, 9, 29)) == "before_open"
        assert mc.is_market_open(_ny(2026, 11, 10, 9, 30)) is True
        # 15:59 -> open, 16:00 -> closed
        assert mc.is_market_open(_ny(2026, 11, 10, 15, 59)) is True
        assert mc.is_market_open(_ny(2026, 11, 10, 16, 0)) is False
        assert mc.market_close_reason(_ny(2026, 11, 10, 16, 0)) == "after_close"

    def test_naive_datetime_is_treated_as_new_york(self):
        assert mc.to_ny(_ny(2026, 11, 10, 10, 0)).hour == 10

    def test_utc_input_is_converted(self):
        # 14:30 UTC in November (EST, UTC-5) == 09:30 NY -> open
        assert mc.is_market_open(_utc(2026, 11, 10, 14, 30)) is True
        # 15:00 UTC == 10:00 NY -> open
        assert mc.is_market_open(_utc(2026, 11, 10, 15, 0)) is True

    def test_unparsable_input_is_closed(self):
        assert mc.is_market_open("not-a-datetime") is False
        assert mc.market_close_reason("not-a-datetime") == "unparsable_timestamp"
        assert mc.is_trading_day("not-a-datetime") is False

    def test_dst_spring_forward_and_fall_back(self):
        # 2026-03-08 is the US DST start; 2026-11-01 the end.
        # 09:30 NY is 13:30 UTC under EDT and 14:30 UTC under EST.
        assert mc.is_market_open(_utc(2026, 3, 9, 13, 30)) is True
        assert mc.is_market_open(_utc(2026, 3, 9, 14, 30)) is True
        assert mc.is_market_open(_utc(2026, 11, 2, 14, 30)) is True
        # The session boundaries in NY wall time do not move with DST.
        assert mc.is_market_open(_ny(2026, 7, 1, 9, 30)) is True
        assert mc.is_market_open(_ny(2026, 12, 1, 9, 30)) is True


class TestWeekends:
    @pytest.mark.parametrize(
        "day", [(2026, 11, 14), (2026, 11, 15), (2027, 1, 9), (2027, 1, 10)],
    )
    def test_weekends_are_closed(self, day):
        moment = _ny(*day, 11, 0)
        assert mc.is_market_open(moment) is False
        assert mc.market_close_reason(moment) == "weekend"
        assert mc.is_trading_day(moment) is False

    def test_friday_after_close_and_monday_before_open(self):
        assert mc.market_close_reason(_ny(2026, 11, 13, 16, 30)) == "after_close"
        assert mc.market_close_reason(_ny(2026, 11, 16, 8, 0)) == "before_open"


class TestHolidays:
    @pytest.mark.parametrize(
        "day",
        [
            (2026, 1, 1),    # New Year's Day
            (2026, 1, 19),   # MLK Day
            (2026, 2, 16),   # Presidents' Day
            (2026, 4, 3),    # Good Friday
            (2026, 5, 25),   # Memorial Day
            (2026, 6, 19),   # Juneteenth
            (2026, 7, 3),    # Independence Day (observed)
            (2026, 9, 7),    # Labor Day
            (2026, 11, 26),  # Thanksgiving
            (2026, 12, 25),  # Christmas
            (2027, 1, 1),
            (2027, 1, 18),   # MLK Day
            (2027, 6, 18),   # Juneteenth (observed)
            (2027, 12, 24),
        ],
    )
    def test_nyse_holidays_are_closed(self, day):
        moment = _ny(*day, 11, 0)
        assert mc.is_market_open(moment) is False
        assert mc.market_close_reason(moment) == "holiday"
        assert mc.is_trading_day(moment) is False

    def test_calendar_covers_2026_and_2027(self):
        for year in (2026, 2027):
            year_holidays = {
                d for d in mc.MARKET_HOLIDAYS if d.startswith(f"{year}-")
            }
            assert len(year_holidays) >= 9, f"{year} holiday list too short"

    def test_known_trading_days_are_open(self):
        for day in [(2026, 11, 10), (2026, 11, 11), (2027, 2, 16)]:
            moment = _ny(*day, 10, 0)
            assert mc.is_market_open(moment) is True
            assert mc.is_trading_day(moment) is True


class TestEarlyClose:
    @pytest.mark.parametrize(
        "day",
        [
            # DB-GROWTH-FIX-4: the NYSE calendar has NO July 2 early
            # close (2026-07-03 is a full closure; 2027-07-03 is a
            # Saturday).
            (2026, 11, 27),  # Friday after Thanksgiving
            (2026, 12, 24),  # Christmas Eve
            (2027, 11, 26),  # Friday after Thanksgiving
        ],
    )
    def test_early_close_days_close_at_13_00(self, day):
        moment = _ny(*day, 12, 59)
        assert mc.is_market_open(moment) is True
        assert mc.market_close_reason(moment) is None
        moment_after = _ny(*day, 13, 0)
        assert mc.is_market_open(moment_after) is False
        assert mc.market_close_reason(moment_after) == "after_close"
        assert mc.session_close_time(moment_after).hour == 13

    def test_regular_day_closes_at_16_00(self):
        assert mc.session_close_time(_ny(2026, 11, 10, 12, 0)).hour == 16

    def test_early_close_list_covers_2026_and_2027(self):
        """DB-GROWTH-FIX-4: the early-close lists are pinned exactly."""
        assert {
            d for d in mc.EARLY_CLOSE_DAYS if d.startswith("2026-")
        } == {"2026-11-27", "2026-12-24"}
        assert {
            d for d in mc.EARLY_CLOSE_DAYS if d.startswith("2027-")
        } == {"2027-11-26"}


class TestTradingDateHelpers:
    def test_ny_trading_date_uses_eastern_time(self):
        # 02:00 UTC on 2026-11-14 is still 2026-11-13 in New York (EST).
        assert mc.ny_trading_date(_utc(2026, 11, 14, 2, 0)) == "2026-11-13"
        # 18:00 UTC on 2026-11-14 is 13:00 NY on the same day.
        assert mc.ny_trading_date(_utc(2026, 11, 14, 18, 0)) == "2026-11-14"

    def test_ny_day_bounds_utc(self):
        start, end = mc.ny_day_bounds_utc(_ny(2026, 11, 13, 12, 0))
        assert start == "2026-11-13T05:00:00+00:00"
        assert end == "2026-11-14T05:00:00+00:00"
        # DST-aware in July (EDT, UTC-4).
        start, end = mc.ny_day_bounds_utc(_ny(2026, 7, 13, 12, 0))
        assert start == "2026-07-13T04:00:00+00:00"
        assert end == "2026-07-14T04:00:00+00:00"

    def test_retention_cutoff_is_utc_iso(self):
        cutoff = mc.retention_cutoff(30, _utc(2026, 11, 14, 12, 0))
        assert cutoff == "2026-10-15T12:00:00+00:00"
        assert mc.retention_cutoff(0, _utc(2026, 11, 14, 12, 0)) == (
            "2026-11-14T12:00:00+00:00"
        )
        with pytest.raises(ValueError):
            mc.retention_cutoff(-1)

    def test_iso_utc_is_timezone_aware(self):
        value = mc.iso_utc(_utc(2026, 11, 14, 12, 0))
        assert value == "2026-11-14T12:00:00+00:00"
        parsed = datetime.fromisoformat(value)
        assert parsed.tzinfo is not None
