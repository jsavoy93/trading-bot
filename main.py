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

    parser = argparse.ArgumentParser(description="Trading Bot launcher")
    parser.add_argument("--test-sms", action="store_true", help="Send a single test SMS using configured Twilio credentials and exit")
    args, _ = parser.parse_known_args()

    if args.test_sms:
        _send_test_sms_and_exit()

    # If no test flag, start the bot normally (delegates to smart_bot.main)
    from core.smart_bot import main

    main()