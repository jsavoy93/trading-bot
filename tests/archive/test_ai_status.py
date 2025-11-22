#!/usr/bin/env python3
"""
Test AI Status Display
Quick test to show the AI status reporting feature
"""
import sys
import os
from pathlib import Path

# Add src to path
src_path = str(Path(__file__).parent / "src")
sys.path.insert(0, src_path)

from core.smart_bot import SmartTradingBot

def test_ai_status():
    """Test the AI status display"""
    print("🧪 TESTING AI STATUS DISPLAY")
    print("=" * 50)
    
    bot = SmartTradingBot()
    
    # Test the AI status display
    bot._show_ai_status(1)
    
    print("\n✅ AI Status test completed!")

if __name__ == "__main__":
    test_ai_status()