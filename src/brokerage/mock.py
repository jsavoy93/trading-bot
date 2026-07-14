"""
Mock Brokerage Implementation for Testing

Provides fully mocked brokerage and market data clients
that return deterministic data without any API calls.
"""

import os
import pandas as pd
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Dict, Any
from dataclasses import dataclass


# Check if we're running in test mode
def is_test_environment() -> bool:
    """Detect if we're running in a test environment."""
    return (
        'PYTEST_CURRENT_TEST' in os.environ or
        'TESTING' in os.environ or
        'UNIT_TESTING' in os.environ
    )


class MockAsset:
    """Mock asset object."""
    def __init__(self, symbol: str, tradable: bool = True, status: str = 'ACTIVE', asset_class: str = 'US_EQUITY'):
        self.symbol = symbol
        self.tradable = tradable
        self.status = status
        self.asset_class = asset_class


class MockPosition:
    """Mock position object."""
    def __init__(self, symbol: str, qty: float, avg_entry_price: float = 100.0):
        self.symbol = symbol
        self.qty = qty
        self.avg_entry_price = avg_entry_price
        self.unrealized_pl = 0.0
        self.unrealized_plpc = 0.0


class MockOrder:
    """Mock order object."""
    def __init__(self, symbol: str, qty: int, side: str, order_id: str = None):
        self.id = order_id or f"mock_order_{symbol}_{datetime.now().timestamp()}"
        self.symbol = symbol
        self.qty = qty
        self.side = side
        self.status = 'accepted'


class MockAccount:
    """Mock account object."""
    def __init__(self):
        self.cash = '100000.00'
        self.portfolio_value = '100000.00'
        self.buying_power = '100000.00'
        self.status = 'ACTIVE'


@dataclass
class MockBar:
    """Mock OHLCV bar."""
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


class MockStockBarsResponse:
    """Mock StockBars response from Alpaca."""
    def __init__(self, data: Dict[str, List[MockBar]]):
        self.data = data


class MockMarketDataClient:
    """
    Mock market data client that returns deterministic data.
    No API calls are made.
    """
    
    def __init__(self, api_key: str = None, api_secret: str = None):
        pass

    def get_stock_bars(self, request: Any) -> MockStockBarsResponse:
        """
        Return mock stock bars.
        
        Args:
            request: StockBarsRequest with symbol_or_symbols, timeframe, start, end
            
        Returns:
            MockStockBarsResponse with deterministic data
        """
        symbols = request.symbol_or_symbols if hasattr(request, 'symbol_or_symbols') else [request.symbol]
        if isinstance(symbols, str):
            symbols = [symbols]
            
        timeframe = request.timeframe if hasattr(request, 'timeframe') else None
        start = request.start if hasattr(request, 'start') else datetime.now() - timedelta(days=100)
        end = request.end if hasattr(request, 'end') else datetime.now()
        
        result = {}
        for symbol in symbols:
            bars = self._generate_mock_bars(symbol, start, end)
            result[symbol] = bars
            
        return MockStockBarsResponse(data=result)

    def _generate_mock_bars(self, symbol: str, start: datetime, end: datetime) -> List[MockBar]:
        """Generate deterministic mock bars."""
        import random
        random.seed(hash(symbol) % (2**32))
        
        bars = []
        days = (end - start).days
        current_date = start
        base_price = 100.0 + (hash(symbol) % 200)
        
        for i in range(min(days, 100)):
            open_price = base_price + random.uniform(-2, 2)
            close_price = open_price + random.uniform(-2, 2)
            
            # Ensure high is max of open/close + random
            high_price = max(open_price, close_price) + random.uniform(0, 3)
            # Ensure low is min of open/close - random
            low_price = min(open_price, close_price) - random.uniform(0, 3)
            
            # Sanity check
            assert high_price >= open_price, f"high {high_price} < open {open_price}"
            assert high_price >= close_price, f"high {high_price} < close {close_price}"
            assert low_price <= open_price, f"low {low_price} > open {open_price}"
            assert low_price <= close_price, f"low {low_price} > close {close_price}"
            
            volume = random.randint(1000000, 10000000)
            
            bars.append(MockBar(
                timestamp=current_date + timedelta(days=i),
                open=open_price,
                high=high_price,
                low=low_price,
                close=close_price,
                volume=volume
            ))
            
        return bars

    def getLatestData(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get latest mock data for a symbol."""
        bars = self._generate_mock_bars(
            symbol,
            datetime.now(timezone.utc) - timedelta(days=100),
            datetime.now(timezone.utc)
        )
        if bars:
            latest = bars[-1]
            return {
                'open': latest.open,
                'high': latest.high,
                'low': latest.low,
                'close': latest.close,
                'volume': latest.volume,
                'timestamp': latest.timestamp
            }
        return None


class MockBrokerageClient:
    """
    Mock brokerage client for testing.
    
    This client is completely safe for testing - it never makes
    any live API calls and returns deterministic mock data.
    """
    
    def __init__(
        self,
        api_key: str = None,
        secret_key: str = None,
        paper: bool = True,
        test_mode: bool = True
    ):
        """
        Initialize mock brokerage client.
        
        Args:
            api_key: Ignored for mock client
            secret_key: Ignored for mock client
            paper: Ignored (always paper-like behavior)
            test_mode: If True, enforces test safety checks
        """
        self._test_mode = test_mode
        self._positions = {}  # symbol -> MockPosition
        self._orders = []  # List of MockOrder
        self._assets = self._generate_mock_assets()

    def _generate_mock_assets(self) -> List[MockAsset]:
        """Generate a set of common mock assets."""
        common_symbols = [
            'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'TSLA', 'META', 'NVDA', 'AMD',
            'NFLX', 'DIS', 'JPM', 'BAC', 'WMT', 'PG', 'KO', 'PFE', 'T', 'VZ',
            'SPY', 'QQQ', 'IWM', 'DIA'
        ]
        return [MockAsset(symbol=s) for s in common_symbols]

    def get_all_assets(self) -> List[MockAsset]:
        """Return mock assets."""
        return self._assets

    def get_all_positions(self) -> List[MockPosition]:
        """Return current mock positions."""
        return list(self._positions.values())

    def get_open_position(self, symbol: str) -> Optional[MockPosition]:
        """Get mock position for symbol."""
        return self._positions.get(symbol)

    def get_orders(self, status: str = 'all') -> List[MockOrder]:
        """Return mock orders."""
        return self._orders

    def get_account(self) -> MockAccount:
        """Return mock account."""
        return MockAccount()

    def submit_order(self, order_data: Any) -> MockOrder:
        """
        Submit a mock order.
        
        Args:
            order_data: MarketOrderRequest or similar with symbol, qty, side
            
        Returns:
            MockOrder
        """
        symbol = order_data.symbol if hasattr(order_data, 'symbol') else order_data.get('symbol')
        qty = order_data.qty if hasattr(order_data, 'qty') else order_data.get('qty')
        side = order_data.side if hasattr(order_data, 'side') else order_data.get('side')
        
        order = MockOrder(symbol=symbol, qty=qty, side=str(side).split('.')[-1].lower())
        self._orders.append(order)
        
        # Update mock positions
        if side and 'sell' in str(side).lower():
            if symbol in self._positions:
                self._positions[symbol].qty = max(0, self._positions[symbol].qty - qty)
        else:
            if symbol in self._positions:
                self._positions[symbol].qty += qty
            else:
                self._positions[symbol] = MockPosition(symbol=symbol, qty=qty)
                
        return order

    def is_live_mode(self) -> bool:
        """Always returns False for mock client."""
        return False

    def is_test_mode(self) -> bool:
        """Returns True if test mode is enabled."""
        return self._test_mode


# Convenience function to create a test-safe brokerage setup
def create_mock_brokerage(test_mode: bool = True) -> tuple:
    """
    Create a fully mocked brokerage setup.
    
    Returns:
        Tuple of (BrokerageClient, MarketDataClient)
    """
    brokerage = MockBrokerageClient(test_mode=test_mode)
    market_data = MockMarketDataClient()
    return brokerage, market_data
