#!/usr/bin/env python3
"""
AI Provider Test Script
Tests all configured AI providers to verify they're working correctly.
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
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

def test_ai_provider(provider_name: str) -> dict:
    """Test a specific AI provider"""
    print(f"\n{'='*50}")
    print(f"🧪 TESTING {provider_name.upper()} PROVIDER")
    print(f"{'='*50}")
    
    result = {
        'provider': provider_name,
        'status': 'UNKNOWN',
        'error': None,
        'response': None,
        'api_key_present': False
    }
    
    # Check if API key is present
    key_mapping = {
        'openai': 'OPENAI_API_KEY',
        'google': 'GOOGLE_AI_API_KEY', 
        'huggingface': 'HUGGINGFACE_API_KEY',
        'openrouter': 'OPENROUTER_API_KEY',
        'mistral': 'MISTRAL_API_KEY',
        'cohere': 'COHERE_API_KEY'
    }
    
    api_key = os.getenv(key_mapping.get(provider_name, f"{provider_name.upper()}_API_KEY"))
    result['api_key_present'] = bool(api_key and api_key.strip())
    
    if not result['api_key_present']:
        result['status'] = 'FAILED'
        result['error'] = f"❌ No API key found for {provider_name}"
        print(result['error'])
        return result
    
    print(f"✅ API key found for {provider_name}")
    
    try:
        # Create AI agent with specific provider
        agent = AITradingAgent()
        
        # Force set the provider for testing
        agent.ai_provider = provider_name
        agent.current_provider = provider_name
        
        # Initialize the specific provider
        try:
            if provider_name == 'openai':
                success = agent._init_openai()
            elif provider_name == 'google':
                success = agent._init_google_ai()
            elif provider_name == 'huggingface':
                success = agent._init_huggingface()
            elif provider_name == 'openrouter':
                success = agent._init_openrouter()
            elif provider_name == 'mistral':
                success = agent._init_mistral()
            elif provider_name == 'cohere':
                success = agent._init_cohere()
            else:
                success = False
        except Exception as init_error:
            result['status'] = 'FAILED'
            result['error'] = f"❌ {provider_name} initialization error: {str(init_error)}"
            print(result['error'])
            return result
            
        if not success:
            result['status'] = 'FAILED'
            result['error'] = f"❌ Failed to initialize {provider_name} client"
            print(result['error'])
            return result
            
        print(f"✅ {provider_name} client initialized successfully")
        
        # Test with a simple prompt
        test_prompt = "Analyze this stock ticker: AAPL. Provide a brief 2-sentence analysis focusing on current market sentiment."
        
        print(f"🔄 Testing API call...")
        response = agent._call_ai_sync(test_prompt, max_tokens=100)
        
        if response:
            result['status'] = 'SUCCESS'
            result['response'] = response[:200] + "..." if len(response) > 200 else response
            print(f"✅ {provider_name} API call successful!")
            print(f"📄 Response preview: {result['response']}")
        else:
            result['status'] = 'FAILED'
            result['error'] = f"❌ {provider_name} returned empty response"
            print(result['error'])
            
    except Exception as e:
        result['status'] = 'FAILED'
        result['error'] = f"❌ {provider_name} error: {str(e)}"
        print(result['error'])
    
    return result

def main():
    """Test all AI providers"""
    print("🤖 AI PROVIDER COMPATIBILITY TEST")
    print("=" * 60)
    
    providers_to_test = [
        'openai',
        'google', 
        'huggingface',
        'openrouter',
        'mistral',
        'cohere'
    ]
    
    results = []
    
    for provider in providers_to_test:
        result = test_ai_provider(provider)
        results.append(result)
    
    # Summary report
    print(f"\n{'='*60}")
    print("📊 SUMMARY REPORT")
    print(f"{'='*60}")
    
    working_providers = []
    failed_providers = []
    
    for result in results:
        status_icon = "✅" if result['status'] == 'SUCCESS' else "❌"
        key_icon = "🔑" if result['api_key_present'] else "❌"
        
        print(f"{status_icon} {result['provider'].upper():12} | API Key: {key_icon} | Status: {result['status']}")
        
        if result['status'] == 'SUCCESS':
            working_providers.append(result['provider'])
        else:
            failed_providers.append((result['provider'], result['error']))
    
    print(f"\n🎉 WORKING PROVIDERS ({len(working_providers)}):")
    for provider in working_providers:
        print(f"   ✅ {provider}")
    
    if failed_providers:
        print(f"\n⚠️  FAILED PROVIDERS ({len(failed_providers)}):")
        for provider, error in failed_providers:
            print(f"   ❌ {provider}: {error}")
    
    print(f"\n{'='*60}")
    if working_providers:
        print(f"🚀 Your bot can use {len(working_providers)} AI provider(s)!")
        print("   Recommendation: Set AI_PROVIDER to one of the working providers in .env")
    else:
        print("⚠️  No working AI providers found. Please check your API keys.")
    print(f"{'='*60}")

if __name__ == "__main__":
    main()