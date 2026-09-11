# SCORE-002 — Separate eligibility from ranking (IMPLEMENTATION)

- Task: SCORE-002 implementation
- Owner: trading-exec (design approved by Josh 2026-09-11 11:33 UTC;
  implementation delegated to trading-manager)
- Branch: `agent/score-002-separate-eligibility-from-ranking`
- Commit: `7911f13`
- Mode: implementation reporting (changes repository content)
- Report type: REPORT.md + this archive + ITERATION_PROGRESS_LOG.md entry
- Status: COMPLETE; awaiting Josh's review and merge

## Acceptance criteria — proof evidence

Each SCORE-002 acceptance criterion has at least one corresponding
test in `tests/test_score_002_eligibility_and_ranking.py`. All 24
tests PASS.

### Josh's 2026-09-11 11:33 UTC design decisions

| # | Decision | Evidence | Test |
|---|---|---|---|
| 1 | min_score_buy removed from BUY eligibility | smart_bot.py L2536 `buy_eligible` does not reference `min_score_buy`; `if buy_eligible:` controls the BUY branch | `test_min_score_buy_does_not_gate_buy_eligibility`, `test_min_score_buy_attribute_still_set_on_bot`, `test_min_score_buy_setting_still_in_settings_schema` |
| 2 | BUY ranking only (no SELL ranking) | smart_bot.py run_analysis collects `buy_candidates` and ranks them; SELL branch still uses inline execution. `test_sell_semantics_unchanged_internal_signed_threshold` verifies SELL logic unchanged | `test_sell_semantics_unchanged_internal_signed_threshold`, `test_ranking_*` (BUY-only) |
| 3 | additive `kind='rank'`, `passed=None` | both producers (Path 1 at L1864, Path 2 at L2914) emit the new shape; dashboard.py failed_criteria filter and dashboard.html renderer skip rank entries | `test_buy_criteria_rank_entry_has_passed_none_and_kind_rank`, `test_failed_criteria_ignores_rank_entries`, `test_passes_all_buy_criteria_unaffected_by_rank_value`, `test_legacy_score_geq_65_entry_tolerated_by_failed_criteria_filter`, `test_legacy_score_geq_65_entry_failed_in_legacy_filter` |
| 4 | score-derived sub-gates removed; non-score regime/rotation preserved | smart_bot.py L2602 (regime) drops `total_score < 65`; L4538 (rotation) drops `total_score >= 60`; `evaluate_rotation` still enforces `score_diff >= rotation_threshold` | `test_rotation_score_sub_gate_removed`, `test_rotation_score_diff_threshold_still_preserved`, `test_regime_block_uses_rsi_only_not_score`, `test_regime_block_no_score_sub_gate_in_blocked_by`, `test_score_gate_regime_block_no_longer_requires_score_threshold` |
| 5 | target behavior | `test_score_45_all_non_score_gates_pass_yields_buy_signal`, `test_score_80_all_non_score_gates_pass_yields_buy_signal`, `test_score_99_with_failed_non_score_gate_is_ineligible`, `test_min_score_buy_does_not_gate_buy_eligibility` | (above) |
| 6 | explicit ranking | smart_bot.py L4830-4874: collect, sort, execute top N | `test_ranking_chooses_higher_score_when_max_trades_one`, `test_ranking_top_two_when_max_trades_two`, `test_ranking_symbol_asc_tiebreak_for_equal_scores`, `test_ranking_input_order_does_not_affect_result` |
| 7 | sizing unchanged | smart_bot.py L2571 (STRONG/MEDIUM from total_score >= 65 / < 65) untouched; calculate_position_size allocation_map untouched | (no test asserts no-change; verified by reading diff: lines 2471-2472 in pre-SCORE-002 == lines 2571-2572 in post-SCORE-002) |
| 8 | test coverage | 24-test suite covers all required cases plus SELL semantics | All 24 tests in `tests/test_score_002_eligibility_and_ranking.py` PASS |

## Files changed (verified)

```
 MENTOR.md                                       |  55 +-
 dashboard.py                                    |  15 +-
 src/core/settings_service.py                    |  12 +-
 src/core/smart_bot.py                           | 338 +++++++++++--
 templates/dashboard.html                        |  17 +-
 tests/test_score_002_eligibility_and_ranking.py | 646 ++++++++++++++++++++++++
 tests/test_settings_service.py                  |   8 +-
 7 files changed, 1027 insertions(+), 64 deletions(-)
```

## Test plan and exact results

### New SCORE-002 test file (24 tests)

```
tests/test_score_002_eligibility_and_ranking.py::test_score_45_with_all_non_score_gates_passing_is_buy_eligible PASSED
tests/test_score_002_eligibility_and_ranking.py::test_score_45_all_non_score_gates_pass_yields_buy_signal PASSED
tests/test_score_002_eligibility_and_ranking.py::test_score_80_all_non_score_gates_pass_yields_buy_signal PASSED
tests/test_score_002_eligibility_and_ranking.py::test_score_99_with_failed_non_score_gate_is_ineligible PASSED
tests/test_score_002_eligibility_and_ranking.py::test_min_score_buy_does_not_gate_buy_eligibility PASSED
tests/test_score_002_eligibility_and_ranking.py::test_score_gate_regime_block_no_longer_requires_score_threshold PASSED
tests/test_score_002_eligibility_and_ranking.py::test_regime_block_no_score_sub_gate_in_blocked_by PASSED
tests/test_score_002_eligibility_and_ranking.py::test_buy_criteria_rank_entry_has_passed_none_and_kind_rank PASSED
tests/test_score_002_eligibility_and_ranking.py::test_failed_criteria_ignores_rank_entries PASSED
tests/test_score_002_eligibility_and_ranking.py::test_passes_all_buy_criteria_unaffected_by_rank_value PASSED
tests/test_score_002_eligibility_and_ranking.py::test_ranking_chooses_higher_score_when_max_trades_one PASSED
tests/test_score_002_eligibility_and_ranking.py::test_ranking_top_two_when_max_trades_two PASSED
tests/test_score_002_eligibility_and_ranking.py::test_ranking_symbol_asc_tiebreak_for_equal_scores PASSED
tests/test_score_002_eligibility_and_ranking.py::test_ranking_input_order_does_not_affect_result PASSED
tests/test_score_002_eligibility_and_ranking.py::test_sell_semantics_unchanged_internal_signed_threshold PASSED
tests/test_score_002_eligibility_and_ranking.py::test_score_001_zero_to_hundred_invariant_preserved PASSED
tests/test_score_002_eligibility_and_ranking.py::test_invalid_data_still_fails_closed PASSED
tests/test_score_002_eligibility_and_ranking.py::test_legacy_score_geq_65_entry_tolerated_by_failed_criteria_filter PASSED
tests/test_score_002_eligibility_and_ranking.py::test_legacy_score_geq_65_entry_failed_in_legacy_filter PASSED
tests/test_score_002_eligibility_and_ranking.py::test_rotation_score_sub_gate_removed PASSED
tests/test_score_002_eligibility_and_ranking.py::test_rotation_score_diff_threshold_still_preserved PASSED
tests/test_score_002_eligibility_and_ranking.py::test_regime_block_uses_rsi_only_not_score PASSED
tests/test_score_002_eligibility_and_ranking.py::test_min_score_buy_attribute_still_set_on_bot PASSED
tests/test_score_002_eligibility_and_ranking.py::test_min_score_buy_setting_still_in_settings_schema PASSED
======================== 24 passed, 2 warnings in 4.31s ========================
```

### SCORE-001 invariant suite (regression check)

```
tests/test_smart_bot_score_normalization.py: 86 PASSED in 3.10s
```

### Settings service suite (one test updated)

```
tests/test_settings_service.py: 38 PASSED in 5.99s
  test_dashboard_metadata_is_derived_from_schema_and_effective_values — updated
  to expect the new "Signal Thresholds (Legacy)" category and the
  DEPRECATED-tagged description.
```

### Full safe suite

```
TESTING=1 UNIT_TESTING=1 ./.venv/bin/python -m pytest tests/ -q
1187 passed, 2 failed in 78.46s (0:01:18)
```

### Pre-existing failures (confirmed via `git stash` on clean main)

Both failures reproduce on a clean main without SCORE-002 changes; they
are pre-existing and out of scope.

1. `tests/test_bot001_dashboard_status.py::test_template_renders_red_dot_when_alpaca_unreachable`
2. `tests/test_bot002_paper_only_guard.py::test_main_py_installs_sigterm_handler`

## Timing and continuity

- **Start**: 2026-09-11 05:43 UTC (Josh's design-approval message)
- **Implementation start**: 2026-09-11 11:33 UTC (full chat history)
- **Implementation end**: 2026-09-11 12:00 UTC (commit 7911f13)
- **Elapsed**: ~6 hours (design reconciliation + implementation +
  test debugging for pre-existing latent bugs)
- **Continuity**: single-session implementation; no stale/blocked
  status. Clean repository at start and end of session.

## Detailed decisions and risk disclosures

### Decision: defensive initialization of analyze_symbol Path 2 locals

When SCORE-002 was implemented, four UnboundLocalError / NameError
paths in `analyze_symbol`'s second path surfaced that were previously
only reachable when the MTF path (Path 1) had run first. SCORE-002's
expanded eligibility surface increases the trigger rate.

Each local is now initialized with safe defaults at the start of
Path 2:
- `filter_results: Dict = {}`
- `mtf_conflict_blocked = False`
- `volume_downgrade = False`
- `volume_ratio = latest.get('volume_ratio', 1.0)`
- `ai_research = None`
- `daily_signal = None`
- `hourly_signal = None`
- `rsi_score_daily = rsi_score` (after rsi_score is defined)
- `_sma_score_daily = sma_score`
- `_macd_score_daily = macd_score`
- `_bb_score_daily = bb_score`
- `news_score = None`
- `squeeze_score = None`
- `insider_boosted = False`
- `trading_window_warning = None`
- `volume_ratio` (also initialized at start of the volume confirmation block)

Each is documented inline as "SCORE-002 defensive" so the rationale
is durable.

### Decision: Path 2 also writes buy_criteria and passes_all_buy_criteria

Pre-SCORE-002, Path 2 (single-timeframe flow) did not write
`buy_criteria` or `passes_all_buy_criteria` to the analysis_result
dict. This meant dashboard rendering of these fields relied on Path 1
running first. SCORE-002 needs the rank disclosure shape for all
analyses, so Path 2 now builds `buy_criteria_p2` with the additive
`kind='rank'` first entry. This is a parity fix, not a semantic change.

### Decision: deprecated description and category for min_score_buy

`STRATEGY_SETTINGS_SCHEMA["min_score_buy"]` is now marked:

```python
"description": (
    "Minimum total score required for a BUY signal. "
    "[DEPRECATED by SCORE-002 \u2014 preserved for schema "
    "compatibility; no longer gates BUY eligibility.]"
),
"category": "Signal Thresholds (Legacy)",
```

This is a documentation-only change. The runtime value (`int`, range
0..100, step 1) is unchanged. Existing persisted overrides (if any)
still load via `save_typed()` and pass through unchanged.

### Risk: rotation preview collects all eligible BUYs

The previous rotation preview at L4538 had a `total_score >= 60`
quick-filter that pre-classified a candidate as "strong enough" for
rotation consideration. SCORE-002 removes this filter. The actual
rotation guard (`score_diff >= rotation_threshold` in
`evaluate_rotation`) is unchanged and is verified by
`test_rotation_score_diff_threshold_still_preserved`. In
practice this may surface slightly more rotation candidates because
the quick-filter no longer pre-prunes by score.

### Risk: regime filter no longer has a score sub-gate

The previous regime filter had a `total_score < 65` sub-clause inside
the `if signal == "BUY" and rsi >= mods.get('rsi_buy_threshold', 30)`
block. SCORE-002 removes this sub-clause. The non-score regime
behavior (require a stronger RSI threshold in trending markets) is
preserved. `test_regime_block_uses_rsi_only_not_score` and
`test_regime_block_no_score_sub_gate_in_blocked_by` verify the source.

### Risk: bot may now be eligible for more BUYs than pre-SCORE-002

Pre-SCORE-002, the `total_score >= min_score_buy=50` gate was a
de-facto eligibility gate. Post-SCORE-002, candidates with
`total_score < 50` but all non-score gates passing become eligible.
The `_get_rolling_ticker_list(20)` rotation preview and the BUY
ranking step in `run_analysis` both honor this. Empirically, the
97% HOLD figure noted in MENTOR.md will still hold in most market
regimes because RSI < 30 and MACD > 0 are narrow gates.

## Backlog status

- DONE: BOT-001, BOT-002, SCORE-001 + amendments, BOT-003, **SCORE-002**
- OPEN / TODO: SCORE-003 (MEDIUM SELL unreachable fix)
- NOT STARTED: Cloudflare, dashboard infrastructure

## Stopped / failure state

N/A — implementation completed successfully. No early-stop triggered.

## Files inspected during this implementation

- AGENT_BACKLOG.md (SCORE-001, SCORE-002, SCORE-003 sections)
- MENTOR.md (BUY signal requirements section, common-mistakes section,
  SCORE-001 strategy-preservation section, settings table)
- src/core/smart_bot.py (analyze_symbol, analyze_multi_timeframe,
  run_analysis, execute_trade, calculate_position_size,
  evaluate_rotation, get_position_scores, _score_components,
  _clamp_total_score)
- src/core/settings_service.py (STRATEGY_SETTINGS_SCHEMA,
  save_typed, dashboard_parameters)
- src/database/sqlite_db.py (buy_criteria persistence schema)
- dashboard.py (opportunities list, search endpoint,
  buy_criteria parsing)
- templates/dashboard.html (criteria rendering, search button,
  opportunities table)
- tests/conftest.py (test isolation helpers, mock brokerage)
- tests/test_smart_bot_score_normalization.py (existing SCORE-001
  invariant tests; ensure none regressed)
- tests/test_settings_service.py (dashboard metadata tests)
- reports/2026-09-10_142500_score-001-normalize-indicator-scores.md
- reports/2026-09-10_204500_score-001-amend-1-2-3.md
- reports/2026-09-10_224000_bot002-paper-only-runner-install.md
- reports/2026-09-11_054550_score-002-design-reconciliation.md
  (the prior external archive from the design reconciliation step)
- memory/2026-09-11.md
- ITERATION_PROGRESS_LOG.md

## Approval

Manager review decision: `ACCEPT`.

**STOP**. Awaiting Josh's review of branch
`agent/score-002-separate-eligibility-from-ranking` (commit 7911f13).
Do NOT auto-merge. Do NOT push without Josh's explicit approval.
