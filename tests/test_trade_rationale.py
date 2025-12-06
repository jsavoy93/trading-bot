#!/usr/bin/env python3
"""Quick sanity checks for trade rationale text formatting."""

import sys
from pathlib import Path

# Ensure src is on path for local tests
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.rationale import format_buy_rationale, format_sell_rationale


def test_buy_message_price_below_fast():
    lines = format_buy_rationale(price=12.13, sma_fast=12.63, sma_slow=12.52, rsi=21.3, signal_strength="STRONG")
    msg = "\n".join(lines)
    assert "below short-term avg" in msg
    assert "Fast > Slow bullish crossover" in msg
    assert "RSI (21.3): OVERSOLD" in msg
    assert "STRONG signal" in msg


def test_buy_message_price_above_fast():
    lines = format_buy_rationale(price=15.00, sma_fast=14.50, sma_slow=14.20, rsi=55.0, signal_strength="MEDIUM")
    msg = "\n".join(lines)
    assert "above short-term avg" in msg
    assert "Fast > Slow bullish crossover" in msg
    assert "RSI (55.0): NEUTRAL" in msg
    assert "MEDIUM signal" in msg


def test_sell_message_price_above_fast_bearish_crossover():
    lines = format_sell_rationale(price=10.00, sma_fast=9.50, sma_slow=10.50, rsi=75.0)
    msg = "\n".join(lines)
    assert "above short-term avg" in msg
    assert "Fast < Slow bearish crossover" in msg
    assert "RSI (75.0): OVERBOUGHT" in msg


def test_sell_message_price_below_fast_bearish_crossover():
    lines = format_sell_rationale(price=9.00, sma_fast=9.50, sma_slow=10.50, rsi=40.0)
    msg = "\n".join(lines)
    assert "below short-term avg" in msg
    assert "Fast < Slow bearish crossover" in msg
    assert "RSI (40.0): NEUTRAL" in msg


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__]))
