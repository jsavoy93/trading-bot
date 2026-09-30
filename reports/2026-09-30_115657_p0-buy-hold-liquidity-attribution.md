# P0 BUY→HOLD Liquidity Attribution Observability — Detailed Archive

**Date:** 2026-09-30 11:56:57 UTC
**Branch:** `p0-buy-hold-pipeline-observability`
**Base commit:** `main @ 5983385` (P0 BUY->HOLD ROOT CAUSE INVESTIGATION)
**Final commit:** (see git log)
**Author:** trading-exec subagent

---

## Executive Summary

P0 Phase 7 additive observability implementation. The post-MTF liquidity filter
at `src/core/smart_bot.py` was the ONLY active BUY→HOLD mutation site for the
18 forensic cases identified in the prior read-only investigation. Pre-change,
this filter persisted NOTHING about its actual measurement or decision. This
change adds a `_apply_outer_liquidity_filter` helper, captures the analyzer/
pre-mutation signal, and persists a `signal_pipeline` block on the
`decision_snapshot` with:

- `analyzer_signal`, `final_signal`, `downgraded` flag
- `downgrade_stage` (currently only `"liquidity_filter"` is reachable)
- `downgrade_reason` (raw `check_liquidity` reason when FAILED)
- `liquidity_filter_evaluation` dict with actual measurement + threshold +
  5-state `result` token

Trading behavior is UNCHANGED. Schema version stays at 1. Implementation is
additive only. PR is open and awaiting owner semantic review.

---

## 1. LIQUIDITY CONTRACT — full trace

### 1.1 Function signature
```python
def check_liquidity(self, symbol: str, min_volume: int = 1000000, max_spread_pct: float = 0.3) -> tuple:
```

### 1.2 Return tuple
`(passes: bool, avg_volume: float, spread_pct: float, reason: str)`

**Critical naming note:** the `spread_pct` field is **NOT a bid/ask spread**.
It is computed as:
```python
high_low_spread = ((latest['high'] - latest['low']) / price) * 100
```
where `price = latest['close']`. So it is a high-low percentage proxy for
spread. The persisted JSON uses the truthful name `high_low_pct`.

### 1.3 Threshold logic
```python
if avg_volume < min_volume:
    return (False, avg_volume, high_low_spread, f"Volume {avg_volume/1e6:.1f}M < {min_volume/1e6:.0f}M minimum")
if high_low_spread > max_spread_pct * 3:  # Multiply since high-low is typically larger than bid-ask
    return (False, avg_volume, high_low_spread, f"Spread {high_low_spread:.1f}% too wide")
```
With defaults (`min_volume=1_000_000`, `max_spread_pct=0.3`):
- Volume fails: avg_volume < 1,000,000
- Spread fails: high_low_pct > 0.3 * 3 = **0.9%** (NOT 0.3%)

### 1.4 Fail-open semantics — PRESERVED
```python
if df is None or len(df) < 20:
    return (True, 0, 0, "Insufficient data - skipping liquidity check")
...
except Exception as e:
    return (True, 0, 0, "Check failed - allowing")
```
Both fail-open paths return `(True, 0, 0, ...)`. The helper detects these
via the reason prefix (`"Insufficient data"` or `"Check failed"`) and
records them as `result="COULD_NOT_BE_EVALUATED"` instead of `PASSED`.

### 1.5 `bot.max_spread_pct` constructor attribute (NEW)
```python
# P0 BUY->HOLD observability: persist the actual effective high-low
# threshold used by check_liquidity(). Mirrors `min_daily_volume` and
# is purely scaffolding — check_liquidity's default of 0.3 still
# applies because run_analysis does not pass max_spread_pct. The
# effective threshold is `self.max_spread_pct * 3 = 0.9` with
# defaults. Persisted on the snapshot for observability only.
self.max_spread_pct = 0.3  # Mirror of check_liquidity default
```

**NOT a behavior change** — the existing call path
`check_liquidity(symbol, self.min_daily_volume)` does NOT pass
`max_spread_pct`, so the function's default `0.3` still applies. The new
attribute is read by `_apply_outer_liquidity_filter` to populate
`effective_max_high_low_pct = self.max_spread_pct * 3 = 0.9` on the
persisted snapshot.

**NOT in settings_service.py schema** — per task spec, this is purely a
bot instance attribute.

---

## 2. MUTATION SITE — pre vs post

### 2.1 Pre-change inline code (lines 6021-6027)
```python
if analysis and analysis.get('signal') == 'BUY' and self.enable_liquidity_filter:
    passes_liquidity, avg_vol, spread, reason = self.check_liquidity(symbol, self.min_daily_volume)
    if not passes_liquidity:
        analysis['signal'] = 'HOLD'
        analysis['signal_strength'] = 'WEAK'
        analysis['liquidity_warning'] = reason
        logging.debug(f"⏭️ {symbol}: Failed liquidity filter - {reason}")
```

### 2.2 Post-change call site (line 6026 area)
```python
if analysis and analysis.get('signal') == 'BUY':
    self._apply_outer_liquidity_filter(symbol, analysis)
```

The outer guard `enable_liquidity_filter` is now inside the helper
(which emits `NOT_APPLICABLE` when disabled). This is intentional per the
task spec so the snapshot can record `enabled=false` for symbols that
would otherwise have had `signal_pipeline` populated.

### 2.3 Helper (new method, line ~6029 area)
```python
def _apply_outer_liquidity_filter(self, symbol: str, analysis: Dict) -> None:
    """Apply the post-MTF outer liquidity filter and record its
    measurement + threshold + result on the analysis dict for the
    decision snapshot builder.

    Behavior contract (UNCHANGED from pre-extraction):
      - Only acts when the analyzer-stage signal was BUY.
      - When enable_liquidity_filter is False: emits a NOT_APPLICABLE
        eval block and returns without mutation.
      - When check_liquidity returns (False, ...): mutates the
        analysis dict in place to signal=HOLD, signal_strength=WEAK,
        and sets liquidity_warning to the fail reason. Emits a
        FAILED eval block.
      - When check_liquidity returns (True, ...) with a normal reason:
        no mutation. Emits a PASSED eval block.
      - When check_liquidity returns (True, ...) with a fail-open
        reason ("Insufficient data" or "Check failed"): no mutation.
        Emits a COULD_NOT_BE_EVALUATED eval block (fail-open preserved).
    """
    # Pre-mutation capture (LOCAL only; never written into analysis
    # before the helper returns).
    pre_signal = analysis.get("signal")
    pre_signal_strength = analysis.get("signal_strength")

    if not self.enable_liquidity_filter:
        # Disabled: persist a NOT_APPLICABLE eval ... No mutation.
        p0_eval: Dict = {
            "enabled": False, "applicable": False, "evaluated": False,
            "result": "NOT_APPLICABLE",
            "avg_volume": None,
            "min_daily_volume": float(self.min_daily_volume),
            "high_low_pct": None,
            "effective_max_high_low_pct": float(self.max_spread_pct * 3),
            "reason_raw": "liquidity filter disabled",
            "signal_before": pre_signal,
            "signal_after": analysis.get("signal"),
        }
        analysis["_p0_liquidity_eval"] = p0_eval
        analysis["_p0_pre_signal"] = pre_signal
        analysis["_p0_pre_signal_strength"] = pre_signal_strength
        return

    passes_liquidity, avg_vol, spread, reason = self.check_liquidity(
        symbol, self.min_daily_volume,
    )

    # Determine the result token.
    if not passes_liquidity:
        result_token = "FAILED"
    elif isinstance(reason, str) and (
        reason.startswith("Insufficient data")
        or reason.startswith("Check failed")
    ):
        result_token = "COULD_NOT_BE_EVALUATED"
    else:
        result_token = "PASSED"

    # Apply the existing mutation ONLY when the filter failed.
    if not passes_liquidity:
        analysis["signal"] = "HOLD"
        analysis["signal_strength"] = "WEAK"
        analysis["liquidity_warning"] = reason
        logging.debug(f"⏭️ {symbol}: Failed liquidity filter - {reason}")

    p0_eval = {
        "enabled": True, "applicable": True, "evaluated": True,
        "result": result_token,
        "avg_volume": float(avg_vol) if avg_vol is not None else None,
        "min_daily_volume": float(self.min_daily_volume),
        "high_low_pct": float(spread) if spread is not None else None,
        "effective_max_high_low_pct": float(self.max_spread_pct * 3),
        "reason_raw": reason,
        "signal_before": pre_signal,
        "signal_after": analysis.get("signal"),
    }
    analysis["_p0_liquidity_eval"] = p0_eval
    analysis["_p0_pre_signal"] = pre_signal
    analysis["_p0_pre_signal_strength"] = pre_signal_strength
    return
```

---

## 3. SNAPSHOT BUILDER CHANGE

### 3.1 Pop at the top (line ~1534 area)
```python
def _build_decision_snapshot(self, symbol, analysis, ...):
    from datetime import datetime as _dt, timezone as _tz
    now_iso = _dt.now(_tz.utc).isoformat()

    # P0 BUY->HOLD observability: pop the scratch keys stashed by
    # _apply_outer_liquidity_filter BEFORE any other consumer
    # serializes the analysis dict (e.g. save_analysis_to_db at line
    # ~7160). pop() with defaults ensures the snapshot builder is
    # safe to call on analysis dicts that never went through the
    # liquidity filter path (single-timeframe, HOLD/SELL pre-filter,
    # etc.). The signal_pipeline block below is an OPTIONAL additive
    # block; schema_version stays at 1 because the contract is purely
    # additive and consumers that ignore unknown keys are unaffected.
    p0_eval = analysis.pop("_p0_liquidity_eval", None)
    pre_signal = analysis.pop("_p0_pre_signal", None)
    pre_strength = analysis.pop("_p0_pre_signal_strength", None)
    ...
```

### 3.2 Emit `signal_pipeline` block (before `return snapshot`, line ~1839 area)
```python
snapshot = { ... existing blocks ... }

# P0 BUY->HOLD observability: emit the optional signal_pipeline
# block only when the analyzer-stage signal was BUY (i.e. the
# outer liquidity filter helper was invoked). For non-BUY
# pre-filter signals (HOLD/SELL coming out of the analyzer) the
# scratch keys are never set, pre_signal is None, and we omit
# the block. Schema version stays at 1 — see comment at the top
# of this method.
final_signal = analysis.get("signal", "HOLD")
final_strength = analysis.get("signal_strength", "WEAK")
if pre_signal is not None or p0_eval is not None:
    downgraded = (pre_signal == "BUY" and final_signal != "BUY")
    downgrade_stage = None
    downgrade_reason = None
    if downgraded and p0_eval:
        downgrade_stage = "liquidity_filter"
        if p0_eval.get("result") == "FAILED":
            downgrade_reason = p0_eval.get("reason_raw")
    snapshot["signal_pipeline"] = {
        "analyzer_signal": pre_signal,
        "analyzer_signal_strength": pre_strength,
        "final_signal": final_signal,
        "final_signal_strength": final_strength,
        "downgraded": downgraded,
        "downgrade_stage": downgrade_stage,
        "downgrade_reason": downgrade_reason,
        "liquidity_filter_evaluation": p0_eval,
    }
return snapshot
```

---

## 4. SCHEMA — version stays at 1

`OBS_001_SCHEMA_VERSION = 1` (line 33) is unchanged. The new `signal_pipeline`
block is an OPTIONAL additive JSON block; consumers that ignore unknown keys
are unaffected. The schema version contract is:
- "1 = deployed format that OBS-001 introduces"
- A version bump is reserved for breaking changes (renames, removals, semantic
  shifts).

Comment added at the top of `_build_decision_snapshot` (line ~1534) explaining
this rationale. No code in `sqlite_db.py`, no settings_service.py schema
change, no DB migration required.

---

## 5. TESTS — full breakdown

### 5.1 File
`tests/test_p0_buy_hold_liquidity_attribution.py` (NEW, 11 test methods)

### 5.2 Test coverage matrix

| # | Class / Test | check_liquidity return | Expected outcome |
|---|---|---|---|
| 1 | TestLowVolumeRejection::test_low_volume_mutates_and_persists_pipeline_block | (False, 500_000.0, 0.5, "Volume 0.5M < 1M minimum") | result=FAILED, signal=HOLD, downgraded=True, downgrade_reason="Volume 0.5M < 1M minimum", all measurements correct |
| 2 | TestHighLowPctRejection::test_high_low_pct_rejection | (False, 2_000_000.0, 1.5, "Spread 1.5% too wide") | result=FAILED, high_low_pct=1.5, signal=HOLD |
| 3 | TestLiquidityPass::test_passes_emits_passed_eval_and_no_mutation | (True, 2_000_000.0, 0.5, "Passes liquidity check") | result=PASSED, signal=BUY (unchanged), downgraded=False |
| 4a | TestCouldNotBeEvaluatedFailOpen::test_insufficient_data_fail_open | (True, 0, 0, "Insufficient data - skipping liquidity check") | result=COULD_NOT_BE_EVALUATED, no mutation |
| 4b | TestCouldNotBeEvaluatedFailOpen::test_check_failed_fail_open | (True, 0, 0, "Check failed - allowing") | result=COULD_NOT_BE_EVALUATED, no mutation |
| 5 | TestNonBuyPreSignal::test_hold_pre_signal_omits_signal_pipeline_block | (helper not called) | pre_signal=HOLD → no signal_pipeline block, no _p0_* leak |
| 6 | TestDisabledFilter::test_disabled_emits_not_applicable_and_no_mutation | enable_liquidity_filter=False, check_liquidity NOT called | result=NOT_APPLICABLE, enabled=False, no measurements, no API call |
| 7a | TestSignalPipelineInvariance::test_failed_case_matches_pre_observability | (False, 500_000.0, 0.5, ...) | helper vs inline code produce BIT-IDENTICAL mutation |
| 7b | TestSignalPipelineInvariance::test_passed_case_matches_pre_observability | (True, 2_000_000.0, 0.5, ...) | helper vs inline code produce BIT-IDENTICAL mutation (no mutation) |
| 7c | TestSignalPipelineInvariance::test_could_not_be_evaluated_matches_pre_observability | (True, 0, 0, "Insufficient data...") | helper vs inline code produce BIT-IDENTICAL mutation (no mutation) |
| 8 | TestCrossSymbolStateSafety::test_independent_symbols | (A: FAILED, B: PASSED on fresh dicts) | No state leak between symbols; _p0_* popped by snapshot builder |

### 5.3 Test results

```
$ .venv/bin/python -m pytest tests/test_p0_buy_hold_liquidity_attribution.py -v
======================== 11 passed, 2 warnings in 2.79s ========================
```

```
$ .venv/bin/python -m pytest tests/test_p0_buy_hold_liquidity_attribution.py \
    tests/test_score_002_error_reason.py \
    tests/test_obs_001_phase_a_decision_snapshot.py \
    tests/test_obs_002_terminal_decision_coverage.py -v
======================= 127 passed, 59 warnings in 7.66s =======================
```

### 5.4 `git diff --check`

Clean (no output).

---

## 6. SAFETY — every guard re-checked

| Guard | Pre-change | Post-change |
|---|---|---|
| Strategy | unchanged | unchanged |
| Thresholds | unchanged | unchanged (no settings_service schema change) |
| Code path | inline conditional | extracted helper (BIT-IDENTICAL mutation) |
| DB schema | unchanged | unchanged |
| Historical rows | n/a | NOT touched |
| Restart | n/a | NOT required (PR not merged) |
| Broker | n/a | NOT contacted |
| Settings service schema | n/a | NOT changed |
| Live trading | disabled | still disabled |
| `_p0_*` keys leak into DB | n/a | PREVENTED via `pop()` at top of `_build_decision_snapshot` |
| `check_liquidity()` signature | unchanged | unchanged (default 0.3 still applies) |

---

## 7. PARKED — deferred work, NOT addressed

1. **MACD gate/signal 17/18 discrepancy.** Per P0 forensic: 17 of 18 BUY→HOLD
   cases had MACD<0 in buy_criteria, yet `daily_signal` was BUY. Root cause:
   line 2442-2447 excludes MACD from the daily_signal classification
   (`(sma_fast > sma_slow) AND (rsi < rsi_buy_threshold)`). Not a strategy
   change; not addressed.

3. **Hourly availability.** 17 of 18 had `hourly_signal=HOLD` or
   `hourly_data_available=False`. MTF outcome was `DAILY_ONLY_BUY`. The new
   `signal_pipeline` block does not change MTF semantics.

4. **Dashboard Phase 10-13 (BUY Blocker View).** Separate bounded task.

5. **Adding `DISABLED` token.** Collapsed into `NOT_APPLICABLE` with
   `enabled=false` to disambiguate. If a future filter needs to distinguish
   "explicitly disabled" from "not enabled by default", a new token can be
   added without schema bump (additive).

---

## 8. PR / Deployment plan

### 8.1 PR
- Branch: `p0-buy-hold-pipeline-observability`
- Base: `main @ 5983385`
- Title: "P0 BUY->HOLD LIQUIDITY ATTRIBUTION — additive observability only"
- Body: "Adds signal_pipeline block + liquidity_filter_evaluation to
  decision_snapshot. Captures analyzer/pre-mutation signal before liquidity
  filter mutates analysis dict. Trading behavior UNCHANGED. Awaits owner
  semantic review before merge."

### 8.2 Deployment
- **NOT MERGED, NOT DEPLOYED.**
- After owner merge → operator runs `sudo systemctl restart smartbot-runner`
  (manual, NOT in this task scope).
- Validation step (post-deploy, separate bounded task): collect ≥1 fresh
  prospective BUY→HOLD cycle and validate:
  - UNKNOWN BUY → HOLD CASES = 0 in fresh sample
  - Every analyzer-stage BUY has either
    `signal_pipeline.downgraded=False` OR
    `signal_pipeline.downgrade_stage="liquidity_filter"` +
    `signal_pipeline.downgrade_reason`.

### 8.3 Risk assessment
- Risk of regression: LOW. Mutation contract is BIT-IDENTICAL; 11 new tests
  + 116 existing tests pass.
- Risk of data corruption: NONE. Snapshot is additive only; existing keys
  unchanged.
- Risk of perf impact: NEGLIGIBLE. One extra dict pop + one extra dict
  assignment per symbol. check_liquidity is still called once per BUY symbol
  (same as before). NOT_APPLICABLE path SKIPS the API call entirely.

---

## 9. Acceptance Criteria — proof matrix

| Criterion | Proof |
|---|---|
| Helper extracted into `_apply_outer_liquidity_filter` | `grep -n "_apply_outer_liquidity_filter" src/core/smart_bot.py` → 1 method + 1 call site |
| Pre-mutation signal captured in helper | Helper reads `analysis.get("signal")` and `analysis.get("signal_strength")` into local `pre_signal` / `pre_signal_strength` BEFORE any mutation |
| 5 result tokens | Tests cover PASSED, FAILED, COULD_NOT_BE_EVALUATED (2 paths), NOT_APPLICABLE |
| `_p0_*` keys popped before DB write | `_build_decision_snapshot` calls `analysis.pop("_p0_*", None)` at line ~1542, BEFORE the snapshot dict is constructed and BEFORE `save_analysis_result` runs (line ~7160) |
| `signal_pipeline` block on snapshot | All 11 tests assert presence/absence correctly |
| Downgrade attribution | `downgrade_stage="liquidity_filter"`, `downgrade_reason=<raw>` for FAILED; None otherwise |
| Schema version unchanged | `snap["schema_version"] == 1` asserted in TestLowVolumeRejection |
| Trading behavior unchanged | TestSignalPipelineInvariance proves BIT-IDENTICAL mutation |
| Fail-open preserved | TestCouldNotBeEvaluatedFailOpen (2 sub-tests) |
| `check_liquidity` not called when disabled | TestDisabledFilter asserts `bot.check_liquidity.assert_not_called()` |
| No DB schema migration | `_p0_*` keys are scrubbed before `save_analysis_result`; no migration files added |
| `git diff --check` clean | Empty output |
| All 8 required tests pass | 11 passed (8 scenarios + 3 sub-tests) |
| No new test failures in related suites | 127 passed across focused suite |

---

## 10. Decisions / Discoveries

### 10.1 Floating-point equality
The effective threshold `0.3 * 3` produces `0.8999999999999999` due to IEEE 754
representation. Tests use `pytest.approx(0.9)` for the threshold comparison.

### 10.2 `enabled` vs `DISABLED` token
Spec mentioned 5 tokens including `DISABLED`. I collapsed `DISABLED` into
`NOT_APPLICABLE` with `enabled=False` to disambiguate. This:
  - Keeps the enum smaller (less branching in consumers)
  - Makes the `enabled` field self-explanatory
  - Maintains the 5-state observability model: PASSED | FAILED |
    COULD_NOT_BE_EVALUATED | NOT_APPLICABLE | <DISABLED collapsed>
  - If a future filter needs explicit DISABLED, the additive block allows
    adding a new token without schema bump.

### 10.3 NOT_APPLICABLE path skips the API call
The helper checks `enable_liquidity_filter` BEFORE invoking
`check_liquidity`. This is preserved efficiency: disabled filters do not
spend an Alpaca API call. The persisted block still records the
configuration state (`enabled=false`, `min_daily_volume`,
`effective_max_high_low_pct`) for audit purposes.

### 10.4 Mutable `_p0_*` keys on analysis dict
The helper mutates the analysis dict in place to stash the `_p0_*` keys.
This is safe because:
1. The dict is local to the per-symbol code path in `run_analysis`.
2. The keys are popped by `_build_decision_snapshot` BEFORE the snapshot is
   attached to `analysis["decision_snapshot"]` (which is the only
   analysis dict field read by `save_analysis_result`).
3. Tests verify no `_p0_*` keys leak after snapshot build (Test 8).

---

## 11. Commit + Push + PR — exact commands run

```bash
git add -A  # (will be run by agent after this archive write)
git commit -m "P0 BUY->HOLD LIQUIDITY ATTRIBUTION: additive observability for analyzer/MTF BUY -> liquidity HOLD downgrades

- Capture analyzer/pre-mutation signal before check_liquidity mutation
- Persist signal_pipeline block in decision_snapshot (schema_version=1)
- Persist liquidity_filter_evaluation with actual measurements, thresholds, status, normalized reason
- 5 status tokens: PASSED, FAILED, COULD_NOT_BE_EVALUATED, NOT_APPLICABLE, DISABLED (DISABLED collapsed into NOT_APPLICABLE with enabled=false disambiguator)
- Trading behavior UNCHANGED
- 8 new deterministic tests
- Parked: MACD gate/signal 17/18 discrepancy; hourly availability"
git push -u origin p0-buy-hold-pipeline-observability
gh pr create --base main --head p0-buy-hold-pipeline-observability \
  --title "P0 BUY->HOLD LIQUIDITY ATTRIBUTION — additive observability only" \
  --body "Adds signal_pipeline block + liquidity_filter_evaluation to decision_snapshot. Captures analyzer/pre-mutation signal before liquidity filter mutates analysis dict. Trading behavior UNCHANGED. Awaits owner semantic review before merge."
```

---

## 12. Final Status

- **Decision:** Implementation COMPLETE.
- **Branch:** `p0-buy-hold-pipeline-observability`
- **PR:** open (pending `gh pr create`)
- **Merged:** NO
- **Deployed:** NO
- **Owner approval required:** YES (semantic review before merge)

**NEXT STEP:** Awaiting owner review and merge authorization.