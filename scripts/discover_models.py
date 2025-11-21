#!/usr/bin/env python3
"""
Discover available Google AI models
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
    
    print("🔍 Discovering Google AI Models...")
    print("=" * 50)
    
    models = list(genai.list_models())
    
    print(f"📋 Found {len(models)} total models")
    print("\n✅ Models that support generateContent:")
    
    compatible_models = []
    for model in models:
        if 'generateContent' in model.supported_generation_methods:
            print(f"  • {model.name}")
            compatible_models.append(model.name)
    
    print(f"\n🎯 Testing the first compatible model...")
    
    if compatible_models:
        test_model = compatible_models[0]
        print(f"Testing: {test_model}")
        
        try:
            model = genai.GenerativeModel(test_model)
            response = model.generate_content(
                "Say 'Hello Google AI!' in exactly those words",
                generation_config={'max_output_tokens': 20, 'temperature': 0}
            )
            
            print(f"✅ Success! Response: {response.text}")
            print(f"\n💡 Use this model name: {test_model}")
            
        except Exception as e:
            print(f"❌ Test failed: {e}")
    else:
        print("❌ No compatible models found")
            
except ImportError:
    print("❌ google-generativeai not installed")
except Exception as e:
    print(f"❌ Error: {e}")