"""
Brokerage Abstraction Layer

Provides interfaces for different brokerage implementations:
- Live trading (restricted in tests)
- Paper trading (safe for testing)
- Mock (pure unit testing)
"""

from src.brokerage.base import BrokerageClient, MarketDataClient
from src.brokerage.mock import MockBrokerageClient, MockMarketDataClient

__all__ = [
    'BrokerageClient',
    'MarketDataClient',
    'MockBrokerageClient',
    'MockMarketDataClient',
]
