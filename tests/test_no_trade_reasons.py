#!/usr/bin/env python3
"""Test script to demonstrate the new no-trade reasons feature"""

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

print("\n" + "="*70)
print("🧪 TESTING NO-TRADE REASONS FEATURE")
print("="*70)
print("Running a trading session with 20 major stocks and no AI...")
print("This will demonstrate the detailed per-ticker failure reasons.\n")

# Run a single trading session with more symbols
bot.run_analysis(max_symbols=20, max_trades=2, use_ai=False)

print("\n" + "="*70)
print("✅ Test complete! Check the output above for detailed no-trade reasons.")
print("="*70)
