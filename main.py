#!/usr/bin/env python3
"""
Trading Bot - Main Entry Point
Enhanced trading bot with intelligent position management.
"""
import sys
import os
from pathlib import Path

# Add src to path for imports
src_path = str(Path(__file__).parent / "src")
sys.path.insert(0, src_path)

# Validate configuration before starting
from config.settings import validate_settings
import argparse


def _send_test_sms_and_exit():
    # Import here so we only load bot when needed
    from core.smart_bot import SmartTradingBot

    bot = SmartTradingBot()
    if not getattr(bot, "sms_enabled", False) or not bot.twilio_client:
        print("⚠️ SMS not configured or Twilio client unavailable. Set TWILIO env vars and install twilio.")
        sys.exit(1)

    try:
        test_body = "[TEST] Trading Bot SMS test - notifications are configured."
        bot.twilio_client.messages.create(body=test_body, from_=bot.twilio_from_number, to=bot.alert_phone_number)
        print("✅ Test SMS sent successfully")
        sys.exit(0)
    except Exception as e:
        print(f"❌ Failed to send test SMS: {e}")
        sys.exit(1)


if __name__ == "__main__":
    # Validate environment variables and configuration
    print("🔍 Validating configuration...")
    if not validate_settings():
        print("❌ Configuration validation failed. Exiting.")
        sys.exit(1)

    parser = argparse.ArgumentParser(
        description="Trading Bot launcher",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Strategy Mode (FULL PRO MODE is DEFAULT):
  -bs, --basic-signals        Use basic mode (SMA + RSI only)
  -ae, --atr-exits            Already enabled by default
  -ap, --atr-position-sizing  Already enabled by default
  -sc, --scored-signals       Already enabled by default
  
  Default includes: MACD, ATR, 200-SMA, volume shelves, scored signals, ATR exits, ATR sizing

Signal Strength Thresholds (scored mode only):
  --signal-profile [conservative|balanced|aggressive]  Preset threshold profile (default: balanced)
      conservative: min_buy=4.0, strong=5.5 (fewer, higher quality trades)
      balanced:     min_buy=3.0, strong=4.5 (default)
      aggressive:   min_buy=2.5, strong=4.0 (more trades, lower quality)
  
  Or customize individual thresholds:
  --min-buy-score N          Minimum score for BUY (default: 3.0)
  --min-sell-score N         Minimum score for SELL (default: -3.0)
  --strong-threshold N       Score for STRONG signal (default: 4.5)
  --min-strength [weak|medium|strong]  Only trade signals at this strength or higher (default: medium)

Bot Runtime Flags (passed to bot):
  -c, --continuous            Run in continuous mode
  -d, --delay SECONDS         Delay between loops (default: 300)
  --max-symbols N             Max symbols per loop (default: 30)
  --max-trades N              Max trades per loop (default: 2)
  --ai-full                   Full AI (selection + per-ticker + summary)
  --no-ai                     Disable all AI features
  (Default: AI selection only - AI picks tickers, technical decides trades)

Examples:
  python main.py                           # Full pro mode (all features, balanced thresholds)
  python main.py --signal-profile conservative  # High quality signals only (fewer trades)
  python main.py --signal-profile aggressive    # More trading opportunities (lower quality)
  python main.py -bs                       # Basic signals only (minimal)
  python main.py --ai-full                 # Full pro mode + full AI
  python main.py --no-ai                   # Full pro mode, no AI (pure technical)
  python main.py -c -d 300                 # Continuous mode with all pro features
  python main.py -c --ai-full              # Continuous + full pro + full AI
  python main.py --signal-profile conservative --min-strength strong  # Ultra-selective
  python main.py --test-sms                # Test SMS notifications
        """
    )
    
    # SMS testing
    parser.add_argument("--test-sms", action="store_true", 
                       help="Send a single test SMS using configured Twilio credentials and exit")
    
    # Strategy mode flags (advanced is default, use -bs for basic)
    parser.add_argument("-bs", "--basic-signals", action="store_true",
                       help="Use basic mode (SMA + RSI only) instead of advanced")
    parser.add_argument("-ae", "--atr-exits", action="store_true",
                       help="Enable ATR-based volatility-adjusted exits")
    parser.add_argument("-ap", "--atr-position-sizing", action="store_true",
                       help="Enable ATR-based risk-adjusted position sizing")
    parser.add_argument("-sc", "--scored-signals", action="store_true",
                       help="Enable scored signal evaluation (pro voting system - more trades)")
    
    # Signal profile presets (easy single-flag control)
    parser.add_argument("--signal-profile", type=str, 
                       choices=["conservative", "balanced", "aggressive"],
                       help="Preset threshold profile: conservative (high quality), balanced (default), aggressive (more trades)")
    
    # Signal strength threshold controls (for scored mode) - can override profile
    parser.add_argument("--min-buy-score", type=float,
                       help="Minimum score for BUY signal (default: 3.0, or set by profile)")
    parser.add_argument("--min-sell-score", type=float,
                       help="Minimum score for SELL signal (default: -3.0, or set by profile)")
    parser.add_argument("--strong-threshold", type=float,
                       help="Score threshold for STRONG signal (default: 4.5, or set by profile)")
    parser.add_argument("--min-strength", type=str, choices=["weak", "medium", "strong"], default="medium",
                       help="Minimum signal strength to trade (default: medium)")
    
    # Parse only known args, let smart_bot.py handle the rest
    args, unknown = parser.parse_known_args()
    
    # Reconstruct sys.argv with only the unknown args for smart_bot.py to parse
    sys.argv = [sys.argv[0]] + unknown

    if args.test_sms:
        _send_test_sms_and_exit()
    
    # Set environment variables from command-line flags
    # Advanced mode is default, only disable if -bs flag used
    if args.basic_signals:
        os.environ["USE_ADVANCED_SIGNALS"] = "false"
        print("📊 Basic signals mode (SMA + RSI only)")
    else:
        print("🎯 Advanced mode enabled (MACD, ATR, 200-SMA, volume shelves, scored signals, ATR exits/sizing)")
    
    if args.atr_exits:
        os.environ["USE_ATR_EXITS"] = "true"
        print("📊 ATR-based exits enabled (volatility-adjusted stops)")
    
    if args.atr_position_sizing:
        os.environ["USE_ATR_SIZING"] = "true"
        print("💰 ATR-based position sizing enabled (risk-adjusted)")
    
    if args.scored_signals:
        os.environ["USE_SCORED_SIGNALS"] = "true"
        print("🎯 Scored signal evaluation enabled (voting system - more flexible)")
    
    # Apply signal profile presets
    profile_settings = {
        "conservative": {"min_buy": 4.0, "min_sell": -4.0, "strong": 5.5},
        "balanced": {"min_buy": 3.0, "min_sell": -3.0, "strong": 4.5},
        "aggressive": {"min_buy": 2.5, "min_sell": -2.5, "strong": 4.0}
    }
    
    # Start with defaults or profile
    if args.signal_profile:
        profile = profile_settings[args.signal_profile]
        min_buy_score = profile["min_buy"]
        min_sell_score = profile["min_sell"]
        strong_threshold = profile["strong"]
        print(f"📊 Signal Profile: {args.signal_profile.upper()}")
    else:
        min_buy_score = 3.0
        min_sell_score = -3.0
        strong_threshold = 4.5
    
    # Individual flags override profile settings
    if args.min_buy_score is not None:
        min_buy_score = args.min_buy_score
    if args.min_sell_score is not None:
        min_sell_score = args.min_sell_score
    if args.strong_threshold is not None:
        strong_threshold = args.strong_threshold
    
    # Set signal threshold environment variables
    os.environ["MIN_BUY_SCORE"] = str(min_buy_score)
    os.environ["MIN_SELL_SCORE"] = str(min_sell_score)
    os.environ["STRONG_SIGNAL_THRESHOLD"] = str(strong_threshold)
    os.environ["MIN_SIGNAL_STRENGTH"] = args.min_strength
    
    # Show threshold config if non-default
    if args.signal_profile or args.min_buy_score is not None or args.min_sell_score is not None or args.strong_threshold is not None or args.min_strength != "medium":
        print(f"\n📊 Signal Thresholds:")
        print(f"   BUY: score ≥ {min_buy_score} | SELL: score ≤ {min_sell_score}")
        print(f"   STRONG: |score| ≥ {strong_threshold} | Min strength: {args.min_strength.upper()}")

    # If no test flag, start the bot normally (delegates to smart_bot.main)
    from core.smart_bot import main

    main()