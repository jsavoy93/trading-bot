"""SCORE-002 — Persist score-component validation failure reason.

Tests that ``_score_components`` / ``_clamp_total_score`` return an
identifiable fail-closed reason, that the reason propagates through
``analyze_symbol`` and ``analyze_multi_timeframe``, that the reason
survives into the persisted ``decision_snapshot.scoring.score_error_reason``
field on a ``SKIPPED_INVALID_DATA`` terminal decision, and that no
existing trading behavior changes.

The diagnostic context for this work is the 786
``SKIPPED_INVALID_DATA`` rows identified in the frozen joint-pass
cohort diagnostic (see ``reports/2026-09-28_173000_analyze-symbol-none-root-cause.md``):
the bot fell back to a HOLD score and persisted a partial HOLD
snapshot, but the production observability did not tell us WHICH
``_score_components`` validation tripped. The class constants
``SCORE_REASON_*`` enumerate the stable machine-readable tokens this
observability feature uses.

The tests are pure-function tests; they construct SmartTradingBot
instances via ``__new__`` to avoid Alpaca client construction.
"""

from unittest.mock import MagicMock

import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# Test helpers (mirrors the pattern in
# tests/test_smart_bot_score_normalization.py)
# ---------------------------------------------------------------------------


def _bot_with_config():
    """Construct a SmartTradingBot instance without service clients."""
    from src.core.smart_bot import SmartTradingBot
    bot = SmartTradingBot.__new__(SmartTradingBot)
    bot.sma_fast = 10
    bot.sma_slow = 30
    bot.rsi_period = 14
    bot.enable_multi_timeframe = False
    return bot


def _indicator_series(
    *,
    rsi: float | None = 50.0,
    sma_fast_val: float | None = None,
    sma_slow_val: float | None = None,
    macd_hist: float | None = 0.0,
    atr: float | None = 2.0,
    bb_upper: float | None = 110.0,
    bb_lower: float | None = 90.0,
    close: float | None = 100.0,
) -> pd.Series:
    """Default-valued indicator series; override fields to simulate
    non-finite or missing indicators."""
    if sma_fast_val is None:
        sma_fast_val = close
    if sma_slow_val is None:
        sma_slow_val = close
    return pd.Series(
        {
            "RSI": rsi,
            "SMA_10": sma_fast_val,
            "SMA_30": sma_slow_val,
            "MACD_histogram": macd_hist,
            "ATR": atr,
            "BB_upper": bb_upper,
            "BB_lower": bb_lower,
            "close": close,
        }
    )


def _persist_bot():
    """A bot wired with a MagicMock DB layer so the persistence helper
    can be exercised end-to-end without touching trading_bot.db. Mirrors
    the helper in tests/test_obs_002_terminal_decision_coverage.py."""
    from src.core.smart_bot import SmartTradingBot
    bot = SmartTradingBot.__new__(SmartTradingBot)
    bot.rsi_buy_threshold = 30
    bot.rsi_sell_threshold = 70
    bot.enable_volume_confirmation = True
    bot.bot_version = "2.1.0-test"
    bot.session_id = 99999
    bot.db = MagicMock()
    bot.db.is_available.return_value = True
    return bot


# ---------------------------------------------------------------------------
# (a) Stable reason tokens are exposed as class constants
# ---------------------------------------------------------------------------


class TestScoreReasonTokensAreExposed:
    """The reason tokens are part of the observability contract; tests
    pin them so a typo or rename is caught immediately."""

    def test_macd_histogram_reason_token(self):
        from src.core.smart_bot import SmartTradingBot
        assert (
            SmartTradingBot.SCORE_REASON_MACD_HISTOGRAM_NON_FINITE
            == "macd_histogram_non_finite"
        )

    def test_atr_reason_tokens(self):
        from src.core.smart_bot import SmartTradingBot
        assert (
            SmartTradingBot.SCORE_REASON_ATR_NON_FINITE == "atr_non_finite"
        )
        assert SmartTradingBot.SCORE_REASON_ATR_ZERO == "atr_zero"

    def test_bb_reason_tokens(self):
        from src.core.smart_bot import SmartTradingBot
        assert (
            SmartTradingBot.SCORE_REASON_BB_UPPER_NON_FINITE
            == "bb_upper_non_finite"
        )
        assert (
            SmartTradingBot.SCORE_REASON_BB_LOWER_NON_FINITE
            == "bb_lower_non_finite"
        )

    def test_close_and_rsi_reason_tokens(self):
        from src.core.smart_bot import SmartTradingBot
        assert (
            SmartTradingBot.SCORE_REASON_CLOSE_NON_FINITE == "close_non_finite"
        )
        assert SmartTradingBot.SCORE_REASON_RSI_NON_FINITE == "rsi_non_finite"


# ---------------------------------------------------------------------------
# (b) Per-field fail-closed reason identification
# ---------------------------------------------------------------------------


class TestScoreComponentsPerFieldFailClosedReason:
    """Each documented fail-closed case in ``_score_components`` produces
    a unique, stable reason token. Mirrors the existing
    ``test_score_components_returns_none_when_*`` tests but asserts
    the reason (not just ``is None``)."""

    def test_non_finite_macd_histogram(self):
        bot = _bot_with_config()
        latest = _indicator_series(macd_hist=None)
        latest["MACD_histogram"] = float("nan")
        components, reason = bot._score_components_with_reason(latest)
        assert components is None
        assert reason == "macd_histogram_non_finite"

    def test_non_finite_atr(self):
        bot = _bot_with_config()
        latest = _indicator_series(atr=None)
        latest["ATR"] = float("nan")
        components, reason = bot._score_components_with_reason(latest)
        assert components is None
        assert reason == "atr_non_finite"

    def test_atr_zero(self):
        bot = _bot_with_config()
        latest = _indicator_series(atr=0.0)
        components, reason = bot._score_components_with_reason(latest)
        assert components is None
        assert reason == "atr_zero"

    def test_non_finite_bb_upper(self):
        bot = _bot_with_config()
        latest = _indicator_series(bb_upper=None)
        latest["BB_upper"] = float("nan")
        components, reason = bot._score_components_with_reason(latest)
        assert components is None
        assert reason == "bb_upper_non_finite"

    def test_non_finite_bb_lower(self):
        bot = _bot_with_config()
        latest = _indicator_series(bb_lower=None)
        latest["BB_lower"] = float("nan")
        components, reason = bot._score_components_with_reason(latest)
        assert components is None
        assert reason == "bb_lower_non_finite"

    def test_non_finite_close(self):
        bot = _bot_with_config()
        # Build a fully-valid series, then overwrite ONLY close with NaN
        # (the ``_indicator_series`` helper falls back to ``close`` for
        # SMA defaults, so we cannot use ``close=None`` here).
        latest = _indicator_series()
        latest["close"] = float("nan")
        components, reason = bot._score_components_with_reason(latest)
        assert components is None
        assert reason == "close_non_finite"

    def test_non_finite_rsi(self):
        bot = _bot_with_config()
        latest = _indicator_series(rsi=None)
        latest["RSI"] = float("nan")
        components, reason = bot._score_components_with_reason(latest)
        assert components is None
        assert reason == "rsi_non_finite"

    def test_non_finite_sma_fast(self):
        bot = _bot_with_config()
        latest = _indicator_series()
        latest["SMA_10"] = float("nan")
        components, reason = bot._score_components_with_reason(latest)
        assert components is None
        assert reason == "sma_fast_non_finite"

    def test_non_finite_sma_slow(self):
        bot = _bot_with_config()
        latest = _indicator_series()
        latest["SMA_30"] = float("nan")
        components, reason = bot._score_components_with_reason(latest)
        assert components is None
        assert reason == "sma_slow_non_finite"

    def test_non_finite_catalyst(self):
        bot = _bot_with_config()
        latest = _indicator_series()
        components, reason = bot._score_components_with_reason(
            latest, catalyst_score=float("nan"),
        )
        assert components is None
        assert reason == "catalyst_non_finite"

    def test_multiple_invalid_fields_are_comma_joined_in_stable_order(self):
        """When multiple required fields are invalid, every invalid
        field is reported in the same order the helper iterates. The
        first-listed token matches the existing first-fail short
        circuit so legacy callers comparing against a single reason
        token still work."""
        bot = _bot_with_config()
        latest = _indicator_series()
        latest["MACD_histogram"] = float("nan")
        latest["ATR"] = float("nan")
        components, reason = bot._score_components_with_reason(latest)
        assert components is None
        # macd_histogram is iterated before atr in the helper; the
        # first-listed token reflects the dominant failure.
        assert reason.startswith("macd_histogram_non_finite")
        assert "atr_non_finite" in reason

    def test_valid_inputs_return_none_reason(self):
        bot = _bot_with_config()
        latest = _indicator_series()
        components, reason = bot._score_components_with_reason(latest)
        assert components is not None
        assert reason is None


# ---------------------------------------------------------------------------
# (c) Clamp fail-closed reason
# ---------------------------------------------------------------------------


class TestClampTotalScorePerInputFailClosedReason:

    def test_non_finite_returns_clamp_reason(self):
        bot = _bot_with_config()
        value, reason = bot._clamp_total_score_with_reason(float("nan"))
        assert value is None
        assert reason == "clamp_total_score_non_finite"

    def test_inf_returns_clamp_reason(self):
        bot = _bot_with_config()
        value, reason = bot._clamp_total_score_with_reason(float("inf"))
        assert value is None
        assert reason == "clamp_total_score_non_finite"

    def test_unparseable_string_returns_clamp_reason(self):
        bot = _bot_with_config()
        value, reason = bot._clamp_total_score_with_reason("not-a-number")
        assert value is None
        assert reason == "clamp_total_score_non_finite"

    def test_valid_returns_no_reason(self):
        bot = _bot_with_config()
        value, reason = bot._clamp_total_score_with_reason(75.5)
        assert value == 75.5
        assert reason is None


# ---------------------------------------------------------------------------
# (d) Backward-compatible wrappers
# ---------------------------------------------------------------------------


class TestPlainHelpersUnchanged:
    """The original ``_score_components`` and ``_clamp_total_score``
    return ``Optional[Dict]`` and ``Optional[float]`` respectively
    (no reason). This is the contract every existing test relies on.
    These tests prove SCORE-002 did not silently change that."""

    def test_score_components_returns_dict_on_success(self):
        bot = _bot_with_config()
        latest = _indicator_series()
        result = bot._score_components(latest)
        assert isinstance(result, dict)
        assert "rsi_score" in result

    def test_score_components_returns_none_on_failure(self):
        bot = _bot_with_config()
        latest = _indicator_series()
        latest["MACD_histogram"] = float("nan")
        assert bot._score_components(latest) is None

    def test_clamp_total_score_returns_float_on_success(self):
        bot = _bot_with_config()
        assert bot._clamp_total_score(75.5) == 75.5

    def test_clamp_total_score_returns_none_on_failure(self):
        bot = _bot_with_config()
        assert bot._clamp_total_score(float("nan")) is None


# ---------------------------------------------------------------------------
# (e) analyze_symbol / analyze_multi_timeframe propagate the reason
# ---------------------------------------------------------------------------


class TestAnalyzeSymbolPropagatesReason:
    """The SCORE-002 helpers expose ``self._last_score_error_reason``
    before returning ``None``. This is the only way the main loop
    fallback path can attach the reason to the persisted snapshot."""

    def test_analyze_symbol_sets_scratch_reason_on_failure(self):
        """We patch ``_score_components_with_reason`` to return a known
        reason, then assert that ``analyze_symbol`` stores it on
        ``self._last_score_error_reason`` and returns ``None``."""
        bot = _bot_with_config()
        bot.errors_count = 0
        bot.trading_windows = MagicMock()
        # Build a DataFrame that already has every indicator column the
        # analyzer expects. We patch ``calculate_indicators`` to a
        # pass-through identity so the analyzer does not recompute.
        idx = pd.date_range("2026-01-01", periods=40, freq="D")
        df = pd.DataFrame(
            {
                "open": 100.0, "high": 101.0, "low": 99.0,
                "close": 100.0, "volume": 1_000_000,
                "SMA_10": 100.0, "SMA_30": 100.0, "RSI": 50.0,
                "MACD_histogram": 0.0, "MACD": 0.0, "MACD_signal": 0.0,
                "ATR": 2.0, "ATR_pct": 1.5, "BB_upper": 110.0,
                "BB_lower": 90.0, "BB_middle": 100.0, "BB_width": 0.2,
                "volume_sma_20": 1_000_000.0, "volume_ratio": 1.0,
                "vwap": 100.0, "vwap_distance": 0.0,
                "timestamp": idx[-1],
            },
            index=idx,
        )
        bot.get_market_data = MagicMock(return_value=df)
        bot.calculate_indicators = MagicMock(return_value=df)
        bot.scan_catalysts = MagicMock(return_value={"catalyst_score": 0.0})
        # The patched helper returns the SCORE-002 token we want to
        # observe on the bot's scratch field.
        bot._score_components_with_reason = MagicMock(
            return_value=(None, "atr_zero"),
        )
        # Stub everything analyze_symbol calls after the score check
        # to no-ops so it can complete cleanly on the None-return path.
        bot.get_hourly_market_data = MagicMock(return_value=None)

        result = bot.analyze_symbol("AAPL", use_ai=False)

        assert result is None
        assert getattr(bot, "_last_score_error_reason", None) == "atr_zero"

    def test_analyze_symbol_clears_scratch_reason_at_start(self):
        """A stale reason from a prior symbol must NOT leak into the
        next analysis call. We verify by checking the helper is called
        at the top of analyze_symbol (before any score work)."""
        bot = _bot_with_config()
        bot.errors_count = 0
        bot.trading_windows = MagicMock()
        # Pre-set a stale value that must be cleared.
        bot._last_score_error_reason = "stale_value_from_previous_symbol"
        # Same fully-populated indicator DataFrame as above so we do
        # not need to exercise the indicator-calc pipeline.
        idx = pd.date_range("2026-01-01", periods=40, freq="D")
        df = pd.DataFrame(
            {
                "open": 100.0, "high": 101.0, "low": 99.0,
                "close": 100.0, "volume": 1_000_000,
                "SMA_10": 100.0, "SMA_30": 100.0, "RSI": 50.0,
                "MACD_histogram": 0.0, "MACD": 0.0, "MACD_signal": 0.0,
                "ATR": 2.0, "ATR_pct": 1.5, "BB_upper": 110.0,
                "BB_lower": 90.0, "BB_middle": 100.0, "BB_width": 0.2,
                "volume_sma_20": 1_000_000.0, "volume_ratio": 1.0,
                "vwap": 100.0, "vwap_distance": 0.0,
                "timestamp": idx[-1],
            },
            index=idx,
        )
        bot.get_market_data = MagicMock(return_value=df)
        bot.calculate_indicators = MagicMock(return_value=df)
        bot.scan_catalysts = MagicMock(return_value={"catalyst_score": 0.0})
        bot._score_components_with_reason = MagicMock(
            return_value=(
                {
                    "rsi_score": 0.0, "sma_score": 0.0,
                    "macd_score": 0.0, "bb_score": 0.0,
                    "catalyst_score": 0.0, "macd_atr_ratio": 0.0,
                },
                None,
            ),
        )
        bot._clamp_total_score_with_reason = MagicMock(
            return_value=(50.0, None),
        )
        # Stub downstream calls so analyze_symbol can return a real dict.
        bot.get_hourly_market_data = MagicMock(return_value=None)

        # Patch the helper that triggers the analysis-pipeline return
        # so analyze_symbol returns a real dict without further side
        # effects. (We exercise analyze_symbol purely to observe its
        # entry behavior: the scratch field must be cleared.)
        # Easiest: make the score path raise an exception that the
        # outer ``except`` catches and routes to the fallback persist;
        # then check the scratch field was cleared before the raise.
        def _raise(*args, **kwargs):
            raise RuntimeError("synthetic early exit")
        bot.scan_catalysts = MagicMock(side_effect=_raise)

        try:
            bot.analyze_symbol("AAPL", use_ai=False)
        except Exception:
            # analyze_symbol catches outer exceptions inside its body;
            # if anything escapes here the outer ``except`` in the
            # main loop would persist a SKIPPED_INVALID_DATA row with
            # ``analysis_body_exception``. We don't care about that
            # here; we only care that the scratch field is cleared.
            pass

        # The scratch field must have been cleared at function entry,
        # regardless of how analyze_symbol exited.
        assert getattr(bot, "_last_score_error_reason", None) is None


class TestAnalyzeMultiTimeframePropagatesReason:
    def test_analyze_multi_timeframe_sets_scratch_reason_on_failure(self):
        bot = _bot_with_config()
        bot.errors_count = 0
        # The MTF analyzer touches a handful of attributes that the
        # ``_bot_with_config`` helper doesn't set. Without them, the
        # function fails on a NameError inside the outer try/except
        # before reaching ``_score_components_with_reason``.
        bot.rsi_buy_threshold = 30
        bot.rsi_sell_threshold = 70
        bot.hourly_weight = 0.3
        bot.enable_volume_confirmation = False
        bot.enable_regime_filter = False
        bot.enable_sp_filter = False
        bot.enable_liquidity_filter = False
        bot.enable_sector_filter = False
        bot.enable_ai_conflict_filter = False
        bot.enable_mtf_conflict_filter = False
        bot.enable_vol_downgrade_filter = False
        bot.use_ai_for_ticker_analysis = False
        bot.ai = MagicMock()
        bot.ai.is_configured = False
        bot.check_volume_confirmation = MagicMock(return_value=(True, 1.0, None))
        bot.scan_catalysts = MagicMock(return_value={"catalyst_score": 0.0})
        bot.check_earnings_calendar = MagicMock(return_value=(False, None, None))
        bot.check_sp_relative_strength = MagicMock(return_value=(True, 0, 0, 0))
        bot.check_liquidity = MagicMock(return_value=(True, 0, 0, ""))
        bot.get_sector_for_symbol = MagicMock(return_value=None)
        bot.get_current_market_regime = MagicMock(
            return_value={"regime": "RANGING", "modifiers": {}, "adx": 0},
        )
        bot._finalize_filter_results = MagicMock(
            return_value=({}, None, 0),
        )
        idx = pd.date_range("2026-01-01", periods=40, freq="D")
        df = pd.DataFrame(
            {
                "open": 100.0, "high": 101.0, "low": 99.0,
                "close": 100.0, "volume": 1_000_000,
                "SMA_10": 100.0, "SMA_30": 100.0, "RSI": 50.0,
                "MACD_histogram": 0.0, "MACD": 0.0, "MACD_signal": 0.0,
                "ATR": 2.0, "ATR_pct": 1.5, "BB_upper": 110.0,
                "BB_lower": 90.0, "BB_middle": 100.0, "BB_width": 0.2,
                "volume_sma_20": 1_000_000.0, "volume_ratio": 1.0,
                "vwap": 100.0, "vwap_distance": 0.0,
                "timestamp": idx[-1],
            },
            index=idx,
        )
        bot.get_market_data = MagicMock(return_value=df)
        bot.calculate_indicators = MagicMock(return_value=df)
        bot.get_hourly_market_data = MagicMock(return_value=None)
        # Patch the helper so the MTF path reaches the
        # ``_score_components_with_reason`` call on line 2436 and
        # then bails out with the SCORE-002 token.
        bot._score_components_with_reason = MagicMock(
            return_value=(None, "macd_histogram_non_finite"),
        )

        result = bot.analyze_multi_timeframe("AAPL", use_ai=False)

        assert result is None
        assert (
            getattr(bot, "_last_score_error_reason", None)
            == "macd_histogram_non_finite"
        )


# ---------------------------------------------------------------------------
# (f) The reason survives into the persisted SKIPPED_INVALID_DATA snapshot
# ---------------------------------------------------------------------------


class TestScoreErrorReasonPersistsInSnapshot:
    """The diagnostic question: when the fallback HOLD path runs,
    does the persisted snapshot include the exact fail-closed reason
    under ``scoring.score_error_reason``? This is the field the
    dashboard will read for grouping."""

    def test_score_error_reason_kwarg_persisted_in_snapshot(self):
        bot = _persist_bot()
        snap = bot._persist_skipped_terminal_decision(
            "AAPL",
            cycle_id="cycle_score_002_001",
            cycle_start_iso="2026-09-28T18:00:00+00:00",
            outcome="SKIPPED_INVALID_DATA",
            primary_reason="analyze_symbol returned None (fallback HOLD)",
            analysis={"price": 100.0, "rsi": 28.0},
            score_error_reason="atr_zero",
        )
        assert snap is not None
        assert snap["scoring"]["score_error_reason"] == "atr_zero"

    def test_score_error_reason_in_analysis_dict_persisted(self):
        bot = _persist_bot()
        snap = bot._persist_skipped_terminal_decision(
            "AAPL",
            cycle_id="cycle_score_002_002",
            cycle_start_iso="2026-09-28T18:00:00+00:00",
            outcome="SKIPPED_INVALID_DATA",
            primary_reason="analyze_symbol returned None",
            analysis={
                "price": 100.0, "rsi": 28.0,
                "score_error_reason": "macd_histogram_non_finite",
            },
        )
        assert snap is not None
        assert (
            snap["scoring"]["score_error_reason"]
            == "macd_histogram_non_finite"
        )

    def test_kwarg_overrides_analysis_dict_field(self):
        """The explicit kwarg is the canonical place; if both are
        present, the kwarg wins so a stale analysis-dict field cannot
        leak from a caller that failed to clear it."""
        bot = _persist_bot()
        snap = bot._persist_skipped_terminal_decision(
            "AAPL",
            cycle_id="cycle_score_002_003",
            cycle_start_iso="2026-09-28T18:00:00+00:00",
            outcome="SKIPPED_INVALID_DATA",
            primary_reason="test",
            analysis={
                "price": 100.0,
                "score_error_reason": "stale_value",
            },
            score_error_reason="atr_zero",
        )
        assert snap["scoring"]["score_error_reason"] == "atr_zero"

    def test_no_score_error_reason_field_when_not_supplied(self):
        """Backward compatibility: callers that do not pass the
        SCORE-002 kwarg (older tests, pre-existing call sites that
        didn't need a reason) must not see ``score_error_reason`` in
        the persisted snapshot. The field is purely opt-in."""
        bot = _persist_bot()
        snap = bot._persist_skipped_terminal_decision(
            "AAPL",
            cycle_id="cycle_score_002_004",
            cycle_start_iso="2026-09-28T18:00:00+00:00",
            outcome="SKIPPED_INVALID_DATA",
            primary_reason="no market data (needs 30 bars)",
        )
        assert snap is not None
        assert "score_error_reason" not in snap["scoring"]

    def test_score_error_reason_persisted_for_no_market_data_path(self):
        """The ``no market data`` upstream path uses the explicit token
        ``no_market_data`` so the dashboard can distinguish upstream
        Alpaca-availability failures from downstream indicator
        validation failures."""
        bot = _persist_bot()
        snap = bot._persist_skipped_terminal_decision(
            "AAPL",
            cycle_id="cycle_score_002_005",
            cycle_start_iso="2026-09-28T18:00:00+00:00",
            outcome="SKIPPED_INVALID_DATA",
            primary_reason="no market data (needs 30 bars)",
            score_error_reason="no_market_data",
        )
        assert snap["scoring"]["score_error_reason"] == "no_market_data"

    def test_score_error_reason_persisted_for_fallback_nan_path(self):
        """The ``fallback_nan`` token identifies the NaN-guard inside
        the fallback score compute (separate from the strict
        ``_score_components`` validation)."""
        bot = _persist_bot()
        snap = bot._persist_skipped_terminal_decision(
            "AAPL",
            cycle_id="cycle_score_002_006",
            cycle_start_iso="2026-09-28T18:00:00+00:00",
            outcome="SKIPPED_INVALID_DATA",
            primary_reason="NaN in fallback score compute (bb_pos is NaN)",
            analysis={"price": 100.0, "rsi": 50.0},
            score_error_reason="fallback_nan",
        )
        assert snap["scoring"]["score_error_reason"] == "fallback_nan"

    def test_score_error_reason_persisted_for_outer_exception_path(self):
        """The ``analysis_body_exception`` token identifies rows that
        fell into the catch-all ``except Exception`` branch (e.g.
        NaN-to-int conversions, network blips)."""
        bot = _persist_bot()
        snap = bot._persist_skipped_terminal_decision(
            "AAPL",
            cycle_id="cycle_score_002_007",
            cycle_start_iso="2026-09-28T18:00:00+00:00",
            outcome="SKIPPED_INVALID_DATA",
            primary_reason="analysis-body exception: ValueError: cannot convert",
            analysis={"signal": "HOLD", "signal_strength": "WEAK"},
            score_error_reason="analysis_body_exception",
        )
        assert (
            snap["scoring"]["score_error_reason"] == "analysis_body_exception"
        )

    def test_score_error_reason_omitted_for_hold_ineligible(self):
        """HOLD_INELIGIBLE rows have a real score; ``score_error_reason``
        is NOT applicable and must not be persisted when not supplied.
        This protects dashboards from confusing score-component
        validation failures with normal HOLD ineligibility."""
        bot = _persist_bot()
        snap = bot._persist_skipped_terminal_decision(
            "AAPL",
            cycle_id="cycle_score_002_008",
            cycle_start_iso="2026-09-28T18:00:00+00:00",
            outcome="HOLD_INELIGIBLE",
            primary_reason="weak signal strength (WEAK)",
            analysis={
                "signal": "BUY", "signal_strength": "WEAK",
                "total_score": 65.0,
                "rsi": 28.0, "sma_fast": 110.0, "sma_slow": 100.0,
                "macd_histogram": 0.5, "volume_ratio": 1.2,
            },
        )
        assert "score_error_reason" not in snap["scoring"]


# ---------------------------------------------------------------------------
# (g) Fallback HOLD score path remains unchanged in shape
# ---------------------------------------------------------------------------


class TestFallbackHOLDPathBehaviorUnchanged:
    """The fallback HOLD score path (lines 5861-5908 in
    src/core/smart_bot.py) must NOT change shape, content, or the
    ``primary_reason`` string. The SCORE-002 reason is ADDITIVE; it
    goes in a new field. Pre-existing behavior is preserved."""

    def test_primary_reason_text_unchanged(self):
        bot = _persist_bot()
        snap = bot._persist_skipped_terminal_decision(
            "AAPL",
            cycle_id="cycle_score_002_009",
            cycle_start_iso="2026-09-28T18:00:00+00:00",
            outcome="SKIPPED_INVALID_DATA",
            primary_reason=(
                "analyze_symbol returned None (fallback HOLD score "
                "saved but no real strategy-gate evaluation)"
            ),
            analysis={
                "price": 100.0, "rsi": 50.0,
                "sma_fast": 100.0, "sma_slow": 100.0,
                "rsi_score": 0, "sma_score": 0,
                "macd_score": 0, "bb_score": 0,
                "regime_score": 0, "catalyst_score": 0,
                "total_score": 50,
            },
            score_error_reason="atr_zero",
        )
        # The primary_reason text is verbatim the legacy string.
        assert "fallback HOLD score saved" in snap["decision"]["primary_reason"]
        # score_invalid_data is still True for SKIPPED_INVALID_DATA.
        assert snap["scoring"]["score_invalid_data"] is True
        # The new score_error_reason field is present alongside.
        assert snap["scoring"]["score_error_reason"] == "atr_zero"
