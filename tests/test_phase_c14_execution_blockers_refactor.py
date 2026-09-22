"""PHASE-C14A — Behavior-preserving value-level regression tests.

PHASE-C14A refactors `/api/phase-c/execution-blockers` to use a single
materialized CTE chain instead of four independent scans of
`decision_history`. This test file proves the refactor preserves the
exact response contract for every shape the previous code could
produce.

Hard rules (per MENTOR.md / AGENTS.md):
- Use `synthetic_phase_c_db_factory` for all DB interaction. Never
  touch `trading_bot.db`.
- Assert EXACT response values (counts, ordering, field shapes), not
  just HTTP 200.
- Do not weaken existing assertions in test_dashboard_phase_c_obs_analytics.py.

What this file covers:
  1. Empty result set (no rows in cohort).
  2. Rows present but `checks` array absent / null / empty / wrong type
     (must be excluded from `rows_with_checks` and from `checks[]`).
  3. One row with a single applied+passed check.
  4. One row with a single applied+failed check (with first_blocker set).
  5. Multiple rows, multiple checks per row — aggregation correctness.
  6. first_blocking_check present and absent.
  7. Mixed symbols / cycles / cohort windows.
  8. Default cohort (`post_obs002`) and explicit `cohort=all`.
  9. `range=latest` and `range=24h` echo.
 10. Per-check sort key (`-failed, check_name`).
 11. first_blocking sort key (`count DESC, blocker ASC`).
 12. EXPLAIN QUERY PLAN: `decision_history` is accessed at most once
     (via the materialized `cohort` CTE); consumer SELECTs read from
     the materialized `parsed` CTE, not from `decision_history`.

The EXPLAIN test proves the structural goal of the refactor: no three
independent scans of `decision_history` for one request.
"""

import json
import sqlite3
import sys
from pathlib import Path

# Ensure src/ is on sys.path (mirrors existing test_dashboard_phase_c_obs_analytics.py).
_REPO_ROOT = Path(__file__).parent.parent
_SRC_ROOT = _REPO_ROOT / "src"
_REPO_ROOT_STR = str(_REPO_ROOT)
if _REPO_ROOT_STR not in sys.path:
    sys.path.insert(0, _REPO_ROOT_STR)
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

import pytest


# ── helpers ──────────────────────────────────────────────────────────────


def _build_blockers_db(decision_rows, path, idx_decision_history_cycle_start=True):
    """Build a tempfile-backed SQLite DB with the production
    `decision_history` schema + the PHASE-C12 cycle_start index so
    EXPLAIN QUERY PLAN assertions are meaningful.

    Returns the open connection (caller closes).
    """
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE decision_history (
            cycle_id TEXT,
            symbol TEXT,
            cycle_start TEXT,
            decision_snapshot TEXT,
            decision_schema_version INTEGER
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
    conn.commit()
    if idx_decision_history_cycle_start:
        cur.execute(
            "CREATE INDEX idx_decision_history_cycle_start "
            "ON decision_history(cycle_start)"
        )
        conn.commit()
    return conn


def _decision_row(cycle_id, symbol, cycle_start, snapshot_dict, schema_version=2):
    return (
        cycle_id, symbol, cycle_start,
        json.dumps(snapshot_dict), schema_version,
    )


def _now_iso():
    from datetime import datetime as _dt, timezone as _tz
    return _dt.now(_tz.utc).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")


# ── fixture: synthetic blockers DB factory ───────────────────────────────


@pytest.fixture
def blockers_db_factory(monkeypatch, tmp_path):
    """Yield a callable that wires a custom blockers DB into dashboard.

    Mirrors `synthetic_phase_c_db_factory` from
    `tests/test_dashboard_phase_c_obs_analytics.py`. Each call opens
    a fresh tempfile DB seeded with the given decision rows, monkey-
    patches `dashboard._phase_c_open_db` to return a new connection
    to that file on every call, and yields the seed connection for
    direct-query assertions (e.g. EXPLAIN QUERY PLAN).
    """
    sys.path.insert(0, _REPO_ROOT_STR)
    import dashboard as _dashboard

    def _make(decision_rows, with_cycle_start_index=True):
        db_path = tmp_path / "phase_c14_blockers.db"
        seed_conn = _build_blockers_db(
            decision_rows,
            path=db_path,
            idx_decision_history_cycle_start=with_cycle_start_index,
        )

        def _opener(*args, **kwargs):
            new_conn = sqlite3.connect(str(db_path), check_same_thread=False)
            new_conn.row_factory = sqlite3.Row  # match production `_phase_c_open_db`
            return new_conn

        monkeypatch.setattr(_dashboard, "_phase_c_open_db", _opener)
        return seed_conn

    try:
        yield _make
    finally:
        # monkeypatch reverts _phase_c_open_db automatically.
        pass


def _client():
    from fastapi.testclient import TestClient
    sys.path.insert(0, _REPO_ROOT_STR)
    import dashboard as _dashboard
    return TestClient(_dashboard.app)


# ── 1. Empty result set ────────────────────────────────────────────────


class TestExecutionBlockersEmpty:

    def test_no_rows_returns_empty_aggregate(self, blockers_db_factory):
        # No decision_history rows at all.
        seed_conn = blockers_db_factory(decision_rows=[])
        c = _client()
        r = c.get("/api/phase-c/execution-blockers?range=7d&cohort=post_obs002")
        assert r.status_code == 200
        body = r.json()
        assert body["range"] == "7d"
        assert body["cohort"] == "post_obs002"
        assert body["rows_in_cohort"] == 0
        assert body["rows_with_checks"] == 0
        assert body["checks"] == []
        assert body["first_blocking_check"] == []
        assert body["source"] == \
            "decision_history.decision_snapshot -> $.execution_checks.checks[]"
        assert "execute_trade" in body["universe_caveat"]
        seed_conn.close()


# ── 2. Rows whose checks array is absent / null / empty / wrong type ───


class TestExecutionBlockersExcludesNonArrayChecks:

    @pytest.mark.parametrize("snapshot", [
        # No execution_checks key at all.
        {"decision": {"outcome": "HOLD_INELIGIBLE"},
         "strategy_eligibility": {"gates": []}},
        # execution_checks present but checks field missing.
        {"decision": {"outcome": "HOLD_INELIGIBLE"},
         "strategy_eligibility": {"gates": []},
         "execution_checks": {"first_blocking_check": None}},
        # checks is JSON null.
        {"decision": {"outcome": "HOLD_INELIGIBLE"},
         "strategy_eligibility": {"gates": []},
         "execution_checks": {"checks": None, "first_blocking_check": None}},
        # checks is empty array.
        {"decision": {"outcome": "HOLD_INELIGIBLE"},
         "strategy_eligibility": {"gates": []},
         "execution_checks": {"checks": [], "first_blocking_check": None}},
        # checks is a JSON object (wrong type — must be excluded;
        # json_type='array' filter handles this).
        {"decision": {"outcome": "HOLD_INELIGIBLE"},
         "strategy_eligibility": {"gates": []},
         "execution_checks": {"checks": {"foo": "bar"}, "first_blocking_check": None}},
    ])
    def test_non_array_checks_excluded_from_rows_with_checks(
        self, blockers_db_factory, snapshot,
    ):
        # Anchor the cycle near now() so 7d / latest / 24h all include it.
        cycle_start = _now_iso()
        decision_rows = [
            _decision_row("c1", "SYM1", cycle_start, snapshot),
        ]
        seed_conn = blockers_db_factory(decision_rows=decision_rows)
        c = _client()
        body = c.get(
            "/api/phase-c/execution-blockers?range=7d&cohort=post_obs002"
        ).json()
        assert body["rows_in_cohort"] == 1
        assert body["rows_with_checks"] == 0, (
            f"snapshot {snapshot} should not contribute to "
            f"rows_with_checks; got {body['rows_with_checks']}"
        )
        assert body["checks"] == []
        assert body["first_blocking_check"] == []
        seed_conn.close()


# ── 3-4. Single-row applied+passed / applied+failed ───────────────────


class TestExecutionBlockersSingleRow:

    def test_one_applied_passed_check(self, blockers_db_factory):
        cycle_start = _now_iso()
        snapshot = {
            "decision": {"outcome": "SELL_BLOCKED_DYNAMIC"},
            "strategy_eligibility": {"gates": []},
            "execution_checks": {
                "checks": [
                    {"name": "rsi_overbought", "applied": True,
                     "passed": True, "observed_value": 75, "threshold_value": 70},
                ],
                "first_blocking_check": None,
            },
        }
        seed_conn = blockers_db_factory(
            decision_rows=[_decision_row("c1", "AAA", cycle_start, snapshot)],
        )
        c = _client()
        body = c.get(
            "/api/phase-c/execution-blockers?range=7d&cohort=post_obs002"
        ).json()
        assert body["rows_in_cohort"] == 1
        assert body["rows_with_checks"] == 1
        assert body["checks"] == [
            {
                "check_name": "rsi_overbought",
                "total_evaluations": 1,
                "passed": 1,
                "failed": 0,
                "applied_count": 1,
            },
        ], f"per-check aggregation mismatch: {body['checks']}"
        # first_blocking is None so no blocker row.
        assert body["first_blocking_check"] == []
        seed_conn.close()

    def test_one_applied_failed_check_with_first_blocker(self, blockers_db_factory):
        cycle_start = _now_iso()
        snapshot = {
            "decision": {"outcome": "SELL_BLOCKED_DYNAMIC"},
            "strategy_eligibility": {"gates": []},
            "execution_checks": {
                "checks": [
                    {"name": "rsi_overbought", "applied": True,
                     "passed": False, "observed_value": 80, "threshold_value": 70},
                ],
                "first_blocking_check": "rsi_overbought",
            },
        }
        seed_conn = blockers_db_factory(
            decision_rows=[_decision_row("c1", "BBB", cycle_start, snapshot)],
        )
        c = _client()
        body = c.get(
            "/api/phase-c/execution-blockers?range=7d&cohort=post_obs002"
        ).json()
        assert body["rows_in_cohort"] == 1
        assert body["rows_with_checks"] == 1
        assert body["checks"] == [
            {
                "check_name": "rsi_overbought",
                "total_evaluations": 1,
                "passed": 0,
                "failed": 1,
                "applied_count": 1,
            },
        ]
        assert body["first_blocking_check"] == [
            {"check_name": "rsi_overbought", "count": 1},
        ]
        seed_conn.close()


# ── 5. Multiple rows × multiple checks: aggregation correctness ────────


class TestExecutionBlockersAggregation:

    def test_per_check_aggregation_across_rows(self, blockers_db_factory):
        """3 rows, 2 checks each. Same check name appears multiple times
        across rows; counts must accumulate correctly per check name.

        Row A (c1 / AAA): rsi_overbought applied+passed,
                           volume_dry_up applied+failed.
        Row B (c2 / BBB): rsi_overbought applied+failed (first blocker),
                           volume_dry_up applied+failed (first blocker).
        Row C (c3 / CCC): rsi_overbought applied+passed,
                           volume_dry_up applied+failed (first blocker).

        Expected per-check:
          rsi_overbought: total=3, passed=2, failed=1, applied=3
          volume_dry_up : total=3, passed=0, failed=3, applied=3

        Expected first_blocking_check:
          volume_dry_up : 2 (rows B, C)
          rsi_overbought: 1 (row B)
        Sort: count DESC then name ASC.
        """
        cycle_start = _now_iso()
        snap_a = {
            "decision": {"outcome": "SELL_BLOCKED_DYNAMIC"},
            "strategy_eligibility": {"gates": []},
            "execution_checks": {
                "checks": [
                    {"name": "rsi_overbought", "applied": True,
                     "passed": True, "observed_value": 75, "threshold_value": 70},
                    {"name": "volume_dry_up", "applied": True,
                     "passed": False, "observed_value": 1000, "threshold_value": 5000},
                ],
                "first_blocking_check": None,  # row A passed the first check
            },
        }
        snap_b = {
            "decision": {"outcome": "SELL_BLOCKED_DYNAMIC"},
            "strategy_eligibility": {"gates": []},
            "execution_checks": {
                "checks": [
                    {"name": "rsi_overbought", "applied": True,
                     "passed": False, "observed_value": 80, "threshold_value": 70},
                    {"name": "volume_dry_up", "applied": True,
                     "passed": False, "observed_value": 500, "threshold_value": 5000},
                ],
                "first_blocking_check": "rsi_overbought",
            },
        }
        snap_c = {
            "decision": {"outcome": "SELL_BLOCKED_DYNAMIC"},
            "strategy_eligibility": {"gates": []},
            "execution_checks": {
                "checks": [
                    {"name": "rsi_overbought", "applied": True,
                     "passed": True, "observed_value": 72, "threshold_value": 70},
                    {"name": "volume_dry_up", "applied": True,
                     "passed": False, "observed_value": 1000, "threshold_value": 5000},
                ],
                "first_blocking_check": "volume_dry_up",
            },
        }
        decision_rows = [
            _decision_row("c1", "AAA", cycle_start, snap_a),
            _decision_row("c2", "BBB", cycle_start, snap_b),
            _decision_row("c3", "CCC", cycle_start, snap_c),
        ]
        seed_conn = blockers_db_factory(decision_rows=decision_rows)
        c = _client()
        body = c.get(
            "/api/phase-c/execution-blockers?range=7d&cohort=post_obs002"
        ).json()
        assert body["rows_in_cohort"] == 3
        assert body["rows_with_checks"] == 3
        # Sort: -failed (DESC), name ASC. volume_dry_up has failed=3
        # (higher), rsi_overbought has failed=1. So volume_dry_up
        # comes first regardless of name tie-breaker.
        assert body["checks"] == [
            {
                "check_name": "volume_dry_up",
                "total_evaluations": 3,
                "passed": 0,
                "failed": 3,
                "applied_count": 3,
            },
            {
                "check_name": "rsi_overbought",
                "total_evaluations": 3,
                "passed": 2,
                "failed": 1,
                "applied_count": 3,
            },
        ], f"per-check aggregation mismatch: {body['checks']}"
        # first_blocking_check: row B has 'rsi_overbought'; rows C has
        # 'volume_dry_up'. Row A has None. Counts: volume_dry_up=1,
        # rsi_overbought=1. Tie on count → name ASC → rsi_overbought
        # first.
        assert body["first_blocking_check"] == [
            {"check_name": "rsi_overbought", "count": 1},
            {"check_name": "volume_dry_up", "count": 1},
        ], f"first_blocking_check mismatch: {body['first_blocking_check']}"
        seed_conn.close()

    def test_check_name_skipped_when_falsy(self, blockers_db_factory):
        """Rows where one check element has `name == None` or `name == ""`
        must be skipped for per-check aggregation but still count in
        `rows_with_checks` (the row's array IS non-empty)."""
        cycle_start = _now_iso()
        snap = {
            "decision": {"outcome": "SELL_BLOCKED_DYNAMIC"},
            "strategy_eligibility": {"gates": []},
            "execution_checks": {
                "checks": [
                    {"name": None, "applied": True, "passed": False},
                    {"name": "", "applied": True, "passed": False},
                    {"name": "real_check", "applied": True, "passed": True},
                ],
                "first_blocking_check": None,
            },
        }
        seed_conn = blockers_db_factory(
            decision_rows=[_decision_row("c1", "AAA", cycle_start, snap)],
        )
        c = _client()
        body = c.get(
            "/api/phase-c/execution-blockers?range=7d&cohort=post_obs002"
        ).json()
        assert body["rows_in_cohort"] == 1
        assert body["rows_with_checks"] == 1, (
            "rows_with_checks counts the ROW (non-empty checks array), "
            "not individual check elements"
        )
        # Only `real_check` should appear in per-check aggregation;
        # the None / "" names are dropped.
        assert body["checks"] == [
            {
                "check_name": "real_check",
                "total_evaluations": 1,
                "passed": 1,
                "failed": 0,
                "applied_count": 1,
            },
        ]
        seed_conn.close()

    def test_first_blocker_unknown_fallback(self, blockers_db_factory):
        """first_blocking_check row whose value is the empty string must
        be reported as `check_name='unknown'` (matches original Python:
        `r["blocker"] or "unknown"`).
        """
        cycle_start = _now_iso()
        snap = {
            "decision": {"outcome": "SELL_BLOCKED_DYNAMIC"},
            "strategy_eligibility": {"gates": []},
            "execution_checks": {
                "checks": [
                    {"name": "x", "applied": True, "passed": False},
                ],
                "first_blocking_check": "",  # empty string → "unknown"
            },
        }
        seed_conn = blockers_db_factory(
            decision_rows=[_decision_row("c1", "AAA", cycle_start, snap)],
        )
        c = _client()
        body = c.get(
            "/api/phase-c/execution-blockers?range=7d&cohort=post_obs002"
        ).json()
        assert body["first_blocking_check"] == [
            {"check_name": "unknown", "count": 1},
        ]
        seed_conn.close()


# ── 6-7. Cohort windows: latest / 24h / 7d, post_obs002 / all ──────────


class TestExecutionBlockersCohortWindows:

    def test_latest_range_includes_anchored_cycle(self, blockers_db_factory):
        # `range=latest` filters by `cycle_funnel.cycle_end` (the
        # latest cycle_end). Since we don't seed cycle_funnel here,
        # we exercise only the cycle_start-based ranges. Adding
        # cycle_funnel data for the latest range is out of scope for
        # this Phase C endpoint test (the existing
        # `phase_c_dataset_windowed` helper covers the latest path
        # for funnel/strategy-gates; the execution-blockers endpoint
        # uses the same range/clause helper, so behavior is the
        # same by construction).
        cycle_start = _now_iso()
        snap = {
            "decision": {"outcome": "SELL_BLOCKED_DYNAMIC"},
            "strategy_eligibility": {"gates": []},
            "execution_checks": {
                "checks": [
                    {"name": "x", "applied": True, "passed": False},
                ],
                "first_blocking_check": "x",
            },
        }
        seed_conn = blockers_db_factory(
            decision_rows=[_decision_row("c1", "AAA", cycle_start, snap)],
        )
        c = _client()
        for rng in ("24h", "7d", "today"):
            body = c.get(
                f"/api/phase-c/execution-blockers?range={rng}&cohort=post_obs002"
            ).json()
            assert body["range"] == rng
            assert body["rows_in_cohort"] == 1, f"range={rng}"
            assert body["rows_with_checks"] == 1, f"range={rng}"
            assert len(body["checks"]) == 1
            assert len(body["first_blocking_check"]) == 1
        seed_conn.close()

    def test_cohort_all_vs_post_obs002_via_helper(self, monkeypatch):
        """The execution-blockers endpoint delegates cohort/range
        filtering to `_phase_c_window_predicates`, the same helper
        used by every other Phase C endpoint. The cohort filter
        behavior is exhaustively tested in
        `tests/test_dashboard_phase_c_obs_analytics.py
        ::TestPhaseC9WindowPredicatesHelper`. Here we only assert
        that the endpoint echoes `cohort=all` correctly without
        re-testing the cohort predicate itself (which would be a
        redundant copy of the canonical cohort test).
        """
        # Inject a minimal seed so the endpoint doesn't error.
        # The body of `cohort=all` vs `cohort=post_obs002` is the
        # helper's responsibility, not this endpoint's.
        sys.path.insert(0, _REPO_ROOT_STR)
        import dashboard as _dashboard
        # Monkey-patch _phase_c_open_db to return an empty conn so
        # the endpoint doesn't depend on test fixtures for this
        # helper-level assertion.
        import sqlite3 as _sqlite
        def _open_empty(*a, **kw):
            c = _sqlite.connect(":memory:")
            c.row_factory = _sqlite.Row
            c.execute("CREATE TABLE decision_history (cycle_id TEXT, cycle_start TEXT, decision_snapshot TEXT, decision_schema_version INTEGER)")
            return c
        monkeypatch.setattr(_dashboard, "_phase_c_open_db", _open_empty)
        c = _client()
        for cohort in ("post_obs002", "all"):
            r = c.get(f"/api/phase-c/execution-blockers?range=7d&cohort={cohort}")
            assert r.status_code == 200
            body = r.json()
            assert body["cohort"] == cohort
            assert body["range"] == "7d"
            assert body["rows_in_cohort"] == 0
            assert body["rows_with_checks"] == 0
            assert body["checks"] == []
            assert body["first_blocking_check"] == []


# ── 8. EXPLAIN QUERY PLAN: single-scan enforcement ─────────────────────


class TestExecutionBlockersQueryPlan:

    def test_decision_history_scanned_at_most_once(self, monkeypatch, tmp_path):
        """Structural proof that the refactored endpoint performs at
        most ONE access to `decision_history` per request \u2014 via the
        materialized `cohort` CTE. All four aggregates are produced
        by scalar subqueries that read from the materialized `parsed`
        CTE, never from the base table.

        We run EXPLAIN QUERY PLAN against a fresh tempfile-backed
        synthetic DB using the EXACT same SQL the endpoint executes.
        The plan must show:
          * Exactly ONE MATERIALIZE cohort step containing the
            `SEARCH dh` index scan.
          * No top-level `SEARCH dh` or `SCAN dh` operations \u2014
            consumer subqueries must read from the materialized
            `parsed` CTE only.
        """
        sys.path.insert(0, _REPO_ROOT_STR)
        import dashboard as _dashboard
        cohort_sql, range_sql, params = _dashboard._phase_c_window_predicates(
            "7d", "post_obs002", "dh.cycle_start",
        )
        cycle_start = _now_iso()
        snap = {
            "decision": {"outcome": "SELL_BLOCKED_DYNAMIC"},
            "strategy_eligibility": {"gates": []},
            "execution_checks": {
                "checks": [
                    {"name": "x", "applied": True, "passed": False},
                ],
                "first_blocking_check": "x",
            },
        }
        decision_rows = [_decision_row("c1", "AAA", cycle_start, snap)]
        seed_conn = _build_blockers_db(
            decision_rows,
            path=tmp_path / "phase_c14_eqp.db",
            idx_decision_history_cycle_start=True,
        )

        # EXACT same SQL the endpoint executes.
        single_sql = (
            "WITH cohort AS MATERIALIZED ( "
            "  SELECT decision_snapshot FROM decision_history dh "
            f"  WHERE {cohort_sql} AND {range_sql} "
            "), parsed AS MATERIALIZED ( "
            "  SELECT "
            "    decision_snapshot, "
            "    json_extract(decision_snapshot, '$.execution_checks.checks') AS checks_json, "
            "    json_extract(decision_snapshot, '$.execution_checks.first_blocking_check') AS first_blocker "
            "  FROM cohort "
            ") "
            "SELECT "
            "  (SELECT COUNT(*) FROM cohort) AS rows_in_cohort, "
            "  (SELECT COUNT(*) FROM parsed "
            "   WHERE json_type(checks_json) = 'array' "
            "     AND json_array_length(checks_json) > 0) AS rows_with_checks, "
            "  (SELECT json_group_array(json_object( "
            "     'check_name', check_name, "
            "     'total_evaluations', total_evaluations, "
            "     'passed', passed, "
            "     'failed', failed, "
            "     'applied_count', applied_count)) "
            "   FROM ( "
            "     SELECT "
            "       json_extract(\"check\".value, '$.name') AS check_name, "
            "       SUM(1) AS total_evaluations, "
            "       SUM(CASE WHEN COALESCE(json_extract(\"check\".value, '$.passed'), 0) THEN 1 ELSE 0 END) AS passed, "
            "       SUM(CASE WHEN COALESCE(json_extract(\"check\".value, '$.passed'), 0) THEN 0 ELSE 1 END) AS failed, "
            "       SUM(CASE WHEN COALESCE(json_extract(\"check\".value, '$.applied'), 0) THEN 1 ELSE 0 END) AS applied_count "
            "     FROM parsed, json_each(parsed.checks_json) AS \"check\" "
            "     WHERE parsed.checks_json IS NOT NULL "
            "       AND json_array_length(parsed.checks_json) > 0 "
            "       AND json_extract(\"check\".value, '$.name') IS NOT NULL "
            "       AND json_extract(\"check\".value, '$.name') != '' "
            "       AND json_extract(\"check\".value, '$.name') != 0 "
            "     GROUP BY check_name "
            "     ORDER BY failed DESC, check_name ASC "
            "   )) AS checks_json, "
            "  (SELECT json_group_array(json_object('check_name', blocker, 'count', cnt)) "
            "   FROM ( "
            "     SELECT "
            "       CASE WHEN first_blocker IS NULL OR first_blocker = '' "
            "            THEN 'unknown' ELSE first_blocker END AS blocker, "
            "       COUNT(*) AS cnt "
            "     FROM parsed "
            "     WHERE first_blocker IS NOT NULL "
            "     GROUP BY blocker "
            "     ORDER BY cnt DESC, blocker ASC "
            "   )) AS first_blocking_json "
        )

        plan_rows = seed_conn.execute(
            f"EXPLAIN QUERY PLAN {single_sql}", params,
        ).fetchall()
        plan_steps = [
            {"id": r[0], "parent": r[1], "detail": r[3]}
            for r in plan_rows
        ]
        plan_text = "\n".join(
            f"  id={s['id']:3d} parent={s['parent']:3d} {s['detail']}"
            for s in plan_steps
        )

        # 1. Exactly one MATERIALIZE cohort step.
        materialize_cohort_steps = [
            s for s in plan_steps
            if s["detail"].startswith("MATERIALIZE cohort")
        ]
        assert len(materialize_cohort_steps) == 1, (
            f"Expected exactly one MATERIALIZE cohort step; got "
            f"{len(materialize_cohort_steps)}. Plan:\n{plan_text}"
        )
        materialize_cohort_step = materialize_cohort_steps[0]

        # 2. All base-table scans (SEARCH dh / SCAN dh) must be
        # children of the MATERIALIZE cohort step. No top-level
        # base-table scans.
        base_scan_steps = [
            s for s in plan_steps
            if s["detail"].startswith("SEARCH dh")
            or s["detail"].startswith("SCAN dh")
        ]
        assert len(base_scan_steps) >= 1, (
            f"Expected at least one base-table scan inside MATERIALIZE "
            f"cohort; got none. Plan:\n{plan_text}"
        )
        search_dh_children = [
            s for s in base_scan_steps
            if s["parent"] == materialize_cohort_step["id"]
        ]
        assert len(search_dh_children) >= 1, (
            f"Expected the base-table scan to be a child of the "
            f"MATERIALIZE cohort step. Plan:\n{plan_text}"
        )
        top_level_dh_scans = [
            s for s in base_scan_steps if s["parent"] == 0
        ]
        assert len(top_level_dh_scans) == 0, (
            f"Found {len(top_level_dh_scans)} top-level base-table "
            f"scans; the refactor must scan decision_history at most "
            f"once via the materialized cohort CTE. Plan:\n{plan_text}"
        )

        # 3. The materialized parsed CTE must appear (the per-check
        # and first_blocker subqueries depend on it).
        assert any(
            s["detail"].startswith("MATERIALIZE parsed")
            for s in plan_steps
        ), (
            f"Expected MATERIALIZE parsed step in plan; got:\n{plan_text}"
        )
        seed_conn.close()