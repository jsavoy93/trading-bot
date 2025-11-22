#!/usr/bin/env python3
"""
Example: Run trading bot with AI disabled for individual ticker analysis
This will use ONLY technical indicators (RSI, SMA) without AI insights
"""
import sys
from pathlib import Path

# Add src to path
src_path = str(Path(__file__).parent / "src")
sys.path.insert(0, src_path)

from core.smart_bot import SmartTradingBot

if __name__ == "__main__":
    # Initialize bot
    bot = SmartTradingBot()
    
    # Option 1: Disable ALL AI features (pure technical analysis)
    print("\n🔧 Configuring bot for PURE TECHNICAL ANALYSIS...")
    bot.configure_ai_usage(
        ticker_analysis=False,   # No AI insights for individual tickers
        ticker_selection=False,  # No AI-based ticker selection
        market_summary=False     # No AI market summaries
    )
    
    # Option 2: Use AI only for smart ticker selection, not analysis
    # bot.configure_ai_usage(
    #     ticker_analysis=False,   # Rely only on RSI/SMA stats
    #     ticker_selection=True,   # Let AI pick smart tickers
    #     market_summary=False
    # )
    
    # Option 3: Use AI everywhere except individual ticker analysis
    # bot.configure_ai_usage(
    #     ticker_analysis=False,   # Fast analysis using only technical indicators
    #     ticker_selection=True,   # Smart ticker picks
    #     market_summary=True      # Market sentiment overview
    # )
    
    # Start trading session
    bot.start_session()
    
    try:
        # Run analysis - will use only technical indicators for ticker decisions
        print("\n📊 Running analysis with technical indicators only...")
        bot.run_analysis(
            max_symbols=20,
            max_trades=2,
            use_ai=False  # This parameter now only affects ticker selection/summaries
        )
    finally:
        bot.end_session()
    
    # Show results
    bot.show_database_status()
    
    print("\n✅ Analysis complete!")
    print("📈 Trades were based purely on RSI and SMA technical indicators")
