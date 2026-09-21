"""PHASE-C13 — Bootstrap durability regression test.

PHASE-C12 created `idx_decision_history_cycle_start` in the production
DB. PHASE-C13 makes that index durable: a fresh SQLite database built
from `SQLiteDB._init_schema()` MUST also create the same index. Without
this test, a rebuild / drop / restore of `trading_bot.db` would lose
the Phase C analytics speedup silently.

This test exercises the bootstrap path against a temporary SQLite file
(monkeypatched `DB_PATH`). It never touches `trading_bot.db`.

Hard rules (per MENTOR.md and AGENTS.md):
- Read-only on production.
- Temporary / synthetic DB only.
- No network, no brokerage, no bot construction.
"""

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

# Ensure src/ is on sys.path so `database.sqlite_db` resolves (same
# pattern as test_bot001_dashboard_status.py and test_obs_002).
_REPO_ROOT = Path(__file__).parent.parent
_SRC_ROOT = _REPO_ROOT / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

import pytest


# ── helpers ──────────────────────────────────────────────────────────────


def _make_fresh_db(monkeypatch):
    """Create a brand-new SQLite file, redirect DB_PATH to it on BOTH
    module references, run SQLiteDB._init_schema(), and yield the path.

    Mirrors the fixture used in tests/test_bot001_dashboard_status.py
    and tests/test_obs_002_terminal_decision_coverage.py. The test that
    uses this fixture is fully isolated from trading_bot.db.
    """
    import src.database.sqlite_db as sqlite_mod_src
    import database.sqlite_db as sqlite_mod

    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
    tmp.close()
    new_path = Path(tmp.name)

    monkeypatch.setattr(sqlite_mod, "DB_PATH", new_path)
    monkeypatch.setattr(sqlite_mod_src, "DB_PATH", new_path)

    # Run the production schema bootstrap against the temp file.
    sqlite_mod.sqlite_db._init_schema()
    return new_path


def _indexed_columns(db_path, index_name):
    """Return the ordered list of column names an index covers on its
    table. Empty list if the index does not exist.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT name, tbl_name FROM sqlite_master "
            "WHERE type='index' AND name=?",
            (index_name,),
        ).fetchall()
        if not rows:
            return []
        idx_name, tbl_name = rows[0][0], rows[0][1]
        cols = [r[2] for r in conn.execute(
            f"PRAGMA index_info({idx_name})"
        ).fetchall()]
        return cols
    finally:
        conn.close()


def _decision_history_indexes(db_path):
    """Return the list of non-autoindex index names on decision_history."""
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='index' AND tbl_name='decision_history' "
            "AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


# ── 1. The new index must be created on a fresh DB ──────────────────────


class TestCycleStartIndexBootstrap:

    def test_cycle_start_index_present_after_init_schema(self, monkeypatch):
        """A fresh SQLite DB built via SQLiteDB._init_schema() MUST
        contain idx_decision_history_cycle_start. This is the durable
        counterpart to the DDL applied in PHASE-C12 to the production
        DB. If this fails, a rebuild / new DB would lose the Phase C
        analytics speedup (64–114× on `latest` queries).
        """
        db_path = _make_fresh_db(monkeypatch)

        indexes = _decision_history_indexes(db_path)
        assert "idx_decision_history_cycle_start" in indexes, (
            f"idx_decision_history_cycle_start missing from fresh DB; "
            f"found indexes: {indexes}"
        )

    def test_cycle_start_index_covers_only_cycle_start(self, monkeypatch):
        """The new index must cover EXACTLY one column: cycle_start.
        A composite index would waste space and could subtly change
        SQLite's query planner choices for Phase C endpoints.
        """
        db_path = _make_fresh_db(monkeypatch)
        cols = _indexed_columns(db_path, "idx_decision_history_cycle_start")
        assert cols == ["cycle_start"], (
            f"idx_decision_history_cycle_start must cover exactly "
            f"['cycle_start']; got {cols}"
        )

    def test_cycle_start_index_used_for_cohort_filter(self, monkeypatch):
        """The index must actually be usable for the Phase C cohort
        filter (`cycle_start >= ?`). EXPLAIN QUERY PLAN must show
        SEARCH USING INDEX, not SCAN. If SQLite doesn't pick the
        index for the canonical cohort/range predicate, the index
        addition is useless.
        """
        db_path = _make_fresh_db(monkeypatch)

        # Insert one row so the planner has something to seek into.
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute(
                "INSERT INTO decision_history "
                "(cycle_id, symbol, cycle_start, decision_schema_version, decision_snapshot) "
                "VALUES (?, ?, ?, ?, ?)",
                ("c_test_001", "X", "2026-09-21T00:00:00+00:00", 1, "{}"),
            )
            conn.commit()
        finally:
            conn.close()

        # Run EXPLAIN QUERY PLAN for the canonical Phase C cohort/range
        # predicate on the new DB. We expect the planner to use the
        # cycle_start index.
        conn = sqlite3.connect(str(db_path))
        try:
            plan = conn.execute(
                "EXPLAIN QUERY PLAN "
                "SELECT COUNT(*) FROM decision_history "
                "WHERE cycle_start >= ? AND cycle_start >= ?",
                ("2026-09-14T20:24:55", "2026-09-20T00:00:00+00:00"),
            ).fetchall()
        finally:
            conn.close()

        plan_text = " | ".join(str(r[3]) for r in plan)
        assert "idx_decision_history_cycle_start" in plan_text, (
            f"SQLite did not pick idx_decision_history_cycle_start for "
            f"the cohort/range predicate. Plan: {plan_text}"
        )
        assert "SCAN decision_history" not in plan_text, (
            f"SQLite fell back to a full scan; index is not usable. "
            f"Plan: {plan_text}"
        )


# ── 2. Existing decision_history indexes must still exist ──────────────


class TestExistingDecisionHistoryIndexesPreserved:

    def test_symbol_index_still_present(self, monkeypatch):
        db_path = _make_fresh_db(monkeypatch)
        indexes = _decision_history_indexes(db_path)
        assert "idx_decision_history_symbol" in indexes, (
            f"idx_decision_history_symbol was lost on fresh bootstrap; "
            f"found indexes: {indexes}"
        )
        cols = _indexed_columns(db_path, "idx_decision_history_symbol")
        assert cols == ["symbol", "cycle_start"], (
            f"idx_decision_history_symbol column list changed: {cols}"
        )

    def test_session_index_still_present(self, monkeypatch):
        db_path = _make_fresh_db(monkeypatch)
        indexes = _decision_history_indexes(db_path)
        assert "idx_decision_history_session" in indexes, (
            f"idx_decision_history_session was lost on fresh bootstrap; "
            f"found indexes: {indexes}"
        )
        cols = _indexed_columns(db_path, "idx_decision_history_session")
        assert cols == ["session_id", "cycle_start"], (
            f"idx_decision_history_session column list changed: {cols}"
        )

    def test_decision_history_has_exactly_three_explicit_indexes(
        self, monkeypatch
    ):
        """After PHASE-C13, decision_history should have exactly three
        explicit (non-autoindex) indexes:
          - idx_decision_history_symbol (pre-existing)
          - idx_decision_history_session (pre-existing)
          - idx_decision_history_cycle_start (PHASE-C13)
        Plus the implicit UNIQUE(cycle_id, symbol) autoindex.

        This test guards against accidental duplicate CREATE INDEX
        statements or schema drift in future slices.
        """
        db_path = _make_fresh_db(monkeypatch)
        indexes = _decision_history_indexes(db_path)
        assert indexes == [
            "idx_decision_history_cycle_start",
            "idx_decision_history_session",
            "idx_decision_history_symbol",
        ], (
            f"decision_history explicit indexes drifted from expected "
            f"three-index contract; got {indexes}"
        )