# OBS-001 Phase A — Implementation Archive

- Task: OBS-001 Phase A — Decision Trace + Dashboard Observability
- Owner: trading-manager (implementation delegated by Josh)
- Branch: `agent/obs-001-phase-a-decision-snapshot`
- Commits on branch: 2 (implementation + dashboard + tests)
- Mode: implementation reporting (changes repository content)
- Status: COMPLETE; awaiting Josh's review and merge
- Prerequisite: SCORE-002 merged (`main=48dda5b`,
  `origin/main=48dda5b`, working tree clean pre-branch)
- Runtime: PID 702290 unchanged (1 day 26 minutes elapsed),
  paper-only ACTIVE; no restart triggered

## Implementation Guardrails Honored

1. **Schema version = 1** — `OBS_001_SCHEMA_VERSION = 1`. Legacy
   rows: `decision_snapshot IS NULL`.
2. **NO pre-rank filtering** — `_check_pre_rank_actionability` is
   NOT a method on SmartTradingBot. Test asserts this.
3. **Cycle funnel reflects current behavior** — counters derived
   from the actual run_analysis control flow, not invented. The
   only currently proven `not_attempted_reason` is `slots_filled`.
4. **Pipeline preserved** — full universe analyzed → strategy
   eligibility → SCORE-002 ranking → sequential attempts →
   current execute_trade checks → current submit_order behavior.
   NO changes to: candidate population, rank, attempt order,
   score, strategy gates, risk limits, execution checks, sizing,
   slot semantics, SELL behavior, brokerage behavior.
5. **Snapshot v1 with required blocks** — IDENTITY,
   STRATEGY_ELIGIBILITY, SCORING, RANKING, SELECTION,
   EXECUTION_CHECKS, ORDER, DECISION, BASELINE_DIAGNOSTICS.
   BASELINE_DIAGNOSTICS is OBSERVED-ONLY (labeled in snapshot).
6. **Exposure fidelity** — confirmed positions, pending orders,
   internal bot bookkeeping are explicitly separate. _gap_note
   fields on checks that ignore pending exposure.

## Final Outcome Enum (production)

```python
OBS_001_OUTCOMES_CURRENTLY_REACHABLE = frozenset({
    "BUY_ORDER_SUBMITTED",          # submit_order returned an order object
    "BUY_ORDER_FAILED",             # submit_order raised or returned None
    "BUY_ELIGIBLE_NOT_SELECTED",    # ranked beyond slots_remaining slice
    "BUY_BLOCKED_DYNAMIC",          # execute_trade returned False (BUY)
    "HOLD_INELIGIBLE",              # strategy_eligible=False
    "SELL_ORDER_SUBMITTED",
    "SELL_ORDER_FAILED",
    "SELL_BLOCKED_NO_POSITION",     # SELL signal, no position owned
    "SELL_BLOCKED_DYNAMIC",         # execute_trade returned False (SELL)
    "SKIPPED_INVALID_DATA",         # fail-closed (missing MACD/ATR)
})

OBS_001_OUTCOMES_RESERVED = frozenset({
    "BUY_FILLED",                   # RESERVED — fill polling not implemented
    "SELL_FILLED",                  # RESERVED — fill polling not implemented
    "BUY_BLOCKED_PRE_RANK",         # RESERVED for PIPELINE-001
    "SELL_BLOCKED_PRE_RANK",        # RESERVED for PIPELINE-001
    "BUY_PRE_RANK_EXCLUDED",        # RESERVED for PIPELINE-001
})
```

Tests prove reserved values are NEVER produced by current code paths.

## Cycle Funnel Schema (production)

```sql
CREATE TABLE cycle_funnel (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id TEXT NOT NULL UNIQUE,
    session_id INTEGER,
    cycle_start TEXT NOT NULL,
    cycle_end TEXT NOT NULL,
    analyzed_count INTEGER NOT NULL,
    strategy_eligible_count INTEGER NOT NULL,
    ranked_candidate_count INTEGER NOT NULL,
    execution_attempt_count INTEGER NOT NULL,
    execution_blocked_count INTEGER NOT NULL,
    order_submission_attempt_count INTEGER NOT NULL,
    order_submitted_count INTEGER NOT NULL,
    order_failed_count INTEGER NOT NULL,
    not_attempted_count INTEGER NOT NULL,
    not_attempted_reason TEXT,        -- currently only 'slots_filled'
    bot_version TEXT,
    schema_version INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now'))
);
```

### Funnel invariants (mathematically true for current runtime)

- `analyzed_count >= strategy_eligible_count >= ranked_candidate_count`
- `ranked_candidate_count == execution_attempt_count + not_attempted_count`
- `execution_attempt_count == execution_blocked_count + order_submission_attempt_count`
- `order_submission_attempt_count == order_submitted_count + order_failed_count`

`pre_rank_actionable_count` is intentionally NOT in the funnel — no active pre-rank gate in current code.

## Slot-Consumption Semantics (v1)

`slot_consumed=True iff submit_order returned an order object`. Documented in:
- `OBS_001_SLOT_SEMANTICS_VERSION = 1`
- Snapshot `order.slot_consumed` aligns with `order.submitted`
- Snapshot `order.fill_confirmed=False` always (no fill polling)

## Schema Migration

Two new columns on `analyzed_stocks` (nullable; legacy rows = NULL):
- `decision_snapshot TEXT`
- `decision_schema_version INTEGER DEFAULT 0`

Two new tables:
- `decision_history`: one finalized row per (cycle_id, symbol);
  UNIQUE(cycle_id, symbol) enforces single-row invariant.
- `cycle_funnel`: one row per cycle; UNIQUE(cycle_id) enforces
  single-row invariant.

All migrations use the existing try/except "duplicate column
name" pattern (sqlite_db.py L122-156). Idempotent. No data
rewrite. No row drop.

## Snapshot v1 Shape (built by `_build_decision_snapshot`)

```python
{
    "schema_version": 1,
    "schema_version_notes": "OBS-001 Phase A production. ...",
    "symbol": str,
    "analysis_timestamp": str,            # ISO
    "session_id": int | None,
    "bot_version": str,
    "timeframe_mode": "multi_timeframe" | "single_timeframe" | None,
    "cycle_id": str,
    "strategy_eligibility": {
        "strategy_eligible": bool,
        "strategy_reason": str | None,
        "evaluated_at": str,
        "gates": [{name, category, applied, passed, observed_value,
                   threshold_value, reason}, ...],
        "signal": "BUY" | "SELL" | "HOLD",
        "signal_strength": "STRONG" | "MEDIUM" | "WEAK" | "AI_ENHANCED" | "CONFLICTED",
    },
    "scoring": {
        "components": {rsi_score, sma_score, macd_score, bb_score,
                       catalyst_score, regime_score},
        "volatility_tier", "volatility_multiplier_rsi", "volatility_multiplier_sma",
        "insider_boost_applied", "insider_score", "blended_signed",
        "total_score", "score_invalid_data",
    },
    "ranking": {
        "applicable": bool,
        "ranked_candidate": bool,
        "eligible_candidate_count": int | None,
        "candidate_rank": int | None,
        "tiebreak_basis": "symbol ASC",
        "total_score": float | None,
        "eligible_count_at_l3": int | None,
    },
    "selection": {
        "attempted": bool,
        "selection_attempted_at": str | None,
        "slots_available_at_attempt": int | None,
    },
    "execution_checks": {
        "current_state_at_attempt": {...},
        "checks": [{name, applied, passed, observed_value, threshold_value,
                    reason, _gap_note}, ...],
        "first_blocking_check": str | None,
        "first_blocking_reason": str | None,
        "evaluated_in_order": [...9 checks...],
    },
    "order": {
        "submitted": bool,
        "alpaca_order_id": str | None,
        "status_known": "SUBMITTED" | None,
        "submitted_at": str | None,
        "no_order_reason": str | None,
        "fill_confirmed": False,           # always False in Phase A
        "fill_confirmation_method": None,
        "fill_price": None,
        "fill_quantity": None,
        "fill_timestamp": None,
        "slot_consumed": bool,             # v1 semantics
        "slot_consumed_semantics_version": 1,
    },
    "decision": {
        "outcome": <one of CURRENTLY_REACHABLE>,
        "primary_reason": str,
        "decision_evaluated_at": str,
    },
    "baseline_diagnostics": {             # OBSERVED-ONLY (labeled)
        "_label": "OBSERVED-ONLY: for future pre-rank analysis. "
                  "Phase A does NOT use this as a trading gate.",
        "captured_at_cycle_start": str,
        "baseline_state_id": str,
        "cash": float | None,
        "pending_orders": [...],
        "confirmed_positions": {...},
        "sector_allocation_pct": {...},
        "portfolio_beta": float | None,
        "trade_cooldowns_active": {...},
        "internal_bot_bookkeeping": {...},
        "potential_l2_blockers_for_this_symbol": [
            # OBSERVED-ONLY; never affects ranking or execution
            {check_name, would_block, observed_value, threshold_value}
        ],
    },
}
```

## Tests (35 tests, all PASS)

`tests/test_obs_001_phase_a_decision_snapshot.py`:

### TestSchemaConstants (6 tests)
- `test_obs_001_schema_version_is_one`
- `test_currently_reachable_outcomes_are_documented`
- `test_reserved_outcomes_are_documented`
- `test_reserved_outcomes_excluded_from_currently_reachable`
- `test_slot_semantics_version_is_one`
- `test_execution_check_order_matches_current_execute_trade`

### TestSchemaMigration (3 tests)
- `test_decision_history_unique_constraint_enforced`
- `test_cycle_funnel_unique_cycle_id_enforced`
- `test_analyzed_stocks_columns_present`

### TestSnapshotBuilder (16 tests)
- `test_snapshot_schema_version_is_one`
- `test_snapshot_has_all_required_blocks`
- `test_strategy_eligibility_holds_for_strong_buy`
- `test_strategy_ineligible_hold_has_reason`
- `test_outcome_buy_order_submitted_when_submitted_true`
- `test_outcome_sell_order_submitted_when_submitted_true`
- `test_outcome_buy_blocked_dynamic_when_first_block_set`
- `test_outcome_buy_order_failed_when_no_first_block`
- `test_outcome_buy_eligible_not_selected`
- `test_outcome_hold_ineligible`
- `test_outcome_sell_blocked_no_position`
- `test_fill_confirmed_is_always_false`
- `test_slot_consumed_aligned_with_submit_order_return`
- `test_reserved_outcomes_never_appear_in_decision_outcome`
- `test_ranking_block_reflects_candidate_rank`
- `test_baseline_diagnostics_labeled_observed_only`
- `test_execution_check_order_listed_in_snapshot`
- `test_gap_notes_present_for_checks_that_ignore_pending_exposure`
- `test_snapshot_json_serializable`

### TestCycleFunnelInvariants (2 tests)
- `test_funnel_counters_match_actual_control_flow`
- `test_not_attempted_reason_is_known_string`

### TestNoPreRankGate (3 tests)
- `test_no_pre_rank_helper_introduced_in_phase_a`
- `test_no_pre_rank_actionable_in_funnel`
- `test_ranking_input_order_does_not_change_ranking`

### TestLegacyRowSentinel (1 test)
- `test_legacy_marker_constant`

### TestDecisionHistoryInsertOnly (1 test)
- `test_decision_history_persists_snapshot`

### TestControlFlowPreservation (3 tests)
- `test_sell_inline_execution_unchanged`
- `test_buy_ranked_walk_unchanged`
- `test_no_pre_rank_filter_in_run_analysis`

## Full Test Suite

```
TESTING=1 UNIT_TESTING=1 ./.venv/bin/python -m pytest tests/ -q
1222 passed, 2 failed in 88.79s (0:01:28)
```

Pre-existing failures (unchanged):
1. `test_template_renders_red_dot_when_alpaca_unreachable` (BOT-001)
2. `test_main_py_installs_sigterm_handler` (BOT-002)

## Dashboard Endpoints Added

- `GET /api/decision/{symbol}` — full snapshot detail; legacy
  sentinel for `decision_snapshot IS NULL` rows.
- `GET /api/decision-history/{symbol}` — cycle-over-cycle history.
- `GET /api/actionability-summary` — latest cycle_funnel row.

## Dashboard Endpoints Modified

- `GET /api/opportunities` — reads `decision_snapshot` when
  present; preserves legacy score-derivation pathway for rows
  without a snapshot.

## Dashboard Templates Modified

- `templates/dashboard.html` opportunities table renders the
  canonical decision outcome (color-coded: green for SUBMITTED,
  orange for BLOCKED_DYNAMIC, red for FAILED, neutral for
  HOLD/LEGACY/SKIPPED) and the candidate rank `#N/M`.

## Runtime Safety

- Bot PID 702290 unchanged; running process holds pre-OBS-001
  code in memory.
- New columns are nullable; live bot unaffected.
- Migrations are idempotent.
- `decision_history` and `cycle_funnel` INSERT-only by SQL UNIQUE
  constraint + code policy.
- No live-trading, SELL, sizing, risk, brokerage, systemd,
  Cloudflare, or dashboard-infrastructure changes.
- No new trading gates; ranking/selection behavior unchanged.
- SmartBot paper-only guard (`trading_bot_paper_only_guard()`)
  intact.

## STOP

Awaiting Josh's review and merge of branch
`agent/obs-001-phase-a-decision-snapshot` (HEAD on branch).
Do NOT auto-merge. Do NOT push without Josh's explicit approval.
After merge, restart SmartBot at a Josh-approved time to load
the new code into the running process.
