#!/usr/bin/env python3
"""Patch dashboard.py to add settings API and failed-analyses API."""
import re

with open('dashboard.py', 'r') as f:
    content = f.read()

# ── 1. Add _TRADING_PARAMS before get_smart_bot ───────────────────────────────
TRADING_PARAMS = '''
# Trading parameters — editable via dashboard
_TRADING_PARAMS = {
    "rsi_buy_threshold":          {"value": 30,    "min": 10,     "max": 50,     "step": 1,  "type": "int",   "default": 30,   "description": "RSI must be below this to generate a BUY signal.",                    "category": "Signal Thresholds"},
    "rsi_sell_threshold":         {"value": 70,    "min": 50,     "max": 90,     "step": 1,  "type": "int",   "default": 70,   "description": "RSI must be above this to generate a SELL signal.",                   "category": "Signal Thresholds"},
    "min_score_buy":               {"value": 65,    "min": 0,      "max": 100,    "step": 1,  "type": "int",   "default": 65,   "description": "Minimum total score required for a BUY signal.",                       "category": "Signal Thresholds"},
    "rotation_threshold":          {"value": 20,    "min": 0,      "max": 50,     "step": 1,  "type": "int",   "default": 20,   "description": "Rotation score advantage threshold.",                                 "category": "Signal Thresholds"},
    "sma_fast":                   {"value": 10,    "min": 2,      "max": 50,     "step": 1,  "type": "int",   "default": 10,   "description": "Fast SMA period.",                                                   "category": "Indicator Periods"},
    "sma_slow":                   {"value": 30,    "min": 5,      "max": 200,    "step": 1,  "type": "int",   "default": 30,   "description": "Slow SMA period.",                                                   "category": "Indicator Periods"},
    "rsi_period":                 {"value": 14,    "min": 2,      "max": 50,     "step": 1,  "type": "int",   "default": 14,   "description": "RSI lookback period.",                                                "category": "Indicator Periods"},
    "enable_multi_timeframe":     {"value": True,  "min": None,   "max": None,   "step": None,"type": "bool",  "default": True,  "description": "Require daily AND hourly agreement.",                             "category": "Multi-Timeframe"},
    "hourly_weight":              {"value": 0.30,  "min": 0.0,    "max": 1.0,    "step": 0.05,"type": "float", "default": 0.30, "description": "Weight for hourly signals.",                                           "category": "Multi-Timeframe"},
    "enable_volume_confirmation": {"value": True,  "min": None,   "max": None,   "step": None,"type": "bool",  "default": True,  "description": "Require volume above 20-day average.",                              "category": "Filters"},
    "enable_regime_filter":       {"value": True,  "min": None,   "max": None,   "step": None,"type": "bool",  "default": True,  "description": "ADX-based regime detection.",                                        "category": "Filters"},
    "enable_sp_filter":           {"value": True,  "min": None,   "max": None,   "step": None,"type": "bool",  "default": True,  "description": "Only buy stocks outperforming SPY.",                                 "category": "Filters"},
    "enable_liquidity_filter":    {"value": True,  "min": None,   "max": None,   "step": None,"type": "bool",  "default": True,  "description": "Skip illiquid stocks.",                                              "category": "Filters"},
    "enable_sector_filter":       {"value": True,  "min": None,   "max": None,   "step": None,"type": "bool",  "default": True,  "description": "Prefer strong sectors.",                                              "category": "Filters"},
    "atr_position_size_pct":       {"value": 2.0,   "min": 0.1,    "max": 10.0,   "step": 0.1, "type": "float", "default": 2.0,   "description": "Risk per trade as % of portfolio.",                              "category": "Risk & Sizing"},
    "max_sector_concentration":   {"value": 0.25,  "min": 0.05,   "max": 0.80,   "step": 0.05,"type": "float", "default": 0.25,  "description": "Max % in any single sector.",                                   "category": "Risk & Sizing"},
    "max_correlation":            {"value": 0.7,   "min": 0.0,    "max": 1.0,    "step": 0.05,"type": "float", "default": 0.7,   "description": "Max correlation with existing positions.",                        "category": "Risk & Sizing"},
    "max_portfolio_beta":         {"value": 1.5,   "min": 0.0,    "max": 3.0,    "step": 0.1, "type": "float", "default": 1.5,   "description": "Max portfolio beta.",                                               "category": "Risk & Sizing"},
    "max_drawdown_pct":           {"value": 10.0,  "min": 1.0,    "max": 50.0,   "step": 0.5, "type": "float", "default": 10.0,  "description": "Max drawdown before pause.",                                         "category": "Risk & Sizing"},
    "daily_loss_limit_pct":       {"value": 5.0,   "min": 0.5,    "max": 20.0,   "step": 0.5, "type": "float", "default": 5.0,   "description": "Max daily loss before stopping.",                                    "category": "Risk & Sizing"},
}
'''

old1 = "_smart_bot_instance = None\n\ndef get_smart_bot():"
new1 = "_smart_bot_instance = None\n" + TRADING_PARAMS + "\ndef get_smart_bot():"

if old1 not in content:
    print("ERROR: marker1 not found")
else:
    content = content.replace(old1, new1, 1)
    print("1. Added _TRADING_PARAMS")

# ── 2. Add settings API endpoints before __main__ ──────────────────────────────
SETTINGS_API = '''

@app.get("/api/settings")
def api_get_settings():
    """Return all trading parameters with metadata for the dashboard UI."""
    result = {}
    for key, param in _TRADING_PARAMS.items():
        result[key] = {
            "value": param["value"],
            "min": param["min"],
            "max": param["max"],
            "step": param["step"],
            "type": param["type"],
            "default": param["default"],
            "description": param["description"],
            "category": param["category"],
        }
    return result


@app.post("/api/settings")
def api_update_settings(updates: Dict):
    """Update one or more trading parameters. Returns updated values."""
    global _TRADING_PARAMS, _smart_bot_instance
    updated = {}
    for key, new_val in updates.items():
        if key not in _TRADING_PARAMS:
            continue
        param = _TRADING_PARAMS[key]
        if param["type"] in ("int", "float"):
            new_val = float(new_val)
            if param["min"] is not None and new_val < param["min"]:
                new_val = param["min"]
            if param["max"] is not None and new_val > param["max"]:
                new_val = param["max"]
            if param["type"] == "int":
                new_val = int(round(new_val))
        elif param["type"] == "bool":
            new_val = bool(new_val)
        _TRADING_PARAMS[key]["value"] = new_val
        updated[key] = new_val
        if _USE_SETTINGS_SERVICE:
            _ss_save(key, str(new_val))
        if _smart_bot_instance is not None and hasattr(_smart_bot_instance, key):
            setattr(_smart_bot_instance, key, new_val)
    return {"updated": updated, "status": "ok"}

'''

old2 = "\nif __name__ == \"__main__\":\n    import uvicorn"
new2 = SETTINGS_API + "\nif __name__ == \"__main__\":\n    import uvicorn"

if old2 not in content:
    print("ERROR: marker2 not found, trying alternative")
    # Try without the backslash
    alt = "\nif __name__ == '__main__':\n    import uvicorn"
    if alt in content:
        new2 = SETTINGS_API + "\nif __name__ == '__main__':\n    import uvicorn"
        content = content.replace(alt, new2, 1)
        print("2. Added settings API endpoints (alt)")
    else:
        print("ERROR: could not find __main__ block")
else:
    content = content.replace(old2, new2, 1)
    print("2. Added settings API endpoints")

with open('dashboard.py', 'w') as f:
    f.write(content)

# Verify syntax
import subprocess
result = subprocess.run(['python3', '-m', 'py_compile', 'dashboard.py'],
                       capture_output=True, text=True, cwd='/root/.openclaw/workspace/trading-bot')
if result.returncode == 0:
    print("Syntax OK")
else:
    print(f"Syntax error: {result.stderr}")
