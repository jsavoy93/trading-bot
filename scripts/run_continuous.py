#!/usr/bin/env python3
"""
Continuous Trading Bot Runner
Runs the trading bot in continuous loop mode with configurable parameters

BOT-002 NOTES:
  - This script is NOT the systemd ExecStart target. The
    smartbot-runner.service unit targets `main.py --continuous`
    which provides the same single-instance protection and uses
    the schema-backed `loop_delay_seconds` value via
    `_resolve_loop_delay(None)` instead of the previous hard-coded
    60s override.
  - This script remains as a manual smoke-test / interactive runner.
    It now acquires the same /tmp/trading_bot.lock single-instance
    guard as main.py so manual and systemd invocations cannot collide.
  - Operational cadence (MAX_SYMBOLS, MAX_TRADES, SUMMARY_INTERVAL,
    USE_AI) remains hard-coded here because they are explicit
    per-invocation budgets, not strategy semantics. They do not
    change buy/sell thresholds, position sizing, or risk parameters.
"""
import sys
import os
from pathlib import Path

# Add src to path
src_path = str(Path(__file__).parent / "src")
sys.path.insert(0, src_path)

# Change to project directory
os.chdir(Path(__file__).parent)

# BOT-002: acquire the same single-instance lock as main.py so a
# manual run of this script cannot collide with the systemd runner.
LOCK_FILE = "/tmp/trading_bot.lock"


def _acquire_single_instance_lock() -> None:
    """Refuse to start if another bot instance holds /tmp/trading_bot.lock."""
    if os.path.exists(LOCK_FILE):
        try:
            with open(LOCK_FILE, 'r') as f:
                old_pid = f.read().strip()
            if old_pid:
                try:
                    os.kill(int(old_pid), 0)
                    print(f"❌ Bot already running (PID: {old_pid})")
                    print(f"   To stop: kill {old_pid}")
                    sys.exit(1)
                except (OSError, ProcessLookupError):
                    try:
                        os.remove(LOCK_FILE)
                    except Exception:
                        pass
        except Exception:
            try:
                os.remove(LOCK_FILE)
            except Exception:
                pass

    with open(LOCK_FILE, 'w') as f:
        f.write(str(os.getpid()))

    import atexit
    atexit.register(lambda: os.path.exists(LOCK_FILE) and os.remove(LOCK_FILE))


_acquire_single_instance_lock()

from core.smart_bot import SmartTradingBot

def main():
    """Run bot in continuous mode with custom parameters"""
    try:
        bot = SmartTradingBot()

        # Show initial status
        bot.show_database_setup()
        bot.show_database_status()

        # Operational cadence (per-loop budgets, not strategy).
        MAX_SYMBOLS = 30        # Symbols to analyze per loop
        MAX_TRADES = 2          # Max trades per loop
        LOOP_DELAY = None       # Use schema-backed loop_delay_seconds
        SUMMARY_INTERVAL = 50   # Show summary every N loops
        USE_AI = True           # Smart mode: AI auto-disables on rate limits, re-enables after 1hr

        # Resolve loop delay for the banner via the same helper the bot
        # uses internally, so the printed value matches what the loop
        # will actually use.
        resolved_loop_delay = bot._resolve_loop_delay(LOOP_DELAY)

        print(f"""
🔧 CONTINUOUS MODE CONFIGURATION:
   📊 Symbols per loop: {MAX_SYMBOLS}
   💼 Max trades per loop: {MAX_TRADES}
   ⏰ Loop delay: {resolved_loop_delay} seconds ({resolved_loop_delay/60:.1f} minutes)
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