"""Decision-time feature extraction from a v1 decision_snapshot.

This module parses a v1 decision_snapshot and pulls out the
decision-time facts that OBS-003 cares about. It is read-only: it
never writes anywhere.

Returned features are flat scalars so they can be CSV-serialized.
Missing values become ``None``.

The exact set of keys (so other modules can rely on them):

    symbol
    cycle_start          - UTC pd.Timestamp
    session_id
    bot_version
    timeframe_mode
    outcome              - decision.outcome
    signal               - strategy_eligibility.signal
    rsi_oversold_pass    - bool or None
    sma_uptrend_pass     - bool or None
    rsi_value            - float or None (rsi_oversold.observed_value)
    rsi_threshold        - float or None (rsi_oversold.threshold_value)
    sma_fast             - float or None (sma_uptrend.observed_value)
    sma_slow             - float or None (sma_uptrend.threshold_value)
    sma_spread           - sma_fast - sma_slow, or None
    rsi_distance_to_threshold   - rsi_value - rsi_threshold (negative
                                  means rsi is below threshold; for the
                                  fail side the magnitude matters)
    sma_distance_to_threshold   - sma_slow - sma_fast (positive means
                                  sma_fast is below sma_slow; magnitude
                                  is the fail margin)
    total_score          - scoring.total_score or None
    rsi_score
    sma_score
    macd_score
    bb_score
    blended_signed
    score_invalid_data   - bool
    strategy_eligible    - bool
    strategy_reason      - str
    ranking_applicable   - bool or None
    ranked_candidate     - bool or None
    candidate_rank       - int or None
    execution_blocked    - bool (true if any L2-L5 check failed)
    first_blocking_check - str or None
    other_gates_passed   - count of other strategy_gate gates that
                           passed (besides rsi/sma)
    other_gates_failed   - count of other strategy_gate gates that
                           failed (besides rsi/sma)
    other_gates_names_passed - tuple of names
    other_gates_names_failed - tuple of names
"""

from __future__ import annotations

from typing import Optional

import pandas as pd


def _g(snap: dict, dotted: str):
    """Get a value from a nested dict using dot-notation; None on miss."""
    cur = snap
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def extract_features(snapshot: dict, symbol: str, cycle_start_iso: str) -> dict:
    """Extract OBS-003-relevant features from a v1 decision_snapshot.

    Parameters
    ----------
    snapshot:
        The JSON-decoded ``decision_snapshot`` of a v1 row.
    symbol:
        The symbol (top-level column on the row).
    cycle_start_iso:
        The cycle_start as a UTC ISO timestamp string.

    Returns
    -------
    dict of feature_name -> value.
    """
    feats: dict = {"symbol": symbol, "cycle_start": pd.Timestamp(cycle_start_iso)}

    if not isinstance(snapshot, dict):
        # Defensive: if the snapshot is malformed, return minimal features.
        return _with_none(feats)

    # Populate default None values for everything we expect, so callers
    # can rely on the keys existing even when individual fields are missing.
    _with_none(feats)

    feats["session_id"] = snapshot.get("session_id")
    feats["bot_version"] = snapshot.get("bot_version")
    feats["timeframe_mode"] = snapshot.get("timeframe_mode")
    feats["outcome"] = _g(snapshot, "decision.outcome")
    feats["signal"] = _g(snapshot, "strategy_eligibility.signal")
    feats["strategy_eligible"] = _g(snapshot, "strategy_eligibility.strategy_eligible")
    feats["strategy_reason"] = _g(snapshot, "strategy_eligibility.strategy_reason")

    gates = _g(snapshot, "strategy_eligibility.gates") or []
    by_name = {}
    for g in gates:
        if not isinstance(g, dict):
            continue
        name = g.get("name")
        if not name:
            continue
        by_name[name] = g

    rsi = by_name.get("rsi_oversold")
    if rsi:
        feats["rsi_oversold_pass"] = bool(rsi.get("passed"))
        feats["rsi_value"] = _to_float(rsi.get("observed_value"))
        feats["rsi_threshold"] = _to_float(rsi.get("threshold_value"))
        if feats["rsi_value"] is not None and feats["rsi_threshold"] is not None:
            feats["rsi_distance_to_threshold"] = (
                feats["rsi_value"] - feats["rsi_threshold"]
            )
    else:
        feats["rsi_oversold_pass"] = None

    sma = by_name.get("sma_uptrend")
    if sma:
        feats["sma_uptrend_pass"] = bool(sma.get("passed"))
        feats["sma_fast"] = _to_float(sma.get("observed_value"))
        feats["sma_slow"] = _to_float(sma.get("threshold_value"))
        if feats["sma_fast"] is not None and feats["sma_slow"] is not None:
            feats["sma_spread"] = feats["sma_fast"] - feats["sma_slow"]
            # For SMA, "fail" means sma_fast <= sma_slow.
            # Distance to threshold (fail side) is sma_slow - sma_fast (>=0
            # means failing by that many dollars).
            feats["sma_distance_to_threshold"] = (
                feats["sma_slow"] - feats["sma_fast"]
            )
    else:
        feats["sma_uptrend_pass"] = None

    # Other (non-RSI/SMA) gates
    other_passed, other_failed = [], []
    for name, g in by_name.items():
        if name in ("rsi_oversold", "sma_uptrend"):
            continue
        if g.get("passed"):
            other_passed.append(name)
        else:
            other_failed.append(name)
    feats["other_gates_names_passed"] = tuple(other_passed)
    feats["other_gates_names_failed"] = tuple(other_failed)
    feats["other_gates_passed"] = len(other_passed)
    feats["other_gates_failed"] = len(other_failed)

    # Scoring
    feats["total_score"] = _to_float(_g(snapshot, "scoring.total_score"))
    feats["rsi_score"] = _to_float(_g(snapshot, "scoring.components.rsi_score"))
    feats["sma_score"] = _to_float(_g(snapshot, "scoring.components.sma_score"))
    feats["macd_score"] = _to_float(_g(snapshot, "scoring.components.macd_score"))
    feats["bb_score"] = _to_float(_g(snapshot, "scoring.components.bb_score"))
    feats["blended_signed"] = _to_float(_g(snapshot, "scoring.blended_signed"))
    feats["score_invalid_data"] = bool(_g(snapshot, "scoring.score_invalid_data"))

    # Ranking
    feats["ranking_applicable"] = _g(snapshot, "ranking.applicable")
    feats["ranked_candidate"] = _g(snapshot, "ranking.ranked_candidate")
    feats["candidate_rank"] = _g(snapshot, "ranking.candidate_rank")

    # Execution blocking
    checks = _g(snapshot, "execution_checks.checks") or []
    any_block = any((not c.get("passed", True)) and c.get("applied", True) for c in checks if isinstance(c, dict))
    feats["execution_blocked"] = any_block
    feats["first_blocking_check"] = _g(snapshot, "execution_checks.first_blocking_check")

    return feats


def _to_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _with_none(d: dict) -> dict:
    """Ensure all expected keys exist (as None) even on malformed input."""
    expected = [
        "session_id", "bot_version", "timeframe_mode", "outcome", "signal",
        "strategy_eligible", "strategy_reason",
        "rsi_oversold_pass", "sma_uptrend_pass",
        "rsi_value", "rsi_threshold", "rsi_distance_to_threshold",
        "sma_fast", "sma_slow", "sma_spread", "sma_distance_to_threshold",
        "total_score", "rsi_score", "sma_score", "macd_score", "bb_score",
        "blended_signed", "score_invalid_data",
        "ranking_applicable", "ranked_candidate", "candidate_rank",
        "execution_blocked", "first_blocking_check",
        "other_gates_names_passed", "other_gates_names_failed",
        "other_gates_passed", "other_gates_failed",
    ]
    for k in expected:
        d.setdefault(k, None)
    return d
