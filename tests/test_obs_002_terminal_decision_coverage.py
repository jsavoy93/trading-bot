"""
OBS-002 — Terminal Decision Coverage.

Target invariant for a normally completed cycle:

    cycle_funnel.analyzed_count
    ==
    COUNT(DISTINCT decision_history.symbol WHERE cycle_id = current_cycle)

Each analyzed symbol must have exactly one terminal OBS-001
decision_history row explaining what happened. The helper
`_persist_skipped_terminal_decision` covers the six gap paths:

  G1: `if not analysis:` no-market-data sub-case
  G2: `if not analysis:` fallback HOLD compute (analyze returned None)
  G3: NaN exception in fallback score compute (int(NaN) → ValueError)
  G4: BUY/SELL signal with signal_strength == "WEAK"
  G5: BUY/SELL signal with signal_strength == "CONFLICTED"
  G6: Outer per-symbol `except Exception` (incl. NaN-to-int in any
      indicator calc, network blips, etc.)

Outcome mapping:

  G1, G2, G3, G6 → SKIPPED_INVALID_DATA (existing canonical outcome)
  G4, G5         → HOLD_INELIGIBLE      (existing canonical outcome)

This file proves:
  1. Helper builds a schema-version-1 snapshot for each gap path.
  2. The snapshot's strategy_eligibility, scoring, ranking, selection,
     execution_checks, and order blocks truthfully reflect the gap
     (no fabrication of unavailable fields).
  3. The helper persists ONE row to decision_history (UNIQUE safety net)
     and upserts analyzed_stocks.decision_snapshot.
  4. The outcome enum does NOT gain new values (schema migration
     forbidden by the OBSERVABILITY constraint).
  5. NaN inputs are handled without raising — the recurring
     "cannot convert float NaN to integer" exception cannot bypass
     OBS persistence.
"""

import json
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── helpers ──────────────────────────────────────────────────────────────


def _make_bot():
    """Construct a minimal SmartTradingBot-like object for testing the
    skipped-terminal-decision helper in isolation. Bypasses __init__
    to avoid Alpaca client construction. Stubs the DB layer with a
    temporary SQLite database so finalize_decision_history and
    save_analysis_result work end-to-end without touching trading_bot.db.
    """
    from src.core.smart_bot import SmartTradingBot
    bot = SmartTradingBot.__new__(SmartTradingBot)
    bot.rsi_buy_threshold = 30
    bot.rsi_sell_threshold = 70
    bot.enable_volume_confirmation = True
    bot.bot_version = "2.1.0-test"
    bot.session_id = 99999
    # Stub the DB layer so .is_available() returns True and the
    # finalize_decision_history / save_analysis_result calls succeed.
    # Tests that need real persistence use the temp_db fixture and
    # reassign bot.db; tests that only need the snapshot dict use a
    # throwaway MagicMock.
    bot.db = MagicMock()
    bot.db.is_available.return_value = True
    return bot


def _temp_db(monkeypatch):
    """Optional helper retained for ad-hoc integration tests that need
    a real SQLite file. Most tests use the _make_bot() MagicMock DB
    instead — it avoids the schema-drift fragility of the SQLiteDB
    init migration and exercises the helper logic in isolation."""
    from pathlib import Path as _P
    import sys
    import src.database.sqlite_db as sqlite_mod_src
    src_root = "/root/.openclaw/workspace/trading-bot/src"
    if src_root not in sys.path:
        sys.path.insert(0, src_root)
    import database.sqlite_db as sqlite_mod_dash

    fd, tmp_path = tempfile.mkstemp(prefix="obs_002_", suffix=".sqlite3")
    import os
    os.close(fd)
    new_path = _P(tmp_path)
    monkeypatch.setattr(sqlite_mod_src, "DB_PATH", new_path)
    monkeypatch.setattr(sqlite_mod_dash, "DB_PATH", new_path)

    from src.database.sqlite_db import SQLiteDB
    db = SQLiteDB()
    return new_path, db


# ── 1. Schema/contract sanity ─────────────────────────────────────────────


class TestOBS002Contract:
    def test_schema_version_unchanged(self):
        """OBS-002 must NOT bump OBS_001_SCHEMA_VERSION. This is an
        OBSERVABILITY task; schema migration is forbidden."""
        from src.core.smart_bot import OBS_001_SCHEMA_VERSION
        assert OBS_001_SCHEMA_VERSION == 1

    def test_outcome_enum_unchanged(self):
        """OBS-002 reuses SKIPPED_INVALID_DATA and HOLD_INELIGIBLE only.
        No new canonical outcome values are introduced."""
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

    def test_helper_attached_to_bot(self):
        from src.core.smart_bot import SmartTradingBot
        assert hasattr(SmartTradingBot, "_persist_skipped_terminal_decision")


# ── 2. Per-path snapshot truthfulness ─────────────────────────────────────


class TestSkippedHelperSnapshots:

    def test_g1_no_market_data_produces_skipped_invalid_data(self):
        bot = _make_bot()
        snap = bot._persist_skipped_terminal_decision(
            "AAPL",
            cycle_id="cycle_test_001",
            cycle_start_iso="2026-09-14T12:00:00+00:00",
            outcome="SKIPPED_INVALID_DATA",
            primary_reason="no market data (needs 30 bars)",
        )
        assert snap is not None
        assert snap["decision"]["outcome"] == "SKIPPED_INVALID_DATA"
        assert "no market data" in snap["decision"]["primary_reason"]
        # strategy_eligibility must truthfully say ineligible
        assert snap["strategy_eligibility"]["strategy_eligible"] is False
        # scoring must declare invalid data
        assert snap["scoring"]["score_invalid_data"] is True
        # ranking, selection, execution, order must all reflect NOT RUN
        assert snap["ranking"]["applicable"] is False
        assert snap["ranking"]["candidate_rank"] is None
        assert snap["selection"]["attempted"] is False
        assert snap["execution_checks"]["checks"] == []
        assert snap["execution_checks"]["first_blocking_check"] is None
        assert snap["order"]["submitted"] is False
        assert snap["order"]["slot_consumed"] is False

    def test_g4_weak_signal_produces_hold_ineligible(self):
        bot = _make_bot()
        snap = bot._persist_skipped_terminal_decision(
            "AAPL",
            cycle_id="cycle_test_002",
            cycle_start_iso="2026-09-14T12:00:00+00:00",
            outcome="HOLD_INELIGIBLE",
            primary_reason="weak signal strength (WEAK)",
            analysis={
                "signal": "BUY",
                "signal_strength": "WEAK",
                "total_score": 65.0,
                "rsi": 28.0, "sma_fast": 110.0, "sma_slow": 100.0,
                "macd_histogram": 0.5, "volume_ratio": 1.2,
            },
        )
        assert snap["decision"]["outcome"] == "HOLD_INELIGIBLE"
        # Truthful: signal=BUY means non-score strategy gates PASSED
        # (RSI/SMA/MACD). The WEAK strength is an execution filter, not
        # a strategy gate. We MUST NOT lie about the gate outcome.
        assert snap["strategy_eligibility"]["signal"] == "BUY"
        assert snap["strategy_eligibility"]["signal_strength"] == "WEAK"
        # Decision primary_reason captures WHY we did not act.
        assert "weak signal" in snap["decision"]["primary_reason"]

    def test_g5_conflicted_signal_produces_hold_ineligible(self):
        bot = _make_bot()
        snap = bot._persist_skipped_terminal_decision(
            "AAPL",
            cycle_id="cycle_test_003",
            cycle_start_iso="2026-09-14T12:00:00+00:00",
            outcome="HOLD_INELIGIBLE",
            primary_reason="AI conflicts with technical signal (CONFLICTED)",
            analysis={
                "signal": "BUY",
                "signal_strength": "CONFLICTED",
                "total_score": 70.0,
            },
        )
        assert snap["decision"]["outcome"] == "HOLD_INELIGIBLE"
        assert snap["strategy_eligibility"]["signal_strength"] == "CONFLICTED"
        assert "CONFLICTED" in snap["decision"]["primary_reason"]

    def test_g6_outer_exception_produces_skipped_with_exception_detail(self):
        bot = _make_bot()
        snap = bot._persist_skipped_terminal_decision(
            "AAPL",
            cycle_id="cycle_test_004",
            cycle_start_iso="2026-09-14T12:00:00+00:00",
            outcome="SKIPPED_INVALID_DATA",
            primary_reason=(
                "analysis-body exception: ValueError: cannot convert "
                "float NaN to integer"
            ),
        )
        assert snap["decision"]["outcome"] == "SKIPPED_INVALID_DATA"
        assert "ValueError" in snap["decision"]["primary_reason"]
        assert "NaN" in snap["decision"]["primary_reason"]
        assert snap["scoring"]["score_invalid_data"] is True

    def test_invalid_outcome_rejected(self):
        """A bogus outcome value MUST NOT be persisted. The helper logs
        at DEBUG and returns None without writing anything."""
        bot = _make_bot()
        snap = bot._persist_skipped_terminal_decision(
            "AAPL",
            cycle_id="cycle_test_005",
            cycle_start_iso="2026-09-14T12:00:00+00:00",
            outcome="BOGUS_OUTCOME",
            primary_reason="test",
        )
        assert snap is None

    def test_missing_cycle_id_defensive(self):
        """No cycle_id → no persist. UNIQUE(cycle_id, symbol) safety net
        would be violated if we tried to write without a cycle_id."""
        bot = _make_bot()
        snap = bot._persist_skipped_terminal_decision(
            "AAPL",
            cycle_id="",
            cycle_start_iso="2026-09-14T12:00:00+00:00",
            outcome="SKIPPED_INVALID_DATA",
            primary_reason="test",
        )
        assert snap is None


# ── 3. Persistence round-trip ────────────────────────────────────────────


class TestSkippedHelperPersistence:

    def test_decision_history_row_is_inserted(self):
        """The helper MUST call finalize_decision_history exactly once
        per (cycle_id, symbol) with the built snapshot."""
        bot = _make_bot()
        snap = bot._persist_skipped_terminal_decision(
            "AAPL",
            cycle_id="cycle_test_persist_001",
            cycle_start_iso="2026-09-14T12:00:00+00:00",
            outcome="SKIPPED_INVALID_DATA",
            primary_reason="no market data",
        )
        assert snap is not None
        # Verify finalize_decision_history was called once with our
        # snapshot and the right schema version.
        bot.db.finalize_decision_history.assert_called_once()
        kwargs = bot.db.finalize_decision_history.call_args.kwargs
        assert kwargs["cycle_id"] == "cycle_test_persist_001"
        assert kwargs["symbol"] == "AAPL"
        assert kwargs["decision_schema_version"] == 1
        assert kwargs["decision_snapshot"]["decision"]["outcome"] == (
            "SKIPPED_INVALID_DATA"
        )

    def test_analyzed_stocks_snapshot_is_upserted(self):
        """The helper MUST upsert the snapshot into analyzed_stocks."""
        bot = _make_bot()
        bot._persist_skipped_terminal_decision(
            "NVDA",
            cycle_id="cycle_test_persist_002",
            cycle_start_iso="2026-09-14T12:00:00+00:00",
            outcome="SKIPPED_INVALID_DATA",
            primary_reason="no market data",
        )
        bot.db.save_analysis_result.assert_called_once()
        kwargs = bot.db.save_analysis_result.call_args.args
        # args[0] is symbol; args[1] is the analysis dict.
        assert kwargs[0] == "NVDA"
        assert kwargs[1]["decision_snapshot"]["decision"]["outcome"] == (
            "SKIPPED_INVALID_DATA"
        )
        assert kwargs[1]["decision_schema_version"] == 1

    def test_unique_constraint_safety_net(self):
        """If finalize_decision_history raises IntegrityError (UNIQUE
        violation), the helper MUST NOT propagate the error. The
        UNIQUE(cycle_id, symbol) constraint is the documented safety
        net against duplicate finalization."""
        bot = _make_bot()
        # Simulate a UNIQUE-violation on the second call.
        import sqlite3 as _sqlite3
        bot.db.finalize_decision_history.side_effect = _sqlite3.IntegrityError(
            "UNIQUE constraint failed: decision_history.cycle_id, decision_history.symbol"
        )
        # Should NOT raise.
        snap = bot._persist_skipped_terminal_decision(
            "MSFT",
            cycle_id="cycle_test_dup",
            cycle_start_iso="2026-09-14T12:00:00+00:00",
            outcome="SKIPPED_INVALID_DATA",
            primary_reason="first call",
        )
        # The snapshot was still built and returned; the IntegrityError
        # was logged and swallowed.
        assert snap is not None
        assert snap["decision"]["outcome"] == "SKIPPED_INVALID_DATA"


# ── 4. Snapshot field truthfulness ───────────────────────────────────────


class TestSkippedHelperSnapshotFields:

    def test_no_total_score_fabricated_for_invalid_data(self):
        """For SKIPPED_INVALID_DATA, total_score MUST be None — we do
        NOT invent a score for symbols whose compute path failed."""
        bot = _make_bot()
        snap = bot._persist_skipped_terminal_decision(
            "XYZ",
            cycle_id="cycle_test_no_total",
            cycle_start_iso="2026-09-14T12:00:00+00:00",
            outcome="SKIPPED_INVALID_DATA",
            primary_reason="no market data",
        )
        assert snap["scoring"]["total_score"] is None

    def test_ranking_candidate_rank_is_null(self):
        bot = _make_bot()
        snap = bot._persist_skipped_terminal_decision(
            "XYZ",
            cycle_id="cycle_test_rank_null",
            cycle_start_iso="2026-09-14T12:00:00+00:00",
            outcome="SKIPPED_INVALID_DATA",
            primary_reason="no market data",
        )
        assert snap["ranking"]["candidate_rank"] is None
        assert snap["ranking"]["eligible_candidate_count"] is None
        assert snap["ranking"]["applicable"] is False

    def test_execution_checks_empty_for_skipped(self):
        bot = _make_bot()
        snap = bot._persist_skipped_terminal_decision(
            "XYZ",
            cycle_id="cycle_test_exec_empty",
            cycle_start_iso="2026-09-14T12:00:00+00:00",
            outcome="SKIPPED_INVALID_DATA",
            primary_reason="no market data",
        )
        # `checks` is the list of checks that ACTUALLY ran. For skipped
        # symbols, no check ran, so `checks` is empty.
        assert snap["execution_checks"]["checks"] == []
        assert snap["execution_checks"]["first_blocking_check"] is None
        # `evaluated_in_order` is the documented CHECK ORDER (the order
        # the bot WOULD have run if it had reached execute_trade). For
        # skipped symbols, execute_trade was never reached — but the
        # documented order is preserved so renderers can still show
        # "NOT RUN" placeholders for every check, matching the Phase A
        # renderer convention.
        from src.core.smart_bot import OBS_001_EXECUTION_CHECK_ORDER
        assert snap["execution_checks"]["evaluated_in_order"] == list(OBS_001_EXECUTION_CHECK_ORDER)

    def test_fill_confirmed_false_and_slot_consumed_false(self):
        bot = _make_bot()
        snap = bot._persist_skipped_terminal_decision(
            "XYZ",
            cycle_id="cycle_test_order",
            cycle_start_iso="2026-09-14T12:00:00+00:00",
            outcome="SKIPPED_INVALID_DATA",
            primary_reason="no market data",
        )
        assert snap["order"]["fill_confirmed"] is False
        assert snap["order"]["slot_consumed"] is False
        assert snap["order"]["submitted"] is False

    def test_hold_ineligible_keeps_signal_info_truthful(self):
        """For G4/G5, the signal WAS BUY/SELL (passed gates) but
        signal_strength made it unactionable. Snapshot must record
        the truth (signal=BUY/SELL, gates passed)."""
        bot = _make_bot()
        snap = bot._persist_skipped_terminal_decision(
            "XYZ",
            cycle_id="cycle_test_signal_truth",
            cycle_start_iso="2026-09-14T12:00:00+00:00",
            outcome="HOLD_INELIGIBLE",
            primary_reason="weak signal strength (WEAK)",
            analysis={
                "signal": "BUY",
                "signal_strength": "WEAK",
                "total_score": 70.0,
                "rsi": 28.0, "sma_fast": 110.0, "sma_slow": 100.0,
                "macd_histogram": 0.5, "volume_ratio": 1.2,
            },
        )
        # Truthful: signal=BUY means the non-score strategy gates
        # passed (RSI/SMA/MACD/Volume). strategy_eligibility reflects
        # gate truth; the WEAK verdict is captured in primary_reason.
        assert snap["strategy_eligibility"]["signal"] == "BUY"
        assert snap["strategy_eligibility"]["signal_strength"] == "WEAK"
        # The score is real (WEAK does not imply invalid data).
        assert snap["scoring"]["score_invalid_data"] is False
        assert snap["scoring"]["total_score"] == 70.0


# ── 5. NaN hardening (PART 3) ─────────────────────────────────────────────


class TestNaNHardening:

    def test_nan_inputs_do_not_raise(self):
        """The recurring 'cannot convert float NaN to integer' failure
        MUST NOT bypass OBS-001 persistence. The helper itself must
        accept NaN inputs without throwing — the per-symbol outer
        try/except in run_analysis handles the throwing path. The
        helper's own snapshot build must be NaN-safe and must NOT
        serialize NaN to JSON (JSON has no NaN representation)."""
        import math as _math
        bot = _make_bot()
        # NaN values in analysis dict; helper should still build a
        # SKIPPED_INVALID_DATA snapshot.
        snap = bot._persist_skipped_terminal_decision(
            "NAN",
            cycle_id="cycle_test_nan",
            cycle_start_iso="2026-09-14T12:00:00+00:00",
            outcome="SKIPPED_INVALID_DATA",
            primary_reason="analysis-body exception: ValueError: cannot convert float NaN to integer",
            analysis={
                "price": float("nan"),
                "rsi": float("nan"),
                "total_score": float("nan"),
                "signal": "HOLD",
                "signal_strength": "WEAK",
            },
        )
        # Helper did not raise. Snapshot was built.
        assert snap is not None
        assert snap["decision"]["outcome"] == "SKIPPED_INVALID_DATA"
        # NaN in total_score must be normalized to None — JSON has no
        # NaN literal and storing NaN would corrupt the snapshot.
        assert snap["scoring"]["total_score"] is None
        # All NaN-derived fields normalized.
        assert snap["strategy_eligibility"]["gates"] == []
        # The schema-version is unchanged.
        assert snap["schema_version"] == 1
        # JSON round-trip must succeed (NaN cannot survive json.dumps).
        blob = json.dumps(snap, allow_nan=False)
        parsed = json.loads(blob)
        assert parsed["decision"]["outcome"] == "SKIPPED_INVALID_DATA"
        assert parsed["scoring"]["total_score"] is None

    def test_nan_in_rsi_does_not_propagate_to_gates(self):
        """The snapshot builder must NOT inject NaN into the strategy
        gate observed_value fields; if rsi is NaN, the rsi_oversold
        gate must NOT appear (since `pd.notna(rsi)` is the gate for
        including the gate)."""
        bot = _make_bot()
        snap = bot._persist_skipped_terminal_decision(
            "NAN2",
            cycle_id="cycle_test_nan_rsi",
            cycle_start_iso="2026-09-14T12:00:00+00:00",
            outcome="SKIPPED_INVALID_DATA",
            primary_reason="NaN in score compute",
            analysis={
                "rsi": float("nan"),
                "sma_fast": 100.0,
                "sma_slow": 105.0,
                "macd_histogram": 0.0,
                "volume_ratio": 1.0,
            },
        )
        # No NaN JSON values in gates
        for gate in snap["strategy_eligibility"]["gates"]:
            for k, v in gate.items():
                if isinstance(v, float):
                    assert v == v, (  # NaN check: NaN != NaN
                        f"NaN found in gate {gate.get('name')!r} "
                        f"field {k!r}"
                    )


# ── 6. End-to-end invariant: analyzed_count == dh_count for gap paths ────


class TestTerminalCoverageInvariant:

    def test_invariant_holds_for_mixed_gap_paths(self):
        """Simulate a cycle where 6 symbols hit different gap paths.
        The helper MUST be called exactly 6 times for distinct symbols
        within the same cycle_id, with one decision_history insert per
        call. UNIQUE(cycle_id, symbol) prevents duplicates."""
        bot = _make_bot()
        cycle_id = "cycle_test_invariant"
        cycle_start = "2026-09-14T12:00:00+00:00"

        # G1: no market data
        bot._persist_skipped_terminal_decision(
            "G1_SYM", cycle_id=cycle_id, cycle_start_iso=cycle_start,
            outcome="SKIPPED_INVALID_DATA", primary_reason="no market data",
        )
        # G2: fallback HOLD compute (analyze returned None)
        bot._persist_skipped_terminal_decision(
            "G2_SYM", cycle_id=cycle_id, cycle_start_iso=cycle_start,
            outcome="SKIPPED_INVALID_DATA",
            primary_reason="analyze_symbol returned None",
        )
        # G3: NaN exception
        bot._persist_skipped_terminal_decision(
            "G3_SYM", cycle_id=cycle_id, cycle_start_iso=cycle_start,
            outcome="SKIPPED_INVALID_DATA",
            primary_reason="NaN in score compute",
        )
        # G4: WEAK
        bot._persist_skipped_terminal_decision(
            "G4_SYM", cycle_id=cycle_id, cycle_start_iso=cycle_start,
            outcome="HOLD_INELIGIBLE",
            primary_reason="weak signal strength (WEAK)",
            analysis={"signal": "BUY", "signal_strength": "WEAK"},
        )
        # G5: CONFLICTED
        bot._persist_skipped_terminal_decision(
            "G5_SYM", cycle_id=cycle_id, cycle_start_iso=cycle_start,
            outcome="HOLD_INELIGIBLE",
            primary_reason="AI conflicts with technical signal",
            analysis={"signal": "BUY", "signal_strength": "CONFLICTED"},
        )
        # G6: outer exception
        bot._persist_skipped_terminal_decision(
            "G6_SYM", cycle_id=cycle_id, cycle_start_iso=cycle_start,
            outcome="SKIPPED_INVALID_DATA",
            primary_reason="analysis-body exception: ValueError",
        )

        # Exactly 6 finalize_decision_history calls for distinct symbols.
        assert bot.db.finalize_decision_history.call_count == 6
        symbols = [
            call.kwargs["symbol"]
            for call in bot.db.finalize_decision_history.call_args_list
        ]
        assert set(symbols) == {
            "G1_SYM", "G2_SYM", "G3_SYM", "G4_SYM", "G5_SYM", "G6_SYM",
        }
        # All six were called for the same cycle_id (the cycle_funnel
        # row would correspond to cycle_funnel.analyzed_count = 6).
        for call in bot.db.finalize_decision_history.call_args_list:
            assert call.kwargs["cycle_id"] == cycle_id
