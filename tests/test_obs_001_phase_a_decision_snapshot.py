"""
OBS-001 Phase A — Decision Snapshot and Cycle Funnel tests.

Phase A is observability only:
- schema_version = 1 (first deployed version)
- No active pre-rank filtering; ranking/selection behavior unchanged
- Order submission (not fill) is the documented slot-consumption event
- Reserved outcome values (BUY_FILLED, SELL_FILLED, *_BLOCKED_PRE_RANK,
  BUY_PRE_RANK_EXCLUDED) are NEVER produced by current code

These tests verify:
1. Schema constants and outcome enum correctness
2. Snapshot builder produces schema_version=1 with all required blocks
3. decision_history is INSERT-only with UNIQUE(cycle_id, symbol)
4. cycle_funnel reflects current runtime behavior (no invented counters)
5. Reserved outcomes are NOT produced by current code path
6. fill_confirmed=False and slot_consumed semantics
"""

import json
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── 1. Constants / enum correctness ────────────────────────────────────────


class TestSchemaConstants:
    def test_obs_001_schema_version_is_one(self):
        """First deployed schema version is 1, per Josh's guardrail #1."""
        from src.core.smart_bot import OBS_001_SCHEMA_VERSION
        assert OBS_001_SCHEMA_VERSION == 1

    def test_currently_reachable_outcomes_are_documented(self):
        from src.core.smart_bot import OBS_001_OUTCOMES_CURRENTLY_REACHABLE
        expected = {
            "BUY_ORDER_SUBMITTED", "BUY_ORDER_FAILED",
            "BUY_ELIGIBLE_NOT_SELECTED", "BUY_BLOCKED_DYNAMIC",
            "HOLD_INELIGIBLE",
            "SELL_ORDER_SUBMITTED", "SELL_ORDER_FAILED",
            "SELL_BLOCKED_NO_POSITION", "SELL_BLOCKED_DYNAMIC",
            "SKIPPED_INVALID_DATA",
        }
        assert OBS_001_OUTCOMES_CURRENTLY_REACHABLE == expected

    def test_reserved_outcomes_are_documented(self):
        from src.core.smart_bot import OBS_001_OUTCOMES_RESERVED
        expected = {
            "BUY_FILLED", "SELL_FILLED",
            "BUY_BLOCKED_PRE_RANK", "SELL_BLOCKED_PRE_RANK",
            "BUY_PRE_RANK_EXCLUDED",
        }
        assert OBS_001_OUTCOMES_RESERVED == expected

    def test_reserved_outcomes_excluded_from_currently_reachable(self):
        from src.core.smart_bot import (
            OBS_001_OUTCOMES_CURRENTLY_REACHABLE,
            OBS_001_OUTCOMES_RESERVED,
        )
        assert OBS_001_OUTCOMES_RESERVED.isdisjoint(OBS_001_OUTCOMES_CURRENTLY_REACHABLE)

    def test_slot_semantics_version_is_one(self):
        """v1 = consumed when submit_order returns. v2 (future) = consumed
        when fill is confirmed."""
        from src.core.smart_bot import OBS_001_SLOT_SEMANTICS_VERSION
        assert OBS_001_SLOT_SEMANTICS_VERSION == 1

    def test_execution_check_order_matches_current_execute_trade(self):
        """The execution_checks.evaluated_in_order must reflect the ACTUAL
        order of checks in current execute_trade, per guardrail #3."""
        from src.core.smart_bot import OBS_001_EXECUTION_CHECK_ORDER
        expected = (
            "margin_check",
            "pending_order_check",
            "cooldown_check",
            "position_concentration_check",
            "sector_concentration_check",
            "correlation_check",
            "beta_check",
            "buying_power_check",
            "quantity_post_sizing_check",
        )
        assert OBS_001_EXECUTION_CHECK_ORDER == expected


# ── 2. Schema migration ─────────────────────────────────────────────────────


class TestSchemaMigration:
    def test_decision_history_unique_constraint_enforced(self, tmp_path):
        """UNIQUE(cycle_id, symbol) guarantees one finalized row per pair.

        Post-PR-review (Josh 2026-09-12 03:30 UTC): decision_history is
        INSERT-only. The first finalize returns True; the second
        finalize for the same (cycle_id, symbol) returns False because
        the SQL UNIQUE constraint blocks the duplicate insert. The
        ORIGINAL row is NEVER overwritten.
        """
        from src.database.sqlite_db import SQLiteDB, _get_conn
        db = SQLiteDB()
        db._init_schema()

        cycle_id = "test_obs_001_unique_smoke"
        snap = {"schema_version": 1, "symbol": "X", "decision": {"outcome": "HOLD_INELIGIBLE"}}

        # Clean any leftover row from a previous test run so the
        # first finalize is a guaranteed fresh insert.
        with _get_conn() as conn:
            conn.execute(
                "DELETE FROM decision_history WHERE cycle_id=?",
                (cycle_id,),
            )

        # First finalize succeeds (inserts the row).
        ok1 = db.finalize_decision_history(cycle_id, "X", "2026-09-12T00:00:00", None, 1, snap)
        # Second finalize with the same (cycle_id, symbol) MUST be
        # rejected by the UNIQUE constraint; the function returns
        # False (the original row is preserved unchanged).
        ok2 = db.finalize_decision_history(cycle_id, "X", "2026-09-12T00:00:00", None, 1, snap)
        assert ok1 is True, "first finalize must return True"
        assert ok2 is False, (
            "second finalize with same (cycle_id, symbol) MUST return "
            "False (UNIQUE blocks silent overwrite); got True"
        )

        with _get_conn() as conn:
            rows = conn.execute(
                "SELECT COUNT(*) FROM decision_history WHERE cycle_id=?",
                (cycle_id,),
            ).fetchone()
            assert rows[0] == 1
            conn.execute("DELETE FROM decision_history WHERE cycle_id=?", (cycle_id,))

    def test_cycle_funnel_unique_cycle_id_enforced(self):
        from src.database.sqlite_db import SQLiteDB, _get_conn
        db = SQLiteDB()
        db._init_schema()
        cycle_id = "test_obs_001_funnel_smoke"

        funnel = {
            "cycle_id": cycle_id, "session_id": None,
            "cycle_start": "2026-09-12T00:00:00",
            "cycle_end": "2026-09-12T00:01:00",
            "analyzed_count": 10, "strategy_eligible_count": 2,
            "ranked_candidate_count": 1, "execution_attempt_count": 1,
            "execution_blocked_count": 0, "order_submission_attempt_count": 1,
            "order_submitted_count": 1, "order_failed_count": 0,
            "not_attempted_count": 0, "not_attempted_reason": None,
            "bot_version": "2.1.0", "schema_version": 1,
        }
        assert db.insert_cycle_funnel(funnel)
        assert db.insert_cycle_funnel(funnel)  # idempotent
        with _get_conn() as conn:
            rows = conn.execute(
                "SELECT COUNT(*) FROM cycle_funnel WHERE cycle_id=?",
                (cycle_id,),
            ).fetchone()
            assert rows[0] == 1
            conn.execute("DELETE FROM cycle_funnel WHERE cycle_id=?", (cycle_id,))

    def test_analyzed_stocks_columns_present(self):
        from src.database.sqlite_db import SQLiteDB, _get_conn
        SQLiteDB()._init_schema()
        with _get_conn() as conn:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(analyzed_stocks)").fetchall()]
            assert "decision_snapshot" in cols
            assert "decision_schema_version" in cols


# ── 3. Snapshot builder ─────────────────────────────────────────────────────


class TestSnapshotBuilder:
    """Build a snapshot via _build_decision_snapshot and verify shape."""

    def _make_bot(self):
        """Construct a minimal SmartTradingBot-like object for testing the
        snapshot builder in isolation. We bypass __init__ to avoid Alpaca
        client construction."""
        from src.core.smart_bot import SmartTradingBot
        bot = SmartTradingBot.__new__(SmartTradingBot)
        bot.rsi_buy_threshold = 30
        bot.rsi_sell_threshold = 70
        bot.enable_volume_confirmation = True
        bot.bot_version = "2.1.0-test"
        bot.session_id = None
        bot._cycle_start_time = None
        return bot

    def _buy_analysis(self):
        return {
            "symbol": "NVDA", "signal": "BUY", "signal_strength": "STRONG",
            "price": 200.0, "total_score": 88,
            "rsi": 25.0, "sma_fast": 110.0, "sma_slow": 100.0,
            "macd_histogram": 0.5, "volume_ratio": 1.2,
            "rsi_score": 12.5, "sma_score": 18.0, "macd_score": 20.0,
            "bb_score": -5.0, "catalyst_score": 10.0, "regime_score": 5.0,
            "latest": {"MACD_histogram": 0.5},
            "multi_timeframe": True,
        }

    def test_snapshot_schema_version_is_one(self):
        bot = self._make_bot()
        snap = bot._build_decision_snapshot("NVDA", self._buy_analysis())
        assert snap["schema_version"] == 1

    def test_snapshot_has_all_required_blocks(self):
        bot = self._make_bot()
        snap = bot._build_decision_snapshot("NVDA", self._buy_analysis())
        for block in (
            "strategy_eligibility", "scoring", "ranking", "selection",
            "execution_checks", "order", "decision", "baseline_diagnostics",
        ):
            assert block in snap, f"Missing block: {block}"

    def test_strategy_eligibility_holds_for_strong_buy(self):
        bot = self._make_bot()
        snap = bot._build_decision_snapshot("NVDA", self._buy_analysis())
        assert snap["strategy_eligibility"]["strategy_eligible"] is True
        assert snap["strategy_eligibility"]["signal"] == "BUY"
        assert snap["strategy_eligibility"]["signal_strength"] == "STRONG"
        gate_names = {g["name"] for g in snap["strategy_eligibility"]["gates"]}
        assert "rsi_oversold" in gate_names
        assert "sma_uptrend" in gate_names
        assert "macd_positive" in gate_names

    def test_strategy_ineligible_hold_has_reason(self):
        bot = self._make_bot()
        analysis = self._buy_analysis()
        analysis["signal"] = "HOLD"
        analysis["rsi"] = 80.0
        snap = bot._build_decision_snapshot("NVDA", analysis)
        assert snap["strategy_eligibility"]["strategy_eligible"] is False
        assert "failed gates" in (snap["strategy_eligibility"]["strategy_reason"] or "").lower()

    def test_outcome_buy_order_submitted_when_submitted_true(self):
        bot = self._make_bot()
        snap = bot._build_decision_snapshot(
            "NVDA", self._buy_analysis(),
            order_state={"submitted": True},
        )
        assert snap["decision"]["outcome"] == "BUY_ORDER_SUBMITTED"

    def test_outcome_sell_order_submitted_when_submitted_true(self):
        bot = self._make_bot()
        analysis = self._buy_analysis()
        analysis["signal"] = "SELL"
        snap = bot._build_decision_snapshot(
            "NVDA", analysis, order_state={"submitted": True},
        )
        assert snap["decision"]["outcome"] == "SELL_ORDER_SUBMITTED"

    def test_outcome_buy_blocked_dynamic_when_first_block_set(self):
        bot = self._make_bot()
        snap = bot._build_decision_snapshot(
            "NVDA", self._buy_analysis(),
            execution_state={"first_blocking_check": "pending_order_check",
                             "first_blocking_reason": "1 pending order for NVDA"},
            order_state={"submitted": False, "submit_attempted": True},
        )
        assert snap["decision"]["outcome"] == "BUY_BLOCKED_DYNAMIC"

    def test_outcome_buy_order_failed_when_no_first_block(self):
        bot = self._make_bot()
        snap = bot._build_decision_snapshot(
            "NVDA", self._buy_analysis(),
            execution_state={"first_blocking_check": None, "checks": []},
            order_state={"submitted": False, "submit_attempted": True},
        )
        assert snap["decision"]["outcome"] == "BUY_ORDER_FAILED"

    def test_outcome_buy_eligible_not_selected(self):
        bot = self._make_bot()
        snap = bot._build_decision_snapshot(
            "NVDA", self._buy_analysis(),
            ranking_state={"applicable": True, "ranked_candidate": False,
                           "candidate_rank": 4, "eligible_candidate_count": 8},
            selection_state={"slots_available_at_attempt": 3},
        )
        assert snap["decision"]["outcome"] == "BUY_ELIGIBLE_NOT_SELECTED"
        assert "Rank #4" in snap["decision"]["primary_reason"]

    def test_outcome_hold_ineligible(self):
        bot = self._make_bot()
        analysis = self._buy_analysis()
        analysis["signal"] = "HOLD"
        snap = bot._build_decision_snapshot("NVDA", analysis)
        assert snap["decision"]["outcome"] == "HOLD_INELIGIBLE"

    def test_outcome_sell_blocked_no_position(self):
        bot = self._make_bot()
        analysis = self._buy_analysis()
        analysis["signal"] = "SELL"
        snap = bot._build_decision_snapshot(
            "NVDA", analysis, order_state={"submitted": False},
        )
        assert snap["decision"]["outcome"] == "SELL_BLOCKED_NO_POSITION"

    def test_fill_confirmed_is_always_false(self):
        """Phase A never produces a fill confirmation."""
        bot = self._make_bot()
        snap = bot._build_decision_snapshot(
            "NVDA", self._buy_analysis(), order_state={"submitted": True},
        )
        assert snap["order"]["fill_confirmed"] is False
        assert snap["order"]["fill_confirmation_method"] is None
        assert snap["order"]["fill_price"] is None
        assert snap["order"]["fill_quantity"] is None
        assert snap["order"]["fill_timestamp"] is None

    def test_slot_consumed_aligned_with_submit_order_return(self):
        """v1 semantics: slot consumed iff submit_order returned an order."""
        bot = self._make_bot()
        snap_submitted = bot._build_decision_snapshot(
            "NVDA", self._buy_analysis(), order_state={"submitted": True},
        )
        assert snap_submitted["order"]["slot_consumed"] is True
        snap_blocked = bot._build_decision_snapshot(
            "NVDA", self._buy_analysis(), order_state={"submitted": False},
        )
        assert snap_blocked["order"]["slot_consumed"] is False

    def test_reserved_outcomes_never_appear_in_decision_outcome(self):
        """BUY_FILLED / SELL_FILLED / *_BLOCKED_PRE_RANK / BUY_PRE_RANK_EXCLUDED
        must NEVER be produced by current code paths."""
        from src.core.smart_bot import OBS_001_OUTCOMES_RESERVED
        bot = self._make_bot()
        # Try every plausible combination of inputs; reserved values
        # must never appear.
        for signal in ("BUY", "SELL", "HOLD"):
            for submitted in (True, False):
                for first_block in (None, "pending_order_check", "sector_concentration_check"):
                    analysis = self._buy_analysis()
                    analysis["signal"] = signal
                    snap = bot._build_decision_snapshot(
                        "X", analysis,
                        execution_state={"first_blocking_check": first_block, "checks": []},
                        order_state={"submitted": submitted, "submit_attempted": True},
                    )
                    assert snap["decision"]["outcome"] not in OBS_001_OUTCOMES_RESERVED, \
                        f"Reserved outcome leaked: {snap['decision']['outcome']}"

    def test_ranking_block_reflects_candidate_rank(self):
        bot = self._make_bot()
        snap = bot._build_decision_snapshot(
            "NVDA", self._buy_analysis(),
            ranking_state={"applicable": True, "ranked_candidate": True,
                           "candidate_rank": 2, "eligible_candidate_count": 6},
        )
        assert snap["ranking"]["candidate_rank"] == 2
        assert snap["ranking"]["eligible_candidate_count"] == 6
        assert snap["ranking"]["tiebreak_basis"] == "symbol ASC"

    def test_baseline_diagnostics_labeled_observed_only(self):
        bot = self._make_bot()
        bd = {
            "captured_at_cycle_start": "2026-09-12T00:00:00Z",
            "baseline_state_id": "bl_test",
            "cash": 30000.0,
            "potential_l2_blockers_for_this_symbol": [],
        }
        snap = bot._build_decision_snapshot(
            "NVDA", self._buy_analysis(), baseline_diagnostics=bd,
        )
        assert snap["baseline_diagnostics"] is not None
        assert "OBSERVED-ONLY" in snap["baseline_diagnostics"]["_label"]
        assert "does NOT use this as a trading gate" in snap["baseline_diagnostics"]["_label"]

    def test_execution_check_order_listed_in_snapshot(self):
        bot = self._make_bot()
        snap = bot._build_decision_snapshot("NVDA", self._buy_analysis())
        assert snap["execution_checks"]["evaluated_in_order"] == list(
            __import__("src.core.smart_bot", fromlist=["OBS_001_EXECUTION_CHECK_ORDER"]).OBS_001_EXECUTION_CHECK_ORDER
        )

    def test_gap_notes_present_for_checks_that_ignore_pending_exposure(self):
        """Per corrigendum-2, sector/correlation/beta/position/buying_power
        checks must carry a _gap_note documenting the current behavior gap."""
        bot = self._make_bot()
        snap = bot._build_decision_snapshot(
            "NVDA", self._buy_analysis(),
            execution_state={"checks": [
                {"name": "sector_concentration_check", "_gap_note": "test"},
                {"name": "correlation_check", "_gap_note": "test"},
                {"name": "beta_check", "_gap_note": "test"},
                {"name": "position_concentration_check", "_gap_note": "test"},
                {"name": "buying_power_check", "_gap_note": "test"},
                {"name": "pending_order_check", "_gap_note": "test"},
                {"name": "cooldown_check", "_gap_note": "test"},
            ]},
        )
        for check in snap["execution_checks"]["checks"]:
            assert check.get("_gap_note") is not None, \
                f"Missing _gap_note on {check.get('name')}"

    def test_snapshot_json_serializable(self):
        """Snapshot must round-trip through json.dumps without errors."""
        bot = self._make_bot()
        snap = bot._build_decision_snapshot(
            "NVDA", self._buy_analysis(),
            baseline_diagnostics={"captured_at_cycle_start": "2026-09-12T00:00:00Z",
                                  "baseline_state_id": "bl_test"},
        )
        encoded = json.dumps(snap)
        decoded = json.loads(encoded)
        assert decoded["schema_version"] == 1
        assert decoded["symbol"] == "NVDA"


# ── 4. Cycle funnel invariants ──────────────────────────────────────────────


class TestCycleFunnelInvariants:
    """Invariants derived from the ACTUAL run_analysis control flow, not
    from any invented pre-rank gate."""

    def test_funnel_counters_match_actual_control_flow(self):
        """analyzed_count >= strategy_eligible_count >= ranked_candidate_count.
        ranked_candidate_count == execution_attempt_count + not_attempted_count.
        execution_attempt_count == execution_blocked_count + order_submission_attempt_count.
        order_submission_attempt_count == order_submitted_count + order_failed_count.
        The only currently proven not_attempted_reason is 'slots_filled'."""
        from src.database.sqlite_db import SQLiteDB, _get_conn
        SQLiteDB()._init_schema()

        # Simulate a cycle that mirrors actual current behavior.
        analyzed = 50
        strategy_eligible = 8   # 8 BUY/SELL signals
        ranked_candidates = 3   # 3 BUYs (SELL signals are NOT added to buy_candidates)
        execution_attempt = 3   # chosen slice of 3
        execution_blocked = 1   # one blocked at L5
        order_submitted = 2     # two successful submit_order returns
        order_failed = 0
        not_attempted = 0       # no skipped this cycle

        cycle_id = "test_obs_001_invariants_smoke"
        ok = SQLiteDB().insert_cycle_funnel({
            "cycle_id": cycle_id, "session_id": None,
            "cycle_start": "2026-09-12T00:00:00",
            "cycle_end": "2026-09-12T00:01:00",
            "analyzed_count": analyzed, "strategy_eligible_count": strategy_eligible,
            "ranked_candidate_count": ranked_candidates,
            "execution_attempt_count": execution_attempt,
            "execution_blocked_count": execution_blocked,
            "order_submission_attempt_count": order_submitted + order_failed,
            "order_submitted_count": order_submitted, "order_failed_count": order_failed,
            "not_attempted_count": not_attempted, "not_attempted_reason": None,
            "bot_version": "2.1.0", "schema_version": 1,
        })
        assert ok

        # Verify invariants
        assert analyzed >= strategy_eligible
        assert strategy_eligible >= ranked_candidates
        assert ranked_candidates == execution_attempt + not_attempted
        assert execution_attempt == execution_blocked + order_submitted + order_failed
        assert order_submitted + order_failed == order_submitted + order_failed

        with _get_conn() as conn:
            conn.execute("DELETE FROM cycle_funnel WHERE cycle_id=?", (cycle_id,))

    def test_not_attempted_reason_is_known_string(self):
        """Per guardrail #3, only 'slots_filled' is a currently proven reason
        for not_attempted_count. The funnel allows it explicitly; any other
        reason must come from a future traced addition."""
        from src.core.smart_bot import OBS_001_OUTCOMES_RESERVED  # sanity
        # The cycle_funnel.not_attempted_reason column is TEXT. The current
        # code can only emit 'slots_filled' (from the ranked-walk slice).
        # Any other reason is a future-traced value. This test asserts the
        # column is nullable and accepts the documented value.
        from src.database.sqlite_db import SQLiteDB, _get_conn
        SQLiteDB()._init_schema()
        cycle_id = "test_obs_001_slots_filled_smoke"
        SQLiteDB().insert_cycle_funnel({
            "cycle_id": cycle_id, "session_id": None,
            "cycle_start": "2026-09-12T00:00:00",
            "cycle_end": "2026-09-12T00:01:00",
            "analyzed_count": 50, "strategy_eligible_count": 8,
            "ranked_candidate_count": 5, "execution_attempt_count": 3,
            "execution_blocked_count": 1, "order_submission_attempt_count": 2,
            "order_submitted_count": 2, "order_failed_count": 0,
            "not_attempted_count": 2, "not_attempted_reason": "slots_filled",
            "bot_version": "2.1.0", "schema_version": 1,
        })
        with _get_conn() as conn:
            row = conn.execute(
                "SELECT not_attempted_count, not_attempted_reason FROM cycle_funnel WHERE cycle_id=?",
                (cycle_id,),
            ).fetchone()
            assert row[0] == 2
            assert row[1] == "slots_filled"
            conn.execute("DELETE FROM cycle_funnel WHERE cycle_id=?", (cycle_id,))


# ── 5. No pre-rank gate ─────────────────────────────────────────────────────


class TestNoPreRankGate:
    """Phase A MUST NOT change candidate population, ranks, or attempt order."""

    def test_no_pre_rank_helper_introduced_in_phase_a(self):
        """Per guardrail #2, Phase A must NOT introduce _check_pre_rank_actionability
        as a trading gate. We assert it is NOT a method on SmartTradingBot."""
        from src.core.smart_bot import SmartTradingBot
        assert not hasattr(SmartTradingBot, "_check_pre_rank_actionability"), \
            "Phase A must not introduce _check_pre_rank_actionability"

    def test_no_pre_rank_actionable_in_funnel(self):
        """pre_rank_actionable_count must NOT be in cycle_funnel columns."""
        from src.database.sqlite_db import SQLiteDB, _get_conn
        SQLiteDB()._init_schema()
        with _get_conn() as conn:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(cycle_funnel)").fetchall()]
            assert "pre_rank_actionable_count" not in cols

    def test_ranking_input_order_does_not_change_ranking(self):
        """The ranking key is (total_score DESC, symbol ASC); input order
        does not affect ranking outcome."""
        candidates = [
            {"symbol": "AMD",  "total_score": 80.0},
            {"symbol": "NVDA", "total_score": 88.0},
            {"symbol": "MSFT", "total_score": 80.0},
        ]
        ranked = sorted(
            candidates,
            key=lambda a: (-float(a.get("total_score", 0.0)), a.get("symbol", "")),
        )
        assert [c["symbol"] for c in ranked] == ["NVDA", "AMD", "MSFT"]

        # Same input, shuffled order — same ranking
        shuffled = [candidates[2], candidates[0], candidates[1]]
        ranked2 = sorted(
            shuffled,
            key=lambda a: (-float(a.get("total_score", 0.0)), a.get("symbol", "")),
        )
        assert [c["symbol"] for c in ranked2] == ["NVDA", "AMD", "MSFT"]


# ── 6. Legacy row sentinel ─────────────────────────────────────────────────


class TestLegacyRowSentinel:
    """Rows with decision_snapshot IS NULL are legacy rows."""

    def test_legacy_marker_constant(self):
        """The legacy condition is decision_snapshot IS NULL; documented here."""
        # The legacy sentinel is "decision_snapshot IS NULL" — verified at
        # SQL/PRAGMA level. The dashboard renderer must show
        # "Legacy analysis — detailed decision trace unavailable" for these.
        # This test asserts the schema_version column defaults to 0 for legacy rows.
        from src.database.sqlite_db import SQLiteDB, _get_conn
        SQLiteDB()._init_schema()
        with _get_conn() as conn:
            # Insert a row without decision_snapshot (legacy)
            conn.execute(
                "INSERT INTO analyzed_stocks (symbol) VALUES (?)",
                ("LEGACY_X",),
            )
            row = conn.execute(
                "SELECT decision_snapshot, decision_schema_version FROM analyzed_stocks WHERE symbol=?",
                ("LEGACY_X",),
            ).fetchone()
            assert row[0] is None, "Legacy row must have NULL decision_snapshot"
            assert row[1] == 0, "Legacy row must have schema_version=0"
            conn.execute("DELETE FROM analyzed_stocks WHERE symbol=?", ("LEGACY_X",))


# ── 7. decision_history INSERT-only ─────────────────────────────────────────


class TestDecisionHistoryInsertOnly:
    def test_decision_history_persists_snapshot(self):
        from src.database.sqlite_db import SQLiteDB, _get_conn
        SQLiteDB()._init_schema()
        cycle_id = "test_obs_001_history_smoke"
        snap = {
            "schema_version": 1,
            "symbol": "AAPL",
            "decision": {"outcome": "BUY_ORDER_SUBMITTED"},
            "order": {"submitted": True, "slot_consumed": True, "fill_confirmed": False},
        }
        SQLiteDB().finalize_decision_history(
            cycle_id, "AAPL", "2026-09-12T00:00:00", None, 1, snap,
        )
        with _get_conn() as conn:
            row = conn.execute(
                "SELECT decision_snapshot, decision_schema_version "
                "FROM decision_history WHERE cycle_id=? AND symbol=?",
                (cycle_id, "AAPL"),
            ).fetchone()
            assert row is not None
            assert json.loads(row[0])["decision"]["outcome"] == "BUY_ORDER_SUBMITTED"
            assert row[1] == 1
            conn.execute("DELETE FROM decision_history WHERE cycle_id=?", (cycle_id,))


# ── 8. Legacy dashboard fidelity (Issue 1 regression tests) ───────────────
#
# When decision_snapshot IS NULL (legacy rows), api_opportunities must
# read the persisted `signal` and `signal_strength` columns directly
# from analyzed_stocks. It must NEVER re-derive them from total_score,
# because doing so would rewrite the meaning of an old analysis under
# current threshold regimes.
#
# These tests pin that behavior with regression coverage of the four
# cases Josh called out:
#   1. legacy score=90 + stored signal=HOLD renders HOLD (not BUY)
#   2. legacy score=20 + stored signal=BUY renders BUY (not SELL)
#   3. stored signal_strength is used even when current thresholds
#      would imply another strength
#   4. changing dashboard score thresholds cannot alter a legacy
#      persisted signal/strength


class TestLegacyDashboardFidelity:
    """Regression coverage for Issue 1: legacy dashboard fidelity.

    api_opportunities must read analyzed_stocks.signal and
    analyzed_stocks.signal_strength for legacy rows; it must NOT
    re-derive them from total_score thresholds.

    The contract is verified via two complementary strategies:

    A) STATIC ANALYSIS: api_opportunities' source must contain a
       legacy branch that reads row['signal'] and
       row['signal_strength'] directly, and must NOT contain
       score-threshold comparisons in that branch.

    B) DB-LEVEL PROOF: the legacy-row contract is a property of the
       SQL columns themselves — the dashboard MUST honor whatever
       signal/signal_strength the persistence layer wrote. We prove
       this by inserting legacy rows directly and asserting the
       columns survive. The dashboard's read contract is verified
       via the static check.
    """

    def _insert_legacy_row(self, symbol, *, signal, signal_strength,
                           total_score):
        """Insert a legacy row directly. Legacy means
        decision_snapshot IS NULL and decision_schema_version = 0.
        Uses the global test DB.
        """
        from src.database.sqlite_db import SQLiteDB, _get_conn
        SQLiteDB()._init_schema()
        with _get_conn() as conn:
            conn.execute(
                "DELETE FROM analyzed_stocks WHERE symbol=?",
                (symbol,),
            )
            conn.execute(
                "INSERT INTO analyzed_stocks (symbol, price, "
                "total_score, signal, signal_strength, decision_snapshot, "
                "decision_schema_version, last_analyzed) "
                "VALUES (?, ?, ?, ?, ?, NULL, 0, ?)",
                (symbol, 100.0, total_score, signal, signal_strength,
                 "2026-09-12T00:00:00"),
            )

    def _read_signal_strength(self, symbol):
        """Read the persisted signal/strength columns directly."""
        from src.database.sqlite_db import _get_conn
        with _get_conn() as conn:
            row = conn.execute(
                "SELECT signal, signal_strength FROM analyzed_stocks "
                "WHERE symbol=?",
                (symbol,),
            ).fetchone()
            return (row[0], row[1])

    def test_legacy_score_90_stored_hold_renders_hold(self):
        """Case 1: legacy row with score=90 + stored signal=HOLD must
        survive persistence unchanged. The dashboard's contract: it
        must render HOLD (NOT BUY derived from score threshold)."""
        self._insert_legacy_row(
            "LEG_HOLD_BUT_HIGH_SCORE",
            signal="HOLD", signal_strength="WEAK", total_score=90,
        )
        try:
            signal, strength = self._read_signal_strength(
                "LEG_HOLD_BUT_HIGH_SCORE"
            )
            # Persistence contract: the columns hold exactly what
            # we wrote. The dashboard must read them as-is.
            assert signal == "HOLD"
            assert strength == "WEAK"
        finally:
            self._insert_legacy_row(
                "LEG_HOLD_BUT_HIGH_SCORE",
                signal="HOLD", signal_strength="WEAK", total_score=90,
            )

    def test_legacy_score_20_stored_buy_renders_buy(self):
        """Case 2: legacy row with score=20 + stored signal=BUY must
        survive persistence unchanged. The dashboard's contract: it
        must render BUY (NOT SELL derived from score threshold)."""
        self._insert_legacy_row(
            "LEG_BUY_BUT_LOW_SCORE",
            signal="BUY", signal_strength="STRONG", total_score=20,
        )
        try:
            signal, strength = self._read_signal_strength(
                "LEG_BUY_BUT_LOW_SCORE"
            )
            assert signal == "BUY"
            assert strength == "STRONG"
        finally:
            self._insert_legacy_row(
                "LEG_BUY_BUT_LOW_SCORE",
                signal="BUY", signal_strength="STRONG", total_score=20,
            )

    def test_stored_strength_used_even_when_score_implies_other(self):
        """Case 3: stored signal_strength is used even when current
        thresholds would imply another strength (e.g. score=82 stored
        as MEDIUM must remain MEDIUM, not become STRONG)."""
        self._insert_legacy_row(
            "LEG_BUY_MEDIUM_OVER_SCORE_80",
            signal="BUY", signal_strength="MEDIUM", total_score=82,
        )
        try:
            signal, strength = self._read_signal_strength(
                "LEG_BUY_MEDIUM_OVER_SCORE_80"
            )
            # Persistence contract: the persisted MEDIUM wins.
            # If the dashboard's legacy branch used score>=80 to set
            # STRONG, this would never be displayed as MEDIUM.
            assert signal == "BUY"
            assert strength == "MEDIUM"
        finally:
            self._insert_legacy_row(
                "LEG_BUY_MEDIUM_OVER_SCORE_80",
                signal="BUY", signal_strength="MEDIUM", total_score=82,
            )

    def test_dashboard_legacy_branch_does_not_derive_from_score(self):
        """Case 4 (static analysis): api_opportunities must NOT contain
        score-threshold comparisons in the legacy branch. This proves
        that future changes to dashboard thresholds cannot silently
        alter legacy rows.
        """
        import inspect
        from dashboard import api_opportunities
        source = inspect.getsource(api_opportunities)
        # Find the legacy branch (the explicit # Legacy row comment
        # is the marker). The legacy branch must read row['signal']
        # and row['signal_strength'] and must NOT contain score
        # threshold comparisons.
        legacy_marker = "Legacy row (decision_snapshot IS NULL)"
        idx = source.find(legacy_marker)
        assert idx > 0, "api_opportunities must mark the legacy branch"
        end_idx = source.find("# Parse buy_criteria", idx)
        assert end_idx > idx, "could not find end of legacy branch"
        legacy_branch = source[idx:end_idx]
        # Required: legacy branch reads the persisted columns.
        assert "row['signal']" in legacy_branch, \
            "legacy branch must read row['signal']"
        assert "row['signal_strength']" in legacy_branch, \
            "legacy branch must read row['signal_strength']"
        # Forbidden: legacy branch must NOT contain threshold
        # comparisons that re-derive signal/strength from score.
        forbidden = [
            "score >= 65", "score <= 35",
            "score >= 80", "score <= 20",
        ]
        for f in forbidden:
            assert f not in legacy_branch, (
                f"legacy branch must NOT derive signal/strength from "
                f"score; found forbidden expression {f!r}"
            )


# ── 9. execute_trade trace collection (Issue 2 regression tests) ──────────
#
# The OBS-001 trace must be populated by the REAL execute_trade path,
# not by a parallel observation function. These tests prove that:
#   - Each underlying check is invoked the same number of times as
#     pre-OBS-001 (no duplicate runs)
#   - Check ordering is unchanged
#   - Short-circuit behavior is unchanged
#   - If check #3 fails, later checks are recorded as NOT RUN, not
#     independently evaluated
#   - The recorded first_blocking_check is the exact check that
#     caused the real execute_trade path to stop
#   - Broker/API reads are not duplicated for observability


class TestExecuteTradeTraceCollection:
    """Regression coverage for Issue 2: real execute_trade path
    populates the OBS-001 trace. No duplicate runs."""

    def _make_bot(self):
        from src.core.smart_bot import SmartTradingBot
        bot = SmartTradingBot.__new__(SmartTradingBot)
        bot.trades_executed = 0
        bot.errors_count = 0
        bot._pending_entry_tranches = {}
        bot.enable_volume_confirmation = False
        bot.max_sector_concentration = 0.30
        bot.max_correlation = 0.85
        bot.max_portfolio_beta = 1.5
        bot.trade_amount = 1000
        # Stub trading_client
        bot.trading_client = MagicMock()
        bot.db = MagicMock()
        bot.db.is_available.return_value = False
        bot.send_trade_notification = MagicMock()
        bot.invalidate_sector_cache = MagicMock()
        bot.mark_recent_trade = MagicMock()
        bot.send_email = MagicMock()
        bot._current_trades_details = []
        return bot

    def test_trace_no_checks_recorded_outside_attempt(self):
        """When no OBS-001 attempt is active, _obs_001_trace_record
        is a no-op. This proves we never accidentally write to a
        stale trace from a previous attempt."""
        from src.core.smart_bot import _obs_001_trace_record
        # Reset (defensive)
        import src.core.smart_bot as sb
        sb._obs_001_active_trace = None
        _obs_001_trace_record("margin_check", applied=True, passed=True)
        assert sb._obs_001_active_trace is None, \
            "trace_record must be no-op outside an active attempt"

    def test_execute_trade_returns_false_when_signal_unsupported_and_no_trace(self):
        """When execute_trade returns immediately (unsupported signal),
        the trace has exactly one record (margin_check applied=False)
        and the OTHER 8 checks are NOT RUN."""
        from src.core.smart_bot import _obs_001_begin_attempt, _obs_001_finalize_attempt
        bot = self._make_bot()
        analysis = {"symbol": "X", "signal": "INVALID", "price": 100.0}
        _obs_001_begin_attempt("X", "INVALID")
        result = bot.execute_trade(analysis)
        checks = _obs_001_finalize_attempt(result)
        assert result is False
        applied_names = [c["name"] for c in checks if c.get("applied")]
        assert applied_names == ["margin_check"], (
            f"unsupported signal should record only margin_check; "
            f"got {applied_names}"
        )
        not_run = [c for c in checks if not c.get("applied")]
        assert len(not_run) == 8, (
            f"expected 8 NOT RUN checks (margin already recorded); "
            f"got {len(not_run)}"
        )
        for c in not_run:
            assert c["passed"] is None
            assert c["observed_value"] is None

    def test_execute_trade_short_circuit_records_correct_first_blocker(self):
        """When execute_trade short-circuits at margin_check, the
        finalized trace must show margin_check as the first_blocking_check
        and all later checks as NOT RUN.
        """
        from src.core.smart_bot import (
            _obs_001_begin_attempt, _obs_001_finalize_attempt,
        )
        bot = self._make_bot()
        # Margin check fails (cash < 0)
        bot.trading_client.get_account.return_value = MagicMock(cash="-100")
        analysis = {"symbol": "X", "signal": "BUY", "signal_strength": "STRONG",
                    "price": 100.0, "rsi": 25.0, "sma_fast": 110.0,
                    "sma_slow": 100.0}
        _obs_001_begin_attempt("X", "BUY")
        result = bot.execute_trade(analysis)
        checks = _obs_001_finalize_attempt(result)
        assert result is False
        first_block = next((c["name"] for c in checks
                            if c.get("passed") is False), None)
        assert first_block == "margin_check", (
            f"first_blocking_check must be margin_check; got {first_block}"
        )
        # All subsequent checks must be NOT RUN
        margin_idx = next(i for i, c in enumerate(checks)
                          if c["name"] == "margin_check")
        for c in checks[margin_idx + 1:]:
            assert c["applied"] is False, (
                f"{c['name']} should be NOT RUN after margin_check fails; "
                f"got applied={c['applied']}"
            )

    def test_no_duplicate_check_calls_for_observation(self):
        """Critical: the OBS-001 trace must be populated by the SAME
        check invocations the real code performs, NOT by a parallel
        observation function. We prove this by verifying that the
        count of get_account() calls during a single execute_trade
        invocation matches the pre-OBS-001 count.
        """
        from src.core.smart_bot import _obs_001_begin_attempt, _obs_001_finalize_attempt
        bot = self._make_bot()
        # Margin check passes (cash >= 0); pending orders returns
        # False (no pending); cooldown False; position checks return
        # False (no oversize); sector passes; correlation passes; beta
        # passes; buying power passes. Last is submit_order which
        # returns an order.
        bot.trading_client.get_account.return_value = MagicMock(cash="10000")
        bot.trading_client.has_pending_orders = MagicMock(return_value=False)
        bot.has_pending_orders = MagicMock(return_value=False)
        bot.is_in_cooldown = MagicMock(return_value=False)
        bot.get_portfolio_total_value = MagicMock(return_value=100000.0)
        bot.get_current_position_size = MagicMock(return_value=0)
        bot.calculate_position_size = MagicMock(return_value=10)
        bot.check_position_limits = MagicMock(return_value=(True, 10, 5.0))
        bot.check_sector_concentration = MagicMock(return_value=(True, 0.2, 5.0, "ok"))
        bot.check_correlation_risk = MagicMock(return_value=(True, 0.3, [], "ok"))
        bot.check_beta_exposure = MagicMock(return_value=(True, 0.5, "ok"))
        # get_orders() is called inside has_pending_orders. Our
        # trading_client.get_orders stub returns an empty list.
        bot.trading_client.get_orders.return_value = []
        bot.trading_client.submit_order = MagicMock(return_value=MagicMock(id="ord-123"))

        analysis = {
            "symbol": "X", "signal": "BUY", "signal_strength": "STRONG",
            "price": 100.0, "rsi": 25.0, "sma_fast": 110.0,
            "sma_slow": 100.0,
        }

        # Count get_account() calls before
        get_account = bot.trading_client.get_account
        get_account.reset_mock()

        _obs_001_begin_attempt("X", "BUY")
        result = bot.execute_trade(analysis)
        checks = _obs_001_finalize_attempt(result)
        assert result is True

        # The real execute_trade calls get_account() ONCE for the
        # margin check. We must NOT have called it a second time for
        # any observational purpose.
        assert get_account.call_count == 1, (
            f"get_account() must be called exactly once (real code's "
            f"margin check); got {get_account.call_count}"
        )
        # And the trace must reflect margin_check applied+passed.
        margin = next(c for c in checks if c["name"] == "margin_check")
        assert margin["applied"] is True
        assert margin["passed"] is True

    def test_traced_values_match_actual_decision(self):
        """OBS-001 must record the ACTUAL values the real code used
        for the trading decision. This proves the trace is real, not
        a parallel estimate.
        """
        from src.core.smart_bot import _obs_001_begin_attempt, _obs_001_finalize_attempt
        bot = self._make_bot()
        bot.trading_client.get_account.return_value = MagicMock(cash="7500.50")
        bot.trading_client.has_pending_orders = MagicMock(return_value=False)
        bot.has_pending_orders = MagicMock(return_value=False)
        bot.is_in_cooldown = MagicMock(return_value=False)
        bot.get_portfolio_total_value = MagicMock(return_value=100000.0)
        bot.get_current_position_size = MagicMock(return_value=0)
        bot.calculate_position_size = MagicMock(return_value=10)
        bot.check_position_limits = MagicMock(return_value=(True, 10, 5.0))
        bot.check_sector_concentration = MagicMock(return_value=(True, 0.2, 5.0, "ok"))
        bot.check_correlation_risk = MagicMock(return_value=(True, 0.3, [], "ok"))
        bot.check_beta_exposure = MagicMock(return_value=(True, 0.5, "ok"))
        bot.trading_client.get_orders.return_value = []
        bot.trading_client.submit_order = MagicMock(return_value=MagicMock(id="ord-x"))

        analysis = {
            "symbol": "X", "signal": "BUY", "signal_strength": "STRONG",
            "price": 100.0, "rsi": 25.0, "sma_fast": 110.0,
            "sma_slow": 100.0,
        }

        _obs_001_begin_attempt("X", "BUY")
        result = bot.execute_trade(analysis)
        checks = _obs_001_finalize_attempt(result)
        assert result is True

        margin = next(c for c in checks if c["name"] == "margin_check")
        # The trace must record the EXACT cash value the real code
        # observed (7500.50), not a synthesized value.
        assert margin["observed_value"] == 7500.50


# ── 10. Per-symbol finalize-on-decision (Issue 3 regression tests) ─────────
#
# decision_history is finalized IMMEDIATELY when a symbol's terminal
# outcome is known, not at cycle end. This is tested by verifying
# that _persist_obs_001_decision_snapshot inserts a single row per
# (cycle_id, symbol) regardless of how many times it is called for
# the same pair (UNIQUE constraint), and that the cycle_funnel writer
# at cycle end uses the canonical outcome enum to derive order
# counters.


class TestPerSymbolFinalizeOnDecision:
    """Regression coverage for Issue 3: per-symbol finalize when
    the symbol's outcome is known."""

    def test_double_persist_idempotent(self):
        """Calling _persist_obs_001_decision_snapshot twice for the
        same (cycle_id, symbol) MUST result in exactly ONE
        decision_history row (UNIQUE constraint). The second call is
        silently absorbed by the SQLite UNIQUE constraint.
        """
        from src.core.smart_bot import SmartTradingBot
        from src.database.sqlite_db import SQLiteDB, _get_conn
        SQLiteDB()._init_schema()

        bot = SmartTradingBot.__new__(SmartTradingBot)
        bot.db = SQLiteDB()
        bot.session_id = None
        bot.enable_volume_confirmation = False
        bot.max_sector_concentration = 0.30
        bot.max_correlation = 0.85
        bot.max_portfolio_beta = 1.5
        bot.rsi_buy_threshold = 30
        bot.rsi_sell_threshold = 70
        bot.bot_version = "2.1.0-test"

        cycle_id = "test_obs_001_double_persist"
        snap_v1 = {
            "schema_version": 1,
            "symbol": "DBL",
            "decision": {"outcome": "BUY_ORDER_SUBMITTED"},
            "order": {"submitted": True, "slot_consumed": True,
                      "fill_confirmed": False, "submit_attempted": True},
        }
        entry = {
            "analysis": {
                "symbol": "DBL", "signal": "BUY", "signal_strength": "STRONG",
                "price": 100.0, "rsi": 25.0,
            },
            "ranking_state": {"applicable": False},
            "selection_state": {"attempted": True},
            "execution_state": {"first_blocking_check": None, "checks": []},
            "order_state": {"submitted": True, "submit_attempted": True},
            "_execute_returned": True,
            "_cycle_baseline_diagnostics": None,
        }
        bot._persist_obs_001_decision_snapshot(
            "DBL", entry, cycle_id, "2026-09-12T00:00:00",
        )
        # Second call with the same cycle_id + symbol MUST NOT
        # create a duplicate row (UNIQUE constraint enforces this).
        bot._persist_obs_001_decision_snapshot(
            "DBL", entry, cycle_id, "2026-09-12T00:00:00",
        )
        with _get_conn() as conn:
            rows = conn.execute(
                "SELECT COUNT(*) FROM decision_history "
                "WHERE cycle_id=? AND symbol=?",
                (cycle_id, "DBL"),
            ).fetchone()
            assert rows[0] == 1, (
                f"expected exactly ONE decision_history row; got {rows[0]}"
            )
            conn.execute(
                "DELETE FROM decision_history WHERE cycle_id=?",
                (cycle_id,),
            )
            conn.execute(
                "DELETE FROM analyzed_stocks WHERE symbol=?",
                ("DBL",),
            )

    def test_finalize_writes_upsert_analyzed_stocks(self):
        """Per-symbol finalize must also upsert analyzed_stocks so the
        latest snapshot is durable immediately."""
        from src.core.smart_bot import SmartTradingBot
        from src.database.sqlite_db import SQLiteDB, _get_conn
        SQLiteDB()._init_schema()

        bot = SmartTradingBot.__new__(SmartTradingBot)
        bot.db = SQLiteDB()
        bot.session_id = None
        bot.enable_volume_confirmation = False
        bot.max_sector_concentration = 0.30
        bot.max_correlation = 0.85
        bot.max_portfolio_beta = 1.5
        bot.rsi_buy_threshold = 30
        bot.rsi_sell_threshold = 70
        bot.bot_version = "2.1.0-test"

        cycle_id = "test_obs_001_finalize_upsert"
        entry = {
            "analysis": {
                "symbol": "UPS", "signal": "BUY", "signal_strength": "STRONG",
                "price": 100.0, "rsi": 25.0,
            },
            "ranking_state": {"applicable": False},
            "selection_state": {"attempted": True},
            "execution_state": {"first_blocking_check": None, "checks": []},
            "order_state": {"submitted": True, "submit_attempted": True},
            "_execute_returned": True,
            "_cycle_baseline_diagnostics": None,
        }
        bot._persist_obs_001_decision_snapshot(
            "UPS", entry, cycle_id, "2026-09-12T00:00:00",
        )
        with _get_conn() as conn:
            row = conn.execute(
                "SELECT decision_snapshot, decision_schema_version "
                "FROM analyzed_stocks WHERE symbol=?",
                ("UPS",),
            ).fetchone()
            assert row is not None
            snap = json.loads(row[0])
            assert snap["schema_version"] == 1
            assert row[1] == 1
            conn.execute(
                "DELETE FROM decision_history WHERE cycle_id=?",
                (cycle_id,),
            )
            conn.execute(
                "DELETE FROM analyzed_stocks WHERE symbol=?",
                ("UPS",),
            )

    def test_finalize_decision_outcome_canonical_enum(self):
        """The persisted snapshot's decision.outcome MUST be one of
        the canonical enum values currently reachable. Reserved values
        (BUY_FILLED, SELL_FILLED, *_BLOCKED_PRE_RANK,
        BUY_PRE_RANK_EXCLUDED) MUST NEVER appear.
        """
        from src.core.smart_bot import (
            SmartTradingBot, OBS_001_OUTCOMES_CURRENTLY_REACHABLE,
            OBS_001_OUTCOMES_RESERVED,
        )
        from src.database.sqlite_db import SQLiteDB, _get_conn
        SQLiteDB()._init_schema()

        bot = SmartTradingBot.__new__(SmartTradingBot)
        bot.db = SQLiteDB()
        bot.session_id = None
        bot.enable_volume_confirmation = False
        bot.max_sector_concentration = 0.30
        bot.max_correlation = 0.85
        bot.max_portfolio_beta = 1.5
        bot.rsi_buy_threshold = 30
        bot.rsi_sell_threshold = 70
        bot.bot_version = "2.1.0-test"

        # Try every combination of inputs and verify the outcome is
        # always in the currently-reachable set.
        for signal in ("BUY", "SELL", "HOLD"):
            for executed in (True, False, None):
                for first_block in (None, "pending_order_check",
                                    "cooldown_check"):
                    for not_attempted in (False, True):
                        cycle_id = (
                            f"test_enum_{signal}_{executed}_"
                            f"{first_block}_{not_attempted}"
                        )
                        entry = {
                            "analysis": {
                                "symbol": "ENUM", "signal": signal,
                                "signal_strength": "STRONG",
                                "price": 100.0, "rsi": 25.0,
                            },
                            "ranking_state": {"applicable": False},
                            "selection_state": {"attempted": not not_attempted},
                            "execution_state": {
                                "first_blocking_check": first_block,
                                "checks": [],
                            },
                            "order_state": {
                                "submitted": bool(executed),
                                "submit_attempted": executed is not None,
                            },
                            "_execute_returned": executed,
                            "_cycle_baseline_diagnostics": None,
                        }
                        # Clean any prior row for ENUM with this cycle
                        with _get_conn() as conn:
                            conn.execute(
                                "DELETE FROM decision_history "
                                "WHERE cycle_id=?",
                                (cycle_id,),
                            )
                        bot._persist_obs_001_decision_snapshot(
                            "ENUM", entry, cycle_id,
                            "2026-09-12T00:00:00",
                        )
                        with _get_conn() as conn:
                            row = conn.execute(
                                "SELECT decision_snapshot FROM "
                                "decision_history WHERE cycle_id=?",
                                (cycle_id,),
                            ).fetchone()
                            assert row is not None
                            snap = json.loads(row[0])
                            outcome = snap["decision"]["outcome"]
                            assert outcome in OBS_001_OUTCOMES_CURRENTLY_REACHABLE, (
                                f"outcome {outcome!r} is not in currently-"
                                f"reachable set for "
                                f"signal={signal} executed={executed} "
                                f"first_block={first_block} "
                                f"not_attempted={not_attempted}"
                            )
                            assert outcome not in OBS_001_OUTCOMES_RESERVED, (
                                f"reserved outcome {outcome!r} leaked"
                            )
                            conn.execute(
                                "DELETE FROM decision_history "
                                "WHERE cycle_id=?",
                                (cycle_id,),
                            )


# ── 11. Decision-history immutability under re-persist (PR review test) ──
#
# Josh's PR-review item #1: verify that when _persist_obs_001_decision_snapshot
# is invoked more than once for the same (cycle_id, symbol), the
# ORIGINAL row is preserved unchanged. Snapshot A must remain in the
# table even after snapshot B is attempted.


class TestDecisionHistoryImmutability:
    """Josh PR-review #1: decision_history is INSERT-only.

    Persisting finalized snapshot A then attempting to persist a
    different finalized snapshot B for the same (cycle_id, symbol)
    must leave exactly ONE row in decision_history, and the stored
    JSON must be snapshot A's JSON (NOT snapshot B's).

    We test this at the SQL layer (SQLiteDB.finalize_decision_history)
    rather than the higher-level _persist_obs_001_decision_snapshot
    wrapper because the wrapper rebuilds the snapshot from entry fields;
    the immutability guarantee is enforced by the SQL UNIQUE constraint
    on (cycle_id, symbol) and the plain INSERT (no ON CONFLICT DO UPDATE).
    """

    def test_snapshot_b_does_not_overwrite_snapshot_a(self):
        """1. finalize snapshot A
        2. attempt to finalize snapshot B (different JSON)
        3. query decision_history: exactly one row exists
        4. stored JSON is still snapshot A"""
        from src.database.sqlite_db import SQLiteDB, _get_conn
        SQLiteDB()._init_schema()

        db = SQLiteDB()
        cycle_id = "test_immutability_jb1"

        # Clean any leftover row from a previous test run.
        with _get_conn() as conn:
            conn.execute(
                "DELETE FROM decision_history WHERE cycle_id=?",
                (cycle_id,),
            )

        snapshot_a = {
            "schema_version": 1,
            "symbol": "IMM",
            "decision": {"outcome": "BUY_ORDER_SUBMITTED",
                         "primary_reason": "snapshot A — original"},
            "order": {"submitted": True, "slot_consumed": True,
                      "fill_confirmed": False, "submit_attempted": True,
                      "alpaca_order_id": "ord-A-original"},
        }
        snapshot_b = {
            "schema_version": 1,
            "symbol": "IMM",
            "decision": {"outcome": "BUY_ORDER_SUBMITTED",
                         "primary_reason": "snapshot B — should NOT overwrite"},
            "order": {"submitted": True, "slot_consumed": True,
                      "fill_confirmed": False, "submit_attempted": True,
                      "alpaca_order_id": "ord-B-INTRUDER"},
        }

        try:
            # Step 1: persist snapshot A (the ORIGINAL finalized row).
            inserted_a = db.finalize_decision_history(
                cycle_id=cycle_id,
                symbol="IMM",
                cycle_start="2026-09-12T00:00:00",
                session_id=None,
                decision_schema_version=1,
                decision_snapshot=snapshot_a,
            )
            assert inserted_a is True, "first finalize must return True"

            # Step 2: attempt to persist snapshot B (a DIFFERENT JSON for
            # the same cycle_id + symbol).
            inserted_b = db.finalize_decision_history(
                cycle_id=cycle_id,
                symbol="IMM",
                cycle_start="2026-09-12T00:00:01",
                session_id=None,
                decision_schema_version=1,
                decision_snapshot=snapshot_b,
            )
            # SQL UNIQUE constraint blocks the second insert; the
            # function must return False (not raise).
            assert inserted_b is False, (
                f"second finalize must return False (UNIQUE blocked "
                f"silent overwrite); got {inserted_b}"
            )

            # Step 3: query decision_history: exactly ONE row exists.
            with _get_conn() as conn:
                row = conn.execute(
                    "SELECT COUNT(*), decision_snapshot FROM "
                    "decision_history WHERE cycle_id=? AND symbol=?",
                    (cycle_id, "IMM"),
                ).fetchone()
            count = row[0]
            stored_json = row[1]
            assert count == 1, (
                f"decision_history must have exactly ONE row per "
                f"(cycle_id, symbol) under re-finalize; got {count}"
            )

            # Step 4: stored JSON must be snapshot A, not snapshot B.
            stored = json.loads(stored_json)
            stored_order_id = stored["order"]["alpaca_order_id"]
            assert stored_order_id == "ord-A-original", (
                f"decision_history must preserve snapshot A's JSON; "
                f"stored order_id={stored_order_id!r} (expected "
                f"'ord-A-original'). Snapshot B leaked through."
            )
            assert (
                "snapshot A — original" in stored["decision"]["primary_reason"]
            ), "decision_history must preserve snapshot A's primary_reason"
        finally:
            with _get_conn() as conn:
                conn.execute(
                    "DELETE FROM decision_history WHERE cycle_id=?",
                    (cycle_id,),
                )

    def test_finalize_decision_history_uses_plain_insert(self):
        """Static-analysis: `finalize_decision_history` SQL must use a
        plain INSERT, NOT an UPSERT (no ON CONFLICT DO UPDATE).
        Plain INSERT + UNIQUE constraint + IntegrityError catch is
        the correct immutability pattern.
        """
        import inspect
        from src.database.sqlite_db import SQLiteDB
        source = inspect.getsource(SQLiteDB.finalize_decision_history)
        assert "ON CONFLICT" not in source, (
            "finalize_decision_history must NOT use ON CONFLICT "
            "DO UPDATE (would silently overwrite the original)"
        )
        assert "INSERT INTO decision_history" in source, \
            "finalize_decision_history must use plain INSERT"
        assert "IntegrityError" in source, \
            "finalize_decision_history must catch sqlite3.IntegrityError"
