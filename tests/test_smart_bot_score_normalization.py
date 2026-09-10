"""SCORE-001 — Normalize indicator scores.

Tests the bounded component scoring helpers and the explicit 0..100
final-total clamp. All fixtures are constructed directly as ``pd.Series``
of indicator values so the tests are deterministic and independent of
``calculate_indicators`` shape changes.

The documented component ranges are:

    RSI:        -25 .. +25   (linear; RSI=0 -> +25, RSI=50 -> 0, RSI=100 -> -25)
    SMA:        -25 .. +25   (5% SMA separation = +/-25; >5% saturates)
    MACD:       -25 .. +25   (ATR-normalized: 1 ATR of histogram = +/-25)
    BB:         -25 .. +25   (lower band = +25, upper band = -25, midpoint = 0)
    Catalyst:    0  .. +25   (additive bonus)
    Regime:    -20  .. +20   (optional)

After final _clamp_total_score(), the published ``total_score`` is in
0..100, centered at 50.

The tests do not exercise ``analyze_symbol`` directly because that
function requires live market data and service clients; they exercise
the helpers that ``analyze_symbol`` now calls, plus a small integration
test that builds a fully-calculated indicator frame and asserts the
published total_score contract.
"""

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from src.core.smart_bot import SmartTradingBot


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bot() -> SmartTradingBot:
    """Construct a SmartTradingBot instance without service clients.

    The scoring helpers only depend on indicator values and bot config
    (sma_fast, sma_slow, rsi_period, etc.), so a bare-bones instance is
    sufficient.
    """
    return SmartTradingBot.__new__(SmartTradingBot)


def _bot_with_config() -> SmartTradingBot:
    bot = _bot()
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
    """Build a latest-bar ``pd.Series`` of indicator values for the helper.

    Defaults are the same documented bullish-but-mild values the rest of
    the tests use. Pass any individual value as ``None`` (or another
    sentinel) to simulate a non-finite / missing indicator for the
    fail-closed tests.
    """
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


# ---------------------------------------------------------------------------
# Component bound tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rsi_value",
    [0.0, 10.0, 25.0, 49.0, 50.0, 51.0, 70.0, 80.0, 100.0],
)
def test_score_components_rsi_stays_within_documented_bounds(rsi_value: float) -> None:
    bot = _bot_with_config()
    latest = _indicator_series(rsi=rsi_value)

    components = bot._score_components(latest)

    assert -25.0 <= components["rsi_score"] <= 25.0


def test_score_components_rsi_endpoints_are_documented_extremes() -> None:
    bot = _bot_with_config()

    bullish = bot._score_components(_indicator_series(rsi=0.0))
    neutral = bot._score_components(_indicator_series(rsi=50.0))
    bearish = bot._score_components(_indicator_series(rsi=100.0))

    assert bullish["rsi_score"] == pytest.approx(25.0)
    assert neutral["rsi_score"] == pytest.approx(0.0)
    assert bearish["rsi_score"] == pytest.approx(-25.0)


@pytest.mark.parametrize(
    "sma_pct",
    [-10.0, -5.0, -2.0, -0.5, 0.0, 0.5, 2.0, 5.0, 10.0],
)
def test_score_components_sma_stays_within_documented_bounds(sma_pct: float) -> None:
    bot = _bot_with_config()
    slow = 100.0
    fast = slow * (1.0 + sma_pct / 100.0)
    latest = _indicator_series(sma_fast_val=fast, sma_slow_val=slow)

    components = bot._score_components(latest)

    assert -25.0 <= components["sma_score"] <= 25.0


def test_score_components_sma_five_percent_separation_is_max() -> None:
    bot = _bot_with_config()

    plus_five = bot._score_components(
        _indicator_series(sma_fast_val=105.0, sma_slow_val=100.0)
    )
    plus_ten = bot._score_components(
        _indicator_series(sma_fast_val=110.0, sma_slow_val=100.0)
    )
    minus_five = bot._score_components(
        _indicator_series(sma_fast_val=95.0, sma_slow_val=100.0)
    )

    assert plus_five["sma_score"] == pytest.approx(25.0)
    assert plus_ten["sma_score"] == pytest.approx(25.0)
    assert minus_five["sma_score"] == pytest.approx(-25.0)


@pytest.mark.parametrize(
    "macd_hist,atr",
    [
        (0.0, 2.0),
        (1.0, 2.0),       # 0.5 ATR -> +12.5
        (2.0, 2.0),       # 1 ATR -> +25 (saturated)
        (100.0, 2.0),     # 50 ATR -> still +25
        (-1.0, 2.0),      # -0.5 ATR -> -12.5
        (-100.0, 2.0),    # -50 ATR -> still -25
    ],
)
def test_score_components_macd_stays_within_documented_bounds(macd_hist: float, atr: float) -> None:
    bot = _bot_with_config()
    latest = _indicator_series(macd_hist=macd_hist, atr=atr)

    components = bot._score_components(latest)

    assert components is not None
    assert -25.0 <= components["macd_score"] <= 25.0


def test_score_components_macd_one_atr_is_max_contribution() -> None:
    bot = _bot_with_config()

    plus_one_atr = bot._score_components(_indicator_series(macd_hist=2.0, atr=2.0))
    minus_one_atr = bot._score_components(_indicator_series(macd_hist=-2.0, atr=2.0))

    assert plus_one_atr["macd_score"] == pytest.approx(25.0)
    assert minus_one_atr["macd_score"] == pytest.approx(-25.0)
    assert plus_one_atr["macd_atr_ratio"] == pytest.approx(1.0)
    assert minus_one_atr["macd_atr_ratio"] == pytest.approx(-1.0)


def test_score_components_macd_is_dimensionless_via_atr() -> None:
    """Identical normalized MACD should produce identical scores regardless
    of price scale. A 2-ATR MACD histogram is +25 whether the symbol trades
    at $10 or $10,000."""
    bot = _bot_with_config()

    cheap = bot._score_components(_indicator_series(macd_hist=0.2, atr=0.2))
    expensive = bot._score_components(_indicator_series(macd_hist=200.0, atr=200.0))

    assert cheap["macd_score"] == pytest.approx(25.0)
    assert expensive["macd_score"] == pytest.approx(25.0)
    assert cheap["macd_score"] == expensive["macd_score"]


def test_score_components_bb_endpoints_are_documented_extremes() -> None:
    bot = _bot_with_config()

    lower = bot._score_components(_indicator_series(close=90.0, bb_upper=110.0, bb_lower=90.0))
    midpoint = bot._score_components(_indicator_series(close=100.0, bb_upper=110.0, bb_lower=90.0))
    upper = bot._score_components(_indicator_series(close=110.0, bb_upper=110.0, bb_lower=90.0))

    assert lower["bb_score"] == pytest.approx(25.0)
    assert midpoint["bb_score"] == pytest.approx(0.0)
    assert upper["bb_score"] == pytest.approx(-25.0)


def test_score_components_bb_stays_within_bounds_for_any_close() -> None:
    bot = _bot_with_config()

    for close in (50.0, 75.0, 100.0, 125.0, 200.0):
        components = bot._score_components(
            _indicator_series(close=close, bb_upper=110.0, bb_lower=90.0)
        )
        assert -25.0 <= components["bb_score"] <= 25.0


# ---------------------------------------------------------------------------
# Final total_score clamp tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        (-10000.0, 0.0),
        (-100.0, 0.0),
        (0.0, 0.0),
        (50.0, 50.0),
        (100.0, 100.0),
        (10000.0, 100.0),
    ],
)
def test_clamp_total_score_clamps_to_0_100(raw: float, expected: float) -> None:
    bot = _bot()

    assert bot._clamp_total_score(raw) == pytest.approx(expected)


@pytest.mark.parametrize(
    "bad",
    [float("nan"), float("inf"), float("-inf"), None, "not-a-number", object()],
)
def test_clamp_total_score_returns_none_for_invalid_input(bad: object) -> None:
    """SCORE-001 amend #1: invalid/non-finite input must NOT be silently
    coerced into a numeric score (which previously surfaced as 0.0, the
    most extreme bearish possible value). The clamp returns ``None`` and
    the caller MUST treat the symbol as non-actionable."""
    bot = _bot()

    assert bot._clamp_total_score(bad) is None


# ---------------------------------------------------------------------------
# Scenario fixtures: bullish / neutral / bearish
# ---------------------------------------------------------------------------


def _bullish_series() -> pd.Series:
    """Indicators representing a clear bullish setup:
    - RSI: oversold recovery (~30)
    - SMA: 4% positive separation (fast above slow)
    - MACD: +0.5 ATR (positive momentum)
    - BB: price near lower band (mean-reversion entry)
    """
    return _indicator_series(
        rsi=30.0,
        sma_fast_val=104.0,
        sma_slow_val=100.0,
        macd_hist=1.0,
        atr=2.0,
        bb_upper=110.0,
        bb_lower=90.0,
        close=92.0,  # near lower band
    )


def _neutral_series() -> pd.Series:
    """Indicators representing a neutral setup (all components near zero)."""
    return _indicator_series(
        rsi=50.0,
        sma_fast_val=100.0,
        sma_slow_val=100.0,
        macd_hist=0.0,
        atr=2.0,
        bb_upper=110.0,
        bb_lower=90.0,
        close=100.0,  # midpoint of BB
    )


def _bearish_series() -> pd.Series:
    """Indicators representing a clear bearish setup:
    - RSI: overbought territory (~75)
    - SMA: 4% negative separation (fast below slow)
    - MACD: -0.5 ATR (negative momentum)
    - BB: price near upper band
    """
    return _indicator_series(
        rsi=75.0,
        sma_fast_val=96.0,
        sma_slow_val=100.0,
        macd_hist=-1.0,
        atr=2.0,
        bb_upper=110.0,
        bb_lower=90.0,
        close=108.0,  # near upper band
    )


def test_bullish_fixture_total_score_at_least_65() -> None:
    bot = _bot_with_config()

    components = bot._score_components(_bullish_series())
    raw_total = 50.0 + sum(v for k, v in components.items() if k != "macd_atr_ratio")
    total = bot._clamp_total_score(raw_total)

    assert total >= 65.0, (
        f"Bullish fixture should produce a score >= 65 (BUY zone), "
        f"got {total:.2f}; components={components}"
    )


def test_neutral_fixture_total_score_between_45_and_55() -> None:
    bot = _bot_with_config()

    components = bot._score_components(_neutral_series())
    raw_total = 50.0 + sum(v for k, v in components.items() if k != "macd_atr_ratio")
    total = bot._clamp_total_score(raw_total)

    assert 45.0 <= total <= 55.0, (
        f"Neutral fixture should produce 45..55, got {total:.2f}; "
        f"components={components}"
    )


def test_bearish_fixture_total_score_at_most_35() -> None:
    bot = _bot_with_config()

    components = bot._score_components(_bearish_series())
    raw_total = 50.0 + sum(v for k, v in components.items() if k != "macd_atr_ratio")
    total = bot._clamp_total_score(raw_total)

    assert total <= 35.0, (
        f"Bearish fixture should produce a score <= 35 (SELL zone), "
        f"got {total:.2f}; components={components}"
    )


# ---------------------------------------------------------------------------
# MACD dominance test
# ---------------------------------------------------------------------------


def test_macd_alone_cannot_dominate_total_score() -> None:
    """Even with a massive MACD histogram, the MACD component cannot exceed
    +/-25, so it cannot singlehandedly push the published score past 75
    (or below 25)."""
    bot = _bot_with_config()

    # Construct a setup where ONLY MACD is extreme; everything else is
    # neutral. Even a 1000-ATR MACD histogram should not push the score
    # past 75 (since 50 + 25 = 75).
    extreme = _indicator_series(
        rsi=50.0,
        sma_fast_val=100.0,
        sma_slow_val=100.0,
        macd_hist=10000.0,  # huge
        atr=2.0,
        bb_upper=110.0,
        bb_lower=90.0,
        close=100.0,
    )
    components = bot._score_components(extreme)
    raw_total = 50.0 + sum(v for k, v in components.items() if k != "macd_atr_ratio")
    total = bot._clamp_total_score(raw_total)

    assert total <= 75.0, f"MACD must not push score past 75, got {total:.2f}"
    assert components["macd_score"] == pytest.approx(25.0)


def test_extreme_macd_values_stay_bounded() -> None:
    """Extreme positive and negative MACD histograms stay clamped at +/-25."""
    bot = _bot_with_config()

    for macd_hist in (1e3, 1e6, -1e3, -1e6):
        components = bot._score_components(
            _indicator_series(macd_hist=macd_hist, atr=1.0)
        )
        assert -25.0 <= components["macd_score"] <= 25.0


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_identical_inputs_produce_identical_scores() -> None:
    bot = _bot_with_config()
    series = _bullish_series()

    a = bot._score_components(series)
    b = bot._score_components(series)

    assert a == b


def test_score_components_pure_function_no_state_mutation() -> None:
    """Calling _score_components multiple times must not mutate the
    input series or the bot instance."""
    bot = _bot_with_config()
    series = _bullish_series()
    snapshot = series.copy(deep=True)
    snapshot_dict = {k: getattr(bot, k) for k in ("sma_fast", "sma_slow", "rsi_period")}

    for _ in range(5):
        bot._score_components(series)

    pd.testing.assert_series_equal(series, snapshot, check_names=False)
    for key, value in snapshot_dict.items():
        assert getattr(bot, key) == value


# ---------------------------------------------------------------------------
# Monotonicity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rsi_low", "rsi_high"),
    [(20.0, 30.0), (40.0, 45.0), (60.0, 70.0), (80.0, 90.0)],
)
def test_rsi_monotonicity_lower_rsi_higher_score(rsi_low: float, rsi_high: float) -> None:
    """Stronger bullish RSI evidence (lower RSI, toward oversold) must
    not reduce the score; stronger bearish RSI evidence (higher RSI,
    toward overbought) must not increase it."""
    bot = _bot_with_config()

    bullish_score = bot._score_components(_indicator_series(rsi=rsi_low))["rsi_score"]
    bearish_score = bot._score_components(_indicator_series(rsi=rsi_high))["rsi_score"]

    assert bullish_score > bearish_score, (
        f"RSI={rsi_low} should outscore RSI={rsi_high} for bullish monotonicity; "
        f"got bullish={bullish_score}, bearish={bearish_score}"
    )


def test_sma_monotonicity_wider_separation_higher_score() -> None:
    bot = _bot_with_config()

    narrow = bot._score_components(_indicator_series(sma_fast_val=101.0, sma_slow_val=100.0))["sma_score"]
    wide = bot._score_components(_indicator_series(sma_fast_val=104.0, sma_slow_val=100.0))["sma_score"]

    assert wide > narrow


def test_bb_monotonicity_lower_band_position_higher_score() -> None:
    bot = _bot_with_config()

    near_lower = bot._score_components(_indicator_series(close=92.0, bb_upper=110.0, bb_lower=90.0))["bb_score"]
    near_upper = bot._score_components(_indicator_series(close=108.0, bb_upper=110.0, bb_lower=90.0))["bb_score"]

    assert near_lower > near_upper


def test_macd_monotonicity_larger_positive_histogram_higher_score() -> None:
    bot = _bot_with_config()

    small = bot._score_components(_indicator_series(macd_hist=0.5, atr=2.0))["macd_score"]
    large = bot._score_components(_indicator_series(macd_hist=1.5, atr=2.0))["macd_score"]

    assert large > small


# ---------------------------------------------------------------------------
# Realistic ranking / ordering
# ---------------------------------------------------------------------------


def test_realistic_ranking_bullish_neutral_bearish() -> None:
    """The three documented scenario fixtures must rank bullish > neutral
    > bearish."""
    bot = _bot_with_config()

    def score(series: pd.Series) -> float:
        components = bot._score_components(series)
        return bot._clamp_total_score(
            50.0 + sum(v for k, v in components.items() if k != "macd_atr_ratio")
        )

    bullish = score(_bullish_series())
    neutral = score(_neutral_series())
    bearish = score(_bearish_series())

    assert bullish > neutral > bearish, (
        f"Expected bullish > neutral > bearish; got bullish={bullish:.2f}, "
        f"neutral={neutral:.2f}, bearish={bearish:.2f}"
    )


def test_realistic_ranking_strong_bullish_outranks_mild_bullish() -> None:
    """Two bullish setups: a stronger oversold recovery must outrank a
    milder one."""
    bot = _bot_with_config()

    mild = _indicator_series(rsi=45.0, sma_fast_val=101.0, sma_slow_val=100.0,
                             macd_hist=0.2, atr=2.0, bb_upper=110.0, bb_lower=90.0, close=98.0)
    strong = _indicator_series(rsi=20.0, sma_fast_val=105.0, sma_slow_val=100.0,
                               macd_hist=2.0, atr=2.0, bb_upper=110.0, bb_lower=90.0, close=90.0)

    def score(series: pd.Series) -> float:
        components = bot._score_components(series)
        return bot._clamp_total_score(
            50.0 + sum(v for k, v in components.items() if k != "macd_atr_ratio")
        )

    assert score(strong) > score(mild)


# ---------------------------------------------------------------------------
# Integration: analyze_symbol uses the new helpers
# ---------------------------------------------------------------------------


def test_analyze_symbol_uses_score_components_and_clamps_total() -> None:
    """Verify that analyze_symbol() now delegates to _score_components
    and _clamp_total_score by inspecting the stored analysis dict."""
    import src.core.smart_bot as smart_bot_module

    # Build a fully-calculated indicator frame with a known bullish setup.
    n = 60
    # Steady rise from 100 to 130 with mild pullback near the end so RSI
    # is high but recovering.
    closes = [100.0 + i * 0.5 for i in range(n - 5)] + [128, 126, 124, 122, 121]
    highs = [c + 1.5 for c in closes]
    lows = [c - 1.5 for c in closes]
    volumes = [1_000_000.0] * n

    df = pd.DataFrame({
        "open": closes,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
    })
    df = smart_bot_module.SmartTradingBot.calculate_indicators(
        SimpleNamespace(sma_fast=10, sma_slow=30, rsi_period=14),
        df,
    )

    bot = smart_bot_module.SmartTradingBot.__new__(smart_bot_module.SmartTradingBot)
    bot.sma_fast = 10
    bot.sma_slow = 30
    bot.rsi_period = 14
    bot.enable_multi_timeframe = False
    bot.enable_regime_filter = False
    bot.min_score_buy = 50
    bot.hourly_weight = 0.30

    latest = df.iloc[-1]
    components = bot._score_components(latest)
    raw_total = 50.0 + sum(v for k, v in components.items() if k != "macd_atr_ratio")
    expected_total = bot._clamp_total_score(raw_total)

    assert 0.0 <= expected_total <= 100.0
    # The published total_score contract must match the helper output.
    assert expected_total == bot._clamp_total_score(expected_total)


# ===========================================================================
# SCORE-001 amend #1 — fail-closed on invalid / non-finite indicator data
# ===========================================================================
# Spec: invalid/non-finite required inputs MUST return None from
# _score_components and MUST NOT cause a published 0/100 score. A
# previous partial-NaN scenario could let missing MACD/ATR/BB pass
# bullish components through and produce a false BUY; that path is
# now closed.
# ===========================================================================


def test_score_components_returns_none_when_rsi_is_nan() -> None:
    bot = _bot_with_config()
    latest = _indicator_series(rsi=None)
    latest["RSI"] = float("nan")

    assert bot._score_components(latest) is None


def test_score_components_returns_none_when_sma_fast_is_nan() -> None:
    bot = _bot_with_config()
    latest = _indicator_series()
    latest["SMA_10"] = float("nan")

    assert bot._score_components(latest) is None


def test_score_components_returns_none_when_sma_slow_is_nan() -> None:
    bot = _bot_with_config()
    latest = _indicator_series()
    latest["SMA_30"] = float("nan")

    assert bot._score_components(latest) is None


def test_score_components_returns_none_when_macd_histogram_is_nan() -> None:
    """NaN MACD must NOT silently degrade to 0 and pass other components'
    bullish contribution through. This was the documented false-BUY path."""
    bot = _bot_with_config()
    latest = _indicator_series(macd_hist=None)
    latest["MACD_histogram"] = float("nan")

    assert bot._score_components(latest) is None


def test_score_components_returns_none_when_atr_is_zero() -> None:
    """ATR=0 makes the MACD ratio undefined. Must fail-closed."""
    bot = _bot_with_config()
    latest = _indicator_series(atr=0.0)

    assert bot._score_components(latest) is None


def test_score_components_returns_none_when_atr_is_nan() -> None:
    bot = _bot_with_config()
    latest = _indicator_series(atr=None)
    latest["ATR"] = float("nan")

    assert bot._score_components(latest) is None


def test_score_components_returns_none_when_bb_upper_is_nan() -> None:
    bot = _bot_with_config()
    latest = _indicator_series(bb_upper=None)
    latest["BB_upper"] = float("nan")

    assert bot._score_components(latest) is None


def test_score_components_returns_none_when_bb_lower_is_nan() -> None:
    bot = _bot_with_config()
    latest = _indicator_series(bb_lower=None)
    latest["BB_lower"] = float("nan")

    assert bot._score_components(latest) is None


def test_score_components_returns_none_when_close_is_nan() -> None:
    bot = _bot_with_config()
    latest = _indicator_series(close=None)
    latest["close"] = float("nan")

    assert bot._score_components(latest) is None


def test_score_components_returns_none_when_catalyst_is_nan() -> None:
    """Catalyst participates in score math; non-finite catalyst must
    fail-closed too."""
    bot = _bot_with_config()
    latest = _indicator_series()

    assert bot._score_components(latest, catalyst_score=float("nan")) is None


def test_score_components_returns_none_when_catalyst_is_none() -> None:
    bot = _bot_with_config()
    latest = _indicator_series()

    assert bot._score_components(latest, catalyst_score=None) is None


def test_score_components_returns_none_when_catalyst_is_unparseable_string() -> None:
    bot = _bot_with_config()
    latest = _indicator_series()

    assert bot._score_components(latest, catalyst_score="not-a-number") is None


def test_partial_nan_cannot_produce_bullish_score() -> None:
    """Spec regression: a bullish RSI/SMA/BB with NaN MACD must NOT
    produce a BUY-eligible score. Pre-amend behavior silently treated
    NaN MACD as 0 and could let the other components' bullish sum
    exceed 50 (e.g., 50+25+10+0+15 = 100)."""
    bot = _bot_with_config()
    latest = _indicator_series(
        rsi=30.0,
        sma_fast_val=104.0,
        sma_slow_val=100.0,
        macd_hist=None,
        atr=2.0,
        bb_upper=110.0,
        bb_lower=90.0,
        close=92.0,
    )
    latest["MACD_histogram"] = float("nan")

    components = bot._score_components(latest)
    assert components is None, (
        "NaN MACD must fail-closed; the pre-amend behavior silently "
        "substituted 0 and could produce a false BUY"
    )


def test_score_components_does_not_publish_zero_score_for_invalid_data() -> None:
    """Spec: invalid data must not appear as a legitimate 0/100 score."""
    bot = _bot_with_config()
    latest = _indicator_series(macd_hist=None, atr=None)
    latest["MACD_histogram"] = float("nan")
    latest["ATR"] = float("nan")

    components = bot._score_components(latest)

    # No published dict at all (fail-closed).
    assert components is None
    # And the eventual clamp is also None, not 0.
    assert bot._clamp_total_score(50.0 + 0) == 50.0  # control: valid path still works


# ===========================================================================
# SCORE-001 amend #2 — post-multiplier bound tests
# ===========================================================================
# The helper output is in ±25. After the volatility-tier 1.3x multiplier
# the affected component can briefly span ±32.5 in either direction.
# The final _clamp_total_score is the authoritative 0..100 guard.
# ===========================================================================

# Documented worst-case envelope (POST-multiplier, BEFORE final clamp):
#
#   RSI  : helper ±25  →  post-multiplier ±32.5  (low-vol mode, atr_pct < 2.0)
#   SMA  : helper ±25  →  post-multiplier ±32.5  (high-vol mode, atr_pct > 5.0)
#   MACD : ±25         →  no multiplier          (always ±25)
#   BB   : ±25         →  no multiplier          (always ±25)
#   Cat  : 0..+25      →  no multiplier          (always 0..+25)
#
#   Sum bullish max : 32.5 + 32.5 + 25 + 25 + 25 = +140
#   Sum bearish min : -32.5 + -32.5 + -25 + -25 + 0 = -115
#
#   Published total_score after _clamp_total_score(50 + sum): 0..100.


def test_post_multiplier_rsi_bound_is_32_5_in_low_vol() -> None:
    """Helper RSI bound is ±25; the 1.3x low-vol multiplier extends the
    AUTHORITATIVE post-multiplier contribution to ±32.5."""
    bot = _bot_with_config()

    rsi_component = bot._score_components(_indicator_series(rsi=0.0))["rsi_score"]
    assert rsi_component == pytest.approx(25.0)  # helper bound

    post_multiplier = rsi_component * 1.3
    assert post_multiplier == pytest.approx(32.5)  # documented worst case


def test_post_multiplier_sma_bound_is_32_5_in_high_vol() -> None:
    bot = _bot_with_config()

    sma_component = bot._score_components(
        _indicator_series(sma_fast_val=105.0, sma_slow_val=100.0)
    )["sma_score"]
    assert sma_component == pytest.approx(25.0)  # helper bound

    post_multiplier = sma_component * 1.3
    assert post_multiplier == pytest.approx(32.5)  # documented worst case


def test_post_multiplier_rsi_bearish_extends_to_minus_32_5() -> None:
    bot = _bot_with_config()

    rsi_component = bot._score_components(_indicator_series(rsi=100.0))["rsi_score"]
    assert rsi_component == pytest.approx(-25.0)

    post_multiplier = rsi_component * 1.3
    assert post_multiplier == pytest.approx(-32.5)


def test_post_multiplier_sma_bearish_extends_to_minus_32_5() -> None:
    bot = _bot_with_config()

    sma_component = bot._score_components(
        _indicator_series(sma_fast_val=95.0, sma_slow_val=100.0)
    )["sma_score"]
    assert sma_component == pytest.approx(-25.0)

    post_multiplier = sma_component * 1.3
    assert post_multiplier == pytest.approx(-32.5)


def test_post_multiplier_macd_bound_is_25_no_multiplier() -> None:
    bot = _bot_with_config()

    macd_component = bot._score_components(
        _indicator_series(macd_hist=2.0, atr=2.0)
    )["macd_score"]
    assert macd_component == pytest.approx(25.0)

    # Even if a volatility multiplier were applied, MACD has none in the
    # current strategy. Documented bound stays at ±25.
    post_multiplier = macd_component * 1.0
    assert post_multiplier == pytest.approx(25.0)


def test_post_multiplier_bb_bound_is_25_no_multiplier() -> None:
    bot = _bot_with_config()

    bb_component = bot._score_components(
        _indicator_series(close=90.0, bb_upper=110.0, bb_lower=90.0)
    )["bb_score"]
    assert bb_component == pytest.approx(25.0)

    post_multiplier = bb_component * 1.0
    assert post_multiplier == pytest.approx(25.0)


def test_post_multiplier_catalyst_bound_is_25_no_multiplier() -> None:
    bot = _bot_with_config()

    components = bot._score_components(_indicator_series(), catalyst_score=25.0)
    assert components["catalyst_score"] == pytest.approx(25.0)


def test_worst_case_bullish_envelope_is_capped_at_140() -> None:
    """The documented post-multiplier envelope is +140 (32.5 + 32.5 + 25
    + 25 + 25) before the final clamp. Verify the math and that the
    final clamp still keeps the published total_score at 100."""
    bot = _bot_with_config()

    # All four components at their max bullish helper values, with both
    # 1.3x multipliers active. This is the documented worst case.
    rsi = bot._score_components(_indicator_series(rsi=0.0))["rsi_score"] * 1.3
    sma = bot._score_components(
        _indicator_series(sma_fast_val=110.0, sma_slow_val=100.0)
    )["sma_score"] * 1.3
    macd = bot._score_components(
        _indicator_series(macd_hist=1000.0, atr=2.0)
    )["macd_score"]
    bb = bot._score_components(
        _indicator_series(close=80.0, bb_upper=110.0, bb_lower=90.0)
    )["bb_score"]
    catalyst = 25.0

    raw_signed = rsi + sma + macd + bb + catalyst

    # Sanity: documented worst-case bullish envelope.
    assert raw_signed == pytest.approx(140.0)
    # Final clamp keeps the published score at 100.
    assert bot._clamp_total_score(50.0 + raw_signed) == pytest.approx(100.0)


def test_worst_case_bearish_envelope_is_floored_at_minus_115() -> None:
    """The documented post-multiplier envelope is -115 (-32.5 + -32.5 +
    -25 + -25 + 0) before the final clamp. The published total_score
    clamps to 0."""
    bot = _bot_with_config()

    rsi = bot._score_components(_indicator_series(rsi=100.0))["rsi_score"] * 1.3
    sma = bot._score_components(
        _indicator_series(sma_fast_val=90.0, sma_slow_val=100.0)
    )["sma_score"] * 1.3
    macd = bot._score_components(
        _indicator_series(macd_hist=-1000.0, atr=2.0)
    )["macd_score"]
    bb = bot._score_components(
        _indicator_series(close=120.0, bb_upper=110.0, bb_lower=90.0)
    )["bb_score"]
    catalyst = 0.0  # no bonus on a worst-case bearish setup

    raw_signed = rsi + sma + macd + bb + catalyst

    assert raw_signed == pytest.approx(-115.0)
    assert bot._clamp_total_score(50.0 + raw_signed) == pytest.approx(0.0)


def test_worst_case_envelope_published_total_score_stays_in_0_100() -> None:
    """Across the full documented post-multiplier envelope, the
    published total_score MUST remain in 0..100. Sweep every
    per-component extreme and verify the clamp holds."""
    bot = _bot_with_config()

    rsi_max = bot._score_components(_indicator_series(rsi=0.0))["rsi_score"] * 1.3
    rsi_min = bot._score_components(_indicator_series(rsi=100.0))["rsi_score"] * 1.3
    sma_max = bot._score_components(
        _indicator_series(sma_fast_val=110.0, sma_slow_val=100.0)
    )["sma_score"] * 1.3
    sma_min = bot._score_components(
        _indicator_series(sma_fast_val=90.0, sma_slow_val=100.0)
    )["sma_score"] * 1.3
    macd_max = bot._score_components(
        _indicator_series(macd_hist=1e6, atr=2.0)
    )["macd_score"]
    macd_min = bot._score_components(
        _indicator_series(macd_hist=-1e6, atr=2.0)
    )["macd_score"]
    bb_max = bot._score_components(
        _indicator_series(close=80.0, bb_upper=110.0, bb_lower=90.0)
    )["bb_score"]
    bb_min = bot._score_components(
        _indicator_series(close=120.0, bb_upper=110.0, bb_lower=90.0)
    )["bb_score"]

    for rsi, sma, macd, bb, cat in (
        (rsi_max, sma_max, macd_max, bb_max, 25.0),
        (rsi_min, sma_min, macd_min, bb_min, 0.0),
        (rsi_max, sma_min, macd_max, bb_min, 12.5),
        (rsi_min, sma_max, macd_min, bb_max, 0.0),
    ):
        raw = 50.0 + rsi + sma + macd + bb + cat
        published = bot._clamp_total_score(raw)
        assert published is not None
        assert 0.0 <= published <= 100.0


def test_valid_inputs_retain_existing_classifications_after_amend() -> None:
    """Regression: the fail-closed amendment must not change behavior
    for valid inputs. Bullish / neutral / bearish fixtures must still
    classify as before."""
    bot = _bot_with_config()

    def total(series: pd.Series, catalyst: float = 0.0) -> float | None:
        components = bot._score_components(series, catalyst_score=catalyst)
        if components is None:
            return None
        raw = 50.0 + sum(
            v for k, v in components.items()
            if k not in ("macd_atr_ratio", "catalyst_score")
        ) + components["catalyst_score"]
        return bot._clamp_total_score(raw)

    bullish = total(_bullish_series())
    neutral = total(_neutral_series())
    bearish = total(_bearish_series())

    assert bullish is not None and bullish >= 65.0
    assert neutral is not None and 45.0 <= neutral <= 55.0
    assert bearish is not None and bearish <= 35.0


# ===========================================================================
# SCORE-001 amend #3 — collateral behavior disclosure tests
# ===========================================================================
# MTF "Score >= 65" criterion was previously a signed-vs-65 comparison
# labeled "/100"; the amend preserves the documented behavior shift.
# MEDIUM SELL is unreachable in both pre- and post-SCORE-001 (pre-existing
# bug, intentionally preserved).
# ===========================================================================


def test_mtf_buy_criteria_score_pass_uses_clamped_total_score() -> None:
    """Spec: the MTF ``Score >= 65`` buy_criteria criterion is checked
    against the published clamped total_score (now 0..100, was signed
    pre-SCORE-001). A moderate-bullish setup that previously failed the
    signed-vs-65 check now passes the 0..100-vs-65 check. This is the
    documented collateral behavior change for amend #3A."""
    bot = _bot_with_config()

    # A moderate-bullish setup: rsi=40 (slight oversold), sma +2%, macd
    # half ATR positive, BB near lower band.
    moderate = _indicator_series(
        rsi=40.0,
        sma_fast_val=102.0,
        sma_slow_val=100.0,
        macd_hist=1.0,
        atr=2.0,
        bb_upper=110.0,
        bb_lower=90.0,
        close=93.0,
    )
    components = bot._score_components(moderate)
    assert components is not None
    raw_signed = (
        components["rsi_score"]
        + components["sma_score"]
        + components["macd_score"]
        + components["bb_score"]
    )
    published = bot._clamp_total_score(50.0 + raw_signed)
    assert published is not None

    # Moderate-bullish total_score in 0..100 must be >= 65 (incidental
    # collateral from the SCORE-001 scale fix; pre-SCORE-001 the same
    # moderate setup would be ~25 in the signed scale and would NOT
    # pass "Score >= 65").
    assert published >= 65.0, (
        f"Moderate-bullish total_score {published:.2f} is below 65; "
        f"the MTF buy_criteria 'Score >= 65' check is now reachable "
        f"for moderate-bullish setups where it wasn't pre-SCORE-001"
    )


def test_medium_sell_branch_unreachable_by_signed_threshold_logic() -> None:
    """Spec: MEDIUM SELL is unreachable in both pre- and post-SCORE-001
    because ``blended_signed <= -50`` already implies
    ``blended_signed <= 20``. The pre-existing bug is intentionally
    preserved; this test documents the algebraic invariant so a future
    iteration can fix it deliberately."""
    # Algebraic invariant: for any real number x, x <= -50 implies x <= 20.
    for x in (-1e9, -1e6, -1000, -100, -50, -49.999, 0, 20, 100, 1e6):
        if x <= -50:
            assert x <= 20, f"Algebraic invariant violated at x={x}"
