#!/usr/bin/env python3
"""
Download historical data for all current positions.

Usage:
    python3 download_portfolio_data.py
    python3 download_portfolio_data.py --symbols AAPL,MSFT,GOOG
"""
import os
import sys
import argparse
import time

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.data.historical_pipeline import HistoricalDataPipeline
from src.core.smart_bot import SmartTradingBot


def get_portfolio_symbols() -> list:
    """Get list of symbols from current portfolio."""
    try:
        bot = SmartTradingBot()
        positions = bot.trading_client.get_all_positions()
        symbols = [p.symbol for p in positions]
        print(f"Found {len(symbols)} positions in portfolio")
        return symbols
    except Exception as e:
        print(f"Error getting portfolio: {e}")
        return []


def main():
    parser = argparse.ArgumentParser(description='Download historical data for portfolio')
    parser.add_argument('--symbols', type=str, default=None,
                       help='Comma-separated symbols (or use --portfolio for all positions)')
    parser.add_argument('--portfolio', action='store_true',
                       help='Download for all current positions')
    parser.add_argument('--years', type=int, default=3,
                       help='Years of history to download (default: 3)')
    parser.add_argument('--delay', type=float, default=0.3,
                       help='Delay between requests in seconds (default: 0.3)')
    
    args = parser.parse_args()
    
    # Determine symbols to download
    if args.portfolio:
        symbols = get_portfolio_symbols()
    elif args.symbols:
        symbols = [s.strip() for s in args.symbols.split(',')]
    else:
        print("Error: Specify --symbols or --portfolio")
        sys.exit(1)
    
    if not symbols:
        print("No symbols to download")
        return
    
    print(f"\n📥 Downloading {args.years} years of data for {len(symbols)} symbols...")
    print(f"   Delay between requests: {args.delay}s\n")
    
    pipeline = HistoricalDataPipeline()
    pipeline.default_lookback_days = args.years * 365
    
    success = 0
    failed = []
    
    for i, symbol in enumerate(symbols):
        print(f"[{i+1}/{len(symbols)}] {symbol}...", end=" ")
        
        try:
            df = pipeline.fetch_daily_ohlcv(symbol)
            if df is not None and len(df) > 0:
                pipeline.save_to_csv(df, symbol, 'daily')
                print(f"✓ {len(df)} days")
                success += 1
            else:
                print("✗ No data")
                failed.append(symbol)
        except Exception as e:
            print(f"✗ Error: {e}")
            failed.append(symbol)
        
        # Rate limiting
        if i < len(symbols) - 1:
            time.sleep(args.delay)
    
    print(f"\n📊 Download complete:")
    print(f"   Success: {success}/{len(symbols)}")
    if failed:
        print(f"   Failed: {', '.join(failed[:10])}{'...' if len(failed) > 10 else ''}")
    
    # Show where data is stored
    print(f"\n📁 Data saved to: {pipeline.data_dir}")


if __name__ == "__main__":
    main()
