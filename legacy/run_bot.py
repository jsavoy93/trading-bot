#!/usr/bin/env python3
"""
Simple setup script for testing the trading bot without database.
This allows you to run the bot immediately for testing purposes.
"""
import os
import sys
import logging
from dotenv import load_dotenv

# Add current directory to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

def main():
    """Quick setup check and run trading bot without database"""
    print("🚀 Trading Bot Quick Setup")
    print("=" * 40)
    
    # Check required Alpaca credentials
    alpaca_key = os.getenv("ALPACA_API_KEY")
    alpaca_secret = os.getenv("ALPACA_API_SECRET")
    
    if not alpaca_key or not alpaca_secret:
        print("❌ Missing Alpaca API credentials")
        print("\nPlease set the following in your .env file:")
        print("ALPACA_API_KEY=your_alpaca_api_key")
        print("ALPACA_API_SECRET=your_alpaca_secret_key")
        print("\nGet your API keys from: https://app.alpaca.markets/")
        return False
    
    print("✅ Alpaca API credentials found")
    
    # Check database credentials (optional)
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        print("✅ Database credentials found - will attempt database logging")
    else:
        print("⚠️  No database credentials - will run without database logging")
        print("   This is fine for testing! Database features are optional.")
    
    print("\n🎯 Trading Bot Configuration:")
    print(f"   Mode: Paper Trading (Safe)")
    print(f"   Database Logging: {'Enabled' if database_url else 'Disabled'}")
    print(f"   Symbol Limit: 100 (for testing)")
    
    print("\n🏃 Starting trading bot...")
    
    try:
        # Import and run the bot
        from bot import main_loop
        main_loop()
    except KeyboardInterrupt:
        print("\n\n⏹️  Trading bot stopped by user")
        print("✅ Shutdown completed successfully")
    except Exception as e:
        print(f"\n❌ Trading bot error: {e}")
        logging.exception("Trading bot error")
        return False
    
    return True

if __name__ == "__main__":
    success = main()
    print(f"\n{'✅ Setup completed successfully!' if success else '❌ Setup failed'}")
    sys.exit(0 if success else 1)