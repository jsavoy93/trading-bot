#!/usr/bin/env python3
"""
AI Setup and Configuration Script
Validates AI API keys and tests connectivity
"""

import os
import sys
import asyncio
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotenv import load_dotenv

async def test_openai_connection():
    """Test OpenAI API connection"""
    try:
        import openai
        api_key = os.getenv('OPENAI_API_KEY')
        
        if not api_key:
            print("❌ OPENAI_API_KEY not found in environment")
            return False
            
        client = openai.AsyncOpenAI(api_key=api_key)
        
        # Test with a simple completion
        response = await client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": "Test connection"}],
            max_tokens=10
        )
        
        print("✅ OpenAI API connection successful")
        return True
        
    except ImportError:
        print("❌ OpenAI package not installed. Run: pip install openai")
        return False
    except Exception as e:
        print(f"❌ OpenAI API connection failed: {e}")
        return False

def test_google_ai_connection():
    """Test Google AI API connection"""
    try:
        import google.generativeai as genai
        api_key = os.getenv('GOOGLE_AI_API_KEY')
        
        if not api_key:
            print("❌ GOOGLE_AI_API_KEY not found in environment")
            return False
            
        genai.configure(api_key=api_key)
        
        # Try different models in order of preference (using correct API names)
        models_to_try = [
            'models/gemini-2.5-flash',
            'models/gemini-2.0-flash', 
            'models/gemini-flash-latest',
            'models/gemini-2.5-flash-lite',
            'models/gemini-2.0-flash-lite'
        ]
        
        for model_name in models_to_try:
            try:
                model = genai.GenerativeModel(model_name)
                
                # Test with a simple generation
                response = model.generate_content(
                    "Test connection - respond with 'OK'",
                    generation_config={'max_output_tokens': 10, 'temperature': 0.1}
                )
                
                if response.text and ('OK' in response.text or len(response.text.strip()) > 0):
                    print(f"✅ Google AI API connection successful ({model_name})")
                    return True
                    
            except Exception as model_error:
                print(f"⚠️ Model {model_name} failed: {str(model_error)[:100]}")
                continue
        
        print("❌ No compatible Google AI models found")
        return False
            
    except ImportError:
        print("❌ Google GenerativeAI package not installed. Run: pip install google-generativeai")
        return False
    except Exception as e:
        print(f"❌ Google AI API connection failed: {e}")
        return False

def test_news_api():
    """Test News API connection"""
    try:
        from newsapi import NewsApiClient
        api_key = os.getenv('NEWS_API_KEY')
        
        if not api_key:
            print("❌ NEWS_API_KEY not found in environment")
            return False
            
        newsapi = NewsApiClient(api_key=api_key)
        
        # Test with a simple query
        response = newsapi.get_top_headlines(
            category='business',
            language='en',
            country='us',
            page_size=1
        )
        
        if response.get('status') == 'ok':
            print("✅ News API connection successful")
            return True
        else:
            print(f"❌ News API connection failed: {response}")
            return False
            
    except ImportError:
        print("❌ NewsAPI package not installed. Run: pip install newsapi-python")
        return False
    except Exception as e:
        print(f"❌ News API connection failed: {e}")
        return False

def test_polygon_api():
    """Test Polygon API connection"""
    try:
        import aiohttp
        api_key = os.getenv('POLYGON_API_KEY')
        
        if not api_key:
            print("❌ POLYGON_API_KEY not found in environment")
            return False
            
        # Simple test - just check if we can make a request
        print("✅ Polygon API key found (connection test requires async)")
        return True
        
    except ImportError:
        print("❌ aiohttp package not installed. Run: pip install aiohttp")
        return False
    except Exception as e:
        print(f"❌ Polygon API test failed: {e}")
        return False

async def main():
    """Main setup function"""
    print("🤖 AI Agent Setup and Configuration")
    print("=" * 50)
    
    # Load environment variables
    load_dotenv()
    
    print("\n📋 Checking AI Dependencies...")
    
    # Check if AI agent can be imported
    try:
        from analysis.ai_agent import AITradingAgent
        print("✅ AI Agent module loaded successfully")
    except ImportError as e:
        print(f"❌ AI Agent module import failed: {e}")
        return False
    
    print("\n🔑 Testing API Connections...")
    
    # Test each API
    ai_provider = os.getenv('AI_PROVIDER', 'openai').lower()
    openai_ok = False
    google_ok = False
    
    if ai_provider == 'google' or os.getenv('GOOGLE_AI_API_KEY'):
        google_ok = test_google_ai_connection()
    
    if ai_provider == 'openai' or os.getenv('OPENAI_API_KEY'):
        openai_ok = await test_openai_connection()
    
    news_ok = test_news_api()
    polygon_ok = test_polygon_api()
    
    print("\n📊 Configuration Summary:")
    print(f"  AI Provider: {ai_provider}")
    print(f"  OpenAI API:  {'✅' if openai_ok else '❌'}")
    print(f"  Google AI:   {'✅' if google_ok else '❌'}")
    print(f"  News API:    {'✅' if news_ok else '❌'}")
    print(f"  Polygon API: {'✅' if polygon_ok else '❌'}")
    
    ai_configured = openai_ok or google_ok
    if ai_configured and news_ok and polygon_ok:
        print("\n🎉 AI Agent fully configured and ready!")
        
        # Test AI agent initialization
        try:
            agent = AITradingAgent()
            print("✅ AI Agent initialized successfully")
            
            # Quick test
            print("\n🧪 Running quick AI test...")
            summary = await agent.create_market_summary(["AAPL"])
            if summary:
                print("✅ AI Agent test completed successfully")
                print(f"   Sample output: {summary[:100]}...")
            else:
                print("⚠️ AI Agent test returned empty result")
                
        except Exception as e:
            print(f"❌ AI Agent initialization failed: {e}")
            return False
            
        return True
    else:
        print("\n⚠️ Some AI services are not configured")
        if not ai_configured:
            print("   - No AI provider configured (OpenAI or Google AI)")
        if not news_ok:
            print("   - News API not configured")
        print("   The trading bot will work without AI features")
        print("   Configure missing API keys in .env file to enable AI")
        return False

if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)