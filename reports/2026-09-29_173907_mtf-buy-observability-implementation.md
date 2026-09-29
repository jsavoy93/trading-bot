# MTF BUY OBSERVABILITY — Implementation Report

**Task:** Persist daily/hourly signals + normalized MTF outcome/reason into `decision_snapshot` so the dashboard can answer "why was this WEAK?" without recomputation.

**Branch:** `mtf-buy-observability` (off `main @ 3406a8e`)
**Commit:** `3a3b3e7c23212417661893d19a634bcf19861bc2`
**PR:** #105 OPEN — https://github.com/jsavoy93/trading-bot/pull/105
**Decision:** DO NOT MERGE / DO NOT DEPLOY — owner review pending

---

## Files Changed

| Path | Lines | Type |
|---|---|---|
| `src/core/mtf_outcome.py` | +287 | NEW (pure helper) |
| `src/core/smart_bot.py` | +98 / -0 | MODIFIED (additive only) |
| `tests/test_mtf_buy_observability.py` | +539 | NEW (focused tests) |
| **Total** | **+924 / -0** | strict additive |

PR additions: 926 / deletions: 0 / files: 3 / mergeable: MERGEABLE

---

## MTF Code Path (authoritative branch map)

The MTF combination lives in `analyze_multi_timeframe()` (line 2351+ in `src/core/smart_bot.py`). It evaluates daily and hourly independently, then combines:

### Daily signal evaluation
```python
if sma_fast_daily > sma_slow_daily and rsi_daily < self.rsi_buy_threshold:
    daily_signal = "BUY"
elif sma_fast_daily < sma_slow_daily and rsi_daily > self.rsi_sell_threshold:
    daily_signal = "SELL"
# else daily_signal = None  → HOLD
```

### Hourly signal evaluation
```python
if sma_fast_hourly > sma_slow_hourly and rsi_hourly < self.rsi_buy_threshold:
    hourly_signal = "BUY"
elif sma_fast_hourly < sma_slow_hourly and rsi_hourly > self.rsi_sell_threshold:
    hourly_signal = "SELL"
# else hourly_signal = None  → HOLD
```
Hourly is computed only if `df_hourly is not None and len(df_hourly) >= self.sma_slow`. Otherwise `hourly_indicators is None` and the helper sees `hourly_data_available=False`.

### MTF combination (the branch map)
| daily | hourly | hourly_avail | final signal | final signal_strength |
|---|---|---|---|---|
| BUY | BUY | True | BUY | STRONG / MEDIUM |
| SELL | SELL | True | SELL | STRONG / MEDIUM |
| BUY | SELL | True | HOLD | CONFLICTED |
| SELL | BUY | True | HOLD | CONFLICTED |
| BUY | HOLD | True | BUY | DAILY_ONLY |
| BUY | HOLD | False | BUY | DAILY_ONLY |
| SELL | HOLD | True | SELL | DAILY_ONLY |
| SELL | HOLD | False | SELL | DAILY_ONLY |
| HOLD | BUY | True | HOLD | WEAK |
| HOLD | SELL | True | HOLD | WEAK |
| HOLD | HOLD | True | HOLD | WEAK |
| HOLD | HOLD | False | HOLD | WEAK |

After combination, BUY signals can be downgraded by:
- `enable_volume_confirmation` (volume fails → WEAK_VOLUME / HOLD)
- `enable_ai_conflict_filter` (AI disagrees → HOLD)
- `enable_vol_downgrade_filter` (volume downgrades → HOLD)
- `enable_mtf_conflict_filter` (MTF conflict → HOLD; redundant with combination block but exists)

The MTF combination outcome is independent of these filter blocks. The `multi_timeframe.mtf_outcome` / `mtf_reason` tokens describe the COMBINATION branch; the final `signal` / `signal_strength` describe the post-filter result.

---

## Persistence (exact JSON paths)

### New snapshot block
```
decision_snapshot.multi_timeframe = {
    "daily_signal": "BUY" | "SELL" | "HOLD" | null,
    "hourly_signal": "BUY" | "SELL" | "HOLD" | null,
    "hourly_data_available": bool,
    "mtf_outcome": "<stable token>",
    "mtf_reason":  "<stable token>"
}
```

### Stable outcome tokens (8)
- `AGREE_BUY`
- `AGREE_SELL`
- `CONFLICT`
- `DAILY_ONLY_BUY`
- `DAILY_ONLY_SELL`
- `HOURLY_ONLY_BUY`
- `HOURLY_ONLY_SELL`
- `NO_ACTION`

### Stable reason tokens (12, specific to executed branch)
- `daily_buy_hourly_buy_agreement`
- `daily_sell_hourly_sell_agreement`
- `daily_buy_hourly_sell_disagreement`
- `daily_sell_hourly_buy_disagreement`
- `daily_buy_hourly_hold_daily_only`
- `daily_sell_hourly_hold_daily_only`
- `daily_buy_no_hourly_data_daily_only`
- `daily_sell_no_hourly_data_daily_only`
- `daily_hold_hourly_buy_no_action`
- `daily_hold_hourly_sell_no_action`
- `daily_hold_hourly_hold_no_action`
- `daily_hold_no_hourly_data_no_action`

### Where the block is null
- `analyze_symbol` (single-timeframe path) → `multi_timeframe: null`
- Invalid-data early-exits → scratch cleared at function entry; MTF block requires `analysis.multi_timeframe=True`
- Existing top-level snapshot keys preserved (additive; schema_version stays 1)

---

## Tests

### New tests
- `tests/test_mtf_buy_observability.py` — 41 focused tests, all PASS
  - `TestMtfOutcomeTokens` (2 tests)
  - `TestMtfOutcomeAgreement` (2 parametrize cases)
  - `TestMtfOutcomeConflict` (2 tests)
  - `TestMtfOutcomeDailyOnly` (4 parametrize cases)
  - `TestMtfOutcomeHourlyOnly` (2 parametrize cases)
  - `TestMtfOutcomeNoAction` (2 tests)
  - `TestMtfOutcomeInputNormalization` (12 parametrize cases)
  - `TestMtfOutcomeHourlyDataFlag` (2 tests)
  - `TestMtfOutcomeReturnShape` (2 tests)
  - `TestSnapshotMtfBlockPresentForMtfPath` (4 tests)
  - `TestSnapshotMtfBlockAbsentForSingleTimeframe` (2 tests)
  - `TestSnapshotExistingShapePreserved` (1 test)
  - `TestScratchStateClearedOnEntry` (2 tests)
  - `TestMtfHelperImportable` (2 tests)

### Regression tests
- `tests/test_market_data_eligibility_cache.py` — 39 tests PASS (MKT-CACHE-001)
- `tests/test_smart_bot_decision_paths.py` — PASS
- `tests/test_smart_bot_score_normalization.py` — PASS
- `tests/test_smart_bot_indicators.py` — PASS
- `tests/test_smart_bot_log_path.py` — PASS

### Test counts
- 41 new MTF tests + 142 regression tests = 183 focused tests PASS
- 0 regressions

### Diff check
- `git diff --check` clean

---

## Safety

- **STRATEGY/TRADING LOGIC CHANGED = NO**
- No RSI/SMA/MACD threshold changes
- No signal combination logic changes
- No scoring formula changes
- No ranking / execution / risk / universe changes
- DB schema unchanged (JSON snapshot storage only — no new columns)
- Historical rows untouched (no backfill attempted)
- No production restart, no broker action
- Schema version remains 1 (additive only)

---

## Future Validation (after merge/deploy)

The post-deploy production sample should answer:

Among fresh valid MTF HOLDs:
- daily_signal / hourly_signal combinations:
  - HOLD / HOLD
  - HOLD / BUY
  - HOLD / SELL
  - BUY / HOLD
  - SELL / HOLD
  - BUY / SELL
  - SELL / BUY
  - BUY / BUY (volume-downgrade blocked)
  - SELL / SELL (volume-downgrade blocked)
- mtf_outcome distribution
- mtf_reason distribution within each outcome bucket
- hourly_data_available rate (how often are we missing hourly data?)
- Per-reason dominant blocker identification

This will tell us the actual dominant blocker for the ~510 historical WEAK rows.

### Implementation
- Future read path: `SELECT json_extract(decision_snapshot, '$.multi_timeframe.mtf_outcome'), COUNT(*) FROM decision_history WHERE json_extract(decision_snapshot, '$.decision.outcome') IN ('HOLD_INELIGIBLE', ...) GROUP BY 1`
- Future read path for reasons: `SELECT json_extract(decision_snapshot, '$.multi_timeframe.mtf_reason'), COUNT(*) FROM decision_history WHERE json_extract(decision_snapshot, '$.multi_timeframe.mtf_outcome') = 'NO_ACTION' GROUP BY 1`

---

## PR Boundary

Implementation stays within:
- `src/core/mtf_outcome.py` (new pure helper)
- `src/core/smart_bot.py` (snapshot builder + MTF call site)
- `tests/test_mtf_buy_observability.py` (focused tests)

No dashboard work. No cache work. No strategy work.

---

## Owner Action Required

- Review PR #105
- Approve or reject the merge
- After approval: authorize deploy via standard systemd restart sequence
- After deploy: authorize a post-deploy sample collection per "Future Validation" section above