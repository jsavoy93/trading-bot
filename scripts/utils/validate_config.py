#!/usr/bin/env python3
"""
Configuration validation utility.
Run this to check if your .env file is properly configured.
"""
import sys
import os
from pathlib import Path

# Add src to path (go up 2 levels from scripts/utils to project root)
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from config.settings import get_settings, TradingBotSettings
from pydantic import ValidationError


def main():
    """Validate configuration and provide helpful feedback"""
    print("\n" + "="*80)
    print("🔍 TRADING BOT CONFIGURATION VALIDATOR")
    print("="*80)
    
    # Check if .env file exists
    env_file = Path(__file__).parent / ".env"
    if not env_file.exists():
        print("\n⚠️  WARNING: No .env file found!")
        print(f"   Expected location: {env_file}")
        print("\n   Create a .env file with your configuration:")
        print("   cp .env.example .env")
        print("\n   Or set environment variables directly.")
        print("\n" + "="*80 + "\n")
    else:
        print(f"\n✅ Found .env file: {env_file}")
    
    # Try to load and validate settings
    print("\n📋 Validating configuration...\n")
    
    try:
        settings = get_settings()
        
        # If we get here, validation passed!
        print("✅ Configuration validation PASSED!\n")
        
        # Print detailed summary
        settings.print_configuration_summary()
        
        # Additional recommendations
        print("💡 RECOMMENDATIONS:")
        print("-" * 80)
        
        if not settings.has_database():
            print("⚠️  Database not configured:")
            print("   - Bot will work without database")
            print("   - Features limited: no session tracking, no cooldown persistence")
            print("   - To enable: Set SUPABASE_URL and SUPABASE_ANON_KEY")
        
        if not settings.has_ai_provider():
            print("⚠️  No AI providers configured:")
            print("   - Bot will use technical analysis only")
            print("   - No AI-powered ticker selection or analysis")
            print("   - To enable: Set at least one AI provider key (OPENAI_API_KEY, etc.)")
        
        if not settings.NEWS_API_KEY:
            print("⚠️  News API not configured:")
            print("   - AI analysis will not include recent news")
            print("   - To enable: Set NEWS_API_KEY")
        
        if "paper" in settings.ALPACA_BASE_URL.lower():
            print("✅ Paper trading mode enabled (safe for testing)")
        else:
            print("⚠️  LIVE TRADING MODE - Real money at risk!")
            print("   - Make sure you understand the risks")
            print("   - Start with small position sizes")
            print("   - Monitor your account closely")
        
        print("="*80 + "\n")
        
        # Test summary
        print("🎉 Your configuration is valid and ready to use!")
        print("   Run the bot with: python main.py\n")
        
        return 0
        
    except ValidationError as e:
        # Pydantic validation error - detailed error messages
        print("❌ Configuration validation FAILED!\n")
        print("Errors found:")
        print("-" * 80)
        
        for error in e.errors():
            field = error['loc'][0] if error['loc'] else 'Unknown'
            message = error['msg']
            print(f"\n  Field: {field}")
            print(f"  Error: {message}")
        
        print("\n" + "="*80)
        print("\n📖 HELP:")
        print("-" * 80)
        print("\nRequired environment variables:")
        print("  ALPACA_API_KEY       - Your Alpaca API key (get from alpaca.markets)")
        print("  ALPACA_API_SECRET    - Your Alpaca API secret")
        print("\nOptional environment variables:")
        print("  ALPACA_BASE_URL      - Default: https://paper-api.alpaca.markets")
        print("  SUPABASE_URL         - Your Supabase project URL")
        print("  SUPABASE_ANON_KEY    - Your Supabase anonymous key")
        print("  OPENAI_API_KEY       - OpenAI API key for GPT models")
        print("  GOOGLE_API_KEY       - Google API key for Gemini")
        print("  NEWS_API_KEY         - NewsAPI key for market news")
        print("\nExample .env file:")
        print("  ALPACA_API_KEY=PKxxxxxxxxxxxxxxxxxx")
        print("  ALPACA_API_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
        print("  ALPACA_BASE_URL=https://paper-api.alpaca.markets")
        print("  OPENAI_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
        print("\n" + "="*80 + "\n")
        
        return 1
        
    except Exception as e:
        print(f"❌ Unexpected error during validation: {e}\n")
        print("="*80 + "\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
