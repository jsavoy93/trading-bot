#!/usr/bin/env python3
"""
Test News Fetching
"""

import os
import sys
import asyncio
from dotenv import load_dotenv

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from analysis.ai_agent import AITradingAgent

load_dotenv()

async def test_news_fetching():
    """Test news fetching functionality"""
    print("📰 TESTING NEWS FETCHING")
    print("=" * 40)
    
    agent = AITradingAgent()
    
    # Check if API keys are configured
    print(f"News API Key: {'✅ Configured' if agent.news_api_key else '❌ Not configured'}")
    print(f"Polygon API Key: {'✅ Configured' if agent.polygon_api_key else '❌ Not configured'}")
    
    # Test fetching news for a popular stock
    symbol = "AAPL"
    print(f"\n🔍 Testing news fetch for {symbol}...")
    
    articles = await agent._fetch_news(symbol, days=7)
    
    print(f"📊 Found {len(articles)} articles")
    
    if articles:
        print("\n📰 Article samples:")
        for i, article in enumerate(articles[:3], 1):
            title = article.get('title', 'No title')[:60]
            source = article.get('source', {}).get('name', 'Unknown source')
            print(f"   {i}. {title}... (Source: {source})")
    
    return len(articles) > 0

if __name__ == "__main__":
    asyncio.run(test_news_fetching())