#!/usr/bin/env python3
"""Test script to show per-ticker failure criteria on major stocks"""

import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.smart_bot import SmartTradingBot

# Create bot instance
bot = SmartTradingBot()

# Disable all AI features for faster testing
bot.use_ai_for_ticker_analysis = False
bot.use_ai_for_ticker_selection = False
bot.use_ai_for_market_summary = False

print("\n" + "="*80)
print("🧪 TESTING PER-TICKER FAILURE CRITERIA")
print("="*80)
print("Analyzing major liquid stocks to show which criteria each ticker fails...\n")

# Test with well-known liquid stocks
test_symbols = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'TSLA', 'META', 'NVDA', 'AMD', 
                'NFLX', 'DIS', 'JPM', 'BAC', 'WMT', 'PG', 'KO', 'PFE', 'T', 'VZ']

print(f"📊 Testing {len(test_symbols)} major stocks: {', '.join(test_symbols)}\n")

# Analyze each symbol individually
for symbol in test_symbols:
    try:
        analysis = bot.analyze_symbol(symbol, use_ai=False)
        if not analysis:
            print(f"   ⏭️  {symbol}: ❌ Insufficient market data (needs {bot.sma_slow} bars minimum)")
        elif analysis['signal']:
            print(f"   ✅ {symbol}: {analysis['signal']} signal ({analysis['signal_strength']}) - "
                  f"RSI: {analysis['rsi']:.1f}, Price: ${analysis['price']:.2f}")
        else:
            # Show which criteria failed
            rsi = analysis['rsi']
            sma_fast = analysis['sma_fast']
            sma_slow = analysis['sma_slow']
            
            failed_criteria = []
            if sma_fast <= sma_slow:
                failed_criteria.append(f"SMA bearish (Fast ${sma_fast:.2f} ≤ Slow ${sma_slow:.2f})")
            if rsi >= bot.rsi_buy_threshold:
                failed_criteria.append(f"RSI not oversold ({rsi:.1f} ≥ {bot.rsi_buy_threshold})")
            
            # Check sell criteria
            if sma_fast >= sma_slow and rsi <= bot.rsi_sell_threshold:
                failed_criteria = [f"RSI not overbought ({rsi:.1f} ≤ {bot.rsi_sell_threshold})"]
            
            failure_msg = ", ".join(failed_criteria) if failed_criteria else f"No clear trend (RSI: {rsi:.1f})"
            print(f"   ⏭️  {symbol}: ⊗ {failure_msg}")
    except Exception as e:
        print(f"   ❌ {symbol}: Error - {e}")

print("\n" + "="*80)
print("✅ Test complete! Each ticker shows exactly which criteria it failed.")
print("="*80)
