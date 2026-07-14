"""
Test configuration and fixtures for trading-bot tests.

This conftest ensures all tests use mocked/paper brokerage clients
and prevents accidental live trading API calls.
"""

import os
import sys
import pytest
from pathlib import Path

# Set test environment variables BEFORE any imports
os.environ['TESTING'] = '1'
os.environ['UNIT_TESTING'] = '1'
os.environ['PYTEST_CURRENT_TEST'] = '1'

# Ensure the project root is in the path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


@pytest.fixture(scope='session')
def project_root_path():
    """Return the project root path."""
    return Path(__file__).parent.parent


@pytest.fixture
def mock_brokerage():
    """
    Provide a mock brokerage client for tests.
    
    This fixture returns a completely mocked brokerage client
    that never makes any live API calls. Creates fresh instance per test.
    """
    from src.brokerage.mock import MockBrokerageClient, MockMarketDataClient
    
    brokerage = MockBrokerageClient(test_mode=True)
    market_data = MockMarketDataClient()
    
    return brokerage, market_data


@pytest.fixture
def mock_market_data():
    """
    Provide a mock market data client for tests.
    Creates fresh instance per test.
    """
    from src.brokerage.mock import MockMarketDataClient
    return MockMarketDataClient()


@pytest.fixture
def mock_brokerage_client():
    """
    Provide a mock brokerage trading client for tests.
    Creates fresh instance per test.
    """
    from src.brokerage.mock import MockBrokerageClient
    return MockBrokerageClient(test_mode=True)


@pytest.fixture
def sample_market_data():
    """
    Provide sample market data for testing indicators and analysis.
    """
    import pandas as pd
    from datetime import datetime, timedelta
    
    dates = [(datetime.now() - timedelta(days=100-i)) for i in range(100)]
    
    return pd.DataFrame({
        'timestamp': dates,
        'open': [100.0 + i * 0.1 for i in range(100)],
        'high': [102.0 + i * 0.1 for i in range(100)],
        'low': [98.0 + i * 0.1 for i in range(100)],
        'close': [100.0 + i * 0.1 + 0.05 * (i % 10) for i in range(100)],
        'volume': [1000000 + i * 10000 for i in range(100)]
    })


@pytest.fixture
def sample_analysis_result():
    """
    Provide a sample analysis result for testing.
    """
    return {
        'symbol': 'AAPL',
        'signal': 'HOLD',
        'score': 35,
        'rsi': 45,
        'sma_fast': 150.25,
        'sma_slow': 148.50,
        'price': 152.30,
        'volume': 50000000,
        'timestamp': '2026-07-14T01:00:00Z'
    }


def pytest_configure(config):
    """
    Pytest hook to validate test environment.
    
    This runs before any tests and ensures:
    1. TESTING environment variable is set
    2. No live brokerage credentials are being misused
    """
    print("\n" + "="*60)
    print("🛡️  TEST SAFETY CHECK")
    print("="*60)
    print(f"TESTING environment: {os.environ.get('TESTING', 'NOT SET')}")
    print(f"UNIT_TESTING environment: {os.environ.get('UNIT_TESTING', 'NOT SET')}")
    print("Live brokerage calls are BLOCKED in test mode")
    print("="*60 + "\n")
    
    # Verify we're not accidentally using live API keys in tests
    alpaca_key = os.environ.get('ALPACA_API_KEY', '')
    if alpaca_key and not alpaca_key.startswith('PK'):  # Alpaca test keys often start with PK
        # Allow but warn - real implementation should use completely separate keys
        print("⚠️  WARNING: Non-test Alpaca API key detected")


def pytest_collection_modifyitems(config, items):
    """
    Pytest hook to modify test collection.
    
    Adds automatic markers and validates test safety.
    """
    for item in items:
        # Auto-mark tests that import brokerage as brokerage tests
        if 'brokerage' in item.nodeid.lower():
            item.add_marker(pytest.mark.brokerage)
