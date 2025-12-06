#!/usr/bin/env python3
"""Test scored signal evaluation"""
import sys
import os
from pathlib import Path
import pandas as pd

# Add src to path
src_path = str(Path(__file__).parent / "src")
sys.path.insert(0, src_path)

from dotenv import load_dotenv
load_dotenv()

# Enable advanced mode + scored signals
os.environ["USE_ADVANCED_SIGNALS"] = "true"
os.environ["USE_SCORED_SIGNALS"] = "true"

from core.smart_bot import SmartTradingBot

print("🧪 Testing Scored Signal Evaluation...")
print("=" * 70)

try:
    bot = SmartTradingBot()
    print(f"✅ Bot initialized")
    print(f"   Advanced signals: {bot.use_advanced_signals}")
    print(f"   Scored signals: {bot.use_scored_signals}")
    print()
    
    # Test with multiple symbols
    test_symbols = ["AAPL", "TSLA", "MSFT"]
    
    for symbol in test_symbols:
        print(f"\n{'=' * 70}")
        print(f"📊 Testing {symbol}")
        print(f"{'=' * 70}")
        
        df = bot.get_market_data(symbol)
        
        if df is None:
            print(f"⚠️  No market data available for {symbol}")
            continue
        
        print(f"✅ Got {len(df)} bars")
        
        # Calculate indicators
        df = bot.calculate_indicators(df)
        
        # Get volume profile and shelves
        from trading.strategy import TechnicalStrategy, StrategyConfig
        
        config = StrategyConfig(
            sma_fast=bot.sma_fast,
            sma_slow=bot.sma_slow,
            rsi_period=bot.rsi_period,
            rsi_buy_threshold=bot.rsi_buy_threshold,
            rsi_sell_threshold=bot.rsi_sell_threshold
        )
        strategy = TechnicalStrategy(config=config)
        
        profile = strategy.compute_volume_profile(df, lookback=100, n_bins=24)
        shelves = None
        if profile is not None:
            shelves = strategy.find_volume_shelves(profile, top_k=3, prominence_pct=0.5)
        
        # Evaluate using scored system
        last_row = df.iloc[-1]
        signal, strength, reasons, score = strategy.evaluate_signal_scored(last_row, shelves=shelves)
        
        print(f"\n🎯 SCORED EVALUATION RESULTS:")
        print(f"   Signal: {signal if signal else 'None'}")
        print(f"   Strength: {strength}")
        print(f"   Score: {score:.1f}")
        print(f"   Price: ${last_row['close']:.2f}")
        
        print(f"\n   Scoring Breakdown:")
        for reason in reasons:
            print(f"      • {reason}")
    
    print()
    print("=" * 70)
    print("✅ Scored signal test completed!")
    
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
