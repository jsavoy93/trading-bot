#!/usr/bin/env python3
"""
Test AI Provider Priority and Fallback
"""

import os
import sys
from dotenv import load_dotenv

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from analysis.ai_agent import AITradingAgent

load_dotenv()

def test_provider_order():
    """Test if providers initialize in the correct priority order"""
    print("🔄 TESTING PROVIDER INITIALIZATION ORDER")
    print("=" * 50)
    
    # Temporarily change primary provider to test fallback
    original_provider = os.getenv('AI_PROVIDER')
    
    # Test with each provider as primary
    providers_to_test = ['cohere', 'openrouter', 'mistral', 'google']
    
    results = {}
    
    for provider in providers_to_test:
        print(f"\n🧪 Testing with AI_PROVIDER={provider}")
        
        # Set environment variable temporarily
        os.environ['AI_PROVIDER'] = provider
        
        try:
            agent = AITradingAgent()
            
            if agent.current_provider:
                print(f"✅ Initialized with: {agent.current_provider}")
                
                # Test AI call
                response = agent._call_ai_sync("Brief analysis of TSLA stock in one sentence:", max_tokens=50)
                
                if response:
                    print(f"✅ AI Response: {response[:100]}...")
                    results[provider] = 'SUCCESS'
                else:
                    print(f"❌ No response from {agent.current_provider}")
                    results[provider] = 'NO_RESPONSE'
            else:
                print(f"❌ Failed to initialize any provider")
                results[provider] = 'FAILED'
                
        except Exception as e:
            print(f"❌ Error: {e}")
            results[provider] = 'ERROR'
    
    # Restore original provider
    if original_provider:
        os.environ['AI_PROVIDER'] = original_provider
    
    print(f"\n📊 FINAL RESULTS:")
    print("=" * 30)
    for provider, status in results.items():
        icon = "✅" if status == 'SUCCESS' else "❌"
        print(f"{icon} {provider.upper()}: {status}")
    
    working_count = sum(1 for status in results.values() if status == 'SUCCESS')
    print(f"\n🎉 Working providers: {working_count}/4")
    
    return results

if __name__ == "__main__":
    test_provider_order()