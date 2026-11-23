"""
NOBUY-FIX-001 FIX 2 — junk penny warrants/units/rights must not enter the
scan queue, and the $5 universe price floor must cost ZERO extra API calls.

Regression from live paper trading on 2026-11-23: warrants/rights/units
(AMACW $0.17, FDMMW $0.24, PECEW $0.08, DAAQU, CHARR, BRLSW) dominated the
scan queue, scored 100/100 and produced 281 of 282 blocked BUY signals
(all later failing the <1M volume gate).

Review fix (rev): the first cut called ``_passes_universe_price_floor``
inside ``get_all_us_symbols()`` — one ``get_market_data()`` per active US
equity (~10k) per scan, twice per ~25 s cycle. The floor is now enforced at
ANALYSIS time, from the daily bar that is already loaded, so the universe
scan is pure string matching (zero API calls).
"""

import numpy as np
import pandas as pd
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


def _df(price, bars=25, drift=0.0):
    """Bars ending at ``price``. ``drift`` adds a small deterministic
    wiggle so RSI / MACD / BB are well defined instead of NaN-on-flat."""
    closes = [price * (1.0 + drift * np.sin(i / 3.0)) for i in range(bars)]
    rows = [{"timestamp": pd.Timestamp("2026-11-20", tz="UTC"),
             "open": c, "high": c * 1.02, "low": c * 0.98,
             "close": c, "volume": 1_000_000}
            for c in closes]
    rows[-1]["close"] = price
    return pd.DataFrame(rows)


def _universe_bot(assets):
    """Bot for get_all_us_symbols() tests. ``get_market_data`` is a mock so
    any per-symbol price fetch during the universe scan is visible."""
    bot = SmartTradingBot.__new__(SmartTradingBot)
    bot._fetch_assets_resilient = lambda: assets
    bot.universe_min_price = SmartTradingBot.UNIVERSE_MIN_PRICE_DEFAULT
    bot.get_market_data = MagicMock(side_effect=lambda s: _df(100.0))
    bot.sma_slow = 5
    bot.db = MagicMock()
    bot.db.is_available.return_value = False
    return bot


def _analysis_bot(prices, bars=60):
    """Minimal bot that can run analyze_symbol()/analyze_multi_timeframe()
    with a small, controlled price per symbol. ``get_market_data`` counts
    calls so the tests can prove the floor does not add requests."""
    bot = SmartTradingBot.__new__(SmartTradingBot)
    bot.sma_fast = 10
    bot.sma_slow = 30
    bot.rsi_period = 14
    bot.errors_count = 0
    bot.rsi_buy_threshold = 30
    bot.rsi_sell_threshold = 70
    bot.enable_volume_confirmation = True
    bot.enable_mtf_conflict_filter = True
    bot.enable_vol_downgrade_filter = True
    bot.enable_ai_conflict_filter = True
    bot.enable_multi_timeframe = True
    bot.enable_regime_filter = False
    bot.earnings_days_skip = 0
    bot.enable_sp_filter = False
    bot.hourly_weight = 0.30
    bot.scan_catalysts = lambda s: {"catalyst_score": 0}
    bot._apply_outer_liquidity_filter = lambda s, a: None
    bot.universe_min_price = SmartTradingBot.UNIVERSE_MIN_PRICE_DEFAULT
    bot.db = MagicMock()
    bot.db.is_available.return_value = False
    bot.research_times = {}
    bot._research_writes_since_cleanup = 0
    bot._last_cooldown_cleanup_at = None
    bot.cooldown_cleanup_interval_writes = 100
    bot.cooldown_cleanup_interval_seconds = 3600

    bot.get_market_data = MagicMock(
        side_effect=lambda s: _df(prices[s], bars=bars, drift=0.05)
    )
    bot.get_hourly_market_data = MagicMock(return_value=None)
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
        bot = _universe_bot(JUNK)
        symbols = bot.get_all_us_symbols()
        assert symbol not in symbols

    def test_all_junk_filtered_and_real_symbols_kept(self):
        bot = _universe_bot(JUNK + KEPT)
        symbols = bot.get_all_us_symbols()
        assert sorted(symbols) == ["AAPL", "IWM", "NKE", "QQQI", "SPY"]

    def test_is_junk_security_is_still_called_in_universe_scan(self):
        """Fix 2's junk filter must stay in get_all_us_symbols()."""
        bot = _universe_bot(JUNK + KEPT)
        bot.is_junk_security = MagicMock(side_effect=bot.is_junk_security)
        bot.get_all_us_symbols()
        assert bot.is_junk_security.call_count == len(JUNK) + len(KEPT)


class TestZeroApiUniverseScan:
    """Review fix: the scan must NEVER fetch prices per symbol."""

    def test_get_all_us_symbols_makes_zero_market_data_calls(self):
        assets = JUNK + KEPT + [
            _asset(f"SYM{i:03d}", f"SYMBOL {i} COMMON STOCK")
            for i in range(200)
        ]
        bot = _universe_bot(assets)
        symbols = bot.get_all_us_symbols()
        assert len(symbols) == len(KEPT) + 200
        assert bot.get_market_data.call_count == 0

    def test_universe_scan_does_not_touch_the_price_floor_at_all(self):
        bot = _universe_bot(JUNK + KEPT)
        bot._passes_price_floor_value = MagicMock(
            side_effect=AssertionError("universe scan must not price-check"),
        )
        assert sorted(bot.get_all_us_symbols()) == ["AAPL", "IWM", "NKE", "QQQI", "SPY"]
        assert bot.get_market_data.call_count == 0

    def test_legacy_per_symbol_price_floor_helper_is_gone(self):
        assert not hasattr(SmartTradingBot, "_passes_universe_price_floor")


class TestPriceFloorAtAnalysisTime:
    """The $5 floor is enforced from the already-loaded daily bar."""

    def test_sub_5_dollar_symbol_is_ineligible_without_extra_api_calls(self):
        bot = _analysis_bot({"PENNY": 4.99})
        before = bot.get_market_data.call_count
        result = bot.analyze_symbol("PENNY")
        assert result is not None
        assert result["signal"] == "HOLD"
        assert result["universe_price_floor_ineligible"] is True
        assert result["passes_all_buy_criteria"] is False
        assert bot.get_market_data.call_count == before + 1

    def test_5_dollar_symbol_passes_the_floor(self):
        bot = _analysis_bot({"FIVE": 5.00})
        result = bot.analyze_symbol("FIVE")
        assert result is not None
        assert not result.get("universe_price_floor_ineligible")

    def test_above_5_dollar_stock_and_etf_pass_the_floor(self):
        bot = _analysis_bot({"AAPL": 180.0, "SPY": 600.0})
        for sym in ("AAPL", "SPY"):
            result = bot.analyze_symbol(sym)
            assert result is not None
            assert not result.get("universe_price_floor_ineligible")

    def test_multi_timeframe_path_enforces_the_floor(self):
        bot = _analysis_bot({"PENNY": 3.10})
        before = bot.get_market_data.call_count
        result = bot.analyze_multi_timeframe("PENNY")
        assert result is not None
        assert result["signal"] == "HOLD"
        assert result["multi_timeframe"] is True
        assert result["universe_price_floor_ineligible"] is True
        # One daily fetch (already required) + at most one hourly fetch for
        # the MTF signal; the floor itself adds ZERO price requests.
        assert bot.get_market_data.call_count == before + 1
        assert bot.get_hourly_market_data.call_count <= 1

    def test_price_floor_is_configurable(self):
        bot = _analysis_bot({"CHEAP": 3.00})
        assert bot.analyze_symbol("CHEAP")["universe_price_floor_ineligible"]
        bot.universe_min_price = 2.50
        assert not bot.analyze_symbol("CHEAP").get("universe_price_floor_ineligible")
        assert SmartTradingBot.UNIVERSE_MIN_PRICE_DEFAULT == 5.00

    def test_floor_disabled_when_zero(self):
        bot = _analysis_bot({"PENNY": 0.42})
        bot.universe_min_price = 0
        assert not bot.analyze_symbol("PENNY").get("universe_price_floor_ineligible")

    def test_decision_snapshot_reports_floor_ineligibility(self):
        """OBS-001 coverage: the persisted row says *why* it was a HOLD."""
        bot = _analysis_bot({"PENNY": 4.01})
        result = bot.analyze_symbol("PENNY")
        snapshot = bot._build_decision_snapshot("PENNY", result)
        eligibility = snapshot["strategy_eligibility"]
        assert eligibility["strategy_eligible"] is False
        assert "below universe price floor" in eligibility["strategy_reason"]
        assert snapshot["decision"]["outcome"] == "HOLD_INELIGIBLE"

    def test_pure_floor_helper_fails_open(self):
        bot = SmartTradingBot.__new__(SmartTradingBot)
        bot.universe_min_price = 5.0
        assert bot._passes_price_floor_value(float("nan"))
        assert bot._passes_price_floor_value(None)
        assert bot._passes_price_floor_value(-1)
        assert bot._passes_price_floor_value(5.0)
        assert not bot._passes_price_floor_value(4.99)
