#!/usr/bin/env python3
"""Test volume shelf analysis"""
import sys
import os
from pathlib import Path
import pandas as pd

# Add src to path
src_path = str(Path(__file__).parent / "src")
sys.path.insert(0, src_path)

from dotenv import load_dotenv
load_dotenv()

# Enable advanced mode + volume shelves
os.environ["USE_ADVANCED_SIGNALS"] = "true"
os.environ["USE_VOLUME_SHELVES"] = "true"

from core.smart_bot import SmartTradingBot

print("🧪 Testing Volume Shelf Analysis...")
print("=" * 60)

try:
    bot = SmartTradingBot()
    print(f"✅ Bot initialized")
    print(f"   Advanced signals: {bot.use_advanced_signals}")
    print(f"   Volume shelves: {bot.use_volume_shelves}")
    print()
    
    # Test getting market data
    print("📊 Fetching market data for AAPL...")
    df = bot.get_market_data("AAPL")
    
    if df is None:
        print("⚠️  No market data available (market may be closed)")
        sys.exit(0)
    
    print(f"✅ Got {len(df)} bars")
    print()
    
    # Calculate indicators
    print("🔢 Calculating indicators...")
    df = bot.calculate_indicators(df)
    print(f"✅ Indicators calculated")
    print()
    
    # Compute volume profile
    print("📈 Computing volume profile...")
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
    
    if profile is not None:
        print(f"✅ Volume profile computed ({len(profile)} price bins)")
        print(f"   Price range: ${profile['price_bin_mid'].min():.2f} - ${profile['price_bin_mid'].max():.2f}")
        print(f"   Total volume: {profile['volume'].sum():,.0f}")
        print()
        
        # Find volume shelves
        print("🏢 Finding volume shelves...")
        shelves = strategy.find_volume_shelves(profile, top_k=3, prominence_pct=0.5)
        
        if not shelves.empty:
            print(f"✅ Found {len(shelves)} volume shelves:")
            current_price = df['close'].iloc[-1]
            for idx, shelf in shelves.iterrows():
                price = shelf['price_bin_mid']
                vol = shelf['volume']
                dist = (price - current_price) / current_price * 100
                position = "SUPPORT" if price < current_price else "RESISTANCE"
                print(f"   • ${price:7.2f} ({position:10}) - Volume: {vol:10,.0f} - {abs(dist):5.2f}% {'below' if dist < 0 else 'above'}")
        else:
            print("⚠️  No significant volume shelves found")
        print()
    
    # Test full analysis with shelves
    print("🎯 Testing analyze_symbol() with volume shelves...")
    result = bot.analyze_symbol("AAPL", use_ai=False)
    
    if result:
        print(f"✅ Analysis successful")
        print(f"   Signal: {result.get('signal')}")
        print(f"   Strength: {result.get('signal_strength')}")
        print(f"   Price: ${result.get('price'):.2f}")
        
        if 'reasons' in result and result['reasons']:
            print(f"   Reasons:")
            for reason in result['reasons']:
                print(f"      • {reason}")
    else:
        print("⚠️  No analysis result")
    
    print()
    print("=" * 60)
    print("✅ Volume shelf test completed successfully!")
    
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
