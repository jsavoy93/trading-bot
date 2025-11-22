#!/usr/bin/env python3
"""
Direct Bot AI Agent Test
"""

import os
import sys
import logging
from dotenv import load_dotenv

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from analysis.ai_agent import AITradingAgent

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)

def test_bot_ai():
    """Test the bot's AI agent directly"""
    print("🤖 TESTING BOT'S AI AGENT")
    print("=" * 40)
    
    try:
        # Create AI agent
        agent = AITradingAgent()
        print("✅ AI agent created")
        
        # Test prompt
        test_prompt = """
        Analyze AAPL stock and provide a brief JSON response with:
        - sentiment: positive/negative/neutral
        - action: buy/sell/hold
        - confidence: 0-100
        - reason: brief explanation
        """
        
        print("🔄 Testing AI analysis...")
        response = agent._call_ai_sync(test_prompt, max_tokens=200)
        
        if response:
            print(f"✅ AI Response received!")
            print(f"📄 Response: {response}")
            return True
        else:
            print("❌ No response received")
            return False
            
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

if __name__ == "__main__":
    test_bot_ai()