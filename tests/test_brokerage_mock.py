"""
TEST-001: Test Mock Brokerage Client Functionality

Tests that the mock brokerage and market data clients
work correctly and return deterministic data.
"""

import pytest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace


class TestMockMarketDataClient:
    """Tests for the mock market data client."""

    def test_get_stock_bars_returns_response(self, mock_market_data):
        """Verify get_stock_bars returns a valid response."""
        from src.brokerage.mock import MockStockBarsResponse
        
        # Create a minimal request object
        class MockRequest:
            symbol_or_symbols = ['AAPL']
            timeframe = type('obj', (object,), {'Day': '1D'})()
            start = datetime.now() - timedelta(days=100)
            end = datetime.now()
        
        response = mock_market_data.get_stock_bars(MockRequest())
        
        assert isinstance(response, MockStockBarsResponse)
        assert 'AAPL' in response.data
        assert len(response.data['AAPL']) > 0

    def test_get_stock_bars_multiple_symbols(self, mock_market_data):
        """Verify get_stock_bars handles multiple symbols."""
        class MockRequest:
            symbol_or_symbols = ['AAPL', 'MSFT', 'GOOGL']
            timeframe = type('obj', (object,), {'Day': '1D'})()
            start = datetime.now() - timedelta(days=100)
            end = datetime.now()
        
        response = mock_market_data.get_stock_bars(MockRequest())
        
        assert 'AAPL' in response.data
        assert 'MSFT' in response.data
        assert 'GOOGL' in response.data

    def test_get_latest_data(self, mock_market_data):
        """Verify getLatestData returns valid data."""
        data = mock_market_data.getLatestData('AAPL')
        
        assert data is not None
        assert 'open' in data
        assert 'high' in data
        assert 'low' in data
        assert 'close' in data
        assert 'volume' in data
        assert 'timestamp' in data
        
        # Values should be numeric
        assert isinstance(data['close'], float)
        assert isinstance(data['volume'], int)

    def test_mock_data_is_deterministic(self, mock_market_data):
        """Verify mock data is deterministic (same input = same output)."""
        class MockRequest:
            symbol_or_symbols = ['AAPL']
            timeframe = type('obj', (object,), {'Day': '1D'})()
            start = datetime.now() - timedelta(days=100)
            end = datetime.now()
        
        response1 = mock_market_data.get_stock_bars(MockRequest())
        response2 = mock_market_data.get_stock_bars(MockRequest())
        
        # Same symbol should produce same number of bars
        assert len(response1.data['AAPL']) == len(response2.data['AAPL'])


class TestMockBrokerageClient:
    """Tests for the mock brokerage trading client."""

    def test_get_all_assets(self, mock_brokerage_client):
        """Verify get_all_assets returns mock assets."""
        assets = mock_brokerage_client.get_all_assets()
        
        assert isinstance(assets, list)
        assert len(assets) > 0
        
        # Should contain common symbols
        symbols = [a.symbol for a in assets]
        assert 'AAPL' in symbols
        assert 'SPY' in symbols

    def test_get_all_positions_empty(self, mock_brokerage_client):
        """Verify get_all_positions returns empty list initially."""
        positions = mock_brokerage_client.get_all_positions()
        
        assert isinstance(positions, list)
        assert len(positions) == 0

    def test_submit_order(self, mock_brokerage_client):
        """Verify submit_order creates a mock order."""
        # Use simple dict-like objects instead of complex type constructs
        order_req = SimpleNamespace(symbol='AAPL', qty=10, side='buy')
        
        order = mock_brokerage_client.submit_order(order_req)
        
        assert order is not None
        assert order.symbol == 'AAPL'
        assert order.qty == 10

    def test_order_updates_position(self, mock_brokerage_client):
        """Verify submitting a buy order updates positions."""
        order_req = SimpleNamespace(symbol='AAPL', qty=10, side='buy')
        
        mock_brokerage_client.submit_order(order_req)
        
        positions = mock_brokerage_client.get_all_positions()
        assert len(positions) == 1
        assert positions[0].symbol == 'AAPL'
        assert positions[0].qty == 10

    def test_sell_order_reduces_position(self, mock_brokerage_client):
        """Verify submitting a sell order reduces position."""
        # First buy
        buy_req = SimpleNamespace(symbol='AAPL', qty=10, side='buy')
        mock_brokerage_client.submit_order(buy_req)
        
        # Then sell
        sell_req = SimpleNamespace(symbol='AAPL', qty=5, side='sell')
        mock_brokerage_client.submit_order(sell_req)
        
        positions = mock_brokerage_client.get_all_positions()
        assert len(positions) == 1
        assert positions[0].symbol == 'AAPL'
        assert positions[0].qty == 5

    def test_get_account(self, mock_brokerage_client):
        """Verify get_account returns mock account info."""
        account = mock_brokerage_client.get_account()
        
        assert account is not None
        assert hasattr(account, 'cash')
        assert hasattr(account, 'portfolio_value')
        assert hasattr(account, 'buying_power')

    def test_is_test_mode(self, mock_brokerage_client):
        """Verify is_test_mode returns True."""
        assert mock_brokerage_client.is_test_mode() == True

    def test_is_live_mode(self, mock_brokerage_client):
        """Verify is_live_mode returns False."""
        assert mock_brokerage_client.is_live_mode() == False


class TestMockDataQuality:
    """Tests to verify mock data quality for analysis."""

    def test_mock_bars_have_required_fields(self, mock_market_data):
        """Verify mock bars have all required OHLCV fields."""
        class MockRequest:
            symbol_or_symbols = ['AAPL']
            timeframe = type('obj', (object,), {'Day': '1D'})()
            start = datetime.now() - timedelta(days=100)
            end = datetime.now()
        
        response = mock_market_data.get_stock_bars(MockRequest())
        bars = response.data['AAPL']
        
        assert len(bars) > 0
        bar = bars[0]
        
        assert hasattr(bar, 'open')
        assert hasattr(bar, 'high')
        assert hasattr(bar, 'low')
        assert hasattr(bar, 'close')
        assert hasattr(bar, 'volume')
        assert hasattr(bar, 'timestamp')

    def test_mock_bars_are_chronological(self, mock_market_data):
        """Verify mock bars are in chronological order."""
        class MockRequest:
            symbol_or_symbols = ['AAPL']
            timeframe = type('obj', (object,), {'Day': '1D'})()
            start = datetime.now() - timedelta(days=100)
            end = datetime.now()
        
        response = mock_market_data.get_stock_bars(MockRequest())
        bars = response.data['AAPL']
        
        for i in range(len(bars) - 1):
            assert bars[i].timestamp <= bars[i+1].timestamp

    def test_mock_bars_high_ge_open_and_close(self, mock_market_data):
        """Verify mock bars have high >= open and close."""
        class MockRequest:
            symbol_or_symbols = ['AAPL']
            timeframe = type('obj', (object,), {'Day': '1D'})()
            start = datetime.now() - timedelta(days=100)
            end = datetime.now()
        
        response = mock_market_data.get_stock_bars(MockRequest())
        bars = response.data['AAPL']
        
        for bar in bars:
            assert bar.high >= bar.open, f"High {bar.high} < Open {bar.open}"
            assert bar.high >= bar.close, f"High {bar.high} < Close {bar.close}"
            assert bar.low <= bar.open, f"Low {bar.low} > Open {bar.open}"
            assert bar.low <= bar.close, f"Low {bar.low} > Close {bar.close}"
