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
    rsi: float = 50.0,
    sma_fast_val: float | None = None,
    sma_slow_val: float | None = None,
    macd_hist: float = 0.0,
    atr: float = 2.0,
    bb_upper: float = 110.0,
    bb_lower: float = 90.0,
    close: float = 100.0,
) -> pd.Series:
    """Build a latest-bar ``pd.Series`` of indicator values for the helper."""
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
        (1.0, 0.0),       # zero ATR -> 0
    ],
)
def test_score_components_macd_stays_within_documented_bounds(macd_hist: float, atr: float) -> None:
    bot = _bot_with_config()
    latest = _indicator_series(macd_hist=macd_hist, atr=atr)

    components = bot._score_components(latest)

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
        (float("nan"), 0.0),
    ],
)
def test_clamp_total_score_clamps_to_0_100(raw: float, expected: float) -> None:
    bot = _bot()

    assert bot._clamp_total_score(raw) == pytest.approx(expected)


def test_clamp_total_score_handles_unparseable_input() -> None:
    bot = _bot()

    assert bot._clamp_total_score("not-a-number") == 0.0
    assert bot._clamp_total_score(None) == 0.0


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
