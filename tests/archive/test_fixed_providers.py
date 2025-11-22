#!/usr/bin/env python3
"""
Test Fixed Mistral and Hugging Face
"""

import os
import sys
from dotenv import load_dotenv

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

# Load environment variables
load_dotenv()

def test_mistral_direct():
    """Test Mistral with new API"""
    try:
        from mistralai import Mistral
        
        api_key = os.getenv('MISTRAL_API_KEY')
        if not api_key:
            print("❌ No Mistral API key")
            return False
            
        print(f"✅ Mistral API key found: {api_key[:10]}...")
        
        client = Mistral(api_key=api_key)
        print("✅ Mistral client created")
        
        messages = [
            {"role": "system", "content": "You are a financial analyst."},
            {"role": "user", "content": "Analyze AAPL stock in one sentence."}
        ]
        
        response = client.chat.complete(
            model="mistral-small-latest",
            messages=messages,
            max_tokens=50
        )
        
        result = response.choices[0].message.content
        print(f"✅ Mistral response: {result}")
        return True
        
    except Exception as e:
        print(f"❌ Mistral error: {e}")
        return False

def test_huggingface_direct():
    """Test Hugging Face with different approach"""
    try:
        from huggingface_hub import InferenceClient
        
        api_key = os.getenv('HUGGINGFACE_API_KEY')
        if not api_key:
            print("❌ No Hugging Face API key")
            return False
            
        print(f"✅ Hugging Face API key found: {api_key[:10]}...")
        
        client = InferenceClient(token=api_key)
        print("✅ Hugging Face client created")
        
        # Try a simpler model that works better with the API
        prompt = "Financial analysis: AAPL stock is"
        response = client.text_generation(
            prompt=prompt,
            model="gpt2",
            max_new_tokens=30,
            temperature=0.7
        )
        
        print(f"✅ Hugging Face response: {response}")
        return True
        
    except Exception as e:
        print(f"❌ Hugging Face error: {e}")
        return False

if __name__ == "__main__":
    print("🔧 TESTING FIXED PROVIDERS")
    print("=" * 40)
    
    print("\n🟣 Testing Mistral (new API)...")
    mistral_works = test_mistral_direct()
    
    print("\n🟡 Testing Hugging Face (simple model)...")
    hf_works = test_huggingface_direct()
    
    print(f"\n📊 RESULTS:")
    print(f"Mistral: {'✅ WORKS' if mistral_works else '❌ FAILED'}")
    print(f"Hugging Face: {'✅ WORKS' if hf_works else '❌ FAILED'}")