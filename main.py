#!/usr/bin/env python3
"""
Trading Bot - Main Entry Point
Enhanced trading bot with intelligent position management.
"""
import sys
import os
from pathlib import Path

# Add src to path for imports
src_path = str(Path(__file__).parent / "src")
sys.path.insert(0, src_path)

# Single instance lock
LOCK_FILE = "/tmp/trading_bot.lock"

def check_single_instance():
    """Ensure only one instance of the bot runs at a time"""
    # Check if lock file exists from a dead process
    if os.path.exists(LOCK_FILE):
        try:
            # Try to read the PID from the lock file
            with open(LOCK_FILE, 'r') as f:
                old_pid = f.read().strip()
            # Check if that process is still running
            if old_pid:
                try:
                    os.kill(int(old_pid), 0)  # Signal 0 = check if process exists
                    print(f"❌ Bot already running (PID: {old_pid})")
                    print(f"   To stop: kill {old_pid}")
                    sys.exit(1)
                except (OSError, ProcessLookupError):
                    # Process is dead, remove stale lock
                    os.remove(LOCK_FILE)
        except Exception:
            # Lock file corrupted, remove it
            try:
                os.remove(LOCK_FILE)
            except:
                pass
    
    # Create lock file with current PID
    with open(LOCK_FILE, 'w') as f:
        f.write(str(os.getpid()))
    
    # Register cleanup on exit
    import atexit
    atexit.register(lambda: os.path.exists(LOCK_FILE) and os.remove(LOCK_FILE))

# Check for single instance before importing
check_single_instance()

from core.smart_bot import main

if __name__ == "__main__":
    main()
