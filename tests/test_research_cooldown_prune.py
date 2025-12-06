#!/usr/bin/env python3
"""Unit tests for pruning in-memory research cooldowns when DB is unavailable."""

import sys
from datetime import datetime, timedelta
from pathlib import Path

# Ensure src is on path for local tests
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.smart_bot import SmartTradingBot


class _DbUnavailable:
    def is_available(self):
        return False


def test_prunes_expired_research_times_and_returns_recent():
    bot = SmartTradingBot.__new__(SmartTradingBot)
    bot.db = _DbUnavailable()
    now = datetime.now()
    bot.research_times = {
        "OLD": now - timedelta(minutes=30),
        "RECENT": now - timedelta(minutes=5),
    }

    recent = bot.get_recently_researched_tickers(cooldown_minutes=15)

    assert recent == ["RECENT"]
    assert "OLD" not in bot.research_times
    assert len(bot.research_times) == 1


def test_prune_helper_counts_deleted():
    bot = SmartTradingBot.__new__(SmartTradingBot)
    bot.db = _DbUnavailable()
    now = datetime.now()
    bot.research_times = {
        "OLD1": now - timedelta(hours=2),
        "OLD2": now - timedelta(hours=3),
        "NEW": now,
    }

    pruned = bot._prune_research_times(now - timedelta(hours=1))

    assert pruned == 2
    assert bot.research_times == {"NEW": bot.research_times["NEW"]}


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__]))
