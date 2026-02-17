"""
Sector Regime Detection Module

Detects sector rotation vs broad market regimes.
"""
from .sector_regime import (
    SECTOR_ETFS,
    SECTOR_TO_ETF,
    ETF_TO_SECTOR,
    fetch_sector_data,
    calculate_sector_momentum,
    detect_rotation_regime,
    get_stock_sector,
    map_sector_to_etf,
    filter_signal_by_regime,
    get_regime_summary_message,
    get_current_regime,
)

__all__ = [
    'SECTOR_ETFS',
    'SECTOR_TO_ETF', 
    'ETF_TO_SECTOR',
    'fetch_sector_data',
    'calculate_sector_momentum',
    'detect_rotation_regime',
    'get_stock_sector',
    'map_sector_to_etf',
    'filter_signal_by_regime',
    'get_regime_summary_message',
    'get_current_regime',
]
