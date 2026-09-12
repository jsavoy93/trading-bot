# OBS-001 Phase A — Corrigendum Archive

- Task: OBS-001 Phase A — Decision Trace + Dashboard Observability (corrected per Josh 2026-09-12 00:55 UTC)
- Owner: trading-manager
- Branch: `agent/obs-001-phase-a-decision-snapshot`
- Status: corrigendum complete; awaiting Josh review and merge
- Mode: implementation reporting
- Prerequisite: OBS-001 Phase A first-pass implementation (commit 495784f)
- Runtime: PID 702290 unchanged; running bot holds pre-OBS-001 code

## Three corrections (Josh 2026-09-12 00:55 UTC)

### Fix #1 — Legacy dashboard fidelity

`api_opportunities` previously re-derived signal/strength from total_score
thresholds for legacy rows (`decision_snapshot IS NULL`). This violated
the approved OBS-001 design — re-deriving historical signal/strength
using current thresholds rewrites the meaning of an old analysis.

**Required behavior (now implemented)**:
- OBS-001 snapshot row: decision/signal/strength come from persisted
  `decision_snapshot`.
- Legacy row: signal comes from `analyzed_stocks.signal`;
  signal_strength comes from `analyzed_stocks.signal_strength`;
  rank/detail unavailable; display "Legacy analysis — detailed
  decision trace unavailable".

**Static analysis test**: `test_dashboard_legacy_branch_does_not_derive_from_score`
verifies that the source of `api_opportunities` contains:
- `row['signal']` and `row['signal_strength']` reads
- NO `score >= 65`, `score <= 35`, `score >= 80`, `score <= 20`
  comparisons in the legacy branch

### Fix #2 — Trace collected by REAL execute_trade

The previous implementation ran the checks TWICE — once for the actual
trade decision, and once for the observation. The second run could
observe different pending orders, cooldown state, account/cash,
positions, sector exposure, correlation, beta, broker state.

**Required behavior (now implemented)**:
The real `execute_trade` populates the trace passively. Each existing
check calls `_obs_001_trace_record(name, applied, passed, observed,
threshold, reason, gap)` at the EXACT location where the check
executes. The trace records the actual values used by the real code.

**Acceptable approaches (chosen: side-channel trace collector)**:
- Module-level trace collector: `_obs_001_active_trace`
- `execute_trade` calls `_obs_001_trace_record` adjacent to each check
- Checks NOT reached by the real code (due to short-circuit) are
  recorded as `applied=False`, `passed=None`, with reason
  "NOT RUN: real execute_trade short-circuited before reaching this check"
- broker/API reads are not duplicated

**Forbidden actions (avoided)**:
- DO NOT duplicate the checks ✓
- DO NOT reorder checks ✓
- DO NOT change short-circuit behavior ✓
- DO NOT add or remove checks ✓
- DO NOT change values used by the actual decision ✓

**Tests prove**:
- `test_no_duplicate_check_calls_for_observation`: `get_account()` is
  called exactly once per execute_trade (same as pre-OBS-001)
- `test_traced_values_match_actual_decision`: trace's
  `observed_value` for margin_check equals the exact cash value the
  real code observed (7500.50 in test)
- `test_execute_trade_short_circuit_records_correct_first_blocker`:
  when margin_check fails, all later checks are recorded as NOT RUN,
  and `first_blocking_check == "margin_check"` (the EXACT check that
  caused the real execute_trade to return)
- `test_execute_trade_returns_false_when_signal_unsupported_and_no_trace`:
  when signal is unsupported, only margin_check is recorded (applied
  + failed); the other 8 checks are NOT RUN
- `test_trace_no_checks_recorded_outside_attempt`: `_obs_001_trace_record`
  is a no-op outside an active attempt

### Fix #3 — Per-symbol finalize-on-decision

Previously all snapshot persistence was batched at end of run_analysis.
A crash mid-cycle would lose every decision made that cycle.

**Required behavior (now implemented)**:
- `analyzed_stocks.decision_snapshot` may be updated as latest-state
  storage (multiple writes OK)
- `decision_history`: exactly ONE immutable finalized row per
  `cycle_id + symbol` (UNIQUE constraint enforces it)
- The decision_history row is written when that symbol reaches its
  terminal outcome, NOT at cycle end

**Where _persist_obs_001_decision_snapshot is called**:
- After each SELL inline `execute_trade` call (BUY/SELL submitted or blocked)
- After each BUY ranked-walk `execute_trade` call
- After each slots_filled SELL/BUY skip
- After each HOLD outcome (in the `elif analysis:` branch)

**Idempotency**: the UNIQUE(cycle_id, symbol) constraint on
`decision_history` makes double-persist a no-op (the second call raises
an IntegrityError that is caught and logged).

**Tests prove**:
- `test_double_persist_idempotent`: 2 calls → 1 row
- `test_finalize_writes_upsert_analyzed_stocks`: per-symbol finalize
  upserts the latest snapshot into analyzed_stocks
- `test_finalize_decision_outcome_canonical_enum`: for ALL combinations
  of input signals, the persisted outcome is always in
  `OBS_001_OUTCOMES_CURRENTLY_REACHABLE` and never in
  `OBS_001_OUTCOMES_RESERVED`

## Trace collector architecture (precise)

```python
# Module-level (smart_bot.py)
_obs_001_active_trace: Optional[List[Dict]] = None
_obs_001_active_attempt_symbol: Optional[str] = None
_obs_001_active_attempt_signal: Optional[str] = None
_obs_001_active_attempt_returned: Optional[bool] = None

def _obs_001_begin_attempt(symbol: str, signal: str) -> None:
    """Open a fresh OBS-001 trace for one execute_trade call."""
    global _obs_001_active_trace, _obs_001_active_attempt_symbol
    global _obs_001_active_attempt_signal, _obs_001_active_attempt_returned
    _obs_001_active_trace = []
    _obs_001_active_attempt_symbol = symbol
    _obs_001_active_attempt_signal = signal
    _obs_001_active_attempt_returned = None

def _obs_001_trace_record(name, *, applied, passed, observed_value=None,
                          threshold_value=None, reason=None, gap_note=None):
    """Record a check at the EXACT point where the real execute_trade
    code performed it. This function is called by execute_trade; it
    does NOT perform any check itself."""
    global _obs_001_active_trace
    if _obs_001_active_trace is None:
        return
    _obs_001_active_trace.append({...})

def _obs_001_finalize_attempt(returned: bool) -> List[Dict]:
    """Close the active trace. Back-fills NOT RUN entries for checks
    the real code did not reach."""
    ...
```

## execute_trade recording points (precise)

Each existing check in `execute_trade` calls
`_obs_001_trace_record(...)` AT THE EXACT POINT where it executes:

1. `margin_check` — at L4634 (after `if cash < 0: return False`)
   and at L4654 (after `else: pass`)
2. `pending_order_check` — at L4693 (always; passed=not has_pending)
3. `cooldown_check` (BUY only) — at L4706-L4722 (in_cooldown/else)
4. `position_existence_check` (SELL only) — at L4733 (no position)
   and L4747 (has position)
5. `position_concentration_check` (BUY only) — at L4776/L4786/L4796
6. `sector_concentration_check` (BUY only) — at L4811/L4816
7. `correlation_check` (BUY only) — at L4822/L4827
8. `beta_check` (BUY only) — at L4833/L4838
9. `quantity_post_sizing_check` — at L4875/L4881 (implicit final check)

## Snapshot ordering invariants (run_analysis)

Per-symbol snapshot persistence order matches the real-time control flow:
1. `run_analysis` opens a fresh trace per symbol via
   `_obs_001_begin_attempt`
2. The real `execute_trade` is called unchanged
3. `_obs_001_finalize_attempt(execute_returned)` closes the trace
4. `_persist_obs_001_decision_snapshot(symbol, entry, cycle_id, ...)`
   is called IMMEDIATELY after each execute_trade/skipped/HOLD branch
5. The `decision_history` row is durable before the cycle continues

## Cycle funnel invariant (unchanged from corrigendum-2)

`order_submitted_count` is derived from the canonical outcome enum,
NOT from the boolean `execute_trade` return:
- `BUY_ORDER_SUBMITTED` / `SELL_ORDER_SUBMITTED` → order_submitted
- `BUY_ORDER_FAILED` / `SELL_ORDER_FAILED` → order_failed

## Tests run (full safe suite)

```
TESTING=1 UNIT_TESTING=1 ./.venv/bin/python -m pytest tests/ -q
1234 passed, 2 failed in 86.68s (0:01:26)
```

Pre-existing failures (unchanged):
1. `test_template_renders_red_dot_when_alpaca_unreachable` (BOT-001)
2. `test_main_py_installs_sigterm_handler` (BOT-002)

## Runtime safety

- SmartBot PID 702290 unchanged (1 day 13 minutes elapsed at archive)
- Paper-only ACTIVE; TRADING_BOT_PAPER_ONLY=1 preserved
- New schema columns nullable; running bot unaffected
- Migrations are idempotent (existing try/except pattern)
- `decision_history` and `cycle_funnel` INSERT-only by SQL UNIQUE
  constraint + code policy
- No live-trading, SELL, sizing, risk, brokerage, systemd,
  Cloudflare, or dashboard-infrastructure changes
- No new trading gates; ranking/selection behavior unchanged
- Zero broker/API duplicate reads for observation

## STOP

Awaiting Josh's review and merge of branch
`agent/obs-001-phase-a-decision-snapshot`. Do NOT auto-merge. Do NOT
restart SmartBot. After merge and Josh-approved restart, the running
bot will start writing decision snapshots.
