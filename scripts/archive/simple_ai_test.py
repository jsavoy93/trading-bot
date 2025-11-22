#!/usr/bin/env python3
"""
Simple Google AI Test
"""

import os
import sys
from dotenv import load_dotenv

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

# Load environment variables
load_dotenv()

def test_google_simple():
    """Simple test of Google AI"""
    try:
        import google.generativeai as genai
        
        # Get API key
        api_key = os.getenv('GOOGLE_AI_API_KEY')
        if not api_key:
            print("❌ No Google AI API key found")
            return False
            
        print(f"✅ Google AI API key found: {api_key[:10]}...")
        
        # Configure the API
        genai.configure(api_key=api_key)
        
        # Create model
        model = genai.GenerativeModel('gemini-2.0-flash')
        print("✅ Google AI model created")
        
        # Test simple generation
        response = model.generate_content("Say 'Hello from Google AI' in exactly 5 words.")
        result = response.text.strip()
        
        print(f"✅ Google AI response: {result}")
        return True
        
    except Exception as e:
        print(f"❌ Google AI error: {e}")
        return False

def test_cohere_simple():
    """Simple test of Cohere"""
    try:
        import cohere
        
        # Get API key
        api_key = os.getenv('COHERE_API_KEY')
        if not api_key:
            print("❌ No Cohere API key found")
            return False
            
        print(f"✅ Cohere API key found: {api_key[:10]}...")
        
        # Create client
        co = cohere.Client(api_key)
        print("✅ Cohere client created")
        
        # Test simple generation using Chat API
        response = co.chat(
            message="Say 'Hello from Cohere' in exactly 4 words:",
            max_tokens=20
        )
        result = response.text.strip()
        
        print(f"✅ Cohere response: {result}")
        return True
        
    except Exception as e:
        print(f"❌ Cohere error: {e}")
        return False

def test_openrouter_simple():
    """Simple test of OpenRouter"""
    try:
        import requests
        
        # Get API key
        api_key = os.getenv('OPENROUTER_API_KEY')
        if not api_key:
            print("❌ No OpenRouter API key found")
            return False
            
        print(f"✅ OpenRouter API key found: {api_key[:10]}...")
        
        # Test simple generation
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            json={
                "model": "microsoft/wizardlm-2-8x22b",
                "messages": [{"role": "user", "content": "Say 'Hello from OpenRouter' in exactly 4 words:"}],
                "max_tokens": 20
            }
        )
        
        if response.status_code == 200:
            result = response.json()
            message = result['choices'][0]['message']['content']
            print(f"✅ OpenRouter response: {message}")
            return True
        else:
            print(f"❌ OpenRouter HTTP error: {response.status_code} - {response.text}")
            return False
        
    except Exception as e:
        print(f"❌ OpenRouter error: {e}")
        return False

def test_huggingface_simple():
    """Simple test of Hugging Face"""
    try:
        from huggingface_hub import InferenceClient
        
        # Get API key
        api_key = os.getenv('HUGGINGFACE_API_KEY')
        if not api_key:
            print("❌ No Hugging Face API key found")
            return False
            
        print(f"✅ Hugging Face API key found: {api_key[:10]}...")
        
        # Create client
        client = InferenceClient(token=api_key)
        print("✅ Hugging Face client created")
        
        # Test simple generation
        response = client.text_generation(
            "Say 'Hello from Hugging Face':",
            model="gpt2",
            max_new_tokens=10
        )
        
        print(f"✅ Hugging Face response: {response}")
        return True
        
    except Exception as e:
        print(f"❌ Hugging Face error: {e}")
        return False

if __name__ == "__main__":
    print("🧪 SIMPLE AI PROVIDER TESTS")
    print("=" * 40)
    
    print("\n🔵 Testing Google AI...")
    google_works = test_google_simple()
    
    print("\n🟣 Testing Cohere...")
    cohere_works = test_cohere_simple()
    
    print("\n🟠 Testing OpenRouter...")
    openrouter_works = test_openrouter_simple()
    
    print("\n🟡 Testing Hugging Face...")
    hf_works = test_huggingface_simple()
    
    print("\n📊 SUMMARY:")
    print(f"Google AI: {'✅ WORKS' if google_works else '❌ FAILED'}")
    print(f"Cohere: {'✅ WORKS' if cohere_works else '❌ FAILED'}")
    print(f"OpenRouter: {'✅ WORKS' if openrouter_works else '❌ FAILED'}")
    print(f"Hugging Face: {'✅ WORKS' if hf_works else '❌ FAILED'}")