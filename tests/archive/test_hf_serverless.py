#!/usr/bin/env python3
"""
Test HF Serverless Inference API
"""

import os
from dotenv import load_dotenv
import requests

load_dotenv()

def test_hf_serverless():
    """Test HF Serverless Inference API"""
    try:
        api_key = os.getenv('HUGGINGFACE_API_KEY')
        
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        # Try serverless inference endpoint
        url = "https://api-inference.huggingface.co/models/gpt2"
        
        payload = {
            "inputs": "AAPL stock analysis:",
            "parameters": {
                "max_new_tokens": 30,
                "temperature": 0.7
            }
        }
        
        response = requests.post(url, headers=headers, json=payload)
        
        print(f"Status: {response.status_code}")
        print(f"Response: {response.text}")
        
        if response.status_code == 200:
            result = response.json()
            print(f"✅ Success: {result}")
            return True
        
        return False
            
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

if __name__ == "__main__":
    print("🔧 TESTING HF SERVERLESS")
    test_hf_serverless()