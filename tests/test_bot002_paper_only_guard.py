"""BOT-002: Paper-only fail-closed guard tests.

Proves that:
  - trading_bot_paper_only_guard() allows when the environment is
    fully paper (TRADING_BOT_PAPER_ONLY=1, ALPACA_BASE_URL paper
    endpoint, ALPACA_API_KEY starts with PK).
  - The guard rejects live endpoints.
  - The guard rejects missing TRADING_BOT_PAPER_ONLY.
  - The guard rejects missing/empty ALPACA_BASE_URL.
  - The guard rejects unrecognized ALPACA_BASE_URL.
  - The guard rejects non-paper API keys (live keys start with AK).
  - The guard never logs secret values (api_key, api_secret).
"""
import os
import logging

import pytest

from src.core.smart_bot import trading_bot_paper_only_guard


@pytest.fixture
def paper_env(monkeypatch):
    """Standard paper-only environment for happy-path tests."""
    monkeypatch.setenv("TRADING_BOT_PAPER_ONLY", "1")
    monkeypatch.setenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
    monkeypatch.setenv("ALPACA_API_KEY", "PKTESTKEY1234567890ABCDEF")
    monkeypatch.setenv("ALPACA_API_SECRET", "secret")


def test_paper_endpoint_with_guard_is_allowed(paper_env) -> None:
    """Happy path: paper endpoint + TRADING_BOT_PAPER_ONLY=1 + PK key
    passes the guard without raising."""
    trading_bot_paper_only_guard()  # must not raise


def test_paper_endpoint_with_v2_path_is_allowed(paper_env, monkeypatch) -> None:
    """The /v2 suffix variant of the paper endpoint is also allowed."""
    monkeypatch.setenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets/v2")
    trading_bot_paper_only_guard()  # must not raise


def test_paper_endpoint_with_trailing_slash_is_allowed(paper_env, monkeypatch) -> None:
    monkeypatch.setenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets/")
    trading_bot_paper_only_guard()  # must not raise


def test_live_endpoint_api_alpaca_markets_is_rejected(monkeypatch) -> None:
    """The classic live endpoint (api.alpaca.markets) MUST be rejected."""
    monkeypatch.setenv("TRADING_BOT_PAPER_ONLY", "1")
    monkeypatch.setenv("ALPACA_BASE_URL", "https://api.alpaca.markets")
    monkeypatch.setenv("ALPACA_API_KEY", "AKLIVEKEY1234567890ABCDEF")

    with pytest.raises(RuntimeError, match="live Alpaca endpoint"):
        trading_bot_paper_only_guard()


def test_live_endpoint_with_v2_path_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("TRADING_BOT_PAPER_ONLY", "1")
    monkeypatch.setenv("ALPACA_BASE_URL", "https://api.alpaca.markets/v2")
    monkeypatch.setenv("ALPACA_API_KEY", "AKLIVEKEY1234567890ABCDEF")

    with pytest.raises(RuntimeError, match="live Alpaca endpoint"):
        trading_bot_paper_only_guard()


def test_missing_paper_only_guard_is_rejected(monkeypatch) -> None:
    """TRADING_BOT_PAPER_ONLY not set (or not '1') MUST be rejected."""
    monkeypatch.delenv("TRADING_BOT_PAPER_ONLY", raising=False)
    monkeypatch.setenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
    monkeypatch.setenv("ALPACA_API_KEY", "PKTESTKEY1234567890ABCDEF")

    with pytest.raises(RuntimeError, match="TRADING_BOT_PAPER_ONLY"):
        trading_bot_paper_only_guard()


def test_paper_only_set_to_zero_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("TRADING_BOT_PAPER_ONLY", "0")
    monkeypatch.setenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
    monkeypatch.setenv("ALPACA_API_KEY", "PKTESTKEY1234567890ABCDEF")

    with pytest.raises(RuntimeError, match="TRADING_BOT_PAPER_ONLY"):
        trading_bot_paper_only_guard()


def test_paper_only_set_to_true_string_is_rejected(monkeypatch) -> None:
    """Only the literal '1' is accepted. 'true' / 'yes' / 'on' are NOT
    accepted; this is a fail-closed guard and accepts only the exact
    runner-protocol value."""
    monkeypatch.setenv("TRADING_BOT_PAPER_ONLY", "true")
    monkeypatch.setenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
    monkeypatch.setenv("ALPACA_API_KEY", "PKTESTKEY1234567890ABCDEF")

    with pytest.raises(RuntimeError, match="TRADING_BOT_PAPER_ONLY"):
        trading_bot_paper_only_guard()


def test_missing_base_url_is_rejected(monkeypatch) -> None:
    """An absent ALPACA_BASE_URL MUST be rejected — the guard must not
    fall back to a default, because the default itself is ambiguous
    in this context."""
    monkeypatch.setenv("TRADING_BOT_PAPER_ONLY", "1")
    monkeypatch.delenv("ALPACA_BASE_URL", raising=False)
    monkeypatch.setenv("ALPACA_API_KEY", "PKTESTKEY1234567890ABCDEF")

    with pytest.raises(RuntimeError, match="ALPACA_BASE_URL is not set"):
        trading_bot_paper_only_guard()


def test_empty_base_url_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("TRADING_BOT_PAPER_ONLY", "1")
    monkeypatch.setenv("ALPACA_BASE_URL", "")
    monkeypatch.setenv("ALPACA_API_KEY", "PKTESTKEY1234567890ABCDEF")

    with pytest.raises(RuntimeError, match="ALPACA_BASE_URL is not set"):
        trading_bot_paper_only_guard()


def test_malformed_base_url_is_rejected(monkeypatch) -> None:
    """An unrecognised host MUST be rejected — typos like
    'paper-api.alpca.markets' (missing letter) are not paper."""
    monkeypatch.setenv("TRADING_BOT_PAPER_ONLY", "1")
    monkeypatch.setenv("ALPACA_BASE_URL", "https://paper-api.alpca.markets")
    monkeypatch.setenv("ALPACA_API_KEY", "PKTESTKEY1234567890ABCDEF")

    with pytest.raises(RuntimeError, match="does not match"):
        trading_bot_paper_only_guard()


def test_localhost_base_url_is_rejected(monkeypatch) -> None:
    """A localhost URL is unrecognised and MUST be rejected."""
    monkeypatch.setenv("TRADING_BOT_PAPER_ONLY", "1")
    monkeypatch.setenv("ALPACA_BASE_URL", "http://localhost:8080/alpaca")
    monkeypatch.setenv("ALPACA_API_KEY", "PKTESTKEY1234567890ABCDEF")

    with pytest.raises(RuntimeError, match="does not match"):
        trading_bot_paper_only_guard()


def test_live_key_prefix_is_rejected(monkeypatch) -> None:
    """Alpaca paper keys start with 'PK'; live keys start with 'AK'.
    A live-prefixed key MUST be rejected even if the URL is paper
    (defense-in-depth: a paper URL with a live key could mean
    misconfigured credentials)."""
    monkeypatch.setenv("TRADING_BOT_PAPER_ONLY", "1")
    monkeypatch.setenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
    monkeypatch.setenv("ALPACA_API_KEY", "AKLIVEKEY1234567890ABCDEF")

    with pytest.raises(RuntimeError, match="paper-key prefix"):
        trading_bot_paper_only_guard()


def test_missing_api_key_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("TRADING_BOT_PAPER_ONLY", "1")
    monkeypatch.setenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="ALPACA_API_KEY is not set"):
        trading_bot_paper_only_guard()


def test_guard_does_not_log_secret_values(paper_env, monkeypatch, caplog) -> None:
    """The guard must raise clean error messages; it must NOT echo
    the secret API key or API secret in any log line."""
    caplog.set_level(logging.DEBUG)
    secret_value = "PKTESTKEY1234567890ABCDEF"
    monkeypatch.setenv("ALPACA_API_KEY", secret_value)
    monkeypatch.setenv("ALPACA_API_SECRET", "this-is-the-secret-do-not-leak")

    # Happy path; no exception, no logs containing the secret.
    trading_bot_paper_only_guard()
    for record in caplog.records:
        assert secret_value not in record.getMessage()
        assert "this-is-the-secret-do-not-leak" not in record.getMessage()


def test_guard_error_messages_do_not_leak_secret(paper_env, monkeypatch) -> None:
    """When the guard raises, the error message must contain the
    failing condition name but NOT the secret API key value."""
    monkeypatch.setenv("TRADING_BOT_PAPER_ONLY", "1")
    monkeypatch.setenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
    monkeypatch.setenv("ALPACA_API_KEY", "AKLIVEKEY1234567890ABCDEF")

    with pytest.raises(RuntimeError) as exc_info:
        trading_bot_paper_only_guard()

    msg = str(exc_info.value)
    assert "AKLIVEKEY1234567890ABCDEF" not in msg
    assert "paper-key prefix" in msg  # condition name is exposed


def test_guard_runs_before_trading_client_construction(paper_env, monkeypatch) -> None:
    """End-to-end: SmartTradingBot() construction with the full
    paper-only environment succeeds; with TRADING_BOT_PAPER_ONLY
    removed, the guard fails BEFORE TradingClient construction
    (no API call attempted)."""
    from src.core.smart_bot import SmartTradingBot

    # Happy path: paper env -> bot construction succeeds.
    bot = SmartTradingBot()
    assert bot.trading_client is not None

    # Failure path: clear TRADING_BOT_PAPER_ONLY and confirm guard
    # fires before any TradingClient retry.
    monkeypatch.delenv("TRADING_BOT_PAPER_ONLY", raising=False)
    with pytest.raises(RuntimeError, match="TRADING_BOT_PAPER_ONLY"):
        SmartTradingBot()


def test_finalize_active_session_is_noop_when_no_session_id(paper_env) -> None:
    """_finalize_active_session_on_shutdown is a no-op when no
    session_id is set (e.g. the bot never reached start_session).
    Cannot raise even if the DB layer is unavailable."""
    from src.core.smart_bot import SmartTradingBot

    bot = SmartTradingBot()
    assert bot.session_id is None
    # Must not raise; nothing to do.
    bot._finalize_active_session_on_shutdown(reason="test")


def test_main_py_installs_sigterm_handler() -> None:
    """main.py must install SIGTERM and SIGINT handlers that translate
    the signal into a graceful shutdown. Without this, systemd's
    SIGTERM would kill the bot mid-time.sleep() and the session row
    would be left ACTIVE in the DB.

    This test verifies that main.py's top-level signal.signal(...) calls
    run without raising. We import main as a module (after a fresh
    subprocess to avoid the single-instance lock collision)."""
    import subprocess
    import sys
    import tempfile
    import os

    # Use a subprocess so we don't collide with the test process's
    # signal handlers or acquire the real /tmp/trading_bot.lock.
    code = (
        "import sys, signal;"
        "sys.path.insert(0, '.');"
        "import main;"
        "assert signal.getsignal(signal.SIGTERM) is not signal.SIG_DFL, 'SIGTERM handler not installed';"
        "assert signal.getsignal(signal.SIGINT) is not signal.SIG_DFL, 'SIGINT handler not installed';"
        "print('OK')"
    )
    env = os.environ.copy()
    env["TRADING_BOT_PAPER_ONLY"] = "1"
    env["ALPACA_BASE_URL"] = "https://paper-api.alpaca.markets"
    env["ALPACA_API_KEY"] = "PKTESTKEY1234567890ABCDEF"
    env["ALPACA_API_SECRET"] = "secret"
    env["LOCK_FILE_OVERRIDE"] = tempfile.mktemp(prefix="trading_bot_test_")
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd="/root/.openclaw/workspace/trading-bot",
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert "OK" in result.stdout, (
        f"main.py did not install signal handlers; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
