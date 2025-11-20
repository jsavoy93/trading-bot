#!/usr/bin/env python3
"""
Trading Bot - Main Entry Point
Enhanced trading bot with Alpaca API integration and Supabase database support.
"""
import sys
import os
from pathlib import Path

# Add src to path
src_path = str(Path(__file__).parent / "src")
sys.path.insert(0, src_path)

# Change to project directory to ensure relative imports work
os.chdir(Path(__file__).parent)

from core.smart_bot import main

if __name__ == "__main__":
    main()