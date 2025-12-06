"""
Trade rationale formatting helpers to keep messaging consistent and testable.
"""
from typing import List


ALLOCATION_MAP = {
    'AI_ENHANCED': '2.5%',
    'STRONG': '2.0%',
    'MEDIUM': '1.5%',
    'WEAK': '1.0%',
    'CONFLICTED': '0.5%'
}


def format_buy_rationale(price: float, sma_fast: float, sma_slow: float, rsi: float, signal_strength: str) -> List[str]:
    """Return bullet lines describing BUY rationale aligned with signal math."""
    price_vs_fast = "above" if price >= sma_fast else "below"
    crossover_note = "(Fast > Slow bullish crossover)" if sma_fast > sma_slow else "(Fast ≤ Slow)"
    rsi_text = (
        "OVERSOLD - Good entry point" if rsi < 30 else
        "NEUTRAL - Momentum building" if rsi < 70 else
        "STRONG MOMENTUM"
    )
    target_allocation = ALLOCATION_MAP.get(signal_strength, '1.0%')

    return [
        f"BULLISH: Price (${price:.2f}) {price_vs_fast} short-term avg (${sma_fast:.2f}) {crossover_note}",
        f"RSI ({rsi:.1f}): {rsi_text}",
        f"SIZING: {signal_strength} signal → {target_allocation} portfolio allocation",
    ]


def format_sell_rationale(price: float, sma_fast: float, sma_slow: float, rsi: float) -> List[str]:
    """Return bullet lines describing SELL rationale aligned with signal math."""
    price_vs_fast = "below" if price <= sma_fast else "above"
    crossover_note = "(Fast < Slow bearish crossover)" if sma_fast < sma_slow else "(Fast ≥ Slow)"
    rsi_text = (
        "OVERBOUGHT - Good exit point" if rsi > 70 else
        "NEUTRAL - Weakness showing" if rsi > 30 else
        "OVERSOLD"
    )

    return [
        f"BEARISH: Price (${price:.2f}) {price_vs_fast} short-term avg (${sma_fast:.2f}) {crossover_note}",
        f"RSI ({rsi:.1f}): {rsi_text}",
    ]


__all__ = [
    "format_buy_rationale",
    "format_sell_rationale",
]
