"""
NOBUY-FIX-001 FIX 3 — HOLD/WEAK log lines must show real indicator values.

Before the fix, analysis_result never carried macd / bb_pos / vwap_distance /
volatility_tier / atr_pct from the multi-timeframe analyzer, and the log
path hardcoded bb_pos = 50 and defaulted the rest to 0/'mid', so ~98% of
HOLD/WEAK lines printed "MACD:+0.00 | BB:50% | VWAP:+0.0% | Vol:mid(0.0%)".
"""

import logging

import numpy as np
import pandas as pd

from src.core.smart_bot import SmartTradingBot, _format_hold_log_line


def _daily_bars(n=60):
    """Bars with a clearly non-zero MACD, a known BB position and VWAP gap."""
    rng = np.random.default_rng(7)
    price = 100.0
    rows = []
    for i in range(n):
        price = price * (1 + 0.004)  # steady uptrend -> positive MACD
        rows.append({
            "timestamp": pd.Timestamp("2026-11-20", tz="UTC") + pd.Timedelta(days=i),
            "open": price - 0.5,
            "high": price + 1.0,
            "low": price - 1.0,
            "close": price + rng.uniform(-0.2, 0.2),
            "volume": 2_000_000,
        })
    return pd.DataFrame(rows)


def _make_bot(monkeypatch):
    bot = SmartTradingBot.__new__(SmartTradingBot)
    bot.sma_fast = 10
    bot.sma_slow = 30
    bot.rsi_period = 14
    bot.rsi_buy_threshold = 30
    bot.rsi_sell_threshold = 70
    bot.enable_multi_timeframe = True
    bot._last_score_error_reason = None
    bot._last_mtf_outcome = None
    bot._last_mtf_reason = None
    bot._last_mtf_daily_signal = None
    bot._last_mtf_hourly_signal = None
    bot._last_mtf_hourly_data_available = False
    bot.use_ai_for_ticker_analysis = False
    # MTF combination reads these filter flags directly.
    bot.enable_mtf_conflict_filter = True
    bot.enable_vol_downgrade_filter = True
    bot.enable_ai_conflict_filter = True
    bot.enable_volume_confirmation = True
    bot.check_volume_confirmation = lambda df: (True, 1.5, "")
    bot.get_market_data = lambda s: _daily_bars()
    bot.get_hourly_market_data = lambda s, lookback_hours=120: None
    bot.mark_recent_research = lambda s: None
    return bot


def test_analysis_result_carries_real_display_values(monkeypatch):
    bot = _make_bot(monkeypatch)
    analysis = bot.analyze_multi_timeframe("TEST", use_ai=False)
    assert analysis is not None
    for key in ("macd", "bb_pos", "vwap_distance", "volatility_tier", "atr_pct"):
        assert key in analysis, key
    assert analysis["macd"] is not None and analysis["macd"] != 0
    assert analysis["bb_pos"] is not None
    # Uptrend close near the upper band => a real (non-50) position.
    assert analysis["bb_pos"] > 50
    assert analysis["vwap_distance"] is not None
    assert analysis["volatility_tier"] in ("low", "mid", "high")
    assert analysis["atr_pct"] is not None and analysis["atr_pct"] > 0


def test_log_line_shows_real_values(caplog):
    analysis = {
        "symbol": "NKE",
        "price": 101.0,
        "rsi": 44.2,
        "total_score": 61.0,
        "macd": 0.42,
        "bb_upper": 105.0,
        "bb_lower": 95.0,
        "bb_pos": 60.0,
        "vwap_distance": 1.75,
        "volatility_tier": "high",
        "atr_pct": 6.3,
        "catalyst_score": 2,
    }
    with caplog.at_level(logging.INFO, logger=logging.getLogger().name):
        logging.info(_format_hold_log_line("NKE", analysis))
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "MACD:+0.42" in text
    assert "BB:60%" in text
    assert "VWAP:+1.8%" in text
    assert "Vol:high(6.3%)" in text
    assert "MACD:+0.00" not in text and "BB:50%" not in text


def test_log_line_bb_pos_derived_when_absent():
    analysis = {
        "symbol": "AAPL", "price": 90.0, "rsi": 40, "total_score": 55,
        "macd": -0.25, "bb_upper": 100.0, "bb_lower": 80.0,
        "vwap_distance": -0.5, "volatility_tier": "low", "atr_pct": 1.2,
    }
    line = _format_hold_log_line("AAPL", analysis)
    assert "BB:50%" in line  # (90-80)/(100-80) = 50%
    assert "MACD:-0.25" in line
    assert "Vol:low(1.2%)" in line


def test_log_line_defaults_only_for_missing_values():
    line = _format_hold_log_line("XYZ", {"symbol": "XYZ", "price": 10.0})
    assert "MACD:n/a" in line
    assert "BB:50%" in line          # bands missing => documented default
    assert "VWAP:n/a" in line
    assert "Vol:mid(n/a%)" in line
