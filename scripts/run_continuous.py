#!/usr/bin/env python3
"""
Continuous Trading Bot Runner
Runs the trading bot in continuous loop mode with configurable parameters
"""
import sys
import os
from pathlib import Path

# Add src to path
src_path = str(Path(__file__).parent / "src")
sys.path.insert(0, src_path)

# Change to project directory
os.chdir(Path(__file__).parent)

from core.smart_bot import SmartTradingBot

def main():
    """Run bot in continuous mode with custom parameters"""
    try:
        bot = SmartTradingBot()
        
        # Show initial status
        bot.show_database_setup()
        bot.show_database_status()
        
        # Configuration for continuous mode
        MAX_SYMBOLS = 30        # Symbols to analyze per loop
        MAX_TRADES = 2          # Max trades per loop
        LOOP_DELAY = 60         # Seconds between loops
        SUMMARY_INTERVAL = 50   # Show summary every N loops
        USE_AI = True           # Smart mode: AI auto-disables on rate limits, re-enables after 1hr
        
        print(f"""
🔧 CONTINUOUS MODE CONFIGURATION:
   📊 Symbols per loop: {MAX_SYMBOLS}
   💼 Max trades per loop: {MAX_TRADES}
   ⏰ Loop delay: {LOOP_DELAY} seconds ({LOOP_DELAY/60:.1f} minutes)
   📈 Summary every: {SUMMARY_INTERVAL} loops
   🧠 AI Mode: {'SMART MODE' if USE_AI else 'DISABLED'}
   
🧠 SMART MODE: AI automatically disables on rate limits, re-enables after 1 hour
⚠️  Google AI: 200 requests/day limit - will fallback gracefully to technical analysis
💡 To modify these settings, edit run_continuous.py
🛑 Press Ctrl+C to stop gracefully
        """)
        
        # Start continuous loop
        bot.run_continuous_loop(
            max_symbols=MAX_SYMBOLS,
            max_trades=MAX_TRADES, 
            loop_delay=LOOP_DELAY,
            summary_interval=SUMMARY_INTERVAL,
            use_ai=USE_AI
        )
        
    except KeyboardInterrupt:
        print("\n🛑 Continuous mode stopped by user")
    except Exception as e:
        print(f"❌ Error: {e}")
        raise

if __name__ == "__main__":
    main()