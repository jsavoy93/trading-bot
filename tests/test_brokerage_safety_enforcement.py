"""
TEST-001 Part 2: Prove pytest aborts for live trading indicators.

These tests verify that pytest exits with an error when:
1. Live mode is enabled
2. Paper mode is disabled
3. A live Alpaca endpoint is configured

These are subprocess tests — they spawn pytest with specific env vars
and verify it aborts before running any tests.
"""

import subprocess
import sys
import os
import pytest

# Path to the project root (parent of tests/)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run_pytest_with_env(env_overrides, extra_args=None):
    """
    Run pytest in a subprocess with given environment overrides.
    Returns (returncode, stdout, stderr).
    """
    env = os.environ.copy()
    env.update(env_overrides)
    # Always set TESTING so pytest framework is active
    env.setdefault("TESTING", "1")
    env.setdefault("UNIT_TESTING", "1")
    env.setdefault("PYTEST_CURRENT_TEST", "1")

    args = [sys.executable, "-m", "pytest", "--tb=short"]
    if extra_args:
        args.extend(extra_args)

    result = subprocess.run(
        args,
        env=env,
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    )
    return result.returncode, result.stdout, result.stderr


class TestPytestAbortsOnLiveMode:
    """Verify pytest aborts when live trading mode is detected."""

    def test_live_mode_env_var_triggers_abort(self):
        """pytest must abort when ALPACA_LIVE_MODE=true."""
        rc, stdout, stderr = _run_pytest_with_env({
            "ALPACA_LIVE_MODE": "true",
        }, extra_args=["-v", "tests/test_brokerage_mock.py::TestMockBrokerageClient::test_get_account"])

        combined = stdout + stderr
        assert rc != 0, f"Expected non-zero exit, got {rc}. Output: {combined[:500]}"
        assert "TEST SAFETY VIOLATION" in combined or "live" in combined.lower(), \
            f"Safety violation not reported. Output: {combined[:500]}"

    def test_enable_live_trading_env_var_triggers_abort(self):
        """pytest must abort when ENABLE_LIVE_TRADING=true."""
        rc, stdout, stderr = _run_pytest_with_env({
            "ENABLE_LIVE_TRADING": "true",
        }, extra_args=["-v", "tests/test_brokerage_mock.py::TestMockBrokerageClient::test_get_account"])

        combined = stdout + stderr
        assert rc != 0, f"Expected non-zero exit, got {rc}. Output: {combined[:500]}"
        assert "TEST SAFETY VIOLATION" in combined or "live" in combined.lower(), \
            f"Safety violation not reported. Output: {combined[:500]}"

    def test_live_trading_enabled_env_var_triggers_abort(self):
        """pytest must abort when LIVE_TRADING_ENABLED=yes."""
        rc, stdout, stderr = _run_pytest_with_env({
            "LIVE_TRADING_ENABLED": "yes",
        }, extra_args=["-v", "tests/test_brokerage_mock.py::TestMockBrokerageClient::test_get_account"])

        combined = stdout + stderr
        assert rc != 0, f"Expected non-zero exit, got {rc}. Output: {combined[:500]}"
        assert "TEST SAFETY VIOLATION" in combined or "live" in combined.lower(), \
            f"Safety violation not reported. Output: {combined[:500]}"


class TestPytestAbortsOnPaperModeDisabled:
    """Verify pytest aborts when paper trading is explicitly disabled."""

    def test_paper_mode_false_triggers_abort(self):
        """pytest must abort when PAPER_MODE=false."""
        rc, stdout, stderr = _run_pytest_with_env({
            "PAPER_MODE": "false",
        }, extra_args=["-v", "tests/test_brokerage_mock.py::TestMockBrokerageClient::test_get_account"])

        combined = stdout + stderr
        assert rc != 0, f"Expected non-zero exit, got {rc}. Output: {combined[:500]}"
        assert "TEST SAFETY VIOLATION" in combined or "paper" in combined.lower(), \
            f"Safety violation not reported. Output: {combined[:500]}"

    def test_paper_trading_false_triggers_abort(self):
        """pytest must abort when PAPER_TRADING=false."""
        rc, stdout, stderr = _run_pytest_with_env({
            "PAPER_TRADING": "false",
        }, extra_args=["-v", "tests/test_brokerage_mock.py::TestMockBrokerageClient::test_get_account"])

        combined = stdout + stderr
        assert rc != 0, f"Expected non-zero exit, got {rc}. Output: {combined[:500]}"
        assert "TEST SAFETY VIOLATION" in combined or "paper" in combined.lower(), \
            f"Safety violation not reported. Output: {combined[:500]}"

    def test_paper_disabled_zero_triggers_abort(self):
        """pytest must abort when PAPER=0."""
        rc, stdout, stderr = _run_pytest_with_env({
            "PAPER": "0",
        }, extra_args=["-v", "tests/test_brokerage_mock.py::TestMockBrokerageClient::test_get_account"])

        combined = stdout + stderr
        assert rc != 0, f"Expected non-zero exit, got {rc}. Output: {combined[:500]}"
        assert "TEST SAFETY VIOLATION" in combined or "paper" in combined.lower(), \
            f"Safety violation not reported. Output: {combined[:500]}"

    def test_use_paper_disabled_triggers_abort(self):
        """pytest must abort when USE_PAPER=0."""
        rc, stdout, stderr = _run_pytest_with_env({
            "USE_PAPER": "0",
        }, extra_args=["-v", "tests/test_brokerage_mock.py::TestMockBrokerageClient::test_get_account"])

        combined = stdout + stderr
        assert rc != 0, f"Expected non-zero exit, got {rc}. Output: {combined[:500]}"
        assert "TEST SAFETY VIOLATION" in combined or "paper" in combined.lower(), \
            f"Safety violation not reported. Output: {combined[:500]}"


class TestPytestAbortsOnLiveEndpoint:
    """Verify pytest aborts when a live Alpaca endpoint is configured."""

    def test_live_alpaca_endpoint_triggers_abort(self):
        """pytest must abort when ALPACA_BASE_URL points to live API."""
        rc, stdout, stderr = _run_pytest_with_env({
            "ALPACA_BASE_URL": "https://api.alpaca.markets",
        }, extra_args=["-v", "tests/test_brokerage_mock.py::TestMockBrokerageClient::test_get_account"])

        combined = stdout + stderr
        assert rc != 0, f"Expected non-zero exit, got {rc}. Output: {combined[:500]}"
        assert "TEST SAFETY VIOLATION" in combined or "live" in combined.lower(), \
            f"Safety violation not reported. Output: {combined[:500]}"

    def test_live_data_endpoint_triggers_abort(self):
        """pytest must abort when ALPACA_BASE_URL points to live data endpoint."""
        rc, stdout, stderr = _run_pytest_with_env({
            "ALPACA_BASE_URL": "https://data.alpaca.markets",
        }, extra_args=["-v", "tests/test_brokerage_mock.py::TestMockBrokerageClient::test_get_account"])

        combined = stdout + stderr
        assert rc != 0, f"Expected non-zero exit, got {rc}. Output: {combined[:500]}"
        assert "TEST SAFETY VIOLATION" in combined or "live" in combined.lower(), \
            f"Safety violation not reported. Output: {combined[:500]}"


class TestPytestAbortsOnLiveApiKey:
    """Verify pytest aborts when a non-test Alpaca API key is detected."""

    def test_non_pk_alpaca_key_triggers_abort(self):
        """pytest must abort when Alpaca API key doesn't start with PK."""
        rc, stdout, stderr = _run_pytest_with_env({
            "ALPACA_API_KEY": "LIVEKEY1234567890abcdef",
            "ALPACA_API_SECRET": "livesecret1234567890abcdefghijklmnop",
        }, extra_args=["-v", "tests/test_brokerage_mock.py::TestMockBrokerageClient::test_get_account"])

        combined = stdout + stderr
        assert rc != 0, f"Expected non-zero exit, got {rc}. Output: {combined[:500]}"
        assert "TEST SAFETY VIOLATION" in combined or "API key" in combined, \
            f"Safety violation not reported. Output: {combined[:500]}"


class TestPytestPassesWithSafeConfig:
    """Verify pytest passes with safe (paper/test) configuration."""

    def test_paper_endpoint_is_safe(self):
        """pytest must pass when ALPACA_BASE_URL=paper-api.alpaca.markets."""
        rc, stdout, stderr = _run_pytest_with_env({
            "ALPACA_BASE_URL": "https://paper-api.alpaca.markets",
        }, extra_args=["-v", "tests/test_brokerage_mock.py::TestMockBrokerageClient::test_get_account"])

        combined = stdout + stderr
        assert rc == 0, f"Expected zero exit, got {rc}. Output: {combined[:500]}"

    def test_test_key_prefix_pk_is_safe(self):
        """pytest must pass when ALPACA_API_KEY starts with PK."""
        rc, stdout, stderr = _run_pytest_with_env({
            "ALPACA_API_KEY": "PKTEST1234567890abcdefghijklmnop",
            "ALPACA_API_SECRET": "testsecret1234567890abcdefghijklmnop",
        }, extra_args=["-v", "tests/test_brokerage_mock.py::TestMockBrokerageClient::test_get_account"])

        combined = stdout + stderr
        assert rc == 0, f"Expected zero exit, got {rc}. Output: {combined[:500]}"

    def test_no_alpaca_key_is_safe(self):
        """pytest must pass when no ALPACA_API_KEY is set (test env)."""
        env = os.environ.copy()
        env.update({
            "TESTING": "1",
            "UNIT_TESTING": "1",
            "PYTEST_CURRENT_TEST": "1",
        })
        # Remove any Alpaca key from env
        env.pop("ALPACA_API_KEY", None)
        env.pop("ALPACA_API_SECRET", None)

        args = [sys.executable, "-m", "pytest", "--tb=short", "-v",
                "tests/test_brokerage_mock.py::TestMockBrokerageClient::test_get_account"]

        result = subprocess.run(args, env=env, capture_output=True, text=True, cwd=PROJECT_ROOT)
        combined = result.stdout + result.stderr
        assert result.returncode == 0, f"Expected zero exit, got {result.returncode}. Output: {combined[:500]}"
