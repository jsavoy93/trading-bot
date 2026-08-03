from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from src.core.smart_bot import SmartTradingBot


def _calculate(frame: pd.DataFrame) -> pd.DataFrame:
    """Exercise the dataframe calculation without constructing service clients."""
    indicator_config = SimpleNamespace(sma_fast=10, sma_slow=30, rsi_period=14)
    return SmartTradingBot.calculate_indicators(indicator_config, frame)


def _market_frame(closes: list[float], volumes: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": closes,
            "high": [close + 1.0 for close in closes],
            "low": [close - 1.0 for close in closes],
            "close": closes,
            "volume": volumes,
        }
    )


def test_calculate_indicators_preserves_raw_volume_and_uses_current_bar_in_sma() -> None:
    volumes = [100.0] * 29 + [300.0]

    result = _calculate(_market_frame([100.0 + index for index in range(30)], volumes))

    assert result["volume"].tolist() == volumes
    assert result.iloc[-1]["volume"] == 300.0
    assert result.iloc[-1]["volume_sma_20"] == pytest.approx(110.0)
    assert result.iloc[-1]["volume_ratio"] == pytest.approx(300.0 / 110.0)


def test_calculate_indicators_constant_volume_is_one_times_average() -> None:
    result = _calculate(
        _market_frame([100.0 + index for index in range(30)], [250.0] * 30)
    )

    assert result.iloc[-1]["volume"] == 250.0
    assert result.iloc[-1]["volume_sma_20"] == pytest.approx(250.0)
    assert result.iloc[-1]["volume_ratio"] == pytest.approx(1.0)


def test_calculate_indicators_volume_history_and_zero_average_remain_nan() -> None:
    result = _calculate(
        _market_frame([100.0 + index for index in range(30)], [0.0] * 30)
    )

    assert result["volume_sma_20"].iloc[:19].isna().all()
    assert result["volume_ratio"].iloc[:19].isna().all()
    assert result.iloc[19:]["volume_sma_20"].eq(0.0).all()
    assert result.iloc[19:]["volume_ratio"].isna().all()
    assert not np.isinf(result["volume_ratio"].to_numpy()).any()


@pytest.mark.parametrize(
    ("case", "closes", "volumes", "expected"),
    [
        (
            "bullish",
            [100.0 + index for index in range(60)],
            [1000.0 + 10.0 * index for index in range(60)],
            {
                "SMA_10": 154.5,
                "SMA_30": 144.5,
                "RSI": 100.0,
                "MACD": 6.866964287009332,
                "MACD_signal": 6.804975589431558,
                "MACD_histogram": 0.06198869757777393,
                "BB_middle": 149.5,
                "BB_upper": 161.33215956619924,
                "BB_lower": 137.66784043380076,
                "volume_sma_20": 1495.0,
                "volume_ratio": 1590.0 / 1495.0,
            },
        ),
        (
            "neutral",
            [100.0 + (1.0 if index % 2 else -1.0) for index in range(60)],
            [1000.0] * 60,
            {
                "SMA_10": 100.0,
                "SMA_30": 100.0,
                "RSI": 50.0,
                "MACD": 0.05507943646978501,
                "MACD_signal": 0.019895203513925875,
                "MACD_histogram": 0.035184232955859135,
                "BB_middle": 100.0,
                "BB_upper": 102.0519567041703,
                "BB_lower": 97.9480432958297,
                "volume_sma_20": 1000.0,
                "volume_ratio": 1.0,
            },
        ),
        (
            "bearish",
            [160.0 - index for index in range(60)],
            [1600.0 - 10.0 * index for index in range(60)],
            {
                "SMA_10": 105.5,
                "SMA_30": 115.5,
                "RSI": 0.0,
                "MACD": -6.866964287009332,
                "MACD_signal": -6.804975589431558,
                "MACD_histogram": -0.06198869757777393,
                "BB_middle": 110.5,
                "BB_upper": 122.33215956619924,
                "BB_lower": 98.66784043380076,
                "volume_sma_20": 1105.0,
                "volume_ratio": 1010.0 / 1105.0,
            },
        ),
    ],
)
def test_calculate_indicators_known_values(
    case: str,
    closes: list[float],
    volumes: list[float],
    expected: dict[str, float],
) -> None:
    result = _calculate(_market_frame(closes, volumes))
    latest = result.iloc[-1]

    assert case in {"bullish", "neutral", "bearish"}
    for column, expected_value in expected.items():
        assert latest[column] == pytest.approx(expected_value, rel=1e-12, abs=1e-12)


def test_calculate_indicators_short_frame_returns_unchanged() -> None:
    frame = _market_frame([100.0 + index for index in range(29)], [1000.0] * 29)
    original = frame.copy(deep=True)

    result = _calculate(frame)

    assert result is frame
    pd.testing.assert_frame_equal(result, original)
    assert "volume_sma_20" not in result.columns
    assert "volume_ratio" not in result.columns
