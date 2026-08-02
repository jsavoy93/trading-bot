"""
Brokerage Abstraction Layer - Base Interfaces

Defines abstract interfaces for brokerage clients to allow
paper trading, mock, or live implementations.
"""

from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any
import pandas as pd


class MarketDataClient(ABC):
    """Abstract interface for market data retrieval."""

    @abstractmethod
    def get_stock_bars(self, request: Any) -> Any:
        """
        Get historical stock bars.
        
        Args:
            request: StockBarsRequest or compatible request object
            
        Returns:
            StockBars response or compatible object with .data attribute
        """
        pass

    @abstractmethod
    def getLatestData(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Get the latest market data for a symbol.
        
        Returns:
            Dict with keys: open, high, low, close, volume, timestamp
        """
        pass


class BrokerageClient(ABC):
    """
    Abstract interface for brokerage trading operations.
    
    All implementations should be safe for testing by default.
    Live trading implementations should raise errors when used in test mode.
    """

    @abstractmethod
    def get_all_assets(self) -> List[Any]:
        """Get all tradeable assets."""
        pass

    @abstractmethod
    def get_all_positions(self) -> List[Any]:
        """Get all open positions."""
        pass

    @abstractmethod
    def get_open_position(self, symbol: str) -> Optional[Any]:
        """Get open position for a specific symbol."""
        pass

    @abstractmethod
    def get_orders(self, status: str = 'all') -> List[Any]:
        """Get orders, optionally filtered by status."""
        pass

    @abstractmethod
    def get_account(self) -> Any:
        """Get account information."""
        pass

    @abstractmethod
    def submit_order(self, order_data: Any) -> Any:
        """Submit a new order."""
        pass

    @abstractmethod
    def is_live_mode(self) -> bool:
        """Return True if this is a live trading client."""
        pass

    @abstractmethod
    def is_test_mode(self) -> bool:
        """Return True if this client should block live trading."""
        pass
