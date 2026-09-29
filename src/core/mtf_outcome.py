"""MTF BUY OBSERVABILITY — normalized MTF outcome + reason.

A pure helper layer that classifies the multi-timeframe (daily + hourly)
signal combination into a small, stable set of OUTCOME tokens and
persists the SPECIFIC BRANCH the code actually executed via a REASON
token. This is OBSERVABILITY ONLY.

The helper exists because historical ``decision_history`` rows for the
valid RSI+SMA joint-pass cohort preserved only the FINAL
``signal`` / ``signal_strength`` (HOLD + WEAK or HOLD + CONFLICTED) but
not:

  - the daily signal component that the MTF combiner consumed
  - the hourly signal component that the MTF combiner consumed
  - whether hourly data was available at all
  - the SPECIFIC branch the combiner chose

For ``~510`` historical rows classified ``HOLD / WEAK`` we could not
distinguish between:

  - daily HOLD + hourly HOLD
  - daily HOLD + hourly BUY (single-side insufficient)
  - daily BUY + hourly HOLD (single-side, daily only)
  - daily BUY + hourly BUY volume-downgraded to HOLD

This module is therefore the SINGLE SOURCE OF TRUTH for the MTF
combination semantics. The SmartBot analyzer MUST call
``compute_mtf_outcome`` once per multi-timeframe analysis and persist
the returned ``mtf_outcome`` / ``mtf_reason`` into the
``decision_snapshot.multi_timeframe`` block.

Contract:

  Inputs:
    daily_signal           in {"BUY", "SELL", "HOLD", None}
    hourly_signal          in {"BUY", "SELL", "HOLD", None}
    hourly_data_available  bool  (False means no hourly bars returned)

  Outputs (always populated, never None):
    mtf_outcome            str (one of MTF_OUTCOME_* tokens below)
    mtf_reason             str (one of MTF_REASON_* tokens below)
    daily_signal_normalized    str (always one of "BUY"|"SELL"|"HOLD")
    hourly_signal_normalized   str (always one of "BUY"|"SELL"|"HOLD")
    hourly_data_available  bool  (passed through)

Token stability:
  Both ``mtf_outcome`` and ``mtf_reason`` are STABLE machine-readable
  strings. Dashboards group by them. NEVER rename without a
  compatibility shim and a migration plan.

OUTCOME tokens (small stable set):

  AGREE_BUY            daily BUY,  hourly BUY   → final signal BUY
  AGREE_SELL           daily SELL, hourly SELL  → final signal SELL
  CONFLICT             daily X,    hourly !X    → final signal HOLD/CONFLICTED
  DAILY_ONLY_BUY       daily BUY,  hourly HOLD  → final signal BUY (DAILY_ONLY)
  DAILY_ONLY_SELL      daily SELL, hourly HOLD  → final signal SELL (DAILY_ONLY)
  HOURLY_ONLY_BUY      daily HOLD, hourly BUY   → final signal HOLD (single-side)
  HOURLY_ONLY_SELL     daily HOLD, hourly SELL  → final signal HOLD (single-side)
  NO_ACTION            daily HOLD, hourly HOLD or no hourly data
                                              → final signal HOLD (WEAK)

REASON tokens (specific to the executed branch):

  daily_buy_hourly_buy_agreement
  daily_sell_hourly_sell_agreement
  daily_buy_hourly_sell_disagreement
  daily_sell_hourly_buy_disagreement
  daily_buy_hourly_hold_daily_only
  daily_sell_hourly_hold_daily_only
  daily_buy_no_hourly_data_daily_only
  daily_sell_no_hourly_data_daily_only
  daily_hold_hourly_buy_no_action
  daily_hold_hourly_sell_no_action
  daily_hold_hourly_hold_no_action
  daily_hold_no_hourly_data_no_action

Design notes:

  - The outcome set is deliberately small (8 tokens). The reason set
    is deliberately specific (12 tokens). They are MECE across the
    daily/hourly/data-availability ternary.
  - Both daily and hourly inputs are normalized to {"BUY", "SELL",
    "HOLD"}. None or unrecognized values become "HOLD".
  - This module has NO I/O, NO database, NO global state, NO logging.
    It is a pure function suitable for unit tests.
"""

from __future__ import annotations

from typing import Dict, Optional


# ----- Stable outcome tokens --------------------------------------------------
MTF_OUTCOME_AGREE_BUY = "AGREE_BUY"
MTF_OUTCOME_AGREE_SELL = "AGREE_SELL"
MTF_OUTCOME_CONFLICT = "CONFLICT"
MTF_OUTCOME_DAILY_ONLY_BUY = "DAILY_ONLY_BUY"
MTF_OUTCOME_DAILY_ONLY_SELL = "DAILY_ONLY_SELL"
MTF_OUTCOME_HOURLY_ONLY_BUY = "HOURLY_ONLY_BUY"
MTF_OUTCOME_HOURLY_ONLY_SELL = "HOURLY_ONLY_SELL"
MTF_OUTCOME_NO_ACTION = "NO_ACTION"

ALL_MTF_OUTCOMES = frozenset({
    MTF_OUTCOME_AGREE_BUY,
    MTF_OUTCOME_AGREE_SELL,
    MTF_OUTCOME_CONFLICT,
    MTF_OUTCOME_DAILY_ONLY_BUY,
    MTF_OUTCOME_DAILY_ONLY_SELL,
    MTF_OUTCOME_HOURLY_ONLY_BUY,
    MTF_OUTCOME_HOURLY_ONLY_SELL,
    MTF_OUTCOME_NO_ACTION,
})


# ----- Stable reason tokens --------------------------------------------------
MTF_REASON_DAILY_BUY_HOURLY_BUY_AGREEMENT = "daily_buy_hourly_buy_agreement"
MTF_REASON_DAILY_SELL_HOURLY_SELL_AGREEMENT = "daily_sell_hourly_sell_agreement"
MTF_REASON_DAILY_BUY_HOURLY_SELL_DISAGREEMENT = "daily_buy_hourly_sell_disagreement"
MTF_REASON_DAILY_SELL_HOURLY_BUY_DISAGREEMENT = "daily_sell_hourly_buy_disagreement"
MTF_REASON_DAILY_BUY_HOURLY_HOLD_DAILY_ONLY = "daily_buy_hourly_hold_daily_only"
MTF_REASON_DAILY_SELL_HOURLY_HOLD_DAILY_ONLY = "daily_sell_hourly_hold_daily_only"
MTF_REASON_DAILY_BUY_NO_HOURLY_DATA_DAILY_ONLY = "daily_buy_no_hourly_data_daily_only"
MTF_REASON_DAILY_SELL_NO_HOURLY_DATA_DAILY_ONLY = "daily_sell_no_hourly_data_daily_only"
MTF_REASON_DAILY_HOLD_HOURLY_BUY_NO_ACTION = "daily_hold_hourly_buy_no_action"
MTF_REASON_DAILY_HOLD_HOURLY_SELL_NO_ACTION = "daily_hold_hourly_sell_no_action"
MTF_REASON_DAILY_HOLD_HOURLY_HOLD_NO_ACTION = "daily_hold_hourly_hold_no_action"
MTF_REASON_DAILY_HOLD_NO_HOURLY_DATA_NO_ACTION = "daily_hold_no_hourly_data_no_action"


# ----- Valid signal values ---------------------------------------------------
_VALID_SIGNALS = ("BUY", "SELL", "HOLD")


def _normalize_signal(value: Optional[str]) -> str:
    """Normalize a daily/hourly signal value to {"BUY","SELL","HOLD"}.

    Anything other than those three literal strings (including None,
    empty string, lowercase, "buy", etc.) becomes "HOLD".
    """
    if value is None:
        return "HOLD"
    if not isinstance(value, str):
        return "HOLD"
    upper = value.strip().upper()
    if upper in _VALID_SIGNALS:
        return upper
    return "HOLD"


def compute_mtf_outcome(
    daily_signal: Optional[str],
    hourly_signal: Optional[str],
    hourly_data_available: bool,
) -> Dict[str, object]:
    """Classify a daily/hourly signal pair into MTF outcome + reason.

    Pure function. No I/O, no database, no logging, no global state.

    Args:
        daily_signal:           the daily signal component (None or
                                unrecognized → "HOLD").
        hourly_signal:          the hourly signal component (None or
                                unrecognized → "HOLD").
        hourly_data_available:  True iff at least one hourly bar was
                                returned by the data provider. False
                                distinguishes "no hourly data" from
                                "hourly computed but neutral".

    Returns:
        Dict with keys:
          mtf_outcome              str (one of MTF_OUTCOME_*)
          mtf_reason               str (one of MTF_REASON_*)
          daily_signal_normalized  str ("BUY" | "SELL" | "HOLD")
          hourly_signal_normalized str ("BUY" | "SELL" | "HOLD")
          hourly_data_available     bool  (passed through)
    """
    d = _normalize_signal(daily_signal)
    h = _normalize_signal(hourly_signal)
    avail = bool(hourly_data_available)

    # AGREE_BUY / AGREE_SELL  →  both sides agree on direction
    if d == "BUY" and h == "BUY":
        return {
            "mtf_outcome": MTF_OUTCOME_AGREE_BUY,
            "mtf_reason": MTF_REASON_DAILY_BUY_HOURLY_BUY_AGREEMENT,
            "daily_signal_normalized": d,
            "hourly_signal_normalized": h,
            "hourly_data_available": avail,
        }
    if d == "SELL" and h == "SELL":
        return {
            "mtf_outcome": MTF_OUTCOME_AGREE_SELL,
            "mtf_reason": MTF_REASON_DAILY_SELL_HOURLY_SELL_AGREEMENT,
            "daily_signal_normalized": d,
            "hourly_signal_normalized": h,
            "hourly_data_available": avail,
        }

    # CONFLICT  →  explicit directional disagreement
    if d == "BUY" and h == "SELL":
        return {
            "mtf_outcome": MTF_OUTCOME_CONFLICT,
            "mtf_reason": MTF_REASON_DAILY_BUY_HOURLY_SELL_DISAGREEMENT,
            "daily_signal_normalized": d,
            "hourly_signal_normalized": h,
            "hourly_data_available": avail,
        }
    if d == "SELL" and h == "BUY":
        return {
            "mtf_outcome": MTF_OUTCOME_CONFLICT,
            "mtf_reason": MTF_REASON_DAILY_SELL_HOURLY_BUY_DISAGREEMENT,
            "daily_signal_normalized": d,
            "hourly_signal_normalized": h,
            "hourly_data_available": avail,
        }

    # DAILY_ONLY_*  →  daily directional, hourly neutral or unavailable
    if d == "BUY" and (not avail or h == "HOLD"):
        reason = (
            MTF_REASON_DAILY_BUY_HOURLY_HOLD_DAILY_ONLY
            if avail
            else MTF_REASON_DAILY_BUY_NO_HOURLY_DATA_DAILY_ONLY
        )
        return {
            "mtf_outcome": MTF_OUTCOME_DAILY_ONLY_BUY,
            "mtf_reason": reason,
            "daily_signal_normalized": d,
            "hourly_signal_normalized": h,
            "hourly_data_available": avail,
        }
    if d == "SELL" and (not avail or h == "HOLD"):
        reason = (
            MTF_REASON_DAILY_SELL_HOURLY_HOLD_DAILY_ONLY
            if avail
            else MTF_REASON_DAILY_SELL_NO_HOURLY_DATA_DAILY_ONLY
        )
        return {
            "mtf_outcome": MTF_OUTCOME_DAILY_ONLY_SELL,
            "mtf_reason": reason,
            "daily_signal_normalized": d,
            "hourly_signal_normalized": h,
            "hourly_data_available": avail,
        }

    # HOURLY_ONLY_*  →  hourly directional but daily neutral (no actionable agreement)
    if d == "HOLD" and h == "BUY":
        return {
            "mtf_outcome": MTF_OUTCOME_HOURLY_ONLY_BUY,
            "mtf_reason": MTF_REASON_DAILY_HOLD_HOURLY_BUY_NO_ACTION,
            "daily_signal_normalized": d,
            "hourly_signal_normalized": h,
            "hourly_data_available": avail,
        }
    if d == "HOLD" and h == "SELL":
        return {
            "mtf_outcome": MTF_OUTCOME_HOURLY_ONLY_SELL,
            "mtf_reason": MTF_REASON_DAILY_HOLD_HOURLY_SELL_NO_ACTION,
            "daily_signal_normalized": d,
            "hourly_signal_normalized": h,
            "hourly_data_available": avail,
        }

    # NO_ACTION  →  both neutral, OR daily HOLD with no hourly data
    if d == "HOLD":
        reason = (
            MTF_REASON_DAILY_HOLD_HOURLY_HOLD_NO_ACTION
            if avail
            else MTF_REASON_DAILY_HOLD_NO_HOURLY_DATA_NO_ACTION
        )
        return {
            "mtf_outcome": MTF_OUTCOME_NO_ACTION,
            "mtf_reason": reason,
            "daily_signal_normalized": d,
            "hourly_signal_normalized": h,
            "hourly_data_available": avail,
        }

    # Defensive: unreachable if inputs are BUY/SELL/HOLD. If somehow we
    # land here (e.g. a future code path introduces a new daily value),
    # classify conservatively as NO_ACTION.
    return {
        "mtf_outcome": MTF_OUTCOME_NO_ACTION,
        "mtf_reason": MTF_REASON_DAILY_HOLD_HOURLY_HOLD_NO_ACTION,
        "daily_signal_normalized": d,
        "hourly_signal_normalized": h,
        "hourly_data_available": avail,
    }