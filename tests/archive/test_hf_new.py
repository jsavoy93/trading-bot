#!/usr/bin/env python3
"""
Test Updated Hugging Face
"""

import os
from dotenv import load_dotenv
import requests

load_dotenv()

def test_hf_new_endpoint():
    """Test HF with new router endpoint"""
    try:
        api_key = os.getenv('HUGGINGFACE_API_KEY')
        
        headers = {"Authorization": f"Bearer {api_key}"}
        
        # Test with the new router endpoint
        url = "https://router.huggingface.co/models/gpt2"
        
        payload = {
            "inputs": "Financial analysis request: Analyze AAPL stock briefly.\n\nAnalysis:",
            "parameters": {
                "max_new_tokens": 50,
                "temperature": 0.7,
                "return_full_text": False,
                "do_sample": True
            }
        }
        
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        
        print(f"Status: {response.status_code}")
        print(f"Response: {response.text[:500]}...")
        
        if response.status_code == 200:
            result = response.json()
            if isinstance(result, list) and len(result) > 0:
                generated_text = result[0].get('generated_text', '').strip()
                print(f"✅ Generated: {generated_text}")
                return True
        
        return False
            
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

if __name__ == "__main__":
    print("🔧 TESTING UPDATED HUGGING FACE")
    test_hf_new_endpoint()