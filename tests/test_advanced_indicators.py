#!/usr/bin/env python3
"""Quick test to verify advanced indicators are calculated correctly"""
import sys
import os
from pathlib import Path
import pandas as pd

# Add src to path
src_path = str(Path(__file__).parent / "src")
sys.path.insert(0, src_path)

from dotenv import load_dotenv
load_dotenv()

# Set advanced mode
os.environ["USE_ADVANCED_SIGNALS"] = "true"

from core.smart_bot import SmartTradingBot

print("🧪 Testing Advanced Indicators...")
print("=" * 60)

try:
    bot = SmartTradingBot()
    print(f"✅ Bot initialized")
    print(f"   Advanced signals: {bot.use_advanced_signals}")
    print(f"   ATR exits: {bot.use_atr_exits}")
    print(f"   ATR sizing: {bot.use_atr_sizing}")
    print()
    
    # Test getting market data and calculating indicators
    print("📊 Fetching market data for AAPL...")
    df = bot.get_market_data("AAPL")
    
    if df is None:
        print("⚠️  No market data available (market may be closed)")
        sys.exit(0)
    
    print(f"✅ Got {len(df)} bars")
    print(f"   Columns: {list(df.columns)}")
    print()
    
    print("🔢 Calculating indicators...")
    df = bot.calculate_indicators(df)
    
    # Check which indicators are present
    indicators = ['SMA_10', 'SMA_30', 'SMA_200', 'RSI', 'MACD', 'MACD_signal', 'MACD_hist', 'ATR']
    
    print("📈 Indicators present:")
    for ind in indicators:
        if ind in df.columns:
            latest_val = df[ind].iloc[-1]
            print(f"   ✅ {ind:15} = {latest_val:.2f}" if not pd.isna(latest_val) else f"   ⚠️  {ind:15} = NaN")
        else:
            print(f"   ❌ {ind:15} = NOT CALCULATED")
    
    print()
    print("🎯 Testing analyze_symbol()...")
    result = bot.analyze_symbol("AAPL", use_ai=False)
    
    if result:
        print(f"✅ Analysis successful")
        print(f"   Signal: {result.get('signal')}")
        print(f"   Strength: {result.get('signal_strength')}")
        print(f"   Price: ${result.get('price'):.2f}")
        
        if 'macd' in result:
            print(f"   MACD: {result['macd']:.2f}")
        if 'atr' in result:
            print(f"   ATR: {result['atr']:.2f}")
        
        if 'reasons' in result and result['reasons']:
            print(f"   Reasons:")
            for reason in result['reasons']:
                print(f"      • {reason}")
    else:
        print("⚠️  No analysis result")
    
    print()
    print("=" * 60)
    print("✅ Test completed successfully!")
    
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
