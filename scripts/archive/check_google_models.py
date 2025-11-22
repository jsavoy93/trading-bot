#!/usr/bin/env python3
"""
Check available Google AI models
"""

import os
from dotenv import load_dotenv

load_dotenv()

try:
    import google.generativeai as genai
    
    api_key = os.getenv('GOOGLE_AI_API_KEY')
    genai.configure(api_key=api_key)
    
    print("🔍 Available Google AI models:")
    models = genai.list_models()
    
    for model in models:
        if 'generateContent' in model.supported_generation_methods:
            print(f"✅ {model.name} - {model.display_name}")
            
except Exception as e:
    print(f"❌ Error: {e}")