#!/usr/bin/env python3
"""
Trading Bot Dashboard Launcher

Starts the Flask API server for the web dashboard.
Open http://localhost:5000 in your browser to view the dashboard.
"""

import sys
from pathlib import Path

# Add src to path (go up 2 levels from scripts/utils to project root, then into src)
src_path = str(Path(__file__).parent.parent.parent / "src")
sys.path.insert(0, src_path)

from api.dashboard_api import run_dashboard

if __name__ == '__main__':
    print("\n" + "="*60)
    print("🌐 TRADING BOT WEB DASHBOARD")
    print("="*60)
    print("Starting API server...")
    print("Dashboard will be available at: http://localhost:5000")
    print("Open dashboard/index.html in your browser")
    print("\nPress Ctrl+C to stop")
    print("="*60 + "\n")
    
    run_dashboard(host='0.0.0.0', port=5000, debug=False)
