#!/usr/bin/env python3
"""
Debug Hugging Face API
"""

import os
from dotenv import load_dotenv
import requests

load_dotenv()

def test_hf_direct_api():
    """Test HF with direct HTTP API"""
    try:
        api_key = os.getenv('HUGGINGFACE_API_KEY')
        
        # Try the Inference API directly
        headers = {"Authorization": f"Bearer {api_key}"}
        
        # Test with a working text generation model
        url = "https://api-inference.huggingface.co/models/microsoft/DialoGPT-medium"
        
        payload = {
            "inputs": "Financial analysis for AAPL:",
            "parameters": {
                "max_new_tokens": 30,
                "temperature": 0.7,
                "return_full_text": False
            }
        }
        
        response = requests.post(url, headers=headers, json=payload)
        
        print(f"Status: {response.status_code}")
        print(f"Response: {response.text}")
        
        if response.status_code == 200:
            result = response.json()
            return True
        else:
            print(f"HTTP Error: {response.status_code}")
            return False
            
    except Exception as e:
        print(f"Error: {e}")
        return False

if __name__ == "__main__":
    print("🔍 DEBUGGING HUGGING FACE API")
    test_hf_direct_api()