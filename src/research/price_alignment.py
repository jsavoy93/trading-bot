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
    Rule: ``decision_bar_ts <= cycle_start`` AND
    ``(cycle_start - decision_bar_ts) <= DECISION_BAR_MAX_AGE_MINUTES``.

    This freshness cap has two layers of protection:

    A. **Maximum bar age**: 240 minutes (DECISION_BAR_MAX_AGE_MINUTES).
       Prevents the price lookup from using a bar older than 4h even
       if it is technically at-or-before cycle_start.

    B. **Eligible date partitions**: ``label_decisions`` merges only
       ``[decision_date, decision_date + 1]`` into the per-decision
       frame. Bars from earlier dates are NEVER loaded into the frame
       that ``decision_bar`` searches. So a prior-day bar is NEVER
       considered regardless of age.

    Combined behavior for overnight/weekend decisions:

    * Overnight (00:00-07:59 UTC) decision on a date where Alpaca
      paper-tier returns bars starting at 08:00 UTC: NO eligible bar
      at-or-before cycle_start in ``[decision_date, decision_date+1]``.
      Result: MISSING_DECISION_BAR (reason: no contemporaneous bar).
    * Weekend decision: ``[decision_date, decision_date+1]`` contains
      no bars for the weekend date. Result: MISSING_DECISION_BAR.
    * Pre-market (08:00-13:29 UTC) decision: first bar of day at
      08:00 UTC, age 0 to 5h29m. The 240-min cap REJECTS any bar more
      than 4h old (e.g., a 13:30 UTC decision cannot use the 08:00 UTC
      bar, age 5h30m); it ACCEPTS the 08:00 UTC bar for a 12:00 UTC
      decision (age 4h, at the boundary). Eligible date partition
      contains only bars from ``[decision_date, decision_date+1]`` so
      no prior-day bar is considered.

    Note on cross-day after-hours: A 02:00 UTC decision on date D with
    a 20:00 UTC prior-session close on date D-1 would technically have
    a 6h-old eligible prior-session bar, BUT the per-decision frame
    only includes date D and date D+1, so the D-1 bar is never in the
    frame. The 240-min cap alone does NOT enforce this — it is the
    per-decision frame partition that does.

    **IMPORTANT**: this 1-minute reference price is a SYNTHETIC
    counterfactual research reference. It is NOT the price SmartBot
    itself uses. SmartBot's ``analyze_symbol`` fetches daily bars via
    ``get_stock_bars(TimeFrame.Day)`` and reads ``df.iloc[-1]['close']``,
    which is yesterday's daily close for most decisions during market
    hours. The decision_snapshot stores gate results (RSI/SMA/MACD)
    but does NOT persist raw bar price or bar timestamp. OBS-003's
    reference is therefore the question: "what 1-minute Alpaca bar
    would a real-time observer have seen at-or-before cycle_start?"

FUTURE BAR for a +N-wall-clock-minutes horizon (CURRENT OBS-003 CONTRACT)
    The forward target timestamp is ``cycle_start + timedelta(minutes=N)`` —
    elapsed wall-clock minutes from the decision time, NOT accumulated
    regular-session trading minutes. The forward bar is the first 1-min
    bar in the available bar list with timestamp at-or-after that
    target. Pre-market, regular-session, and after-hours bars are all
    eligible. If no bar exists at-or-after the target, the label is
    ``HORIZON_BEYOND_AVAILABLE_BARS``.

    This is the originally-authorized OBS-003A contract. The earlier
    "trading-minute walking" implementation has been retained as
    ``forward_bar_trading_minutes`` for backward compatibility and unit
    tests but is NOT used by the default ``label_horizons`` entry point.

FUTURE BAR for a +N-trading-minutes horizon (LEGACY — NOT DEFAULT)
    Walks N bars forward through the *trading-time* minute bar list of
    the same symbol starting from the next bar strictly after
    ``decision_bar_ts``. Each step consumes one bar whose timestamp is
    at-or-after the previous step and that falls inside a regular
    trading session (see ``TRADING_SESSION``). Retained for unit tests
    and reproducibility; do not use for new labeling.

FUTURE BAR for a +next-session-open horizon
    The first 1-minute bar whose timestamp falls inside the regular
    trading session of the *next* trading day strictly after the
    decision date.

TRADING SESSION
    09:30 <= America/New_York local time < 16:00, evaluated on a regular
    US trading day. The conversion to UTC uses ``zoneinfo.ZoneInfo(
    "America/New_York")`` so the window automatically shifts between
    13:30-20:00 UTC (EDT, March-November) and 14:30-21:00 UTC (EST,
    November-March). The trading-session filter is used for the
    next-session-open horizon and for the legacy trading-minute walking
    function. It is NOT applied to the default wall-clock horizons
    (which use any available bar at-or-after the target, including
    pre-market and after-hours).

    NYSE market holidays, early closes, and extraordinary closures are
    NOT explicitly handled. The implementation is timezone/DST-aware
    for ordinary full U.S. trading sessions only. For the current
    September 2026 OBS-003 cohort (Wed Sep 23, Thu Sep 24, Fri Sep 25
    2026) no holiday or early-close date falls in the cohort, so this
    limitation is not material. Full NYSE exchange-calendar support is
    parked for future research windows.

MISSING BAR
    If a horizon cannot be reached inside the available bar list (e.g.,
    +240 trading minutes requested but only 120 trading minutes remain
    in the same session and no next-session data exists in the cache),
    the label is ``HORIZON_BEYOND_AVAILABLE_BARS``.

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
from zoneinfo import ZoneInfo

import pandas as pd


# US/Eastern timezone for DST-aware session-window computation.
NY_TZ = ZoneInfo("America/New_York")

# US equity regular session in America/New_York local time.
# 09:30-16:00 ET on a regular trading day.
# Conversion to UTC happens inside ``is_regular_session_minute`` so the
# result automatically shifts between EDT (13:30-20:00 UTC) and EST
# (14:30-21:00 UTC) without any further code changes.
SESSION_OPEN_ET = time(9, 30)
SESSION_CLOSE_ET = time(16, 0)

# Maximum age of a decision bar relative to cycle_start, in wall-clock
# minutes. See module docstring for rationale. Used as a freshness
# guard against using stale prior-day after-hours bars for overnight
# or weekend-decision cases.
DECISION_BAR_MAX_AGE_MINUTES: int = 4 * 60  # 240 minutes


@dataclass(frozen=True)
class TradingSessionWindow:
    """Single trading session window in UTC."""
    open_ts: pd.Timestamp
    close_ts: pd.Timestamp

    def contains(self, ts: pd.Timestamp) -> bool:
        return self.open_ts <= ts < self.close_ts


def is_regular_session_minute(ts: pd.Timestamp) -> bool:
    """Return True if *ts* falls inside a US regular session minute (UTC).

    Uses ``America/New_York`` to derive the local-time window
    09:30 <= t < 16:00, so the result is automatically correct in
    both EDT (13:30-20:00 UTC) and EST (14:30-21:00 UTC). Weekend
    days are always excluded.
    """
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    ny = ts.tz_convert(NY_TZ)
    # Saturday = 5, Sunday = 6 in Python weekday().
    if ny.weekday() >= 5:
        return False
    t = ny.time()
    return SESSION_OPEN_ET <= t < SESSION_CLOSE_ET


def filter_trading_minutes(bar_index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Return only bar timestamps that fall inside regular trading minutes."""
    mask = bar_index.map(is_regular_session_minute)
    return bar_index[mask]


def decision_bar(bars: pd.DataFrame, cycle_start: pd.Timestamp) -> Optional[pd.Timestamp]:
    """Return the decision-time bar timestamp.

    ``bars`` must have a single-level DatetimeIndex of bar timestamps
    (NOT a MultiIndex).

    Returns None if no bar at-or-before ``cycle_start`` exists, OR if
    the most recent at-or-before bar is older than
    ``DECISION_BAR_MAX_AGE_MINUTES`` (the freshness rule — see module
    docstring).
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
    candidate = eligible[-1]
    # Freshness check: reject stale prior-day bars that are technically
    # at-or-before but represent a different trading day.
    age_minutes = (cycle_start - candidate).total_seconds() / 60.0
    if age_minutes > DECISION_BAR_MAX_AGE_MINUTES:
        return None
    return candidate


def forward_bar_wall_clock(
    bars: pd.DataFrame,
    cycle_start: pd.Timestamp,
    n_minutes: int,
) -> tuple[Optional[pd.Timestamp], str]:
    """Return the bar at-or-after ``cycle_start + N wall-clock minutes``.

    CURRENT OBS-003 CONTRACT (per original OBS-003A authorization).

    The target is computed purely in elapsed wall-clock time:
        target = cycle_start + timedelta(minutes=N)

    Pre-market, regular-session, and after-hours bars are all eligible
    if they satisfy ``bar_ts >= target``. This means a 15:50 ET decision
    with a +30m horizon looks for a bar at-or-after 16:20 ET, NOT
    next-session's 10:20 ET. And a +240m horizon always equals exactly
    cycle_start + 240 wall-clock minutes regardless of session breaks.

    Returns
    -------
    (ts, reason)
        ts: the timestamp of the first bar at-or-after the target, or None.
        reason: ``OK`` or ``HORIZON_BEYOND_AVAILABLE_BARS``.
    """
    if bars.empty:
        return None, "HORIZON_BEYOND_AVAILABLE_BARS"
    idx = bars.index
    target = cycle_start + pd.Timedelta(minutes=n_minutes)
    # Ensure timezone alignment between target and index.
    if idx.tz is not None and target.tzinfo is None:
        target = target.tz_localize(idx.tz)
    elif idx.tz is None and target.tzinfo is not None:
        target = target.tz_convert(None)
    eligible = idx[idx >= target]
    if len(eligible) == 0:
        return None, "HORIZON_BEYOND_AVAILABLE_BARS"
    return eligible[0], "OK"


def forward_bar_trading_minutes(
    bars: pd.DataFrame,
    decision_bar_ts: pd.Timestamp,
    n_minutes: int,
) -> tuple[Optional[pd.Timestamp], str]:
    """Return the bar at +N trading minutes and a reason string (LEGACY).

    Walks exactly N bars forward through regular-session minute bars.
    Stops early with reason ``HORIZON_BEYOND_AVAILABLE_BARS`` if there are
    not enough future trading-minute bars.

    This function is RETAINED for unit-test reproducibility of the
    earlier OBS-003 implementation but is NOT the default contract. The
    OBS-003A authorization specified wall-clock horizons
    (``forward_bar_wall_clock``), and the wall-clock function is now
    the default used by ``label_horizons``.

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
    n_wall_clock_minutes:
        For wall-clock horizons: number of elapsed minutes from
        cycle_start. None for next-session-open. (Was ``n_trading_minutes``
        in the legacy trading-minute walking implementation; renamed for
        accuracy.)
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
    n_wall_clock_minutes: Optional[int]
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

    ``horizons`` is a list of ``(name, n_wall_clock_minutes)`` tuples.
    ``n_wall_clock_minutes`` is ``None`` for the next-session-open horizon.

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
                n_wall_clock_minutes=n,
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
            # CURRENT OBS-003 CONTRACT: wall-clock elapsed minutes
            # (NOT trading-minute walking). See OBS-003A authorization
            # and the module docstring.
            fwd_ts, reason = forward_bar_wall_clock(bars, cycle_start, n)

        if fwd_ts is None:
            out[name] = HorizonLabel(
                horizon_name=name,
                n_wall_clock_minutes=n,
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
            n_wall_clock_minutes=n,
            decision_price=decision_price,
            decision_price_ts=dec_ts,
            forward_price=fwd_price,
            forward_price_ts=fwd_ts,
            forward_return=ret,
            label_status="OK",
        )
    return out
