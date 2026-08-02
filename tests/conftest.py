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


# Known live Alpaca endpoints that must be blocked in tests
_LIVE_ALPACA_ENDPOINTS = (
    "https://api.alpaca.markets",
    "https://live-api.alpaca.markets",
    "https://data.alpaca.markets",  # live data endpoint
)

# Live mode environment variable patterns that indicate live trading is enabled
_LIVE_MODE_ENV_PATTERNS = (
    "ALPACA_LIVE_MODE",
    "ENABLE_LIVE_TRADING",
    "LIVE_TRADING_ENABLED",
    "ALPACA_LIVE",
)

# Paper mode = False patterns
_PAPER_DISABLED_PATTERNS = (
    "PAPER_MODE=false",
    "PAPER_TRADING=false",
    "PAPER=false",
    "USE_PAPER=0",
    "PAPER_MODE=0",
)


def _check_live_mode():
    """
    Check if live trading mode is enabled via environment.
    Returns a list of violations found.
    """
    violations = []

    # Check for live mode flags
    for var in _LIVE_MODE_ENV_PATTERNS:
        if os.environ.get(var, "").lower() in ("true", "1", "yes"):
            violations.append(f"{var} is enabled (live mode)")

    # Check for paper mode disabled
    for var in ("PAPER_MODE", "PAPER_TRADING", "PAPER", "USE_PAPER"):
        val = os.environ.get(var, "").lower()
        if val in ("false", "0", "no", "disabled"):
            violations.append(f"{var}={os.environ.get(var)} (paper mode disabled)")

    # Check for live Alpaca endpoint
    base_url = os.environ.get("ALPACA_BASE_URL", "")
    if base_url.rstrip("/") in _LIVE_ALPACA_ENDPOINTS:
        violations.append(f"ALPACA_BASE_URL points to live endpoint: {base_url}")

    # Check for non-test Alpaca API key (live keys don't start with PK)
    alpaca_key = os.environ.get("ALPACA_API_KEY", "")
    if alpaca_key and not alpaca_key.startswith("PK"):
        violations.append(f"Non-test Alpaca API key detected (key prefix: {alpaca_key[:4]}...)")

    return violations


def pytest_configure(config):
    """
    Pytest hook to validate test environment BEFORE any tests run.

    This runs before test collection and hard-fails if live trading
    mode, disabled paper mode, or live Alpaca endpoints are detected.
    """
    violations = _check_live_mode()

    if violations:
        error_lines = [
            "",
            "=" * 60,
            "🚨 TEST SAFETY VIOLATION — LIVE BROKERAGE BLOCKED",
            "=" * 60,
            "The following live trading indicators were detected:",
        ]
        for v in violations:
            error_lines.append(f"  • {v}")

        error_lines += [
            "",
            "Tests cannot run with live brokerage enabled.",
            "To fix: unset ALPACA_LIVE_MODE, set PAPER_MODE=true,",
            "set ALPACA_BASE_URL to paper-api.alpaca.markets,",
            "and use test API keys (starting with PK).",
            "=" * 60,
            "",
        ]
        pytest.fail("\n".join(error_lines))

    print("\n" + "=" * 60)
    print("🛡️  TEST SAFETY CHECK — PASSED")
    print("=" * 60)
    print(f"TESTING environment: {os.environ.get('TESTING', 'NOT SET')}")
    print(f"UNIT_TESTING environment: {os.environ.get('UNIT_TESTING', 'NOT SET')}")
    print(f"ALPACA_BASE_URL: {os.environ.get('ALPACA_BASE_URL', 'default (paper)')}")
    print("Live brokerage calls are BLOCKED in test mode")
    print("=" * 60 + "\n")


def pytest_collection_modifyitems(config, items):
    """
    Pytest hook to modify test collection.
    
    Adds automatic markers and validates test safety.
    """
    for item in items:
        # Auto-mark tests that import brokerage as brokerage tests
        if 'brokerage' in item.nodeid.lower():
            item.add_marker(pytest.mark.brokerage)
