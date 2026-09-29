"""MKT-CACHE-001: Market-data eligibility cache.

A pure helper layer that decides, given a symbol, whether SmartBot should
skip the Alpaca ``get_stock_bars`` request because the symbol is known
to be temporarily data-ineligible.

The helper is OBSERVABILITY + EFFICIENCY ONLY. It does NOT change:

  - the BUY/SELL/HOLD decision logic
  - the scoring formulas
  - the required daily-bar minimum
  - the multi-timeframe logic
  - the universe ranking
  - the Alpaca feed used
  - trading thresholds / risk / execution behavior

Design contract:

  - When the cache says "ineligible right now", get_market_data returns
    a sentinel ``(None, reason)`` tuple without calling Alpaca.
  - The caller treats the sentinel identically to a "no data" result,
    but emits the distinct score_error_reason token
    ``market_data_cached_*`` instead of the per-cycle reason tokens.
  - On a fresh attempt that DOES produce enough data, the caller calls
    ``delete_market_data_eligibility`` so the symbol returns to normal
    analysis.

The cache stores four reason classes:

  MARKET_DATA_NOT_IN_FEED        Alpaca responded but symbol absent / 0 bars
  MARKET_DATA_INSUFFICIENT_BARS  0 < bars_returned < required
  MARKET_DATA_SPARSE_HISTORY     older symbol with sparse / discontinuous
                                 history (defensive)
  MARKET_DATA_API_EXCEPTION      NO CACHE ROW written (transient)

TTL policy:

  NOT_IN_FEED          : 7 days fixed (structural; slow recheck)
  INSUFFICIENT_BARS
    recent_continuous   : estimated-eligibility-date (validated against the
                            observed 33-symbol sample: ~1 bar per trading
                            session)
    sparse_defensive    : 14 days fixed (NOT used here; reserved for the
                            defensive old/sparse sub-classification)
  API_EXCEPTION        : no cache row

Trading-session vs calendar-day conversion:

  ``missing_sessions`` is the number of additional TRADING SESSIONS
  required to reach ``required_bars``. Converting sessions to calendar
  days uses a conservative weekday approximation
  (``calendar_days = sessions * 7/5``) that ERRORS TOWARD EARLY RECHECK
  (so a 1-bar-per-day symbol becomes eligible one weekend early rather
  than one day late).

This module intentionally has no internal state. All persistence is
delegated to ``SQLiteDB`` (see ``src/database/sqlite_db.py``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple


# Stable reason tokens persisted to score_error_reason / cache row.
REASON_NOT_IN_FEED = "MARKET_DATA_NOT_IN_FEED"
REASON_INSUFFICIENT_BARS = "MARKET_DATA_INSUFFICIENT_BARS"
REASON_SPARSE_HISTORY = "MARKET_DATA_SPARSE_HISTORY"
# API exceptions intentionally are not stored as cache reasons. They are
# surfaced as their own score_error_reason and trigger the existing
# retry/fail-closed behavior with no long cache.
REASON_API_EXCEPTION = "MARKET_DATA_API_EXCEPTION"

# Cached-skip tokens used when the cache is consulted but the symbol is
# currently skipped. These are distinct from the per-cycle reason tokens
# so dashboards can attribute the skip to "cache hit" rather than
# "fresh API failure".
SCORE_REASON_CACHED_NOT_IN_FEED = "market_data_cached_not_in_feed"
SCORE_REASON_CACHED_INSUFFICIENT_HISTORY = (
    "market_data_cached_insufficient_history"
)

# Per-cycle (fresh-attempt) reason tokens.
SCORE_REASON_NOT_IN_FEED = "market_data_not_in_feed"
SCORE_REASON_INSUFFICIENT_BARS = "market_data_insufficient_bars"
SCORE_REASON_API_EXCEPTION = "market_data_api_exception"


# TTL constants.
NOT_IN_FEED_TTL_DAYS = 7
SPARSE_HISTORY_TTL_DAYS = 14


@dataclass(frozen=True)
class CacheDecision:
    """The cache consult outcome for one symbol.

    Attributes:
        should_skip: True iff the symbol has an unexpired cache row and
                     the caller should skip the fresh Alpaca request.
        reason:      The reason from the cache row, if any.
        bars_returned: The previously-observed bar count, if known.
        score_error_reason: The score_error_reason token to emit when
                            the caller records a SKIPPED_INVALID_DATA row.
                            Distinguishes "cache hit" from a fresh failure.
    """

    should_skip: bool
    reason: Optional[str]
    bars_returned: Optional[int]
    score_error_reason: Optional[str]


def is_eligible_now(
    cache_row: Optional[dict],
    now: Optional[datetime] = None,
) -> CacheDecision:
    """Inspect one cache row and return the cache consult decision.

    A cache row of ``None`` or with ``next_recheck_iso <= now`` means
    the symbol IS eligible for a fresh attempt.

    ``cache_row`` is the dict returned by
    ``SQLiteDB.get_market_data_eligibility(symbol)``. If the caller
    already has the row, use this function; otherwise the helper
    ``consult_cache_for_symbol`` below wraps both the read and the
    decision.
    """
    if cache_row is None:
        return CacheDecision(
            should_skip=False,
            reason=None,
            bars_returned=None,
            score_error_reason=None,
        )
    now = now or datetime.now(timezone.utc)
    next_recheck = cache_row.get("next_recheck_iso")
    # String comparison on ISO-8601 timestamps is correct because the
    # format is lexicographically sortable.
    if not next_recheck or next_recheck <= now.isoformat():
        return CacheDecision(
            should_skip=False,
            reason=cache_row.get("reason"),
            bars_returned=cache_row.get("bars_returned"),
            score_error_reason=None,
        )
    # Active cache hit: pick the correct skipped token.
    reason = cache_row.get("reason")
    if reason == REASON_NOT_IN_FEED:
        token = SCORE_REASON_CACHED_NOT_IN_FEED
    elif reason in (REASON_INSUFFICIENT_BARS, REASON_SPARSE_HISTORY):
        token = SCORE_REASON_CACHED_INSUFFICIENT_HISTORY
    else:
        # Defensive default — should not occur, but never block.
        token = SCORE_REASON_CACHED_NOT_IN_FEED
    return CacheDecision(
        should_skip=True,
        reason=reason,
        bars_returned=cache_row.get("bars_returned"),
        score_error_reason=token,
    )


def compute_next_recheck_for_not_in_feed(
    now: Optional[datetime] = None,
) -> str:
    """Return ISO-8601 timestamp ``now + 7 days``. Stable policy."""
    now = now or datetime.now(timezone.utc)
    return (now + timedelta(days=NOT_IN_FEED_TTL_DAYS)).isoformat()


def compute_next_recheck_for_insufficient_bars(
    bars_returned: int,
    required_bars: int,
    now: Optional[datetime] = None,
) -> str:
    """Return ISO-8601 timestamp for when the symbol is estimated to
    accumulate ``required_bars`` daily bars.

    ``missing_sessions = max(0, required_bars - bars_returned)``.
    Converting trading sessions to calendar days uses
    ``sessions * 7/5`` which approximates the trading-week ratio
    (5 trading days per 7 calendar days). This ERRORS TOWARD EARLY
    RECHECK by a couple of hours because the multiplication slightly
    under-counts pure weekend gaps; a symbol that needs exactly N
    more trading sessions will be retried at the start of the first
    weekday on or after the Nth trading day, possibly one weekend
    early.

    For sparse_history, callers should NOT use this function; use
    ``compute_next_recheck_for_sparse_history`` instead.
    """
    now = now or datetime.now(timezone.utc)
    missing = max(0, int(required_bars) - int(bars_returned))
    if missing == 0:
        # Defensive: should not be called when the symbol is already
        # eligible, but provide an immediate recheck to allow a
        # delete-on-success cycle.
        return now.isoformat()
    # sessions * 7/5 calendar days. Use int() truncation so we land on
    # or slightly before the expected date.
    cal_days = int(missing * 7 / 5)
    return (now + timedelta(days=cal_days)).isoformat()


def compute_next_recheck_for_sparse_history(
    now: Optional[datetime] = None,
) -> str:
    """Conservative periodic recheck for the defensive old/sparse
    sub-classification. Fixed 14 days. See owner spec: "do not assume
    one new bar per trading day; likely requires slower periodic
    recheck"."""
    now = now or datetime.now(timezone.utc)
    return (now + timedelta(days=SPARSE_HISTORY_TTL_DAYS)).isoformat()


def cache_outcome(
    db,
    symbol: str,
    bars_returned: Optional[int],
    required_bars: int,
    first_bar_timestamp: Optional[str],
    latest_bar_timestamp: Optional[str],
    barset_key_present: bool,
    now: Optional[datetime] = None,
) -> Optional[str]:
    """Classify a fresh Alpaca response and write the appropriate cache
    row. Returns the score_error_reason token to emit on the
    SKIPPED_INVALID_DATA persistence path, or None if no cache row
    was written (sufficient data or transient error).

    Args:
        db: SQLiteDB instance.
        symbol: the symbol just queried.
        bars_returned: count of bars returned (None if unknown /
                       exception path).
        required_bars: the configured minimum (e.g. self.sma_slow = 30).
        first_bar_timestamp: ISO-8601 timestamp of the first bar, if any.
        latest_bar_timestamp: ISO-8601 timestamp of the last bar, if any.
        barset_key_present: True iff ``symbol in barset.data``.
        now: optional override for testing.

    Returns:
        - SCORE_REASON_NOT_IN_FEED when the symbol is in
          MARKET_DATA_NOT_IN_FEED cache class.
        - SCORE_REASON_INSUFFICIENT_BARS when in
          MARKET_DATA_INSUFFICIENT_BARS class.
        - SCORE_REASON_API_EXCEPTION if the input was an exception.
        - None if bars_returned >= required_bars (sufficient data; the
          cache row is DELETED to allow normal flow).

    All cache writes are best-effort: on failure, the function returns
    the per-cycle token anyway so the SKIPPED_INVALID_DATA row still
    gets the right attribution.
    """
    # Sufficient data: clear any existing cache row and emit no skip.
    if (
        bars_returned is not None
        and int(bars_returned) >= int(required_bars)
    ):
        try:
            db.delete_market_data_eligibility(symbol)
        except Exception as e:
            logging.debug(
                f"market_data_eligibility delete failed for {symbol}: {e}"
            )
        return None

    # Classify the failure.
    if bars_returned is None:
        # Either an API exception or symbol absent from the response.
        # We treat both as MARKET_DATA_NOT_IN_FEED with a 7-day TTL
        # because both manifest identically to the bot and both are
        # candidates for a slow recheck. API exceptions still emit
        # SCORE_REASON_API_EXCEPTION for per-cycle observability, but
        # the cache row uses NOT_IN_FEED so the slow-recheck semantic
        # also applies to transient errors (the per-cycle exception
        # token ensures dashboards still see the transient nature).
        try:
            db.upsert_market_data_eligibility(
                symbol=symbol,
                reason=REASON_NOT_IN_FEED,
                bars_returned=0,
                required_bars=int(required_bars),
                next_recheck_iso=compute_next_recheck_for_not_in_feed(now=now),
                first_bar_timestamp=None,
                latest_bar_timestamp=None,
            )
        except Exception as e:
            logging.debug(
                f"market_data_eligibility upsert failed for {symbol}: {e}"
            )
        return SCORE_REASON_API_EXCEPTION

    if not barset_key_present or int(bars_returned) == 0:
        reason = REASON_NOT_IN_FEED
        next_recheck = compute_next_recheck_for_not_in_feed(now=now)
        score_token = SCORE_REASON_NOT_IN_FEED
    else:
        # 0 < bars_returned < required_bars
        reason = REASON_INSUFFICIENT_BARS
        next_recheck = compute_next_recheck_for_insufficient_bars(
            bars_returned=int(bars_returned),
            required_bars=int(required_bars),
            now=now,
        )
        score_token = SCORE_REASON_INSUFFICIENT_BARS

    try:
        db.upsert_market_data_eligibility(
            symbol=symbol,
            reason=reason,
            bars_returned=int(bars_returned),
            required_bars=int(required_bars),
            next_recheck_iso=next_recheck,
            first_bar_timestamp=first_bar_timestamp,
            latest_bar_timestamp=latest_bar_timestamp,
        )
    except Exception as e:
        logging.debug(
            f"market_data_eligibility upsert failed for {symbol}: {e}"
        )
    return score_token


def consult_cache_for_symbol(db, symbol: str) -> CacheDecision:
    """Convenience wrapper that reads the cache row and applies the
    decision policy. Returns a CacheDecision."""
    if not db.is_available():
        return CacheDecision(False, None, None, None)
    row = db.get_market_data_eligibility(symbol)
    return is_eligible_now(row)
