#!/usr/bin/env python3
"""
Simple AI Connection Test
Tests if AI providers are working with your configured API keys
"""

import os
import sys
import asyncio
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotenv import load_dotenv

def test_ai_connections():
    """Test all AI connections with a simple prompt"""
    load_dotenv()
    
    print("🧪 AI Connection Test")
    print("=" * 50)
    
    # Test OpenAI
    openai_key = os.getenv('OPENAI_API_KEY')
    if openai_key:
        print("\n🔑 Testing OpenAI Connection...")
        try:
            import openai
            client = openai.OpenAI(api_key=openai_key)
            
            response = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "user", "content": "Say 'OpenAI connected' in exactly those words"}
                ],
                max_tokens=10,
                temperature=0
            )
            
            result = response.choices[0].message.content.strip()
            if "OpenAI connected" in result:
                print("✅ OpenAI: Connected and responding correctly")
                print(f"   Response: {result}")
            else:
                print(f"✅ OpenAI: Connected but unexpected response: {result}")
                
        except ImportError:
            print("❌ OpenAI: Package not installed (pip install openai)")
        except Exception as e:
            print(f"❌ OpenAI: Connection failed - {e}")
    else:
        print("⚪ OpenAI: API key not configured")
    
    # Test Google AI
    google_key = os.getenv('GOOGLE_AI_API_KEY')
    if google_key:
        print("\n🔑 Testing Google AI Connection...")
        try:
            import google.generativeai as genai
            genai.configure(api_key=google_key)
            
            # Try multiple models
            models_to_try = [
                'gemini-1.5-flash-latest', 
                'gemini-1.5-flash', 
                'gemini-pro',
                'models/gemini-1.5-flash-latest',
                'models/gemini-1.5-flash',
                'models/gemini-pro'
            ]
            
            success = False
            for model_name in models_to_try:
                try:
                    model = genai.GenerativeModel(model_name)
                    response = model.generate_content(
                        "Say 'Google AI connected' in exactly those words",
                        generation_config={
                            'max_output_tokens': 10,
                            'temperature': 0
                        }
                    )
                    
                    if response.text and "Google AI connected" in response.text:
                        print(f"✅ Google AI: Connected using {model_name}")
                        print(f"   Response: {response.text.strip()}")
                        success = True
                        break
                    elif response.text:
                        print(f"✅ Google AI: Connected using {model_name} (unexpected response)")
                        print(f"   Response: {response.text.strip()}")  
                        success = True
                        break
                        
                except Exception as model_error:
                    continue
            
            if not success:
                print("❌ Google AI: No compatible models found")
                print("   Available models might have different names")
                
        except ImportError:
            print("❌ Google AI: Package not installed (pip install google-generativeai)")
        except Exception as e:
            print(f"❌ Google AI: Connection failed - {e}")
    else:
        print("⚪ Google AI: API key not configured")
    
    # Test News API
    news_key = os.getenv('NEWS_API_KEY')
    if news_key:
        print("\n🔑 Testing News API Connection...")
        try:
            from newsapi import NewsApiClient
            newsapi = NewsApiClient(api_key=news_key)
            
            response = newsapi.get_top_headlines(
                category='business',
                language='en',
                country='us',
                page_size=1
            )
            
            if response.get('status') == 'ok' and response.get('articles'):
                print("✅ News API: Connected and returning articles")
                print(f"   Found {response.get('totalResults', 0)} articles")
            else:
                print(f"❌ News API: Connected but no articles returned")
                
        except ImportError:
            print("❌ News API: Package not installed (pip install newsapi-python)")
        except Exception as e:
            print(f"❌ News API: Connection failed - {e}")
    else:
        print("⚪ News API: API key not configured")
    
    print("\n📊 Summary:")
    configured_services = sum([
        bool(openai_key),
        bool(google_key), 
        bool(news_key)
    ])
    
    if configured_services == 0:
        print("   No AI services configured")
        print("   Add API keys to .env file to enable AI features")
    elif configured_services < 3:
        print(f"   {configured_services}/3 services configured")
        print("   Configure remaining services for full AI capabilities")
    else:
        print("   All AI services configured!")
    
    print("\n💡 To configure AI services:")
    print("   1. Copy .env.example to .env")  
    print("   2. Add your API keys to .env")
    print("   3. Run this test again")
    
    return configured_services > 0

if __name__ == "__main__":
    test_ai_connections()