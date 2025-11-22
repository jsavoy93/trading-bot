#!/usr/bin/env python3
"""
Test HF with latest huggingface_hub
"""

import os
from dotenv import load_dotenv

load_dotenv()

def test_hf_hub():
    """Test with latest huggingface_hub client"""
    try:
        from huggingface_hub import InferenceClient
        
        api_key = os.getenv('HUGGINGFACE_API_KEY')
        
        # Create client with explicit endpoint
        client = InferenceClient(token=api_key)
        
        # Try text generation with a simple model
        prompt = "AAPL stock analysis: "
        
        # Use text generation with a model that should work
        response = client.text_generation(
            prompt,
            model="microsoft/DialoGPT-medium",
            max_new_tokens=30
        )
        
        print(f"✅ Success: {response}")
        return True
            
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

def test_hf_transformers():
    """Alternative: Use transformers pipeline"""
    try:
        # This would be local inference, not API
        from transformers import pipeline
        
        # This requires downloading the model locally
        generator = pipeline('text-generation', model='gpt2')
        response = generator("AAPL stock analysis:", max_length=50, num_return_sequences=1)
        
        print(f"✅ Local success: {response}")
        return True
        
    except Exception as e:
        print(f"❌ Transformers error: {e}")
        return False

if __name__ == "__main__":
    print("🔧 TESTING HF ALTERNATIVES")
    print("\n1. Testing hub client...")
    hub_works = test_hf_hub()
    
    print("\n2. Testing local transformers...")
    trans_works = test_hf_transformers()
    
    print(f"\nResults:")
    print(f"Hub: {'✅' if hub_works else '❌'}")
    print(f"Transformers: {'✅' if trans_works else '❌'}")