#!/usr/bin/env python3
"""
Test News for Specific Tickers from Bot Logs
"""

import os
import sys
import asyncio
from dotenv import load_dotenv

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from analysis.ai_agent import AITradingAgent

load_dotenv()

async def test_specific_tickers():
    """Test news fetching for the exact tickers the bot was analyzing"""
    print("📰 TESTING NEWS FOR SPECIFIC TICKERS")
    print("=" * 50)
    
    agent = AITradingAgent()
    
    # Test the exact tickers from the bot logs
    test_tickers = ["AMZN", "BIL", "CLIP", "TDAC", "TDACW"]
    
    for ticker in test_tickers:
        print(f"\n🔍 Testing {ticker}...")
        
        try:
            articles = await agent._fetch_news(ticker, days=7)
            print(f"   📊 Found {len(articles)} articles")
            
            if articles:
                # Show first article title
                first_title = articles[0].get('title', 'No title')[:50]
                print(f"   📰 Sample: {first_title}...")
            else:
                print(f"   ❌ No articles found for {ticker}")
                
        except Exception as e:
            print(f"   ❌ Error fetching news for {ticker}: {e}")

if __name__ == "__main__":
    asyncio.run(test_specific_tickers())