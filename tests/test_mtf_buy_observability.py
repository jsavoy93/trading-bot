"""MTF BUY OBSERVABILITY — focused tests.

Three layers of coverage:

  1. Pure-helper tests for ``compute_mtf_outcome`` (no SmartBot dependency).
     Covers the full daily × hourly × hourly-data-available ternary and
     the input-normalization contract.

  2. Snapshot-builder integration tests. Drive ``_build_decision_snapshot``
     with synthetic analysis results and assert the ``multi_timeframe`` block
     is populated for MTF paths and absent/null for non-MTF paths.

  3. Regression tests confirming:
     - analyze_symbol (single-timeframe) does NOT emit the multi_timeframe block
     - MTF scratch fields are cleared at every analyze_* entry
     - existing signal/signal_strength semantics are unchanged
     - existing decision_snapshot shape is preserved (additive only)

These tests are deterministic and do NOT contact a live brokerage.
"""

from __future__ import annotations

import json
from typing import Dict, Optional

import pytest

from src.core.mtf_outcome import (
    ALL_MTF_OUTCOMES,
    MTF_OUTCOME_AGREE_BUY,
    MTF_OUTCOME_AGREE_SELL,
    MTF_OUTCOME_CONFLICT,
    MTF_OUTCOME_DAILY_ONLY_BUY,
    MTF_OUTCOME_DAILY_ONLY_SELL,
    MTF_OUTCOME_HOURLY_ONLY_BUY,
    MTF_OUTCOME_HOURLY_ONLY_SELL,
    MTF_OUTCOME_NO_ACTION,
    MTF_REASON_DAILY_BUY_HOURLY_BUY_AGREEMENT,
    MTF_REASON_DAILY_BUY_HOURLY_HOLD_NO_ACTION,
    MTF_REASON_DAILY_BUY_HOURLY_SELL_DISAGREEMENT,
    MTF_REASON_DAILY_BUY_NO_HOURLY_DATA_DAILY_ONLY,
    MTF_REASON_DAILY_HOLD_HOURLY_BUY_NO_ACTION,
    MTF_REASON_DAILY_HOLD_HOURLY_HOLD_NO_ACTION,
    MTF_REASON_DAILY_HOLD_HOURLY_SELL_NO_ACTION,
    MTF_REASON_DAILY_HOLD_NO_HOURLY_DATA_NO_ACTION,
    MTF_REASON_DAILY_SELL_HOURLY_HOLD_NO_ACTION,
    MTF_REASON_DAILY_SELL_HOURLY_SELL_AGREEMENT,
    MTF_REASON_DAILY_SELL_HOURLY_BUY_DISAGREEMENT,
    MTF_REASON_DAILY_SELL_NO_HOURLY_DATA_DAILY_ONLY,
    compute_mtf_outcome,
)


# =============================================================================
# Layer 1 — Pure-helper coverage (deterministic, no SmartBot dependency)
# =============================================================================


class TestMtfOutcomeTokens:
    """The helper exposes a small stable outcome set."""

    def test_outcome_set_is_exactly_eight(self):
        # If you ever add/remove an outcome, update this test and the
        # documentation in src/core/mtf_outcome.py simultaneously.
        assert len(ALL_MTF_OUTCOMES) == 8

    def test_outcome_set_contents(self):
        assert ALL_MTF_OUTCOMES == frozenset({
            MTF_OUTCOME_AGREE_BUY,
            MTF_OUTCOME_AGREE_SELL,
            MTF_OUTCOME_CONFLICT,
            MTF_OUTCOME_DAILY_ONLY_BUY,
            MTF_OUTCOME_DAILY_ONLY_SELL,
            MTF_OUTCOME_HOURLY_ONLY_BUY,
            MTF_OUTCOME_HOURLY_ONLY_SELL,
            MTF_OUTCOME_NO_ACTION,
        })


class TestMtfOutcomeAgreement:
    """AGREE_* outcomes (both daily and hourly agree on direction)."""

    @pytest.mark.parametrize("signal", ["BUY", "SELL"])
    def test_daily_and_hourly_agree_buy_or_sell(self, signal):
        r = compute_mtf_outcome(signal, signal, hourly_data_available=True)
        expected_outcome = MTF_OUTCOME_AGREE_BUY if signal == "BUY" else MTF_OUTCOME_AGREE_SELL
        expected_reason = (
            MTF_REASON_DAILY_BUY_HOURLY_BUY_AGREEMENT
            if signal == "BUY"
            else MTF_REASON_DAILY_SELL_HOURLY_SELL_AGREEMENT
        )
        assert r["mtf_outcome"] == expected_outcome
        assert r["mtf_reason"] == expected_reason
        assert r["daily_signal_normalized"] == signal
        assert r["hourly_signal_normalized"] == signal
        assert r["hourly_data_available"] is True


class TestMtfOutcomeConflict:
    """CONFLICT outcome (explicit directional disagreement)."""

    def test_daily_buy_hourly_sell_conflict(self):
        r = compute_mtf_outcome("BUY", "SELL", hourly_data_available=True)
        assert r["mtf_outcome"] == MTF_OUTCOME_CONFLICT
        assert r["mtf_reason"] == MTF_REASON_DAILY_BUY_HOURLY_SELL_DISAGREEMENT

    def test_daily_sell_hourly_buy_conflict(self):
        r = compute_mtf_outcome("SELL", "BUY", hourly_data_available=True)
        assert r["mtf_outcome"] == MTF_OUTCOME_CONFLICT
        assert r["mtf_reason"] == MTF_REASON_DAILY_SELL_HOURLY_BUY_DISAGREEMENT


class TestMtfOutcomeDailyOnly:
    """DAILY_ONLY_* only fires when hourly data is unavailable.

    This mirrors the ``elif daily_signal and not hourly_indicators:``
    branch in SmartBot.analyze_multi_timeframe — the ONLY branch that
    emits a BUY/SELL with the DAILY_ONLY strength label.

    If hourly indicators are present but the hourly signal evaluated to
    HOLD (because neither BUY nor SELL condition triggered), the actual
    code falls through to ``else: signal = "HOLD"`` — NOT DAILY_ONLY.
    Those cases are covered by ``TestMtfOutcomeNoActionDailyDirectional``
    below.
    """

    @pytest.mark.parametrize("signal", ["BUY", "SELL"])
    def test_daily_directional_no_hourly_data(self, signal):
        r = compute_mtf_outcome(signal, "HOLD", hourly_data_available=False)
        expected_outcome = (
            MTF_OUTCOME_DAILY_ONLY_BUY if signal == "BUY" else MTF_OUTCOME_DAILY_ONLY_SELL
        )
        expected_reason = (
            MTF_REASON_DAILY_BUY_NO_HOURLY_DATA_DAILY_ONLY
            if signal == "BUY"
            else MTF_REASON_DAILY_SELL_NO_HOURLY_DATA_DAILY_ONLY
        )
        assert r["mtf_outcome"] == expected_outcome
        assert r["mtf_reason"] == expected_reason

    def test_daily_directional_with_hourly_data_is_not_daily_only(self):
        # Explicit guard: hourly data present → NEVER DAILY_ONLY,
        # regardless of hourly_signal value.
        for signal in ("BUY", "SELL"):
            r = compute_mtf_outcome(signal, "HOLD", hourly_data_available=True)
            assert r["mtf_outcome"] != MTF_OUTCOME_DAILY_ONLY_BUY
            assert r["mtf_outcome"] != MTF_OUTCOME_DAILY_ONLY_SELL


class TestMtfOutcomeNoActionDailyDirectional:
    """Daily directional + hourly HOLD (with data) → NO_ACTION (HOLD/WEAK).

    In SmartBot.analyze_multi_timeframe, when daily has a BUY/SELL signal
    but the hourly signal evaluated to HOLD (hourly indicators present,
    but neither BUY nor SELL condition triggered), control reaches the
    ``else: signal = "HOLD"`` branch — NOT the ``elif daily_signal and
    not hourly_indicators:`` branch. The final signal is HOLD/WEAK and
    the helper classifies this as NO_ACTION with a distinct reason
    so the dashboard can tell ``daily BUY / hourly HOLD (with data)``
    apart from ``daily BUY / no hourly data at all``.
    """

    @pytest.mark.parametrize("signal", ["BUY", "SELL"])
    def test_daily_directional_hourly_hold_with_data(self, signal):
        r = compute_mtf_outcome(signal, "HOLD", hourly_data_available=True)
        assert r["mtf_outcome"] == MTF_OUTCOME_NO_ACTION
        expected_reason = (
            MTF_REASON_DAILY_BUY_HOURLY_HOLD_NO_ACTION
            if signal == "BUY"
            else MTF_REASON_DAILY_SELL_HOURLY_HOLD_NO_ACTION
        )
        assert r["mtf_reason"] == expected_reason
        assert r["daily_signal_normalized"] == signal
        assert r["hourly_signal_normalized"] == "HOLD"
        assert r["hourly_data_available"] is True


class TestMtfOutcomeHourlyOnly:
    """HOURLY_ONLY_* (hourly directional but daily neutral → no agreement)."""

    @pytest.mark.parametrize("signal", ["BUY", "SELL"])
    def test_daily_hold_hourly_directional(self, signal):
        r = compute_mtf_outcome("HOLD", signal, hourly_data_available=True)
        expected_outcome = (
            MTF_OUTCOME_HOURLY_ONLY_BUY if signal == "BUY" else MTF_OUTCOME_HOURLY_ONLY_SELL
        )
        expected_reason = (
            MTF_REASON_DAILY_HOLD_HOURLY_BUY_NO_ACTION
            if signal == "BUY"
            else MTF_REASON_DAILY_HOLD_HOURLY_SELL_NO_ACTION
        )
        assert r["mtf_outcome"] == expected_outcome
        assert r["mtf_reason"] == expected_reason


class TestMtfOutcomeNoAction:
    """NO_ACTION (both neutral, or daily neutral with no hourly data)."""

    def test_daily_hold_hourly_hold_with_data(self):
        r = compute_mtf_outcome("HOLD", "HOLD", hourly_data_available=True)
        assert r["mtf_outcome"] == MTF_OUTCOME_NO_ACTION
        assert r["mtf_reason"] == MTF_REASON_DAILY_HOLD_HOURLY_HOLD_NO_ACTION

    def test_daily_hold_no_hourly_data(self):
        r = compute_mtf_outcome("HOLD", "HOLD", hourly_data_available=False)
        assert r["mtf_outcome"] == MTF_OUTCOME_NO_ACTION
        assert r["mtf_reason"] == MTF_REASON_DAILY_HOLD_NO_HOURLY_DATA_NO_ACTION


class TestMtfOutcomeInputNormalization:
    """None / lowercase / garbage values are normalized to HOLD or canonical."""

    @pytest.mark.parametrize("value", [None, "", "garbage", 42, []])
    def test_daily_signal_invalid_normalizes_to_hold(self, value):
        r = compute_mtf_outcome(value, "BUY", hourly_data_available=True)
        assert r["daily_signal_normalized"] == "HOLD"
        assert r["mtf_outcome"] == MTF_OUTCOME_HOURLY_ONLY_BUY

    @pytest.mark.parametrize("value", ["buy", "Buy", "BUY", "  BUY  "])
    def test_daily_signal_case_and_whitespace_normalized_to_canonical(self, value):
        r = compute_mtf_outcome(value, "BUY", hourly_data_available=True)
        assert r["daily_signal_normalized"] == "BUY"
        assert r["mtf_outcome"] == MTF_OUTCOME_AGREE_BUY

    @pytest.mark.parametrize("value", [None, "", "garbage"])
    def test_hourly_signal_invalid_normalizes_to_hold(self, value):
        # hourly_data_available=True and daily=BUY, so this is the
        # ``daily BUY + hourly HOLD (with data)`` branch → NO_ACTION.
        # Invalid hourly values normalize to HOLD, and the combination
        # mirrors the actual SmartBot ``else: signal = "HOLD"`` branch.
        r = compute_mtf_outcome("BUY", value, hourly_data_available=True)
        assert r["hourly_signal_normalized"] == "HOLD"
        assert r["mtf_outcome"] == MTF_OUTCOME_NO_ACTION
        assert r["mtf_reason"] == MTF_REASON_DAILY_BUY_HOURLY_HOLD_NO_ACTION

    @pytest.mark.parametrize("value", [None, "", "garbage"])
    def test_hourly_signal_invalid_no_hourly_data_normalizes_to_daily_only(self, value):
        # hourly_data_available=False and daily=BUY → DAILY_ONLY_BUY.
        # Invalid hourly values normalize to HOLD; with no hourly data
        # the actual SmartBot branch is ``elif daily_signal and not
        # hourly_indicators:`` → BUY/DAILY_ONLY.
        r = compute_mtf_outcome("BUY", value, hourly_data_available=False)
        assert r["hourly_signal_normalized"] == "HOLD"
        assert r["mtf_outcome"] == MTF_OUTCOME_DAILY_ONLY_BUY
        assert r["mtf_reason"] == MTF_REASON_DAILY_BUY_NO_HOURLY_DATA_DAILY_ONLY


class TestMtfOutcomeHourlyDataFlag:
    """hourly_data_available is passed through and influences the reason token."""

    def test_hourly_data_flag_true(self):
        r = compute_mtf_outcome("HOLD", "HOLD", hourly_data_available=True)
        assert r["hourly_data_available"] is True

    def test_hourly_data_flag_false(self):
        r = compute_mtf_outcome("HOLD", "HOLD", hourly_data_available=False)
        assert r["hourly_data_available"] is False


class TestMtfOutcomeReturnShape:
    """The return shape is contractually stable for the snapshot builder."""

    def test_return_keys(self):
        r = compute_mtf_outcome("BUY", "BUY", True)
        assert set(r.keys()) == {
            "mtf_outcome",
            "mtf_reason",
            "daily_signal_normalized",
            "hourly_signal_normalized",
            "hourly_data_available",
        }

    def test_values_are_json_serializable(self):
        r = compute_mtf_outcome("BUY", "SELL", True)
        # No tuples, no custom objects — must round-trip through JSON.
        json.dumps(r)


# =============================================================================
# Layer 2 — Snapshot-builder integration
# =============================================================================


def _bot():
    """Minimal SmartBot stub with the snapshot builder bound.

    We deliberately avoid constructing a full SmartTradingBot because
    its __init__ touches network/IO. The snapshot builder only needs
    the OBS_001 schema constants and ``_last_*`` scratch defaults.
    """
    from src.core.smart_bot import OBS_001_SCHEMA_VERSION
    from types import SimpleNamespace
    bot = SimpleNamespace()
    bot.session_id = "test-session"
    bot.bot_version = "test-bot"
    bot.rsi_buy_threshold = 30.0
    bot.rsi_sell_threshold = 70.0
    bot.sma_fast = 5
    bot.sma_slow = 20
    bot.enable_volume_confirmation = False
    bot._last_score_error_reason = None
    bot._last_mtf_outcome = None
    bot._last_mtf_reason = None
    bot._last_mtf_daily_signal = None
    bot._last_mtf_hourly_signal = None
    bot._last_mtf_hourly_data_available = False
    # Bind the real builder onto the stub.
    from src.core.smart_bot import SmartTradingBot
    bot._build_decision_snapshot = SmartTradingBot._build_decision_snapshot.__get__(bot)
    bot._schema_version = OBS_001_SCHEMA_VERSION
    return bot


class TestSnapshotMtfBlockPresentForMtfPath:
    """Multi-timeframe analyses get the multi_timeframe block."""

    def test_agree_buy_persists_full_block(self):
        bot = _bot()
        analysis = {
            "symbol": "AAPL",
            "signal": "BUY",
            "signal_strength": "STRONG",
            "rsi": 22.0,
            "sma_fast": 102.0,
            "sma_slow": 100.0,
            "macd_histogram": 0.5,
            "price": 100.0,
            "rsi_score": 22.0,
            "sma_score": 5.0,
            "macd_score": 6.0,
            "bb_score": 3.0,
            "total_score": 86.0,
            "score_invalid_data": False,
            "multi_timeframe": True,
            "daily_signal": "BUY",
            "hourly_signal": "BUY",
            "mtf_outcome": MTF_OUTCOME_AGREE_BUY,
            "mtf_reason": MTF_REASON_DAILY_BUY_HOURLY_BUY_AGREEMENT,
            "mtf_hourly_data_available": True,
        }
        snap = bot._build_decision_snapshot(symbol="AAPL", analysis=analysis)
        mtf = snap["multi_timeframe"]
        assert mtf is not None
        assert mtf["daily_signal"] == "BUY"
        assert mtf["hourly_signal"] == "BUY"
        assert mtf["hourly_data_available"] is True
        assert mtf["mtf_outcome"] == MTF_OUTCOME_AGREE_BUY
        assert mtf["mtf_reason"] == MTF_REASON_DAILY_BUY_HOURLY_BUY_AGREEMENT

    def test_conflict_persists_full_block(self):
        bot = _bot()
        analysis = {
            "symbol": "XOM",
            "signal": "HOLD",
            "signal_strength": "CONFLICTED",
            "rsi": 50.0,
            "sma_fast": 100.0,
            "sma_slow": 100.0,
            "macd_histogram": 0.0,
            "price": 50.0,
            "rsi_score": 0.0,
            "sma_score": 0.0,
            "macd_score": 0.0,
            "bb_score": 0.0,
            "total_score": 50.0,
            "score_invalid_data": False,
            "multi_timeframe": True,
            "daily_signal": "BUY",
            "hourly_signal": "SELL",
            "mtf_outcome": MTF_OUTCOME_CONFLICT,
            "mtf_reason": MTF_REASON_DAILY_BUY_HOURLY_SELL_DISAGREEMENT,
            "mtf_hourly_data_available": True,
        }
        snap = bot._build_decision_snapshot(symbol="XOM", analysis=analysis)
        mtf = snap["multi_timeframe"]
        assert mtf is not None
        assert mtf["mtf_outcome"] == MTF_OUTCOME_CONFLICT
        assert mtf["mtf_reason"] == MTF_REASON_DAILY_BUY_HOURLY_SELL_DISAGREEMENT
        # The final signal is HOLD/CONFLICTED — the mtf fields explain WHY.
        assert snap["strategy_eligibility"]["signal"] == "HOLD"
        assert snap["strategy_eligibility"]["signal_strength"] == "CONFLICTED"

    def test_no_action_persists_full_block(self):
        bot = _bot()
        analysis = {
            "symbol": "MSFT",
            "signal": "HOLD",
            "signal_strength": "WEAK",
            "rsi": 50.0,
            "sma_fast": 100.0,
            "sma_slow": 100.0,
            "macd_histogram": 0.0,
            "price": 50.0,
            "rsi_score": 0.0,
            "sma_score": 0.0,
            "macd_score": 0.0,
            "bb_score": 0.0,
            "total_score": 50.0,
            "score_invalid_data": False,
            "multi_timeframe": True,
            "daily_signal": "HOLD",
            "hourly_signal": "HOLD",
            "mtf_outcome": MTF_OUTCOME_NO_ACTION,
            "mtf_reason": MTF_REASON_DAILY_HOLD_HOURLY_HOLD_NO_ACTION,
            "mtf_hourly_data_available": True,
        }
        snap = bot._build_decision_snapshot(symbol="MSFT", analysis=analysis)
        mtf = snap["multi_timeframe"]
        assert mtf is not None
        assert mtf["mtf_outcome"] == MTF_OUTCOME_NO_ACTION
        assert mtf["mtf_reason"] == MTF_REASON_DAILY_HOLD_HOURLY_HOLD_NO_ACTION

    def test_daily_only_no_hourly_data_persists_distinct_reason(self):
        bot = _bot()
        analysis = {
            "symbol": "TSLA",
            "signal": "BUY",
            "signal_strength": "DAILY_ONLY",
            "rsi": 25.0,
            "sma_fast": 102.0,
            "sma_slow": 100.0,
            "macd_histogram": 0.5,
            "price": 200.0,
            "rsi_score": 17.0,
            "sma_score": 5.0,
            "macd_score": 6.0,
            "bb_score": 3.0,
            "total_score": 81.0,
            "score_invalid_data": False,
            "multi_timeframe": True,
            "daily_signal": "BUY",
            "hourly_signal": "HOLD",
            "mtf_outcome": MTF_OUTCOME_DAILY_ONLY_BUY,
            "mtf_reason": MTF_REASON_DAILY_BUY_NO_HOURLY_DATA_DAILY_ONLY,
            "mtf_hourly_data_available": False,
        }
        snap = bot._build_decision_snapshot(symbol="TSLA", analysis=analysis)
        mtf = snap["multi_timeframe"]
        assert mtf is not None
        assert mtf["hourly_data_available"] is False
        assert mtf["mtf_reason"] == MTF_REASON_DAILY_BUY_NO_HOURLY_DATA_DAILY_ONLY


class TestSnapshotMtfBlockAbsentForSingleTimeframe:
    """Single-timeframe analyses do NOT get a multi_timeframe block."""

    def test_single_timeframe_block_is_none(self):
        bot = _bot()
        analysis = {
            "symbol": "NVDA",
            "signal": "BUY",
            "signal_strength": "STRONG",
            "rsi": 22.0,
            "sma_fast": 102.0,
            "sma_slow": 100.0,
            "macd_histogram": 0.5,
            "price": 500.0,
            "rsi_score": 22.0,
            "sma_score": 5.0,
            "macd_score": 6.0,
            "bb_score": 3.0,
            "total_score": 86.0,
            "score_invalid_data": False,
            # multi_timeframe is absent — single-timeframe path.
        }
        snap = bot._build_decision_snapshot(symbol="NVDA", analysis=analysis)
        assert snap["multi_timeframe"] is None

    def test_single_timeframe_block_is_none_even_with_stale_scratch(self):
        # Even if scratch fields are populated from a prior MTF call,
        # the single-timeframe path MUST NOT leak them into the snapshot.
        bot = _bot()
        bot._last_mtf_outcome = MTF_OUTCOME_AGREE_BUY
        bot._last_mtf_reason = MTF_REASON_DAILY_BUY_HOURLY_BUY_AGREEMENT
        bot._last_mtf_daily_signal = "BUY"
        bot._last_mtf_hourly_signal = "BUY"
        bot._last_mtf_hourly_data_available = True
        analysis = {
            "symbol": "NVDA",
            "signal": "HOLD",
            "signal_strength": "WEAK",
            "rsi": 50.0,
            "sma_fast": 100.0,
            "sma_slow": 100.0,
            "price": 500.0,
            "rsi_score": 0.0,
            "sma_score": 0.0,
            "macd_score": 0.0,
            "bb_score": 0.0,
            "total_score": 50.0,
            "score_invalid_data": False,
            # multi_timeframe is absent — single-timeframe path.
        }
        snap = bot._build_decision_snapshot(symbol="NVDA", analysis=analysis)
        assert snap["multi_timeframe"] is None


class TestSnapshotExistingShapePreserved:
    """Existing snapshot keys remain unchanged (additive only)."""

    def test_existing_top_level_keys_present(self):
        bot = _bot()
        analysis = {
            "symbol": "IBM",
            "signal": "HOLD",
            "signal_strength": "WEAK",
            "rsi": 50.0,
            "sma_fast": 100.0,
            "sma_slow": 100.0,
            "price": 100.0,
            "total_score": 50.0,
            "score_invalid_data": False,
        }
        snap = bot._build_decision_snapshot(symbol="IBM", analysis=analysis)
        for k in (
            "schema_version",
            "schema_version_notes",
            "symbol",
            "analysis_timestamp",
            "session_id",
            "bot_version",
            "timeframe_mode",
            "cycle_id",
            "strategy_eligibility",
            "scoring",
            "ranking",
            "selection",
            "execution_checks",
            "order",
            "decision",
            "baseline_diagnostics",
            "multi_timeframe",  # NEW
        ):
            assert k in snap, f"missing required key: {k}"


# =============================================================================
# Layer 3 — Regression coverage
# =============================================================================


class TestScratchStateClearedOnEntry:
    """MTF scratch fields are cleared at every analyze_* entry.

    This prevents cross-symbol leakage of daily_signal / hourly_signal /
    mtf_outcome between cycles.
    """

    def test_analyze_symbol_clears_scratch_fields(self, monkeypatch):
        from src.core.smart_bot import SmartTradingBot
        # We don't construct a full bot; instead we directly verify the
        # function body calls the clear-via-assignment pattern by
        # inspecting the source. This is a structural guard against
        # future refactors that drop the clearing.
        import inspect
        src = inspect.getsource(SmartTradingBot.analyze_symbol)
        assert "_last_mtf_outcome = None" in src
        assert "_last_mtf_reason = None" in src
        assert "_last_mtf_daily_signal = None" in src
        assert "_last_mtf_hourly_signal = None" in src
        assert "_last_mtf_hourly_data_available = False" in src

    def test_analyze_multi_timeframe_clears_scratch_fields(self):
        from src.core.smart_bot import SmartTradingBot
        import inspect
        src = inspect.getsource(SmartTradingBot.analyze_multi_timeframe)
        assert "_last_mtf_outcome = None" in src
        assert "_last_mtf_reason = None" in src
        assert "_last_mtf_daily_signal = None" in src
        assert "_last_mtf_hourly_signal = None" in src
        assert "_last_mtf_hourly_data_available = False" in src


class TestMtfHelperImportable:
    """The helper module is importable and stable."""

    def test_helper_importable(self):
        from src.core.mtf_outcome import compute_mtf_outcome
        assert callable(compute_mtf_outcome)

    def test_helper_used_by_analyzer(self):
        # The analyzer MUST call compute_mtf_outcome. If a refactor
        # inlines it, this guard fails so the reviewer can decide.
        from src.core.smart_bot import SmartTradingBot
        import inspect
        src = inspect.getsource(SmartTradingBot.analyze_multi_timeframe)
        assert "from core.mtf_outcome import compute_mtf_outcome" in src
        assert "compute_mtf_outcome(" in src