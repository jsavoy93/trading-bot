"""Live Observational Phase C Tests — empirical checks against the running bot.

PHASE-C10D relocation.

These tests verify Phase C endpoints and OBS-002 invariants against the
**live** `trading_bot.db` populated by the running SmartBot. They are
**observational / empirical** — they observe current state, not a
deterministic synthetic dataset.

**Why they are separated:**
- The deterministic Phase C tests in
  `tests/test_dashboard_phase_c_obs_analytics.py` use synthetic
  in-memory SQLite fixtures via `monkeypatch` on
  `dashboard._phase_c_open_db`. They are fast, hermetic, and CI-safe.
- These observational tests MUST read `trading_bot.db` because they
  verify invariants that only hold in real SmartBot operation:
  recent cycle completion, post-OBS-002 coverage, gate structure
  consistency, etc.

**Hard rules (from PHASE-C10D protocol):**
- Read-only: never write to `trading_bot.db`.
- Never pause, stop, or restart SmartBot.
- Never mutate production state.
- Never call brokerage or network services.
- Tolerate concurrent SmartBot writes: where the assertion requires
  internal consistency across two derived counts, use ONE atomic SQL
  statement (SQLite serializes writes via the database lock, so a
  single statement observes one snapshot).
- Avoid repeated expensive endpoint calls when one bounded read can
  prove the invariant.

**Markers:**
This module declares `pytestmark = pytest.mark.observational`.
The default pytest run (via `pytest.ini` addopts
`-m "not observational"`) does NOT collect these tests.
Run them explicitly:

```
pytest -m observational tests/observational/test_phase_c_live_observational.py
```

This is the diagnostic / release-gate / post-deploy verification path.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest


# PHASE-C10D marker — opt in to the `observational` marker so
# pytest skips this whole module during default collection.
pytestmark = pytest.mark.observational


REPO_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = REPO_ROOT / "trading_bot.db"

# PHASE-C10D: copied verbatim from
# `tests/test_dashboard_phase_c_obs_analytics.py` (the deterministic
# Phase C test file) — same value as `dashboard.py`'s
# `OBS_002_DEPLOYMENT_BOUNDARY_UTC`. Kept as a local constant here so
# this module does NOT import the deterministic test file (which
# would import `dashboard.py` and trigger module-level side effects).
OBS_002_BOUNDARY = "2026-09-14T20:24:55"


# ─────────────────────────────────────────────────────────────────────────
# Minimal live-DB helpers (scope-bounded to this module only)
# ─────────────────────────────────────────────────────────────────────────


def _db():
    """Open trading_bot.db in read-only mode.

    PHASE-C10D: this is the ONLY path the observational tests use to
    read live state. It MUST remain read-only and MUST NOT pause or
    otherwise interact with SmartBot.
    """
    if not DB_PATH.exists():
        pytest.skip(f"trading_bot.db not present at {DB_PATH}")
    # Open in URI read-only mode (`mode=ro`) so any accidental write
    # statement raises sqlite3.OperationalError instead of mutating
    # production state. Defense in depth.
    db_uri = f"file:{DB_PATH}?mode=ro"
    conn = sqlite3.connect(db_uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _client():
    """Build a FastAPI TestClient without spinning up the live bot.

    PHASE-C10D: this routes through `dashboard.app` exactly like the
    deterministic tests do. The endpoint calls will read live
    `trading_bot.db` via the un-patched `_phase_c_open_db` (because
    we are NOT installing the synthetic seam in the observational
    module — that is the whole point).
    """
    from fastapi.testclient import TestClient

    sys.path.insert(0, str(REPO_ROOT))
    import dashboard as _dashboard

    return TestClient(_dashboard.app)


@pytest.fixture(scope="module")
def shared_client():
    """Module-scoped TestClient.

    Importing dashboard.py takes a few seconds; reusing one TestClient
    across the equivalence tests avoids re-paying that cost. The
    observational tests are run explicitly (release gate) so a few
    seconds of import overhead is acceptable.
    """
    return _client()


@pytest.fixture(scope="module")
def gates_response_today_post(shared_client):
    """Cached `strategy-gates?range=today&cohort=post_obs002` response.

    The endpoint can take 25-30s per call on the production DB
    because of JSON extraction over hundreds of thousands of
    `decision_history` rows. We cache ONE bounded response at
    module scope so the equivalence tests that share this fixture
    don't pay the cost repeatedly.

    PHASE-C10D: this cache is scoped to the module and is read-only;
    it is recomputed at the start of each observational run.
    """
    return shared_client.get(
        "/api/phase-c/strategy-gates?range=today&cohort=post_obs002"
    ).json()


# ─────────────────────────────────────────────────────────────────────────
# 13 LIVE_DB_READ tests relocated from
# `tests/test_dashboard_phase_c_obs_analytics.py` (PHASE-C10D).
#
# Each test preserves its semantic assertion unchanged. Only the
# surrounding fixture wiring and import surface moved.
# ─────────────────────────────────────────────────────────────────────────


# 1. ─── OBS-002 regression guard: HOLD_INELIGIBLE never contributes
#        a failed gate when its gates[] is empty. Atomic single-query
#        formulation so concurrent SmartBot writes cannot produce
#        inconsistent counts between hi_total and hi_with_gates.

def test_no_hold_ineligible_to_failed_gate_inference():
    """Critical OBS-002 invariant.

    A HOLD_INELIGIBLE row with empty `gates[]` MUST contribute zero
    gate failures to the Strategy Gate Failure Frequency card.
    PHASE-C8 / PHASE-C10D: the previous version issued two separate
    SELECTs against `decision_history` and could observe inconsistent
    counts while SmartBot writes concurrently (one row ~every 21s).
    The fix below uses a single atomic query so both counts are
    derived from one snapshot.
    """
    conn = _db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            SUM(CASE WHEN json_extract(decision_snapshot, '$.decision.outcome') = 'HOLD_INELIGIBLE'
                     THEN 1 ELSE 0 END) AS hi_total,
            SUM(CASE WHEN json_extract(decision_snapshot, '$.decision.outcome') = 'HOLD_INELIGIBLE'
                          AND json_extract(decision_snapshot, '$.strategy_eligibility.gates') IS NOT NULL
                          AND json_array_length(json_extract(decision_snapshot, '$.strategy_eligibility.gates')) > 0
                     THEN 1 ELSE 0 END) AS hi_with_gates
        FROM decision_history
        WHERE cycle_start >= ?
        """,
        (OBS_002_BOUNDARY,),
    )
    row = cur.fetchone()
    conn.close()
    if row is None:
        pytest.skip("no decision_history rows in the post-OBS-002 window")
    hi_total = int(row["hi_total"] or 0)
    hi_with_gates = int(row["hi_with_gates"] or 0)
    assert hi_total == 0 or hi_with_gates == hi_total, (
        f"HOLD_INELIGIBLE rows must all carry structured gates[] "
        f"({hi_with_gates}/{hi_total}); endpoint must never infer "
        f"failed gate from outcome alone"
    )


# 2-4. ─── Funnel aggregation empirical checks against live data.

def test_funnel_latest_returns_one_cycle():
    """`range=latest` must return at most one cycle's worth of funnel data."""
    c = _client()
    r = c.get("/api/phase-c/funnel?range=latest")
    body = r.json()
    if body.get("cycles") is None:
        pytest.skip("funnel endpoint returned no cycles key (DB missing?)")
    assert body["cycles"] == 1, f"expected exactly 1 cycle, got {body['cycles']}"


def test_funnel_24h_returns_something():
    """`range=24h` must return a non-negative cycle count (may be zero if no recent cycles)."""
    c = _client()
    r = c.get("/api/phase-c/funnel?range=24h")
    body = r.json()
    if body.get("cycles") is None:
        pytest.skip("funnel endpoint returned no cycles key (DB missing?)")
    assert body["cycles"] >= 0


def test_funnel_cohort_all_includes_pre_obs002():
    """`cohort=all` must include ≥ the cycles returned by `cohort=post_obs002`."""
    c = _client()
    r1 = c.get("/api/phase-c/funnel?cohort=all&range=7d").json()
    r2 = c.get("/api/phase-c/funnel?cohort=post_obs002&range=7d").json()
    if "cycles" not in r1 or "cycles" not in r2:
        pytest.skip("funnel endpoint missing cycles key (DB missing?)")
    assert r1["cycles"] >= r2["cycles"]


# 5. ─── Execution-blockers back-fill invariant.

def test_per_check_totals_sum_to_with_checks_rows():
    """For SELL_BLOCKED_DYNAMIC rows, every execution check is back-filled
    exactly once per row. So SUM(total_evaluations across distinct check
    names) == rows_with_checks × (number of distinct check names).
    """
    c = _client()
    r = c.get("/api/phase-c/execution-blockers?range=latest")
    body = r.json()
    if body.get("rows_with_checks", 0) == 0:
        pytest.skip("no rows with execution checks in window")
    per_check_total = [ck["total_evaluations"] for ck in body["checks"]]
    assert len(set(per_check_total)) == 1
    assert per_check_total[0] == body["rows_with_checks"]


# 6. ─── Outcome distribution empirical invariant.

def test_pct_sums_to_1():
    """Sum of `pct` across all outcome rows must equal 1.0 (within tolerance)."""
    c = _client()
    r = c.get("/api/phase-c/outcomes?range=latest")
    body = r.json()
    if body.get("total_decisions", 0) == 0:
        pytest.skip("no decisions in window")
    total_pct = sum(o["pct"] for o in body["outcomes"])
    assert abs(total_pct - 1.0) < 1e-9


# 7. ─── Coverage post-OBS-002 100% invariant.

def test_post_band_invariant_holds_in_observation_window():
    """OBS-002 long-run validation proved post-OBS-002 coverage is 100%.
    For `range=latest` (unambiguous boundary), the endpoint must report
    complete_cycles == cycle_count for the post band.
    """
    c = _client()
    r = c.get("/api/phase-c/coverage?range=latest")
    body = r.json()
    post = body["post_obs002"]
    if post["cycle_count"] == 0:
        pytest.skip("no post-OBS-002 cycles in the latest window")
    assert post["incomplete_cycles"] == 0
    assert post["total_missing_symbol_decisions"] == 0


# 8. ─── Cycle-funnel / decision-history cross-table invariant.

def test_decision_history_invariant():
    """Phase B invariant: cycle_funnel.analyzed_count ==
    COUNT(DISTINCT decision_history.symbol) for the latest completed
    cycle. Phase C must NOT violate this contract.
    """
    conn = _db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT cf.cycle_id, cf.analyzed_count,
               (SELECT COUNT(DISTINCT dh.symbol) FROM decision_history dh
                 WHERE dh.cycle_id = cf.cycle_id) AS D
        FROM cycle_funnel cf
        WHERE cf.cycle_end IS NOT NULL
          AND cf.cycle_start >= ?
        ORDER BY cf.cycle_end DESC
        LIMIT 1
        """,
        (OBS_002_BOUNDARY,),
    )
    row = cur.fetchone()
    conn.close()
    if row is None:
        pytest.skip("no post-OBS-002 cycle to check")
    assert int(row["analyzed_count"]) == int(row["D"]), (
        f"OBS-002 invariant broken: cycle_funnel.analyzed_count="
        f"{row['analyzed_count']} but COUNT(DISTINCT decision_history.symbol)="
        f"{row['D']} for cycle {row['cycle_id']}"
    )


# 9. ─── Strategy-gates response shape, today-bounded (cached).

def test_strategy_gates_response_shape_after_refactor(gates_response_today_post):
    """`today` bounded cut covers the response shape; the 7d / cohort=all
    paths are exercised by the synthetic-DB tests in
    TestGateAggregationAppliedFilter.
    """
    body = gates_response_today_post
    assert body["range"] == "today"
    assert body["cohort"] == "post_obs002"
    assert "rows_in_cohort" in body
    assert "gate_rows_aggregated" in body
    assert "gates" in body
    for g in body["gates"]:
        assert "applied_count" in g
        assert "total_evaluations" in g
        assert "passed" in g
        assert "failed" in g
        assert "failure_rate" in g
        # PHASE-C7 invariant: applied_count == total_evaluations.
        assert g["applied_count"] == g["total_evaluations"]


# 10. ─── Strategy-gates cohort=all superset of post_obs002 (today cut).

def test_strategy_gates_cohort_all_is_superset_of_post_obs002(shared_client):
    """For the bounded `today` range (fast), cohort=all must have >=
    the same rows_in_cohort as cohort=post_obs002. The 7d version of
    this invariant is covered by the synthetic-DB tests in
    TestGateAggregationAppliedFilter.
    """
    post = shared_client.get(
        "/api/phase-c/strategy-gates?range=today&cohort=post_obs002"
    ).json()
    all_cohort = shared_client.get(
        "/api/phase-c/strategy-gates?range=today&cohort=all"
    ).json()
    assert all_cohort["rows_in_cohort"] >= post["rows_in_cohort"]
    for g in all_cohort["gates"]:
        assert g["applied_count"] == g["total_evaluations"]


# 11. ─── Funnel cohort=all superset of post_obs002 (7d).

def test_funnel_cohort_all_superset_of_post_obs002(shared_client):
    """cohort=all must produce a superset (per-key >=) of cohort=post_obs002
    for the 7d window.
    """
    post = shared_client.get("/api/phase-c/funnel?range=7d&cohort=post_obs002").json()
    all_cohort = shared_client.get("/api/phase-c/funnel?range=7d&cohort=all").json()
    assert all_cohort["cycles"] >= post["cycles"]
    for k in post["forward_path"]:
        assert all_cohort["forward_path"][k] >= post["forward_path"][k]


# 12. ─── Latest funnel returns at most one cycle.

def test_latest_returns_one_or_zero_cycles(shared_client):
    """`range=latest` may return zero cycles (if no cycle has completed
    since OBS-002) or exactly one. Counts must be 0 in the zero case.
    """
    body = shared_client.get("/api/phase-c/funnel?range=latest&cohort=post_obs002").json()
    assert body["cycles"] <= 1
    if body["cycles"] == 0:
        for k in body["forward_path"]:
            assert body["forward_path"][k] == 0


# 13. ─── PHASE-C7 WHERE clause still present: applied_count == total_evaluations.

def test_applied_filter_still_excludes_applied_false(gates_response_today_post):
    """PHASE-C7's WHERE clause must still be present in the strategy-gates
    endpoint's SQL. We verify this by reading the rendered response and
    confirming every gate's applied_count equals its total_evaluations
    (PHASE-C7's invariant). The 7d version of this invariant is covered
    by the synthetic-DB tests in TestGateAggregationAppliedFilter.
    """
    body = gates_response_today_post
    for g in body["gates"]:
        assert g["applied_count"] == g["total_evaluations"], (
            f"PHASE-C7 invariant broken: applied_count != total_evaluations "
            f"for gate {g['gate_name']}"
        )
    assert "applied" in body.get("inference_rule", "").lower()
