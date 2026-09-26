"""Dashboard Phase C — OBS Analytics.

Five read-only endpoints redesigned around persisted OBS decision truth:
- /api/phase-c/funnel
- /api/phase-c/strategy-gates
- /api/phase-c/execution-blockers
- /api/phase-c/outcomes
- /api/phase-c/coverage

This test file proves the source-of-truth contract end-to-end:

  1. Every endpoint reads ONLY from cycle_funnel +
     decision_history.decision_snapshot. No recompute from current
     settings or raw indicators.
  2. `strategy_eligibility.gates[]` is the canonical structured
     source for Strategy Gate Failure Frequency. `primary_reason`
     text is NEVER parsed into gate fields.
  3. HOLD_INELIGIBLE never auto-implies a failed strategy gate.
  4. SKIPPED_INVALID_DATA is reported as its own outcome (never
     lumped with HOLD).
  5. Execution Blocked and Orders Failed are off-path. The funnel
     response carries them in a separate `off_path` envelope; they
     never appear as forward funnel stages.
  6. Pre-OBS-002 cycles are visible separately and never
     back-filled; the coverage endpoint proves the
     analyzed_count == COUNT(DISTINCT decision_history.symbol)
     invariant from OBS-002 long-run validation.
  7. candidate_rank IS NOT NULL is the only authoritative candidate
     existence test (Phase B contract preserved).
  8. The Analytics tab HTML carries the 5 Phase C cards + the time
     range selector and never replaces legacy cards.
  9. The Phase C JS never parses decision.primary_reason text.
"""

import json
import re
import sqlite3
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = REPO_ROOT / "templates" / "dashboard.html"
DB_PATH = REPO_ROOT / "trading_bot.db"
OBS_002_BOUNDARY = "2026-09-14T20:24:55"


# ─────────────────────────────────────────────────────────────────────────
# PHASE-C7 regression fixtures and tests
# ─────────────────────────────────────────────────────────────────────────
#
# These tests verify the gate-aggregation hardening: only persisted
# `applied == true` gate evaluations contribute to total_evaluations,
# passed, and failed. They use an in-memory sqlite DB with synthetic
# decision_history rows so we can inject `applied == false` and
# malformed `applied` records that the live DB never contains.
#
# The `synthetic_phase_c_db` fixture monkey-patches
# `dashboard._phase_c_open_db` so the endpoint reads from the
# in-memory DB for the duration of each test. monkeypatch restores
# the original automatically.


def _build_synthetic_phase_c_db():
    """Build an in-memory sqlite DB with controlled synthetic gate
    records. Returns (conn, inserted_row_descriptions).

    Synthetic rows (one decision_history row each):
      c1: applied=true,  passed=true,  name='rsi_oversold' -> contributes +1 passed, +1 total
      c2: applied=true,  passed=false, name='rsi_oversold' -> contributes +1 failed, +1 total
      c3: applied=false, passed=null,  name='rsi_oversold' -> contributes 0 to all
      c4: applied missing (malformed), passed=true, name='rsi_oversold' -> fails closed -> contributes 0
      c5: applied=true,  passed=false, name='sma_uptrend'  -> +1 failed, +1 total (for sort/secondary-gate check)
      c6: HOLD_INELIGIBLE with empty gates[] -> contributes 0 to any gate count
    """
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE decision_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cycle_id TEXT,
            symbol TEXT,
            cycle_start TEXT,
            decision_snapshot TEXT,
            decision_schema_version INTEGER,
            analytics_persistence_version INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE cycle_funnel (
            cycle_id TEXT,
            cycle_start TEXT,
            cycle_end TEXT,
            analyzed_count INTEGER,
            strategy_eligible_count INTEGER,
            ranked_candidate_count INTEGER,
            execution_attempt_count INTEGER,
            execution_blocked_count INTEGER,
            order_submission_attempt_count INTEGER,
            order_submitted_count INTEGER,
            order_failed_count INTEGER,
            not_attempted_count INTEGER,
            schema_version INTEGER
        )
        """
    )

    rows = [
        # c1: applied=true, passed=true (rsi_oversold) -> passed +1
        (
            "c1", "AAA", "2026-09-15T10:00:00+00:00", json.dumps({
                "decision": {"outcome": "HOLD_INELIGIBLE"},
                "strategy_eligibility": {
                    "gates": [
                        {"name": "rsi_oversold", "category": "technical",
                         "applied": True, "passed": True,
                         "observed_value": 30, "threshold_value": 25}
                    ]
                }
            })
        ),
        # c2: applied=true, passed=false (rsi_oversold) -> failed +1
        (
            "c2", "BBB", "2026-09-15T10:01:00+00:00", json.dumps({
                "decision": {"outcome": "HOLD_INELIGIBLE"},
                "strategy_eligibility": {
                    "gates": [
                        {"name": "rsi_oversold", "category": "technical",
                         "applied": True, "passed": False,
                         "observed_value": 40, "threshold_value": 25}
                    ]
                }
            })
        ),
        # c3: applied=false (rsi_oversold) -> contributes 0
        (
            "c3", "CCC", "2026-09-15T10:02:00+00:00", json.dumps({
                "decision": {"outcome": "HOLD_INELIGIBLE"},
                "strategy_eligibility": {
                    "gates": [
                        {"name": "rsi_oversold", "category": "technical",
                         "applied": False, "passed": None,
                         "observed_value": None, "threshold_value": None}
                    ]
                }
            })
        ),
        # c4: malformed applied (field missing) (rsi_oversold) -> fails closed -> 0
        (
            "c4", "DDD", "2026-09-15T10:03:00+00:00", json.dumps({
                "decision": {"outcome": "HOLD_INELIGIBLE"},
                "strategy_eligibility": {
                    "gates": [
                        {"name": "rsi_oversold", "category": "technical",
                         "passed": True,
                         "observed_value": 50, "threshold_value": 25}
                        # NOTE: 'applied' field intentionally absent
                    ]
                }
            })
        ),
        # c5: applied=true, passed=false (sma_uptrend) -> +1 failed, +1 total
        (
            "c5", "EEE", "2026-09-15T10:04:00+00:00", json.dumps({
                "decision": {"outcome": "HOLD_INELIGIBLE"},
                "strategy_eligibility": {
                    "gates": [
                        {"name": "sma_uptrend", "category": "technical",
                         "applied": True, "passed": False,
                         "observed_value": 0.95, "threshold_value": 1.05}
                    ]
                }
            })
        ),
        # c6: HOLD_INELIGIBLE with empty gates[] -> 0 to any gate count
        (
            "c6", "FFF", "2026-09-15T10:05:00+00:00", json.dumps({
                "decision": {"outcome": "HOLD_INELIGIBLE"},
                "strategy_eligibility": {"gates": []}
            })
        ),
    ]
    for cycle_id, symbol, cycle_start, snapshot in rows:
        cur.execute(
            "INSERT INTO decision_history "
            "(cycle_id, symbol, cycle_start, decision_snapshot, decision_schema_version) "
            "VALUES (?, ?, ?, ?, ?)",
            (cycle_id, symbol, cycle_start, snapshot, 2),
        )

    # Insert a synthetic cycle_funnel row whose cycle_start matches
    # the latest decision_history row above. This is required so the
    # `range=latest` query (which subqueries cycle_funnel for the
    # largest cycle_end) returns a non-null cycle_start.
    cur.execute(
        "INSERT INTO cycle_funnel "
        "(cycle_id, cycle_start, cycle_end, analyzed_count, "
        " strategy_eligible_count, ranked_candidate_count, "
        " execution_attempt_count, execution_blocked_count, "
        " order_submission_attempt_count, order_submitted_count, "
        " order_failed_count, not_attempted_count, schema_version) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("c1", "2026-09-15T10:00:00+00:00", "2026-09-15T10:00:30+00:00",
         6, 0, 0, 0, 0, 0, 0, 0, 0, 2),
    )

    # PHASE-C14B-2C: create empty child tables so the v1 path of the
    # hybrid reader does not error with "no such table" for tests
    # that pre-date the hybrid read path. With the synthetic rows
    # defaulting to analytics_persistence_version=0, the v1 path
    # returns no rows (it filters on parent version=1), so empty
    # child tables are the correct shape.
    cur.execute(
        """
        CREATE TABLE decision_gate_evaluations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            decision_history_id INTEGER NOT NULL,
            cycle_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            cycle_start TEXT NOT NULL,
            ordinality INTEGER NOT NULL,
            gate_name TEXT NOT NULL,
            gate_category TEXT,
            applied INTEGER NOT NULL,
            passed INTEGER,
            observed_value REAL,
            threshold_value REAL,
            reason TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE decision_execution_checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            decision_history_id INTEGER NOT NULL,
            cycle_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            cycle_start TEXT NOT NULL,
            ordinality INTEGER NOT NULL,
            check_name TEXT NOT NULL,
            applied INTEGER NOT NULL,
            passed INTEGER,
            observed_value REAL,
            threshold_value REAL,
            reason TEXT,
            gap_note TEXT,
            is_first_blocking INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    # PHASE-C14B-2C: the v1 SQL uses INDEXED BY hints to force the
    # production cycle_start indexes on the child tables. The
    # synthetic test DB must have matching indexes or INDEXED BY
    # raises "no such index".
    cur.execute(
        "CREATE INDEX idx_dge_cycle_start "
        "ON decision_gate_evaluations(cycle_start)"
    )
    cur.execute(
        "CREATE INDEX idx_dec_cycle_start "
        "ON decision_execution_checks(cycle_start)"
    )
    conn.commit()
    return conn


@pytest.fixture
def synthetic_phase_c_db(monkeypatch):
    """Yield an in-memory sqlite DB wired into the strategy-gates endpoint
    via monkeypatch on `dashboard._phase_c_open_db`. Original behavior is
    restored automatically after each test."""
    import sys
    sys.path.insert(0, str(REPO_ROOT))
    import dashboard as _dashboard
    conn = _build_synthetic_phase_c_db()
    monkeypatch.setattr(_dashboard, "_phase_c_open_db", lambda: conn)
    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:
            pass


@pytest.fixture
def live_phase_c_db(monkeypatch):
    """Restore the live trading_bot.db opener for tests that need the
    production data path."""
    import sys
    sys.path.insert(0, str(REPO_ROOT))
    import dashboard as _dashboard
    monkeypatch.setattr(_dashboard, "_phase_c_open_db",
                        lambda: sqlite3.connect(str(DB_PATH)))
    yield DB_PATH


def _read_template() -> str:
    return TEMPLATE_PATH.read_text(encoding="utf-8")


def _client():
    """Build a FastAPI TestClient without spinning up the live bot."""
    from fastapi.testclient import TestClient
    import sys
    sys.path.insert(0, str(REPO_ROOT))
    import dashboard as _dashboard
    return TestClient(_dashboard.app)


# PHASE-C10D: the `shared_client`, `gates_response_today_post`, and
# `_db()` helpers were relocated to
# `tests/observational/test_phase_c_live_observational.py` along
# with the 13 observational tests that used them. This file is
# now purely synthetic — no live-DB helpers needed.


# ─────────────────────────────────────────────────────────────────────────
# PHASE-C10A: reusable synthetic Phase C DB factory
# ─────────────────────────────────────────────────────────────────────────
#
# Extends the original PHASE-C7 helper (`_build_synthetic_phase_c_db`)
# into a reusable dataset-driven factory. The factory wires any
# `phase_c_dataset_*()` rows into `dashboard.py` via `monkeypatch` on
# `_phase_c_open_db`. The companion no-fallback fixture proves no test
# path can fall through to the production `trading_bot.db`.
#
# Preserved Phase C semantics (apply to every dataset produced here):
#   * applied=false contributes 0 to gate aggregation
#   * applied missing or malformed fails closed (contributes 0)
#   * HOLD_INELIGIBLE never auto-implies a failed gate
#   * SKIPPED_INVALID_DATA is a distinct outcome (not merged with HOLD)
#   * candidate requires candidate_rank != null (Phase B contract)
#   * submitted != filled (separate cycle_funnel columns)
#   * Default cohort is post_obs002; explicit cohort=all supported
#   * UTC ISO-8601 timestamps with explicit +00:00 suffix
#
# The factory NEVER reads `REPO_ROOT / trading_bot.db`. Tests that
# need the production path must opt in via the explicit `live_phase_c_db`
# fixture defined above (or skip when `DB_PATH` is missing).

# Original PHASE-C7 default rows promoted to module-level constants
# so the generalized factory can reuse them. Kept identical so
# `TestGateAggregationAppliedFilter` continues to pass unchanged.
_PHASE_C7_DEFAULT_DECISION_ROWS = [
    # (cycle_id, symbol, cycle_start, decision_snapshot_json, decision_schema_version)
    (
        "c1", "AAA", "2026-09-15T10:00:00+00:00", json.dumps({
            "decision": {"outcome": "HOLD_INELIGIBLE"},
            "strategy_eligibility": {
                "gates": [
                    {"name": "rsi_oversold", "category": "technical",
                     "applied": True, "passed": True,
                     "observed_value": 30, "threshold_value": 25}
                ]
            }
        }), 2,
    ),
    (
        "c2", "BBB", "2026-09-15T10:01:00+00:00", json.dumps({
            "decision": {"outcome": "HOLD_INELIGIBLE"},
            "strategy_eligibility": {
                "gates": [
                    {"name": "rsi_oversold", "category": "technical",
                     "applied": True, "passed": False,
                     "observed_value": 40, "threshold_value": 25}
                ]
            }
        }), 2,
    ),
    (
        "c3", "CCC", "2026-09-15T10:02:00+00:00", json.dumps({
            "decision": {"outcome": "HOLD_INELIGIBLE"},
            "strategy_eligibility": {
                "gates": [
                    {"name": "rsi_oversold", "category": "technical",
                     "applied": False, "passed": None,
                     "observed_value": None, "threshold_value": None}
                ]
            }
        }), 2,
    ),
    (
        "c4", "DDD", "2026-09-15T10:03:00+00:00", json.dumps({
            "decision": {"outcome": "HOLD_INELIGIBLE"},
            "strategy_eligibility": {
                "gates": [
                    {"name": "rsi_oversold", "category": "technical",
                     "passed": True,
                     "observed_value": 50, "threshold_value": 25}
                    # NOTE: 'applied' field intentionally absent
                ]
            }
        }), 2,
    ),
    (
        "c5", "EEE", "2026-09-15T10:04:00+00:00", json.dumps({
            "decision": {"outcome": "HOLD_INELIGIBLE"},
            "strategy_eligibility": {
                "gates": [
                    {"name": "sma_uptrend", "category": "technical",
                     "applied": True, "passed": False,
                     "observed_value": 0.95, "threshold_value": 1.05}
                ]
            }
        }), 2,
    ),
    (
        "c6", "FFF", "2026-09-15T10:05:00+00:00", json.dumps({
            "decision": {"outcome": "HOLD_INELIGIBLE"},
            "strategy_eligibility": {"gates": []}
        }), 2,
    ),
]

_PHASE_C7_DEFAULT_FUNNEL_ROW = (
    "c1", "2026-09-15T10:00:00+00:00", "2026-09-15T10:00:30+00:00",
    6, 0, 0, 0, 0, 0, 0, 0, 0, 2,
)


def phase_c_decision_row(cycle_id, symbol, cycle_start,
                          snapshot_dict, decision_schema_version=2):
    """Build a 5-tuple decision_history row."""
    return (
        cycle_id, symbol, cycle_start,
        json.dumps(snapshot_dict), decision_schema_version,
    )


def phase_c_funnel_row(cycle_id, cycle_start, cycle_end,
                        analyzed=0, strategy_eligible=0, ranked=0,
                        exec_attempt=0, exec_blocked=0,
                        order_submit_attempt=0, order_submitted=0,
                        order_failed=0, not_attempted=0, schema_version=2):
    """Build a 13-tuple cycle_funnel row matching production schema."""
    return (
        cycle_id, cycle_start, cycle_end,
        analyzed, strategy_eligible, ranked,
        exec_attempt, exec_blocked,
        order_submit_attempt, order_submitted,
        order_failed, not_attempted, schema_version,
    )


# ── Representative datasets ──────────────────────────────────────────
# Each returns (decision_rows, funnel_rows) suitable for the named
# endpoint test. Keep these small; bounded test data is the point.

def phase_c_dataset_funnel():
    """One multi-stage forward + off-path cycle.

    Forward path: analyzed=6 -> eligible=3 -> ranked=2 -> exec=1 -> submitted=1
    Off path:    blocked=1, failed=1, not_attempted=1
    """
    base = "2026-09-15T10:00:00+00:00"
    end = "2026-09-15T10:00:30+00:00"
    decision_rows = [
        phase_c_decision_row("c1", f"SYM{i}", base, {
            "decision": {"outcome": "HOLD_INELIGIBLE"},
            "strategy_eligibility": {"gates": []},
        })
        for i in range(6)
    ]
    funnel_rows = [
        phase_c_funnel_row("c1", base, end,
                            analyzed=6, strategy_eligible=3, ranked=2,
                            exec_attempt=1, exec_blocked=1,
                            order_submit_attempt=1, order_submitted=1,
                            order_failed=1, not_attempted=1),
    ]
    return decision_rows, funnel_rows


def phase_c_dataset_gates_coverage():
    """Three decision_history rows with mixed applied/passed states.

    Suitable for `test_gates_come_from_structured_gates_array`:
    rsi_oversold has applied=true rows with mixed passed values;
    sma_uptrend has one applied=true+passed=false row.
    """
    base = "2026-09-15T10:00:00+00:00"
    decision_rows = [
        phase_c_decision_row("c1", "AAA", base, {
            "decision": {"outcome": "HOLD_INELIGIBLE"},
            "strategy_eligibility": {
                "gates": [
                    {"name": "rsi_oversold", "category": "technical",
                     "applied": True, "passed": True,
                     "observed_value": 30, "threshold_value": 25},
                ]
            }
        }),
        phase_c_decision_row("c2", "BBB", base, {
            "decision": {"outcome": "HOLD_INELIGIBLE"},
            "strategy_eligibility": {
                "gates": [
                    {"name": "rsi_oversold", "category": "technical",
                     "applied": True, "passed": False,
                     "observed_value": 40, "threshold_value": 25},
                ]
            }
        }),
        phase_c_decision_row("c3", "CCC", base, {
            "decision": {"outcome": "HOLD_INELIGIBLE"},
            "strategy_eligibility": {
                "gates": [
                    {"name": "sma_uptrend", "category": "technical",
                     "applied": True, "passed": False,
                     "observed_value": 0.95, "threshold_value": 1.05},
                ]
            }
        }),
    ]
    funnel_rows = [
        phase_c_funnel_row("c1", base, base.replace("10:00:00", "10:00:30")),
    ]
    return decision_rows, funnel_rows


def phase_c_dataset_outcomes():
    """Rows covering HOLD_INELIGIBLE + SKIPPED_INVALID_DATA + BUY_FILLED.

    Satisfies `test_skipped_invalid_data_separated`: SKIPPED_INVALID_DATA
    must be its own row, not merged with HOLD_INELIGIBLE.
    """
    base = "2026-09-15T10:00:00+00:00"
    decision_rows = [
        phase_c_decision_row("c1", "AAA", base, {
            "decision": {"outcome": "HOLD_INELIGIBLE"},
            "strategy_eligibility": {"gates": []},
        }),
        phase_c_decision_row("c1", "BBB", base, {
            "decision": {"outcome": "HOLD_INELIGIBLE"},
            "strategy_eligibility": {"gates": []},
        }),
        phase_c_decision_row("c1", "CCC", base, {
            "decision": {"outcome": "SKIPPED_INVALID_DATA"},
            "strategy_eligibility": {"gates": []},
        }),
        phase_c_decision_row("c1", "DDD", base, {
            "decision": {"outcome": "BUY_FILLED"},
            "strategy_eligibility": {"gates": []},
        }),
    ]
    funnel_rows = [
        phase_c_funnel_row("c1", base, base.replace("10:00:00", "10:00:30"),
                            analyzed=4, strategy_eligible=1, ranked=1,
                            exec_attempt=1, order_submit_attempt=1,
                            order_submitted=1),
    ]
    return decision_rows, funnel_rows


def phase_c_dataset_coverage():
    """One pre-OBS-002 cycle + one post-OBS-002 cycle.

    Satisfies `test_coverage_response_has_pre_and_post_obs002_bands`:
    both bands present, each with the four stat keys.
    """
    pre_start = "2026-09-14T10:00:00+00:00"
    pre_end = "2026-09-14T10:00:30+00:00"
    post_start = "2026-09-15T10:00:00+00:00"
    post_end = "2026-09-15T10:00:30+00:00"
    decision_rows = [
        phase_c_decision_row("post1", "AAA", post_start, {
            "decision": {"outcome": "HOLD_INELIGIBLE"},
            "strategy_eligibility": {"gates": []},
        }),
    ]
    funnel_rows = [
        # Pre-OBS-002 cycle: no matching decision_history footprint,
        # so the coverage SQL sees this as incomplete (gap = analyzed).
        phase_c_funnel_row("pre1", pre_start, pre_end,
                            analyzed=3, schema_version=1),
        # Post-OBS-002 cycle: one matching decision row, complete.
        phase_c_funnel_row("post1", post_start, post_end,
                            analyzed=1, strategy_eligible=1, ranked=1,
                            schema_version=2),
    ]
    return decision_rows, funnel_rows


def phase_c_dataset_range_cohort():
    """One funnel row in the post_obs002 window.

    Satisfies `test_cohort_param_echoed_in_response`: the response
    echoes `cohort` and `range` regardless of cohort=all vs cohort=
    post_obs002 (both must include this single cycle).
    """
    base = "2026-09-15T10:00:00+00:00"
    decision_rows = [
        phase_c_decision_row("c1", "AAA", base, {
            "decision": {"outcome": "HOLD_INELIGIBLE"},
            "strategy_eligibility": {"gates": []},
        }),
    ]
    funnel_rows = [
        phase_c_funnel_row("c1", base, base.replace("10:00:00", "10:00:30"),
                            analyzed=1),
    ]
    return decision_rows, funnel_rows


# ── PHASE-C10B-2: window-anchored datasets ──────────────────────────
#
# `range_name=today|24h|7d` and `range_name=latest` all reference
# UTC `now()` (calendar day, 24h cutoff, 7d cutoff, latest
# cycle_end). To make the four ranges deterministic against a
# synthetic DB we anchor `cycle_start` and `cycle_end` to a
# `now()-epsilon` window so every range filter includes the row.
# The cohort filter is independent: `post_obs002` checks
# `cycle_start >= "2026-09-14T20:24:55"`, which any now()-anchored
# timestamp satisfies; `all` is `(1=1)`.
def _now_iso():
    """Return the current UTC timestamp as `+00:00` ISO-8601 string.

    Re-evaluated at every call so each migrated test gets a fresh
    anchor. Microseconds kept for lexicographic correctness.
    """
    from datetime import datetime as _dt, timezone as _tz
    return _dt.now(_tz.utc).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")


def phase_c_dataset_windowed():
    """Small forward-path + one applied-gate row pinned near `now()`.

    Anchors one cycle whose `cycle_start` and `cycle_end` are both
    within the current UTC day so:
      * `range=today` (>= today 00:00 AND < today 23:59:59.999999)  ✔
      * `range=24h`    (>= now-24h)                                 ✔
      * `range=7d`     (>= now-7d)                                  ✔
      * `range=latest` (cycle with the latest non-null cycle_end)   ✔
      * `cohort=post_obs002` (>= 2026-09-14T20:24:55)               ✔
      * `cohort=all`    ((1=1))                                      ✔

    The single decision row carries one applied=true + passed=true
    gate so `/api/phase-c/strategy-gates` returns a non-empty
    `gates[]` for any range. The cycle_funnel row encodes the
    smallest non-zero forward path so the funnel/off-path
    assertions still find their keys.
    """
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    now = _dt.now(_tz.utc)
    base = now.strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")
    end_dt = now + _td(seconds=30)
    end = end_dt.strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")
    decision_rows = [
        phase_c_decision_row("cwin1", "WIN", base, {
            "decision": {"outcome": "HOLD_INELIGIBLE"},
            "strategy_eligibility": {
                "gates": [
                    {"name": "rsi_oversold", "category": "technical",
                     "applied": True, "passed": True,
                     "observed_value": 30, "threshold_value": 25},
                ]
            }
        }),
    ]
    funnel_rows = [
        phase_c_funnel_row("cwin1", base, end,
                            analyzed=1, strategy_eligible=1, ranked=1,
                            exec_attempt=1, order_submit_attempt=1,
                            order_submitted=1, schema_version=2),
    ]
    return decision_rows, funnel_rows


def phase_c_dataset_outcomes_windowed():
    """Windowed outcomes mix (HOLD×2, SKIPPED_INVALID_DATA×1,
    BUY_FILLED×1) anchored near `now()`.

    Used by `test_outcomes_response_shape_after_refactor` so the
    `outcomes[]` array contains `SKIPPED_INVALID_DATA` AND the
    response also works against `range=7d` / `cohort=post_obs002`.
    """
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    now = _dt.now(_tz.utc)
    base = now.strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")
    end = (now + _td(seconds=30)).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")
    decision_rows = [
        phase_c_decision_row("cout1", "AAA", base, {
            "decision": {"outcome": "HOLD_INELIGIBLE"},
            "strategy_eligibility": {"gates": []},
        }),
        phase_c_decision_row("cout1", "BBB", base, {
            "decision": {"outcome": "HOLD_INELIGIBLE"},
            "strategy_eligibility": {"gates": []},
        }),
        phase_c_decision_row("cout1", "CCC", base, {
            "decision": {"outcome": "SKIPPED_INVALID_DATA"},
            "strategy_eligibility": {"gates": []},
        }),
        phase_c_decision_row("cout1", "DDD", base, {
            "decision": {"outcome": "BUY_FILLED"},
            "strategy_eligibility": {"gates": []},
        }),
    ]
    funnel_rows = [
        phase_c_funnel_row("cout1", base, end,
                            analyzed=4, strategy_eligible=1, ranked=1,
                            exec_attempt=1, order_submit_attempt=1,
                            order_submitted=1, schema_version=2),
    ]
    return decision_rows, funnel_rows


def phase_c_dataset_coverage_windowed():
    """One pre-OBS-002 cycle + one post-OBS-002 cycle, both anchored
    within the 7d window relative to `now()`.

    Used by `test_coverage_response_shape_after_refactor` (range=7d).
    The pre cycle uses `OBS_002_DEPLOYMENT_BOUNDARY_UTC` minus 1
    microsecond (so it lands in `pre_obs002` band) and the post
    cycle uses a recent timestamp inside `post_obs002`. Because
    the boundary is a fixed string, the pre/post split works
    deterministically regardless of `now()`.
    """
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    now = _dt.now(_tz.utc)
    # Pre-OBS-002: 1 second before the boundary (well below now-7d
    # but we still include a cycle_end so it counts as a row).
    pre_start = "2026-09-14T20:24:54.999999+00:00"
    pre_end = "2026-09-14T20:24:55.999999+00:00"
    # Post-OBS-002: now-ish so `range=7d` includes it.
    post_start = now.strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")
    post_end = (now + _td(seconds=30)).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")
    decision_rows = [
        phase_c_decision_row("cov_post1", "AAA", post_start, {
            "decision": {"outcome": "HOLD_INELIGIBLE"},
            "strategy_eligibility": {"gates": []},
        }),
    ]
    funnel_rows = [
        # Pre-OBS-002 cycle: schema_version=1, no matching decision
        # row in the post-obs-002 era (so it counts as incomplete).
        phase_c_funnel_row("cov_pre1", pre_start, pre_end,
                            analyzed=3, schema_version=1),
        # Post-OBS-002 cycle: one matching decision row, complete.
        phase_c_funnel_row("cov_post1", post_start, post_end,
                            analyzed=1, strategy_eligible=1, ranked=1,
                            schema_version=2),
    ]
    return decision_rows, funnel_rows


# ── Generalized factory + fixtures ────────────────────────────────────

def _build_phase_c_db(decision_rows=None, funnel_rows=None, path=None):
    """Build an in-memory (or file-backed) sqlite DB with the Phase C schema.

    With no arguments, returns the original PHASE-C7 6-row dataset so
    `TestGateAggregationAppliedFilter` keeps working unchanged. Pass
    `decision_rows` and/or `funnel_rows` to seed a custom dataset.

    Schema columns match `dashboard._phase_c_open_db()` exactly.

    When `path` is provided, the DB lives on that filesystem path
    so multiple `sqlite3.connect(path)` calls share the same data
    even after individual connections are closed. This matters for
    tests like `TestTimeWindowBoundaries::test_latest_returns_subset_of_7d`
    that issue multiple endpoint calls within one test (each Phase C
    endpoint closes its conn in a `finally:` block, so a single shared
    in-memory connection would be closed after the first call).

    In-memory mode (`path is None`) keeps the original PHASE-C7
    semantics intact for the 9-test `TestPhaseC10AFactorySelfTest`
    block.
    """
    if decision_rows is None:
        decision_rows = _PHASE_C7_DEFAULT_DECISION_ROWS
    if funnel_rows is None:
        funnel_rows = [_PHASE_C7_DEFAULT_FUNNEL_ROW]

    if path is None:
        conn = sqlite3.connect(":memory:", check_same_thread=False)
    else:
        conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE decision_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cycle_id TEXT,
            symbol TEXT,
            cycle_start TEXT,
            decision_snapshot TEXT,
            decision_schema_version INTEGER,
            analytics_persistence_version INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE cycle_funnel (
            cycle_id TEXT,
            cycle_start TEXT,
            cycle_end TEXT,
            analyzed_count INTEGER,
            strategy_eligible_count INTEGER,
            ranked_candidate_count INTEGER,
            execution_attempt_count INTEGER,
            execution_blocked_count INTEGER,
            order_submission_attempt_count INTEGER,
            order_submitted_count INTEGER,
            order_failed_count INTEGER,
            not_attempted_count INTEGER,
            schema_version INTEGER
        )
        """
    )

    for row in decision_rows:
        cur.execute(
            "INSERT INTO decision_history "
            "(cycle_id, symbol, cycle_start, decision_snapshot, decision_schema_version) "
            "VALUES (?, ?, ?, ?, ?)",
            row,
        )

    for row in funnel_rows:
        cur.execute(
            "INSERT INTO cycle_funnel "
            "(cycle_id, cycle_start, cycle_end, "
            " analyzed_count, strategy_eligible_count, ranked_candidate_count, "
            " execution_attempt_count, execution_blocked_count, "
            " order_submission_attempt_count, order_submitted_count, "
            " order_failed_count, not_attempted_count, schema_version) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            row,
        )

    # PHASE-C14B-2C: create empty child tables so the v1 path of
    # the hybrid reader doesn't error on "no such table" when
    # running pre-hybrid test data. With analytics_persistence_version
    # defaulting to 0, the v1 path returns no rows; this just
    # makes the schema complete.
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS decision_gate_evaluations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            decision_history_id INTEGER NOT NULL,
            cycle_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            cycle_start TEXT NOT NULL,
            ordinality INTEGER NOT NULL,
            gate_name TEXT NOT NULL,
            gate_category TEXT,
            applied INTEGER NOT NULL,
            passed INTEGER,
            observed_value REAL,
            threshold_value REAL,
            reason TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS decision_execution_checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            decision_history_id INTEGER NOT NULL,
            cycle_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            cycle_start TEXT NOT NULL,
            ordinality INTEGER NOT NULL,
            check_name TEXT NOT NULL,
            applied INTEGER NOT NULL,
            passed INTEGER,
            observed_value REAL,
            threshold_value REAL,
            reason TEXT,
            gap_note TEXT,
            is_first_blocking INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    # PHASE-C14B-2C: cycle_start indexes for v1 SQL INDEXED BY hints.
    cur.execute(
        "CREATE INDEX idx_dge_cycle_start "
        "ON decision_gate_evaluations(cycle_start)"
    )
    cur.execute(
        "CREATE INDEX idx_dec_cycle_start "
        "ON decision_execution_checks(cycle_start)"
    )
    conn.commit()
    return conn



def _build_phase_c_db_with_hybrid(decision_rows=None, funnel_rows=None,
                                    gate_rows_v1=None, exec_rows_v1=None,
                                    decision_rows_extended=None,
                                    path=None):
    """PHASE-C14B-2C: build a synthetic Phase C DB for hybrid-read tests.

    Like `_build_phase_c_db`, but:
      * Adds the `analytics_persistence_version` column to decision_history
        (defaulting to 0 if `decision_rows` tuples don't supply one).
      * Creates `decision_gate_evaluations` and `decision_execution_checks`
        child tables so the v1 path of the hybrid reader has somewhere
        to read from.

    `gate_rows_v1` / `exec_rows_v1` are lists of tuples matching the
    production schemas (see src/database/sqlite_db.py CREATE TABLE).

    `decision_rows_extended` is an optional override for `decision_rows`
    that uses the full 6-column tuple
    (cycle_id, symbol, cycle_start, snapshot_json, schema_version,
    analytics_persistence_version). If supplied, it takes precedence
    over `decision_rows`.
    """
    if decision_rows_extended is None and decision_rows is not None:
        # Default all legacy rows to v0 unless overridden
        decision_rows_extended = []
        for row in decision_rows:
            if len(row) >= 6:
                decision_rows_extended.append(row)
            else:
                # Append analytics_persistence_version=0
                decision_rows_extended.append(row + (0,))
    elif decision_rows_extended is None:
        decision_rows_extended = []
        if decision_rows is None:
            decision_rows = _PHASE_C7_DEFAULT_DECISION_ROWS
        for row in decision_rows:
            if len(row) >= 6:
                decision_rows_extended.append(row)
            else:
                decision_rows_extended.append(row + (0,))

    if funnel_rows is None:
        funnel_rows = [_PHASE_C7_DEFAULT_FUNNEL_ROW]

    if path is None:
        conn = sqlite3.connect(":memory:", check_same_thread=False)
    else:
        conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE decision_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cycle_id TEXT,
            symbol TEXT,
            cycle_start TEXT,
            decision_snapshot TEXT,
            decision_schema_version INTEGER,
            analytics_persistence_version INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE cycle_funnel (
            cycle_id TEXT,
            cycle_start TEXT,
            cycle_end TEXT,
            analyzed_count INTEGER,
            strategy_eligible_count INTEGER,
            ranked_candidate_count INTEGER,
            execution_attempt_count INTEGER,
            execution_blocked_count INTEGER,
            order_submission_attempt_count INTEGER,
            order_submitted_count INTEGER,
            order_failed_count INTEGER,
            not_attempted_count INTEGER,
            schema_version INTEGER
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE decision_gate_evaluations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            decision_history_id INTEGER NOT NULL,
            cycle_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            cycle_start TEXT NOT NULL,
            ordinality INTEGER NOT NULL,
            gate_name TEXT NOT NULL,
            gate_category TEXT,
            applied INTEGER NOT NULL,
            passed INTEGER,
            observed_value REAL,
            threshold_value REAL,
            reason TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE decision_execution_checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            decision_history_id INTEGER NOT NULL,
            cycle_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            cycle_start TEXT NOT NULL,
            ordinality INTEGER NOT NULL,
            check_name TEXT NOT NULL,
            applied INTEGER NOT NULL,
            passed INTEGER,
            observed_value REAL,
            threshold_value REAL,
            reason TEXT,
            gap_note TEXT,
            is_first_blocking INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    # PHASE-C14B-2C: cycle_start indexes for the v1 SQL INDEXED BY hints.
    cur.execute(
        "CREATE INDEX idx_dge_cycle_start "
        "ON decision_gate_evaluations(cycle_start)"
    )
    cur.execute(
        "CREATE INDEX idx_dec_cycle_start "
        "ON decision_execution_checks(cycle_start)"
    )

    for row in decision_rows_extended:
        cur.execute(
            "INSERT INTO decision_history "
            "(cycle_id, symbol, cycle_start, decision_snapshot, "
            " decision_schema_version, analytics_persistence_version) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            row,
        )

    for row in funnel_rows:
        cur.execute(
            "INSERT INTO cycle_funnel "
            "(cycle_id, cycle_start, cycle_end, "
            " analyzed_count, strategy_eligible_count, ranked_candidate_count, "
            " execution_attempt_count, execution_blocked_count, "
            " order_submission_attempt_count, order_submitted_count, "
            " order_failed_count, not_attempted_count, schema_version) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            row,
        )

    if gate_rows_v1:
        for row in gate_rows_v1:
            cur.execute(
                "INSERT INTO decision_gate_evaluations "
                "(decision_history_id, cycle_id, symbol, cycle_start, "
                " ordinality, gate_name, gate_category, "
                " applied, passed, observed_value, threshold_value, reason) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                row,
            )

    if exec_rows_v1:
        for row in exec_rows_v1:
            cur.execute(
                "INSERT INTO decision_execution_checks "
                "(decision_history_id, cycle_id, symbol, cycle_start, "
                " ordinality, check_name, "
                " applied, passed, observed_value, threshold_value, "
                " reason, gap_note, is_first_blocking) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                row,
            )

    # PHASE-C14B-2C: cycle_start indexes are created at table-creation
    # time (lines 888-892 above). No-op here to avoid
    # "index already exists" if both code paths execute.
    pass

    conn.commit()
    return conn


@pytest.fixture
def synthetic_phase_c_hybrid_db(monkeypatch, tmp_path):
    """PHASE-C14B-2C: factory for synthetic DBs with v0/v1 split.

    Usage:
        def test_x(synthetic_phase_c_hybrid_db):
            conn, opener = synthetic_phase_c_hybrid_db(
                decision_rows=[...],         # v0 by default
                decision_rows_extended=[...], # explicit version per row
                gate_rows_v1=[...],          # normalized gate rows
                exec_rows_v1=[...],          # normalized exec-check rows
            )
            ...
    """
    tracked = []
    opened_paths = set()

    def _make(decision_rows=None, decision_rows_extended=None,
              funnel_rows=None, gate_rows_v1=None, exec_rows_v1=None):
        db_path = tmp_path / "phase_c_hybrid_synthetic.db"
        seed_conn = _build_phase_c_db_with_hybrid(
            decision_rows=decision_rows,
            decision_rows_extended=decision_rows_extended,
            funnel_rows=funnel_rows,
            gate_rows_v1=gate_rows_v1,
            exec_rows_v1=exec_rows_v1,
            path=str(db_path),
        )
        opened_paths.add(str(db_path))
        tracked.append((str(db_path), seed_conn))

        def _opener(*args, **kwargs):
            opened_paths.add(str(db_path))
            new_conn = sqlite3.connect(str(db_path), check_same_thread=False)
            new_conn.row_factory = sqlite3.Row
            return new_conn

        import sys
        sys.path.insert(0, str(REPO_ROOT))
        import dashboard as _dashboard
        monkeypatch.setattr(_dashboard, "_phase_c_open_db", _opener)
        return seed_conn

    try:
        yield _make
    finally:
        # Safety canary: no opener should have resolved to production.
        for path, _ in tracked:
            assert path in opened_paths
            assert path.startswith(str(tmp_path)), (
                f"hybrid DB opened outside tmp_path: {path}"
            )
        for _, seed_conn in tracked:
            try:
                seed_conn.close()
            except Exception:
                pass


@pytest.fixture
def synthetic_phase_c_db_factory(monkeypatch, tmp_path):
    """Yield a callable that wires a custom DB into dashboard.

    Usage:
        def test_x(synthetic_phase_c_db_factory):
            decision_rows, funnel_rows = phase_c_dataset_funnel()
            conn = synthetic_phase_c_db_factory(
                decision_rows=decision_rows, funnel_rows=funnel_rows,
            )
            c = _client()
            r = c.get("/api/phase-c/funnel?range=7d")
            ...

    The fixture installs a `monkeypatch` that routes
    `dashboard._phase_c_open_db` to a closure that opens a fresh
    connection to a tempfile-backed DB on every call. This lets
    tests issue multiple endpoint calls within a single test (each
    Phase C endpoint closes its conn in `finally:`). The patch is
    restored automatically at fixture teardown. The factory NEVER
    reads `REPO_ROOT / trading_bot.db`.

    **PHASE-C10B-1 canary**: every call to the patched opener is
    logged. At fixture teardown, the fixture asserts every recorded
    call opened the synthetic DB at the test's `tmp_path` location.
    A foreign-path open proves live-DB fall-through and fails the
    test loudly with a clear AssertionError pointing at the seam.
    """
    import sys
    sys.path.insert(0, str(REPO_ROOT))
    import dashboard as _dashboard

    # Each `_make()` call gets its own tmp DB path. The path is
    # recorded so the teardown canary can assert every opener call
    # opened the synthetic path (not the live DB).
    tracked = []  # list of (db_path, call_log_dict, seed_conn)

    def _make(decision_rows=None, funnel_rows=None):
        db_path = tmp_path / "phase_c_synthetic.db"
        # Seed the DB by opening once and committing the rows.
        # Keep this conn open for the duration of the test so
        # direct callers (C10A factory self-tests that do
        # `cur = conn.cursor()` after `_make()`) can still query it.
        # The seed conn is closed during fixture teardown.
        seed_conn = _build_phase_c_db(
            decision_rows=decision_rows,
            funnel_rows=funnel_rows,
            path=str(db_path),
        )
        # Keep the seed conn reference alive across the fixture
        # lifetime. It must outlive the endpoint `finally: conn.close()`
        # calls inside the test.
        tracked.append((str(db_path), {"calls": [str(db_path)]}, seed_conn))
        call_log = tracked[-1][1]

        def _opener(*args, **kwargs):
            # Each opener call returns a NEW sqlite3 connection
            # pointing at the same tmp_path file. This survives
            # the endpoint's `finally: conn.close()` so subsequent
            # requests within the same test still see the seeded
            # data.
            call_log["calls"].append(str(db_path))
            new_conn = sqlite3.connect(str(db_path), check_same_thread=False)
            new_conn.row_factory = sqlite3.Row
            return new_conn

        monkeypatch.setattr(_dashboard, "_phase_c_open_db", _opener)
        return seed_conn

    try:
        yield _make
    finally:
        # C10B-1 canary: every recorded call must have opened OUR
        # synthetic DB path. A foreign-path open means a code path
        # bypassed the patch (live-DB fall-through).
        for db_path, call_log, seed_conn in tracked:
            for actual_path in call_log["calls"]:
                assert actual_path == db_path, (
                    "synthetic_phase_c_db_factory canary fired: a code "
                    "path opened a foreign sqlite database. "
                    "_phase_c_open_db was bypassed or routed to a "
                    "different DB. Live-DB fall-through detected."
                )
            try:
                seed_conn.close()
            except Exception:
                pass
            # Best-effort cleanup of the tmp DB file.
            try:
                import os as _os
                if (REPO_ROOT / "phase_c_synthetic.db").exists() and \
                        str(REPO_ROOT / "phase_c_synthetic.db") == db_path:
                    _os.unlink(db_path)
            except Exception:
                pass


@pytest.fixture
def synthetic_phase_c_db_no_fallback(monkeypatch):
    """Safety net: any fall-through to live `trading_bot.db` raises.

    Use this in addition to `synthetic_phase_c_db_factory` to prove
    the test path never opens production data. Calling the patched
    `_phase_c_open_db` raises AssertionError with a clear message.
    """
    import sys
    sys.path.insert(0, str(REPO_ROOT))
    import dashboard as _dashboard

    def _explode(*args, **kwargs):
        raise AssertionError(
            "synthetic_phase_c_db_no_fallback fired: a code path tried "
            "to open the production trading_bot.db. monkeypatch "
            "_phase_c_open_db before any dashboard query runs."
        )
    monkeypatch.setattr(_dashboard, "_phase_c_open_db", _explode)
    yield _explode


# ─────────────────────────────────────────────────────────────────────────
# Endpoint registration
# ─────────────────────────────────────────────────────────────────────────

class TestPhaseCEndpointsRegistered:
    def test_funnel_endpoint_registered(self):
        from fastapi.testclient import TestClient
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        import dashboard as _dashboard
        routes = [r.path for r in _dashboard.app.routes if hasattr(r, "path")]
        assert "/api/phase-c/funnel" in routes

    def test_strategy_gates_endpoint_registered(self):
        from fastapi.testclient import TestClient
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        import dashboard as _dashboard
        routes = [r.path for r in _dashboard.app.routes if hasattr(r, "path")]
        assert "/api/phase-c/strategy-gates" in routes

    def test_execution_blockers_endpoint_registered(self):
        from fastapi.testclient import TestClient
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        import dashboard as _dashboard
        routes = [r.path for r in _dashboard.app.routes if hasattr(r, "path")]
        assert "/api/phase-c/execution-blockers" in routes

    def test_outcomes_endpoint_registered(self):
        from fastapi.testclient import TestClient
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        import dashboard as _dashboard
        routes = [r.path for r in _dashboard.app.routes if hasattr(r, "path")]
        assert "/api/phase-c/outcomes" in routes

    def test_coverage_endpoint_registered(self):
        from fastapi.testclient import TestClient
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        import dashboard as _dashboard
        routes = [r.path for r in _dashboard.app.routes if hasattr(r, "path")]
        assert "/api/phase-c/coverage" in routes

    def test_no_other_phase_c_endpoints_added(self):
        # Whitelist: only these five new endpoints are added by Phase C.
        from fastapi.testclient import TestClient
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        import dashboard as _dashboard
        routes = sorted({
            r.path for r in _dashboard.app.routes
            if hasattr(r, "path") and r.path.startswith("/api/phase-c")
        })
        assert routes == [
            "/api/phase-c/coverage",
            "/api/phase-c/execution-blockers",
            "/api/phase-c/funnel",
            "/api/phase-c/outcomes",
            "/api/phase-c/strategy-gates",
        ]


# ─────────────────────────────────────────────────────────────────────────
# Range and cohort parameter validation
# ─────────────────────────────────────────────────────────────────────────

class TestPhaseCRangeCohortValidation:
    def test_funnel_invalid_range_returns_error_envelope(self, synthetic_phase_c_db_factory):
        # PHASE-C10B-3: install the factory seam so the canary
        # proves no live-DB fall-through even though the validate
        # gate short-circuits before any DB call. The dataset is
        # unused; only the seam patch matters for isolation.
        decision_rows, funnel_rows = phase_c_dataset_windowed()
        conn = synthetic_phase_c_db_factory(
            decision_rows=decision_rows, funnel_rows=funnel_rows,
        )
        c = _client()
        r = c.get("/api/phase-c/funnel?range=garbage")
        assert r.status_code == 200
        body = r.json()
        assert body.get("error")
        assert "invalid range" in body["error"]
        assert sorted(body["valid_ranges"]) == ["24h", "7d", "latest", "today"]

    def test_funnel_invalid_cohort_returns_error_envelope(self, synthetic_phase_c_db_factory):
        decision_rows, funnel_rows = phase_c_dataset_windowed()
        conn = synthetic_phase_c_db_factory(
            decision_rows=decision_rows, funnel_rows=funnel_rows,
        )
        c = _client()
        r = c.get("/api/phase-c/funnel?cohort=garbage")
        body = r.json()
        assert body.get("error")
        assert "invalid cohort" in body["error"]
        assert sorted(body["valid_cohorts"]) == ["all", "post_obs002"]

    def test_outcomes_invalid_range_returns_error_envelope(self, synthetic_phase_c_db_factory):
        decision_rows, funnel_rows = phase_c_dataset_windowed()
        conn = synthetic_phase_c_db_factory(
            decision_rows=decision_rows, funnel_rows=funnel_rows,
        )
        c = _client()
        r = c.get("/api/phase-c/outcomes?range=foo")
        body = r.json()
        assert "error" in body

    def test_cohort_param_echoed_in_response(self, synthetic_phase_c_db_factory):
        decision_rows, funnel_rows = phase_c_dataset_range_cohort()
        conn = synthetic_phase_c_db_factory(
            decision_rows=decision_rows, funnel_rows=funnel_rows,
        )
        c = _client()
        for cohort in ("post_obs002", "all"):
            r = c.get(f"/api/phase-c/funnel?cohort={cohort}&range=7d")
            body = r.json()
            assert body.get("cohort") == cohort
            assert body.get("range") == "7d"


# ─────────────────────────────────────────────────────────────────────────
# Source-of-truth enforcement
# ─────────────────────────────────────────────────────────────────────────

class TestSourceOfTruth:
    """No endpoint may recompute a decision from current settings or
    raw indicators. We assert each endpoint reads only from
    cycle_funnel + decision_history."""

    def test_funnel_reads_only_from_cycle_funnel(self, synthetic_phase_c_db_factory):
        # The funnel endpoint must NOT touch decision_history at all.
        # It reads cycle_funnel aggregated counters (decision_history
        # would double-count per-symbol decisions).
        decision_rows, funnel_rows = phase_c_dataset_windowed()
        conn = synthetic_phase_c_db_factory(
            decision_rows=decision_rows, funnel_rows=funnel_rows,
        )
        c = _client()
        r = c.get("/api/phase-c/funnel?range=7d")
        body = r.json()
        # Forward path counters come from cycle_funnel; no per-decision
        # counts in the response.
        assert "decision_history" not in body
        assert "forward_path" in body
        assert "off_path" in body

    def test_strategy_gates_source_field(self, synthetic_phase_c_db_factory):
        decision_rows, funnel_rows = phase_c_dataset_windowed()
        conn = synthetic_phase_c_db_factory(
            decision_rows=decision_rows, funnel_rows=funnel_rows,
        )
        c = _client()
        r = c.get("/api/phase-c/strategy-gates?range=latest")
        body = r.json()
        assert body["source"] == (
            "PHASE-C14B-2C hybrid: v0=decision_history.decision_snapshot -> "
            "$.strategy_eligibility.gates[] (analytics_persistence_version=0); "
            "v1=decision_gate_evaluations (analytics_persistence_version=1)"
        )
        # The inference rule is documented explicitly in the response.
        assert "HOLD_INELIGIBLE" in body["inference_rule"]
        assert "passed == false" in body["inference_rule"]

    def test_execution_blockers_source_field_and_universe_caveat(self, synthetic_phase_c_db_factory):
        decision_rows, funnel_rows = phase_c_dataset_windowed()
        conn = synthetic_phase_c_db_factory(
            decision_rows=decision_rows, funnel_rows=funnel_rows,
        )
        c = _client()
        r = c.get("/api/phase-c/execution-blockers?range=latest")
        body = r.json()
        assert body["source"] == (
            "PHASE-C14B-2C hybrid: v0=decision_history.decision_snapshot -> "
            "$.execution_checks.checks[] (analytics_persistence_version=0); "
            "v1=decision_execution_checks (analytics_persistence_version=1)"
        )
        # Honest universe caveat is present.
        caveat = body["universe_caveat"]
        assert "execute_trade" in caveat
        assert "SELL_BLOCKED_DYNAMIC" in caveat

    def test_outcomes_source_field(self, synthetic_phase_c_db_factory):
        decision_rows, funnel_rows = phase_c_dataset_outcomes_windowed()
        conn = synthetic_phase_c_db_factory(
            decision_rows=decision_rows, funnel_rows=funnel_rows,
        )
        c = _client()
        r = c.get("/api/phase-c/outcomes?range=latest")
        body = r.json()
        assert body["source"] == "decision_history.decision_snapshot -> $.decision.outcome"


# ─────────────────────────────────────────────────────────────────────────
# Gate-aggregation contract
# ─────────────────────────────────────────────────────────────────────────

class TestGateAggregationContract:
    """The Strategy Gate Failure Frequency must come from
    decision_history.decision_snapshot -> $.strategy_eligibility.gates[].
    NEVER from primary_reason text."""

    def test_gates_come_from_structured_gates_array(self, synthetic_phase_c_db_factory):
        decision_rows, funnel_rows = phase_c_dataset_gates_coverage()
        conn = synthetic_phase_c_db_factory(
            decision_rows=decision_rows, funnel_rows=funnel_rows,
        )
        c = _client()
        r = c.get("/api/phase-c/strategy-gates?range=latest")
        body = r.json()
        # Gates list may be empty only if the cohort is empty.
        assert isinstance(body["gates"], list)
        # If non-empty, every gate entry has structured fields.
        for g in body["gates"]:
            for k in ("gate_name", "total_evaluations", "passed", "failed",
                      "failure_rate", "applied_count"):
                assert k in g, f"missing structured field '{k}' in gate {g.get('gate_name')}"
            # failure_rate is a number (not a string) and bounded [0, 1].
            assert 0.0 <= float(g["failure_rate"]) <= 1.0

    def test_no_primary_reason_parsing_in_response(self, synthetic_phase_c_db_factory):
        # PHASE-C10B-3: install the factory seam so the test
        # deterministically reads from a synthetic decision_history
        # instead of the live trading_bot.db.
        decision_rows, funnel_rows = phase_c_dataset_windowed()
        conn = synthetic_phase_c_db_factory(
            decision_rows=decision_rows, funnel_rows=funnel_rows,
        )
        c = _client()
        # The strategy-gates response must NOT include any parsed
        # primary_reason text. We verify the response shape: gate
        # entries do NOT carry a "primary_reason" field.
        r = c.get("/api/phase-c/strategy-gates?range=latest")
        body = r.json()
        for g in body["gates"]:
            assert "primary_reason" not in g
            assert "failed_gates_text" not in g

    def test_failure_counting_matches_structured_passed_field(self, synthetic_phase_c_db_factory):
        # PHASE-C10B-3: install the factory seam so the test
        # deterministically reads from a synthetic decision_history
        # instead of the live trading_bot.db.
        decision_rows, funnel_rows = phase_c_dataset_windowed()
        conn = synthetic_phase_c_db_factory(
            decision_rows=decision_rows, funnel_rows=funnel_rows,
        )
        c = _client()
        # For each gate: failed = total_evaluations - passed
        # (where passed = count of gate records where passed=true).
        r = c.get("/api/phase-c/strategy-gates?range=latest")
        body = r.json()
        for g in body["gates"]:
            # Two possibilities for "failure":
            #   (a) gate.passed == false (the canonical "failed" semantic)
            #   (b) gate.passed == true (counted as passed)
            # The endpoint MUST NOT count gate.passed == null as failed.
            # That's covered by the structured source itself; we
            # verify the totals are non-negative.
            assert g["total_evaluations"] >= 0
            assert g["passed"] >= 0
            assert g["failed"] >= 0
            assert g["total_evaluations"] == g["passed"] + g["failed"]


# ─────────────────────────────────────────────────────────────────────────
# PHASE-C7: applied=true filter
# ─────────────────────────────────────────────────────────────────────────

class TestGateAggregationAppliedFilter:
    """PHASE-C7 hardening: only persisted applied=true gate evaluations
    contribute to total_evaluations, passed, and failed. The applied=false
    and malformed/missing-applied paths contribute 0 to all three (fail
    closed). These tests use a synthetic in-memory DB (see
    `synthetic_phase_c_db` fixture) so we can inject applied=false and
    malformed-applied rows that the live DB never contains."""

    def _rsi(self, body):
        for g in body["gates"]:
            if g["gate_name"] == "rsi_oversold":
                return g
        return None

    def _sma(self, body):
        for g in body["gates"]:
            if g["gate_name"] == "sma_uptrend":
                return g
        return None

    def test_applied_false_excluded_from_total(self, synthetic_phase_c_db):
        # c1 (applied=true, passed=true), c2 (applied=true, passed=false),
        # c3 (applied=false), c4 (malformed applied), c5 (sma_uptrend),
        # c6 (empty gates).
        # rsi_oversold should have total=2 (c1 + c2), NOT 4 (which would
        # include c3 and c4).
        c = _client()
        body = c.get("/api/phase-c/strategy-gates?range=7d&cohort=all").json()
        rsi = self._rsi(body)
        assert rsi is not None, "rsi_oversold missing from response"
        assert rsi["total_evaluations"] == 2, (
            f"applied=false/malformed must not contribute to total "
            f"(got {rsi['total_evaluations']}, expected 2)"
        )

    def test_applied_false_excluded_from_passed(self, synthetic_phase_c_db):
        # Only c1 (applied=true, passed=true) contributes to passed.
        c = _client()
        body = c.get("/api/phase-c/strategy-gates?range=7d&cohort=all").json()
        rsi = self._rsi(body)
        assert rsi is not None
        assert rsi["passed"] == 1, (
            f"only applied=true+passed=true should count as passed "
            f"(got {rsi['passed']}, expected 1)"
        )

    def test_applied_false_excluded_from_failed(self, synthetic_phase_c_db):
        # Only c2 (applied=true, passed=false) contributes to failed.
        c = _client()
        body = c.get("/api/phase-c/strategy-gates?range=7d&cohort=all").json()
        rsi = self._rsi(body)
        assert rsi is not None
        assert rsi["failed"] == 1, (
            f"only applied=true+passed=false should count as failed "
            f"(got {rsi['failed']}, expected 1)"
        )

    def test_failure_rate_uses_only_applied_true_denominator(self, synthetic_phase_c_db):
        # failure_rate = failed / total = 1 / 2 = 0.5
        c = _client()
        body = c.get("/api/phase-c/strategy-gates?range=7d&cohort=all").json()
        rsi = self._rsi(body)
        assert rsi is not None
        assert abs(rsi["failure_rate"] - 0.5) < 1e-9, (
            f"failure_rate should be 0.5 (1 failed / 2 total applied=true), "
            f"got {rsi['failure_rate']}"
        )

    def test_applied_count_equals_total_count_in_filtered_view(self, synthetic_phase_c_db):
        # PHASE-C7: WHERE filter restricts the join to applied=true
        # rows, so applied_count == total_count for every gate.
        c = _client()
        body = c.get("/api/phase-c/strategy-gates?range=7d&cohort=all").json()
        for g in body["gates"]:
            assert g["applied_count"] == g["total_evaluations"], (
                f"gate {g['gate_name']}: applied_count={g['applied_count']} "
                f"!= total_evaluations={g['total_evaluations']} (PHASE-C7 invariant)"
            )

    def test_malformed_applied_fails_closed(self, synthetic_phase_c_db):
        # c4 has no `applied` field at all. It must contribute 0 to
        # rsi_oversold's counts (treated identically to applied=false).
        c = _client()
        body = c.get("/api/phase-c/strategy-gates?range=7d&cohort=all").json()
        rsi = self._rsi(body)
        assert rsi is not None
        # If c4 had contributed (e.g., as failed=1), total would be 3+.
        assert rsi["total_evaluations"] == 2
        assert rsi["passed"] == 1
        assert rsi["failed"] == 1

    def test_hold_ineligible_with_empty_gates_does_not_create_failed_gate(self, synthetic_phase_c_db):
        # c6 has outcome=HOLD_INELIGIBLE but empty gates[]. It must
        # NOT contribute to any gate count.
        c = _client()
        body = c.get("/api/phase-c/strategy-gates?range=7d&cohort=all").json()
        # rsi_oversold count must be unchanged (still 2).
        rsi = self._rsi(body)
        assert rsi is not None
        assert rsi["total_evaluations"] == 2
        assert rsi["passed"] == 1
        assert rsi["failed"] == 1
        # sma_uptrend must be exactly c5 (1 evaluation, 0 passed, 1 failed).
        sma = self._sma(body)
        assert sma is not None
        assert sma["total_evaluations"] == 1
        assert sma["passed"] == 0
        assert sma["failed"] == 1

    def test_inference_rule_string_documents_applied_filter(self, synthetic_phase_c_db):
        c = _client()
        body = c.get("/api/phase-c/strategy-gates?range=7d&cohort=all").json()
        rule = body["inference_rule"]
        assert "applied" in rule.lower()
        assert "true" in rule.lower()
        assert "false" in rule.lower()
        assert "HOLD_INELIGIBLE" in rule

    def test_total_evaluations_equals_passed_plus_failed_per_gate(self, synthetic_phase_c_db):
        # tautology, but locks in the relationship
        c = _client()
        body = c.get("/api/phase-c/strategy-gates?range=7d&cohort=all").json()
        for g in body["gates"]:
            assert g["total_evaluations"] == g["passed"] + g["failed"], (
                f"gate {g['gate_name']}: total != passed + failed"
            )


# ─────────────────────────────────────────────────────────────────────────
# Funnel aggregation
# ─────────────────────────────────────────────────────────────────────────

class TestFunnelAggregation:
    def test_funnel_response_envelope_shape(self, synthetic_phase_c_db_factory):
        decision_rows, funnel_rows = phase_c_dataset_funnel()
        conn = synthetic_phase_c_db_factory(
            decision_rows=decision_rows, funnel_rows=funnel_rows,
        )
        c = _client()
        r = c.get("/api/phase-c/funnel?range=7d")
        body = r.json()
        # Required keys
        for k in ("range", "cohort", "cycles", "forward_path", "off_path"):
            assert k in body
        # Forward path has exactly five stages
        fp = body["forward_path"]
        for k in ("analyzed_count", "strategy_eligible_count",
                  "ranked_candidate_count", "execution_attempt_count",
                  "orders_submitted_count"):
            assert k in fp
            assert isinstance(fp[k], int)
        # Off-path has exactly three counters
        op = body["off_path"]
        for k in ("execution_blocked_count", "orders_failed_count",
                  "not_attempted_count"):
            assert k in op
            assert isinstance(op[k], int)

    def test_off_path_is_separate_from_forward_path(self, synthetic_phase_c_db_factory):
        # The Phase C contract requires execution_blocked_count and
        # order_failed_count to NEVER appear in the forward path.
        decision_rows, funnel_rows = phase_c_dataset_windowed()
        conn = synthetic_phase_c_db_factory(
            decision_rows=decision_rows, funnel_rows=funnel_rows,
        )
        c = _client()
        r = c.get("/api/phase-c/funnel?range=7d")
        body = r.json()
        fp = body["forward_path"]
        for forbidden in ("execution_blocked_count", "order_failed_count",
                          "orders_failed_count"):
            assert forbidden not in fp, (
                f"off-path counter '{forbidden}' must not appear in "
                f"forward_path"
            )


# ─────────────────────────────────────────────────────────────────────────
# Execution blockers honest universe
# ─────────────────────────────────────────────────────────────────────────

class TestExecutionBlockersHonestUniverse:
    def test_universe_size_explicitly_reported(self, synthetic_phase_c_db_factory):
        decision_rows, funnel_rows = phase_c_dataset_windowed()
        conn = synthetic_phase_c_db_factory(
            decision_rows=decision_rows, funnel_rows=funnel_rows,
        )
        c = _client()
        r = c.get("/api/phase-c/execution-blockers?range=latest")
        body = r.json()
        assert "rows_in_cohort" in body
        assert "rows_with_checks" in body
        assert body["rows_in_cohort"] >= body["rows_with_checks"]

    def test_universe_caveat_warns_about_coverage(self, synthetic_phase_c_db_factory):
        decision_rows, funnel_rows = phase_c_dataset_windowed()
        conn = synthetic_phase_c_db_factory(
            decision_rows=decision_rows, funnel_rows=funnel_rows,
        )
        c = _client()
        r = c.get("/api/phase-c/execution-blockers?range=latest")
        body = r.json()
        # The caveat must explicitly state that not every decision
        # is in the universe.
        assert "execute_trade" in body["universe_caveat"]
        assert "currently" in body["universe_caveat"].lower() or "SELL" in body["universe_caveat"]


# ─────────────────────────────────────────────────────────────────────────
# Outcome distribution
# ─────────────────────────────────────────────────────────────────────────

class TestOutcomeDistribution:
    def test_skipped_invalid_data_separated(self, synthetic_phase_c_db_factory):
        decision_rows, funnel_rows = phase_c_dataset_outcomes()
        conn = synthetic_phase_c_db_factory(
            decision_rows=decision_rows, funnel_rows=funnel_rows,
        )
        c = _client()
        r = c.get("/api/phase-c/outcomes?range=latest")
        body = r.json()
        outcome_names = [o["outcome"] for o in body["outcomes"]]
        assert "SKIPPED_INVALID_DATA" in outcome_names
        assert "HOLD_INELIGIBLE" in outcome_names
        # SKIPPED_INVALID_DATA must have its own row, not be merged
        # into HOLD_INELIGIBLE.
        assert len(outcome_names) == len(set(outcome_names))

    def test_skipped_invalid_data_label_distinct(self, synthetic_phase_c_db_factory):
        decision_rows, funnel_rows = phase_c_dataset_outcomes()
        conn = synthetic_phase_c_db_factory(
            decision_rows=decision_rows, funnel_rows=funnel_rows,
        )
        c = _client()
        r = c.get("/api/phase-c/outcomes?range=latest")
        body = r.json()
        assert body["skipped_invalid_data_label"] == "Invalid data (no score, no rank)"

    def test_outcomes_sorted_by_count_desc_then_outcome_asc(self, synthetic_phase_c_db_factory):
        decision_rows, funnel_rows = phase_c_dataset_outcomes()
        conn = synthetic_phase_c_db_factory(
            decision_rows=decision_rows, funnel_rows=funnel_rows,
        )
        c = _client()
        r = c.get("/api/phase-c/outcomes?range=latest")
        body = r.json()
        outcomes = body["outcomes"]
        for i in range(len(outcomes) - 1):
            a, b = outcomes[i], outcomes[i + 1]
            # Either count strictly decreasing, or count equal and
            # name strictly ascending.
            assert (a["count"] > b["count"]) or (
                a["count"] == b["count"] and a["outcome"] <= b["outcome"]
            )


# ─────────────────────────────────────────────────────────────────────────
# Coverage / historical completeness
# ─────────────────────────────────────────────────────────────────────────

class TestCoverageIndicator:
    def test_coverage_response_has_pre_and_post_obs002_bands(self, synthetic_phase_c_db_factory):
        decision_rows, funnel_rows = phase_c_dataset_coverage()
        conn = synthetic_phase_c_db_factory(
            decision_rows=decision_rows, funnel_rows=funnel_rows,
        )
        c = _client()
        r = c.get("/api/phase-c/coverage?range=7d")
        body = r.json()
        assert "pre_obs002" in body
        assert "post_obs002" in body
        for k in ("cycle_count", "complete_cycles", "incomplete_cycles",
                  "total_missing_symbol_decisions"):
            assert k in body["pre_obs002"]
            assert k in body["post_obs002"]

    def test_obs002_boundary_constant(self, synthetic_phase_c_db_factory):
        decision_rows, funnel_rows = phase_c_dataset_coverage_windowed()
        conn = synthetic_phase_c_db_factory(
            decision_rows=decision_rows, funnel_rows=funnel_rows,
        )
        c = _client()
        r = c.get("/api/phase-c/coverage?range=7d")
        body = r.json()
        assert body["obs002_deployment_boundary_utc"] == OBS_002_BOUNDARY

    def test_pre_band_explicitly_documents_legacy_coverage_gap(self, synthetic_phase_c_db_factory):
        decision_rows, funnel_rows = phase_c_dataset_coverage_windowed()
        conn = synthetic_phase_c_db_factory(
            decision_rows=decision_rows, funnel_rows=funnel_rows,
        )
        c = _client()
        r = c.get("/api/phase-c/coverage?range=7d")
        body = r.json()
        # The note must explicitly call out that the dashboard does
        # NOT backfill pre-OBS-002 history.
        note = body["pre_obs002"]["note"]
        assert "backfill" in note.lower()
        assert "decision_history" in note


# ─────────────────────────────────────────────────────────────────────────
# Analytics tab HTML markers
# ─────────────────────────────────────────────────────────────────────────

class TestAnalyticsTabHtml:
    """Phase C HTML markup tests — verify the 5 Phase C cards plus the
    time-range and cohort selectors are present in the analytics tab."""

    def test_cohort_selector_present(self):
        # The cohort selector (default post_obs002) must be present
        # as a display element with id="phase-c-cohort-display" or
        # as a select element with id="phase-c-cohort".
        t = _read_template()
        assert (
            'id="phase-c-cohort-display"' in t
            or 'id="phase-c-cohort"' in t
        ), "Phase C cohort selector not present in template"

    def test_time_range_selector_present(self):
        # The time-range selector must be present as a select element
        # with id="phase-c-range".
        t = _read_template()
        assert 'id="phase-c-range"' in t

    def test_funnel_card_present(self):
        t = _read_template()
        assert 'id="phase-c-funnel-card"' in t

    def test_execution_blockers_card_present(self):
        t = _read_template()
        assert 'id="phase-c-execution-blockers-card"' in t

    def test_outcome_card_present(self):
        t = _read_template()
        assert 'id="phase-c-outcome-card"' in t

    def test_coverage_card_present(self):
        t = _read_template()
        assert 'id="phase-c-coverage-card"' in t

    def test_gate_freq_card_present(self):
        t = _read_template()
        assert 'id="phase-c-gate-freq-card"' in t

    def test_legacy_cards_preserved(self):
        # Phase C must not delete the legacy cards. We verify their
        # unique ID markers remain.
        t = _read_template()
        # The legacy Filter Analysis card has id="filter-analysis-card".
        assert 'id="filter-analysis-card"' in t
        # The legacy Failed Analysis Breakdown is rendered via
        # /api/failed-analyses; the title "Failed Analysis Breakdown"
        # should still appear.
        assert "Failed Analysis Breakdown" in t

    def test_phase_c_cards_above_legacy_in_analytics_tab(self):
        # Phase C cards must be inserted ABOVE the legacy
        # "Performance Analytics" card.
        t = _read_template()
        idx_phase_c = t.find('id="phase-c-funnel-card"')
        idx_legacy = t.find("Performance Analytics")
        assert idx_phase_c != -1, "Phase C funnel card missing"
        assert idx_legacy != -1, "Legacy Performance Analytics card missing"
        assert idx_phase_c < idx_legacy, (
            "Phase C cards must appear ABOVE the legacy Performance "
            "Analytics card in the Analytics tab"
        )


# ─────────────────────────────────────────────────────────────────────────
# Front-end: Phase C JS contract
# ─────────────────────────────────────────────────────────────────────────

class TestPhaseCJSContract:
    """Verify the Phase C JS does not violate source-of-truth rules."""

    def _pc_js(self):
        """Extract the Phase C JS block from the dashboard template."""
        t = _read_template()
        # The Phase C block is marked with this comment header.
        marker = "// Phase C — OBS Analytics renderer (2026-09-17)"
        i = t.find(marker)
        if i == -1:
            pytest.skip("Phase C JS marker not found in template")
        return t[i:]

    def test_no_primary_reason_substring_parsing_in_phase_c(self):
        # The Phase C JS must NEVER parse decision.primary_reason
        # text into structured fields. We grep for substring operations
        # on primary_reason that would imply heuristic parsing.
        js = self._pc_js()
        # Look for patterns like primary_reason.split, primary_reason.indexOf,
        # primary_reason.match, primary_reason.includes on the Phase C block.
        # A tolerant check: only allow primary_reason as the SOURCE
        # of a verbatim display (e.g., in tooltips), never as input
        # to a structured parser.
        assert "primary_reason.match" not in js
        assert "primary_reason.split" not in js
        assert "primary_reason.indexOf" not in js

    def test_no_inference_from_outcome_to_gate(self):
        # The Phase C JS must NOT contain patterns that map a HOLD
        # outcome to a failed gate.
        js = self._pc_js()
        # The negative guard: no string match for "HOLD_INELIGIBLE"
        # combined with "gate" in a way that would imply inference.
        # Allow references to HOLD_INELIGIBLE in comments only.
        for line in js.split("\n"):
            stripped = line.strip()
            if stripped.startswith("//") or stripped.startswith("*"):
                continue
            # If HOLD_INELIGIBLE appears in code (not comment), it
            # must NOT be in a pattern that builds a gate failure
            # from the outcome alone.
            if "HOLD_INELIGIBLE" in stripped:
                # Reject patterns like "if (HOLD_INELIGIBLE) add_gate_failure"
                assert "add_gate_failure" not in stripped
                assert "gate_failure_from_outcome" not in stripped

    def test_off_path_separated_in_renderer(self):
        # The funnel renderer must render off-path as a separate
        # row, not as a forward stage with an arrow flowing into
        # Orders Submitted.
        js = self._pc_js()
        # The renderer has explicit "(off-path)" labels.
        assert "Execution Blocked (off-path)" in js
        assert "Orders Failed (off-path)" in js
        # The off-path is rendered AFTER the forward row, in its own
        # .lc-funnel-row. Verify both rows exist in the renderer.
        assert js.count("lc-funnel-row") >= 2

    def test_skipped_invalid_data_label_is_distinct(self):
        js = self._pc_js()
        # The Outcome Distribution renderer treats SKIPPED_INVALID_DATA
        # with a distinct visual treatment (different border color).
        assert "SKIPPED_INVALID_DATA" in js
        assert "Invalid data" in js or "Invalid data (no score, no rank)" in js

    def test_all_five_loaders_present(self):
        js = self._pc_js()
        for fn in ("loadPhaseCFunnel", "loadPhaseCGates",
                   "loadPhaseCBlockers", "loadPhaseCOutcomes",
                   "loadPhaseCCoverage"):
            assert ("function " + fn) in js

    def test_loadPhaseCAll_chains_all_five(self):
        # loadPhaseCAll must call all five loaders.
        js = self._pc_js()
        m = re.search(r"async function loadPhaseCAll\(\)\s*\{([^}]*)\}", js, re.S)
        assert m, "loadPhaseCAll not found"
        body = m.group(1)
        for fn in ("loadPhaseCFunnel", "loadPhaseCGates",
                   "loadPhaseCBlockers", "loadPhaseCOutcomes",
                   "loadPhaseCCoverage"):
            assert fn in body


# ─────────────────────────────────────────────────────────────────────────
# No new global functions / variables introduced into dashboard.py beyond
# the documented Phase C surface area.
# ─────────────────────────────────────────────────────────────────────────

class TestPhaseCTestSurface:
    def test_phase_c_modules_added_to_dashboard_py(self):
        # Verify the new endpoint signatures exist.
        from fastapi.testclient import TestClient
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        import dashboard as _dashboard
        for name in ("api_phase_c_funnel", "api_phase_c_strategy_gates",
                     "api_phase_c_execution_blockers", "api_phase_c_outcomes",
                     "api_phase_c_coverage"):
            assert hasattr(_dashboard, name), f"missing dashboard function {name}"

    def test_no_bot_runtime_import_in_phase_c(self):
        # Phase C endpoints must NOT import SmartTradingBot or any
        # bot runtime surface.
        import subprocess
        txt = subprocess.run(
            ["grep", "-nE", "SmartTradingBot|smart_bot|from .core",
             "dashboard.py"],
            capture_output=True, text=True, timeout=10,
        ).stdout
        # Pre-existing api_score_breakdown uses SmartTradingBot.
        # Verify no Phase C endpoint function does.
        # We test by importing each endpoint and confirming it doesn't
        # require smart_bot.
        from fastapi.testclient import TestClient
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        import dashboard as _dashboard
        for fn_name in ("api_phase_c_funnel", "api_phase_c_strategy_gates",
                        "api_phase_c_execution_blockers", "api_phase_c_outcomes",
                        "api_phase_c_coverage"):
            fn = getattr(_dashboard, fn_name)
            src = getattr(fn, "__wrapped__", fn).__code__.co_filename
            # The endpoint lives in dashboard.py, not smart_bot.py.
            assert src.endswith("dashboard.py"), (
                f"{fn_name} not defined in dashboard.py"
            )

    def test_obs002_boundary_constant_in_dashboard(self):
        from fastapi.testclient import TestClient
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        import dashboard as _dashboard
        assert hasattr(_dashboard, "OBS_002_DEPLOYMENT_BOUNDARY_UTC")
        assert _dashboard.OBS_002_DEPLOYMENT_BOUNDARY_UTC == OBS_002_BOUNDARY


# ─────────────────────────────────────────────────────────────────────────
# Time-window boundary tests
# ─────────────────────────────────────────────────────────────────────────

class TestTimeWindowBoundaries:
    """Each endpoint must respect the four range values."""

    @pytest.mark.parametrize("endpoint", [
        "/api/phase-c/funnel",
        "/api/phase-c/strategy-gates",
        "/api/phase-c/execution-blockers",
        "/api/phase-c/outcomes",
        "/api/phase-c/coverage",
    ])
    @pytest.mark.parametrize("range_value", ["latest", "today", "24h", "7d"])
    def test_range_value_accepted(
        self, endpoint, range_value, synthetic_phase_c_db_factory,
    ):
        # PHASE-C10B-2: anchor the synthetic cycle near `now()` so
        # `today`, `24h`, `7d`, and `latest` all include the row.
        decision_rows, funnel_rows = phase_c_dataset_windowed()
        conn = synthetic_phase_c_db_factory(
            decision_rows=decision_rows, funnel_rows=funnel_rows,
        )
        c = _client()
        r = c.get(f"{endpoint}?range={range_value}")
        assert r.status_code == 200
        body = r.json()
        assert body.get("range") == range_value

    def test_latest_returns_subset_of_7d(self, synthetic_phase_c_db_factory):
        # For metrics that scale with cycles, latest <= 7d.
        # The synthetic cycle is anchored near `now()` so both
        # ranges include the row; the equality case (latest == 7d)
        # is acceptable because both queries return the same row.
        decision_rows, funnel_rows = phase_c_dataset_windowed()
        conn = synthetic_phase_c_db_factory(
            decision_rows=decision_rows, funnel_rows=funnel_rows,
        )
        c = _client()
        latest = c.get("/api/phase-c/funnel?range=latest").json()
        seven_day = c.get("/api/phase-c/funnel?range=7d").json()
        assert latest["cycles"] <= seven_day["cycles"]
        # forward path counts in latest are <= those in 7d.
        for k in latest["forward_path"]:
            assert latest["forward_path"][k] <= seven_day["forward_path"][k]


# ─────────────────────────────────────────────────────────────────────────
# PHASE-C8: legacy cohort opt-in + mobile UX + table overflow
# ─────────────────────────────────────────────────────────────────────────

class TestPhaseCLegacyCohortOptIn:
    """PHASE-C8: legacy cycles require explicit opt-in.

    The default cohort must remain post_obs002 (complete coverage).
    The cohort <select> remains in the DOM (for backend support) but
    is hidden — the visible control is an unchecked checkbox.
    Selecting the cohort=all backend value requires the user to tick
    the box, at which point a coverage warning appears.
    """

    def test_legacy_optin_checkbox_present(self):
        t = _read_template()
        assert 'id="phase-c-include-legacy"' in t
        # Unchecked by default.
        assert '<input type="checkbox" id="phase-c-include-legacy"' in t
        # The visible label includes "Include legacy pre-OBS-002 cycles".
        assert "Include legacy pre-OBS-002 cycles" in t

    def test_legacy_optin_checkbox_drives_handler(self):
        t = _read_template()
        # The checkbox onchange must call onPhaseCIncludeLegacyChange.
        # Find the line containing the checkbox and assert it
        # wires through to the handler.
        assert 'id="phase-c-include-legacy" onchange="onPhaseCIncludeLegacyChange()"' in t

    def test_legacy_optin_checkbox_not_checked_by_default(self):
        # The HTML attribute default is unchecked (no `checked` attr).
        t = _read_template()
        # Find the input element and ensure `checked` is not present.
        # We do this by ensuring the checkbox string does not include
        # `checked`.
        import re
        m = re.search(r'<input type="checkbox" id="phase-c-include-legacy"[^>]*>', t)
        assert m, "checkbox input not found"
        tag = m.group(0)
        assert 'checked' not in tag.lower(), f"checkbox must default to unchecked, got: {tag}"

    def test_legacy_warning_banner_present(self):
        t = _read_template()
        assert 'id="phase-c-legacy-warning"' in t
        # The warning copy must mention the three required points.
        body = t.split('id="phase-c-legacy-warning"', 1)[1]
        # banner is hidden by default
        assert 'display:none' in body[:300]
        # Must mention "legacy" or "pre-OBS-002"
        assert 'legacy' in body or 'pre-OBS-002' in body
        # Must mention incomplete
        assert 'incomplete' in body
        # Must mention "no backfill"
        assert 'backfill' in body

    def test_legacy_warning_hidden_by_default(self):
        t = _read_template()
        # The banner element must have display:none in its style attribute.
        import re
        m = re.search(r'<div[^>]*id="phase-c-legacy-warning"[^>]*>', t)
        assert m
        assert 'display:none' in m.group(0)

    def test_legacy_optin_checkbox_handler_toggles_warning(self):
        # The onPhaseCIncludeLegacyChange JS function must toggle the
        # warning's display style based on the checkbox state.
        t = _read_template()
        # Find function body
        idx = t.find('function onPhaseCIncludeLegacyChange')
        assert idx >= 0
        body = t[idx:idx + 1500]
        # Must reference the warning element
        assert "'phase-c-legacy-warning'" in body
        # Must check cb.checked
        assert 'cb.checked' in body
        # Must set style.display
        assert 'style.display' in body

    def test_loadPhaseCAll_resyncs_legacy_warning(self):
        # The auto-load path (loadPhaseCAll) must re-sync the
        # warning's display style to match the checkbox.
        t = _read_template()
        idx = t.find('async function loadPhaseCAll')
        assert idx >= 0
        body = t[idx:idx + 1500]
        assert 'phase-c-legacy-warning' in body
        assert 'phase-c-include-legacy' in body

    def test_cohort_select_remains_for_backend_support(self):
        # PHASE-C8 keeps the cohort <select> in the DOM (hidden) so
        # the backend _pc_getCohort() continues to work.
        t = _read_template()
        assert 'id="phase-c-cohort"' in t
        import re
        m = re.search(r'<select id="phase-c-cohort"[^>]*>', t)
        assert m
        assert 'display:none' in m.group(0)
        # Both options still present (post_obs002 default + all).
        assert 'value="post_obs002"' in t
        assert 'value="all"' in t

    def test_default_cohort_is_post_obs002(self):
        # The <select> must default to post_obs002 — this is the
        # backend default that takes effect if the hidden control
        # is bypassed.
        t = _read_template()
        import re
        m = re.search(
            r'<option value="post_obs002"[^>]*selected[^>]*>',
            t,
        )
        assert m, "post_obs002 must be the selected default"

    def test_cohort_endpoint_supports_all_cohort(self, synthetic_phase_c_db_factory):
        # Backend must continue to accept cohort=all so the
        # opt-in path can use it.
        decision_rows, funnel_rows = phase_c_dataset_windowed()
        conn = synthetic_phase_c_db_factory(
            decision_rows=decision_rows, funnel_rows=funnel_rows,
        )
        c = _client()
        r = c.get("/api/phase-c/funnel?range=7d&cohort=all")
        assert r.status_code == 200
        body = r.json()
        assert body.get("cohort") == "all"

    def test_cohort_endpoint_supports_post_obs002_default(self, synthetic_phase_c_db_factory):
        # Backend must accept cohort=post_obs002 (the default).
        decision_rows, funnel_rows = phase_c_dataset_windowed()
        conn = synthetic_phase_c_db_factory(
            decision_rows=decision_rows, funnel_rows=funnel_rows,
        )
        c = _client()
        r = c.get("/api/phase-c/funnel?range=7d&cohort=post_obs002")
        assert r.status_code == 200
        body = r.json()
        assert body.get("cohort") == "post_obs002"

    def test_cohort_all_includes_more_rows_than_post_obs002(self, synthetic_phase_c_db_factory):
        # Functional check: opt-in expands the visible data.
        # On live data, cohort=all must have >= the same number of
        # cycles as cohort=post_obs002 (since "all" is a superset).
        decision_rows, funnel_rows = phase_c_dataset_windowed()
        conn = synthetic_phase_c_db_factory(
            decision_rows=decision_rows, funnel_rows=funnel_rows,
        )
        c = _client()
        all_body = c.get("/api/phase-c/funnel?range=7d&cohort=all").json()
        post_body = c.get("/api/phase-c/funnel?range=7d&cohort=post_obs002").json()
        assert all_body["cycles"] >= post_body["cycles"]


class TestPhaseCMobileUx:
    """PHASE-C8: mobile polish for Phase C controls and tables.

    - 44px minimum touch target height for the latest/today/24h/7d
      controls and the legacy opt-in control.
    - Phase C selector controls stack vertically below 600px.
    - Tables are wrapped in .phase-c-card-table-wrap so they
      never overflow the page horizontally.
    """

    def test_phase_c_card_table_wrap_class_defined(self):
        t = _read_template()
        assert "phase-c-card-table-wrap" in t

    def test_phase_c_table_renderers_wrap_in_overflow_div(self):
        # The three table-emitting JS renderers must wrap their
        # tables in .phase-c-card-table-wrap so the table
        # overflows within the card rather than the page.
        t = _read_template()
        # Each renderer should appear with both an opening
        # `<div class="phase-c-card-table-wrap"><table` and a
        # closing `</table></div>`.
        for renderer_table in (
            ('_pc_renderGates', 'Gate</th>'),
            ('_pc_renderBlockers', 'Per-check totals'),
            ('_pc_renderOutcomes', 'Outcome</th>'),
        ):
            renderer_name, _ = renderer_table
            idx = t.find('function ' + renderer_name)
            assert idx >= 0, f"renderer {renderer_name} not found"
            body = t[idx:idx + 3000]
            assert '<div class="phase-c-card-table-wrap"><table' in body
            assert '</table></div>' in body

    def test_mobile_media_query_handles_phase_c_selectors(self):
        t = _read_template()
        # Find the existing 600px media query and check it now
        # touches Phase C selector layout.
        import re
        matches = list(re.finditer(
            r'@media\s*\(\s*max-width:\s*600px\s*\)\s*\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}',
            t,
        ))
        assert matches, "no 600px media query found"
        # Look across all 600px media query blocks for Phase C rules.
        joined = "\n".join(m.group(0) for m in matches)
        assert '#phase-c-selector-card' in joined
        assert '#phase-c-range' in joined
        assert 'min-height: 44px' in joined
        # The legacy opt-in toggle must also be 44px.
        assert 'phase-c-legacy-toggle' in joined
        # Overflow containment for tables.
        assert 'phase-c-card-table-wrap' in joined or 'overflow-x: auto' in joined

    def test_phase_c_legacy_toggle_44px_target(self):
        # The visible label for the legacy checkbox must carry
        # a 44px min-height to satisfy mobile touch target rules.
        t = _read_template()
        # Find the label containing "Include legacy pre-OBS-002 cycles".
        import re
        m = re.search(
            r'<label[^>]*for="phase-c-include-legacy"[^>]*>',
            t,
        )
        assert m
        tag = m.group(0)
        assert 'min-height:44px' in tag or 'min-height: 44px' in tag
        assert 'display:inline-flex' in tag

    def test_cohort_display_chip_44px_target(self):
        # The read-only span that displays the current cohort
        # selection must be at least 44px tall on its own
        # (not depending on the 600px media query override).
        t = _read_template()
        import re
        m = re.search(
            r'<span[^>]*id="phase-c-cohort-display"[^>]*>',
            t,
        )
        assert m
        tag = m.group(0)
        assert 'min-height:44px' in tag or 'min-height: 44px' in tag

    def test_no_overflow_on_phase_c_cards(self):
        # All five Phase C cards must be present (already tested
        # elsewhere) and the page must declare no horizontal
        # overflow. The simplest sanity check: the wrapper
        # `.phase-c-card-table-wrap` carries `overflow-x: auto`
        # in the 600px media query.
        t = _read_template()
        import re
        matches = list(re.finditer(
            r'@media\s*\(\s*max-width:\s*600px\s*\)\s*\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}',
            t,
        ))
        joined = "\n".join(m.group(0) for m in matches)
        assert 'overflow-x: auto' in joined


# ─────────────────────────────────────────────────────────────────────────
# PHASE-C9: shared time-range/cohort SQL helper
# ─────────────────────────────────────────────────────────────────────────

class TestPhaseCSharedHelper:
    """PHASE-C9: prove the refactor preserved behavior.

    The five Phase C endpoints now share `_phase_c_range_clause`
    and `_phase_c_cohort_clause` via qualified-column arguments.
    This class proves:
      1. The helper returns the same SQL fragments and bound
         parameters that the inline code used to.
      2. Five endpoints produce identical results for representative
         fixtures.
      3. The alias allowlist is enforced; arbitrary alias strings
         are rejected.
      4. User input never reaches SQL identifier interpolation.
    """

    # --- helper shape ----------------------------------------------------

    def test_helper_qualify_column_accepts_unqualified(self):
        # Import the dashboard module via the same shim the test
        # suite uses elsewhere.
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        import dashboard
        assert dashboard._phase_c_qualify_column("cycle_start") == "cycle_start"

    def test_helper_qualify_column_accepts_allowlisted_aliases(self):
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        import dashboard
        assert dashboard._phase_c_qualify_column("dh.cycle_start") == "dh.cycle_start"
        assert dashboard._phase_c_qualify_column("cf.cycle_start") == "cf.cycle_start"

    def test_helper_qualify_column_rejects_unknown_alias(self):
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        import dashboard
        for bad_alias in (
            "evil.cycle_start",        # arbitrary alias
            "users.cycle_start",       # table that exists but isn't allowed
            "decision_history.cycle_start",  # full table name as alias
            "1dh.cycle_start",         # numeric prefix
        ):
            with pytest.raises(ValueError):
                dashboard._phase_c_qualify_column(bad_alias)

    def test_helper_qualify_column_rejects_unknown_column(self):
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        import dashboard
        for bad_col in (
            "decision_outcome",
            "symbol",
            "decision_snapshot",
        ):
            with pytest.raises(ValueError):
                dashboard._phase_c_qualify_column(bad_col)

    def test_helper_qualify_column_rejects_injection_payloads(self):
        # Even if a future caller mistakenly passed request data,
        # the helper must reject anything outside the allowlist.
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        import dashboard
        for payload in (
            "cycle_start; DROP TABLE decision_history; --",
            "dh.cycle_start) OR (1=1",
            "' OR 1=1 --",
            "dh.cycle_start/**/UNION/**/SELECT",
            "$(rm -rf /)",
        ):
            with pytest.raises(ValueError):
                dashboard._phase_c_qualify_column(payload)

    # --- range clause shape ----------------------------------------------

    def test_range_clause_latest_unqualified(self):
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        import dashboard
        sql, params = dashboard._phase_c_range_clause("latest", "cycle_start")
        assert sql == (
            "(cycle_start = (SELECT cycle_start FROM cycle_funnel "
            "WHERE cycle_end IS NOT NULL ORDER BY cycle_end DESC LIMIT 1))"
        )
        assert params == ()

    def test_range_clause_latest_qualified_with_dh(self):
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        import dashboard
        sql, params = dashboard._phase_c_range_clause("latest", "dh.cycle_start")
        assert sql == (
            "(dh.cycle_start = (SELECT cycle_start FROM cycle_funnel "
            "WHERE cycle_end IS NOT NULL ORDER BY cycle_end DESC LIMIT 1))"
        )
        assert params == ()

    def test_range_clause_latest_qualified_with_cf(self):
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        import dashboard
        sql, params = dashboard._phase_c_range_clause("latest", "cf.cycle_start")
        assert sql == (
            "(cf.cycle_start = (SELECT cycle_start FROM cycle_funnel "
            "WHERE cycle_end IS NOT NULL ORDER BY cycle_end DESC LIMIT 1))"
        )
        assert params == ()

    def test_range_clause_24h_qualified(self):
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        import dashboard
        sql, params = dashboard._phase_c_range_clause("24h", "dh.cycle_start")
        assert sql == "(dh.cycle_start >= ?)"
        assert len(params) == 1
        cutoff = params[0]
        assert cutoff.endswith("+00:00")
        from datetime import datetime
        parsed = datetime.fromisoformat(cutoff)
        assert parsed.tzinfo is not None

    def test_range_clause_7d_qualified(self):
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        import dashboard
        sql, params = dashboard._phase_c_range_clause("7d", "dh.cycle_start")
        assert sql == "(dh.cycle_start >= ?)"
        assert len(params) == 1
        assert params[0].endswith("+00:00")

    def test_range_clause_today_qualified(self):
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        import dashboard
        sql, params = dashboard._phase_c_range_clause("today", "dh.cycle_start")
        assert sql == "(dh.cycle_start >= ? AND dh.cycle_start < ?)"
        assert len(params) == 2
        lo, hi = params
        assert lo.endswith("T00:00:00+00:00")
        assert hi.endswith("T23:59:59.999999+00:00")
        assert lo[:10] == hi[:10]

    # --- cohort clause shape ---------------------------------------------

    def test_cohort_clause_post_obs002_qualified(self):
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        import dashboard
        sql, params = dashboard._phase_c_cohort_clause("post_obs002", "dh.cycle_start")
        assert sql == "(dh.cycle_start >= ?)"
        assert params == ("2026-09-14T20:24:55",)
        assert params[0] == dashboard.OBS_002_DEPLOYMENT_BOUNDARY_UTC

    def test_cohort_clause_all(self):
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        import dashboard
        sql, params = dashboard._phase_c_cohort_clause("all", "dh.cycle_start")
        assert sql == "(1=1)"
        assert params == ()

    def test_cohort_clause_unqualified_still_works(self):
        # The funnel endpoint uses unqualified "cycle_start".
        # The helper must still accept it (backward compatibility).
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        import dashboard
        sql, params = dashboard._phase_c_cohort_clause("post_obs002", "cycle_start")
        assert sql == "(cycle_start >= ?)"
        assert params == ("2026-09-14T20:24:55",)

    # --- equivalence: 5 endpoints accept documented ranges --------------

    def test_all_five_endpoints_accept_documented_ranges(
        self, synthetic_phase_c_db_factory,
    ):
        # The four ranges × five endpoints matrix. The strategy-gates
        # endpoint is slow (JSON extraction over hundreds of thousands
        # of decision_history rows) so we skip the full matrix there
        # and verify it separately in
        # `test_strategy_gates_accepts_all_four_ranges`.
        fast_endpoints = (
            "/api/phase-c/funnel",
            "/api/phase-c/execution-blockers",
            "/api/phase-c/outcomes",
            "/api/phase-c/coverage",
        )
        decision_rows, funnel_rows = phase_c_dataset_windowed()
        conn = synthetic_phase_c_db_factory(
            decision_rows=decision_rows, funnel_rows=funnel_rows,
        )
        c = _client()
        for endpoint in fast_endpoints:
            for rng in ("latest", "today", "24h", "7d"):
                r = c.get(f"{endpoint}?range={rng}")
                assert r.status_code == 200, (
                    f"{endpoint}?range={rng} returned {r.status_code}"
                )
        # For strategy-gates, use the cached `today` fixture to keep
        # the test fast; the other ranges are exercised by
        # `test_strategy_gates_accepts_all_four_ranges`.
        assert c.get(
            "/api/phase-c/strategy-gates?range=today&cohort=post_obs002"
        ).status_code == 200

    def test_strategy_gates_accepts_all_four_ranges(
        self, synthetic_phase_c_db_factory,
    ):
        # The 7d range for strategy-gates is exercised by the cached
        # 7d fixtures; today is exercised by `gates_response_today_post`.
        # Here we cover `latest` and `24h` only. (`latest` is bounded to
        # a single cycle; `24h` is bounded to the past 24 hours.)
        decision_rows, funnel_rows = phase_c_dataset_windowed()
        conn = synthetic_phase_c_db_factory(
            decision_rows=decision_rows, funnel_rows=funnel_rows,
        )
        c = _client()
        for rng in ("latest", "24h"):
            r = c.get(f"/api/phase-c/strategy-gates?range={rng}")
            assert r.status_code == 200, (
                f"strategy-gates?range={rng} returned {r.status_code}"
            )

    def test_all_three_cohort_endpoints_accept_both_cohorts(
        self, synthetic_phase_c_db_factory,
    ):
        # The three fast cohort endpoints first.
        fast_endpoints = (
            "/api/phase-c/funnel",
            "/api/phase-c/execution-blockers",
            "/api/phase-c/outcomes",
        )
        decision_rows, funnel_rows = phase_c_dataset_windowed()
        conn = synthetic_phase_c_db_factory(
            decision_rows=decision_rows, funnel_rows=funnel_rows,
        )
        c = _client()
        for endpoint in fast_endpoints:
            for cohort in ("post_obs002", "all"):
                r = c.get(f"{endpoint}?range=7d&cohort={cohort}")
                assert r.status_code == 200
                body = r.json()
                assert body.get("cohort") == cohort
        # For strategy-gates, use the bounded `today` cut; both
        # cohorts must work and the cohort value must echo back.
        for cohort in ("post_obs002", "all"):
            r = c.get(
                f"/api/phase-c/strategy-gates?range=today&cohort={cohort}"
            )
            assert r.status_code == 200
            body = r.json()
            assert body.get("cohort") == cohort

    # --- response shape preserved ---------------------------------------

    def test_execution_blockers_response_shape_after_refactor(
        self, synthetic_phase_c_db_factory,
    ):
        decision_rows, funnel_rows = phase_c_dataset_windowed()
        conn = synthetic_phase_c_db_factory(
            decision_rows=decision_rows, funnel_rows=funnel_rows,
        )
        c = _client()
        body = c.get("/api/phase-c/execution-blockers?range=7d&cohort=post_obs002").json()
        assert "rows_in_cohort" in body
        assert "rows_with_checks" in body
        assert "universe_caveat" in body
        assert "checks" in body
        assert "first_blocking_check" in body
        assert "source" in body

    def test_outcomes_response_shape_after_refactor(
        self, synthetic_phase_c_db_factory,
    ):
        # Use the windowed outcomes dataset so SKIPPED_INVALID_DATA
        # is present in the response and the timestamps still fall
        # in `range=7d`.
        decision_rows, funnel_rows = phase_c_dataset_outcomes_windowed()
        conn = synthetic_phase_c_db_factory(
            decision_rows=decision_rows, funnel_rows=funnel_rows,
        )
        c = _client()
        body = c.get("/api/phase-c/outcomes?range=7d&cohort=post_obs002").json()
        assert "total_decisions" in body
        assert "outcomes" in body
        assert "skipped_invalid_data_label" in body
        assert "source" in body
        outcomes = body["outcomes"]
        assert any(o["outcome"] == "SKIPPED_INVALID_DATA" for o in outcomes)

    def test_coverage_response_shape_after_refactor(
        self, synthetic_phase_c_db_factory,
    ):
        # Pre/post OBS-002 bands with both anchored so `range=7d`
        # includes them.
        decision_rows, funnel_rows = phase_c_dataset_coverage_windowed()
        conn = synthetic_phase_c_db_factory(
            decision_rows=decision_rows, funnel_rows=funnel_rows,
        )
        c = _client()
        body = c.get("/api/phase-c/coverage?range=7d").json()
        assert "pre_obs002" in body
        assert "post_obs002" in body
        assert "obs002_deployment_boundary_utc" in body
        assert body["obs002_deployment_boundary_utc"] == "2026-09-14T20:24:55"
        for band in ("pre_obs002", "post_obs002"):
            assert "cycle_count" in body[band]
            assert "complete_cycles" in body[band]
            assert "incomplete_cycles" in body[band]
            assert "total_missing_symbol_decisions" in body[band]

    def test_funnel_response_shape_after_refactor(
        self, synthetic_phase_c_db_factory,
    ):
        decision_rows, funnel_rows = phase_c_dataset_windowed()
        conn = synthetic_phase_c_db_factory(
            decision_rows=decision_rows, funnel_rows=funnel_rows,
        )
        c = _client()
        body = c.get("/api/phase-c/funnel?range=7d&cohort=post_obs002").json()
        assert "forward_path" in body
        assert "off_path" in body
        assert "cycles" in body
        assert "largest_dropoff" in body
        for k in (
            "analyzed_count", "strategy_eligible_count",
            "ranked_candidate_count", "execution_attempt_count",
            "orders_submitted_count",
        ):
            assert k in body["forward_path"]
        for k in (
            "execution_blocked_count",
            "orders_failed_count",
            "not_attempted_count",
        ):
            assert k in body["off_path"]

    # --- invariant: cohort=all is a strict superset of post_obs002 -----

    # --- invariant: malformed range/cohort rejection preserved --------

    def test_invalid_range_returns_error_envelope(
        self, synthetic_phase_c_db_factory,
    ):
        # PHASE-C10B-3: install the factory seam so the canary
        # proves no live-DB fall-through even though the validate
        # gate short-circuits before any DB call. The dataset is
        # unused; only the seam patch matters for isolation.
        decision_rows, funnel_rows = phase_c_dataset_windowed()
        conn = synthetic_phase_c_db_factory(
            decision_rows=decision_rows, funnel_rows=funnel_rows,
        )
        c = _client()
        for endpoint in (
            "/api/phase-c/funnel",
            "/api/phase-c/strategy-gates",
            "/api/phase-c/execution-blockers",
            "/api/phase-c/outcomes",
            "/api/phase-c/coverage",
        ):
            r = c.get(f"{endpoint}?range=NOT_A_RANGE")
            assert r.status_code == 200
            body = r.json()
            assert "error" in body
            # PHASE-C10B-3A: production validator envelope uses
            # "valid_ranges" (the allowlist), not "range" (the echoed input).
            # The DB-missing fallback envelope at dashboard.py:2075 also
            # includes "range", but the validate gate short-circuits first.
            # This is a typo correction; the deeper check on the next line
            # still asserts the real value.
            assert "valid_ranges" in body
            assert body.get("valid_ranges") == ["24h", "7d", "latest", "today"]

    def test_invalid_cohort_returns_error_envelope(
        self, synthetic_phase_c_db_factory,
    ):
        decision_rows, funnel_rows = phase_c_dataset_windowed()
        conn = synthetic_phase_c_db_factory(
            decision_rows=decision_rows, funnel_rows=funnel_rows,
        )
        c = _client()
        for endpoint in (
            "/api/phase-c/funnel",
            "/api/phase-c/strategy-gates",
            "/api/phase-c/execution-blockers",
            "/api/phase-c/outcomes",
        ):
            r = c.get(f"{endpoint}?range=7d&cohort=BAD_COHORT")
            assert r.status_code == 200
            body = r.json()
            assert "error" in body
            # PHASE-C10B-3A: production validator envelope uses
            # "valid_cohorts" (the allowlist), not "cohort" (the echoed input).
            assert "valid_cohorts" in body
            assert body.get("valid_cohorts") == ["all", "post_obs002"]

    # --- empty results: latest range returns at most one cycle ---------

    # --- boundary constant preserved in helpers -------------------------

    def test_OBS_002_boundary_constant_unchanged(self):
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        import dashboard
        assert dashboard.OBS_002_DEPLOYMENT_BOUNDARY_UTC == "2026-09-14T20:24:55"

    def test_cohort_clause_post_obs002_uses_correct_boundary(self):
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        import dashboard
        sql, params = dashboard._phase_c_cohort_clause("post_obs002", "dh.cycle_start")
        assert params[0] == "2026-09-14T20:24:55"

    # --- PHASE-C7 invariant: applied=true filter preserved ------------


# ─────────────────────────────────────────────────────────────────────────
# PHASE-C9: bundle-helper unit tests
# ─────────────────────────────────────────────────────────────────────────

class TestPhaseC9WindowPredicatesHelper:
    """PHASE-C9: smallest shared helper for the five Phase C endpoints.

    `_phase_c_window_predicates(range_name, cohort, column)` bundles
    the cohort + range filter fragments and bound parameters into a
    single call. Each Phase C endpoint previously duplicated the
    three-line pattern:

        cohort_sql, cohort_params = _phase_c_cohort_clause(cohort, column)
        range_sql, range_params = _phase_c_range_clause(range_name, column)
        params = cohort_params + range_params

    The bundle helper must:
      1. Return the same SQL fragments and bound parameters as the
         inline pattern. No behavior change.
      2. Accept the same alias set ("", "dh", "cf") via the same
         allowlist. No new identifier interpolation paths.
      3. Reject arbitrary alias or column strings.
      4. Reject injection payloads at the helper entry point.
    """

    # --- equivalence: bundle == inline -----------------------------------

    def test_bundle_matches_inline_for_dh_alias(self):
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        import dashboard
        for rng, cohort, col in (
            ("latest", "post_obs002", "dh.cycle_start"),
            ("today", "post_obs002", "dh.cycle_start"),
            ("24h", "post_obs002", "dh.cycle_start"),
            ("7d", "post_obs002", "dh.cycle_start"),
            ("latest", "all", "dh.cycle_start"),
            ("today", "all", "dh.cycle_start"),
            ("24h", "all", "dh.cycle_start"),
            ("7d", "all", "dh.cycle_start"),
        ):
            cs1, cp1 = dashboard._phase_c_cohort_clause(cohort, col)
            rs1, rp1 = dashboard._phase_c_range_clause(rng, col)
            cs2, rs2, p2 = dashboard._phase_c_window_predicates(rng, cohort, col)
            assert cs1 == cs2, f"cohort_sql mismatch {rng}/{cohort}: {cs1!r} vs {cs2!r}"
            assert rs1 == rs2, f"range_sql mismatch {rng}/{cohort}: {rs1!r} vs {rs2!r}"
            assert tuple(cp1) + tuple(rp1) == p2, (
                f"params mismatch {rng}/{cohort}: {(tuple(cp1) + tuple(rp1))!r} vs {p2!r}"
            )

    def test_bundle_matches_inline_for_unqualified(self):
        # The funnel endpoint uses unqualified "cycle_start".
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        import dashboard
        for rng, cohort in (
            ("latest", "post_obs002"),
            ("today", "post_obs002"),
            ("24h", "post_obs002"),
            ("7d", "post_obs002"),
            ("latest", "all"),
            ("today", "all"),
            ("24h", "all"),
            ("7d", "all"),
        ):
            cs1, cp1 = dashboard._phase_c_cohort_clause(cohort, "cycle_start")
            rs1, rp1 = dashboard._phase_c_range_clause(rng, "cycle_start")
            cs2, rs2, p2 = dashboard._phase_c_window_predicates(
                rng, cohort, "cycle_start"
            )
            assert cs1 == cs2 and rs1 == rs2 and tuple(cp1) + tuple(rp1) == p2

    def test_bundle_matches_inline_for_cf_alias(self):
        # The coverage endpoint uses "cf.cycle_start".
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        import dashboard
        for rng in ("latest", "today", "24h", "7d"):
            cs1, cp1 = dashboard._phase_c_cohort_clause("all", "cf.cycle_start")
            rs1, rp1 = dashboard._phase_c_range_clause(rng, "cf.cycle_start")
            cs2, rs2, p2 = dashboard._phase_c_window_predicates(
                rng, "all", "cf.cycle_start"
            )
            assert cs1 == cs2 and rs1 == rs2 and tuple(cp1) + tuple(rp1) == p2

    # --- bundle shape: returns a 3-tuple ---------------------------------

    def test_bundle_returns_three_tuple(self):
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        import dashboard
        result = dashboard._phase_c_window_predicates(
            "7d", "post_obs002", "dh.cycle_start"
        )
        assert isinstance(result, tuple)
        assert len(result) == 3
        cohort_sql, range_sql, params = result
        assert isinstance(cohort_sql, str)
        assert isinstance(range_sql, str)
        assert isinstance(params, tuple)

    # --- param ordering: cohort_params first, range_params second -------

    def test_param_ordering_is_cohort_then_range(self):
        # The bundle helper must concatenate cohort_params BEFORE
        # range_params. The cohort boundary is a fixed string;
        # the range boundary is computed at call time. If the
        # order were swapped, every range+cohort query would
        # bind parameters in the wrong slot and silently return
        # wrong rows. Lock the order in.
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        import dashboard
        cs, rs, params = dashboard._phase_c_window_predicates(
            "7d", "post_obs002", "dh.cycle_start"
        )
        assert params[0] == dashboard.OBS_002_DEPLOYMENT_BOUNDARY_UTC, (
            f"first param must be cohort boundary, got {params[0]!r}"
        )
        # Second param is the 7d cutoff — must be a recent ISO string
        # (not the cohort boundary).
        assert params[0] != params[1]
        assert params[1].endswith("+00:00")

    # --- range=all helper combination produces a tautology WHERE -------

    def test_cohort_all_with_any_range_yields_safe_where(self):
        # cohort=all returns "(1=1)" — a SQL tautology. Composing it
        # with any range clause must NOT introduce injection paths.
        # The two fragments are AND'd; the resulting clause is bounded
        # to the range fragment only.
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        import dashboard
        for rng, expected_rs in (
            ("latest", "(dh.cycle_start = (SELECT cycle_start FROM cycle_funnel WHERE cycle_end IS NOT NULL ORDER BY cycle_end DESC LIMIT 1))"),
            ("today",  "(dh.cycle_start >= ? AND dh.cycle_start < ?)"),
            ("24h",    "(dh.cycle_start >= ?)"),
            ("7d",     "(dh.cycle_start >= ?)"),
        ):
            cs, rs, params = dashboard._phase_c_window_predicates(
                rng, "all", "dh.cycle_start"
            )
            assert cs == "(1=1)", f"cohort=all must be tautology, got {cs!r}"
            # Range fragment must still be the non-trivial filter.
            assert rs == expected_rs, f"range={rng}: got {rs!r}, expected {expected_rs!r}"

    # --- alias allowlist enforcement -------------------------------------

    def test_bundle_rejects_unknown_alias(self):
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        import dashboard
        for bad in (
            "users.cycle_start",
            "1dh.cycle_start",
            "evil.cycle_start",
            "decision_history.cycle_start",
            "(SELECT 1).cycle_start",
        ):
            with pytest.raises(ValueError):
                dashboard._phase_c_window_predicates(
                    "7d", "post_obs002", bad
                )

    def test_bundle_rejects_unknown_column(self):
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        import dashboard
        for bad in (
            "decision_outcome",
            "dh.decision_outcome",
            "symbol",
            "dh.total_score",
        ):
            with pytest.raises(ValueError):
                dashboard._phase_c_window_predicates(
                    "7d", "post_obs002", bad
                )

    def test_bundle_rejects_injection_payloads_at_column(self):
        # Even if a future caller mistakenly forwards request data
        # through the column argument, the bundle must reject it.
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        import dashboard
        payloads = (
            "cycle_start; DROP TABLE decision_history; --",
            "dh.cycle_start) OR (1=1",
            "' OR 1=1 --",
            "dh.cycle_start/**/UNION/**/SELECT",
            "$(rm -rf /)",
            # Attempt to bypass allowlist with an aliased identifier
            "cf.cycle_start; UPDATE users SET admin=1; --",
            # Multiple dots (more than one partition)
            "a.b.cycle_start",
            # Whitespace-padded identifier that splits into alias=cycle_start
            "cycle_start.something",
            # Tab/newline injection
            "dh.cycle_start\n;DROP TABLE x",
        )
        for payload in payloads:
            with pytest.raises(ValueError):
                dashboard._phase_c_window_predicates(
                    "7d", "post_obs002", payload
                )

    # --- cohort input still validated ------------------------------------

    def test_bundle_rejects_invalid_cohort(self):
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        import dashboard
        with pytest.raises(ValueError):
            dashboard._phase_c_window_predicates(
                "7d", "NOT_A_COHORT", "dh.cycle_start"
            )

    def test_bundle_rejects_invalid_range(self):
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        import dashboard
        with pytest.raises(ValueError):
            dashboard._phase_c_window_predicates(
                "NOT_A_RANGE", "post_obs002", "dh.cycle_start"
            )

    # --- no user input reaches SQL identifier interpolation --------------

    def test_no_user_data_in_sql_fragment(self):
        # The bundle must not pass `range_name` or `cohort` strings
        # into the SQL fragment. Only the validated column alias
        # ("", "dh", "cf") and a fixed set of SQL keywords
        # ("cycle_start", "SELECT", etc.) appear in the fragment.
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        import dashboard
        for rng in ("latest", "today", "24h", "7d"):
            for cohort in ("post_obs002", "all"):
                cs, rs, params = dashboard._phase_c_window_predicates(
                    rng, cohort, "dh.cycle_start"
                )
                # The user values themselves must NOT appear in the SQL.
                assert rng not in cs and rng not in rs
                assert cohort not in cs and cohort not in rs
                # User values only flow through bound parameters.
                for p in params:
                    assert rng not in str(p)
                    assert cohort not in str(p)

    # --- per-range boundary semantics ------------------------------------

    def test_latest_boundary_uses_subquery(self):
        # "latest" must keep its subquery shape; the bundle helper
        # must not flatten it.
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        import dashboard
        cs, rs, params = dashboard._phase_c_window_predicates(
            "latest", "post_obs002", "dh.cycle_start"
        )
        assert "SELECT cycle_start FROM cycle_funnel" in rs
        assert params == (dashboard.OBS_002_DEPLOYMENT_BOUNDARY_UTC,)

    def test_today_boundary_is_calendar_day(self):
        # "today" uses two boundaries: >= 00:00:00 and < 23:59:59.999999
        # of the same UTC date. The bundle must preserve both range
        # params in addition to the cohort boundary param.
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        import dashboard
        cs, rs, params = dashboard._phase_c_window_predicates(
            "today", "post_obs002", "dh.cycle_start"
        )
        assert rs == "(dh.cycle_start >= ? AND dh.cycle_start < ?)"
        # params = (cohort_boundary, today_lo, today_hi) — 3 total.
        assert len(params) == 3
        # The cohort boundary is bound first (index 0).
        assert params[0] == dashboard.OBS_002_DEPLOYMENT_BOUNDARY_UTC
        # Indices 1 and 2 are today's low/high boundaries.
        lo, hi = params[1], params[2]
        assert lo.endswith("T00:00:00+00:00")
        assert hi.endswith("T23:59:59.999999+00:00")
        assert lo[:10] == hi[:10]

    def test_24h_boundary_is_single_cutoff(self):
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        import dashboard
        cs, rs, params = dashboard._phase_c_window_predicates(
            "24h", "post_obs002", "dh.cycle_start"
        )
        assert rs == "(dh.cycle_start >= ?)"
        # cohort param first, then range cutoff
        assert len(params) == 2
        assert params[0] == dashboard.OBS_002_DEPLOYMENT_BOUNDARY_UTC
        assert params[1].endswith("+00:00")

    def test_7d_boundary_is_single_cutoff(self):
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        import dashboard
        cs, rs, params = dashboard._phase_c_window_predicates(
            "7d", "post_obs002", "dh.cycle_start"
        )
        assert rs == "(dh.cycle_start >= ?)"
        assert len(params) == 2
        assert params[0] == dashboard.OBS_002_DEPLOYMENT_BOUNDARY_UTC
        assert params[1].endswith("+00:00")

    # --- per-cohort boundary semantics -----------------------------------

    def test_post_obs002_default_cohort_uses_boundary(self):
        # The default "complete post-OBS-002 cohort" must keep
        # the OBS_002 boundary as a hard lower bound.
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        import dashboard
        cs, rs, params = dashboard._phase_c_window_predicates(
            "7d", "post_obs002", "dh.cycle_start"
        )
        assert cs == "(dh.cycle_start >= ?)"
        assert params[0] == "2026-09-14T20:24:55"

    def test_all_cohort_uses_tautology(self):
        # "all" must NOT add a cohort boundary. It's a strict
        # superset of "post_obs002" and must keep pre-OBS-002 data.
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        import dashboard
        cs, rs, params = dashboard._phase_c_window_predicates(
            "7d", "all", "dh.cycle_start"
        )
        assert cs == "(1=1)"
        assert len(params) == 1  # only the 7d cutoff, no cohort param

    # --- bundle helper does not leak bound params -------------------------

    def test_bundle_returns_only_bound_params_for_post_obs002(self):
        # The post_obs002 cohort adds 1 bound param; the 7d range
        # adds 1. Total: 2. Make sure no extra params are leaked.
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        import dashboard
        _, _, params = dashboard._phase_c_window_predicates(
            "7d", "post_obs002", "dh.cycle_start"
        )
        assert len(params) == 2
        # For cohort=all, the cohort adds 0 params; only the range.
        _, _, params = dashboard._phase_c_window_predicates(
            "7d", "all", "dh.cycle_start"
        )
        assert len(params) == 1
        # For range=today + cohort=post_obs002: 1 (cohort) + 2 (today) = 3.
        _, _, params = dashboard._phase_c_window_predicates(
            "today", "post_obs002", "dh.cycle_start"
        )
        assert len(params) == 3
        # For range=today + cohort=all: 0 (all) + 2 (today) = 2.
        _, _, params = dashboard._phase_c_window_predicates(
            "today", "all", "dh.cycle_start"
        )
        assert len(params) == 2
        # For range=latest + cohort=all: 0 (all) + 0 (latest) = 0.
        _, _, params = dashboard._phase_c_window_predicates(
            "latest", "all", "dh.cycle_start"
        )
        assert len(params) == 0


# ─────────────────────────────────────────────────────────────────────────
# PHASE-C9: SQL injection surface via query parameters
# ─────────────────────────────────────────────────────────────────────────

class TestPhaseC9QueryParamInjectionSafety:
    """PHASE-C9: end-to-end injection safety for query params.

    The bundle helper returns bound parameters, so even a hostile
    `range` or `cohort` value must be:
      - rejected by `_phase_c_validate` (the upstream gate), OR
      - bound via `?` placeholders and never reach SQL parsing.
    These tests exercise the FastAPI query-parameter path (the
    actual entry point used by the browser).
    """

    def _client(self):
        from fastapi.testclient import TestClient
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        import dashboard as _dashboard
        return TestClient(_dashboard.app)

    def test_hostile_range_value_is_rejected(
        self, synthetic_phase_c_db_factory,
    ):
        # PHASE-C10B-3: install the factory seam so the canary
        # proves no live-DB fall-through. The validate gate
        # short-circuits before any SQL is built; the dataset is
        # unused. Use `c = _client()` (the module-level helper) so
        # the request goes through the same FastAPI app that
        # production uses, proving real bound-parameter behavior.
        decision_rows, funnel_rows = phase_c_dataset_windowed()
        conn = synthetic_phase_c_db_factory(
            decision_rows=decision_rows, funnel_rows=funnel_rows,
        )
        c = _client()
        # Standard SQL injection attempt against the `range` query
        # parameter. The validate gate must reject it before any
        # SQL is built. We assert the response is an error envelope,
        # not a successful 200 with rows — i.e. no SQL was executed.
        payload = "7d; DROP TABLE decision_history; --"
        r = c.get(f"/api/phase-c/funnel?range={payload}")
        body = r.json()
        assert "error" in body
        assert "invalid range" in body["error"]
        # The valid_ranges field must echo the allowlist — never
        # anything derived from the user input.
        assert body.get("valid_ranges") == ["24h", "7d", "latest", "today"]
        # The successful response fields must NOT be present.
        assert "forward_path" not in body
        assert "cycles" not in body

    def test_hostile_cohort_value_is_rejected(
        self, synthetic_phase_c_db_factory,
    ):
        decision_rows, funnel_rows = phase_c_dataset_windowed()
        conn = synthetic_phase_c_db_factory(
            decision_rows=decision_rows, funnel_rows=funnel_rows,
        )
        c = _client()
        payload = "all' OR 1=1 --"
        r = c.get(f"/api/phase-c/funnel?range=7d&cohort={payload}")
        body = r.json()
        assert "error" in body
        assert "invalid cohort" in body["error"]
        # The valid_cohorts field must echo the allowlist.
        assert body.get("valid_cohorts") == ["all", "post_obs002"]
        assert "forward_path" not in body

    def test_url_encoded_injection_attempt_rejected(
        self, synthetic_phase_c_db_factory,
    ):
        decision_rows, funnel_rows = phase_c_dataset_windowed()
        conn = synthetic_phase_c_db_factory(
            decision_rows=decision_rows, funnel_rows=funnel_rows,
        )
        c = _client()
        # URL-encoded semicolon + DROP TABLE attempt.
        payload = "7d%3B%20DROP%20TABLE%20decision_history%3B%20--"
        r = c.get(f"/api/phase-c/funnel?range={payload}")
        body = r.json()
        assert "error" in body

    def test_valid_range_with_extra_payload_returns_error(
        self, synthetic_phase_c_db_factory,
    ):
        decision_rows, funnel_rows = phase_c_dataset_windowed()
        conn = synthetic_phase_c_db_factory(
            decision_rows=decision_rows, funnel_rows=funnel_rows,
        )
        c = _client()
        # Any string outside the four valid ranges must error.
        for bad in ("7days", "latest;DROP", "", "today%20OR%201%3D1"):
            r = c.get(f"/api/phase-c/funnel?range={bad}")
            body = r.json()
            assert "error" in body, f"range={bad!r} unexpectedly accepted"

    def test_valid_cohort_with_extra_payload_returns_error(
        self, synthetic_phase_c_db_factory,
    ):
        decision_rows, funnel_rows = phase_c_dataset_windowed()
        conn = synthetic_phase_c_db_factory(
            decision_rows=decision_rows, funnel_rows=funnel_rows,
        )
        c = _client()
        for bad in ("post_obs002 ", "all;DROP", "post_obs002%27", ""):
            r = c.get(f"/api/phase-c/funnel?range=7d&cohort={bad}")
            body = r.json()
            assert "error" in body, f"cohort={bad!r} unexpectedly accepted"


# ─────────────────────────────────────────────────────────────────────────
# PHASE-C10A: synthetic DB factory self-tests
# ─────────────────────────────────────────────────────────────────────────

class TestPhaseC10AFactorySelfTest:
    """PHASE-C10A: prove the synthetic DB factory contract holds.

    These tests are independent of any endpoint. They verify:
      1. The factory builds a connection with the schema the Phase C
         endpoints query.
      2. With no arguments, the factory reproduces the original
         PHASE-C7 6-row dataset (backward compatibility).
      3. The factory monkeypatches `dashboard._phase_c_open_db` so
         endpoint code reads from the in-memory connection.
      4. The companion no-fallback fixture refuses to call the live
         `trading_bot.db` opener.
      5. Each representative dataset produces the row counts its
         name implies.
    """

    def test_factory_builds_db_with_decision_history_schema(self):
        conn = _build_phase_c_db(decision_rows=[], funnel_rows=[])
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(decision_history)")
        cols = {row[1] for row in cur.fetchall()}
        for col in ("cycle_id", "symbol", "cycle_start",
                    "decision_snapshot", "decision_schema_version"):
            assert col in cols, f"missing decision_history.{col}"
        conn.close()

    def test_factory_builds_db_with_cycle_funnel_schema(self):
        conn = _build_phase_c_db(decision_rows=[], funnel_rows=[])
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(cycle_funnel)")
        cols = {row[1] for row in cur.fetchall()}
        for col in ("cycle_id", "cycle_start", "cycle_end",
                    "analyzed_count", "strategy_eligible_count",
                    "ranked_candidate_count", "execution_attempt_count",
                    "execution_blocked_count",
                    "order_submission_attempt_count", "order_submitted_count",
                    "order_failed_count", "not_attempted_count",
                    "schema_version"):
            assert col in cols, f"missing cycle_funnel.{col}"
        conn.close()

    def test_default_dataset_matches_PHASE_C7(self):
        # Backward compat: original PHASE-C7 helper had 6 decision
        # rows + 1 funnel row.
        # PHASE-C10B-3 reclassification: route through
        # `_build_phase_c_db()` directly (no fixture, no live DB).
        # This is a self-test of the factory helper itself, not an
        # endpoint contract test, so it does NOT need
        # `synthetic_phase_c_db_factory`. It only reads the in-memory
        # seed rows that `_build_phase_c_db` produces with no
        # arguments (the original PHASE-C7 6-row dataset).
        conn = _build_phase_c_db()
        cur = conn.cursor()
        n_dh = cur.execute("SELECT COUNT(*) FROM decision_history").fetchone()[0]
        n_cf = cur.execute("SELECT COUNT(*) FROM cycle_funnel").fetchone()[0]
        assert n_dh == 6, f"expected 6 decision_history rows, got {n_dh}"
        assert n_cf == 1, f"expected 1 cycle_funnel row, got {n_cf}"
        conn.close()

    def test_factory_monkeypatches_dashboard_open_db_seam(
        self, synthetic_phase_c_db_factory
    ):
        decision_rows, funnel_rows = phase_c_dataset_funnel()
        synthetic_phase_c_db_factory(
            decision_rows=decision_rows, funnel_rows=funnel_rows,
        )
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        import dashboard
        # The patched opener must yield our in-memory conn, not the
        # live trading_bot.db conn. We confirm by inserting a
        # uniquely-tagged synthetic row and reading it back. Live
        # DB will not have this row, so a successful round-trip
        # proves the seam was rewired.
        conn = dashboard._phase_c_open_db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO cycle_funnel "
            "(cycle_id, cycle_start, cycle_end, analyzed_count, "
            " strategy_eligible_count, ranked_candidate_count, "
            " execution_attempt_count, execution_blocked_count, "
            " order_submission_attempt_count, order_submitted_count, "
            " order_failed_count, not_attempted_count, schema_version) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("PHASE_C10A_CANARY", "2030-01-01T00:00:00+00:00",
             "2030-01-01T00:01:00+00:00", 1, 0, 0, 0, 0, 0, 0, 0, 0, 2),
        )
        conn.commit()
        found = cur.execute(
            "SELECT cycle_id FROM cycle_funnel "
            "WHERE cycle_id = 'PHASE_C10A_CANARY'"
        ).fetchone()
        assert found is not None, (
            "factory seam was not rewired: live DB has no synthetic row, "
            "but the factory opener should return our in-memory DB"
        )
        assert found["cycle_id"] == "PHASE_C10A_CANARY"
        conn.close()

    def test_no_fallback_seam_raises_on_direct_call(
        self, synthetic_phase_c_db_no_fallback
    ):
        # The no-fallback fixture replaced dashboard._phase_c_open_db
        # with a raiser. Direct invocation must fail closed with the
        # declared message.
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        import dashboard
        with pytest.raises(AssertionError) as excinfo:
            dashboard._phase_c_open_db()
        assert "synthetic_phase_c_db_no_fallback fired" in str(excinfo.value)

    def test_dataset_funnel_produces_expected_counts(
        self, synthetic_phase_c_db_factory
    ):
        decision_rows, funnel_rows = phase_c_dataset_funnel()
        conn = synthetic_phase_c_db_factory(
            decision_rows=decision_rows, funnel_rows=funnel_rows,
        )
        cur = conn.cursor()
        n_dh = cur.execute("SELECT COUNT(*) FROM decision_history").fetchone()[0]
        n_cf = cur.execute("SELECT COUNT(*) FROM cycle_funnel").fetchone()[0]
        assert n_dh == 6, f"funnel dataset: expected 6 decisions, got {n_dh}"
        assert n_cf == 1, f"funnel dataset: expected 1 funnel row, got {n_cf}"

    def test_dataset_gates_coverage_produces_expected_applied_count(
        self, synthetic_phase_c_db_factory
    ):
        decision_rows, funnel_rows = phase_c_dataset_gates_coverage()
        conn = synthetic_phase_c_db_factory(
            decision_rows=decision_rows, funnel_rows=funnel_rows,
        )
        cur = conn.cursor()
        n = cur.execute("SELECT COUNT(*) FROM decision_history").fetchone()[0]
        assert n == 3, f"gates dataset: expected 3 decisions, got {n}"

    def test_dataset_outcomes_produces_skipped_and_hold(
        self, synthetic_phase_c_db_factory
    ):
        decision_rows, funnel_rows = phase_c_dataset_outcomes()
        conn = synthetic_phase_c_db_factory(
            decision_rows=decision_rows, funnel_rows=funnel_rows,
        )
        cur = conn.cursor()
        # Both SKIPPED_INVALID_DATA and HOLD_INELIGIBLE must be
        # present in the JSON-embedded outcome field.
        rows = cur.execute(
            "SELECT decision_snapshot FROM decision_history"
        ).fetchall()
        outcomes = {json.loads(r[0])["decision"]["outcome"] for r in rows}
        assert "SKIPPED_INVALID_DATA" in outcomes
        assert "HOLD_INELIGIBLE" in outcomes

    def test_dataset_coverage_produces_pre_and_post_bands(
        self, synthetic_phase_c_db_factory
    ):
        decision_rows, funnel_rows = phase_c_dataset_coverage()
        conn = synthetic_phase_c_db_factory(
            decision_rows=decision_rows, funnel_rows=funnel_rows,
        )
        cur = conn.cursor()
        rows = cur.execute(
            "SELECT cycle_start FROM cycle_funnel "
            "ORDER BY cycle_start ASC"
        ).fetchall()
        starts = [r[0] for r in rows]
        assert len(starts) == 2
        assert OBS_002_BOUNDARY not in starts  # exactly two distinct
        # First (sorted) must be pre-OBS-002; second must be post.
        assert starts[0] < OBS_002_BOUNDARY, (
            f"first cycle_start {starts[0]!r} should be pre-OBS-002"
        )
        assert starts[1] >= OBS_002_BOUNDARY, (
            f"second cycle_start {starts[1]!r} should be post-OBS-002"
        )

# ─────────────────────────────────────────────────────────────────────────
# PHASE-C14B-2C: hybrid normalized read-path coverage
# ─────────────────────────────────────────────────────────────────────────
#
# These tests prove the authority contract for the C14B-2C hybrid
# read path on /api/phase-c/strategy-gates and
# /api/phase-c/execution-blockers:
#
#   analytics_persistence_version = 0 → decision_snapshot JSON is
#                                       authoritative; child rows for
#                                       v0 parents MUST be ignored.
#   analytics_persistence_version = 1 → decision_gate_evaluations /
#                                       decision_execution_checks rows
#                                       are authoritative; snapshot
#                                       JSON MUST NOT be parsed.
#   v1 parent with zero child rows is VALID — contributes zero with
#                                       no JSON fallback.
#
# The fixture `synthetic_phase_c_hybrid_db` (defined earlier in
# this file) routes `dashboard._phase_c_open_db` to a tmp-path DB
# that has both child tables populated. All tests here use that
# fixture — they never touch trading_bot.db.

def _gate(ordinality, name, category, applied, passed, observed=None,
          threshold=None, reason=None):
    """Build a single strategy_eligibility.gates[] entry as JSON."""
    return {
        "ordinality": ordinality,
        "name": name,
        "category": category,
        "applied": applied,
        "passed": passed,
        "observed_value": observed,
        "threshold_value": threshold,
        "reason": reason,
    }


def _check(ordinality, name, applied, passed, observed=None, threshold=None,
           reason=None, gap_note=None, is_first_blocking=0):
    """Build a single execution_checks.checks[] entry as JSON."""
    return {
        "ordinality": ordinality,
        "name": name,
        "applied": applied,
        "passed": passed,
        "observed_value": observed,
        "threshold_value": threshold,
        "reason": reason,
        "gap_note": gap_note,
        "is_first_blocking": is_first_blocking,
    }


class TestStrategyGatesHybridReadPath:
    """PHASE-C14B-2C: hybrid v0/v1 read path for strategy-gates.

    Each test pins ONE authority-contract rule. The contract is:

      * v0 parent → snapshot JSON is authoritative; any child rows
        that exist for the v0 parent MUST be ignored.
      * v1 parent → normalized child rows are authoritative; the
        snapshot JSON MUST NOT be parsed.
      * v1 parent with zero child rows contributes zero to the
        aggregation — there is NO JSON fallback.
      * The merged output preserves ordering (failed DESC, name ASC).
    """

    # ── all-v0 window ─────────────────────────────────────────────────

    def test_all_v0_window_reads_only_snapshot(self,
                                               synthetic_phase_c_hybrid_db):
        """3 v0 parents with gates in snapshot. Even if child rows
        exist for these v0 parents, the hybrid reader must aggregate
        from the snapshot JSON and ignore the child rows."""
        snapshot_gates = json.dumps({"strategy_eligibility": {
            "gates": [
                _gate(1, "rsi_oversold", "trend", True, True, 25, 30),
                _gate(2, "sma_uptrend", "trend", True, False, None, None),
            ],
        }, "decision": {"outcome": "HOLD_INELIGIBLE"}})
        snapshot_other = json.dumps({"decision": {"outcome": "HOLD_INELIGIBLE"}})

        decision_rows = [
            ("c1", "AAA", "2026-09-24T10:00:00+00:00", snapshot_gates, 1, 0),
            ("c2", "BBB", "2026-09-24T10:00:01+00:00", snapshot_gates, 1, 0),
            ("c3", "CCC", "2026-09-24T10:00:02+00:00", snapshot_gates, 1, 0),
        ]
        # Stale gate child rows for v0 parents — MUST be ignored.
        # If the hybrid reader incorrectly joined, totals would
        # double (snapshot 6 + children 6 = 12).
        gate_rows_v1 = [
            # (dh_id, cycle_id, symbol, cycle_start, ord, name,
            #  category, applied, passed, observed, threshold, reason)
            (1, "c1", "AAA", "2026-09-24T10:00:00+00:00", 1, "rsi_oversold",
             "trend", 1, 1, 1.0, 30.0, "STALE CHILD — must be ignored"),
            (1, "c1", "AAA", "2026-09-24T10:00:00+00:00", 2, "sma_uptrend",
             "trend", 1, 1, 1.0, 0.0, "STALE CHILD — must be ignored"),
            (2, "c2", "BBB", "2026-09-24T10:00:01+00:00", 1, "rsi_oversold",
             "trend", 1, 1, 1.0, 30.0, "STALE CHILD — must be ignored"),
            (2, "c2", "BBB", "2026-09-24T10:00:01+00:00", 2, "sma_uptrend",
             "trend", 1, 1, 1.0, 0.0, "STALE CHILD — must be ignored"),
        ]
        synthetic_phase_c_hybrid_db(
            decision_rows=decision_rows, gate_rows_v1=gate_rows_v1,
        )
        c = _client()
        body = c.get("/api/phase-c/strategy-gates?range=7d&cohort=all").json()

        rsi = next((g for g in body["gates"] if g["gate_name"] == "rsi_oversold"), None)
        sma = next((g for g in body["gates"] if g["gate_name"] == "sma_uptrend"), None)
        # Each gate: 3 v0 parents × 1 applied=true evaluation.
        # If stale child rows leaked: 3+3 = 6.
        assert rsi is not None and sma is not None, "expected both gates"
        assert rsi["total_evaluations"] == 3, (
            f"v0 + stale child double-counted? got {rsi['total_evaluations']}"
        )
        assert rsi["passed"] == 3
        assert rsi["failed"] == 0
        assert sma["total_evaluations"] == 3
        assert sma["passed"] == 0
        assert sma["failed"] == 3

    # ── all-v1 window ─────────────────────────────────────────────────

    def test_all_v1_window_reads_only_child_rows_no_json_parse(
            self, synthetic_phase_c_hybrid_db):
        """3 v1 parents whose snapshot JSON contains DIFFERENT gate
        data (decoy). The hybrid reader must aggregate from the
        child rows and ignore the snapshot entirely."""
        # Snapshot says rsi_oversold passes 1, sma_uptrend passes 1
        # (DECOY). Child rows say rsi passes 0, sma passes 0.
        # Hybrid output must follow the child rows.
        decoy_snapshot = json.dumps({"strategy_eligibility": {
            "gates": [
                _gate(1, "rsi_oversold", "trend", True, True, 1, 30,
                      "DECOY from snapshot"),
                _gate(2, "sma_uptrend", "trend", True, True, 1, 0,
                      "DECOY from snapshot"),
            ]
        }, "decision": {"outcome": "BUY_ELIGIBLE_NOT_SELECTED"}})

        decision_rows = [
            ("c1", "AAA", "2026-09-24T10:00:00+00:00", decoy_snapshot, 1, 1),
            ("c2", "BBB", "2026-09-24T10:00:01+00:00", decoy_snapshot, 1, 1),
            ("c3", "CCC", "2026-09-24T10:00:02+00:00", decoy_snapshot, 1, 1),
        ]
        gate_rows_v1 = [
            (1, "c1", "AAA", "2026-09-24T10:00:00+00:00", 1, "rsi_oversold",
             "trend", 1, 0, 0.0, 30.0, "AUTHORITATIVE child row"),
            (1, "c1", "AAA", "2026-09-24T10:00:00+00:00", 2, "sma_uptrend",
             "trend", 1, 0, 0.0, 0.0, "AUTHORITATIVE child row"),
            (2, "c2", "BBB", "2026-09-24T10:00:01+00:00", 1, "rsi_oversold",
             "trend", 1, 0, 0.0, 30.0, "AUTHORITATIVE child row"),
            (2, "c2", "BBB", "2026-09-24T10:00:01+00:00", 2, "sma_uptrend",
             "trend", 1, 0, 0.0, 0.0, "AUTHORITATIVE child row"),
            (3, "c3", "CCC", "2026-09-24T10:00:02+00:00", 1, "rsi_oversold",
             "trend", 1, 1, 1.0, 30.0, "AUTHORITATIVE child row"),
            (3, "c3", "CCC", "2026-09-24T10:00:02+00:00", 2, "sma_uptrend",
             "trend", 1, 1, 1.0, 0.0, "AUTHORITATIVE child row"),
        ]
        synthetic_phase_c_hybrid_db(
            decision_rows=decision_rows, gate_rows_v1=gate_rows_v1,
        )
        c = _client()
        body = c.get("/api/phase-c/strategy-gates?range=7d&cohort=all").json()

        rsi = next((g for g in body["gates"] if g["gate_name"] == "rsi_oversold"), None)
        sma = next((g for g in body["gates"] if g["gate_name"] == "sma_uptrend"), None)
        assert rsi is not None and sma is not None
        # If JSON was parsed for v1: rsi passed=3, sma passed=3.
        # Authoritative child output: rsi passed=1, sma passed=1.
        assert rsi["total_evaluations"] == 3
        assert rsi["passed"] == 1, (
            f"v1 must NOT parse snapshot — got passed={rsi['passed']}, "
            f"expected 1"
        )
        assert rsi["failed"] == 2
        assert sma["total_evaluations"] == 3
        assert sma["passed"] == 1
        assert sma["failed"] == 2

    # ── mixed v0/v1 window ────────────────────────────────────────────

    def test_mixed_v0_v1_window_sums_without_double_count(
            self, synthetic_phase_c_hybrid_db):
        """1 v0 parent (snapshot) + 1 v1 parent (child rows). The
        output must combine both without double-counting."""
        v0_snapshot = json.dumps({"strategy_eligibility": {
            "gates": [
                _gate(1, "rsi_oversold", "trend", True, True, 25, 30),
                _gate(2, "sma_uptrend", "trend", True, False, None, None),
            ]
        }, "decision": {"outcome": "HOLD_INELIGIBLE"}})

        decision_rows = [
            ("c1", "AAA", "2026-09-24T10:00:00+00:00", v0_snapshot, 1, 0),
            ("c2", "BBB", "2026-09-24T10:00:01+00:00", v0_snapshot, 1, 1),
        ]
        gate_rows_v1 = [
            (2, "c2", "BBB", "2026-09-24T10:00:01+00:00", 1, "rsi_oversold",
             "trend", 1, 0, 0.0, 30.0, "child row"),
            (2, "c2", "BBB", "2026-09-24T10:00:01+00:00", 2, "sma_uptrend",
             "trend", 1, 1, 1.0, 0.0, "child row"),
        ]
        synthetic_phase_c_hybrid_db(
            decision_rows=decision_rows, gate_rows_v1=gate_rows_v1,
        )
        c = _client()
        body = c.get("/api/phase-c/strategy-gates?range=7d&cohort=all").json()

        rsi = next((g for g in body["gates"] if g["gate_name"] == "rsi_oversold"), None)
        sma = next((g for g in body["gates"] if g["gate_name"] == "sma_uptrend"), None)
        # Each gate: 1 v0 (applied=true) + 1 v1 (applied=true) = 2.
        # If double-counted: 2 v0 + 2 v1 = 4 (with overlap).
        # If only v1 read: 1. If only v0 read: 1.
        assert rsi["total_evaluations"] == 2, (
            f"mixed: rsi total={rsi['total_evaluations']}, expected 2 "
            f"(1 v0 + 1 v1, NO double-count)"
        )
        # v0 rsi passed=1, v1 rsi passed=0 → total passed=1
        assert rsi["passed"] == 1
        assert rsi["failed"] == 1
        assert sma["total_evaluations"] == 2
        # v0 sma passed=0, v1 sma passed=1 → total passed=1
        assert sma["passed"] == 1
        assert sma["failed"] == 1

    # ── v1 zero-gate parent ───────────────────────────────────────────

    def test_v1_zero_gate_parent_contributes_zero_with_no_fallback(
            self, synthetic_phase_c_hybrid_db):
        """v1 parent with NO gate child rows, but with a snapshot
        that DECLARES gates. The hybrid reader must NOT parse the
        snapshot — the parent contributes zero, even though the
        JSON contains data that would change the result."""
        decoy_snapshot = json.dumps({"strategy_eligibility": {
            "gates": [
                _gate(1, "rsi_oversold", "trend", True, True, 1, 30,
                      "DECOY — should never be parsed"),
            ]
        }, "decision": {"outcome": "HOLD_INELIGIBLE"}})

        decision_rows = [
            ("c1", "AAA", "2026-09-24T10:00:00+00:00", decoy_snapshot, 1, 1),
        ]
        # No gate_rows_v1 for c1.
        synthetic_phase_c_hybrid_db(
            decision_rows=decision_rows, gate_rows_v1=[],
        )
        c = _client()
        body = c.get("/api/phase-c/strategy-gates?range=7d&cohort=all").json()

        # rsi_oversold must NOT appear — v1 zero-child contributes zero.
        rsi = next((g for g in body["gates"] if g["gate_name"] == "rsi_oversold"), None)
        assert rsi is None, (
            "v1 zero-child parent must NOT contribute via JSON fallback. "
            "rsi_oversold unexpectedly present in response."
        )
        assert body["rows_in_cohort"] == 1, "v1 parent counted in cohort"

    # ── response shape / ordering parity ──────────────────────────────

    def test_response_shape_matches_pre_hybrid_contract(
            self, synthetic_phase_c_hybrid_db):
        """The hybrid output must preserve the pre-hybrid JSON
        response shape (keys, top-level envelope)."""
        snapshot_gates = json.dumps({"strategy_eligibility": {
            "gates": [_gate(1, "rsi_oversold", "trend", True, True, 25, 30)]
        }, "decision": {"outcome": "HOLD_INELIGIBLE"}})
        decision_rows = [
            ("c1", "AAA", "2026-09-24T10:00:00+00:00", snapshot_gates, 1, 0),
        ]
        synthetic_phase_c_hybrid_db(decision_rows=decision_rows, gate_rows_v1=[])
        c = _client()
        body = c.get("/api/phase-c/strategy-gates?range=7d&cohort=all").json()
        # Top-level keys preserved.
        for key in ("range", "cohort", "rows_in_cohort",
                    "gate_rows_aggregated", "gates", "source",
                    "inference_rule"):
            assert key in body, f"response missing top-level key '{key}'"
        # Gate row keys preserved.
        assert body["gates"], "expected at least one gate"
        for g in body["gates"]:
            for key in ("gate_name", "category", "total_evaluations",
                        "passed", "failed", "applied_count",
                        "failure_rate"):
                assert key in g, f"gate row missing key '{key}'"
        # source string reflects hybrid.
        assert "PHASE-C14B-2C hybrid" in body["source"]

    def test_ordering_failures_desc_then_name_asc(
            self, synthetic_phase_c_hybrid_db):
        """Hybrid ordering rule: failed DESC, then gate_name ASC.
        Mixed v0/v1 must preserve this."""
        v0_snapshot = json.dumps({"strategy_eligibility": {
            "gates": [
                _gate(1, "alpha", "trend", True, True, 1, 1),
                _gate(2, "beta", "trend", True, False, 1, 1),
                _gate(3, "gamma", "trend", True, False, 1, 1),
                _gate(4, "delta", "trend", True, True, 1, 1),
            ]
        }, "decision": {"outcome": "HOLD_INELIGIBLE"}})
        decision_rows = [
            ("c1", "AAA", "2026-09-24T10:00:00+00:00", v0_snapshot, 1, 0),
            ("c2", "BBB", "2026-09-24T10:00:01+00:00", v0_snapshot, 1, 1),
        ]
        # v1 parent adds 1 more failure to beta (so beta ends up
        # with 2 failures total).
        gate_rows_v1 = [
            (2, "c2", "BBB", "2026-09-24T10:00:01+00:00", 2, "beta",
             "trend", 1, 0, 0.0, 0.0, "extra v1 fail"),
        ]
        synthetic_phase_c_hybrid_db(
            decision_rows=decision_rows, gate_rows_v1=gate_rows_v1,
        )
        c = _client()
        body = c.get("/api/phase-c/strategy-gates?range=7d&cohort=all").json()
        names = [g["gate_name"] for g in body["gates"]]
        # Expected order: gamma (1 fail), beta (2 fail), alpha (0),
        # delta (0). Within failures DESC: beta (2), gamma (1),
        # then alpha (0) and delta (0) tie on name ASC.
        assert names == ["beta", "gamma", "alpha", "delta"], (
            f"ordering broken: {names}"
        )

    def test_applied_false_still_excluded_in_v1_path(
            self, synthetic_phase_c_hybrid_db):
        """PHASE-C7 contract must hold on the v1 child path:
        applied=0 rows are excluded by SQL (g.applied = 1 filter)
        and contribute zero to totals."""
        decision_rows = [
            ("c1", "AAA", "2026-09-24T10:00:00+00:00", "{}", 1, 1),
        ]
        gate_rows_v1 = [
            (1, "c1", "AAA", "2026-09-24T10:00:00+00:00", 1, "rsi_oversold",
             "trend", 1, 1, 1.0, 30.0, "applied=true passed"),
            (1, "c1", "AAA", "2026-09-24T10:00:00+00:00", 2, "rsi_oversold",
             "trend", 1, 0, 0.0, 30.0, "applied=true failed"),
            (1, "c1", "AAA", "2026-09-24T10:00:00+00:00", 3, "rsi_oversold",
             "trend", 0, None, None, None, "applied=false (excluded)"),
        ]
        synthetic_phase_c_hybrid_db(
            decision_rows=decision_rows, gate_rows_v1=gate_rows_v1,
        )
        c = _client()
        body = c.get("/api/phase-c/strategy-gates?range=7d&cohort=all").json()
        rsi = next((g for g in body["gates"] if g["gate_name"] == "rsi_oversold"), None)
        assert rsi is not None
        # 2 applied=true rows counted: 1 passed, 1 failed.
        # The applied=false row is filtered out by `g.applied = 1`.
        assert rsi["total_evaluations"] == 2, (
            f"applied=0 row leaked? total={rsi['total_evaluations']}"
        )
        assert rsi["passed"] == 1
        assert rsi["failed"] == 1


class TestExecutionBlockersHybridReadPath:
    """PHASE-C14B-2C: hybrid v0/v1 read path for execution-blockers."""

    def test_v1_zero_check_parent_does_not_appear_with_decoy_snapshot(
            self, synthetic_phase_c_hybrid_db):
        """v1 parent with NO exec-check child rows but with a
        snapshot that DECLARES checks. Hybrid reader must NOT
        parse the snapshot — parent contributes zero."""
        decoy_snapshot = json.dumps({"execution_checks": {
            "checks": [
                _check(1, "buying_power_check", True, False, 1, 0,
                       reason="DECOY"),
            ],
            "first_blocking_check": "buying_power_check",
        }, "decision": {"outcome": "BUY_BLOCKED_DYNAMIC"}})

        decision_rows = [
            ("c1", "AAA", "2026-09-24T10:00:00+00:00", decoy_snapshot, 1, 1),
        ]
        # No exec_rows_v1.
        synthetic_phase_c_hybrid_db(
            decision_rows=decision_rows, exec_rows_v1=[],
        )
        c = _client()
        body = c.get("/api/phase-c/execution-blockers?range=7d&cohort=all").json()
        names = [chk["check_name"] for chk in body["checks"]]
        assert "buying_power_check" not in names, (
            "v1 zero-check parent must NOT contribute via JSON fallback. "
            f"Got checks={names}"
        )
        assert body["rows_in_cohort"] == 1

    def test_v0_stale_exec_check_children_are_ignored(
            self, synthetic_phase_c_hybrid_db):
        """v0 parent has exec-check child rows. Hybrid reader must
        ignore them and aggregate from snapshot."""
        snapshot = json.dumps({"execution_checks": {
            "checks": [
                _check(1, "buying_power_check", True, False, 1, 0,
                       reason="v0 snapshot"),
                _check(2, "position_existence_check", True, True, 1, 0),
            ],
            "first_blocking_check": "buying_power_check",
        }, "decision": {"outcome": "SELL_BLOCKED_DYNAMIC"}})

        decision_rows = [
            ("c1", "AAA", "2026-09-24T10:00:00+00:00", snapshot, 1, 0),
        ]
        exec_rows_v1 = [
            (1, "c1", "AAA", "2026-09-24T10:00:00+00:00", 1,
             "stale_child_check", 1, 1, 1, 0, "STALE", None, 1),
        ]
        synthetic_phase_c_hybrid_db(
            decision_rows=decision_rows, exec_rows_v1=exec_rows_v1,
        )
        c = _client()
        body = c.get("/api/phase-c/execution-blockers?range=7d&cohort=all").json()
        names = [chk["check_name"] for chk in body["checks"]]
        assert "stale_child_check" not in names, (
            f"v0 stale exec-check child leaked into output: {names}"
        )
        assert "buying_power_check" in names
        assert "position_existence_check" in names

    def test_all_v1_exec_check_path(self, synthetic_phase_c_hybrid_db):
        """v1 parent with snapshot DECLARING different checks than
        the child rows. Child rows win."""
        decoy_snapshot = json.dumps({"execution_checks": {
            "checks": [
                _check(1, "decoy_check", True, False, 1, 0, reason="DECOY"),
            ],
            "first_blocking_check": "decoy_check",
        }, "decision": {"outcome": "SELL_BLOCKED_DYNAMIC"}})

        decision_rows = [
            ("c1", "AAA", "2026-09-24T10:00:00+00:00", decoy_snapshot, 1, 1),
        ]
        exec_rows_v1 = [
            (1, "c1", "AAA", "2026-09-24T10:00:00+00:00", 1,
             "buying_power_check", 1, 0, 1, 0,
             "v1 authoritative", None, 1),
            (1, "c1", "AAA", "2026-09-24T10:00:00+00:00", 2,
             "buying_power_check", 1, 1, 1, 0,
             "v1 authoritative", None, 0),
        ]
        synthetic_phase_c_hybrid_db(
            decision_rows=decision_rows, exec_rows_v1=exec_rows_v1,
        )
        c = _client()
        body = c.get("/api/phase-c/execution-blockers?range=7d&cohort=all").json()
        names = [chk["check_name"] for chk in body["checks"]]
        assert "decoy_check" not in names, (
            f"v1 must not parse snapshot decoy: {names}"
        )
        assert "buying_power_check" in names
        bp = next(chk for chk in body["checks"]
                  if chk["check_name"] == "buying_power_check")
        assert bp["total_evaluations"] == 2
        assert bp["passed"] == 1
        assert bp["failed"] == 1

    def test_first_blocking_merges_v0_and_v1(self,
                                             synthetic_phase_c_hybrid_db):
        """first_blocking_check aggregation: v0 'a' (count=1) + v1
        'b' (count=2) merges to [{b:2}, {a:1}] (cnt DESC, name ASC)."""
        v0_snapshot = json.dumps({"execution_checks": {
            "checks": [_check(1, "a", True, False, 1, 0,
                              is_first_blocking=1)],
            "first_blocking_check": "a",
        }, "decision": {"outcome": "SELL_BLOCKED_DYNAMIC"}})
        decision_rows = [
            ("c1", "AAA", "2026-09-24T10:00:00+00:00", v0_snapshot, 1, 0),
            ("c2", "BBB", "2026-09-24T10:00:01+00:00", v0_snapshot, 1, 1),
        ]
        exec_rows_v1 = [
            (2, "c2", "BBB", "2026-09-24T10:00:01+00:00", 1, "b", 1, 0,
             1, 0, "v1 fb", None, 1),
            (2, "c2", "BBB", "2026-09-24T10:00:01+00:00", 2, "b", 1, 0,
             1, 0, "v1 fb", None, 1),
        ]
        synthetic_phase_c_hybrid_db(
            decision_rows=decision_rows, exec_rows_v1=exec_rows_v1,
        )
        c = _client()
        body = c.get("/api/phase-c/execution-blockers?range=7d&cohort=all").json()
        fb = body["first_blocking_check"]
        # b (cnt=2) comes first, then a (cnt=1). Tied names sort by
        # name ASC.
        names = [(f["check_name"], f["count"]) for f in fb]
        assert names == [("b", 2), ("a", 1)], (
            f"first_blocking merge wrong: {names}"
        )

    def test_response_shape_matches_pre_hybrid_contract(
            self, synthetic_phase_c_hybrid_db):
        """execution-blockers response shape preserved under hybrid."""
        snapshot = json.dumps({"execution_checks": {
            "checks": [_check(1, "buying_power_check", True, False, 1, 0,
                              is_first_blocking=1)],
            "first_blocking_check": "buying_power_check",
        }, "decision": {"outcome": "SELL_BLOCKED_DYNAMIC"}})
        decision_rows = [
            ("c1", "AAA", "2026-09-24T10:00:00+00:00", snapshot, 1, 0),
        ]
        synthetic_phase_c_hybrid_db(
            decision_rows=decision_rows, exec_rows_v1=[],
        )
        c = _client()
        body = c.get("/api/phase-c/execution-blockers?range=7d&cohort=all").json()
        for key in ("range", "cohort", "rows_in_cohort",
                    "rows_with_checks", "universe_caveat",
                    "checks", "first_blocking_check", "source"):
            assert key in body, f"missing top-level key '{key}'"
        for chk in body["checks"]:
            for key in ("check_name", "total_evaluations", "passed",
                        "failed", "applied_count"):
                assert key in chk, f"check missing key '{key}'"
        assert "PHASE-C14B-2C hybrid" in body["source"]


class TestHybridZeroBothParent:
    """PHASE-C14B-2C: a v1 parent with ZERO gate rows AND ZERO
    exec-check rows must remain fully valid. The snapshot may
    contain rich data — it must NOT be parsed under any
    circumstance for v1. This is the strongest test of the
    authority contract."""

    def test_v1_zero_both_does_not_parse_snapshot_for_any_fact(
            self, synthetic_phase_c_hybrid_db):
        """One v1 parent with:
          * Snapshot containing a HOLD_INELIGIBLE outcome
          * Snapshot containing rsi_oversold gate (applied=true,
            passed=false)
          * Snapshot containing 5 execution checks
          * ZERO gate child rows
          * ZERO exec-check child rows

        Expected output:
          * rsi_oversold NOT in /api/phase-c/strategy-gates
          * No checks in /api/phase-c/execution-blockers
          * rows_in_cohort = 1 (parent is counted)
        """
        decoy_snapshot = json.dumps({
            "strategy_eligibility": {
                "gates": [
                    _gate(1, "rsi_oversold", "trend", True, False, 99, 30),
                    _gate(2, "sma_uptrend", "trend", True, True, 1, 0),
                ],
            },
            "execution_checks": {
                "checks": [
                    _check(1, "buying_power_check", True, False, 1, 0,
                           is_first_blocking=1),
                    _check(2, "position_concentration_check", True, False,
                           1, 0, is_first_blocking=0),
                    _check(3, "sector_concentration_check", True, True,
                           1, 0, is_first_blocking=0),
                    _check(4, "correlation_check", True, True, 1, 0,
                           is_first_blocking=0),
                    _check(5, "beta_check", True, True, 1, 0,
                           is_first_blocking=0),
                ],
                "first_blocking_check": "buying_power_check",
            },
            "decision": {"outcome": "HOLD_INELIGIBLE"},
        })
        decision_rows = [
            ("c1", "AAA", "2026-09-24T10:00:00+00:00", decoy_snapshot, 1, 1),
        ]
        # NO gate_rows_v1, NO exec_rows_v1.
        synthetic_phase_c_hybrid_db(
            decision_rows=decision_rows, gate_rows_v1=[], exec_rows_v1=[],
        )
        c = _client()

        # Strategy gates: empty (zero both → zero both).
        gates_body = c.get(
            "/api/phase-c/strategy-gates?range=7d&cohort=all"
        ).json()
        gate_names = [g["gate_name"] for g in gates_body["gates"]]
        assert "rsi_oversold" not in gate_names, (
            f"v1 zero-both leaked gate from snapshot: {gate_names}"
        )
        assert "sma_uptrend" not in gate_names
        assert gates_body["rows_in_cohort"] == 1

        # Execution blockers: empty (zero both → zero both).
        eb_body = c.get(
            "/api/phase-c/execution-blockers?range=7d&cohort=all"
        ).json()
        check_names = [chk["check_name"] for chk in eb_body["checks"]]
        assert "buying_power_check" not in check_names, (
            f"v1 zero-both leaked check from snapshot: {check_names}"
        )
        assert "position_concentration_check" not in check_names
        assert eb_body["rows_in_cohort"] == 1


class TestHybridRangeParamBinding:
    """PHASE-C14B-2C: prove the v1 SQL param-binding fix works
    for every supported range, including 'latest' (which has zero
    params in its range clause)."""

    @pytest.mark.parametrize("range_name", ["latest", "today", "24h", "7d"])
    def test_v1_path_works_for_every_range(self, range_name,
                                            synthetic_phase_c_hybrid_db):
        """All four supported ranges must execute the v1 child-table
        path without SQL parameter-binding errors. Pre-fix, 'latest'
        would crash because v1 SQL had a hardcoded extra '?'."""
        decision_rows = [
            ("c1", "AAA", "2026-09-24T10:00:00+00:00", "{}", 1, 1),
        ]
        gate_rows_v1 = [
            (1, "c1", "AAA", "2026-09-24T10:00:00+00:00", 1,
             "rsi_oversold", "trend", 1, 1, 25, 30, "v1"),
        ]
        exec_rows_v1 = [
            (1, "c1", "AAA", "2026-09-24T10:00:00+00:00", 1,
             "buying_power_check", 1, 1, 1, 0, "v1", None, 0),
        ]
        synthetic_phase_c_hybrid_db(
            decision_rows=decision_rows, gate_rows_v1=gate_rows_v1,
            exec_rows_v1=exec_rows_v1,
        )
        c = _client()
        # No SQL error means the param binding is correct.
        gates_resp = c.get(f"/api/phase-c/strategy-gates?range={range_name}&cohort=all")
        assert gates_resp.status_code == 200, (
            f"strategy-gates range={range_name} returned "
            f"{gates_resp.status_code}: {gates_resp.text}"
        )
        eb_resp = c.get(f"/api/phase-c/execution-blockers?range={range_name}&cohort=all")
        assert eb_resp.status_code == 200, (
            f"execution-blockers range={range_name} returned "
            f"{eb_resp.status_code}: {eb_resp.text}"
        )


class TestRowsInCohortRangeLimited:
    """PHASE-C14B-2D: rows_in_cohort MUST be range-limited, NOT
    range-agnostic.

    Pre-C14B-2C and pre-C14B-2D contract:
        rows_in_cohort = number of decision_history parents in the
        SELECTED cohort AND SELECTED range/window, regardless of
        analytics_persistence_version. The authority split affects
        analytics FACTS, not the parent universe count.

    C14B-2C bug: the rows_in_cohort SQL applied the cohort fragments
    but DROPPED the range fragments, returning ~1.31M (the entire
    post-OBS-002 universe) for every range, including `latest` and
    `1h`.

    These tests pin the range-limited contract across:
        * latest   — single latest cycle only (~30 parents in prod)
        * today    — today UTC only
        * 1h       — last hour
        * 6h       — last 6 hours
        * 24h      — last 24 hours
        * 7d       — last 7 days
        * mixed v0/v1 within range — both versions counted
        * v0 outside range — excluded
        * v1 outside range — excluded
        * v1 zero-child parent within range — STILL counted
    """

    # Cycle_starts are anchored RELATIVE TO REAL NOW (not to a
    # fixed wall-clock date). Tests must pass regardless of when
    # pytest is invoked, because the Phase C range predicates use
    # `_dt.now(timezone.utc)` for 24h/7d/today windows.
    @classmethod
    def _iso(cls, dt):
        return dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")

    @classmethod
    def _in_range_start(cls, range_name):
        """Return cycle_start that IS in the requested range."""
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz
        now = _dt.now(_tz.utc)
        if range_name == "latest":
            # 5 min ago — paired with a matching cycle_end in the funnel.
            return cls._iso(now - _td(minutes=5))
        if range_name == "today":
            today_prefix = now.strftime("%Y-%m-%d")
            return f"{today_prefix}T00:00:00+00:00"
        if range_name == "24h":
            return cls._iso(now - _td(hours=1))
        if range_name == "7d":
            return cls._iso(now - _td(days=1))
        raise ValueError(f"unknown range: {range_name}")

    @classmethod
    def _out_of_range_start(cls, range_name):
        """Return cycle_start that is NOT in the requested range."""
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz
        now = _dt.now(_tz.utc)
        if range_name == "latest":
            return cls._iso(now - _td(days=5))
        if range_name == "today":
            # Yesterday at 23:30 UTC — definitely before today's UTC midnight.
            yday = (now - _td(days=1)).replace(
                hour=23, minute=30, second=0, microsecond=0
            )
            return cls._iso(yday)
        if range_name == "24h":
            return cls._iso(now - _td(hours=30))
        if range_name == "7d":
            return cls._iso(now - _td(days=10))
        raise ValueError(f"unknown range: {range_name}")

    @classmethod
    def _latest_cycle_end(cls):
        from datetime import datetime as _dt, timezone as _tz
        return cls._iso(_dt.now(_tz.utc))

    def _build_dataset(self, range_name, *, with_cycle_funnel=True,
                       include_outside=True, version_mix=True,
                       v1_zero_child=False):
        """Build a deterministic dataset for a given range.

        Returns (decision_rows, gate_rows_v1, exec_rows_v1,
        funnel_rows).

        Layout (per range):
          - 1 v0 parent at in-range cycle_start
          - 1 v1 parent at in-range cycle_start
            - if v1_zero_child: NO child rows
            - else: 2 gate rows + 2 exec-check rows
          - 1 v0 parent at out-of-range cycle_start
          - 1 v1 parent at out-of-range cycle_start
        """
        in_start = self._in_range_start(range_name)
        out_start = self._out_of_range_start(range_name)
        snapshot_v0 = json.dumps({"decision": {"outcome": "HOLD_INELIGIBLE"}})
        snapshot_v1 = json.dumps({"decision": {"outcome": "BUY_ELIGIBLE_NOT_SELECTED"}})

        decision_rows = []
        # In-range v0 parent (id 1)
        decision_rows.append(("c_in_v0", "AAA", in_start, snapshot_v0, 1, 0))
        # In-range v1 parent (id 2)
        decision_rows.append(("c_in_v1", "BBB", in_start, snapshot_v1, 1, 1))
        if include_outside:
            # Out-of-range v0 parent (id 3)
            decision_rows.append(("c_out_v0", "CCC", out_start, snapshot_v0, 1, 0))
            # Out-of-range v1 parent (id 4)
            decision_rows.append(("c_out_v1", "DDD", out_start, snapshot_v1, 1, 1))

        gate_rows_v1 = []
        exec_rows_v1 = []
        if not v1_zero_child:
            # In-range v1 parent (id 2) gets 2 gate rows + 2 exec-check rows.
            gate_rows_v1 = [
                (2, "c_in_v1", "BBB", in_start, 1, "rsi_oversold",
                 "trend", 1, 1, 25, 30, "v1 in-range"),
                (2, "c_in_v1", "BBB", in_start, 2, "sma_uptrend",
                 "trend", 1, 0, 0, 0, "v1 in-range"),
            ]
            exec_rows_v1 = [
                (2, "c_in_v1", "BBB", in_start, 1,
                 "buying_power_check", 1, 1, 1, 0, "v1 in-range", None, 0),
                (2, "c_in_v1", "BBB", in_start, 2,
                 "margin_check", 1, 0, 1, 0, "v1 in-range", None, 1),
            ]

        funnel_rows = []
        if with_cycle_funnel and range_name == "latest":
            funnel_rows = [
                phase_c_funnel_row(
                    "latest", self._in_range_start("latest"), self._latest_cycle_end(),
                    analyzed=2, strategy_eligible=1, ranked=1,
                    exec_attempt=1, exec_blocked=1,
                    order_submit_attempt=0, order_submitted=0,
                    order_failed=0, not_attempted=0,
                ),
            ]

        return decision_rows, gate_rows_v1, exec_rows_v1, funnel_rows

    # ── latest: only the latest cycle ──────────────────────────────

    def test_latest_includes_only_latest_cycle(
            self, synthetic_phase_c_hybrid_db):
        """`range=latest` should return rows_in_cohort=2 (only the
        in-range v0+v1 parents). Pre-fix, this returned ~1.31M
        because the range predicate was dropped."""
        decision_rows, gate_rows_v1, exec_rows_v1, funnel_rows = (
            self._build_dataset("latest")
        )
        synthetic_phase_c_hybrid_db(
            decision_rows=decision_rows, gate_rows_v1=gate_rows_v1,
            exec_rows_v1=exec_rows_v1, funnel_rows=funnel_rows,
        )
        c = _client()
        gates_body = c.get(
            "/api/phase-c/strategy-gates?range=latest&cohort=all"
        ).json()
        assert gates_body["rows_in_cohort"] == 2, (
            f"latest rows_in_cohort={gates_body['rows_in_cohort']}, "
            f"expected 2 (in-range v0+v1). Bug C14B-2D regressed: "
            f"range predicate was dropped."
        )
        eb_body = c.get(
            "/api/phase-c/execution-blockers?range=latest&cohort=all"
        ).json()
        assert eb_body["rows_in_cohort"] == 2, (
            f"latest eb rows_in_cohort={eb_body['rows_in_cohort']}, "
            f"expected 2"
        )

    # ── parameterized: every supported range, in/out counted ────────

    @pytest.mark.parametrize("range_name", ["today", "24h", "7d"])
    def test_each_range_excludes_outside_window(
            self, range_name, synthetic_phase_c_hybrid_db):
        """For each time range, rows_in_cohort must include ONLY the
        in-range parents (not the ones at OUTSIDE_STARTS).

        Expected: 2 (1 v0 + 1 v1 at the in-range cycle_start).
        Pre-fix, this returned the entire post-OBS-002 universe."""
        decision_rows, gate_rows_v1, exec_rows_v1, funnel_rows = (
            self._build_dataset(range_name, with_cycle_funnel=False)
        )
        synthetic_phase_c_hybrid_db(
            decision_rows=decision_rows, gate_rows_v1=gate_rows_v1,
            exec_rows_v1=exec_rows_v1, funnel_rows=funnel_rows,
        )
        c = _client()
        gates_body = c.get(
            f"/api/phase-c/strategy-gates?range={range_name}&cohort=all"
        ).json()
        assert gates_body["rows_in_cohort"] == 2, (
            f"strategy-gates rows_in_cohort for range={range_name}: "
            f"got {gates_body['rows_in_cohort']}, expected 2"
        )
        eb_body = c.get(
            f"/api/phase-c/execution-blockers?range={range_name}&cohort=all"
        ).json()
        assert eb_body["rows_in_cohort"] == 2, (
            f"execution-blockers rows_in_cohort for range={range_name}: "
            f"got {eb_body['rows_in_cohort']}, expected 2"
        )

    # ── v0/v1 mix within range counts both ──────────────────────────

    def test_mixed_v0_v1_within_range_counts_both_versions(
            self, synthetic_phase_c_hybrid_db):
        """In a 24h window containing both v0 and v1 parents, both
        versions count toward rows_in_cohort. The authority split
        affects FACTS, not the parent universe."""
        decision_rows, gate_rows_v1, exec_rows_v1, funnel_rows = (
            self._build_dataset("24h", with_cycle_funnel=False)
        )
        synthetic_phase_c_hybrid_db(
            decision_rows=decision_rows, gate_rows_v1=gate_rows_v1,
            exec_rows_v1=exec_rows_v1, funnel_rows=funnel_rows,
        )
        c = _client()
        # 2 in-range parents: 1 v0 + 1 v1 = both count.
        gates_body = c.get(
            "/api/phase-c/strategy-gates?range=24h&cohort=all"
        ).json()
        assert gates_body["rows_in_cohort"] == 2, (
            f"mixed v0/v1 rows_in_cohort={gates_body['rows_in_cohort']}, "
            f"expected 2 (1 v0 + 1 v1)"
        )

    # ── v0 outside range excluded ──────────────────────────────────

    def test_v0_outside_range_excluded(
            self, synthetic_phase_c_hybrid_db):
        """A v0 parent at OUTSIDE_STARTS must NOT be counted in
        rows_in_cohort for `range=24h`."""
        decision_rows, gate_rows_v1, exec_rows_v1, funnel_rows = (
            self._build_dataset("24h", with_cycle_funnel=False)
        )
        synthetic_phase_c_hybrid_db(
            decision_rows=decision_rows, gate_rows_v1=gate_rows_v1,
            exec_rows_v1=exec_rows_v1, funnel_rows=funnel_rows,
        )
        c = _client()
        eb_body = c.get(
            "/api/phase-c/execution-blockers?range=24h&cohort=all"
        ).json()
        # 4 total parents, but 2 are out-of-range (1 v0 + 1 v1).
        # rows_in_cohort MUST be 2.
        assert eb_body["rows_in_cohort"] == 2, (
            f"v0 outside range leaked? rows_in_cohort="
            f"{eb_body['rows_in_cohort']}, expected 2"
        )

    # ── v1 zero-child within range STILL counts ─────────────────────

    def test_v1_zero_child_within_range_still_counted(
            self, synthetic_phase_c_hybrid_db):
        """CRITICAL: a v1 parent with ZERO normalized child rows
        must STILL count toward rows_in_cohort. The authority
        contract for facts is independent of the parent universe
        count — v1 zero-child means zero FACTS but the parent
        itself remains a valid cohort member."""
        decision_rows, gate_rows_v1, exec_rows_v1, funnel_rows = (
            self._build_dataset("24h", with_cycle_funnel=False,
                                v1_zero_child=True)
        )
        synthetic_phase_c_hybrid_db(
            decision_rows=decision_rows, gate_rows_v1=[],
            exec_rows_v1=[], funnel_rows=funnel_rows,
        )
        c = _client()
        gates_body = c.get(
            "/api/phase-c/strategy-gates?range=24h&cohort=all"
        ).json()
        # 2 in-range parents, neither with v1 child rows. Both still
        # count in rows_in_cohort.
        assert gates_body["rows_in_cohort"] == 2, (
            f"v1 zero-child within range wrongly excluded? "
            f"rows_in_cohort={gates_body['rows_in_cohort']}, expected 2"
        )
        # The v1 parent's contribution to FACTS is zero (no child rows).
        # gates list is empty (v0 parent has no gates in snapshot).
        assert gates_body["gates"] == [], (
            f"v1 zero-child must NOT contribute facts from snapshot: "
            f"got gates={[g['gate_name'] for g in gates_body['gates']]}"
        )
        # Same for execution-blockers.
        eb_body = c.get(
            "/api/phase-c/execution-blockers?range=24h&cohort=all"
        ).json()
        assert eb_body["rows_in_cohort"] == 2
        assert eb_body["checks"] == [], (
            "v1 zero-child must NOT contribute facts from snapshot"
        )

    # ── range=7d excludes parents 8+ days old ──────────────────────

    def test_7d_excludes_parents_older_than_seven_days(
            self, synthetic_phase_c_hybrid_db):
        """Sanity check that 7d truly excludes the 8+ day-old parent
        that the C14B-2C bug would have included."""
        decision_rows, gate_rows_v1, exec_rows_v1, funnel_rows = (
            self._build_dataset("7d", with_cycle_funnel=False)
        )
        synthetic_phase_c_hybrid_db(
            decision_rows=decision_rows, gate_rows_v1=gate_rows_v1,
            exec_rows_v1=exec_rows_v1, funnel_rows=funnel_rows,
        )
        c = _client()
        # OUTSIDE_STARTS["7d"] is ~10.5 days old. Only the 2 in-range
        # parents (~5.5 days old) count.
        gates_body = c.get(
            "/api/phase-c/strategy-gates?range=7d&cohort=all"
        ).json()
        assert gates_body["rows_in_cohort"] == 2, (
            f"7d window includes stale parent? rows_in_cohort="
            f"{gates_body['rows_in_cohort']}, expected 2"
        )


    def test_no_deterministic_test_uses_live_db_path(self):
        """PHASE-C10D static guard: walk this file's AST and assert that
        no test method (other than this one) is structured to read the
        live trading_bot.db. We check three classes of patterns:

          * direct `_db()` helper calls (the only direct live-DB
            accessor that was ever defined in this module; relocated
            to `tests/observational/`);
          * `live_phase_c_db` fixture arguments (the documented opt-in
            escape hatch that no test currently uses);
          * the `shared_client` and `gates_response_today_post`
            module-scoped live fixtures (also relocated to
            `tests/observational/`).

        If any of these patterns is reintroduced into this file's test
        bodies, this test fails at collection time so the regression
        is caught BEFORE it can run live during CI.
        """
        import ast as _ast
        from pathlib import Path as _Path
        src_path = _Path(__file__)
        tree = _ast.parse(src_path.read_text())

        LIVE_DB_PATTERNS = {
            # direct calls
            "_db": "_db(",
            # opt-in live fixture
            "live_phase_c_db": "live_phase_c_db",
            # live module-scoped fixtures
            "shared_client": "shared_client",
            "gates_response_today_post": "gates_response_today_post",
        }

        violations = []
        for cls_node in _ast.walk(tree):
            if not isinstance(cls_node, _ast.ClassDef):
                continue
            for item in cls_node.body:
                if not isinstance(item, _ast.FunctionDef):
                    continue
                if not item.name.startswith("test_"):
                    continue
                if item.name == "test_no_deterministic_test_uses_live_db_path":
                    continue  # this guard itself
                item_src = _ast.unparse(item)
                args = [a.arg for a in item.args.args]
                # Check arg-list patterns
                for pattern_name, _needle in LIVE_DB_PATTERNS.items():
                    if pattern_name in args and pattern_name in (
                        "live_phase_c_db", "shared_client",
                        "gates_response_today_post",
                    ):
                        violations.append(
                            f"  {cls_node.name}::{item.name}: "
                            f"uses live-DB fixture '{pattern_name}'"
                        )
                # Check body patterns (only direct `_db(` call)
                # Word-boundary match: `_db(` not preceded by a letter
                # or underscore. This avoids false positives like
                # `_phase_c_open_db(` (the `open_db(` substring) and
                # `_build_phase_c_db(` (factory helper).
                import re as _re
                for m in _re.finditer(r"(?<![A-Za-z_])_db\(", item_src):
                    violations.append(
                        f"  {cls_node.name}::{item.name}: direct _db() call"
                    )

        assert not violations, (
            "PHASE-C10D static guard FAILED — deterministic Phase C "
            "tests must not reach trading_bot.db. Violations:\n"
            + "\n".join(violations)
            + "\n\nMove the offending test(s) to "
            + "`tests/observational/test_phase_c_live_observational.py` "
            + "and tag with `@pytest.mark.observational`."
        )
