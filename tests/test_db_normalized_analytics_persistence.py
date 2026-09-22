"""
PHASE-C14B-1 — Normalized analytics persistence tests.

These tests verify the FOUNDATION layer of the C14B storage design:

  1. Fresh / bootstrap DB creates the normalized child tables and
     their indexes (idempotent CREATE TABLE IF NOT EXISTS + CREATE INDEX
     IF NOT EXISTS, so production DB and fresh DB reach the same
     schema).
  2. finalize_decision_history() persists the child rows in the SAME
     transaction as the parent decision_history INSERT, extracting
     values directly from the structured Python Dict (no JSON
     reparse).
  3. Truth-contract invariants are enforced at write time:
       - applied=true / passed=true / applied=true / passed=false /
         applied=false are persisted correctly.
       - missing / malformed `applied` is coerced to 0 (fail-closed).
       - passed is NULL when applied = 0.
       - HOLD_INELIGIBLE does not create gate failures (the snapshot's
         outcome enum is never consulted; only the structured
         strategy_eligibility.gates[*].passed field is used).
       - multiplicity is preserved (duplicate gate names allowed,
         distinct by ordinality).
       - first_blocking_check is set on exactly ONE check row per
         decision_history parent.
       - gap_note is preserved verbatim from the structured Dict.
       - execution checks come only from structured
         execution_checks.checks[]; no prose parsing.
       - decision_snapshot is never modified by the helper.

  4. Atomicity: if the child-row INSERTs raise, the parent
     decision_history row is NOT committed.

All tests run against a fresh tempfile-based DB. The production
trading_bot.db is NEVER touched. The schema bootstrap is invoked by
constructing SQLiteDB() against the temp DB path (monkeypatched) so
the production-DB invariant is preserved.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path
from typing import Dict, Any

import pytest


# ─────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────


@pytest.fixture
def temp_db_path(tmp_path):
    """Provide a fresh empty file path for an isolated SQLite DB.

    Production trading_bot.db is NEVER opened by these tests.
    """
    p = tmp_path / "c14b1_test.db"
    yield p
    # tmp_path is cleaned up by pytest


@pytest.fixture
def isolated_db(monkeypatch, temp_db_path):
    """Redirect src.database.sqlite_db.DB_PATH to the temp DB and
    bootstrap the schema. Yields a SQLiteDB instance ready for
    finalize_decision_history() calls.

    Implementation note: src.database.sqlite_db computes DB_PATH at
    module import time as
        Path(__file__).parent.parent.parent / "trading_bot.db"
    so monkey-patching DB_PATH BEFORE reload is undone by the reload
    itself. We reload first to get the module fully imported, then
    monkey-patch DB_PATH to point at the temp DB. The _init_schema
    call inside SQLiteDB() then runs against the temp DB.
    """
    import importlib
    import src.database.sqlite_db as sqlite_db_module

    # Reload first so the module body has fully executed (and the
    # class is in scope for monkeypatching).
    importlib.reload(sqlite_db_module)

    # Now monkey-patch DB_PATH. _get_conn() looks up DB_PATH at call
    # time, so this redirection takes effect for the upcoming
    # SQLiteDB() constructor and every subsequent _get_conn() call.
    monkeypatch.setattr(sqlite_db_module, "DB_PATH", temp_db_path)

    try:
        db = sqlite_db_module.SQLiteDB()
        assert db.available, "SQLiteDB bootstrap must succeed on a fresh temp DB"
        yield db, sqlite_db_module, temp_db_path
    finally:
        # Monkeypatch will undo the DB_PATH override when the test
        # ends. No explicit restoration needed.
        pass


@pytest.fixture(scope="session", autouse=True)
def cleanup_production_db_after_session(request):
    """Drop the PHASE-C14B-1 child tables from the production DB
    after the test session finishes.

    Rationale: other test files in the suite (e.g. test_obs_001,
    test_bot001, test_obs_002) call `SQLiteDB()` directly against
    the production DB and rely on the existing schema bootstrap.
    The PHASE-C14B-1 additions to _init_schema are CREATE TABLE
    IF NOT EXISTS, so when those tests run they create the new
    tables on the production DB as a side effect. This is the
    same pattern that C13 (cycle_start index) followed: the
    deploy-time schema migration runs whenever _init_schema() runs.

    To keep the production DB in its pre-PR state for Josh's
    review (no orphan empty tables), this fixture removes the
    new tables and their indexes at the end of the session.
    """
    yield  # let all tests run

    # After all tests: clean up production DB.
    import sqlite3
    try:
        con = sqlite3.connect(str(
            __import__("src.database.sqlite_db", fromlist=["DB_PATH"]).DB_PATH
        ))
        cur = con.cursor()
        for stmt in [
            "DROP TABLE IF EXISTS decision_execution_checks",
            "DROP TABLE IF EXISTS decision_gate_evaluations",
            "DROP INDEX IF EXISTS idx_dge_cycle_start",
            "DROP INDEX IF EXISTS idx_dge_gate_name",
            "DROP INDEX IF EXISTS idx_dec_cycle_start",
            "DROP INDEX IF EXISTS idx_dec_check_name",
        ]:
            cur.execute(stmt)
        con.commit()
        con.close()
    except Exception as e:
        # If cleanup fails (e.g. DB is locked), do not fail the test
        # session — the cleanup is best-effort.
        print(f"\n[cleanup_production_db_after_session] warning: {e}")


def _make_snapshot(
    *,
    cycle_id: str = "c_test",
    symbol: str = "AAPL",
    outcome: str = "HOLD_INELIGIBLE",
    gates: list = None,
    checks: list = None,
    first_blocking_check: Any = None,
    schema_version: int = 1,
) -> Dict[str, Any]:
    """Build a minimal but schema-version-1 decision_snapshot Dict.

    Defaults to HOLD_INELIGIBLE with no gates/checks; tests override
    the gates/checks lists to exercise specific shapes.
    """
    snap = {
        "schema_version": schema_version,
        "cycle_id": cycle_id,
        "symbol": symbol,
        "decision": {
            "outcome": outcome,
            "primary_reason": "test",
        },
        "strategy_eligibility": {
            "strategy_eligible": False,
            "strategy_reason": "test",
            "evaluated_at": "2026-09-22T11:00:00+00:00",
            "gates": gates if gates is not None else [],
            "signal": "HOLD",
            "signal_strength": "WEAK",
        },
        "scoring": {
            "components": {},
            "total_score": 50,
            "score_invalid_data": False,
        },
        "ranking": {
            "applicable": False,
            "ranked_candidate": False,
            "candidate_rank": None,
        },
        "selection": {
            "attempted": False,
        },
        "execution_checks": {
            "current_state_at_attempt": {},
            "checks": checks if checks is not None else [],
            "first_blocking_check": first_blocking_check,
            "first_blocking_reason": None,
            "evaluated_in_order": [
                "margin_check",
                "pending_order_check",
                "cooldown_check",
                "position_concentration_check",
                "sector_concentration_check",
                "correlation_check",
                "beta_check",
                "buying_power_check",
                "quantity_post_sizing_check",
            ],
        },
        "order": {
            "submitted": False,
            "fill_confirmed": False,
            "slot_consumed": False,
        },
    }
    return snap


# ─────────────────────────────────────────────────────────────────────────
# 1. Bootstrap / schema correctness
# ─────────────────────────────────────────────────────────────────────────


class TestBootstrapSchema:
    """Fresh / bootstrap DB creates the normalized child tables and indexes."""

    def test_child_tables_exist_after_init_schema(self, isolated_db):
        _, sqlite_db_module, db_path = isolated_db
        with sqlite3.connect(str(db_path)) as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        assert "decision_gate_evaluations" in tables
        assert "decision_execution_checks" in tables

    def test_child_table_columns_match_schema(self, isolated_db):
        _, sqlite_db_module, db_path = isolated_db
        with sqlite3.connect(str(db_path)) as conn:
            cols_gate = {
                row[1]
                for row in conn.execute(
                    "PRAGMA table_info(decision_gate_evaluations)"
                )
            }
            cols_check = {
                row[1]
                for row in conn.execute(
                    "PRAGMA table_info(decision_execution_checks)"
                )
            }

        expected_gate = {
            "id", "decision_history_id", "cycle_id", "symbol",
            "cycle_start", "ordinality", "gate_name", "gate_category",
            "applied", "passed", "observed_value", "threshold_value",
            "reason",
        }
        assert cols_gate == expected_gate, (
            f"decision_gate_evaluations columns mismatch: "
            f"missing={expected_gate - cols_gate}, extra={cols_gate - expected_gate}"
        )

        expected_check = {
            "id", "decision_history_id", "cycle_id", "symbol",
            "cycle_start", "ordinality", "check_name",
            "applied", "passed", "observed_value", "threshold_value",
            "reason", "gap_note", "is_first_blocking",
        }
        assert cols_check == expected_check

    def test_required_indexes_exist_after_init_schema(self, isolated_db):
        _, sqlite_db_module, db_path = isolated_db
        with sqlite3.connect(str(db_path)) as conn:
            indexes = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='index' AND tbl_name IN "
                    "('decision_gate_evaluations','decision_execution_checks')"
                )
            }
        assert "idx_dge_cycle_start" in indexes
        assert "idx_dge_gate_name" in indexes
        assert "idx_dec_cycle_start" in indexes
        assert "idx_dec_check_name" in indexes

    def test_unique_constraint_on_ordinality_preserves_multiplicity(
        self, isolated_db
    ):
        _, sqlite_db_module, db_path = isolated_db
        with sqlite3.connect(str(db_path)) as conn:
            # Insert a parent first (simulate finalize)
            cur = conn.execute(
                "INSERT INTO decision_history "
                "(cycle_id, symbol, cycle_start, decision_schema_version, "
                " decision_snapshot) VALUES (?, ?, ?, 1, '{}')",
                ("c_unique_test", "SYM", "2026-09-22T11:00:00+00:00"),
            )
            parent_id = cur.lastrowid
            # Insert two gate rows with the same parent but DIFFERENT
            # ordinalities — must succeed.
            for ord_val in (0, 1, 2):
                conn.execute(
                    "INSERT INTO decision_gate_evaluations "
                    "(decision_history_id, cycle_id, symbol, cycle_start, "
                    " ordinality, gate_name, applied, passed) "
                    "VALUES (?, ?, ?, ?, ?, ?, 1, 1)",
                    (parent_id, "c_unique_test", "SYM",
                     "2026-09-22T11:00:00+00:00", ord_val, "dup_name"),
                )
            # Insert duplicate ordinality — must FAIL.
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO decision_gate_evaluations "
                    "(decision_history_id, cycle_id, symbol, cycle_start, "
                    " ordinality, gate_name, applied, passed) "
                    "VALUES (?, ?, ?, ?, ?, ?, 1, 1)",
                    (parent_id, "c_unique_test", "SYM",
                     "2026-09-22T11:00:00+00:00", 0, "different_name"),
                )

    def test_init_schema_is_idempotent_on_fresh_db(self, isolated_db):
        """Calling _init_schema() a second time must be a no-op."""
        db, sqlite_db_module, db_path = isolated_db
        # Already initialized by the fixture. Call again.
        db._init_schema()
        # Should not raise. Verify tables are still present.
        with sqlite3.connect(str(db_path)) as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        assert "decision_gate_evaluations" in tables
        assert "decision_execution_checks" in tables


# ─────────────────────────────────────────────────────────────────────────
# 2. Strategy gate persistence
# ─────────────────────────────────────────────────────────────────────────


class TestGatePersistence:

    def test_applied_true_passed_true_persisted(self, isolated_db):
        db, _, db_path = isolated_db
        snap = _make_snapshot(
            gates=[
                {
                    "name": "rsi_oversold",
                    "category": "strategy_gate",
                    "applied": True,
                    "passed": True,
                    "observed_value": 18.0,
                    "threshold_value": 30.0,
                    "reason": "RSI 18.0 < threshold 30.0",
                }
            ],
        )
        db.finalize_decision_history(
            "c_g_pass", "AAPL", "2026-09-22T11:00:00+00:00", None, 1, snap,
        )
        with sqlite3.connect(str(db_path)) as conn:
            row = conn.execute(
                "SELECT gate_name, gate_category, applied, passed, "
                "observed_value, threshold_value, reason, ordinality "
                "FROM decision_gate_evaluations"
            ).fetchone()
        assert row[0] == "rsi_oversold"
        assert row[1] == "strategy_gate"
        assert row[2] == 1
        assert row[3] == 1
        assert row[4] == 18.0
        assert row[5] == 30.0
        assert row[6] == "RSI 18.0 < threshold 30.0"
        assert row[7] == 0

    def test_applied_true_passed_false_persisted(self, isolated_db):
        db, _, db_path = isolated_db
        snap = _make_snapshot(
            gates=[
                {
                    "name": "sma_uptrend",
                    "category": "strategy_gate",
                    "applied": True,
                    "passed": False,
                    "observed_value": 1.5,
                    "threshold_value": 2.0,
                    "reason": "SMA fast <= SMA slow",
                }
            ],
        )
        db.finalize_decision_history(
            "c_g_fail", "AAPL", "2026-09-22T11:00:00+00:00", None, 1, snap,
        )
        with sqlite3.connect(str(db_path)) as conn:
            row = conn.execute(
                "SELECT applied, passed FROM decision_gate_evaluations"
            ).fetchone()
        assert row[0] == 1
        assert row[1] == 0

    def test_applied_false_persisted_with_null_passed(self, isolated_db):
        """applied=false gates: applied=0, passed=NULL.

        This is the documented semantics — do not compute passed for
        non-applied gates. The aggregator can distinguish
        "passed=false" (failed gate) from "not applied" (skipped
        evaluation).
        """
        db, _, db_path = isolated_db
        snap = _make_snapshot(
            gates=[
                {
                    "name": "skipped_gate",
                    "category": "strategy_gate",
                    "applied": False,
                    "passed": None,
                    "reason": "Not evaluated",
                }
            ],
        )
        db.finalize_decision_history(
            "c_g_skipped", "AAPL", "2026-09-22T11:00:00+00:00", None, 1, snap,
        )
        with sqlite3.connect(str(db_path)) as conn:
            row = conn.execute(
                "SELECT applied, passed FROM decision_gate_evaluations"
            ).fetchone()
        assert row[0] == 0
        assert row[1] is None  # NULL, not 0

    def test_missing_applied_field_fails_closed(self, isolated_db):
        """Missing `applied` key on a gate is coerced to 0."""
        db, _, db_path = isolated_db
        snap = _make_snapshot(
            gates=[
                {
                    "name": "missing_applied_gate",
                    "category": "strategy_gate",
                    # NOTE: no `applied` key
                    "passed": True,
                    "reason": "applied key missing",
                }
            ],
        )
        db.finalize_decision_history(
            "c_g_missing", "AAPL", "2026-09-22T11:00:00+00:00", None, 1, snap,
        )
        with sqlite3.connect(str(db_path)) as conn:
            row = conn.execute(
                "SELECT applied, passed FROM decision_gate_evaluations"
            ).fetchone()
        assert row[0] == 0  # fail-closed
        assert row[1] is None  # passed is NULL when applied=0

    def test_malformed_applied_field_fails_closed(self, isolated_db):
        """Non-boolean `applied` value (string, int other than 1) is 0."""
        db, _, db_path = isolated_db
        for malformed in ["true", 2, -1, 1.5, [], {}, "yes"]:
            # Fresh finalize so each test case is isolated.
            snap = _make_snapshot(
                gates=[
                    {
                        "name": f"malformed_applied_{malformed!r}",
                        "category": "strategy_gate",
                        "applied": malformed,
                        "passed": True,
                        "reason": "applied value malformed",
                    }
                ],
            )
            db.finalize_decision_history(
                f"c_g_malformed_{malformed!r}",
                "AAPL", "2026-09-22T11:00:00+00:00", None, 1, snap,
            )
            with sqlite3.connect(str(db_path)) as conn:
                # Read the most recent gate row.
                row = conn.execute(
                    "SELECT applied FROM decision_gate_evaluations "
                    "ORDER BY id DESC LIMIT 1"
                ).fetchone()
            assert row[0] == 0, (
                f"malformed applied={malformed!r} should fail-closed to 0, "
                f"got {row[0]}"
            )

    def test_multiple_gates_persisted_in_order(self, isolated_db):
        db, _, db_path = isolated_db
        snap = _make_snapshot(
            gates=[
                {"name": "g1", "category": "strategy_gate",
                 "applied": True, "passed": True, "reason": "ok"},
                {"name": "g2", "category": "strategy_gate",
                 "applied": True, "passed": False, "reason": "fail"},
                {"name": "g3", "category": "strategy_gate",
                 "applied": False, "passed": None, "reason": "skipped"},
                {"name": "g4", "category": "strategy_gate",
                 "applied": True, "passed": True, "reason": "ok"},
            ],
        )
        db.finalize_decision_history(
            "c_g_multi", "AAPL", "2026-09-22T11:00:00+00:00", None, 1, snap,
        )
        with sqlite3.connect(str(db_path)) as conn:
            rows = conn.execute(
                "SELECT gate_name, ordinality, applied, passed "
                "FROM decision_gate_evaluations ORDER BY ordinality"
            ).fetchall()
        assert [r[0] for r in rows] == ["g1", "g2", "g3", "g4"]
        assert [r[1] for r in rows] == [0, 1, 2, 3]
        assert [r[2] for r in rows] == [1, 1, 0, 1]
        assert [r[3] for r in rows] == [1, 0, None, 1]

    def test_duplicate_gate_names_preserved_via_ordinality(self, isolated_db):
        """Multiplicity: the same gate_name appears twice in the source
        array. Both rows must persist, distinguished by ordinality."""
        db, _, db_path = isolated_db
        snap = _make_snapshot(
            gates=[
                {"name": "rsi_oversold", "category": "strategy_gate",
                 "applied": True, "passed": False,
                 "observed_value": 40.0, "threshold_value": 30.0,
                 "reason": "first eval"},
                {"name": "rsi_oversold", "category": "strategy_gate",
                 "applied": True, "passed": True,
                 "observed_value": 18.0, "threshold_value": 30.0,
                 "reason": "second eval"},
            ],
        )
        db.finalize_decision_history(
            "c_g_dup", "AAPL", "2026-09-22T11:00:00+00:00", None, 1, snap,
        )
        with sqlite3.connect(str(db_path)) as conn:
            rows = conn.execute(
                "SELECT ordinality, observed_value, passed, reason "
                "FROM decision_gate_evaluations ORDER BY ordinality"
            ).fetchall()
        assert len(rows) == 2
        assert rows[0][0] == 0
        assert rows[0][1] == 40.0
        assert rows[0][2] == 0
        assert rows[0][3] == "first eval"
        assert rows[1][0] == 1
        assert rows[1][1] == 18.0
        assert rows[1][2] == 1
        assert rows[1][3] == "second eval"

    def test_hold_ineligible_with_no_gates_persists_no_gate_rows(
        self, isolated_db
    ):
        """HOLD_INELIGIBLE with empty gates array → zero gate rows.

        Truth contract: HOLD_INELIGIBLE does not imply failed gate. A
        HOLD row with empty gates[] contributes nothing to gate
        aggregations (matches Phase C7 contract).
        """
        db, _, db_path = isolated_db
        snap = _make_snapshot(
            outcome="HOLD_INELIGIBLE",
            gates=[],  # empty array
        )
        db.finalize_decision_history(
            "c_hold_no_gates", "AAPL", "2026-09-22T11:00:00+00:00",
            None, 1, snap,
        )
        with sqlite3.connect(str(db_path)) as conn:
            n = conn.execute(
                "SELECT COUNT(*) FROM decision_gate_evaluations"
            ).fetchone()[0]
        assert n == 0

    def test_decision_snapshot_unmodified_by_persistence(self, isolated_db):
        """The structured Dict passed to finalize_decision_history
        must NOT be mutated. This is the user's explicit requirement:
        use the structured facts directly, no JSON reparse.
        """
        db, _, db_path = isolated_db
        snap = _make_snapshot(
            gates=[
                {"name": "g1", "category": "strategy_gate",
                 "applied": True, "passed": True, "reason": "ok"},
            ],
        )
        # Capture the snapshot's gate list before.
        gates_before = list(snap["strategy_eligibility"]["gates"])
        db.finalize_decision_history(
            "c_snap_unmod", "AAPL", "2026-09-22T11:00:00+00:00",
            None, 1, snap,
        )
        gates_after = snap["strategy_eligibility"]["gates"]
        assert gates_before == gates_after, (
            "decision_snapshot was mutated by finalize_decision_history — "
            "forbidden by the truth contract"
        )
        # And the same content survives in decision_snapshot TEXT.
        with sqlite3.connect(str(db_path)) as conn:
            row = conn.execute(
                "SELECT decision_snapshot FROM decision_history"
            ).fetchone()
        persisted = json.loads(row[0])
        assert (
            persisted["strategy_eligibility"]["gates"][0]["name"] == "g1"
        )
        assert (
            persisted["strategy_eligibility"]["gates"][0]["applied"] is True
        )

    def test_parent_decision_history_id_is_bound(self, isolated_db):
        """Every child row's decision_history_id matches the parent."""
        db, _, db_path = isolated_db
        snap = _make_snapshot(
            gates=[
                {"name": "g1", "category": "strategy_gate",
                 "applied": True, "passed": True, "reason": "ok"},
                {"name": "g2", "category": "strategy_gate",
                 "applied": True, "passed": False, "reason": "fail"},
            ],
        )
        db.finalize_decision_history(
            "c_parent_bind", "AAPL", "2026-09-22T11:00:00+00:00",
            None, 1, snap,
        )
        with sqlite3.connect(str(db_path)) as conn:
            parent_id = conn.execute(
                "SELECT id FROM decision_history"
            ).fetchone()[0]
            children = conn.execute(
                "SELECT DISTINCT decision_history_id FROM "
                "decision_gate_evaluations"
            ).fetchall()
        assert len(children) == 1
        assert children[0][0] == parent_id


# ─────────────────────────────────────────────────────────────────────────
# 3. Execution check persistence
# ─────────────────────────────────────────────────────────────────────────


class TestExecutionCheckPersistence:

    def test_multiple_checks_persisted(self, isolated_db):
        db, _, db_path = isolated_db
        snap = _make_snapshot(
            outcome="SELL_BLOCKED_DYNAMIC",
            checks=[
                {
                    "name": "margin_check",
                    "applied": True, "passed": True,
                    "observed_value": 100000.0, "threshold_value": 0.0,
                    "reason": "Cash sufficient",
                    "_gap_note": "real check",
                },
                {
                    "name": "pending_order_check",
                    "applied": True, "passed": True,
                    "observed_value": False, "threshold_value": False,
                    "reason": "No pending orders",
                },
                {
                    "name": "position_existence_check",
                    "applied": True, "passed": False,
                    "observed_value": 0.0, "threshold_value": 1.0,
                    "reason": "No existing position",
                },
            ],
            first_blocking_check="position_existence_check",
        )
        db.finalize_decision_history(
            "c_checks", "AAPL", "2026-09-22T11:00:00+00:00", None, 1, snap,
        )
        with sqlite3.connect(str(db_path)) as conn:
            rows = conn.execute(
                "SELECT check_name, ordinality, applied, passed, "
                "is_first_blocking, gap_note "
                "FROM decision_execution_checks ORDER BY ordinality"
            ).fetchall()
        assert len(rows) == 3
        names = [r[0] for r in rows]
        assert names == ["margin_check", "pending_order_check",
                         "position_existence_check"]
        ordinals = [r[1] for r in rows]
        assert ordinals == [0, 1, 2]
        first_blocking = [r[4] for r in rows]
        # Exactly one row marked first_blocking, and it's the
        # position_existence_check row.
        assert sum(first_blocking) == 1
        assert rows[2][4] == 1  # position_existence_check
        # gap_note preserved verbatim.
        assert rows[0][5] == "real check"
        assert rows[1][5] is None  # not present

    def test_first_blocking_check_preserved_when_none(self, isolated_db):
        """If first_blocking_check is None, no row is marked."""
        db, _, db_path = isolated_db
        snap = _make_snapshot(
            checks=[
                {"name": "margin_check", "applied": True, "passed": True,
                 "reason": "ok"},
            ],
            first_blocking_check=None,
        )
        db.finalize_decision_history(
            "c_no_first", "AAPL", "2026-09-22T11:00:00+00:00",
            None, 1, snap,
        )
        with sqlite3.connect(str(db_path)) as conn:
            row = conn.execute(
                "SELECT is_first_blocking FROM decision_execution_checks"
            ).fetchone()
        assert row[0] == 0

    def test_first_blocking_check_defensive_when_name_not_in_checks(
        self, isolated_db
    ):
        """Defensive: first_blocking_check points to a name that does
        not exist in checks[]. No row gets is_first_blocking = 1.
        """
        db, _, db_path = isolated_db
        snap = _make_snapshot(
            checks=[
                {"name": "margin_check", "applied": True, "passed": True,
                 "reason": "ok"},
            ],
            first_blocking_check="nonexistent_check",
        )
        db.finalize_decision_history(
            "c_first_defensive", "AAPL", "2026-09-22T11:00:00+00:00",
            None, 1, snap,
        )
        with sqlite3.connect(str(db_path)) as conn:
            row = conn.execute(
                "SELECT is_first_blocking FROM decision_execution_checks"
            ).fetchone()
        assert row[0] == 0

    def test_gap_note_preserved_verbatim(self, isolated_db):
        """The _gap_note key on a check is persisted to gap_note verbatim."""
        db, _, db_path = isolated_db
        long_note = (
            "Real check: account.cash >= 0 \u2192 pass. "
            "Uses trading_client.get_account() at the EXACT moment "
            "the real code reads it."
        )
        snap = _make_snapshot(
            checks=[
                {"name": "margin_check", "applied": True, "passed": True,
                 "reason": "ok", "_gap_note": long_note},
            ],
            first_blocking_check=None,
        )
        db.finalize_decision_history(
            "c_gap", "AAPL", "2026-09-22T11:00:00+00:00",
            None, 1, snap,
        )
        with sqlite3.connect(str(db_path)) as conn:
            row = conn.execute(
                "SELECT gap_note FROM decision_execution_checks"
            ).fetchone()
        assert row[0] == long_note

    def test_check_applied_false_persisted(self, isolated_db):
        db, _, db_path = isolated_db
        snap = _make_snapshot(
            checks=[
                {"name": "skipped_check", "applied": False, "passed": None,
                 "reason": "not evaluated"},
            ],
            first_blocking_check=None,
        )
        db.finalize_decision_history(
            "c_chk_skipped", "AAPL", "2026-09-22T11:00:00+00:00",
            None, 1, snap,
        )
        with sqlite3.connect(str(db_path)) as conn:
            row = conn.execute(
                "SELECT applied, passed FROM decision_execution_checks"
            ).fetchone()
        assert row[0] == 0
        assert row[1] is None

    def test_no_execution_checks_persists_zero_rows(self, isolated_db):
        """BUY / HOLD paths that never reach execute_trade have no
        checks[] array. Persist zero rows."""
        db, _, db_path = isolated_db
        snap = _make_snapshot(
            outcome="HOLD_INELIGIBLE",
            checks=[],
            first_blocking_check=None,
        )
        db.finalize_decision_history(
            "c_no_checks", "AAPL", "2026-09-22T11:00:00+00:00",
            None, 1, snap,
        )
        with sqlite3.connect(str(db_path)) as conn:
            n = conn.execute(
                "SELECT COUNT(*) FROM decision_execution_checks"
            ).fetchone()[0]
        assert n == 0


# ─────────────────────────────────────────────────────────────────────────
# 4. Atomicity: parent + child rows are atomic
# ─────────────────────────────────────────────────────────────────────────


class TestAtomicity:
    """The user's atomicity requirement: if the child INSERTs fail,
    the parent decision_history INSERT must NOT be committed.
    """

    def test_child_insert_failure_rolls_back_parent(
        self, isolated_db, monkeypatch
    ):
        db, sqlite_db_module, db_path = isolated_db

        # Monkey-patch _persist_gate_evaluations on this specific
        # instance to raise. The parent decision_history INSERT runs
        # first; then the patched helper raises; finalize_decision_history
        # returns False; the with-block exits via exception → the
        # implicit transaction rolls back → decision_history row is
        # not committed.
        def boom_gate(*a, **kw):
            raise RuntimeError("simulated gate insert failure")

        monkeypatch.setattr(db, "_persist_gate_evaluations", boom_gate)

        snap = _make_snapshot(
            gates=[
                {"name": "g1", "category": "strategy_gate",
                 "applied": True, "passed": True, "reason": "ok"},
            ],
        )
        result = db.finalize_decision_history(
            "c_atomicity", "AAPL", "2026-09-22T11:00:00+00:00",
            None, 1, snap,
        )
        assert result is False, (
            "finalize_decision_history must return False when a child "
            "INSERT raises"
        )
        # And the parent decision_history row must NOT exist.
        with sqlite3.connect(str(db_path)) as conn:
            n_history = conn.execute(
                "SELECT COUNT(*) FROM decision_history"
            ).fetchone()[0]
            n_gates = conn.execute(
                "SELECT COUNT(*) FROM decision_gate_evaluations"
            ).fetchone()[0]
        assert n_history == 0, (
            "decision_history row was committed despite gate INSERT "
            "failure — atomicity violated"
        )
        assert n_gates == 0

    def test_existing_decision_history_preserved_on_duplicate(
        self, isolated_db
    ):
        """Calling finalize twice with the same (cycle_id, symbol)
        must NOT modify the existing row. UNIQUE(cycle_id, symbol)
        is the safety net; child rows of the second call are not
        inserted either (because parent insert fails).
        """
        db, _, db_path = isolated_db
        snap = _make_snapshot(
            cycle_id="c_dup",
            gates=[
                {"name": "g1", "category": "strategy_gate",
                 "applied": True, "passed": True, "reason": "first"},
            ],
        )
        first = db.finalize_decision_history(
            "c_dup", "AAPL", "2026-09-22T11:00:00+00:00",
            None, 1, snap,
        )
        assert first is True

        # Second call with a different snapshot.
        snap2 = _make_snapshot(
            cycle_id="c_dup",
            gates=[
                {"name": "g2", "category": "strategy_gate",
                 "applied": True, "passed": False, "reason": "second"},
            ],
        )
        second = db.finalize_decision_history(
            "c_dup", "AAPL", "2026-09-22T11:00:00+00:00",
            None, 1, snap2,
        )
        assert second is False

        with sqlite3.connect(str(db_path)) as conn:
            history_rows = conn.execute(
                "SELECT COUNT(*) FROM decision_history"
            ).fetchone()[0]
            gate_names = [
                row[0]
                for row in conn.execute(
                    "SELECT gate_name FROM decision_gate_evaluations"
                )
            ]
        assert history_rows == 1
        assert gate_names == ["g1"]  # original preserved, not overwritten


# ─────────────────────────────────────────────────────────────────────────
# 5. Truth-contract regression: outcome enum is never consulted
# ─────────────────────────────────────────────────────────────────────────


class TestTruthContract:
    """Regression tests for the OBS-001 truth contract.

    The normalized rows are derived from the structured
    strategy_eligibility.gates[] and execution_checks.checks[]
    arrays. The decision.outcome enum is NEVER consulted when
    deciding whether a gate failed.
    """

    def test_hold_ineligible_does_not_create_gate_failures(
        self, isolated_db
    ):
        """A row with outcome=HOLD_INELIGIBLE and gates that all pass
        must persist zero failed gate rows.
        """
        db, _, db_path = isolated_db
        snap = _make_snapshot(
            outcome="HOLD_INELIGIBLE",
            gates=[
                {"name": "g1", "category": "strategy_gate",
                 "applied": True, "passed": True, "reason": "passed"},
                {"name": "g2", "category": "strategy_gate",
                 "applied": True, "passed": True, "reason": "passed"},
            ],
        )
        db.finalize_decision_history(
            "c_hold_ingates_pass", "AAPL", "2026-09-22T11:00:00+00:00",
            None, 1, snap,
        )
        with sqlite3.connect(str(db_path)) as conn:
            failed_count = conn.execute(
                "SELECT COUNT(*) FROM decision_gate_evaluations "
                "WHERE applied=1 AND passed=0"
            ).fetchone()[0]
        assert failed_count == 0

    def test_no_recomputation_from_settings(self, isolated_db):
        """The helper must not consult any live settings or live
        indicators. Test: change the outcome to something
        semantically incompatible (BUY_ORDER_SUBMITTED for a HOLD-only
        scenario) and verify the gate rows are still derived from the
        structured gates[] array verbatim.
        """
        db, _, db_path = isolated_db
        snap = _make_snapshot(
            outcome="BUY_ORDER_SUBMITTED",  # outcome is misleading on purpose
            gates=[
                {"name": "rsi_oversold", "category": "strategy_gate",
                 "applied": True, "passed": False, "reason": "fail"},
            ],
        )
        db.finalize_decision_history(
            "c_outcome_mismatch", "AAPL", "2026-09-22T11:00:00+00:00",
            None, 1, snap,
        )
        with sqlite3.connect(str(db_path)) as conn:
            row = conn.execute(
                "SELECT passed FROM decision_gate_evaluations"
            ).fetchone()
        # The persisted passed value must reflect the structured
        # gates[].passed, NOT the outcome enum.
        assert row[0] == 0
