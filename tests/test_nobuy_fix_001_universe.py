"""
NOBUY-FIX-001 FIX 2 — junk penny warrants/units/rights must not enter the
scan queue.

Regression from live paper trading on 2026-11-23: warrants/rights/units
(AMACW $0.17, FDMMW $0.24, PECEW $0.08, DAAQU, CHARR, BRLSW) dominated the
scan queue, scored 100/100 and produced 281 of 282 blocked BUY signals
(all later failing the <1M volume gate).
"""

import pytest
from unittest.mock import MagicMock

from src.core.smart_bot import SmartTradingBot
from src.brokerage.mock import MockAsset


def _asset(symbol, name="", **kw):
    """Asset shaped like the alpaca-py SDK enum string form used by the
    universe filter ('AssetStatus.ACTIVE' / 'AssetClass.US_EQUITY')."""
    kw.setdefault("status", "AssetStatus.ACTIVE")
    kw.setdefault("asset_class", "AssetClass.US_EQUITY")
    return MockAsset(symbol=symbol, name=name or f"{symbol} Common Stock", **kw)


def _bot(assets):
    bot = SmartTradingBot.__new__(SmartTradingBot)
    bot._fetch_assets_resilient = lambda: assets
    bot.universe_min_price = SmartTradingBot.UNIVERSE_MIN_PRICE_DEFAULT
    bot.get_market_data = lambda s: _df(100.0)
    bot.sma_slow = 5  # any analysis fallback path needs this
    # Never touch a real database (module-level DB_PATH resolves inside the
    # worktree; a stray SmartTradingBot shell must not create files there).
    bot.db = MagicMock()
    bot.db.is_available.return_value = False
    return bot


JUNK = [
    # Warrant / Right / Unit suffix tickers with descriptive names.
    _asset("AMACW", "AMAC CUSTOMS CORP Wt"),
    _asset("FDMMW", "FEDDY MAC Wt"),
    _asset("PECEW", "PECES CORP Wt"),
    _asset("DAAQU", "DAVE INC Unit"),
    _asset("CHARR", "CHEETAH NET Rts"),
    _asset("BRLSW", "BURU WHL Wt"),
    # Same suffixes with unhelpful names: pattern is the only signal.
    _asset("ABCDW", "ABCD HOLDINGS"),
    _asset("EFGHR", "EFGH HOLDINGS"),
    _asset("IJKLU", "IJKL HOLDINGS"),
    # Name-only detections with ordinary tickers.
    _asset("XYZAB", "XYZ Warrants Series A"),
    _asset("QQQZZ", "Zed Units Trust"),
]

KEPT = [
    _asset("AAPL", "APPLE INC COMMON STOCK"),
    _asset("SPY", "SPDR S&P 500 ETF TRUST"),
    _asset("NKE", "NIKE INC COMMON STOCK"),
    _asset("IWM", "ISHARES RUSSELL 2000 ETF"),
    _asset("QQQI", "NEOS NASDAQ-100 HIGH INCOME ETF"),
]


class TestJunkFilter:
    @pytest.mark.parametrize("symbol", [
        "AMACW", "FDMMW", "PECEW", "DAAQU", "CHARR", "BRLSW",
        "ABCDW", "EFGHR", "IJKLU", "XYZAB", "QQQZZ",
    ])
    def test_junk_is_filtered(self, symbol):
        bot = _bot(JUNK)
        assert bot.get_all_us_symbols() == [] or symbol not in bot.get_all_us_symbols()

    def test_all_junk_filtered_and_real_symbols_kept(self):
        bot = _bot(JUNK + KEPT)
        symbols = bot.get_all_us_symbols()
        assert sorted(symbols) == ["AAPL", "IWM", "NKE", "QQQI", "SPY"] or \
            symbols == ["AAPL", "SPY", "NKE", "IWM", "QQQI"]


class TestPriceFloor:
    def test_sub_5_dollar_stock_is_filtered(self):
        bot = _bot(KEPT + [_asset("PENNY", "PENNY CORP COMMON STOCK")])
        bot.get_market_data = lambda s: _df(4.99 if s == "PENNY" else 100.0)
        symbols = bot.get_all_us_symbols()
        assert "PENNY" not in symbols
        assert "AAPL" in symbols

    def test_5_dollar_stock_is_kept(self):
        bot = _bot(KEPT + [_asset("FIVE", "FIVE CORP COMMON STOCK")])
        bot.get_market_data = lambda s: _df(5.05 if s == "FIVE" else 100.0)
        assert "FIVE" in bot.get_all_us_symbols()

    def test_missing_price_data_does_not_drop_symbol(self):
        """Data failures must not silently shrink the universe."""
        bot = _bot(KEPT)
        bot.get_market_data = lambda s: None
        assert sorted(bot.get_all_us_symbols()) == ["AAPL", "IWM", "NKE", "QQQI", "SPY"]

    def test_price_floor_is_configurable(self):
        bot = _bot(KEPT + [_asset("CHEAP", "CHEAP CORP COMMON STOCK")])
        bot.get_market_data = lambda s: _df(3.00 if s == "CHEAP" else 100.0)
        assert "CHEAP" not in bot.get_all_us_symbols()
        bot.universe_min_price = 2.50
        assert "CHEAP" in bot.get_all_us_symbols()
        assert bot.__class__.UNIVERSE_MIN_PRICE_DEFAULT == 5.00


def _df(price):
    import pandas as pd
    rows = [{"timestamp": pd.Timestamp("2026-11-20", tz="UTC"),
             "open": price, "high": price, "low": price,
             "close": price, "volume": 1_000_000} for _ in range(25)]
    return pd.DataFrame(rows)
