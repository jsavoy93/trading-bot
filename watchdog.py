#!/usr/bin/env python3
"""Watchdog script to keep trading bot and dashboard running 24/7"""

import subprocess
import time
import sys
import os
from pathlib import Path

WORK_DIR = "/home/ubuntu/.openclaw/workspace/trading-bot"

def get_pids(pattern):
    """Get PIDs matching pattern"""
    try:
        result = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True)
        if result.stdout.strip():
            return result.stdout.strip().split('\n')
        return []
    except Exception:
        return []

def start_process(cmd, name):
    """Start a process with nohup"""
    try:
        subprocess.Popen(
            cmd,
            cwd=WORK_DIR,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True
        )
        print(f"✅ Started {name}")
    except Exception as e:
        print(f"❌ Failed to start {name}: {e}")

def main():
    print("🛡️ Trading Bot Watchdog Started")
    print("=" * 40)
    
    while True:
        # Check watchdog itself - restart if needed
        watchdog_pids = get_pids("watchdog.py")
        if not watchdog_pids or (len(watchdog_pids) == 1 and not get_pids("main.py")):
            # Watchdog not running or only 1 process (the check), restart watchdog
            start_process(["python3", "watchdog.py"], "watchdog")
        
        # Check bot
        bot_pids = get_pids("main.py")
        if not bot_pids:
            print("🔴 Bot not running, starting...")
            start_process(["python3", "main.py", "-c", "-d", "10"], "bot")
        else:
            print(f"✅ Bot running (PID: {bot_pids[0]})")
        
        # Check dashboard
        dash_pids = get_pids("dashboard.py")
        if not dash_pids:
            print("🔴 Dashboard not running, starting...")
            start_process(["python3", "dashboard.py"], "dashboard")
        else:
            print(f"✅ Dashboard running (PID: {dash_pids[0]})")
        
        time.sleep(30)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n🛑 Watchdog stopped")
        sys.exit(0)
