"""DB-GROWTH-FIX-5 — the nightly max-rows setting is the ONLY nightly limit.

Regression tests for the hidden batch-count limit (10) that used to stop
the nightly prune at 50 000 rows (10 batches x 5000) instead of honoring
``decision_history_prune_max_rows_per_night`` (default 200000).

Every test runs against a small temporary SQLite database (tmp_path). The
production ``trading_bot.db`` is never opened.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from test_db_growth_fix_acceptance import (  # noqa: E402,F401
    _count,
    _fail_closed_production_db_guard,
    _make_bot,
    isolated_db,
)


def _seed_old_rows(isolated_db, count):
    """Insert ``count`` decision_history rows older than the retention
    window (and one fresh row that must never be pruned)."""
    _db, sqlite_db_module, _p = isolated_db
    old_start = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    fresh_start = datetime.now(timezone.utc).isoformat()
    with sqlite_db_module._get_conn() as conn:
        for i in range(count):
            conn.execute(
                "INSERT INTO decision_history (cycle_id, symbol, cycle_start, "
                "decision_schema_version, decision_snapshot, "
                "analytics_persistence_version) VALUES (?,?,?,?,?,1)",
                (f"fx5_old_{i}", f"OLD{i}", old_start, 1,
                 json.dumps({"decision": {"outcome": "HOLD_INELIGIBLE"}})),
            )
        conn.execute(
            "INSERT INTO decision_history (cycle_id, symbol, cycle_start, "
            "decision_schema_version, decision_snapshot, "
            "analytics_persistence_version) VALUES (?,?,?,?,?,1)",
            ("fx5_fresh", "FRESH", fresh_start, 1,
             json.dumps({"decision": {"outcome": "HOLD_INELIGIBLE"}})),
        )
    return count


class TestNoHiddenBatchLimit:
    def test_direct_prune_runs_more_than_ten_batches(self, isolated_db,
                                                      tmp_path):
        """30 old rows, batch size 2, limit 100 => 15 batches are needed.

        The old hidden 10-batch cap left 10 rows behind.
        """
        _db, sqlite_db_module, _p = isolated_db
        _seed_old_rows(isolated_db, 30)
        result = sqlite_db_module.SQLiteDB().prune_decision_history(
            retention_days=30, batch_size=2, max_rows_per_night=100,
            archive_dir=str(tmp_path / "archive"),
        )
        assert result["error"] is None, result["error"]
        assert result["parents_deleted"] == 30
        assert result["batches"] == 15, "the prune stopped early"
        assert _count(
            isolated_db, "SELECT COUNT(*) FROM decision_history",
        ) == 1  # only the fresh row survives

    def test_prune_stops_exactly_at_the_limit(self, isolated_db, tmp_path):
        """30 old rows, limit 10 => exactly 10 rows archived + deleted."""
        _db, sqlite_db_module, _p = isolated_db
        _seed_old_rows(isolated_db, 30)
        result = sqlite_db_module.SQLiteDB().prune_decision_history(
            retention_days=30, batch_size=2, max_rows_per_night=10,
            archive_dir=str(tmp_path / "archive"),
        )
        assert result["error"] is None, result["error"]
        assert result["parents_deleted"] == 10
        assert _count(
            isolated_db, "SELECT COUNT(*) FROM decision_history",
        ) == 21  # 30 old rows left + 1 fresh row

    def test_batch_count_is_derived_from_the_cap(self, isolated_db, tmp_path):
        """The batch budget is derived from the cap, not fixed at 10."""
        _db, sqlite_db_module, _p = isolated_db
        result = sqlite_db_module.SQLiteDB().prune_decision_history(
            retention_days=30, batch_size=5000, max_rows_per_night=200000,
            archive_dir=str(tmp_path / "archive"),
        )
        assert result["error"] is None
        assert result["max_batches"] == 40  # 200000 / 5000

    def test_explicit_max_batches_is_still_honored(self, isolated_db,
                                                   tmp_path):
        """An explicit max_batches is still respected (backwards
        compatible), but it is never used by the nightly path."""
        _db, sqlite_db_module, _p = isolated_db
        _seed_old_rows(isolated_db, 30)
        result = sqlite_db_module.SQLiteDB().prune_decision_history(
            retention_days=30, batch_size=2, max_batches=3,
            max_rows_per_night=100, archive_dir=str(tmp_path / "archive"),
        )
        assert result["error"] is None
        assert result["parents_deleted"] == 6  # 3 batches x 2 rows


class TestBotNightlyPath:
    def _bot(self, isolated_db, tmp_path, *, max_rows):
        bot = _make_bot(isolated_db, prune_enabled=True,
                        archive_dir=str(tmp_path / "archive"), batch_size=2)
        bot.decision_history_prune_max_rows_per_night = max_rows
        return bot

    def test_bot_nightly_prune_runs_more_than_ten_batches(self, isolated_db,
                                                           tmp_path):
        """The bot's own nightly path honors the max-rows setting."""
        _db, sqlite_db_module, _p = isolated_db
        _seed_old_rows(isolated_db, 30)
        bot = self._bot(isolated_db, tmp_path, max_rows=100)
        summary = bot.run_decision_history_retention_prune()
        assert summary["error"] is None, summary["error"]
        assert summary["parents_deleted"] == 30
        assert summary["batches"] == 15
        assert _count(
            isolated_db, "SELECT COUNT(*) FROM decision_history",
        ) == 1

    def test_bot_nightly_prune_stops_exactly_at_the_limit(self, isolated_db,
                                                          tmp_path):
        _db, sqlite_db_module, _p = isolated_db
        _seed_old_rows(isolated_db, 30)
        bot = self._bot(isolated_db, tmp_path, max_rows=10)
        summary = bot.run_decision_history_retention_prune()
        assert summary["error"] is None, summary["error"]
        assert summary["parents_deleted"] == 10
        assert _count(
            isolated_db, "SELECT COUNT(*) FROM decision_history",
        ) == 21
