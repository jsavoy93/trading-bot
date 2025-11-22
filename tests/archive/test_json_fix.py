#!/usr/bin/env python3
"""
Test JSON Extraction Fix
"""

import os
import sys
from dotenv import load_dotenv

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from analysis.ai_agent import AITradingAgent

load_dotenv()

def test_json_extraction():
    """Test the new JSON extraction method"""
    print("🧪 TESTING JSON EXTRACTION")
    print("=" * 40)
    
    agent = AITradingAgent()
    
    # Test various AI response formats that typically cause issues
    test_cases = [
        {
            "name": "Clean JSON",
            "response": '{"test": "value", "number": 42}',
            "should_work": True
        },
        {
            "name": "JSON with markdown",
            "response": '```json\n{"test": "value", "number": 42}\n```',
            "should_work": True
        },
        {
            "name": "JSON with extra text after",
            "response": '{"test": "value", "number": 42}\n\nThis is some extra text that usually causes "Extra data" errors.',
            "should_work": True
        },
        {
            "name": "JSON with text before and after", 
            "response": 'Here is the analysis:\n```json\n{"test": "value", "number": 42}\n```\nThat concludes the analysis.',
            "should_work": True
        },
        {
            "name": "Invalid JSON",
            "response": '{"test": "value", "number": 42, invalid}',
            "should_work": False
        },
        {
            "name": "No JSON",
            "response": 'This is just plain text with no JSON at all.',
            "should_work": False
        }
    ]
    
    for i, test_case in enumerate(test_cases, 1):
        print(f"\n{i}. Testing: {test_case['name']}")
        print(f"   Input: {test_case['response'][:50]}...")
        
        result, error = agent._extract_json_from_response(test_case['response'])
        
        if result and test_case['should_work']:
            print(f"   ✅ SUCCESS: {result}")
        elif not result and not test_case['should_work']:
            print(f"   ✅ EXPECTED FAILURE: {error}")
        elif result and not test_case['should_work']:
            print(f"   ❌ UNEXPECTED SUCCESS: {result}")
        else:
            print(f"   ❌ UNEXPECTED FAILURE: {error}")
    
    print(f"\n🧪 Testing with real AI call...")
    
    # Test with actual AI call
    test_prompt = """
    Analyze AAPL stock and provide analysis in this JSON format:
    {
        "sentiment": "positive/negative/neutral",
        "recommendation": "buy/sell/hold", 
        "confidence": 0.85,
        "reasoning": "Brief explanation"
    }
    """
    
    response = agent._call_ai_sync(test_prompt, max_tokens=150)
    if response:
        print(f"   Raw AI Response: {response[:100]}...")
        
        parsed, error = agent._extract_json_from_response(response)
        if parsed:
            print(f"   ✅ Successfully extracted JSON: {parsed}")
        else:
            print(f"   ❌ Failed to extract JSON: {error}")
    else:
        print("   ❌ No response from AI")

if __name__ == "__main__":
    test_json_extraction()