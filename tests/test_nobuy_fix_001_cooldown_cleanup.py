"""
NOBUY-FIX-001 FIX 4 — cooldown cleanup must not run on every symbol call.

The old gate was ``len(self.research_times) % 100 == 0``. When the DB is the
cooldown store the in-memory dict stays empty, so ``0 % 100 == 0`` was always
true and cleanup ran (and logged at INFO) on every mark_recent_research call
— roughly 71k identical log lines per day.
"""

import logging
from datetime import datetime, timedelta

from src.core.smart_bot import SmartTradingBot


class _FakeDB:
    def __init__(self):
        self.calls = 0
        self.deleted = 0

    def is_available(self):
        return True

    def set_research_cooldown(self, symbol, research_time):
        return True

    def cleanup_old_cooldowns(self, days_old=7):
        self.calls += 1
        return self.deleted


def _make_bot():
    bot = SmartTradingBot.__new__(SmartTradingBot)
    bot.db = _FakeDB()
    bot.research_times = {}
    bot._research_writes_since_cleanup = 0
    bot._last_cooldown_cleanup_at = None
    bot.cooldown_cleanup_interval_writes = 100
    bot.cooldown_cleanup_interval_seconds = 3600
    return bot


def test_cleanup_not_run_on_every_symbol_call():
    bot = _make_bot()
    for i in range(99):
        bot.mark_recent_research(f"SYM{i}")
    assert bot.db.calls == 0, "cleanup must not run on every symbol call"
    assert bot.research_times == {}, "dict stays empty when the DB is the store"


def test_cleanup_runs_on_the_100th_write():
    bot = _make_bot()
    for i in range(100):
        bot.mark_recent_research(f"SYM{i}")
    assert bot.db.calls == 1
    # Counter resets, so the next 99 writes are quiet again.
    for i in range(99):
        bot.mark_recent_research(f"AFTER{i}")
    assert bot.db.calls == 1
    bot.mark_recent_research("AFTER100")
    assert bot.db.calls == 2


def test_cleanup_time_floor_runs_hourly_on_low_traffic():
    bot = _make_bot()
    bot.mark_recent_research("AAA")          # first write: no cleanup yet
    assert bot.db.calls == 0
    bot._last_cooldown_cleanup_at = datetime.now() - timedelta(seconds=3700)
    bot.mark_recent_research("BBB")
    assert bot.db.calls == 1, "an hour must pass even without 100 writes"
    # Immediately after, the time floor is reset: next write does not clean.
    bot.mark_recent_research("CCC")
    assert bot.db.calls == 1


def test_cleanup_logs_info_only_when_rows_deleted(caplog, monkeypatch):
    import src.database.sqlite_db as sqlite_db

    conns = []

    class _Conn:
        def __init__(self):
            self.deleted_rows = [None]  # set by the test per scenario

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql, params):
            class _Cur:
                rowcount = conns[0].deleted_rows[0] if conns[0].deleted_rows[0] is not None else 0
            return _Cur()

    conn = _Conn()

    def _fake_get_conn():
        conns[:] = [conn]
        return conn

    monkeypatch.setattr(sqlite_db, "_get_conn", _fake_get_conn)
    db = sqlite_db.SQLiteDB.__new__(sqlite_db.SQLiteDB)
    logger = logging.getLogger(sqlite_db.__name__)

    with caplog.at_level(logging.DEBUG, logger=logger.name):
        conn.deleted_rows = [0]
        assert db.cleanup_old_cooldowns(days_old=7) == 0
    cleanup_info = [
        r for r in caplog.records
        if r.levelno == logging.INFO and "Cleaned up" in r.getMessage()
    ]
    assert cleanup_info == [], "no INFO spam when nothing was deleted"

    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger=logger.name):
        conn.deleted_rows = [3]
        assert db.cleanup_old_cooldowns(days_old=7) > 0
    text = "\n".join(r.getMessage() for r in caplog.records if r.levelno == logging.INFO)
    assert "Cleaned up" in text
