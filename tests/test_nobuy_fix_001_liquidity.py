"""
NOBUY-FIX-001 FIX 1 — liquidity spread proxy regression tests.

The old check used the daily high/low range as a spread proxy and rejected
anything over 0.9%. A liquid large cap (NKE, 23M avg volume, 4.3% high-low
day) was rejected as "Spread 4.3% too wide". The fixed check uses the real
bid/ask spread when a quote is available and never rejects normal daily
high-low movement when it is not.
"""

import logging
import types
from unittest.mock import MagicMock

import pandas as pd

from src.core.smart_bot import SmartTradingBot


def _bars(volume: float, high: float, low: float, close: float = 100.0, n: int = 25):
    rows = [
        {
            "timestamp": pd.Timestamp("2026-11-20", tz="UTC"),
            "open": close,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
        for _ in range(n)
    ]
    return pd.DataFrame(rows)


def _make_bot(df, *, market_open=False, quote=None):
    bot = SmartTradingBot.__new__(SmartTradingBot)
    bot.min_daily_volume = 1_000_000
    bot.max_spread_pct = 0.3
    bot.get_market_data = MagicMock(return_value=df)
    bot.data_client = None
    if quote is not None:
        bid, ask = quote
        response = types.SimpleNamespace(
            data={
                "NKE": types.SimpleNamespace(
                    bid_price=bid, ask_price=ask,
                )
            }
        )
        bot.data_client = MagicMock()
        bot.data_client.get_stock_latest_quote.return_value = response
    return bot


def _patch_market_open(monkeypatch, is_open):
    from src.core import smart_bot as sb
    monkeypatch.setattr(
        sb.market_calendar, "is_market_open", lambda *a, **k: is_open
    )


class TestNoQuotePath:
    def test_liquid_stock_with_43pct_day_passes_without_quote(self, monkeypatch):
        _patch_market_open(monkeypatch, True)
        df = _bars(volume=23_000_000, high=104.0, low=99.7)  # 4.3% range
        bot = _make_bot(df)
        passes, avg_vol, _spread, reason = bot.check_liquidity("NKE", 1_000_000)
        assert passes is True, reason
        assert avg_vol == 23_000_000
        assert "too wide" not in reason

    def test_liquid_stock_passes_off_hours_without_quote(self, monkeypatch):
        _patch_market_open(monkeypatch, False)
        df = _bars(volume=23_000_000, high=108.0, low=103.0)  # 5% range
        bot = _make_bot(df)
        passes, _v, _s, reason = bot.check_liquidity("NKE", 1_000_000)
        assert passes is True, reason

    def test_extreme_25pct_range_fails_without_quote(self, monkeypatch):
        _patch_market_open(monkeypatch, False)
        df = _bars(volume=23_000_000, high=110.0, low=85.0)  # 25% range
        bot = _make_bot(df)
        passes, _v, spread, reason = bot.check_liquidity("NKE", 1_000_000)
        assert passes is False
        assert spread > 20
        assert reason.startswith("Intraday range")
        assert "Spread" not in reason

    def test_low_volume_still_fails_on_volume(self, monkeypatch):
        _patch_market_open(monkeypatch, True)
        df = _bars(volume=10_000, high=101.0, low=99.0)  # thin penny-ish
        bot = _make_bot(df)
        passes, _v, _s, reason = bot.check_liquidity("AMACW", 1_000_000)
        assert passes is False
        assert "Volume" in reason


class TestRealQuotePath:
    def test_wide_real_bid_ask_fails(self, monkeypatch):
        _patch_market_open(monkeypatch, True)
        df = _bars(volume=23_000_000, high=101.0, low=100.0)  # 1% daily range
        bot = _make_bot(df, quote=(99.0, 100.0))  # 1.0% bid/ask
        passes, _v, spread, reason = bot.check_liquidity("NKE", 1_000_000)
        assert passes is False
        assert spread is not None and spread > 0.3
        assert reason.startswith("Spread")

    def test_tight_real_bid_ask_passes(self, monkeypatch):
        _patch_market_open(monkeypatch, True)
        df = _bars(volume=23_000_000, high=101.0, low=100.0)
        bot = _make_bot(df, quote=(99.99, 100.01))  # 0.02%
        passes, _v, spread, _reason = bot.check_liquidity("NKE", 1_000_000)
        assert passes is True
        assert spread < 0.3

    def test_no_quote_available_off_hours_ignores_daily_range(self, monkeypatch):
        _patch_market_open(monkeypatch, False)
        df = _bars(volume=23_000_000, high=104.0, low=99.7)
        # A quote is configured but the market is closed: not used.
        bot = _make_bot(df, quote=(90.0, 100.0))
        passes, _v, _s, reason = bot.check_liquidity("NKE", 1_000_000)
        assert passes is True, reason
        bot.data_client.get_stock_latest_quote.assert_not_called()

    def test_missing_quote_object_falls_back_to_non_rejecting(self, monkeypatch):
        _patch_market_open(monkeypatch, True)
        df = _bars(volume=23_000_000, high=104.0, low=99.7)
        bot = _make_bot(df)
        bot.data_client = MagicMock()
        bot.data_client.get_stock_latest_quote.return_value = types.SimpleNamespace(data={})
        passes, _v, _s, reason = bot.check_liquidity("NKE", 1_000_000)
        assert passes is True, reason
