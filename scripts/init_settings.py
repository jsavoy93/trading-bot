#!/usr/bin/env python3
"""
Seed the settings table with Josh's current live dashboard values.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from core.settings_service import save

# Josh's current live dashboard values (captured 2026-06-18)
SETTINGS = {
    "rsi_buy_threshold":           ("20",   "int"),
    "rsi_sell_threshold":          ("70",   "int"),
    "min_score_buy":              ("55",   "int"),
    "rotation_threshold":         ("20",   "int"),
    "sma_fast":                   ("10",   "int"),
    "sma_slow":                   ("30",   "int"),
    "rsi_period":                 ("14",   "int"),
    "enable_multi_timeframe":     ("true", "bool"),
    "hourly_weight":              ("0.3",  "float"),
    "enable_volume_confirmation": ("true", "bool"),
    "enable_regime_filter":       ("false","bool"),
    "enable_sp_filter":           ("false","bool"),
    "enable_liquidity_filter":    ("true", "bool"),
    "enable_sector_filter":       ("false","bool"),
    "loop_delay_seconds":         ("300",  "int"),
    "atr_position_size_pct":      ("2.0",  "float"),
    "max_sector_concentration":   ("0.5",  "float"),
    "max_correlation":            ("0.7",  "float"),
    "max_portfolio_beta":         ("1.5",  "float"),
    "max_drawdown_pct":           ("10.0", "float"),
    "daily_loss_limit_pct":       ("5.0",  "float"),
}

if __name__ == "__main__":
    for key, (val, _type) in SETTINGS.items():
        save(key, val)
        print(f"  saved {key} = {val}")
    print("Done.")
