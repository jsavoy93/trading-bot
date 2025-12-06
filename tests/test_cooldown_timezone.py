#!/usr/bin/env python3
"""Tests for timezone-aware cooldown checks when DB returns naive timestamps."""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Ensure src is on path for local tests
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.smart_bot import SmartTradingBot


class _DbStub:
    def __init__(self, trade_time=None, analysis_time=None, research_time=None):
        self._trade_time = trade_time
        self._analysis_time = analysis_time
        self._research_time = research_time

    def is_available(self):
        return True

    def get_trade_cooldown(self, symbol):
        return self._trade_time

    def get_position_sell_cooldown(self, symbol):
        return self._analysis_time

    def get_research_cooldown(self, symbol):
        return self._research_time


def _bot_with_db(db):
    bot = SmartTradingBot.__new__(SmartTradingBot)
    bot.db = db
    bot.recent_trades = {}
    bot.position_sell_analysis_times = {}
    bot.research_times = {}
    return bot


def test_is_in_cooldown_handles_naive_db_timestamp():
    now_naive = datetime.now()  # naive
    db = _DbStub(trade_time=now_naive - timedelta(minutes=5))
    bot = _bot_with_db(db)

    assert bot.is_in_cooldown("AAPL", cooldown_minutes=30) is True


def test_is_position_sell_in_cooldown_handles_naive_db_timestamp():
    now_naive = datetime.now()
    db = _DbStub(analysis_time=now_naive - timedelta(minutes=10))
    bot = _bot_with_db(db)

    assert bot.is_position_sell_in_cooldown("AAPL", cooldown_minutes=30) is True


def test_is_in_research_cooldown_handles_naive_db_timestamp():
    now_naive = datetime.now()
    db = _DbStub(research_time=now_naive - timedelta(minutes=8))
    bot = _bot_with_db(db)

    assert bot.is_in_research_cooldown("AAPL", cooldown_minutes=15) is True


def test_cooldown_expired_returns_false():
    old_time = datetime.now(timezone.utc) - timedelta(hours=2)
    db = _DbStub(trade_time=old_time)
    bot = _bot_with_db(db)

    assert bot.is_in_cooldown("AAPL", cooldown_minutes=30) is False
