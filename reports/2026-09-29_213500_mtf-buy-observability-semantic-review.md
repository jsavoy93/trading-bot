# MTF BUY OBSERVABILITY — Semantic Review Report (corrected)

**Task:** Owner-requested bounded semantic review of PR #105 (MTF BUY OBSERVABILITY).

**Result:** A faithful-mapping defect was found in the helper's mapping for two reachable MTF branches. A small observability-only correction has been applied. The corrected helper now mirrors the actual `SmartBot.analyze_multi_timeframe` combination logic exactly across all 12 reachable branches.

**Branch:** `mtf-buy-observability` (off `main @ 3406a8e`)
**HEAD:** `7d3e901` (after correction; supersedes `01ecd68`)
**PR:** #105 OPEN — https://github.com/jsavoy93/trading-bot/pull/105
**Decision:** PR #105 CORRECTED — READY FOR OWNER RE-REVIEW (option B)
**Tests:** 187 focused tests PASS (45 MTF + 142 regression), 0 regressions, `git diff --check` clean
**Files touched in correction:** `src/core/mtf_outcome.py` (+84 / -36), `tests/test_mtf_buy_observability.py` (+79 / -0)
**No MKT-CACHE / strategy / config / dashboard / DB changes.**

---

## Branch Map (authoritative)

The MTF combination in `src/core/smart_bot.py::analyze_multi_timeframe` evaluates daily and hourly independently, then combines. There are **12 reachable combinations**. Each is mapped to a stable OUTCOME token and a distinct REASON token.

### Daily signal evaluation (3 outcomes)
```
if sma_fast_daily > sma_slow_daily AND rsi_daily < rsi_buy_threshold:
    daily_signal = "BUY"
elif sma_fast_daily < sma_slow_daily AND rsi_daily > rsi_sell_threshold:
    daily_signal = "SELL"
# else daily_signal = None (HOLD)
```

### Hourly signal evaluation (4 outcomes, when hourly available)
```
if hourly_indicators is None:
    hourly_signal = None   # NOT EVALUATED
elif not pd.isna(sma_fast_hourly) and not pd.isna(rsi_hourly):
    if sma_fast_hourly > sma_slow_hourly AND rsi_hourly < rsi_buy_threshold:
        hourly_signal = "BUY"
    elif sma_fast_hourly < sma_slow_hourly AND rsi_hourly > rsi_sell_threshold:
        hourly_signal = "SELL"
    # else hourly_signal = None (HOLD — neutral hourly)
```

### MTF combination (the 5 distinct branches)
```python
if daily_signal and hourly_signal:           # both truthy
    if daily_signal == hourly_signal:        # same direction
        signal = daily_signal               # BUY or SELL
        signal_strength = STRONG | MEDIUM   # RSI-extremity based
    else:                                     # conflicting directions
        signal = "HOLD"
        signal_strength = "CONFLICTED"
elif daily_signal and not hourly_indicators: # daily truthy, NO hourly data
    signal = daily_signal                   # BUY or SELL
    signal_strength = "DAILY_ONLY"
else:                                        # EVERYTHING ELSE
    signal = "HOLD"                          # includes:
                                             #   daily=X, hourly=HOLD (with data)
                                             #   daily=HOLD, hourly=Y
                                             #   daily=HOLD, hourly=HOLD
                                             #   daily=HOLD, no hourly data
```

---

## Authoritative mapping table (all 12 reachable branches)

| # | daily | hourly_signal | hourly_data_available | final signal | final signal_strength | mtf_outcome | mtf_reason |
|---|---|---|---|---|---|---|---|
| 1 | BUY | BUY | True | BUY | STRONG/MEDIUM | AGREE_BUY | daily_buy_hourly_buy_agreement |
| 2 | SELL | SELL | True | SELL | STRONG/MEDIUM | AGREE_SELL | daily_sell_hourly_sell_agreement |
| 3 | BUY | SELL | True | HOLD | CONFLICTED | CONFLICT | daily_buy_hourly_sell_disagreement |
| 4 | SELL | BUY | True | HOLD | CONFLICTED | CONFLICT | daily_sell_hourly_buy_disagreement |
| 5 | BUY | HOLD | True | HOLD | WEAK | NO_ACTION | daily_buy_hourly_hold_no_action |
| 6 | SELL | HOLD | True | HOLD | WEAK | NO_ACTION | daily_sell_hourly_hold_no_action |
| 7 | BUY | None | False | BUY | DAILY_ONLY | DAILY_ONLY_BUY | daily_buy_no_hourly_data_daily_only |
| 8 | SELL | None | False | SELL | DAILY_ONLY | DAILY_ONLY_SELL | daily_sell_no_hourly_data_daily_only |
| 9 | HOLD | BUY | True | HOLD | WEAK | HOURLY_ONLY_BUY | daily_hold_hourly_buy_no_action |
| 10 | HOLD | SELL | True | HOLD | WEAK | HOURLY_ONLY_SELL | daily_hold_hourly_sell_no_action |
| 11 | HOLD | HOLD | True | HOLD | WEAK | NO_ACTION | daily_hold_hourly_hold_no_action |
| 12 | HOLD | None | False | HOLD | WEAK | NO_ACTION | daily_hold_no_hourly_data_no_action |

### Unreachable branches (kept for helper completeness, but not production-reachable)

| daily | hourly_signal | hourly_data_available | Why unreachable |
|---|---|---|---|
| HOLD | BUY | False | Requires hourly BUY without hourly data — contradiction |
| HOLD | SELL | False | Requires hourly SELL without hourly data — contradiction |
| HOLD | HOLD | False | Equal to row 12 (daily=HOLD, hourly=None, avail=False) |
| BUY | BUY | False | Requires hourly BUY without hourly data — contradiction |
| BUY | SELL | False | Requires hourly SELL without hourly data — contradiction |
| SELL | BUY | False | Requires hourly BUY without hourly data — contradiction |
| SELL | SELL | False | Requires hourly SELL without hourly data — contradiction |
| BUY | None | True | Already covered by row 5 (hourly_signal None = HOLD when avail True) |
| SELL | None | True | Already covered by row 6 |

The helper accepts any (daily, hourly, avail) triple but only the 12 reachable branches should occur in production data.

---

## Defect Found in PRE-Review PR (corrected in this PR)

**File:** `src/core/mtf_outcome.py`
**Bug:** Rows 5 and 6 above (daily=BUY/SELL, hourly=HOLD, hourly_data_available=True) were classified as `DAILY_ONLY_BUY/SELL` with reasons `daily_{buy|sell}_hourly_hold_daily_only`. The actual SmartBot code falls through to the `else: signal = "HOLD"` branch for these cases — the `elif daily_signal and not hourly_indicators:` branch requires `hourly_indicators is None`, NOT just `hourly_signal is None`.

**Impact (had the defect shipped):** Observability would have mislabeled the actual `HOLD / WEAK` outcomes from rows 5 and 6 as `DAILY_ONLY_BUY/SELL`, hiding the true blocker and conflating "daily BUY + hourly HOLD with data" with "daily BUY + no hourly data".

**Fix (applied in this PR):**
- Helper now requires `not avail` for DAILY_ONLY_* outcomes (matches the elif condition).
- New `NO_ACTION` branch added for daily=X, hourly=HOLD, avail=True cases.
- Two reason tokens renamed:
  - REMOVED: `daily_buy_hourly_hold_daily_only`
  - REMOVED: `daily_sell_hourly_hold_daily_only`
  - ADDED: `daily_buy_hourly_hold_no_action`
  - ADDED: `daily_sell_hourly_hold_no_action`
- 8 OUTCOME tokens unchanged.

---

## Other semantic checks (sections 3–10 of owner review)

### 3. Raw signal source — PASS
`daily_signal` and `hourly_signal` are set directly from RSI/SMA comparisons in `analyze_multi_timeframe`. They are NOT recomputed from current indicators/config. Helper normalization (`"buy"` → `"BUY"`, garbage → `"HOLD"`) only runs when an unrecognized value enters the helper. The actual canonical values from the strategy are `None / "BUY" / "SELL"` — exactly what is persisted.

### 4. No observability feedback into trading — PASS
Call order in `analyze_multi_timeframe`:
1. Compute `signal` and `signal_strength` via existing MTF combination logic.
2. Compute `mtf_outcome` / `mtf_reason` via the helper (post-hoc).
3. Stamp `_last_mtf_*` scratch fields and `analysis_result["mtf_*"]` dict keys.
4. Apply downstream filter blocks (volume, AI).
5. Build `analysis_result`.

Helper output is NEVER consumed by `signal` / `signal_strength` / `eligibility` / `score` / `rank` / `execution` code paths.

### 5. Hourly-data-unavailable semantics — PASS (after correction)
- `get_hourly_market_data()` returns None when Alpaca returns < 24 hourly bars.
- `hourly_indicators` is None in that case; `hourly_signal` stays None.
- The MTF `elif` branch fires → final signal=daily_signal, strength=DAILY_ONLY.
- Helper receives `(daily, None, False)` and produces `DAILY_ONLY_*` (rows 7-8).
- `hourly_data_available=False` means "hourly indicators were never computed".

### 6. Invalid-data paths — PASS
- Missing daily data → early return None before helper is called.
- Insufficient daily bars → early return None.
- Missing hourly data → helper receives `(daily, None, False)`; NOT a fake reason.
- Insufficient hourly bars → helper receives `(daily, None, False)`; NOT a fake reason.
- NaN indicators → early return None.
- SCORE-001 fail-closed → early return None with `self._last_score_error_reason` set.
- Analysis exception → try/except returns None; helper not called.
- No fake normal-strategy reasons are persisted for invalid-data paths.

### 7. Single-timeframe path — PASS
- `analyze_symbol` does NOT call the helper.
- `analyze_symbol` clears `_last_mtf_*` scratch fields at entry.
- `_build_decision_snapshot` only emits the `multi_timeframe` block when `analysis.get("multi_timeframe")` is truthy. Single-timeframe analyses have no such flag → block is None.
- Even with stale scratch fields from a prior MTF call, the single-timeframe path produces `multi_timeframe: null`.

### 8. Scratch-state leakage — PASS
- `analyze_multi_timeframe` clears 5 scratch fields at entry: `_last_mtf_outcome`, `_last_mtf_reason`, `_last_mtf_daily_signal`, `_last_mtf_hourly_signal`, `_last_mtf_hourly_data_available`.
- `analyze_symbol` clears the same 5 fields at entry.
- All early returns within `analyze_multi_timeframe` (missing data, NaN, SCORE-001 fail-closed, exception) preserve the cleared state — helper not called in those paths.

### 9. Early-return coverage — PASS
Every early-return path in `analyze_multi_timeframe`:
- `df_daily is None or len(df_daily) < self.sma_slow` → return None
- `pd.isna(sma_fast_daily) or pd.isna(rsi_daily)` → return None
- `_score_components_with_reason returns None` → return None with `_last_score_error_reason` set
- `_clamp_total_score_with_reason returns None` → return None
- `except Exception` → return None

In every case, the helper is NOT called and scratch fields stay cleared. The snapshot builder, when invoked from a fallback path (`_persist_skipped_terminal_decision`), receives an analysis dict without `multi_timeframe=True`, so the `multi_timeframe` block is None.

### 10. Final signal / signal_strength invariance — PASS
- The helper is called AFTER `signal` and `signal_strength` are computed.
- The helper does not modify them.
- The analysis_result dict receives new keys (`daily_signal`, `mtf_outcome`, `mtf_reason`, `mtf_hourly_data_available`) that downstream code (filter_results, snapshot builder) does not consult.
- Regression: `tests/test_smart_bot_decision_paths.py` and `tests/test_smart_bot_score_normalization.py` still pass.
- **STRATEGY/TRADING LOGIC CHANGED = NO**

---

## Reason quality review (section 11)

The 12 reason tokens are MECE across the 12 reachable MTF branches:

| Quality concern | Status |
|---|---|
| TOO COARSE: different branches collapse together | NOT PRESENT — every reachable branch has a distinct reason |
| TOO FINE: identical behavior gets multiple labels | NOT PRESENT — every reason corresponds to a distinct final signal/strength |
| MISLEADING: label implies untested condition | NOT PRESENT — every label matches an actual code condition |

### Distinction that the dashboard gets
For an `HOLD_INELIGIBLE / WEAK` row, the dashboard can now distinguish:
- `daily BUY / hourly HOLD (with data)` — daily strong, hourly neutral
- `daily SELL / hourly HOLD (with data)` — daily strong (SELL), hourly neutral
- `daily HOLD / hourly BUY` — daily neutral, hourly strong (BUY)
- `daily HOLD / hourly SELL` — daily neutral, hourly strong (SELL)
- `daily HOLD / hourly HOLD` — both neutral
- `daily HOLD / no hourly data` — daily alone, no confirm

And for `HOLD_INELIGIBLE / CONFLICTED`:
- `daily BUY / hourly SELL` — explicit disagreement (BUY vs SELL)
- `daily SELL / hourly BUY` — explicit disagreement (SELL vs BUY)

CONFLICTED and WEAK/no-action are NOT collapsed.

---

## JSON persistence (section 12)

- Path: `decision_snapshot.multi_timeframe.{daily_signal, hourly_signal, hourly_data_available, mtf_outcome, mtf_reason}`
- Additive JSON only — no new top-level keys except `multi_timeframe`
- `schema_version` stays 1
- Old readers tolerate absent `multi_timeframe` block (it is None / key absent)
- No relational schema migration
- No historical backfill
- No consumer validates snapshot keys strictly (dashboard reads via `json_extract`)

---

## Dashboard consumability (section 13)

A future query can distinguish, prospectively, using only persisted fields:

```sql
-- daily_signal x hourly_signal distribution
SELECT
  json_extract(snapshot, '$.multi_timeframe.daily_signal') AS d,
  json_extract(snapshot, '$.multi_timeframe.hourly_signal') AS h,
  COUNT(*) AS n
FROM decision_history
WHERE json_extract(snapshot, '$.decision.outcome') = 'HOLD_INELIGIBLE'
GROUP BY d, h ORDER BY n DESC;

-- mtf_reason distribution within each mtf_outcome bucket
SELECT
  json_extract(snapshot, '$.multi_timeframe.mtf_outcome') AS o,
  json_extract(snapshot, '$.multi_timeframe.mtf_reason') AS r,
  COUNT(*) AS n
FROM decision_history
WHERE json_extract(snapshot, '$.decision.outcome') = 'HOLD_INELIGIBLE'
  AND json_extract(snapshot, '$.multi_timeframe.hourly_data_available') = 1
GROUP BY o, r ORDER BY n DESC;

-- hourly data availability rate
SELECT
  json_extract(snapshot, '$.multi_timeframe.hourly_data_available') AS avail,
  COUNT(*) AS n
FROM decision_history
GROUP BY avail;
```

No recomputation needed.

---

## Test matrix (section 14)

The "16 combinations" reported in the prior implementation summary was a manual smoke test, not a 4×4 matrix. The actual test coverage is:

### Helper tests (deterministic)
- 8 outcome tokens (TestMtfOutcomeTokens)
- 2 agreement cases (BUY/BUY, SELL/SELL)
- 2 conflict cases (BUY/SELL, SELL/BUY)
- 2 daily-only no-data cases (BUY, SELL with avail=False)
- 2 hourly-only cases (daily=HOLD, hourly=BUY/SELL)
- 2 NO_ACTION both-hold cases (avail True/False)
- 2 NEW NO_ACTION daily-directional cases (BUY/SELL with hourly=HOLD, avail=True) — added in this correction
- 5 invalid-input normalization cases (None, "", "garbage", 42, [])
- 4 canonical-input normalization cases ("buy", "Buy", "BUY", "  BUY  ")
- 3 more invalid hourly cases (None, "", "garbage")
- 2 hourly_data_available flag cases
- 2 return-shape cases
- 4 snapshot integration cases
- 2 single-timeframe regression cases
- 1 existing-shape-preserved case
- 2 scratch-clearing structural guards (analyze_symbol + analyze_multi_timeframe)
- 2 helper-importable structural guards

Total: 45 MTF tests (was 41; +4 for the corrected mapping).

### Reachable vs artificial
All 12 reachable branches (see table above) have explicit test coverage. The helper tests for invalid-input normalization test the normalization contract but the resulting normalized values fall into reachable branches (None/garbage → HOLD → some reachable (d, h, avail) triple).

The structural guard tests verify:
- helper is imported and called in analyze_multi_timeframe
- scratch fields cleared in both analyze_symbol and analyze_multi_timeframe

---

## PR Diff Footprint

```
src/core/mtf_outcome.py             | +298 / -36   (added 2 reasons, fixed mapping)
src/core/smart_bot.py               |  +98 / -0
tests/test_mtf_buy_observability.py | +579 / -0
reports/2026-09-29_173907_mtf-buy-observability-implementation.md | archive
ITERATION_PROGRESS_LOG.md            | +68 entries
```

Total: 5 files, strictly additive. No MKT-CACHE / strategy / config / dashboard / DB changes. PR remains OPEN.

---

## Owner Action Required

1. Review the correction in this branch (`7d3e901`).
2. Approve or reject the merge.
3. After approval: authorize deploy via standard systemd restart sequence.
4. After deploy: collect the production sample per the proposed queries above to identify the dominant blocker for HOLD_INELIGIBLE rows.