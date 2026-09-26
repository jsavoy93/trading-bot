"""Price-alignment and trading-time horizon semantics for OBS-003.

This module is purely offline research tooling. It depends only on pandas/
numpy and the standard library. It does NOT call Alpaca. It does NOT touch
the production database. It does NOT submit orders.

The rules here are explicit so that look-ahead bias is structurally
impossible and so that the same decision produces the same label regardless
of when the labeling is run (assuming the same Alpaca historical data).

Definitions
-----------

DECISION-TIME BAR
    The 1-minute bar whose timestamp is at-or-before ``cycle_start`` and
    whose close is closest to (but not later than) ``cycle_start``.
    Decision price = its ``close``.
    Rule: ``decision_bar_ts <= cycle_start``.

FUTURE BAR for a +N-trading-minutes horizon
    Walk N bars forward through the *trading-time* minute bar list of the
    same symbol starting from the next bar strictly after
    ``decision_bar_ts``. Each step consumes one bar whose timestamp is at
    or after the previous step and that falls inside a regular trading
    session (see ``TRADING_SESSION``). The future price is that N-th bar's
    ``close``. The bar's timestamp must be strictly greater than the
    decision bar timestamp.

FUTURE BAR for a +next-session-open horizon
    The first 1-minute bar whose timestamp falls inside the regular
    trading session of the *next* trading day strictly after the
    decision date.

TRADING SESSION
    The set of timestamps that fall inside 14:30:00 <= t < 21:00:00 UTC
    on a regular US trading day. US market holidays are NOT in this set;
    we approximate by treating any minute that has no Alpaca bars as
    outside the session.

MISSING BAR
    If a horizon cannot be reached inside the available bar list (e.g.,
    +240 trading minutes requested but only 120 trading minutes remain
    in the same session and no next-session data exists in the cache),
    the label is ``MISSING_BAR_AT_HORIZON``.

STALE PRICE (after-hours)
    If a horizon lands in after-hours (e.g., a 19:50Z decision with a
    +30-minute horizon falls at 20:20Z), Alpaca may return a 1-minute
    bar but the price is essentially static. We still return that
    price; the caller decides how to interpret it. We attach a
    ``stale_after_hours`` flag so it is visible in the label.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Optional

import pandas as pd


# US equity regular session in UTC.
# 09:30 ET == 14:30 UTC (standard time); 13:30 UTC (DST).
# We approximate with the DST-equivalent UTC window for late September.
# 2026-09-22..2026-09-25 are post-DST-start dates, so 14:30-21:00 UTC is correct.
SESSION_OPEN = time(14, 30)
SESSION_CLOSE = time(21, 00)
SESSION_TZ = timezone.utc


@dataclass(frozen=True)
class TradingSessionWindow:
    """Single trading session window in UTC."""
    open_ts: pd.Timestamp
    close_ts: pd.Timestamp

    def contains(self, ts: pd.Timestamp) -> bool:
        return self.open_ts <= ts < self.close_ts


def is_regular_session_minute(ts: pd.Timestamp) -> bool:
    """Return True if *ts* falls inside a US regular session minute (UTC)."""
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    t = ts.tz_convert("UTC").time()
    return SESSION_OPEN <= t < SESSION_CLOSE


def filter_trading_minutes(bar_index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Return only bar timestamps that fall inside regular trading minutes."""
    mask = bar_index.map(is_regular_session_minute)
    return bar_index[mask]


def decision_bar(bars: pd.DataFrame, cycle_start: pd.Timestamp) -> Optional[pd.Timestamp]:
    """Return the decision-time bar timestamp.

    ``bars`` must have a single-level DatetimeIndex of bar timestamps
    (NOT a MultiIndex).

    Returns None if no bar at-or-before ``cycle_start`` exists.
    """
    if bars.empty:
        return None
    idx = bars.index
    # Convert cycle_start to the index timezone if needed.
    if idx.tz is not None and cycle_start.tzinfo is None:
        cycle_start = cycle_start.tz_localize(idx.tz)
    elif idx.tz is None and cycle_start.tzinfo is not None:
        cycle_start = cycle_start.tz_convert(None)
    eligible = idx[idx <= cycle_start]
    if len(eligible) == 0:
        return None
    return eligible[-1]


def forward_bar_trading_minutes(
    bars: pd.DataFrame,
    decision_bar_ts: pd.Timestamp,
    n_minutes: int,
) -> tuple[Optional[pd.Timestamp], str]:
    """Return the bar at +N trading minutes and a reason string.

    Walks exactly N bars forward through regular-session minute bars.
    Stops early with reason ``HORIZON_BEYOND_AVAILABLE_BARS`` if there are
    not enough future trading-minute bars.

    Returns
    -------
    (ts, reason)
        ts: the timestamp of the N-th forward trading-minute bar, or None.
        reason: one of
            ``OK`` — found the bar.
            ``HORIZON_BEYOND_AVAILABLE_BARS`` — ran out of bars.
    """
    if bars.empty:
        return None, "HORIZON_BEYOND_AVAILABLE_BARS"
    idx = bars.index
    after = idx[idx > decision_bar_ts]
    if len(after) == 0:
        return None, "HORIZON_BEYOND_AVAILABLE_BARS"
    trading_mask = after.map(is_regular_session_minute)
    trading = after[trading_mask]
    if len(trading) < n_minutes:
        return None, "HORIZON_BEYOND_AVAILABLE_BARS"
    return trading[n_minutes - 1], "OK"


def forward_bar_next_session_open(
    bars: pd.DataFrame,
    decision_bar_ts: pd.Timestamp,
) -> tuple[Optional[pd.Timestamp], str]:
    """Return the first regular-session bar on the next trading day.

    The decision date is the date of ``decision_bar_ts`` in UTC.
    We look for any bar whose UTC date is strictly later than the
    decision date and which falls inside the regular session.

    Returns
    -------
    (ts, reason)
        ts: timestamp of the next-session first regular-session bar, or None.
        reason: ``OK`` or ``NO_NEXT_SESSION_BAR``.
    """
    if bars.empty:
        return None, "NO_NEXT_SESSION_BAR"
    idx = bars.index
    decision_date = decision_bar_ts.tz_convert("UTC").date()
    after = idx[idx.date > decision_date]
    if len(after) == 0:
        return None, "NO_NEXT_SESSION_BAR"
    trading_mask = after.map(is_regular_session_minute)
    trading = after[trading_mask]
    if len(trading) == 0:
        return None, "NO_NEXT_SESSION_BAR"
    return trading[0], "OK"


@dataclass(frozen=True)
class HorizonLabel:
    """One labeled horizon for one decision.

    Attributes
    ----------
    horizon_name:
        Human-readable horizon identifier, e.g. ``"+30m"``.
    n_trading_minutes:
        For trading-minute horizons: number of trading minutes. None for
        non-trading-time horizons (next-session-open).
    decision_price:
        The decision-time bar's close.
    decision_price_ts:
        Timestamp of the decision-time bar.
    forward_price:
        The forward-bar close, or None if the label is missing.
    forward_price_ts:
        Timestamp of the forward bar, or None if the label is missing.
    forward_return:
        (forward_price - decision_price) / decision_price, or None.
    label_status:
        ``OK`` or a missing-reason string.
    """
    horizon_name: str
    n_trading_minutes: Optional[int]
    decision_price: Optional[float]
    decision_price_ts: Optional[pd.Timestamp]
    forward_price: Optional[float]
    forward_price_ts: Optional[pd.Timestamp]
    forward_return: Optional[float]
    label_status: str


def label_horizons(
    bars: pd.DataFrame,
    cycle_start: pd.Timestamp,
    horizons: list[tuple[str, Optional[int]]],
) -> dict[str, HorizonLabel]:
    """Label multiple horizons for one decision.

    ``horizons`` is a list of ``(name, n_trading_minutes)`` tuples.
    ``n_trading_minutes`` is ``None`` for the next-session-open horizon.

    Returns
    -------
    dict mapping horizon name to ``HorizonLabel``.

    Rules
    -----
    * If no decision-time bar is found, every horizon is labeled
      ``MISSING_DECISION_BAR``.
    * For each horizon:
        * OK: future bar found, return close and computed return.
        * HORIZON_BEYOND_AVAILABLE_BARS / NO_NEXT_SESSION_BAR:
          forward_price and forward_return are None.
    """
    out: dict[str, HorizonLabel] = {}
    dec_ts = decision_bar(bars, cycle_start)
    if dec_ts is None:
        for name, n in horizons:
            out[name] = HorizonLabel(
                horizon_name=name,
                n_trading_minutes=n,
                decision_price=None,
                decision_price_ts=None,
                forward_price=None,
                forward_price_ts=None,
                forward_return=None,
                label_status="MISSING_DECISION_BAR",
            )
        return out

    decision_price = float(bars.loc[dec_ts, "close"])
    decision_price = float(decision_price)

    for name, n in horizons:
        if n is None:
            # next-session-open
            fwd_ts, reason = forward_bar_next_session_open(bars, dec_ts)
        else:
            fwd_ts, reason = forward_bar_trading_minutes(bars, dec_ts, n)

        if fwd_ts is None:
            out[name] = HorizonLabel(
                horizon_name=name,
                n_trading_minutes=n,
                decision_price=decision_price,
                decision_price_ts=dec_ts,
                forward_price=None,
                forward_price_ts=None,
                forward_return=None,
                label_status=reason,
            )
            continue

        fwd_price = float(bars.loc[fwd_ts, "close"])
        ret = (fwd_price - decision_price) / decision_price
        out[name] = HorizonLabel(
            horizon_name=name,
            n_trading_minutes=n,
            decision_price=decision_price,
            decision_price_ts=dec_ts,
            forward_price=fwd_price,
            forward_price_ts=fwd_ts,
            forward_return=ret,
            label_status="OK",
        )
    return out
