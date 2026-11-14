"""US/Eastern market calendar helper (DB-GROWTH-FIX).

Purpose
-------
DB-GROWTH-FIX needs a single, DST-correct answer to two questions:

1. "Is the US equity market currently open?" — used to skip
   per-symbol ``decision_history`` / ``decision_gate_evaluations``
   writes while the market is closed, and to decide when retention
   pruning may run.
2. "What is the current NY trading date?" — used to run the nightly
   retention prune at most once per NY calendar day.

Design constraints
------------------
* All times are computed with :mod:`zoneinfo` using the
  ``America/New_York`` zone, so EDT/EST transitions are handled by the
  tz database rather than by hand-rolled offsets.
* The helper is pure: it takes an optional ``datetime`` and returns
  plain values. It never touches the database, the network, or the
  broker.
* It never raises for "unknown" input: an unparsable timestamp is
  treated as "market closed" (fail-safe for the *write-skipping* use
  case) — callers that must persist regardless of the clock use
  :func:`market_close_reason` to distinguish the reason.

Holiday data
------------
The holiday lists below are the NYSE (full-day closures) and NYSE
early-close (13:00 ET) calendars. 2026 and 2027 are listed explicitly
so the helper stays correct through the end of 2027; 2024 and 2025 are
included for continuity with the historical data pipeline
(``src/data/historical_pipeline.py``).

``src/data/historical_pipeline.py::MARKET_HOLIDAYS`` is *not* reused
here on purpose: that list is a historical-data-gap heuristic and
contains at least one incorrect 2026 entry (it lists 2026-01-20 for
Martin Luther King Jr. Day, while the actual NYSE closure is
2026-01-19). This module is the single source of truth for
"is the market open".

Failure policy
--------------
If a caller passes a naive datetime it is interpreted as US/Eastern
wall time (that is the only sensible interpretation for a market
calendar). If a timestamp cannot be parsed at all, the market is
reported as CLOSED and the reason is ``"unparsable_timestamp"``.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

NY_TZ = ZoneInfo("America/New_York")

# Regular trading session, US/Eastern.
SESSION_OPEN = time(9, 30)
SESSION_CLOSE = time(16, 0)
# NYSE early-close session (day after Thanksgiving, Christmas Eve).
EARLY_CLOSE = time(13, 0)

# ── NYSE full-day closures ────────────────────────────────────────────────
# Source: NYSE published holiday calendar (2024-2027).
MARKET_HOLIDAYS: frozenset[str] = frozenset(
    {
        # 2024
        "2024-01-01", "2024-01-15", "2024-02-19", "2024-03-29", "2024-05-27",
        "2024-06-19", "2024-07-04", "2024-09-02", "2024-11-28", "2024-12-25",
        # 2025
        "2025-01-01", "2025-01-20", "2025-02-17", "2025-04-18", "2025-05-26",
        "2025-06-19", "2025-07-04", "2025-09-01", "2025-11-27", "2025-12-25",
        # 2026
        "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
        "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
        # 2027
        "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31",
        "2027-06-18", "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24",
    }
)

# ── NYSE 13:00 ET early closes ────────────────────────────────────────────
EARLY_CLOSE_DAYS: frozenset[str] = frozenset(
    {
        "2024-07-03", "2024-11-29", "2024-12-24",
        "2025-07-03", "2025-11-28", "2025-12-24",
        "2026-11-27", "2026-12-24",
        "2027-11-26",
    }
)


def to_ny(value: Optional[datetime] = None) -> datetime:
    """Return ``value`` as an aware US/Eastern datetime.

    Naive datetimes are interpreted as US/Eastern wall time. ``None``
    means "now". Raises ``ValueError`` for values that are not
    datetimes.
    """
    if value is None:
        return datetime.now(tz=NY_TZ)
    if not isinstance(value, datetime):
        raise ValueError(f"not a datetime: {value!r}")
    if value.tzinfo is None:
        return value.replace(tzinfo=NY_TZ)
    return value.astimezone(NY_TZ)


def ny_trading_date(value: Optional[datetime] = None) -> str:
    """Return the current NY calendar date as ``YYYY-MM-DD``."""
    return to_ny(value).date().isoformat()


def session_close_time(value: Optional[datetime] = None) -> time:
    """Return the session close time for the NY day of ``value``."""
    day = to_ny(value).date()
    if day.isoformat() in EARLY_CLOSE_DAYS:
        return EARLY_CLOSE
    return SESSION_CLOSE


def market_close_reason(value: Optional[datetime] = None) -> Optional[str]:
    """Return ``None`` when the market is OPEN, else a short reason token.

    Reason tokens are stable strings so callers can log/count them:
    ``"weekend"``, ``"holiday"``, ``"before_open"``,
    ``"after_close"``, ``"unparsable_timestamp"``.
    """
    try:
        local = to_ny(value)
    except (ValueError, TypeError):
        return "unparsable_timestamp"
    day = local.date()
    if local.weekday() >= 5:
        return "weekend"
    if day.isoformat() in MARKET_HOLIDAYS:
        return "holiday"
    if local.time() < SESSION_OPEN:
        return "before_open"
    if local.time() >= session_close_time(local):
        return "after_close"
    return None


def is_market_open(value: Optional[datetime] = None) -> bool:
    """True when the US equity market is in its regular session."""
    return market_close_reason(value) is None


def is_trading_day(value: Optional[datetime] = None) -> bool:
    """True when the NY calendar day is a weekday that is not a holiday."""
    try:
        day = to_ny(value).date()
    except (ValueError, TypeError):
        return False
    if day.weekday() >= 5:
        return False
    return day.isoformat() not in MARKET_HOLIDAYS


def ny_day_bounds_utc(value: Optional[datetime] = None) -> tuple[str, str]:
    """Return ``(start_iso_utc, end_iso_utc)`` for the NY calendar day.

    Used for "which NY day is it?" bookkeeping (the nightly retention
    prune runs at most once per NY calendar day).
    """
    local = to_ny(value)
    start_local = datetime.combine(local.date(), time(0, 0), tzinfo=NY_TZ)
    end_local = datetime.combine(
        local.date() + timedelta(days=1), time(0, 0), tzinfo=NY_TZ
    )
    return (
        start_local.astimezone(timezone.utc).isoformat(),
        end_local.astimezone(timezone.utc).isoformat(),
    )


def utc_now() -> datetime:
    """Timezone-aware UTC now (used for DB timestamps)."""
    return datetime.now(tz=timezone.utc)


def iso_utc(value: Optional[datetime] = None) -> str:
    """Return an ISO-8601 UTC timestamp string."""
    moment = utc_now() if value is None else value
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat()


def retention_cutoff(days: int, value: Optional[datetime] = None) -> str:
    """Return the ISO UTC timestamp of ``now - days`` (retention cutoff)."""
    if days < 0:
        raise ValueError("retention days must be >= 0")
    moment = utc_now() if value is None else value
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return (moment - timedelta(days=days)).isoformat()
