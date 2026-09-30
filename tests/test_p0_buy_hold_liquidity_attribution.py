"""P0 BUY->HOLD Liquidity Attribution — additive observability tests.

These tests verify the new signal_pipeline block + liquidity_filter_evaluation
on decision_snapshot, plus the extracted _apply_outer_liquidity_filter helper.
The helper preserves the pre-extraction mutation contract exactly:

    Pre:  signal=BUY, signal_strength=STRONG
    Post (FAILED): signal=HOLD, signal_strength=WEAK, liquidity_warning=reason
    Post (PASSED): signal=BUY, signal_strength=STRONG, no warning

Trading behavior is UNCHANGED. These tests are observability-only.
"""

from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _make_bot(*, enable_liquidity_filter: bool = True):
    """Construct a SmartTradingBot instance without service clients."""
    from src.core.smart_bot import SmartTradingBot

    bot = SmartTradingBot.__new__(SmartTradingBot)
    bot.rsi_buy_threshold = 30
    bot.rsi_sell_threshold = 70
    bot.enable_volume_confirmation = True
    bot.bot_version = "2.1.0-test"
    bot.session_id = None
    # Liquidity filter config (constructor-shaped)
    bot.enable_liquidity_filter = enable_liquidity_filter
    bot.min_daily_volume = 1_000_000
    bot.max_spread_pct = 0.3  # observed effective threshold = 0.3 * 3 = 0.9
    return bot


def _buy_analysis(signal_strength: str = "STRONG") -> dict:
    """A representative BUY-eligible analysis dict the helper will mutate."""
    return {
        "symbol": "TEST",
        "signal": "BUY",
        "signal_strength": signal_strength,
        "price": 100.0,
        "total_score": 75,
    }


# ---------------------------------------------------------------------------
# Test 1 — Low-volume rejection mutates signal to HOLD + emits snapshot block
# ---------------------------------------------------------------------------


class TestLowVolumeRejection:
    def test_low_volume_mutates_and_persists_pipeline_block(self):
        bot = _make_bot()
        # check_liquidity returns FAILED with low volume and modest spread
        bot.check_liquidity = MagicMock(
            return_value=(False, 500_000.0, 0.5, "Volume 0.5M < 1M minimum")
        )
        analysis = _buy_analysis(signal_strength="STRONG")

        bot._apply_outer_liquidity_filter("TEST", analysis)

        # In-memory mutation happened
        assert analysis["signal"] == "HOLD"
        assert analysis["signal_strength"] == "WEAK"
        assert analysis["liquidity_warning"] == "Volume 0.5M < 1M minimum"

        # No _p0_* keys should remain on analysis (snapshot will pop them)
        assert "_p0_liquidity_eval" in analysis  # still stashed pre-snapshot

        snap = bot._build_decision_snapshot("TEST", analysis)
        # After snapshot build, scratch keys are gone
        assert "_p0_liquidity_eval" not in analysis
        assert "_p0_pre_signal" not in analysis
        assert "_p0_pre_signal_strength" not in analysis

        # signal_pipeline block is present and correctly populated
        assert "signal_pipeline" in snap
        sp = snap["signal_pipeline"]
        assert sp["analyzer_signal"] == "BUY"
        assert sp["analyzer_signal_strength"] == "STRONG"
        assert sp["final_signal"] == "HOLD"
        assert sp["final_signal_strength"] == "WEAK"
        assert sp["downgraded"] is True
        assert sp["downgrade_stage"] == "liquidity_filter"
        assert sp["downgrade_reason"] == "Volume 0.5M < 1M minimum"

        # liquidity_filter_evaluation contains actual measurements + threshold
        ev = sp["liquidity_filter_evaluation"]
        assert ev["result"] == "FAILED"
        assert ev["enabled"] is True
        assert ev["applicable"] is True
        assert ev["evaluated"] is True
        assert ev["avg_volume"] == 500_000.0
        assert ev["min_daily_volume"] == 1_000_000.0
        assert ev["high_low_pct"] == 0.5
        assert ev["effective_max_high_low_pct"] == pytest.approx(0.9)  # 0.3 * 3
        assert ev["signal_before"] == "BUY"
        assert ev["signal_after"] == "HOLD"
        assert ev["reason_raw"] == "Volume 0.5M < 1M minimum"

        # Schema version is unchanged
        assert snap["schema_version"] == 1


# ---------------------------------------------------------------------------
# Test 2 — High-low pct rejection (spread too wide)
# ---------------------------------------------------------------------------


class TestHighLowPctRejection:
    def test_high_low_pct_rejection(self):
        bot = _make_bot()
        bot.check_liquidity = MagicMock(
            return_value=(False, 2_000_000.0, 1.5, "Spread 1.5% too wide")
        )
        analysis = _buy_analysis(signal_strength="STRONG")

        bot._apply_outer_liquidity_filter("TEST", analysis)

        assert analysis["signal"] == "HOLD"
        assert analysis["signal_strength"] == "WEAK"
        assert analysis["liquidity_warning"] == "Spread 1.5% too wide"

        snap = bot._build_decision_snapshot("TEST", analysis)
        sp = snap["signal_pipeline"]
        ev = sp["liquidity_filter_evaluation"]
        assert ev["result"] == "FAILED"
        assert ev["avg_volume"] == 2_000_000.0
        assert ev["high_low_pct"] == 1.5
        assert ev["min_daily_volume"] == 1_000_000.0
        assert ev["effective_max_high_low_pct"] == pytest.approx(0.9)
        assert sp["downgraded"] is True
        assert sp["downgrade_stage"] == "liquidity_filter"
        assert sp["downgrade_reason"] == "Spread 1.5% too wide"


# ---------------------------------------------------------------------------
# Test 3 — Liquidity passes — no mutation, eval block shows PASSED
# ---------------------------------------------------------------------------


class TestLiquidityPass:
    def test_passes_emits_passed_eval_and_no_mutation(self):
        bot = _make_bot()
        bot.check_liquidity = MagicMock(
            return_value=(True, 2_000_000.0, 0.5, "Passes liquidity check")
        )
        analysis = _buy_analysis(signal_strength="STRONG")

        bot._apply_outer_liquidity_filter("TEST", analysis)

        # No mutation — signal/strength unchanged
        assert analysis["signal"] == "BUY"
        assert analysis["signal_strength"] == "STRONG"
        assert "liquidity_warning" not in analysis

        snap = bot._build_decision_snapshot("TEST", analysis)
        sp = snap["signal_pipeline"]
        ev = sp["liquidity_filter_evaluation"]
        assert ev["result"] == "PASSED"
        assert ev["avg_volume"] == 2_000_000.0
        assert ev["high_low_pct"] == 0.5
        assert ev["signal_before"] == "BUY"
        assert ev["signal_after"] == "BUY"
        assert sp["downgraded"] is False
        assert sp["downgrade_stage"] is None
        assert sp["downgrade_reason"] is None


# ---------------------------------------------------------------------------
# Test 4 — COULD_NOT_BE_EVALUATED (fail-open semantics preserved)
# ---------------------------------------------------------------------------


class TestCouldNotBeEvaluatedFailOpen:
    """check_liquidity fails open on insufficient data and on exceptions.
    The helper must NOT mutate analysis in those cases and must record the
    COULD_NOT_BE_EVALUATED token so the operator can distinguish a real
    PASS from a fail-open."""

    def test_insufficient_data_fail_open(self):
        bot = _make_bot()
        bot.check_liquidity = MagicMock(
            return_value=(True, 0, 0, "Insufficient data - skipping liquidity check")
        )
        analysis = _buy_analysis(signal_strength="STRONG")

        bot._apply_outer_liquidity_filter("TEST", analysis)

        assert analysis["signal"] == "BUY"
        assert analysis["signal_strength"] == "STRONG"
        assert "liquidity_warning" not in analysis

        snap = bot._build_decision_snapshot("TEST", analysis)
        ev = snap["signal_pipeline"]["liquidity_filter_evaluation"]
        assert ev["result"] == "COULD_NOT_BE_EVALUATED"
        assert ev["avg_volume"] == 0
        assert ev["high_low_pct"] == 0
        assert ev["signal_before"] == "BUY"
        assert ev["signal_after"] == "BUY"
        assert snap["signal_pipeline"]["downgraded"] is False

    def test_check_failed_fail_open(self):
        bot = _make_bot()
        bot.check_liquidity = MagicMock(
            return_value=(True, 0, 0, "Check failed - allowing")
        )
        analysis = _buy_analysis(signal_strength="STRONG")

        bot._apply_outer_liquidity_filter("TEST", analysis)

        assert analysis["signal"] == "BUY"
        assert analysis["signal_strength"] == "STRONG"
        assert "liquidity_warning" not in analysis

        snap = bot._build_decision_snapshot("TEST", analysis)
        ev = snap["signal_pipeline"]["liquidity_filter_evaluation"]
        assert ev["result"] == "COULD_NOT_BE_EVALUATED"
        assert ev["signal_before"] == "BUY"
        assert ev["signal_after"] == "BUY"
        assert snap["signal_pipeline"]["downgraded"] is False


# ---------------------------------------------------------------------------
# Test 5 — Non-BUY pre-analysis signal: no eval block created
# ---------------------------------------------------------------------------


class TestNonBuyPreSignal:
    """When the analyzer-stage signal is HOLD or SELL, the outer guard
    in run_analysis (`signal == 'BUY'`) prevents _apply_outer_liquidity_filter
    from being called at all. The snapshot builder therefore sees no
    _p0_* keys and the signal_pipeline block is omitted."""

    def test_hold_pre_signal_omits_signal_pipeline_block(self):
        bot = _make_bot()
        analysis = _buy_analysis()
        analysis["signal"] = "HOLD"
        analysis["signal_strength"] = "WEAK"

        # Helper is NOT called for non-BUY (mirrors run_analysis outer guard)
        # Build snapshot directly to verify omission.
        snap = bot._build_decision_snapshot("TEST", analysis)

        assert "signal_pipeline" not in snap
        # No _p0_* keys should leak into the analysis dict
        assert "_p0_liquidity_eval" not in analysis
        assert "_p0_pre_signal" not in analysis
        assert "_p0_pre_signal_strength" not in analysis


# ---------------------------------------------------------------------------
# Test 6 — Disabled filter: NOT_APPLICABLE result, no mutation
# ---------------------------------------------------------------------------


class TestDisabledFilter:
    def test_disabled_emits_not_applicable_and_no_mutation(self):
        bot = _make_bot(enable_liquidity_filter=False)
        # check_liquidity must NOT be called when disabled
        bot.check_liquidity = MagicMock(
            return_value=(False, 0, 0, "should not be called")
        )
        analysis = _buy_analysis(signal_strength="STRONG")
        pre_signal = analysis["signal"]
        pre_strength = analysis["signal_strength"]

        bot._apply_outer_liquidity_filter("TEST", analysis)

        # No mutation
        assert analysis["signal"] == pre_signal
        assert analysis["signal_strength"] == pre_strength
        assert "liquidity_warning" not in analysis

        # check_liquidity was NOT invoked (we never spent an API call)
        bot.check_liquidity.assert_not_called()

        snap = bot._build_decision_snapshot("TEST", analysis)
        ev = snap["signal_pipeline"]["liquidity_filter_evaluation"]
        assert ev["result"] == "NOT_APPLICABLE"
        assert ev["enabled"] is False
        assert ev["applicable"] is False
        assert ev["evaluated"] is False
        # No measurements populated
        assert ev["avg_volume"] is None
        assert ev["high_low_pct"] is None
        # signal_before == signal_after == 'BUY'
        assert ev["signal_before"] == "BUY"
        assert ev["signal_after"] == "BUY"
        # downgraded is False because the signal didn't change
        assert snap["signal_pipeline"]["downgraded"] is False
        assert snap["signal_pipeline"]["downgrade_stage"] is None


# ---------------------------------------------------------------------------
# Test 7 — Signal pipeline invariance: helper mutations match pre-observability
# ---------------------------------------------------------------------------


class TestSignalPipelineInvariance:
    """The helper must produce EXACTLY the same analysis['signal'] /
    analysis['signal_strength'] mutation as the pre-extraction inline code,
    regardless of whether the _p0_* scratch keys are present.

    Pre-extraction (inline):
        if analysis.get('signal') == 'BUY' and self.enable_liquidity_filter:
            passes, avg_vol, spread, reason = self.check_liquidity(
                symbol, self.min_daily_volume
            )
            if not passes:
                analysis['signal'] = 'HOLD'
                analysis['signal_strength'] = 'WEAK'
                analysis['liquidity_warning'] = reason
    """

    @staticmethod
    def _pre_observability_path(bot, symbol, analysis):
        """Mirror the inline pre-extraction code exactly."""
        if (
            analysis.get("signal") == "BUY"
            and bot.enable_liquidity_filter
        ):
            passes, _avg_vol, _spread, reason = bot.check_liquidity(
                symbol, bot.min_daily_volume
            )
            if not passes:
                analysis["signal"] = "HOLD"
                analysis["signal_strength"] = "WEAK"
                analysis["liquidity_warning"] = reason
        return analysis

    def test_failed_case_matches_pre_observability(self):
        bot = _make_bot()
        bot.check_liquidity = MagicMock(
            return_value=(False, 500_000.0, 0.5, "Volume 0.5M < 1M minimum")
        )

        a_helper = _buy_analysis()
        a_baseline = _buy_analysis()

        bot._apply_outer_liquidity_filter("T1", a_helper)
        self._pre_observability_path(bot, "T1", a_baseline)

        # Mutation identical
        assert a_helper["signal"] == a_baseline["signal"]
        assert a_helper["signal_strength"] == a_baseline["signal_strength"]
        assert a_helper.get("liquidity_warning") == a_baseline.get("liquidity_warning")

    def test_passed_case_matches_pre_observability(self):
        bot = _make_bot()
        bot.check_liquidity = MagicMock(
            return_value=(True, 2_000_000.0, 0.5, "Passes liquidity check")
        )

        a_helper = _buy_analysis()
        a_baseline = _buy_analysis()

        bot._apply_outer_liquidity_filter("T2", a_helper)
        self._pre_observability_path(bot, "T2", a_baseline)

        assert a_helper["signal"] == a_baseline["signal"]
        assert a_helper["signal_strength"] == a_baseline["signal_strength"]
        assert "liquidity_warning" not in a_helper
        assert "liquidity_warning" not in a_baseline

    def test_could_not_be_evaluated_matches_pre_observability(self):
        bot = _make_bot()
        bot.check_liquidity = MagicMock(
            return_value=(True, 0, 0, "Insufficient data - skipping liquidity check")
        )

        a_helper = _buy_analysis()
        a_baseline = _buy_analysis()

        bot._apply_outer_liquidity_filter("T3", a_helper)
        self._pre_observability_path(bot, "T3", a_baseline)

        assert a_helper["signal"] == a_baseline["signal"]
        assert a_helper["signal_strength"] == a_baseline["signal_strength"]
        assert "liquidity_warning" not in a_helper
        assert "liquidity_warning" not in a_baseline


# ---------------------------------------------------------------------------
# Test 8 — Cross-symbol state safety: each analysis is independent
# ---------------------------------------------------------------------------


class TestCrossSymbolStateSafety:
    """Symbol A and symbol B must NOT share state. The helper mutates the
    passed-in dict in place; the snapshot builder pops the _p0_* keys off
    the analysis dict it is given. Verify a fresh analysis dict for B does
    not inherit any state from A."""

    def test_independent_symbols(self):
        bot_a = _make_bot()
        bot_a.check_liquidity = MagicMock(
            return_value=(False, 500_000.0, 0.5, "Volume 0.5M < 1M minimum")
        )
        analysis_a = _buy_analysis()

        bot_b = _make_bot()
        bot_b.check_liquidity = MagicMock(
            return_value=(True, 2_000_000.0, 0.5, "Passes liquidity check")
        )
        analysis_b = _buy_analysis()  # FRESH dict, not A's

        bot_a._apply_outer_liquidity_filter("A", analysis_a)
        bot_b._apply_outer_liquidity_filter("B", analysis_b)

        # A was downgraded
        assert analysis_a["signal"] == "HOLD"
        snap_a = bot_a._build_decision_snapshot("A", analysis_a)
        assert snap_a["signal_pipeline"]["final_signal"] == "HOLD"
        assert snap_a["signal_pipeline"]["downgraded"] is True
        assert snap_a["signal_pipeline"]["liquidity_filter_evaluation"]["result"] == "FAILED"

        # B was NOT downgraded
        assert analysis_b["signal"] == "BUY"
        snap_b = bot_b._build_decision_snapshot("B", analysis_b)
        assert snap_b["signal_pipeline"]["final_signal"] == "BUY"
        assert snap_b["signal_pipeline"]["downgraded"] is False
        assert snap_b["signal_pipeline"]["liquidity_filter_evaluation"]["result"] == "PASSED"

        # After snapshot build, no _p0_* keys remain on either dict
        for k in (
            "_p0_liquidity_eval",
            "_p0_pre_signal",
            "_p0_pre_signal_strength",
        ):
            assert k not in analysis_a
            assert k not in analysis_b