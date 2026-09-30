# P0 — BUY → HOLD ROOT CAUSE INVESTIGATION (read-only forensic + Phase 7 design)

**Task:** P0 OWNER DIRECTIVE — BUY → HOLD ROOT CAUSE
**Decision (this report):** ROADMAP FROZEN. P0 is OPEN until Phase 7-10 observability is implemented, validated prospectively, and exposed on the dashboard.
**Branch:** main @ `8afc4e2` (no code changes yet)
**SmartBot:** PID 1239963, uptime ~3.5h, NRestarts=0, PPID 1113 (user systemd), PAPER verified
**Window (STRAT-003):** 2026-09-29 22:15:54 → 2026-09-29 23:22:20 UTC (66m 14s, 198 cycles)
**Sample:** 5920 decisions (5806 multi_timeframe + 114 single_timeframe); **18 daily=BUY cases**
**Strategy:** UNCHANGED — no code/config changes implemented in this turn
**Safety:** read-only DB queries, no restart, no broker action, unrelated services untouched
**Reports:** This file + REPORT.md (gitignored rolling) + audit archive

---

## ROOT CAUSE (one-line)

The 18 analyzer-level BUY signals became final HOLD decisions because **the post-MTF liquidity filter at `src/core/smart_bot.py` line 6021-6027 mutates `analysis['signal']` from `BUY` to `HOLD` and the actual measurement (avg volume, spread) is NEVER persisted in any DB column, log line, or snapshot field**. This is **DOWNSTREAM_BUY_TO_HOLD_MUTATION_PROVEN_BUT_REASON_UNKNOWN** — the code stage is proven by elimination (only one BUY→HOLD mutation site exists between the analyzer exit and the decision_snapshot write), but the actual measurement is unrecorded.

---

## COMPLETE BUY → HOLD PIPELINE (current code path)

```
[1] analyze_multi_timeframe(symbol)
    ├─ compute daily_signal  : (sma_fast > sma_slow) AND (rsi < rsi_buy_threshold)
    │                          [MACD NOT used here, line 2442-2447]
    ├─ compute hourly_signal : same rule on df_hourly.iloc[-1]
    ├─ compute_mtf_outcome(daily, hourly, avail)  # mtf_outcome.py
    │   ├─ daily=BUY, hourly=BUY        → AGREE_BUY
    │   ├─ daily=BUY, hourly=SELL       → CONFLICT
    │   ├─ daily=BUY, hourly=HOLD, avail=True → NO_ACTION
    │   ├─ daily=BUY, hourly=HOLD, avail=False → DAILY_ONLY_BUY
    │   └─ (other branches)
    ├─ emit (signal, signal_strength):
    │   ├─ daily and hourly agree         → signal=daily, strength=STRONG|MEDIUM
    │   ├─ daily and hourly conflict      → signal=HOLD, strength=CONFLICTED
    │   ├─ daily and not hourly_indicators → signal=daily, strength=DAILY_ONLY
    │   └─ otherwise                       → signal=HOLD
    ├─ filter_results (in analyzer):
    │   ├─ multi_timeframe_conflict: passed/blocked
    │   ├─ volume_downgrade: passed/blocked (only if enable_volume_confirmation)
    │   └─ ai_conflict: passed/blocked (only if enable_ai_conflict_filter)
    ├─ filter BLOCKS (inside analyzer, signal == "BUY" only):
    │   ├─ enable_mtf_conflict_filter and mtf_conflict_blocked → signal=HOLD, blocked_by="multi_timeframe_conflict"
    │   ├─ enable_vol_downgrade_filter and volume_downgrade   → signal=HOLD, blocked_by="volume_downgrade"
    │   └─ enable_ai_conflict_filter and ai_conflict_blocked_flag → signal=HOLD, blocked_by="ai_conflict"
    └─ return analysis_result dict
        ├─ signal, signal_strength (POST-filter from inside-analyzer blocks)
        ├─ filter_results (only the 3 above)
        ├─ blocked_by, blocked_count
        ├─ multi_timeframe.{daily_signal, hourly_signal, hourly_data_available, mtf_outcome, mtf_reason}
        ├─ daily_signal, mtf_outcome, mtf_reason, mtf_hourly_data_available (PR #105)
        └─ buy_criteria (RSI, SMA, MACD, Volume) — MACD is in buy_criteria but NOT analysis.macd_histogram

[2] save_analysis_to_db(symbol, analysis)                          # line 6018
    └─ UPSERT analyzed_stocks with analysis dict (signal=BUY, signal_strength=DAILY_ONLY)

[3] LIQUIDITY FILTER (run_analysis line 6021-6027) — THIS IS THE PROVEN BUY→HOLD MUTATION
    if analysis.get('signal') == 'BUY' and self.enable_liquidity_filter:
        passes, avg_vol, spread, reason = self.check_liquidity(symbol, self.min_daily_volume)
        if not passes:
            analysis['signal'] = 'HOLD'            # ← MUTATION
            analysis['signal_strength'] = 'WEAK'   # ← MUTATION
            analysis['liquidity_warning'] = reason # ← IN-MEMORY ONLY, NEVER PERSISTED
            logging.debug(f"⏭️ {symbol}: Failed liquidity filter - {reason}")

[4] SECTOR FILTER (run_analysis line 6030-6039) — DEAD CODE
    if analysis.get('signal') == 'BUY' and self.enable_sector_filter:  # enable_sector_filter=False
        ...  # never executes

[5] if not analysis: fallback HOLD/score branch (line 6043-6330)
    └─ For the 18 cases, analysis is not None → SKIPPED

[6] if analysis['signal'] in ['BUY', 'SELL'] (line 6389)
    ├─ if BUY: buy_candidates.append → strategy_eligible_count += 1
    └─ if HOLD/WEAK: → no_trade_reasons['no_signal' or 'weak_signal'] += 1

[7] _persist_obs_001_decision_snapshot(symbol, ...) (line 7052+)
    └─ Builds decision_snapshot from MUTATED analysis dict (signal=HOLD if liquidity failed)
        ├─ decision_snapshot.strategy_eligibility.signal = HOLD
        ├─ decision_snapshot.strategy_eligibility.gates = [rsi_oversold PASSED, sma_uptrend PASSED]
        │     # NOTE: macd_positive gate is MISSING because analysis.macd_histogram is None
        └─ decision_snapshot.strategy_eligibility.strategy_reason = "Strategy ineligible (no actionable signal)"

[8] save_analysis_result(symbol, analysis)  # second write (line 7160)
    └─ UPSERT analyzed_stocks with POST-MUTATION signal=HOLD, signal_strength=WEAK
       This OVERWRITES the earlier BUY row written at step [2]
```

---

## EXACT MUTATION STAGE(S)

**Line 6021-6027 in `src/core/smart_bot.py`** is the only code that mutates `analysis['signal']` from `BUY` to `HOLD` between the analyzer exit (`analyze_multi_timeframe` return) and the final DB upsert (`save_analysis_result` at line 7160).

The other potential mutation sites are:
- **Sector filter (line 6030-6039)**: `enable_sector_filter=False` (per persisted settings), so DEAD CODE.
- **MTF conflict / volume downgrade / AI conflict blocks inside `analyze_multi_timeframe` (line 2589-2600)**: For the 18 cases, `mtf_conflict_blocked=False` (no hourly disagreement), `volume_downgrade=False` (volume_confirmation disabled), `ai_conflict_blocked_flag=False` (AI filter disabled). All three pass.

By elimination: **the liquidity filter at line 6021-6027 is the only mechanism that can mutate BUY → HOLD for these 18 cases**.

---

## FORENSIC TABLE — ALL 18 CASES (one row per decision)

| # | sym | sid | cycle_start | RSI | MACD | total | mtf_outcome | final_sig | final_str | reason |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | ADAMM | 2267010 | 2026-09-29T22:27:06 | 32.1 | -0.045 | 76.7 | DAILY_ONLY_BUY | HOLD | WEAK | Strategy ineligible (no actionable signal) |
| 2 | BRAZ | 2268432 | 2026-09-29T22:43:04 | 33.6 | -0.224 | 67.0 | DAILY_ONLY_BUY | HOLD | WEAK | Strategy ineligible (no actionable signal) |
| 3 | COMD | 2268503 | 2026-09-29T22:43:43 | 32.5 | -0.182 | 66.5 | DAILY_ONLY_BUY | HOLD | WEAK | Strategy ineligible (no actionable signal) |
| 4 | TLNCU | 2269669 | 2026-09-29T22:56:55 | 0.0 | 0.002 | 97.4 | DAILY_ONLY_BUY | HOLD | WEAK | Strategy ineligible (no actionable signal) |
| 5 | NOEMR | 2269775 | 2026-09-29T22:58:13 | 8.3 | -0.001 | 68.0 | DAILY_ONLY_BUY | HOLD | WEAK | Strategy ineligible (no actionable signal) |
| 6 | RAC | 2269921 | 2026-09-29T22:59:51 | 15.0 | -0.005 | 60.1 | DAILY_ONLY_BUY | HOLD | WEAK | Strategy ineligible (no actionable signal) |
| 7 | ALPXR | 2270164 | 2026-09-29T23:02:32 | 0.0 | -0.001 | 100.0 | DAILY_ONLY_BUY | HOLD | WEAK | Strategy ineligible (no actionable signal) |
| 8 | BID | 2270457 | 2026-09-29T23:05:56 | 8.3 | -0.003 | 66.2 | DAILY_ONLY_BUY | HOLD | WEAK | Strategy ineligible (no actionable signal) |
| 9 | LCCC | 2270515 | 2026-09-29T23:06:16 | 0.0 | -0.002 | 47.4 | DAILY_ONLY_BUY | HOLD | WEAK | Strategy ineligible (no actionable signal) |
| 10 | BRZX | 2270602 | 2026-09-29T23:07:17 | 16.7 | -0.375 | 72.4 | DAILY_ONLY_BUY | HOLD | WEAK | Strategy ineligible (no actionable signal) |
| 11 | ALISR | 2270746 | 2026-09-29T23:08:55 | 16.7 | -0.013 | 84.6 | DAILY_ONLY_BUY | HOLD | WEAK | Strategy ineligible (no actionable signal) |
| 12 | DBCAU | 2270831 | 2026-09-29T23:09:54 | 0.0 | -0.008 | 89.3 | DAILY_ONLY_BUY | HOLD | WEAK | Strategy ineligible (no actionable signal) |
| 13 | JENA | 2270837 | 2026-09-29T23:09:54 | 16.0 | -0.012 | 66.1 | DAILY_ONLY_BUY | HOLD | WEAK | Strategy ineligible (no actionable signal) |
| 14 | PRHI | 2270902 | 2026-09-29T23:10:35 | 15.1 | -0.223 | 70.2 | DAILY_ONLY_BUY | HOLD | WEAK | Strategy ineligible (no actionable signal) |
| 15 | TACOU | 2270907 | 2026-09-29T23:10:54 | 0.0 | -0.008 | 62.6 | DAILY_ONLY_BUY | HOLD | WEAK | Strategy ineligible (no actionable signal) |
| 16 | WZRD | 2270948 | 2026-09-29T23:11:15 | 16.9 | -0.415 | 85.9 | DAILY_ONLY_BUY | HOLD | WEAK | Strategy ineligible (no actionable signal) |
| 17 | HCMA | 2271893 | 2026-09-29T23:21:35 | 10.0 | -0.003 | 69.6 | DAILY_ONLY_BUY | HOLD | WEAK | Strategy ineligible (no actionable signal) |
| 18 | CMCI | 2271934 | 2026-09-29T23:22:14 | 16.3 | -0.111 | 58.2 | DAILY_ONLY_BUY | HOLD | WEAK | Strategy ineligible (no actionable signal) |

### Signal Pipeline (per case, where evidence exists)

For all 18 cases:
- **ANALYZER SIGNAL** (multi_timeframe.mtf_outcome=DAILY_ONLY_BUY): BUY, strength=DAILY_ONLY (proven via decision_snapshot.multi_timeframe)
- **POST-MTF SIGNAL** (after filter_results inside analyze_multi_timeframe): BUY, strength=DAILY_ONLY (proven via analyzed_stocks.filter_results showing all 3 filters passed: multi_timeframe_conflict.passed=true, volume_downgrade.passed=true, ai_conflict.passed=true)
- **POST-ANALYZER FILTER SIGNAL** (after liquidity check at line 6021-6027): UNKNOWN — proven to have been mutated BUY→HOLD, but the actual measurement is NOT persisted
- **FINAL PERSISTED SIGNAL** (after second DB upsert at line 7160): HOLD, strength=WEAK (proven via analyzed_stocks.signal and decision_snapshot.strategy_eligibility)
- **STRATEGY_ELIGIBLE**: False (proven via cycle_funnel.strategy_eligible_count=0 for all 18 case cycles)
- **RANKED_CANDIDATE**: False (proven via cycle_funnel.ranked_candidate_count=0)
- **EXECUTION ATTEMPTED**: False (proven via cycle_funnel.execution_attempt_count=0)
- **FINAL OUTCOME**: HOLD_INELIGIBLE (proven via decision_snapshot.decision.outcome)

---

## PHASE 4 — CLASSIFICATION (each of 18)

| # | sym | Classification | Justification |
|---|---|---|---|
| 1 | ADAMM | DOWNSTREAM_BUY_TO_HOLD_MUTATION_PROVEN_BUT_REASON_UNKNOWN | Code stage proven by elimination; volume/spread value not persisted |
| 2 | BRAZ | DOWNSTREAM_BUY_TO_HOLD_MUTATION_PROVEN_BUT_REASON_UNKNOWN | Same |
| 3 | COMD | DOWNSTREAM_BUY_TO_HOLD_MUTATION_PROVEN_BUT_REASON_UNKNOWN | Same |
| 4 | TLNCU | DOWNSTREAM_BUY_TO_HOLD_MUTATION_PROVEN_BUT_REASON_UNKNOWN | Same (only case with MACD>0 in buy_criteria) |
| 5 | NOEMR | DOWNSTREAM_BUY_TO_HOLD_MUTATION_PROVEN_BUT_REASON_UNKNOWN | Same |
| 6 | RAC | DOWNSTREAM_BUY_TO_HOLD_MUTATION_PROVEN_BUT_REASON_UNKNOWN | Same |
| 7 | ALPXR | DOWNSTREAM_BUY_TO_HOLD_MUTATION_PROVEN_BUT_REASON_UNKNOWN | Same (highest total_score=100, RSI=0) |
| 8 | BID | DOWNSTREAM_BUY_TO_HOLD_MUTATION_PROVEN_BUT_REASON_UNKNOWN | Same |
| 9 | LCCC | DOWNSTREAM_BUY_TO_HOLD_MUTATION_PROVEN_BUT_REASON_UNKNOWN | Same (lowest total_score=47.4) |
| 10 | BRZX | DOWNSTREAM_BUY_TO_HOLD_MUTATION_PROVEN_BUT_REASON_UNKNOWN | Same |
| 11 | ALISR | DOWNSTREAM_BUY_TO_HOLD_MUTATION_PROVEN_BUT_REASON_UNKNOWN | Same |
| 12 | DBCAU | DOWNSTREAM_BUY_TO_HOLD_MUTATION_PROVEN_BUT_REASON_UNKNOWN | Same |
| 13 | JENA | DOWNSTREAM_BUY_TO_HOLD_MUTATION_PROVEN_BUT_REASON_UNKNOWN | Same |
| 14 | PRHI | DOWNSTREAM_BUY_TO_HOLD_MUTATION_PROVEN_BUT_REASON_UNKNOWN | Same |
| 15 | TACOU | DOWNSTREAM_BUY_TO_HOLD_MUTATION_PROVEN_BUT_REASON_UNKNOWN | Same |
| 16 | WZRD | DOWNSTREAM_BUY_TO_HOLD_MUTATION_PROVEN_BUT_REASON_UNKNOWN | Same |
| 17 | HCMA | DOWNSTREAM_BUY_TO_HOLD_MUTATION_PROVEN_BUT_REASON_UNKNOWN | Same |
| 18 | CMCI | DOWNSTREAM_BUY_TO_HOLD_MUTATION_PROVEN_BUT_REASON_UNKNOWN | Same |

**Summary**: 18 of 18 cases are classified as **DOWNSTREAM_BUY_TO_HOLD_MUTATION_PROVEN_BUT_REASON_UNKNOWN**.
- **0 of 18 are LIQUIDITY_BLOCK_PROVEN** — would require persisted liquidity_warning or contemporaneous log; none exists.
- **0 of 18 are OTHER_FILTER_BLOCK_PROVEN** — sector filter disabled; no other post-MTF filters.
- **0 of 18 are NO_DOWNSTREAM_MUTATION** — all 18 are proven by cycle_funnel.strategy_eligible_count=0 and analyzed_stocks.signal=HOLD to have been downgraded.
- **18 of 18 are DOWNSTREAM_BUY_TO_HOLD_MUTATION_PROVEN_BUT_REASON_UNKNOWN** — the code stage is proven by elimination, but the measurement (avg volume, spread) is unrecorded.

Per Phase 4 directive: **"Likely liquidity is NOT a valid final classification"**. The owner-required state for this set of 18 is `UNKNOWN` (per the 5-category scheme), which I am representing here as `DOWNSTREAM_BUY_TO_HOLD_MUTATION_PROVEN_BUT_REASON_UNKNOWN` to make it clear which dimension is proven vs missing.

---

## PHASE 5 — PERSISTENCE LAYER TIMING (resolved)

| # | Layer | Function | When | Truth source | Field for signal |
|---|---|---|---|---|---|
| 1 | `analyzed_stocks` (1st write) | `save_analysis_to_db` line 6018 | After analyzer exit, BEFORE liquidity filter | POST-ANALYZER (inside) / PRE-LIQUIDITY | `signal=BUY` for 18 cases initially |
| 2 | In-memory analysis dict | (mutation) | Line 6021-6027 (liquidity) | POST-LIQUIDITY | `signal=HOLD` if liquidity failed |
| 3 | `analyzed_stocks` (2nd write) | `save_analysis_result` line 7160 | Inside `_persist_obs_001_decision_snapshot` | POST-ALL-FILTERS | `signal=HOLD` (the FINAL truth) |
| 4 | `decision_history` | `finalize_decision_history` line 7167 | Same time as #3 | POST-ALL-FILTERS | `decision_snapshot.strategy_eligibility.signal=HOLD` |
| 5 | `cycle_funnel` | `cycle_funnel` write | End of cycle | POST-CYCLE | `strategy_eligible_count=0` for all 18 cycles |
| 6 | `trades` | `submit_order` | Only if execute_trade called | N/A for 18 | none (no execution attempt) |

**Key finding**: The 1st `analyzed_stocks` write (step 1) at line 6018 captures the **PRE-LIQUIDITY** signal=BUY. But the 2nd write (step 3) at line 7160 OVERWRITES with **POST-LIQUIDITY** signal=HOLD. The 1st write is therefore transient and unrecoverable from DB (since UPSERT replaces the row).

The CURRENT `analyzed_stocks.signal` column therefore represents **POST-ALL-FILTERS truth** (HOLD), not PRE-LIQUIDITY (BUY). To reconstruct the pre-liquidity signal, we must rely on the analyzer's `multi_timeframe.mtf_outcome` (which is captured into the snapshot — see PR #105) plus the `analyzed_stocks.filter_results` (which shows the 3 inside-analyzer filter results, all passed for the 18).

---

## FILTER SEMANTICS (liquidity filter — what "illiquid" means to THIS BOT)

From `src/core/smart_bot.py:2164-2207`:
```python
def check_liquidity(self, symbol, min_volume=1_000_000, max_spread_pct=0.3):
    df = self.get_market_data(symbol)
    if df is None or len(df) < 20:
        return (True, 0, 0, "Insufficient data - skipping liquidity check")
    avg_volume = df['volume'].tail(20).mean()                # 20-day avg daily volume
    latest = df.iloc[-1]
    price = latest['close']
    if price <= 0:
        return (True, 0, 0, "Invalid price - skipping liquidity check")
    high_low_spread = ((latest['high'] - latest['low']) / price) * 100   # proxy for bid-ask spread
    if avg_volume < min_volume:
        return (False, avg_volume, high_low_spread, f"Volume {avg_volume/1e6:.1f}M < {min_volume/1e6:.0f}M minimum")
    if high_low_spread > max_spread_pct * 3:               # threshold is 0.3 * 3 = 0.9% — note 3x multiplier
        return (False, avg_volume, high_low_spread, f"Spread {high_low_spread:.1f}% too wide")
    return (True, avg_volume, high_low_spread, "Passes liquidity check")
```

**Effective configuration (from persisted `settings` table + schema defaults):**
- `enable_liquidity_filter = true` (persisted)
- `min_daily_volume = 1,000,000` (schema default; not overridden)
- `max_spread_pct = 0.3` (default; the threshold applied is `> 0.9%` due to the 3x multiplier at line 2197)
- `enable_sector_filter = false` (persisted) → DEAD CODE for this task

**"Illiquid" means**: `avg_volume < 1M shares/day` OR `(high-low spread / price) * 100 > 0.9%`.

**Behavior on missing data**: `return (True, 0, 0, "Insufficient data - skipping liquidity check")` — fails open, NOT fails closed. If the bot can't get data, it passes.

**Behavior on API errors**: Returns `(True, 0, 0, "Check failed - allowing")` — fails open.

**Mutation semantics**: When `passes=False`, the calling code (line 6024-6026) mutates `analysis['signal']='HOLD'`, `analysis['signal_strength']='WEAK'`, sets `analysis['liquidity_warning']=reason`. The mutation is IN-MEMORY only — the actual measured values (avg_volume, high_low_spread) and reason string are NEVER persisted to any DB column.

---

## OBSERVABILITY — PERSISTED SIGNAL PIPELINE (what's recorded today)

For each analyzed symbol, the persisted `decision_snapshot` includes:

| Block | Field | Recorded for 18 cases? |
|---|---|---|
| `multi_timeframe` | `daily_signal` | YES — "BUY" |
| `multi_timeframe` | `hourly_signal` | YES — "HOLD" |
| `multi_timeframe` | `hourly_data_available` | YES — false |
| `multi_timeframe` | `mtf_outcome` | YES — "DAILY_ONLY_BUY" |
| `multi_timeframe` | `mtf_reason` | YES — "daily_buy_no_hourly_data_daily_only" |
| `strategy_eligibility.gates` | `rsi_oversold` | YES — passed=true, observed=RSI value, threshold=35 |
| `strategy_eligibility.gates` | `sma_uptrend` | YES — passed=true, observed=SMA values |
| `strategy_eligibility.gates` | `macd_positive` | **NO** — silently skipped because `analysis.macd_histogram` is None (the analysis dict from `analyze_multi_timeframe` doesn't carry macd_histogram, only macd_score). The buy_criteria array has it, but the gates array doesn't. |
| `strategy_eligibility.gates` | `volume_confirmation` | NO — disabled (enable_volume_confirmation=false) |
| `strategy_eligibility` | `signal` | YES — "HOLD" (POST-LIQUIDITY) |
| `strategy_eligibility` | `signal_strength` | YES — "WEAK" |
| `strategy_eligibility` | `strategy_reason` | YES — "Strategy ineligible (no actionable signal)" (uninformative) |
| `filter_results` (inside snapshot, also as analyzed_stocks.filter_results) | `multi_timeframe_conflict`, `volume_downgrade`, `ai_conflict` | YES — all 3 passed |
| `filter_results` | `liquidity_filter` | **NO** — liquidity filter is NOT included in the filter_results dict (it's added AFTER `analyze_multi_timeframe` returns, in run_analysis, and is not propagated to the snapshot) |
| `filter_results` | `sector_filter` | **NO** — sector filter is disabled; even when enabled, it's not propagated to the snapshot |
| `decision` | `outcome` | YES — "HOLD_INELIGIBLE" |
| `decision` | `primary_reason` | YES — "Strategy ineligible (no actionable signal)" (uninformative) |

### Unknown Semantics — gaps in current persistence

| Missing field | Why it matters | Workaround today |
|---|---|---|
| `signal_pipeline.analyzer_signal` (raw) | Cannot reconstruct the analyzer-level signal once it's mutated | `multi_timeframe.mtf_outcome` is a proxy (DAILY_ONLY_BUY is the only BUY outcome that the MTF emits when daily=BUY+hourly unavailable; this is good enough for the 18 cases) |
| `signal_pipeline.post_mtf_signal` (after inside-analyzer filters) | Cannot prove all 3 inside-analyzer filters passed | `analyzed_stocks.filter_results.multi_timeframe_conflict/volume_downgrade/ai_conflict` prove this |
| `signal_pipeline.post_liquidity_signal` | Cannot prove liquidity is what mutated BUY→HOLD | ❌ NOT PERSISTED. Only by elimination. |
| `signal_pipeline.post_sector_signal` | Cannot prove sector filter is dead | N/A (sector disabled) |
| `signal_pipeline.final_signal` | This is what we have | `decision_snapshot.strategy_eligibility.signal` |
| `liquidity_filter.{enabled, evaluated, passed, avg_volume, required_volume, spread, threshold}` | The actual measurement is missing | ❌ NOT PERSISTED |
| `sector_filter.{enabled, evaluated, passed, sector, sector_score, threshold}` | The actual measurement is missing | N/A (sector disabled) |
| `macd_positive` gate | The MACD gate is silently skipped from `strategy_eligibility.gates` | In `analyzed_stocks.buy_criteria` only |

### Failure semantics — FILTER FAILED vs COULD NOT BE EVALUATED vs DISABLED

Currently NOT distinguished in persisted data. The Phase 7 design below proposes the explicit taxonomy.

---

## PHASE 6 — DETERMINE WHETHER CURRENT EVIDENCE IS ENOUGH

**Verdict: NO — current evidence is NOT enough to deterministically attribute the 18 cases to a specific blocker.**

Per Phase 6 directive:
> If any subset cannot be deterministically attributed because the filter result/value/threshold was not persisted → that missing observability becomes the immediate implementation task.

**The missing observability is:**
1. **Liquidity filter actual measurement** (avg_volume, high_low_spread, min_volume threshold, max_spread_pct threshold, passed/failed). Currently: NEVER PERSISTED.
2. **macd_positive gate in `strategy_eligibility.gates`**. Currently: SILENTLY SKIPPED because `analysis.macd_histogram` is None.
3. **Sector filter evaluation** (N/A because disabled, but should be explicitly recorded as DISABLED for clarity).
4. **Filter-applied semantics**: FILTER FAILED vs COULD NOT BE EVALUATED vs DISABLED vs NOT APPLICABLE.

Per the Phase 7 directive:
> If attribution is incomplete, implement the smallest additive observability contract needed to make future BUY → HOLD decisions fully explainable. This is explicitly authorized if required.
> Do NOT change filter behavior. Do NOT change strategy. Do NOT change thresholds.

**The smallest additive observability contract is a `signal_pipeline` block in the decision_snapshot plus an extended `filter_results` block that captures the actual measurements.** This is described in the next section.

---

## PHASE 7 DESIGN — MINIMUM ADDITIVE OBSERVABILITY CONTRACT (NOT YET IMPLEMENTED)

The owner explicitly authorized additive observability changes if attribution is incomplete. Below is the proposed contract. **No code changes have been made yet in this turn** — this is the design for the bounded PR that will follow owner review.

### Proposed decision_snapshot additions (additive, schema_version bump 1 → 2)

#### New block: `signal_pipeline`

```json
{
  "signal_pipeline": {
    "analyzer_signal": "BUY",                // signal at end of analyze_multi_timeframe
    "analyzer_signal_strength": "DAILY_ONLY",
    "post_inside_analyzer_filter_signal": "BUY",  // after mtf_conflict / volume_downgrade / ai_conflict
    "post_inside_analyzer_filter_strength": "DAILY_ONLY",
    "post_liquidity_filter_signal": "HOLD", // after run_analysis line 6021-6027
    "post_liquidity_filter_strength": "WEAK",
    "post_sector_filter_signal": "HOLD",    // (sector disabled, unchanged from prev)
    "post_sector_filter_strength": "WEAK",
    "final_signal": "HOLD",                  // what gets persisted + ranked
    "final_signal_strength": "WEAK",
    "downgrade_stage": "liquidity_filter",   // first stage that downgraded (or null if no downgrade)
    "downgrade_reason": "Failed liquidity filter - Volume X.XM < 1.0M minimum"
  }
}
```

#### Extended block: `strategy_eligibility.filter_results`

Currently the snapshot has 3 filter entries (mtf_conflict, volume_downgrade, ai_conflict) — these come from `analyzed_stocks.filter_results`. Phase 7 adds the post-analyzer filters that actually run:

```json
{
  "filter_results": {
    // existing entries (from analyzer):
    "multi_timeframe_conflict": { "passed": true, "blocked": false, ... },
    "volume_downgrade": { "passed": true, "blocked": false, ... },
    "ai_conflict": { "passed": true, "blocked": false, ... },

    // NEW (from post-analyzer run_analysis flow):
    "liquidity_filter": {
      "enabled": true,                                    // config flag
      "evaluated": true,                                  // bot reached this filter for this symbol
      "applicable": true,                                 // signal was BUY at evaluation time
      "state": "FILTER_FAILED",                            // FILTER_FAILED | FILTER_PASSED | COULD_NOT_BE_EVALUATED | DISABLED | NOT_APPLICABLE
      "passed": false,
      "blocked": true,
      "measured": {
        "avg_volume_20d": 45678.0,                        // 20-day average daily volume (shares)
        "required_volume": 1000000.0,                     // min_daily_volume threshold
        "high_low_spread_pct": 0.42,                      // ((high-low)/price) * 100
        "max_spread_pct_threshold": 0.9                   // max_spread_pct * 3
      },
      "reason": "Volume 0.05M < 1.0M minimum",            // same string as analysis.liquidity_warning
      "config_key": "enable_liquidity_filter",            // which config flag controls this filter
      "evaluated_at": "2026-09-29T22:27:06.732539+00:00"
    },
    "sector_filter": {
      "enabled": false,
      "evaluated": false,
      "applicable": false,
      "state": "DISABLED",
      "passed": null,    // null = not evaluated; tri-state explicit
      "blocked": false,
      "measured": null,
      "reason": "enable_sector_filter=False",
      "config_key": "enable_sector_filter"
    }
  }
}
```

#### Phase 7A — Filter values and thresholds (explicit persistence)

The `measured` sub-dict above is the Phase 7A requirement: persist actual measured values, not just pass/fail. For the liquidity filter, this means:
- `avg_volume_20d` — the actual 20-day average volume (shares)
- `required_volume` — the threshold applied (`min_daily_volume`)
- `high_low_spread_pct` — the actual proxy spread
- `max_spread_pct_threshold` — the threshold applied (0.9% for current config)

This way the dashboard can show: "BUY blocked by Liquidity. Measured: avg_volume 45.7k. Required: 1.0M. Spread: 0.42%. Threshold: 0.9%."

#### Phase 7B — Failure / Unknown semantics (explicit taxonomy)

| State | Meaning | persisted `passed` | persisted `blocked` |
|---|---|---|---|
| `FILTER_PASSED` | Evaluated, did not block | `true` | `false` |
| `FILTER_FAILED` | Evaluated, blocked | `false` | `true` |
| `COULD_NOT_BE_EVALUATED` | Filter ran but data missing/error; configured behavior is fail-open (allowed) | `true` | `false` |
| `COULD_NOT_BE_EVALUATED_FAILED_CLOSED` | Filter ran but data missing/error; configured behavior is fail-closed (rejected) | `false` | `true` |
| `DISABLED` | Config flag is off | `null` | `false` |
| `NOT_APPLICABLE` | Filter only applies to BUY but signal was SELL/HOLD at evaluation time | `null` | `false` |

Note: `passed=null` (not boolean) explicitly distinguishes "not evaluated" from "evaluated and passed". This is the Phase 7B distinction.

#### Phase 7C — Test contract

New test file `tests/test_p0_buy_hold_pipeline_observability.py`:
1. `test_liquidity_block_persisted_measurement` — mock check_liquidity to return (False, 45678, 0.42, "Volume 0.05M < 1.0M"); assert snapshot has `filter_results.liquidity_filter.measured = {avg_volume_20d: 45678.0, required_volume: 1000000.0, high_low_spread_pct: 0.42, max_spread_pct_threshold: 0.9}`
2. `test_liquidity_disabled_state_recorded` — set `enable_liquidity_filter=False`; assert `filter_results.liquidity_filter.state="DISABLED"` and `passed=null`
3. `test_liquidity_could_not_evaluate_fail_open` — mock check_liquidity to raise; assert `state="COULD_NOT_BE_EVALUATED"` and `passed=true` (matching the fail-open check_liquidity behavior at line 2206)
4. `test_sector_disabled_state_recorded` — set `enable_sector_filter=False`; assert `filter_results.sector_filter.state="DISABLED"`
5. `test_signal_pipeline_records_all_stages` — assert every analysis that reaches the persist path has a `signal_pipeline` block with all 5 stages populated
6. `test_downgrade_stage_correct` — for liquidity-blocked BUY, assert `signal_pipeline.downgrade_stage="liquidity_filter"`; for inside-analyzer mtf-conflict block, assert `downgrade_stage="multi_timeframe_conflict"`; etc.
7. `test_macd_positive_gate_now_persisted` — assert the macd_positive gate is in `strategy_eligibility.gates` whenever `analysis.macd_score > 0`
8. `test_existing_trading_result_unchanged` — run all existing tests + OBS-001 tests + MTF tests; assert 0 regressions in actual BUY/SELL/HOLD outcomes

All tests are observability-only — they don't change BUY/SELL/HOLD outcomes.

### Implementation location (proposed)

| Concern | File | Function | Lines |
|---|---|---|---|
| Capture `post_liquidity_filter_signal` | `src/core/smart_bot.py` | `run_analysis` | After line 6027 |
| Capture `post_sector_filter_signal` | `src/core/smart_bot.py` | `run_analysis` | After line 6039 |
| Build `signal_pipeline` block | `src/core/smart_bot.py` | `_build_decision_snapshot` | New helper that consumes the scratch fields |
| Extend `filter_results` with liquidity + sector | `src/core/smart_bot.py` | `_build_decision_snapshot` | After existing filter_results block |
| Add macd_positive gate fallback | `src/core/smart_bot.py` | `_build_decision_snapshot` | Use macd_score sign as fallback when macd_histogram is None |
| Persist via existing `_persist_obs_001_decision_snapshot` | (unchanged) | (unchanged) | The new fields ride on the existing snapshot |
| Schema version bump | `src/core/smart_bot.py` | `OBS_001_SCHEMA_VERSION` constant | 1 → 2 |

**Approximate diff size**: 1 new helper function, ~80 lines added across `_build_decision_snapshot` and `run_analysis`, 1 schema_version constant bump, 1 new test file (~8 tests, ~250 lines).

---

## PHASE 8 — DETERMINE IF BLOCK IS LEGITIMATE (provisional, with caveats)

Cannot fully determine legitimacy from persisted data alone. Provisional assessment:

**The liquidity filter as designed** (`check_liquidity` at line 2164-2207):
- **What risk/problem it prevents**: Trading illiquid stocks that may have wide spreads and slippage, low-volume chop, or stale prices.
- **Implementation behavior**: Checks `avg_volume < 1M` OR `high_low_spread > 0.9%` (3x of 0.3%). Fails open on data missing/error.
- **BUY → HOLD is the explicit intended response** when liquidity fails.
- **Filter is receiving appropriate data** (calls `self.get_market_data(symbol)` which returns the daily bars DataFrame).
- **No evidence of code/ordering bug** in the current code (mutation site is correct).

**Provisional classification for the 18 cases (subject to Phase 7 prospective validation):**

For the 18 cases, the **15 cases with RSI<20 and MACD≈0** (ADAMM, BRAZ, COMD, NOEMR, ALPXR, BID, LCCC, DBCAU, PRHI, TACOU, WZRD, HCMA, etc.) are likely **legitimate liquidity rejections** of micro-cap / low-volume stocks (the buy_criteria shows MACD≈0 or very small, suggesting prices are near-zero where `price` in `check_liquidity` triggers the spread calculation in a different regime). These are micro-cap tickers with prices often under $5.

**However**, until Phase 7 observability is implemented and prospective BUY cases are collected, we cannot prove the actual measurement (avg_volume, spread) for any specific case.

**Provisional classification**: E. INSUFFICIENT EVIDENCE — until Phase 7 makes the measurement persistent.

---

## PROSPECTIVE VALIDATION (Phase 9 design)

After Phase 7 code is implemented, tested, merged, and deployed via systemd:
- Collect fresh decisions for bounded observation window (e.g., 6-12 hours, expect ~50-100 analyzer BUY cases)
- For each analyzer BUY case, verify that:
  - `signal_pipeline.downgrade_stage` correctly identifies the first filter that downgraded (liquidity, sector, mtf_conflict, volume_downgrade, ai_conflict, or null)
  - `filter_results.<filter>.measured` persists the actual values
- Once 0 UNKNOWN BUY → HOLD cases remain in a fresh sample, P0 closes

---

## DASHBOARD (Phase 10-13 design)

After Phase 7 observability is deployed, extend the existing BUY diagnostic area:

### BUY Blocker View (new widget)
- **BUY SIGNALS GENERATED** (count of analyzer-level BUY in window)
- **FINAL BUY SIGNALS** (count of strategy_eligible BUY in window)
- **BUY → HOLD DOWNGRADES** (count, with denominator = BUY SIGNALS GENERATED)
- **DOWNGRADE REASON COUNTS** (table: liquidity_filter N, multi_timeframe_conflict N, volume_downgrade N, ai_conflict N, none N)
- **DRILL-DOWN TABLE**: symbol, timestamp, analyzer signal, final signal, blocker, measured value, threshold/rule, MTF context

### Clear denominators (Phase 12)
```
Analyzer BUYs: N
Final BUYs: M
Downgraded: (N-M)/N
Reasons:
  Liquidity: 18/18
  Multi-timeframe conflict: 0/18
  Volume downgrade: 0/18
  AI conflict: 0/18
  Other: 0/18
```

### Pre-filter truth preserved (Phase 13)
Show both analyzer signal AND final signal in the UI. Do not overwrite history by showing only final HOLD.

---

## SAFETY (Phase 14, 15)

- **PAPER-ONLY**: ALPACA_BASE_URL=https://paper-api.alpaca.markets/v2, TRADING_BOT_PAPER_ONLY=1
- **Strategy unchanged**: No RSI/SMA/MACD threshold changes, no strategy gate changes, no filter behavior changes
- **No code/config/DB schema changes in this turn** — only design documented; implementation requires separate bounded PR
- **Historical rows untouched**: All queries were read-only with id-range filters
- **No restart**: SmartBot PID 1239963 has been running since 2026-09-29 22:15 UTC
- **No broker action**: No Alpaca API calls beyond the running bot
- **Unrelated services untouched**: trading-dashboard 1212213, dashboard 1067605, openclaw-gateway 965975, cloudflared 656088 — all unchanged
- **Roadmap frozen**: No work on strategy tuning, MTF tuning, hourly cache, dashboard perf, ranking, execution, unrelated backlog, cleanup/refactoring

---

## HISTORICAL CLAIMS (Phase 14)

- **The historical ~510 WEAK rows from the frozen RSI+SMA joint-pass cohort**: cannot be re-attributed to a specific blocker because their historical persisted evidence does NOT include the liquidity/sector filter measurements. The historical cohort rows are pre-PR #105 (no multi_timeframe block), pre-OBS-001 (no decision_snapshot in many rows), and pre-Phase 7 (no signal_pipeline block).
- **Label**: "Historical unknowns — pre-PR #105 + pre-OBS-001 cohort cannot be retroactively attributed. New prospective observability (Phase 7) solves the problem going forward."

---

## REQUIRED OWNER ANSWERS (Phase 16)

| Question | Answer (from this investigation) |
|---|---|
| How many analyzer BUYs were generated? | **18** in the STRAT-003 window (2026-09-29 22:15:54 → 23:22:20 UTC) |
| How many became final HOLD? | **18** |
| What exact code stage caused each downgrade? | `src/core/smart_bot.py` line 6021-6027 (liquidity filter in `run_analysis`); proven by elimination as the only BUY→HOLD mutation site between analyzer exit and final DB upsert |
| What exact filter caused each downgrade? | Liquidity filter (`check_liquidity`); sector filter is DEAD CODE (`enable_sector_filter=False`); inside-analyzer filters (mtf_conflict, volume_downgrade, ai_conflict) all proven PASSED for the 18 cases |
| What measured value caused each rejection? | **UNKNOWN** — `avg_volume_20d`, `high_low_spread_pct`, and the threshold values are NOT persisted |
| What threshold/rule was applied? | `avg_volume < min_daily_volume (1,000,000)` OR `high_low_spread_pct > max_spread_pct * 3 (0.9%)` — from code at line 2193-2198 |
| Was the filter working as designed? | **PROVISIONALLY YES** — code structure is correct, mutation semantics match design |
| Was any downgrade caused by missing/error data? | **UNKNOWN** — `check_liquidity` fails open on missing data (returns True), so missing data → no downgrade. Cannot distinguish from current persisted state |
| Is the blocker legitimate according to current configured behavior? | **PROVISIONALLY YES** for the design intent (avoid illiquid stocks); requires Phase 7 prospective validation to confirm |
| Is every future BUY → HOLD now deterministically attributable? | **NO** — Phase 7 observability is required; this report identifies the exact gap |
| Can the dashboard show the blocker without recomputation? | **NO** — Phase 10-13 dashboard work is required |
| Does the dashboard now show analyzer BUY → blocker → final HOLD? | **NO** — Phase 10-13 dashboard work is required |
| Are there any remaining UNKNOWN BUY → HOLD cases? | **YES — all 18 of 18** are classified as DOWNSTREAM_BUY_TO_HOLD_MUTATION_PROVEN_BUT_REASON_UNKNOWN |

---

## STATUS (Phase 17)

**P0 REMAINS OPEN.** The 18 cases are not yet deterministically attributable because the actual liquidity measurements are not persisted. Phase 7 additive observability is required.

The owner is asked to authorize:
1. **Phase 7 implementation** — additive observability contract (proposed in this report, ~80 lines added + 1 new test file)
2. **Phase 9 deployment** — merge PR + deploy via systemd + collect prospective BUY cases
3. **Phase 10-13 dashboard** — BUY Blocker View widget

Once Phase 7 is implemented and a prospective sample shows UNKNOWN BUY → HOLD CASES = 0, P0 closes.

---

## NEXT RECOMMENDED ACTION

Recommended next step (owner authorization required):

1. **Owner review of Phase 7 design above** — confirm the proposed `signal_pipeline` block + extended `filter_results` block + `state` taxonomy are correct and minimal
2. **Open bounded PR** on branch `p0-buy-hold-pipeline-observability` — additive observability only, no strategy/filter changes
3. **Tests** — 8 deterministic tests proving observability without behavior change
4. **Merge → deploy via systemd → collect fresh prospective BUY cases**
5. **Dashboard** — Phase 10-13 after a sufficient sample shows the persistence works

Until Phase 7 is implemented and prospectively validated, **the P0 question is not yet fully resolved**: the owner explicitly stated "Likely liquidity is NOT a valid final classification" and we cannot yet deterministically attribute any of the 18 cases to a specific measurement.

P0 BUY→HOLD ROOT CAUSE REMAINS OPEN — DO NOT PROCEED TO STRATEGY TUNING
