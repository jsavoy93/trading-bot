"""
Smart Limit Order Module

Intelligent limit orders with fill monitoring.
"""
from .smart_orders import (
    QuoteData,
    get_latest_quote,
    get_vwap,
    calculate_limit_price,
    place_limit_order,
    place_market_order,
    cancel_order,
    get_order_status,
    monitor_order_fill,
    log_order_execution,
    execute_smart_order,
)

__all__ = [
    'QuoteData',
    'get_latest_quote',
    'get_vwap',
    'calculate_limit_price',
    'place_limit_order',
    'place_market_order',
    'cancel_order',
    'get_order_status',
    'monitor_order_fill',
    'log_order_execution',
    'execute_smart_order',
]
