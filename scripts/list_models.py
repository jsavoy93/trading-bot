#!/usr/bin/env python3
"""
List available Google AI models
"""
import os
from dotenv import load_dotenv

load_dotenv()

try:
    import google.generativeai as genai
    
    api_key = os.getenv('GOOGLE_AI_API_KEY')
    if not api_key:
        print("❌ GOOGLE_AI_API_KEY not found")
        exit(1)
    
    genai.configure(api_key=api_key)
    
    print("📋 Available Google AI Models:")
    for model in genai.list_models():
        if 'generateContent' in model.supported_generation_methods:
            print(f"  ✅ {model.name}")
        else:
            print(f"  ❌ {model.name} (no generateContent)")
            
except ImportError:
    print("❌ google-generativeai not installed")
except Exception as e:
    print(f"❌ Error: {e}")