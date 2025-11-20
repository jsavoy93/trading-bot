#!/usr/bin/env python3
"""
Simple test script to verify Alpaca data fetching works
"""
import os
import sys
from dotenv import load_dotenv

# Add current directory to path so we can import from bot.py
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Load environment variables
load_dotenv()

try:
    from bot import get_alpaca_bars, data_client
    
    # Test with a well-known symbol
    test_symbol = "AAPL"
    print(f"Testing data fetch for {test_symbol}...")
    
    df = get_alpaca_bars(test_symbol, "1Hour")
    
    print(f"Successfully fetched {len(df)} bars for {test_symbol}")
    print(f"Date range: {df.index[0]} to {df.index[-1]}")
    print("\nLast 5 bars:")
    print(df.tail())
    
    print("\nData types:")
    print(df.dtypes)
    
    print("\nTest passed! Alpaca data fetching is working correctly.")
    
except Exception as e:
    print(f"Test failed with error: {e}")
    import traceback
    traceback.print_exc()