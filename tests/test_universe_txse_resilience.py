"""
Regression tests for the TXSE universe outage (2026-10-19 → present).

Root cause: alpaca-py 0.43.4 (and 0.43.5 / 0.44.0) type
``Asset.exchange`` as the ``AssetExchange`` enum, which has no ``TXSE``
member. Alpaca began returning ``exchange="TXSE"`` on 2026-10-19, so the
strict ``List[Asset]`` validation of the WHOLE response raised
``pydantic.ValidationError``. ``get_all_us_symbols()`` swallowed it in a
catch-all and returned ``[]``, so the rolling universe drained to zero
symbols by 2026-10-20 19:11 UTC.

These tests are deterministic and fully mocked: no network, no credentials,
no orders, no database. ``tests/conftest.py`` already sets TESTING=1 /
UNIT_TESTING=1 and the brokerage live-mode guards.
"""

import logging
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from src.core.smart_bot import SmartTradingBot
from src.brokerage.mock import MockAsset


def _raw_asset(symbol, exchange="NASDAQ", status="active", asset_class="us_equity",
               tradable=True, **extra):
    """Build one raw (pre-SDK-validation) Alpaca asset record."""
    symbol_hash_abs = abs(hash(symbol)) % (10 ** 12)
    asset_uuid = f"00000000-0000-0000-0000-{symbol_hash_abs:012d}"
    record = {
        "id": asset_uuid,
        "symbol": symbol,
        "exchange": exchange,
        "class": asset_class,
        "status": status,
        "tradable": tradable,
        "marginable": tradable,
        "shortable": tradable,
        "easy_to_borrow": tradable,
        "fractionable": True,
        "attributes": [],
    }
    record.update(extra)
    return record


VALID_RAW = [
    _raw_asset("AAPL"),
    _raw_asset("MSFT"),
    _raw_asset("SPY", exchange="ARCA"),
]
TXSE_RAW = [_raw_asset("TEXS", exchange="TXSE"), _raw_asset("TEXU", exchange="TXSE")]


class _StrictClient:
    """Client that reproduces the pre-fix failure mode.

    ``get_all_assets`` performs the same strict ``List[Asset]`` validation the
    real SDK performs. ``get`` returns the raw dicts, exactly as alpaca-py's
    ``TradingClient.get`` does before ``TypeAdapter(List[Asset])`` runs.
    """

    def __init__(self, raw, request_error=None):
        self._raw = raw
        self._request_error = request_error
        self.raw_calls = 0

    def get_all_assets(self):
        from alpaca.trading.models import Asset
        from pydantic import TypeAdapter
        if self._request_error is not None:
            raise self._request_error
        return TypeAdapter(list[Asset]).validate_python(self._raw)

    def get(self, path, params=None):
        self.raw_calls += 1
        if self._request_error is not None:
            raise self._request_error
        return self._raw


def _bot_with(client):
    """Build a SmartTradingBot shell without brokerage/network construction."""
    bot = SmartTradingBot.__new__(SmartTradingBot)
    bot.trading_client = client
    return bot


# ---------------------------------------------------------------------------
# (a) Reproduce the original failure mode
# ---------------------------------------------------------------------------

def test_pre_fix_behaviour_empty_universe_when_one_txse_asset_present(caplog):
    """A single TXSE record used to empty the entire universe (pre-fix path)."""
    from pydantic import TypeAdapter
    from alpaca.trading.models import Asset

    client = _StrictClient(VALID_RAW + TXSE_RAW)
    bot = _bot_with(client)

    with caplog.at_level(logging.ERROR):
        # Reproduce the ORIGINAL implementation: call the typed client and let
        # the catch-all turn the validation failure into an empty universe.
        try:
            TypeAdapter(list[Asset]).validate_python(client.get_all_assets())
            original_result = None
        except ValidationError as exc:
            original_result = []
            assert "TXSE" in str(exc)
    # The old code returned [] here — the outage behaviour we are fixing.
    assert original_result == []

    # And the whole-universe failure also empties the universe via a naive client
    # that mirrors the old get_all_us_symbols body:
    def legacy_get_all_us_symbols(self):
        try:
            assets = self.trading_client.get_all_assets()
            return [a.symbol for a in assets]
        except Exception:
            return []

    assert legacy_get_all_us_symbols(bot) == []

    # The fixed implementation must NOT reproduce that outage.
    assert bot.get_all_us_symbols() != []


# ---------------------------------------------------------------------------
# (b) + (c) One unexpected exchange cannot disable the universe; valid symbols survive
# ---------------------------------------------------------------------------

def test_single_unknown_exchange_cannot_empty_universe(caplog):
    """Two TXSE records among three valid ones: valid universe survives."""
    bot = _bot_with(_StrictClient(VALID_RAW + TXSE_RAW))
    with caplog.at_level(logging.INFO):
        symbols = bot.get_all_us_symbols()
    # TXSE assets are skipped (fail-closed), the valid universe is intact.
    assert sorted(symbols) == ["AAPL", "MSFT", "SPY"]
    assert len(symbols) == 3


def test_unknown_exchange_assets_are_skipped_and_reported(caplog):
    bot = _bot_with(_StrictClient(VALID_RAW + TXSE_RAW))
    with caplog.at_level(logging.WARNING):
        symbols = bot.get_all_us_symbols()
    warning_text = "\n".join(r.getMessage() for r in caplog.records
                             if r.levelno >= logging.WARNING)
    assert "TEXS" in warning_text, "skipped symbol must appear in the sample"
    assert "TXSE" in warning_text, "reason must name the unknown exchange"
    assert "2" in warning_text, "skipped count must be reported"
    assert "TEXS" not in symbols and "TEXU" not in symbols


def test_valid_symbols_remain_available_with_many_bad_records():
    raw = VALID_RAW + [_raw_asset(f"BAD{i}", exchange="ZZZZ") for i in range(50)]
    bot = _bot_with(_StrictClient(raw))
    assert sorted(bot.get_all_us_symbols()) == ["AAPL", "MSFT", "SPY"]


# ---------------------------------------------------------------------------
# Filters are unchanged (eligibility gates still enforced)
# ---------------------------------------------------------------------------

def test_eligibility_filters_unchanged_inactive_untradable_and_non_equity():
    raw = [
        _raw_asset("ACTIVE_OK"),
        _raw_asset("INACTIVE", status="inactive"),
        _raw_asset("UNTRADABLE", tradable=False),
        _raw_asset("CRYPTOY", asset_class="crypto"),
        _raw_asset("OPT", exchange="OTC"),
    ]
    bot = _bot_with(_StrictClient(raw))
    symbols = bot.get_all_us_symbols()
    assert "ACTIVE_OK" in symbols
    assert "INACTIVE" not in symbols
    assert "UNTRADABLE" not in symbols
    assert "CRYPTOY" not in symbols
    # OTC equities remain in the universe exactly as before the fix.
    assert "OPT" in symbols


# ---------------------------------------------------------------------------
# (d) A genuine request-level failure is still surfaced, not silently empty
# ---------------------------------------------------------------------------

def test_request_level_failure_is_logged_as_error_not_silent(caplog):
    class RequestFailure(Exception):
        pass

    client = _StrictClient(VALID_RAW, request_error=RequestFailure("401 Unauthorized"))
    bot = _bot_with(client)
    with caplog.at_level(logging.ERROR):
        symbols = bot.get_all_us_symbols()
    assert symbols == []
    assert any("Failed to get symbols" in r.getMessage() for r in caplog.records)
    assert any(r.levelno == logging.ERROR for r in caplog.records)


def test_request_level_failure_on_raw_path_also_surfaces(caplog):
    """A request-level failure while refetching raw records must surface."""

    class FlakyClient:
        """Strict call fails on validation, then the raw refetch fails hard."""

        def get_all_assets(self):
            from alpaca.trading.models import Asset
            from pydantic import TypeAdapter
            return TypeAdapter(list[Asset]).validate_python(VALID_RAW + TXSE_RAW)

        def get(self, path, params=None):
            raise OSError("connection reset")

    bot = _bot_with(FlakyClient())
    with caplog.at_level(logging.ERROR):
        symbols = bot.get_all_us_symbols()
    assert symbols == []
    assert any("Failed to get symbols" in r.getMessage() for r in caplog.records)
    assert any(r.levelno == logging.ERROR for r in caplog.records)


# ---------------------------------------------------------------------------
# Mock client compatibility (regression guard for existing callers)
# ---------------------------------------------------------------------------

def test_mock_brokerage_universe_behaviour_unchanged_from_baseline():
    """Mock client path still runs and returns exactly what it did pre-fix.

    NOTE: ``MockAsset`` uses plain strings (``status='ACTIVE'``) while the
    production filter compares against ``'AssetStatus.ACTIVE'``, so the mock
    universe was ALREADY empty before this fix. This test pins that parity so
    the repair does not change mock-client behavior.
    """
    from src.brokerage.mock import MockBrokerageClient
    bot = _bot_with(MockBrokerageClient(test_mode=True))
    assert bot.get_all_us_symbols() == []


def test_mixed_valid_invalid_records_keep_legacy_fallback_path():
    """A record whose attributes cannot be read still falls back to tradable."""
    class OddAsset:
        symbol = "ODD"
        tradable = True

        @property
        def status(self):
            raise AttributeError("status unavailable")

        @property
        def asset_class(self):
            raise AttributeError("class unavailable")

    bot = _bot_with(_StrictClient(VALID_RAW))
    with patch.object(type(bot), "_fetch_assets_resilient",
                      return_value=[OddAsset()]):
        symbols = bot.get_all_us_symbols()
    assert symbols == ["ODD"]