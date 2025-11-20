#!/usr/bin/env python3
"""
Database Migration Tool - Main Entry Point
"""
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Import and run the migrate script
sys.path.insert(0, str(Path(__file__).parent))
from migrate import main

if __name__ == "__main__":
    main()