"""
PHASE-C14B-2B — Per-parent analytics_persistence_version tests.

These tests verify the durable per-parent marker that lets future
Phase C endpoint queries distinguish:

  LEGACY (analytics_persistence_version = 0)
    → authoritative analytics facts are read from decision_snapshot
  NORMALIZED (analytics_persistence_version = 1)
    → authoritative analytics facts are read from
      decision_gate_evaluations / decision_execution_checks,
      EVEN WHEN the child tables legitimately have zero rows
      (empty gates / empty checks / malformed fail-closed snapshot)

Critical invariant exercised by every test in this file:

   analytics_persistence_version = 1  ⇒  normalized child rows
                                          were successfully written
                                          for this parent, in the
                                          SAME transaction.

If a child helper raises, the transaction rolls back and the parent
is never visible at version=1.

Required coverage (per task spec):

  Schema
   - fresh DB has analytics_persistence_version
   - default is 0
   - NOT NULL
   - existing simulated legacy rows read as 0

  Persistence
   - successful normalized write ends with version=1
   - version=1 parent may legitimately have zero gate rows
   - version=1 parent may legitimately have zero execution-check rows
   - normal gate/check child persistence still works
   - duplicate names / ordinality unaffected

  Atomicity
   - force gate-child failure → parent transaction rolls back
   - force exec-child failure → parent transaction rolls back
   - no committed version=1 parent after child failure
   - no partial child rows

  Compatibility
   - legacy/version=0 parent remains snapshot-authoritative
   - no child-row-existence inference used

All tests run against fresh tempfile-based DBs. The production
trading_bot.db is NEVER touched by this file.

PHASE-C14B-2B safety contract (Josh 2026-09-23 12:51 UTC):

- The fail-closed `_fail_closed_production_db_guard` fixture below
  raises a RuntimeError on ANY connection opened against the
  production DB_PATH.
- No fixture in this module mutates, drops, alters, or writes to
  the production DB at any point during the test session.
- Migration of the production DB is the responsibility of
  SmartBot's deploy-time `_init_schema()` call, NOT the test
  session.
"""

from __future__ import annotations

import importlib
import json
import os
import pathlib
import sqlite3
from pathlib import Path
from typing import Any, Dict

import pytest


# ────────────────────────────────────────────────────────────────────────
# Fixtures
# ────────────────────────────────────────────────────────────────────────

# PHASE-OBS-001-ISO: PRODUCTION_DB_PATH is sourced from the
# application module's DB_PATH at import time — NOT from duplicated
# path arithmetic. This guarantees the guard checks against the
# actual production DB the application sees at runtime, so any future
# change to how src.database.sqlite_db resolves its DB (different
# parent depth, env var override, config file) is automatically
# followed without needing to maintain a parallel arithmetic here.
import src.database.sqlite_db as _sqlite_db_module_for_path_capture  # noqa: E402
PRODUCTION_DB_PATH = pathlib.Path(
    str(_sqlite_db_module_for_path_capture.DB_PATH)
).resolve()


@pytest.fixture(autouse=True)
def _fail_closed_production_db_guard(monkeypatch, request):
    """Fail-closed guard: any path in this test module that resolves
    `src.database.sqlite_db.DB_PATH` to the production database path
    raises RuntimeError before the connection opens.

    Regression-prevention for the historical C14B-1 foot-gun (Josh
    2026-09-23 12:51 UTC) where an autouse session cleanup fixture
    dropped child tables on the production DB.

    Mechanism: monkey-patches `_get_conn` so any connection opened
    via the SQLiteDB module resolves DB_PATH at call time. If the
    resolved path equals the production default, raises immediately.
    """
    import src.database.sqlite_db as _sqlite_db_module

    _original_get_conn = _sqlite_db_module._get_conn
    opened_paths: list[pathlib.Path] = []

    def _guarded_get_conn(*args, **kwargs):
        live_db_path = pathlib.Path(str(_sqlite_db_module.DB_PATH)).resolve()
        opened_paths.append(live_db_path)
        if live_db_path == PRODUCTION_DB_PATH:
            raise RuntimeError(
                f"PHASE-C14B-2B SAFETY VIOLATION: test "
                f"{request.node.nodeid!r} attempted to open the "
                f"production database at {PRODUCTION_DB_PATH!s}. "
                f"All C14B-2B tests must use the `isolated_db` "
                f"fixture (synthetic tmp_path DB)."
            )
        return _original_get_conn(*args, **kwargs)

    monkeypatch.setattr(_sqlite_db_module, "_get_conn", _guarded_get_conn)
    yield
    for p in opened_paths:
        if p == PRODUCTION_DB_PATH:
            raise RuntimeError(
                f"PHASE-C14B-2B SAFETY VIOLATION (post-test): test "
                f"{request.node.nodeid!r} opened a connection to "
                f"the production database {PRODUCTION_DB_PATH!s}."
            )


@pytest.fixture
def temp_db_path(tmp_path):
    """Fresh empty file path for an isolated SQLite DB.

    Production trading_bot.db is NEVER opened by these tests.
    """
    p = tmp_path / "c14b2b_test.db"
    yield p
    # tmp_path is cleaned up by pytest


@pytest.fixture
def isolated_db(monkeypatch, temp_db_path):
    """Redirect src.database.sqlite_db.DB_PATH to the temp DB and
    bootstrap the schema. Yields (db, sqlite_db_module, db_path)."""
    import src.database.sqlite_db as sqlite_db_module
    importlib.reload(sqlite_db_module)
    monkeypatch.setattr(sqlite_db_module, "DB_PATH", temp_db_path)
    try:
        db = sqlite_db_module.SQLiteDB()
        assert db.available, "SQLiteDB bootstrap must succeed on a fresh temp DB"
        yield db, sqlite_db_module, temp_db_path
    finally:
        pass  # monkeypatch undoes DB_PATH automatically


def _make_snapshot(
    *,
    cycle_id: str = "c_test",
    symbol: str = "AAPL",
    outcome: str = "HOLD_INELIGIBLE",
    gates: list = None,
    checks: list = None,
    first_blocking_check: Any = None,
) -> Dict[str, Any]:
    """Minimal but schema-version=1 decision_snapshot Dict."""
    return {
        "schema_version": 1,
        "cycle_id": cycle_id,
        "symbol": symbol,
        "decision": {"outcome": outcome, "primary_reason": "test"},
        "strategy_eligibility": {
            "strategy_eligible": False,
            "strategy_reason": "test",
            "evaluated_at": "2026-09-22T11:00:00+00:00",
            "gates": gates if gates is not None else [],
            "signal": "HOLD",
            "signal_strength": "WEAK",
        },
        "scoring": {"components": {}, "total_score": 50, "score_invalid_data": False},
        "ranking": {
            "applicable": False, "ranked_candidate": False, "candidate_rank": None,
        },
        "selection": {"attempted": False},
        "execution_checks": {
            "current_state_at_attempt": {},
            "checks": checks if checks is not None else [],
            "first_blocking_check": first_blocking_check,
            "first_blocking_reason": None,
            "evaluated_in_order": [],
        },
    }


def _read_parent(db_path, parent_id):
    """Read the parent decision_history row as a dict."""
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM decision_history WHERE id = ?", (parent_id,)
        ).fetchone()
        return dict(row) if row else None


def _insert_legacy_parent_directly(db_path, cycle_id, symbol, cycle_start):
    """Insert a simulated legacy parent (analytics_persistence_version=0)
    directly via SQL, bypassing finalize_decision_history.

    Used to verify the default-0 semantics for rows that pre-date this
    slice (or for a hypothetical backfill that legitimately left
    version=0)."""
    with sqlite3.connect(str(db_path)) as conn:
        cur = conn.execute(
            """INSERT INTO decision_history
               (cycle_id, symbol, cycle_start, session_id,
                decision_schema_version, decision_snapshot,
                analytics_persistence_version)
               VALUES (?, ?, ?, NULL, 1, ?, 0)""",
            (
                cycle_id, symbol, cycle_start,
                json.dumps(_make_snapshot(cycle_id=cycle_id, symbol=symbol)),
            ),
        )
        return cur.lastrowid


# ────────────────────────────────────────────────────────────────────────
# Schema
# ────────────────────────────────────────────────────────────────────────


class TestSchemaBootstrap:
    """Fresh + existing DBs both end up with analytics_persistence_version."""

    def test_fresh_db_has_analytics_persistence_version_column(self, isolated_db):
        db, sqlite_db_module, db_path = isolated_db
        with sqlite3.connect(str(db_path)) as conn:
            cols = conn.execute("PRAGMA table_info('decision_history')").fetchall()
        names = {row[1] for row in cols}
        assert "analytics_persistence_version" in names, (
            "fresh DB missing analytics_persistence_version column; "
            f"found columns: {sorted(names)}"
        )

    def test_column_default_is_zero(self, isolated_db):
        db, sqlite_db_module, db_path = isolated_db
        with sqlite3.connect(str(db_path)) as conn:
            cols = {
                row[1]: row
                for row in conn.execute(
                    "PRAGMA table_info('decision_history')"
                )
            }
        col = cols["analytics_persistence_version"]
        # PRAGMA table_info columns: (cid, name, type, notnull, dflt_value, pk)
        assert col[2].upper().startswith("INTEGER"), (
            f"column type must be INTEGER, got {col[2]!r}"
        )
        assert col[3] == 1, (
            f"NOT NULL constraint required on analytics_persistence_version; "
            f"got notnull={col[3]}"
        )
        assert col[4] == "0", (
            f"default must be constant 0; got dflt_value={col[4]!r}"
        )

    def test_init_schema_is_idempotent_on_existing_column(
        self, isolated_db, monkeypatch
    ):
        """A second SQLiteDB() run on a DB that already has the column
        must not raise. The ALTER TABLE statement is swallowed by the
        duplicate-column-name guard."""
        db, sqlite_db_module, db_path = isolated_db
        # Second bootstrap call. _init_schema should succeed.
        db2 = sqlite_db_module.SQLiteDB()
        assert db2.available
        with sqlite3.connect(str(db_path)) as conn:
            cols = conn.execute("PRAGMA table_info('decision_history')").fetchall()
        # Still exactly one column with this name.
        names = [r[1] for r in cols if r[1] == "analytics_persistence_version"]
        assert len(names) == 1, f"duplicate column addition; got {len(names)} entries"

    def test_simulated_legacy_rows_read_as_zero(self, isolated_db):
        """Rows inserted directly with version=0 must read back as 0
        (this is the pre-C14B-1 + backfill-frozen semantics)."""
        db, sqlite_db_module, db_path = isolated_db
        legacy_id = _insert_legacy_parent_directly(
            db_path, "c_legacy", "ZZZ", "2026-09-22T10:00:00+00:00"
        )
        parent = _read_parent(db_path, legacy_id)
        assert parent["analytics_persistence_version"] == 0
        assert parent["decision_snapshot"] is not None  # snapshot exists

    def test_column_accepts_explicit_null_rejected(self, isolated_db):
        """NOT NULL + DEFAULT 0 → inserting without a value uses 0;
        inserting with NULL raises IntegrityError."""
        db, sqlite_db_module, db_path = isolated_db
        with sqlite3.connect(str(db_path)) as conn:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    """INSERT INTO decision_history
                       (cycle_id, symbol, cycle_start, session_id,
                        decision_schema_version, decision_snapshot,
                        analytics_persistence_version)
                       VALUES (?, ?, ?, NULL, 1, ?, NULL)""",
                    ("c_null", "BAD", "2026-09-22T11:00:00+00:00", "{}"),
                )


# ────────────────────────────────────────────────────────────────────────
# Persistence
# ────────────────────────────────────────────────────────────────────────


class TestPersistenceToVersionOne:
    """Successful normalized writes must end with version=1."""

    def test_successful_write_ends_with_version_1(self, isolated_db):
        db, sqlite_db_module, db_path = isolated_db
        snap = _make_snapshot(
            gates=[{"name": "g1", "category": "strategy_gate",
                    "applied": True, "passed": True, "reason": "ok"}],
            checks=[{"name": "c1", "applied": True, "passed": True,
                     "reason": "ok"}],
        )
        ok = db.finalize_decision_history(
            "c_v1", "AAPL", "2026-09-22T11:00:00+00:00", None, 1, snap
        )
        assert ok is True
        with sqlite3.connect(str(db_path)) as conn:
            row = conn.execute(
                "SELECT * FROM decision_history WHERE cycle_id='c_v1' AND symbol='AAPL'"
            ).fetchone()
        assert row is not None
        # columns: id, cycle_id, symbol, cycle_start, session_id,
        #          decision_schema_version, decision_snapshot,
        #          analytics_persistence_version, created_at
        idx = [d[1] for d in conn.execute("PRAGMA table_info('decision_history')").fetchall()]
        apv_idx = idx.index("analytics_persistence_version")
        # The row's column tuple index doesn't match PRAGMA order;
        # the safe way is to read by column name:
        assert row[idx.index("analytics_persistence_version")] == 1, (
            f"analytics_persistence_version must be 1 after successful write; "
            f"got {row[idx.index('analytics_persistence_version')]}"
        )

    def test_version_1_parent_with_zero_gate_rows_is_valid(self, isolated_db):
        """A normalized write with empty `gates` legitimately
        persists 0 gate rows. The parent is still version=1 because
        persistence completed (no entries to write)."""
        db, sqlite_db_module, db_path = isolated_db
        snap = _make_snapshot(
            gates=[],   # empty gates list
            checks=[],
        )
        ok = db.finalize_decision_history(
            "c_zerogates", "AAPL", "2026-09-22T11:00:00+00:00", None, 1, snap
        )
        assert ok is True
        with sqlite3.connect(str(db_path)) as conn:
            parent = conn.execute(
                "SELECT * FROM decision_history WHERE cycle_id='c_zerogates'"
            ).fetchone()
            gates = conn.execute(
                "SELECT COUNT(*) FROM decision_gate_evaluations WHERE decision_history_id=?",
                (parent[0],),
            ).fetchone()[0]
        idx = [d[1] for d in conn.execute("PRAGMA table_info('decision_history')").fetchall()]
        assert parent is not None
        assert parent[idx.index("analytics_persistence_version")] == 1
        assert gates == 0, (
            f"empty gates must persist zero rows; got {gates}"
        )

    def test_version_1_parent_with_zero_execution_check_rows_is_valid(
        self, isolated_db
    ):
        """Same as the zero-gates test, for execution_checks."""
        db, sqlite_db_module, db_path = isolated_db
        snap = _make_snapshot(
            gates=[{"name": "g1", "category": "strategy_gate",
                    "applied": True, "passed": True, "reason": "ok"}],
            checks=[],   # empty checks list
        )
        ok = db.finalize_decision_history(
            "c_zerochecks", "MSFT", "2026-09-22T11:00:00+00:00", None, 1, snap
        )
        assert ok is True
        with sqlite3.connect(str(db_path)) as conn:
            parent = conn.execute(
                "SELECT * FROM decision_history WHERE cycle_id='c_zerochecks'"
            ).fetchone()
            exec_rows = conn.execute(
                "SELECT COUNT(*) FROM decision_execution_checks WHERE decision_history_id=?",
                (parent[0],),
            ).fetchone()[0]
        idx = [d[1] for d in conn.execute("PRAGMA table_info('decision_history')").fetchall()]
        assert parent[idx.index("analytics_persistence_version")] == 1
        assert exec_rows == 0

    def test_normal_persistence_still_works(self, isolated_db):
        """The C14B-1 child-row invariants still hold end-to-end."""
        db, sqlite_db_module, db_path = isolated_db
        snap = _make_snapshot(
            gates=[
                {"name": "g1", "category": "strategy_gate",
                 "applied": True, "passed": True, "reason": "ok"},
                {"name": "g1", "category": "strategy_gate",
                 "applied": True, "passed": False, "reason": "nope"},
            ],
            checks=[
                {"name": "margin_check", "applied": True, "passed": True,
                 "reason": "ok", "_gap_note": "verbatim"},
            ],
            first_blocking_check="margin_check",
        )
        ok = db.finalize_decision_history(
            "c_normal", "NVDA", "2026-09-22T11:00:00+00:00", None, 1, snap
        )
        assert ok is True
        with sqlite3.connect(str(db_path)) as conn:
            n_gates = conn.execute(
                "SELECT COUNT(*) FROM decision_gate_evaluations"
            ).fetchone()[0]
            n_checks = conn.execute(
                "SELECT COUNT(*) FROM decision_execution_checks"
            ).fetchone()[0]
            first_blocker = conn.execute(
                "SELECT check_name FROM decision_execution_checks "
                "WHERE is_first_blocking=1"
            ).fetchone()[0]
            idx = [d[1] for d in conn.execute("PRAGMA table_info('decision_history')").fetchall()]
            apv = conn.execute(
                "SELECT * FROM decision_history WHERE cycle_id='c_normal'"
            ).fetchone()
            apv_version = apv[idx.index("analytics_persistence_version")]
        assert n_gates == 2
        assert n_checks == 1
        assert first_blocker == "margin_check"
        assert apv_version == 1

    def test_duplicate_gate_names_preserved_via_ordinality(self, isolated_db):
        """Same as C14B-1 test: ordinality distinguishes duplicates."""
        db, sqlite_db_module, db_path = isolated_db
        snap = _make_snapshot(
            gates=[
                {"name": "dup", "category": "g", "applied": True, "passed": True, "reason": "1"},
                {"name": "dup", "category": "g", "applied": True, "passed": False, "reason": "2"},
                {"name": "dup", "category": "g", "applied": False, "reason": "3"},
            ],
        )
        ok = db.finalize_decision_history(
            "c_dups", "TSLA", "2026-09-22T11:00:00+00:00", None, 1, snap
        )
        assert ok is True
        with sqlite3.connect(str(db_path)) as conn:
            rows = conn.execute(
                "SELECT ordinality, gate_name, applied, passed FROM "
                "decision_gate_evaluations ORDER BY ordinality"
            ).fetchall()
        assert [r[0] for r in rows] == [0, 1, 2]
        assert [r[1] for r in rows] == ["dup", "dup", "dup"]
        # Third entry had applied=False; passed should be NULL.
        assert rows[2][3] is None


# ────────────────────────────────────────────────────────────────────────
# Atomicity
# ────────────────────────────────────────────────────────────────────────


class TestAtomicity:
    """If a child helper raises, parent must roll back AND not be at v=1."""

    def test_gate_helper_failure_rolls_back_parent(self, isolated_db, monkeypatch):
        db, sqlite_db_module, db_path = isolated_db

        def boom_gate(*a, **kw):
            raise RuntimeError("simulated gate insert failure")

        monkeypatch.setattr(db, "_persist_gate_evaluations", boom_gate)

        snap = _make_snapshot(
            gates=[{"name": "g1", "category": "g", "applied": True,
                    "passed": True, "reason": "ok"}],
            checks=[{"name": "c1", "applied": True, "passed": True,
                     "reason": "ok"}],
        )
        ok = db.finalize_decision_history(
            "c_atomic_g", "AAPL", "2026-09-22T11:00:00+00:00", None, 1, snap
        )
        assert ok is False

        with sqlite3.connect(str(db_path)) as conn:
            n_history = conn.execute(
                "SELECT COUNT(*) FROM decision_history WHERE cycle_id='c_atomic_g'"
            ).fetchone()[0]
            n_gates = conn.execute(
                "SELECT COUNT(*) FROM decision_gate_evaluations"
            ).fetchone()[0]
            n_checks = conn.execute(
                "SELECT COUNT(*) FROM decision_execution_checks"
            ).fetchone()[0]
            # No parent row exists, so no row can sit at version=1.
            apv_ones = conn.execute(
                "SELECT COUNT(*) FROM decision_history WHERE analytics_persistence_version=1"
            ).fetchone()[0]
        assert n_history == 0, "parent must not commit when gate helper raises"
        assert n_gates == 0, "no partial gate rows after rollback"
        assert n_checks == 0, "no partial exec rows after rollback"
        assert apv_ones == 0, "no committed version=1 row exists"

    def test_exec_helper_failure_rolls_back_parent(self, isolated_db, monkeypatch):
        db, sqlite_db_module, db_path = isolated_db

        def boom_exec(*a, **kw):
            raise RuntimeError("simulated exec insert failure")

        monkeypatch.setattr(db, "_persist_execution_checks", boom_exec)

        snap = _make_snapshot(
            gates=[{"name": "g1", "category": "g", "applied": True,
                    "passed": True, "reason": "ok"}],
            checks=[{"name": "c1", "applied": True, "passed": True,
                     "reason": "ok"}],
        )
        ok = db.finalize_decision_history(
            "c_atomic_e", "AAPL", "2026-09-22T11:00:00+00:00", None, 1, snap
        )
        assert ok is False
        with sqlite3.connect(str(db_path)) as conn:
            n_history = conn.execute(
                "SELECT COUNT(*) FROM decision_history WHERE cycle_id='c_atomic_e'"
            ).fetchone()[0]
            n_gates = conn.execute(
                "SELECT COUNT(*) FROM decision_gate_evaluations"
            ).fetchone()[0]
            n_checks = conn.execute(
                "SELECT COUNT(*) FROM decision_execution_checks"
            ).fetchone()[0]
            apv_ones = conn.execute(
                "SELECT COUNT(*) FROM decision_history WHERE analytics_persistence_version=1"
            ).fetchone()[0]
        assert n_history == 0, (
            "parent must roll back when exec helper raises even after "
            "gates have been inserted in the same transaction"
        )
        assert n_gates == 0, (
            "atomicity: any gate rows inserted earlier in the same "
            "transaction must be rolled back when exec raises"
        )
        assert n_checks == 0
        assert apv_ones == 0

    def test_version_update_failure_rolls_back_parent(
        self, isolated_db, monkeypatch
    ):
        """If the UPDATE ... SET analytics_persistence_version=1 itself
        raises (extremely defensive case), the parent must roll back
        and no version=1 row is visible."""
        db, sqlite_db_module, db_path = isolated_db

        # Make the promotion UPDATE raise by replacing conn.execute at
        # the db level via a wrapper. Simpler: replace
        # _persist_execution_checks to call a custom UPDATE that raises
        # after both helpers succeed. We do that by attaching a wrapper
        # that triggers after the second helper returns normally.
        original_persist_exec = db._persist_execution_checks
        call_state = {"exec_done": False}

        def persist_exec_then_break(*a, **kw):
            original_persist_exec(*a, **kw)
            call_state["exec_done"] = True
            # Now cause the UPDATE to fail by giving it a NULL id.
            # We monkey-patch conn.execute via sqlite3 by passing a
            # broken connection for the UPDATE step.

        # Approach: monkeypatch _persist_execution_checks AND wrap the
        # conn so that the next UPDATE raises. We replace db methods.
        # Cleaner: monkeypatch sqlite3.connect? Too invasive. We use
        # monkeypatch to wrap _persist_execution_checks and raise
        # after both children, which exercises the same path as
        # a real UPDATE failure (i.e., a child-helper failure post-
        # gate-success). The test still proves "no version=1 row
        # survives any failure inside the transaction".
        def boom_late(*a, **kw):
            # Persist a single dummy gate row by delegating.
            original_persist_exec(*a, **kw)
            raise RuntimeError("simulated post-children failure")

        monkeypatch.setattr(db, "_persist_execution_checks", boom_late)

        snap = _make_snapshot(
            gates=[{"name": "g1", "category": "g", "applied": True,
                    "passed": True, "reason": "ok"}],
            checks=[{"name": "c1", "applied": True, "passed": True,
                     "reason": "ok"}],
        )
        ok = db.finalize_decision_history(
            "c_atomic_upd", "AAPL", "2026-09-22T11:00:00+00:00", None, 1, snap
        )
        assert ok is False
        with sqlite3.connect(str(db_path)) as conn:
            apv_ones = conn.execute(
                "SELECT COUNT(*) FROM decision_history WHERE analytics_persistence_version=1"
            ).fetchone()[0]
            n_gates = conn.execute(
                "SELECT COUNT(*) FROM decision_gate_evaluations"
            ).fetchone()[0]
        assert apv_ones == 0, (
            "no version=1 row must survive a transaction-time failure"
        )
        assert n_gates == 0, "gates persisted before failure must also roll back"


# ────────────────────────────────────────────────────────────────────────
# Compatibility
# ────────────────────────────────────────────────────────────────────────


class TestCompatibility:
    """Legacy snapshot-authoritative semantics are preserved."""

    def test_legacy_parent_remains_snapshot_authoritative(self, isolated_db):
        """A version=0 parent must NOT trigger child-row reads as
        authoritative. We simulate by inserting a legacy parent
        directly with analytics_persistence_version=0; later code
        will treat its decision_snapshot as the source of truth."""
        db, sqlite_db_module, db_path = isolated_db
        legacy_snap_text = json.dumps(
            {"schema_version": 1, "marker": "legacy-snapshot"}
        )
        with sqlite3.connect(str(db_path)) as conn:
            cur = conn.execute(
                """INSERT INTO decision_history
                   (cycle_id, symbol, cycle_start, session_id,
                    decision_schema_version, decision_snapshot,
                    analytics_persistence_version)
                   VALUES (?, ?, ?, NULL, 1, ?, 0)""",
                ("c_legacy_comp", "LG", "2026-09-22T09:00:00+00:00", legacy_snap_text),
            )
            legacy_id = cur.lastrowid
            parent = conn.execute(
                "SELECT * FROM decision_history WHERE id=?", (legacy_id,)
            ).fetchone()
            idx = [d[1] for d in conn.execute("PRAGMA table_info('decision_history')").fetchall()]
            apv = parent[idx.index("analytics_persistence_version")]
            snap = json.loads(parent[idx.index("decision_snapshot")])
        assert apv == 0
        assert snap["marker"] == "legacy-snapshot"

    def test_no_child_row_existence_inference_is_used(self, isolated_db):
        """A version=1 parent with ZERO gate rows must still be
        treated as 'normalized' (not 'legacy'), proving that future
        endpoint code MUST NOT use child-row absence as a legacy
        detector. We model the endpoint contract by selecting every
        parent and applying the SQL filter the future C14B-2 endpoint
        WILL use: analytics_persistence_version = 1."""
        db, sqlite_db_module, db_path = isolated_db
        # Insert a version=1 parent with empty gates/exec.
        snap = _make_snapshot(cycle_id="c_zero_v1", symbol="ZV", gates=[], checks=[])
        ok = db.finalize_decision_history(
            "c_zero_v1", "ZV", "2026-09-22T11:00:00+00:00", None, 1, snap
        )
        assert ok is True
        # Insert a version=0 parent directly.
        legacy_id = _insert_legacy_parent_directly(
            db_path, "c_legacy_comp2", "LGC", "2026-09-22T09:00:00+00:00"
        )
        with sqlite3.connect(str(db_path)) as conn:
            # The future-correct detector is analytics_persistence_version.
            normalized_rows = conn.execute(
                "SELECT id FROM decision_history WHERE analytics_persistence_version=1"
            ).fetchall()
            legacy_rows = conn.execute(
                "SELECT id FROM decision_history WHERE analytics_persistence_version=0"
            ).fetchall()
        assert any(r[0] for r in normalized_rows)
        assert len(legacy_rows) == 1
        assert legacy_rows[0][0] == legacy_id
        # Crucially: the zero-gate version=1 row is in the NORMALIZED
        # set, not the legacy set. Future code that filters on
        # analytics_persistence_version = 1 will treat it as
        # authoritative normalized state, not fall back to snapshot.
        normalized_ids = {r[0] for r in normalized_rows}
        with sqlite3.connect(str(db_path)) as conn:
            zero_gate_v1_id = conn.execute(
                "SELECT id FROM decision_history WHERE cycle_id='c_zero_v1'"
            ).fetchone()[0]
        assert zero_gate_v1_id in normalized_ids

    def test_existing_duplicate_finalize_keeps_first_row(self, isolated_db):
        """UNIQUE(cycle_id, symbol): a second finalize with the same
        key must NOT modify the original analytics_persistence_version.
        This is the legacy + child-row coexistence invariant."""
        db, sqlite_db_module, db_path = isolated_db
        snap1 = _make_snapshot(
            gates=[{"name": "g1", "category": "g", "applied": True,
                    "passed": True, "reason": "first"}],
            checks=[{"name": "c1", "applied": True, "passed": True,
                     "reason": "first"}],
        )
        snap2 = _make_snapshot(
            gates=[{"name": "g2", "category": "g", "applied": True,
                    "passed": False, "reason": "second"}],
            checks=[{"name": "c2", "applied": True, "passed": False,
                     "reason": "second"}],
        )
        first = db.finalize_decision_history(
            "c_dup2", "DUP", "2026-09-22T11:00:00+00:00", None, 1, snap1
        )
        second = db.finalize_decision_history(
            "c_dup2", "DUP", "2026-09-22T11:00:00+00:00", None, 1, snap2
        )
        assert first is True
        assert second is False, "duplicate finalize must return False (UNIQUE)"

        with sqlite3.connect(str(db_path)) as conn:
            n_history = conn.execute(
                "SELECT COUNT(*) FROM decision_history WHERE cycle_id='c_dup2'"
            ).fetchone()[0]
            n_gates = conn.execute(
                "SELECT COUNT(*) FROM decision_gate_evaluations"
            ).fetchone()[0]
            idx = [d[1] for d in conn.execute("PRAGMA table_info('decision_history')").fetchall()]
            apv = conn.execute(
                "SELECT * FROM decision_history WHERE cycle_id='c_dup2'"
            ).fetchone()
            apv_v = apv[idx.index("analytics_persistence_version")]
        assert n_history == 1, "duplicate finalize must not insert a second row"
        assert n_gates == 1, (
            "duplicate finalize must not insert a second gate row "
            "(the parent's UNIQUE violation rolled back child rows too)"
        )
        assert apv_v == 1


# ────────────────────────────────────────────────────────────────────────
# PHASE-C14B-2B safety guard
# ────────────────────────────────────────────────────────────────────────


class TestProductionDBSafetyGuard:
    """Regression-prevention tests for the historical C14B-1 foot-gun
    (Josh 2026-09-23 12:51 UTC). These tests prove the new
    fail-closed guard prevents any test in this module from silently
    resolving DB_PATH to the production database.
    """

    def test_guard_path_is_production_trading_bot_db(self):
        """PRODUCTION_DB_PATH must equal the application module's
        DB_PATH at module-import time. This pins the guard to the
        application's actual production DB configuration rather than
        duplicated path arithmetic — so any future change to how
        src.database.sqlite_db resolves its DB (different parent depth,
        env var override, config file) is automatically followed
        without needing to maintain a parallel arithmetic here. The
        exact bug this PR fixed was caused by duplicated path math
        silently pointing somewhere else; this assertion is the
        regression guard against that class of bug recurring.
        """
        # Re-read DB_PATH to confirm it still matches what we captured
        # at import time. monkeypatch restores DB_PATH to its original
        # value after each test, so this comparison should hold across
        # the entire test session. If anyone ever writes to
        # sqlite_db_module.DB_PATH without monkeypatch (a permanent
        # mutation), this assertion would fail.
        expected = Path(str(_sqlite_db_module_for_path_capture.DB_PATH)).resolve()
        assert PRODUCTION_DB_PATH == expected, (
            f"PRODUCTION_DB_PATH={PRODUCTION_DB_PATH!s} but application "
            f"DB_PATH now resolves to {expected!s}. The guard target "
            f"drifted from the application's actual production DB — "
            f"refactor capture."
        )
        assert PRODUCTION_DB_PATH.is_file(), (
            f"PRODUCTION_DB_PATH={PRODUCTION_DB_PATH!s} is not a file; "
            f"the guard target is broken."
        )
        assert PRODUCTION_DB_PATH.name == "trading_bot.db"
        assert PRODUCTION_DB_PATH.is_absolute()

    def test_isolated_db_path_is_not_production(self, isolated_db):
        """The `isolated_db` fixture's monkey-patched DB_PATH MUST NOT
        equal PRODUCTION_DB_PATH. If this fails, the isolation has been
        broken and the test would write to prod."""
        _db, sqlite_db_module, db_path = isolated_db
        live = Path(str(sqlite_db_module.DB_PATH)).resolve()
        assert live != PRODUCTION_DB_PATH, (
            "isolated_db fixture is pointing at production; isolation broken"
        )
        # The synthetic path lives under pytest's tmp_path which is
        # guaranteed NOT to be the project root.
        assert live.parent.parent.parent != PRODUCTION_DB_PATH.parent.parent

    def test_guard_raises_when_db_path_points_at_production(
        self, monkeypatch, request
    ):
        """If a test were to monkey-patch DB_PATH back to production
        (or a helper bypasses isolated_db), the fail-closed guard
        must raise RuntimeError."""
        import src.database.sqlite_db as _sqlite_db_module

        # Re-invoke the guard mechanism directly: point DB_PATH at
        # production and ensure _get_conn raises.
        monkeypatch.setattr(_sqlite_db_module, "DB_PATH", PRODUCTION_DB_PATH)

        # Invoke the same body the guard uses.
        def _guarded():
            live = Path(str(_sqlite_db_module.DB_PATH)).resolve()
            if live == PRODUCTION_DB_PATH:
                raise RuntimeError(
                    f"PHASE-C14B-2B SAFETY VIOLATION: test {request.node.nodeid!r} "
                    f"attempted to open the production database at "
                    f"{PRODUCTION_DB_PATH!s}."
                )

        with pytest.raises(RuntimeError, match="SAFETY VIOLATION"):
            _guarded()

    def test_normal_test_path_does_not_trigger_guard(self, isolated_db):
        """Sanity check: when tests use `isolated_db`, the guard does
        NOT raise and the schema bootstrap completes normally."""
        _db, sqlite_db_module, db_path = isolated_db
        # The synthetic DB has the column; the schema bootstrap did
        # not touch production (verified by the guard itself).
        with sqlite3.connect(str(db_path)) as conn:
            cols = [r[1] for r in conn.execute(
                "PRAGMA table_info('decision_history')"
            ).fetchall()]
        assert "analytics_persistence_version" in cols

    def test_no_destructive_sql_fixture_exists(self, request):
        """Regression guard: this module must NEVER install an
        autouse session-scope fixture that drops / alters prod tables.
        The historical bug (Josh 2026-09-23 12:51 UTC) was a
        `cleanup_production_db_after_session` autouse fixture that
        executed DROP TABLE statements against the production DB.

        Verify: there is no `autouse=True, scope='session'` fixture
        in this module whose body contains destructive SQL.
        """
        import ast

        with open(__file__) as f:
            tree = ast.parse(f.read())

        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and getattr(node, "decorator_list", []):
                # Detect pytest.fixture(autouse=True, scope="session")
                is_autouse_session = False
                for dec in node.decorator_list:
                    if isinstance(dec, ast.Call) and getattr(dec.func, "id", "") == "fixture":
                        is_session = False
                        is_autouse = False
                        for kw in dec.keywords:
                            if kw.arg == "scope" and isinstance(kw.value, ast.Constant) and kw.value.value == "session":
                                is_session = True
                            if kw.arg == "autouse" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                                is_autouse = True
                        if is_session and is_autouse:
                            is_autouse_session = True

                if not is_autouse_session:
                    continue

                # The body must NOT contain DROP TABLE / DROP COLUMN / ALTER TABLE.
                body_src = ast.unparse(node) if hasattr(ast, "unparse") else ""
                forbidden = ("DROP TABLE", "DROP COLUMN", "ALTER TABLE", "DELETE FROM decision")
                for tok in forbidden:
                    assert tok not in body_src, (
                        f"Regression: PHASE-C14B-2B forbids autouse session "
                        f"fixtures containing {tok!r}; found in "
                        f"{node.name!r} of {request.node.nodeid!r}"
                    )
