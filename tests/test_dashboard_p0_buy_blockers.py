"""P0 BUY→HOLD Dashboard Attribution — endpoint tests.

Verifies the two new read-only endpoints that surface the actual
decision-time truth about why analyzer-stage BUYs did or did not
become final BUYs:

  /api/buy-blockers/summary      — counts + reason buckets (no rows)
  /api/buy-blockers              — same plus paginated detail rows

Reads `decision_history.decision_snapshot.signal_pipeline` (the
additive block introduced by PR #106). Pre-PR #106 rows are NOT
counted as unknown downgrades; they are reported separately as
`attribution_unavailable_count`.

The 7d default reproduces the verified prospective-window stats:
analyzer_buy_count == 19, final_buy_count == 0, downgraded_count == 19,
low_average_volume count == 19. (Live-verification test below
asserts this against the real trading_bot.db.)

Tests are read-only. They use a synthetic in-memory sqlite DB that
mirrors the schema, then monkey-patch dashboard._p0_open_db to route
the endpoints at the synthetic DB. monkeypatch restores the original
opener automatically when each test finishes.
"""

import json
import sqlite3
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = REPO_ROOT / "templates" / "dashboard.html"

# Mirrors dashboard.P0_PROSPECTIVE_BOUNDARY_UTC — the systemd
# ExecMainStartTimestamp from the PR #106 deploy.
P0_BOUNDARY = "2026-09-30T14:10:34+00:00"


def _build_synthetic_p0_db(decision_rows=None, decision_rows_with_pipeline=None):
    """Build an in-memory sqlite DB with a controlled synthetic dataset.

    Args:
        decision_rows: list of (cycle_id, symbol, cycle_start, decision_snapshot_json, decision_schema_version)
            Rows WITHOUT a signal_pipeline block (pre-PR #106 data).
        decision_rows_with_pipeline: list of (cycle_id, symbol, cycle_start, decision_snapshot_json, decision_schema_version)
            Rows WITH a signal_pipeline block. Used to inject the
            controlled analyzer-BUY / final-HOLD scenarios.

    Returns the open connection (caller closes it).
    """
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE decision_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cycle_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            cycle_start TEXT NOT NULL,
            session_id INTEGER,
            decision_schema_version INTEGER NOT NULL,
            decision_snapshot TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            analytics_persistence_version INTEGER NOT NULL DEFAULT 0,
            UNIQUE(cycle_id, symbol)
        )
        """
    )
    cur.execute(
        "CREATE INDEX idx_decision_history_cycle_start "
        "ON decision_history(cycle_start)"
    )
    if decision_rows:
        for row in decision_rows:
            cur.execute(
                "INSERT INTO decision_history "
                "(cycle_id, symbol, cycle_start, decision_schema_version, decision_snapshot) "
                "VALUES (?, ?, ?, ?, ?)",
                row,
            )
    if decision_rows_with_pipeline:
        for row in decision_rows_with_pipeline:
            cur.execute(
                "INSERT INTO decision_history "
                "(cycle_id, symbol, cycle_start, decision_schema_version, decision_snapshot) "
                "VALUES (?, ?, ?, ?, ?)",
                row,
            )
    conn.commit()
    return conn


@pytest.fixture
def p0_db(monkeypatch):
    """Yield a callable that wires a synthetic P0 DB into dashboard.

    Usage:
        def test_x(p0_db):
            conn = p0_db(decision_rows=[...], decision_rows_with_pipeline=[...])
            c = _client()
            r = c.get("/api/buy-blockers/summary?range=7d")
            ...
    """
    sys.path.insert(0, str(REPO_ROOT))
    import dashboard as _dashboard

    def _make(decision_rows=None, decision_rows_with_pipeline=None, db_path=None):
        if db_path is not None:
            conn = sqlite3.connect(str(db_path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                """
                CREATE TABLE decision_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cycle_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    cycle_start TEXT NOT NULL,
                    session_id INTEGER,
                    decision_schema_version INTEGER NOT NULL,
                    decision_snapshot TEXT NOT NULL,
                    created_at TEXT DEFAULT (datetime('now')),
                    analytics_persistence_version INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(cycle_id, symbol)
                )
                """
            )
            cur.execute(
                "CREATE INDEX idx_decision_history_cycle_start "
                "ON decision_history(cycle_start)"
            )
            for r in (decision_rows or []):
                cur.execute(
                    "INSERT INTO decision_history "
                    "(cycle_id, symbol, cycle_start, decision_schema_version, decision_snapshot) "
                    "VALUES (?, ?, ?, ?, ?)", r,
                )
            for r in (decision_rows_with_pipeline or []):
                cur.execute(
                    "INSERT INTO decision_history "
                    "(cycle_id, symbol, cycle_start, decision_schema_version, decision_snapshot) "
                    "VALUES (?, ?, ?, ?, ?)", r,
                )
            conn.commit()
        else:
            conn = _build_synthetic_p0_db(
                decision_rows=decision_rows,
                decision_rows_with_pipeline=decision_rows_with_pipeline,
            )

        def _opener(*args, **kwargs):
            new_conn = sqlite3.connect(":memory:", check_same_thread=False)
            new_conn.row_factory = sqlite3.Row
            # Mirror the schema so SQL works.
            c = new_conn.cursor()
            c.execute(
                """
                CREATE TABLE decision_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cycle_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    cycle_start TEXT NOT NULL,
                    session_id INTEGER,
                    decision_schema_version INTEGER NOT NULL,
                    decision_snapshot TEXT NOT NULL,
                    created_at TEXT DEFAULT (datetime('now')),
                    analytics_persistence_version INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(cycle_id, symbol)
                )
                """
            )
            c.execute(
                "CREATE INDEX idx_decision_history_cycle_start "
                "ON decision_history(cycle_start)"
            )
            for r in (decision_rows or []):
                c.execute(
                    "INSERT INTO decision_history "
                    "(cycle_id, symbol, cycle_start, decision_schema_version, decision_snapshot) "
                    "VALUES (?, ?, ?, ?, ?)", r,
                )
            for r in (decision_rows_with_pipeline or []):
                c.execute(
                    "INSERT INTO decision_history "
                    "(cycle_id, symbol, cycle_start, decision_schema_version, decision_snapshot) "
                    "VALUES (?, ?, ?, ?, ?)", r,
                )
            new_conn.commit()
            return new_conn

        monkeypatch.setattr(_dashboard, "_p0_open_db", _opener)
        return conn

    yield _make


def _client():
    """Build a FastAPI TestClient without spinning up the live bot."""
    sys.path.insert(0, str(REPO_ROOT))
    import dashboard as _dashboard
    from fastapi.testclient import TestClient
    return TestClient(_dashboard.app)


def _make_pipeline_snapshot(analyzer_signal, final_signal, downgrade_reason=None,
                            liq_result="FAILED", avg_volume=500_000,
                            min_daily_volume=1_000_000, high_low_pct=0.5,
                            effective_max_high_low_pct=0.9,
                            analyzer_signal_strength="DAILY_ONLY",
                            final_signal_strength="WEAK",
                            downgrade_stage="liquidity_filter"):
    """Build a decision_snapshot JSON string with the signal_pipeline block."""
    snap = {
        "schema_version": 1,
        "signal_pipeline": {
            "analyzer_signal": analyzer_signal,
            "analyzer_signal_strength": analyzer_signal_strength,
            "final_signal": final_signal,
            "final_signal_strength": final_signal_strength,
            "downgraded": (analyzer_signal == "BUY" and final_signal != "BUY"),
            "downgrade_stage": (downgrade_stage if (analyzer_signal == "BUY" and final_signal != "BUY") else None),
            "downgrade_reason": downgrade_reason,
            "liquidity_filter_evaluation": {
                "enabled": True,
                "applicable": True,
                "evaluated": True,
                "result": liq_result,
                "avg_volume": avg_volume,
                "min_daily_volume": min_daily_volume,
                "high_low_pct": high_low_pct,
                "effective_max_high_low_pct": effective_max_high_low_pct,
                "reason_raw": downgrade_reason or "Passes liquidity check",
                "signal_before": analyzer_signal,
                "signal_after": final_signal,
            },
        },
    }
    return json.dumps(snap)


# ─────────────────────────────────────────────────────────────────────────
# Test 1: API summary math (low_volume only — the verified prospective shape)
# ─────────────────────────────────────────────────────────────────────────
class TestLowVolumeSummaryMath:
    """The verified P0 sample shape: 19 analyzer BUYs → 0 final BUYs →
    19 downgrades, all attributed to low_average_volume."""

    def test_19_low_volume_rows_reproduce_verified_stats(self, p0_db):
        rows = []
        for i in range(19):
            avg_v = 1000 * (i + 1)  # 1000, 2000, ..., 19000
            snap = _make_pipeline_snapshot(
                analyzer_signal="BUY",
                final_signal="HOLD",
                downgrade_reason=f"Volume {avg_v/1e6:.1f}M < 1M minimum",
                liq_result="FAILED",
                avg_volume=float(avg_v),
                min_daily_volume=1_000_000.0,
            )
            rows.append((
                f"cycle_p0_{i:03d}",
                f"SYM{i:03d}",
                "2026-09-30T15:00:00+00:00",
                1,
                snap,
            ))
        p0_db(decision_rows_with_pipeline=rows)
        c = _client()
        r = c.get("/api/buy-blockers/summary?range=7d&cohort=post_p0_attribution")
        assert r.status_code == 200, r.text
        data = r.json()
        s = data["summary"]
        assert s["analyzer_buy_count"] == 19, f"got {s['analyzer_buy_count']}"
        assert s["final_buy_count"] == 0
        assert s["downgraded_count"] == 19
        assert s["unknown_count"] == 0
        assert s["downgrade_rate_pct"] == 100.0
        # Reason breakdown
        reasons = {r["key"]: r for r in data["reasons"]}
        assert reasons["low_average_volume"]["count"] == 19
        assert reasons["high_high_low_range"]["count"] == 0
        assert reasons["could_not_evaluate_fail_open"]["count"] == 0
        assert reasons["other_liquidity"]["count"] == 0
        assert reasons["non_liquidity"]["count"] == 0
        # The applied threshold is exactly the bot's min_daily_volume
        # (= 1,000,000) — the endpoint MUST persist the actual applied
        # threshold, NEVER the current config.
        assert data["ranges"]["low_average_volume"]["applied_threshold"] == 1_000_000.0
        # Min/median/max for low_average_volume
        assert data["ranges"]["low_average_volume"]["min"] == 1000.0
        assert data["ranges"]["low_average_volume"]["max"] == 19000.0


# ─────────────────────────────────────────────────────────────────────────
# Test 2: API summary math (mixed bucket distribution)
# ─────────────────────────────────────────────────────────────────────────
class TestMixedSummaryMath:
    """Mixed scenario: 10 low-vol + 5 high-low + 1 fail-open + 4 final BUY
    + 2 pre-PR #106 rows with no signal_pipeline."""

    def _build_dataset(self):
        rows = []
        # 10 low-volume downgrades
        for i in range(10):
            avg_v = 100_000 + i * 1000  # 100k..109k
            snap = _make_pipeline_snapshot(
                analyzer_signal="BUY",
                final_signal="HOLD",
                downgrade_reason=f"Volume {avg_v/1e6:.1f}M < 1M minimum",
                liq_result="FAILED",
                avg_volume=float(avg_v),
                min_daily_volume=1_000_000.0,
            )
            rows.append((f"c_lv_{i}", f"LV{i}", "2026-09-30T15:01:00+00:00", 1, snap))

        # 5 high-low range downgrades (avg volume ABOVE min, hl_pct above max)
        for i in range(5):
            snap = _make_pipeline_snapshot(
                analyzer_signal="BUY",
                final_signal="HOLD",
                downgrade_reason=f"Spread {2.0 + i * 0.1:.1f}% too wide",
                liq_result="FAILED",
                avg_volume=5_000_000.0,  # well above 1M
                min_daily_volume=1_000_000.0,
                high_low_pct=2.0 + i * 0.1,
                effective_max_high_low_pct=0.9,
            )
            rows.append((f"c_hl_{i}", f"HL{i}", "2026-09-30T15:02:00+00:00", 1, snap))

        # 1 could-not-evaluate fail-open (NOT a downgrade)
        snap = _make_pipeline_snapshot(
            analyzer_signal="BUY",
            final_signal="BUY",  # fail-open preserves the signal
            downgrade_reason=None,
            liq_result="COULD_NOT_BE_EVALUATED",
            avg_volume=0.0,
            min_daily_volume=1_000_000.0,
        )
        rows.append(("c_cnbe", "CNBE", "2026-09-30T15:03:00+00:00", 1, snap))

        # 4 pass-through BUYs (final signal == BUY, no downgrade)
        for i in range(4):
            snap = _make_pipeline_snapshot(
                analyzer_signal="BUY",
                final_signal="BUY",
                downgrade_reason=None,
                liq_result="PASSED",
                avg_volume=2_000_000.0,
                min_daily_volume=1_000_000.0,
                high_low_pct=0.3,
                effective_max_high_low_pct=0.9,
            )
            rows.append((f"c_ok_{i}", f"OK{i}", "2026-09-30T15:04:00+00:00", 1, snap))

        # 2 pre-PR #106 rows with NO signal_pipeline block
        legacy = json.dumps({"schema_version": 1, "decision": {"outcome": "HOLD_INELIGIBLE"}})
        pre_pipeline_rows = [
            ("c_legacy_0", "LEG0", "2026-09-30T15:05:00+00:00", 1, legacy),
            ("c_legacy_1", "LEG1", "2026-09-30T15:05:01+00:00", 1, legacy),
        ]
        return rows, pre_pipeline_rows

    def test_mixed_buckets_classify_correctly(self, p0_db):
        with_pipeline, without = self._build_dataset()
        p0_db(
            decision_rows=without,
            decision_rows_with_pipeline=with_pipeline,
        )
        c = _client()
        r = c.get("/api/buy-blockers/summary?range=7d&cohort=post_p0_attribution")
        assert r.status_code == 200, r.text
        s = r.json()["summary"]
        # analyzer BUYs: 10 LV + 5 HL + 1 CNBE + 4 pass-through = 20
        assert s["analyzer_buy_count"] == 20
        # final BUYs: 1 CNBE (fail-open → BUY) + 4 pass-through = 5
        assert s["final_buy_count"] == 5
        # downgrades: 10 LV + 5 HL = 15
        assert s["downgraded_count"] == 15
        # unknown: 0
        assert s["unknown_count"] == 0
        # attribution_unavailable: 2 pre-PR #106 rows
        assert s["attribution_unavailable_count"] == 2
        # reasons
        reasons = {r["key"]: r for r in r.json()["reasons"]}
        assert reasons["low_average_volume"]["count"] == 10
        assert reasons["high_high_low_range"]["count"] == 5
        assert reasons["could_not_evaluate_fail_open"]["count"] == 1
        assert reasons["other_liquidity"]["count"] == 0
        assert reasons["non_liquidity"]["count"] == 0


# ─────────────────────────────────────────────────────────────────────────
# Test 3: High-low classification
# ─────────────────────────────────────────────────────────────────────────
class TestHighLowClassification:
    """5 rows with high_low_pct > effective_max_high_low_pct AND
    avg_volume >= 1_000_000. Must land in `high_high_low_range`."""

    def test_high_low_rows_classified_correctly(self, p0_db):
        rows = []
        for i in range(5):
            snap = _make_pipeline_snapshot(
                analyzer_signal="BUY",
                final_signal="HOLD",
                downgrade_reason=f"Spread {1.5 + i * 0.1:.1f}% too wide",
                liq_result="FAILED",
                avg_volume=2_000_000.0,  # well above 1M
                min_daily_volume=1_000_000.0,
                high_low_pct=1.5 + i * 0.1,
                effective_max_high_low_pct=0.9,
            )
            rows.append((f"c_hl_{i}", f"HL{i}", "2026-09-30T15:01:00+00:00", 1, snap))
        p0_db(decision_rows_with_pipeline=rows)
        c = _client()
        r = c.get("/api/buy-blockers/summary?range=7d&cohort=post_p0_attribution")
        assert r.status_code == 200, r.text
        reasons = {r["key"]: r for r in r.json()["reasons"]}
        assert reasons["high_high_low_range"]["count"] == 5
        assert reasons["low_average_volume"]["count"] == 0
        # threshold persisted correctly
        assert r.json()["ranges"]["high_high_low_range"]["applied_threshold"] == 0.9


# ─────────────────────────────────────────────────────────────────────────
# Test 4: Range filtering (24h vs older rows)
# ─────────────────────────────────────────────────────────────────────────
class TestRangeFiltering:
    """Mix rows in last 24h and 8 days ago. 24h query must only
    count the recent rows."""

    def test_24h_filters_out_old_rows(self, p0_db):
        # 5 rows recent (within 24h)
        recent = []
        for i in range(5):
            snap = _make_pipeline_snapshot(
                analyzer_signal="BUY",
                final_signal="HOLD",
                downgrade_reason="Volume 0.5M < 1M minimum",
                liq_result="FAILED",
                avg_volume=500_000.0,
                min_daily_volume=1_000_000.0,
            )
            recent.append((f"c_recent_{i}", f"REC{i}", "2026-09-30T15:00:00+00:00", 1, snap))
        # 7 rows ~2 days ago (outside the 24h window).
        # Use cohort='all' so the cohort filter (post_p0_attribution)
        # does NOT additionally exclude these old rows. The range
        # filter is the only thing under test.
        from datetime import datetime, timedelta, timezone
        two_days_ago = (datetime.now(timezone.utc) - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        old = []
        for i in range(7):
            snap = _make_pipeline_snapshot(
                analyzer_signal="BUY",
                final_signal="HOLD",
                downgrade_reason="Volume 0.5M < 1M minimum",
                liq_result="FAILED",
                avg_volume=500_000.0,
                min_daily_volume=1_000_000.0,
            )
            old.append((f"c_old_{i}", f"OLD{i}", two_days_ago, 1, snap))
        p0_db(decision_rows_with_pipeline=recent + old)
        c = _client()
        # 24h query — recent only (old rows are outside 24h).
        # cohort='all' so the cohort boundary filter does not
        # exclude the old rows from the dataset universe.
        r = c.get("/api/buy-blockers/summary?range=24h&cohort=all")
        assert r.status_code == 200, r.text
        s = r.json()["summary"]
        assert s["analyzer_buy_count"] == 5
        assert s["downgraded_count"] == 5

        # 7d query — both recent and old (~2d ago) are included
        r = c.get("/api/buy-blockers/summary?range=7d&cohort=all")
        assert r.status_code == 200, r.text
        s = r.json()["summary"]
        assert s["analyzer_buy_count"] == 12  # 5 recent + 7 from ~2d ago
        assert s["downgraded_count"] == 12


# ─────────────────────────────────────────────────────────────────────────
# Test 5: Pre-PR #106 attribution_unavailable
# ─────────────────────────────────────────────────────────────────────────
class TestPrePR106AttributionUnavailable:
    """5 rows with NO signal_pipeline block. They MUST be reported as
    `attribution_unavailable_count` — NEVER as `unknown_count`."""

    def test_pre_pr106_counted_as_attribution_unavailable_not_unknown(self, p0_db):
        legacy = json.dumps({"schema_version": 1, "decision": {"outcome": "HOLD_INELIGIBLE"}})
        legacy_rows = [
            (f"c_legacy_{i}", f"LEG{i}", "2026-09-30T15:00:00+00:00", 1, legacy)
            for i in range(5)
        ]
        p0_db(decision_rows=legacy_rows)
        c = _client()
        r = c.get("/api/buy-blockers/summary?range=7d&cohort=all")
        assert r.status_code == 200, r.text
        s = r.json()["summary"]
        assert s["analyzer_buy_count"] == 0
        assert s["final_buy_count"] == 0
        assert s["downgraded_count"] == 0
        assert s["unknown_count"] == 0
        assert s["attribution_unavailable_count"] == 5


# ─────────────────────────────────────────────────────────────────────────
# Test 6: Detail rows endpoint with pagination
# ─────────────────────────────────────────────────────────────────────────
class TestDetailRowsEndpoint:
    """20 analyzer BUY rows; verify limit / offset pagination."""

    def test_pagination(self, p0_db):
        rows = []
        for i in range(20):
            snap = _make_pipeline_snapshot(
                analyzer_signal="BUY",
                final_signal="HOLD",
                downgrade_reason="Volume 0.5M < 1M minimum",
                liq_result="FAILED",
                avg_volume=500_000.0 + i,
                min_daily_volume=1_000_000.0,
                high_low_pct=0.5,
                effective_max_high_low_pct=0.9,
            )
            # Unique cycle_start per row (DECREASING so newest is first)
            cycle_start = f"2026-09-30T15:{20 - i:02d}:00+00:00"
            rows.append((f"c_{i:03d}", f"SYM{i:03d}", cycle_start, 1, snap))
        p0_db(decision_rows_with_pipeline=rows)
        c = _client()
        r = c.get("/api/buy-blockers?range=7d&limit=5&offset=10")
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["rows_returned"] == 5
        assert data["limit"] == 5
        assert data["offset"] == 10
        # Detail row schema
        r0 = data["rows"][0]
        for key in ("cycle_id", "symbol", "cycle_start", "analyzer_signal",
                    "analyzer_signal_strength", "final_signal",
                    "final_signal_strength", "downgraded", "downgrade_stage",
                    "downgrade_reason", "liquidity_evaluation",
                    "mtf", "strategy_eligible", "ranked_candidate",
                    "execution_attempted", "order_submitted"):
            assert key in r0, f"missing key {key}"
        # Sort: newest first (DESC by cycle_start)
        cycle_starts = [r["cycle_start"] for r in data["rows"]]
        assert cycle_starts == sorted(cycle_starts, reverse=True), \
            f"rows not sorted DESC by cycle_start: {cycle_starts}"
        # First page (offset 0)
        r = c.get("/api/buy-blockers?range=7d&limit=5&offset=0")
        assert r.json()["rows_returned"] == 5
        assert r.json()["offset"] == 0


# ─────────────────────────────────────────────────────────────────────────
# Test 7: Live verification against trading_bot.db
# ─────────────────────────────────────────────────────────────────────────
class TestLiveVerification:
    """Hit the actual /api/buy-blockers/summary?range=7d endpoint after
    deploy. Assert the verified prospective sample SHAPE:

      analyzer_buy_count >= 19,
      final_buy_count == 0,
      downgraded_count == analyzer_buy_count,
      low_average_volume count >= 19.

    This test catches any divergence between the code and the
    authoritative forensic sample. It is skipped if the live DB does
    not exist (so test machines without /trading_bot.db skip cleanly).
    """

    def test_live_summary_reproduces_verified_shape(self):
        live_db = REPO_ROOT / "trading_bot.db"
        if not live_db.exists():
            pytest.skip(f"live DB not found at {live_db}")
        c = _client()
        r = c.get("/api/buy-blockers/summary?range=7d&cohort=post_p0_attribution")
        assert r.status_code == 200, r.text
        s = r.json()["summary"]
        # Hard assertions for the verified P0 truth:
        assert s["analyzer_buy_count"] >= 19, (
            f"analyzer_buy_count regressed: got {s['analyzer_buy_count']}, "
            f"expected >= 19 (verified prospective sample)"
        )
        assert s["final_buy_count"] == 0, (
            f"final_buy_count != 0 (got {s['final_buy_count']}); "
            f"P0 root cause was verified at 0 final BUYs"
        )
        assert s["downgraded_count"] == s["analyzer_buy_count"], (
            f"downgraded_count ({s['downgraded_count']}) != "
            f"analyzer_buy_count ({s['analyzer_buy_count']}) — every "
            f"analyzer BUY in the verified sample was downgraded"
        )
        assert s["unknown_count"] == 0, (
            f"unknown_count != 0 (got {s['unknown_count']}); "
            f"P0 root cause requires UNKNOWN BUY→HOLD = 0"
        )
        reasons = {r["key"]: r for r in r.json()["reasons"]}
        assert reasons["low_average_volume"]["count"] >= 19, (
            f"low_average_volume regressed: got "
            f"{reasons['low_average_volume']['count']}, expected >= 19"
        )
        # The applied threshold MUST be the decision-time value
        # (1_000_000 with defaults), not a recomputed value.
        assert r.json()["ranges"]["low_average_volume"]["applied_threshold"] == 1_000_000.0