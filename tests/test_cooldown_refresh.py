#!/usr/bin/env python3
"""Tests to ensure cooldown filtering with refresh returns consistent counts."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.smart_bot import SmartTradingBot


class DummyDB:
    def is_available(self):
        return False


def make_bot():
    # Bypass __init__ to avoid external deps; set only what we need
    bot = SmartTradingBot.__new__(SmartTradingBot)
    bot.db = DummyDB()
    bot.research_times = {}
    bot._fresh_calls = []
    return bot


def test_apply_cooldown_with_refresh_uses_filtered_fresh_list():
    bot = make_bot()

    # First filter removes most tickers (simulate cooldown)
    cooldown_set = {"AAA", "BBB", "CCC"}
    def mock_filter(tickers, cooldown_minutes=15):
        return [t for t in tickers if t not in cooldown_set]
    bot.filter_tickers_by_cooldown = mock_filter

    # Fresh list returns a new batch; should be re-filtered too
    def mock_fresh(target_count=30):
        bot._fresh_calls.append(target_count)
        return ["XXX", "YYY", "AAA"]  # includes one in cooldown
    bot._get_fresh_ticker_list = mock_fresh

    initial = ["AAA", "BBB", "CCC", "DDD"]  # only DDD survives first filter
    result = bot.apply_cooldown_with_refresh(initial, min_count=3, cooldown_minutes=15)

    # After refresh, AAA should be filtered out again; leaving XXX, YYY
    assert result == ["XXX", "YYY"]
    assert bot._fresh_calls == [30]


def test_apply_cooldown_with_refresh_enough_initial():
    bot = make_bot()

    def mock_filter(tickers, cooldown_minutes=15):
        return tickers  # no filtering
    bot.filter_tickers_by_cooldown = mock_filter

    bot._get_fresh_ticker_list = lambda target_count=30: []  # should not be called

    initial = ["A", "B", "C", "D"]
    result = bot.apply_cooldown_with_refresh(initial, min_count=3, cooldown_minutes=15)

    assert result == initial


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__]))
