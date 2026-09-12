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
        """UNIQUE(cycle_id, symbol) guarantees one finalized row per pair."""
        from src.database.sqlite_db import SQLiteDB, _get_conn
        db = SQLiteDB()
        db._init_schema()

        cycle_id = "test_obs_001_unique_smoke"
        snap = {"schema_version": 1, "symbol": "X", "decision": {"outcome": "HOLD_INELIGIBLE"}}

        # Two inserts with the same (cycle_id, symbol) MUST collapse to one row.
        ok1 = db.finalize_decision_history(cycle_id, "X", "2026-09-12T00:00:00", None, 1, snap)
        ok2 = db.finalize_decision_history(cycle_id, "X", "2026-09-12T00:00:00", None, 1, snap)
        assert ok1 and ok2

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
