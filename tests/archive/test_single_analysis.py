#!/usr/bin/env python3
"""
Test Single AI Analysis to verify JSON fix
"""

import os
import sys
import asyncio
from dotenv import load_dotenv

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from analysis.ai_agent import AITradingAgent

load_dotenv()

async def test_single_analysis():
    """Test a single AI analysis that was causing issues"""
    print("🧪 TESTING SINGLE AI ANALYSIS")
    print("=" * 40)
    
    agent = AITradingAgent()
    
    # Test the exact type of analysis that was failing
    prompt = """
    You are an educational financial analysis system providing learning-focused market insights.
    
    Based on the following portfolio context, recommend 20-25 high-quality stocks for educational analysis.
    Focus on well-known, liquid stocks across different sectors for learning purposes.
    
    Portfolio Context:
    - Portfolio Value: $100,000
    - Cash: 76%
    - Concentration: High (90% in top 5)
    - Recently researched: AMZN, BIL, CLIP, TDAC
    
    Provide response in JSON format:
    {
        "recommended_tickers": ["AAPL", "MSFT", "GOOGL", ...],
        "reasoning": "Analysis approach explanation",
        "focus_areas": ["diversification", "sector_analysis"]
    }
    """
    
    print("🔄 Testing analyze_with_context...")
    response = await agent.analyze_with_context(prompt, "portfolio_ticker_selection")
    
    if isinstance(response, dict):
        if 'recommended_tickers' in response:
            tickers = response['recommended_tickers']
            print(f"✅ SUCCESS: Got {len(tickers)} tickers")
            print(f"📊 Tickers: {tickers[:10]}...")
            print(f"🧠 Reasoning: {response.get('reasoning', 'None')[:100]}...")
        else:
            print(f"❌ MISSING KEY: Response has keys {list(response.keys())}")
    else:
        print(f"❌ WRONG TYPE: Got {type(response)}, expected dict")
        print(f"📝 Response: {str(response)[:200]}...")

if __name__ == "__main__":
    asyncio.run(test_single_analysis())