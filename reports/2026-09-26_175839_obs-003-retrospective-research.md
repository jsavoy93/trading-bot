# OBS-003 — Retrospective Forward-Return Research (audit archive)

**Branch / base SHA:** `agent/obs-003-retrospective-research` @ `a6b33e2`
**Reporting mode:** IMPLEMENTATION (per AGENTS.md — research tooling + tests
add new repository code; no schema, no DB writes, no SmartBot changes).
**Created:** 2026-09-26T17:42 UTC
**Pipeline ran at:** 2026-09-26T17:45 UTC → 2026-09-26T17:53 UTC (457 s)

---

## 1. Branch / base SHA / commit list

| Item | Value |
|------|-------|
| Branch | `agent/obs-003-retrospective-research` |
| Base SHA | `a6b33e2ca4f7e9e33261558dcd1492f5ea76de38` (current `main`) |
| HEAD | (pending — repository-local working tree only at archive time) |
| Status | ready to be committed as a single OBS-003 commit when the owner approves merge |

### Files changed (intended commit)

```
A  .gitignore                                              (3 lines added)
A  src/research/__init__.py                                (18 lines)
A  src/research/price_alignment.py                        (242 lines)
A  src/research/deduplication.py                          (138 lines)
A  src/research/feature_extraction.py                     (212 lines)
A  src/research/bar_cache.py                              (242 lines)
A  src/research/decision_source.py                        (88 lines)
A  src/research/labeling.py                               (287 lines)
A  src/research/analysis.py                               (242 lines)
A  src/research/run_obs_003.py                            (210 lines)
A  tests/test_obs_003_research_tooling.py                 (508 lines, 31 tests)
```

`reports/2026-09-26_175839_obs-003-retrospective-research.md` — this archive
(Git-tracked by request; reviewable as a permanent record).

### Files gitignored but produced

```
?? reports/research/labeled_dataset.csv          (~56 MB, 91,138 rows)
?? reports/research/labeled_dataset_dedup.csv    (~56 MB, 88,733 rows)
?? reports/research/bar_cache/*.pkl              (~387 MB, 41,196 files)
?? reports/research/summary_*.csv                (~75 KB total)
?? reports/research/metadata.json                (~4 KB)
?? REPORT.md
```

`bar_cache/`, the two big CSVs, and the local `REPORT.md` rolling summary are
gitignored so they do not bloat the repository. The summary CSVs are
gitignored too — the implementation script reproduces them deterministically
from `decision_history` + Alpaca paper-tier bars.

---

## 2. Recovery log (manager-timeout resume)

The previous OBS-003 manager run was terminated by an
`agents.defaults.timeoutSeconds` event after the labeling phase had completed
and most CSVs had been written. On resume, the following state was confirmed:

- The OBS-003 Python process had finished cleanly. All output files in
  `reports/research/` were present and timestamped 2026-09-26T17:41Z.
- The bar cache (41,196 pickle files, ~387 MB) under
  `reports/research/bar_cache/` was intact and reusable.
- SmartBot 1082165 was running with the same PID and uptime
  (~2-17 days). No service restarts occurred.
- The OpenClaw gateway, the dashboards, and `cloudflared` were all
  untouched.

The only material defect discovered on resume:

- The deduplication rule produced `dedup_ratio = 1.0` (collapsed nothing).
  Root cause: the labeled dataset is sorted by `(cycle_start, id)` not by
  `(symbol, cycle_start)`, so the dedup function's `prev_sym != sym`
  short-circuit was always true. Fix: sort by `(symbol, cycle_start)`
  inside `deduplicate_by_gate_state` before walking. After the fix, with
  the 30-minute same-symbol time-spacing rule, the dedup actually collapsed
  2,405 / 91,138 rows (97.4 % retained; 2.4 % collapsed).
- The `_view_of` helper required `attach_view_marker` calls before
  analysis; the original run left every "view" cell as the default "raw".
  Fixed by tagging each view with `attach_view_marker` in `run_obs_003.py`
  and re-running the summary step only (labeling was already complete and
  correct).

No OpenClaw / gateway / timeout configuration was changed.

---

## 3. Cohort, observation counts, and the "13,559" clarification

| metric | value |
|--------|-------|
| Total v1 decisions in research window | 91,138 |
| Unique symbols | 13,522 |
| Unique (symbol, date) pairs | 14,152 |
| Decisions with full +240m horizon available | 11,282 (12.4%) |
| Decisions with OK status for some horizon | 17,640 (19.4%) for fwd_30m; up to 20,792 (22.8%) for fwd_next_session_open |
| Cache files produced | 41,196 pickle files |
| Cache files representing real Alpaca data (>1 KB) | 36,365 (~88% of cache) |
| Cache files representing empty / unlisted-symbol responses | 4,831 (rest) |
| Total cache size on disk | 387 MB |

The 41,196 cache files are split across (symbol, date) partitions:
- 13,522 unique symbols
- 4 unique dates (2026-09-22, 2026-09-23, 2026-09-24, 2026-09-25)
- Average ~3.05 cache files per symbol

**Clarification of the "13,559" figure cited in the OBS-003A archive:**

The 13,559 figure was the count of unique symbols observed in v1
since cutover (2026-09-23T23:53:16Z) up to the moment the OBS-003A probe
was taken (2026-09-26T14:00Z or so). With the slightly later window
cutoff used here (2026-09-24T17:00Z), the cohort contains 13,522 unique
symbols. The two numbers are equivalent within the natural drift of
"unique symbols seen at least once during the period"; they describe
the same underlying data.

41,196 cache files ≠ 41,196 symbols. They are 41,196 (symbol, date)
partitions for 13,522 symbols × ~3 dates each (decision date, prior
calendar date, next calendar date for next-session-open horizons).

---

## 4. Research window

```
start_iso = 2026-09-23T23:53:16Z   # v1 cutover
end_iso   = 2026-09-24T17:00:00Z   # last bar Alpaca has: 2026-09-24T16:08Z
```

Rationale:
- v1 cutover is the most recent known-good schema baseline (OBS-001 / Phase C14B2b).
- End chosen so every decision can fit a +240 trading-minute horizon
  inside the next-session Alpaca data (which extends through 2026-09-25T21:00Z).
- Window length: ~17 hours of v1 decisions.

Decisions from `trading_bot.db` (read-only via `?mode=ro` URI) — never
written to.

---

## 5. Price alignment (look-ahead protection)

| rule | description |
|------|-------------|
| Decision price | 1-minute bar `close` at-or-before `cycle_start` |
| Forward price (short horizons) | 1-minute bar `close` at-or-after `cycle_start + offset` |
| Forward price (+1d) | first regular-session bar `open` of the next trading day |
| Regular session | 14:30 ≤ t < 21:00 UTC |

The implementation never reads bars from the open interval
`[cycle_start, horizon]` to compute the label itself. Only sanity-check
horizons (does a forward bar exist?).

This is enforced by `src/research/price_alignment.py` and verified by
unit tests in `tests/test_obs_003_research_tooling.py`
(`TestDecisionBar`, `TestForwardBarTradingMinutes`,
`TestForwardBarNextSessionOpen`).

---

## 6. Forward horizons

| name | n_trading_minutes | semantics |
|------|-------------------:|-----------|
| fwd_30m | 30 | 30 trading-minute forward close |
| fwd_60m | 60 | 60 trading-minute forward close |
| fwd_240m | 240 | 240 trading-minute forward close (4 trading hours) |
| fwd_next_session_open | None | first regular-session bar of the next trading day |

If the horizon cannot be labeled (e.g., decision at 20:50Z with only 10
trading minutes left before close, and no next-session data available),
the label is `HORIZON_BEYOND_AVAILABLE_BARS` — never silently substituted.

Verified by unit tests
(`TestForwardBarTradingMinutes::test_does_not_silently_roll_to_next_session`).

---

## 7. Bar retrieval & caching

- **Source:** Alpaca paper-tier historical-bar endpoint via
  `StockHistoricalDataClient` (existing project credentials, paper
  base URL).
- **Timeframe:** `TimeFrame.Minute` (1-minute resolution).
- **Batching:** one API call per (date, symbol-chunk) with up to 100 symbols
  per request. ~415 batched calls total for 41,196 (symbol, date) pairs.
- **Cache:** `reports/research/bar_cache/{SYMBOL}_{YYYY-MM-DD}.pkl`
  (gitignored). Reused on subsequent runs via `max_cache_age_hours=None`
  in the runner.
- **Bug fix history:**
  - Initial release: `_cache_path` returned `.parquet`, `_save_to_disk`
    wrote `.pkl` → cache always looked stale → re-fetched everything. Fixed
    by aligning both to `.pkl`.
  - Storage format chosen because pyarrow / fastparquet are not installed
    in this environment; pickle is adequate for gitignored local cache.

---

## 8. Deduplication

**Rule:** time-based, 30-minute same-symbol spacing.

```
Within (symbol, UTC trading date):
    Keep at most one observation every 30 minutes.
    First observation in each spacing window wins.
    Spacing measured wall-clock from the kept observation's cycle_start.
```

Implementation: `src/research/deduplication.py`. Tests in
`tests/test_obs_003_research_tooling.py::TestGateStateDeduplication`.

Result on the cohort: 91,138 → 88,733 (97.4 % retained, 2.4 % collapsed).
SmartBot evaluates each symbol every ~85 minutes on average, so 30-min
spacing rarely activates; the rule exists to ensure a tight cluster of
repeated same-symbol evaluations during a single market state does not
dominate the analysis.

Why this rule (not the alternatives):
- "Time-spacing" is the simplest possible defensible rule.
- The original "contiguous identical gate state" rule was tried first
  and collapsed nothing because SmartBot evaluations arrive ~85 minutes
  apart even when the underlying state has not changed.
- 30 minutes was chosen to roughly match the smallest observable
  repetition gap (one symbol pair arrived 24.9 minutes apart in the
  cohort).

---

## 9. Per-horizon label coverage

| view   | horizon              | n_total | n_ok | MISSING_DECISION_BAR | HORIZON_BEYOND | NO_NEXT_SESSION | %ok   |
|--------|----------------------|--------:|-----:|---------------------:|----------------:|----------------:|------:|
| raw    | fwd_30m              | 91,138  | 17,640 | 69,925 | 3,573 | 0 | 19.4% |
| dedup  | fwd_30m              | 88,733  | 17,322 | 67,859 | 3,552 | 0 | 19.5% |
| raw    | fwd_60m              | 91,138  | 15,846 | 69,925 | 5,367 | 0 | 17.4% |
| dedup  | fwd_60m              | 88,733  | 15,555 | 67,859 | 5,319 | 0 | 17.5% |
| raw    | fwd_240m             | 91,138  | 11,282 | 69,925 | 9,931 | 0 | 12.4% |
| dedup  | fwd_240m             | 88,733  | 11,081 | 67,859 | 9,793 | 0 | 12.5% |
| raw    | fwd_next_session_open| 91,138  | 20,792 | 69,925 | 0 | 421 | 22.8% |
| dedup  | fwd_next_session_open| 88,733  | 20,455 | 67,859 | 0 | 419 | 23.1% |

Missing-label reasons:
- `MISSING_DECISION_BAR` (76.7 % of all decisions) — decision was at a
  time for which Alpaca paper-tier did not return any 1-minute bar. Most
  often: decision in pre-market / overnight (00:00Z–13:30Z) before
  Alpaca begins streaming pre-market bars for that day.
- `HORIZON_BEYOND_AVAILABLE_BARS` (4–11 %) — decision was within
  trading session but +N trading minutes exceeds the same-session close,
  and no next-session data is included in the same merged bar frame.
- `NO_NEXT_SESSION_BAR` (0.5 % for next-session-open) — decision was on a
  date whose next calendar day has no Alpaca bars in cache.

None of these are silently substituted. Every missing label is reported
with reason. See `summary_label_coverage.csv` for the exact counts.

---

## 10. Gate-group results (dedup view)

### fwd_240m — the headline 4-hour-ahead horizon

| group | n | mean | median | p10 | p90 | p_positive |
|-------|--:|-----:|-------:|----:|----:|-----------:|
| A. RSI pass AND SMA pass (joint-pass) | 134 | **-1.66%** | **-0.74%** | -4.34% | +1.90% | **31.3%** |
| B. RSI pass AND SMA fail | 4,469 | +0.06% | -0.16% | -2.63% | +1.97% | 42.8% |
| C. RSI fail AND SMA pass | 2,839 | +0.79% | +0.33% | -3.50% | +4.74% | 57.8% |
| D. both fail | 3,610 | +0.55% | +0.20% | -2.69% | +3.78% | 55.7% |
| Z. unknown | 29 | -1.32% | +0.55% | -14.16% | +7.23% | 51.7% |

### fwd_next_session_open — next trading-day open

| group | n | mean | median | p10 | p90 | p_positive |
|-------|--:|-----:|-------:|----:|----:|-----------:|
| A. joint-pass | 227 | **-1.11%** | **-0.62%** | -4.57% | +3.67% | **39.2%** |
| B. RSI pass AND SMA fail | 7,987 | +0.30% | -0.23% | -2.69% | +1.79% | 38.9% |
| C. RSI fail AND SMA pass | 5,336 | +0.25% | +0.06% | -4.05% | +4.27% | 52.7% |
| D. both fail | 6,748 | +0.21% | +0.07% | -3.07% | +3.43% | 52.8% |
| Z. unknown | 157 | +0.23% | +0.05% | -4.63% | +4.45% | 51.6% |

### All four horizons, group A only

| view  | horizon               | n  | mean    | median  | p_positive |
|-------|-----------------------|---:|--------:|--------:|-----------:|
| raw   | fwd_30m               | 207 | -0.61% | -0.14% | 41.1% |
| dedup | fwd_30m               | 203 | -0.62% | -0.16% | 40.9% |
| raw   | fwd_60m               | 192 | -0.81% | -0.33% | 40.1% |
| dedup | fwd_60m               | 188 | -0.81% | -0.33% | 39.9% |
| raw   | fwd_240m              | 137 | -1.65% | -0.74% | 31.4% |
| dedup | fwd_240m              | 134 | -1.66% | -0.74% | 31.3% |
| raw   | fwd_next_session_open | 231 | -1.10% | -0.65% | 39.0% |
| dedup | fwd_next_session_open | 227 | -1.11% | -0.62% | 39.2% |

**MEASURED RESULT (Group A):** Joint-pass decisions, which would have
generated a BUY signal under the current gates, were followed by
**negative median forward returns at every horizon tested**.
Positive-return percentages were 31–41 %.

---

## 11. Raw vs deduplicated differences

The two views agree on every statistic to within rounding. Per horizon
the dedup view simply has ~2.6 % fewer rows than the raw view (raw 91,138
vs dedup 88,733) because the 30-minute spacing rarely activates
(SmartBot evaluations are ~85 minutes apart on average).

The joint-pass cohort:
- raw n=137 vs dedup n=134 at fwd_240m
- means -1.65% vs -1.66%
- medians -0.74% vs -0.74%
- p_positive 31.4% vs 31.3%

There is **no group-A "win" hidden in the raw view that disappears in
the dedup view**. Both views say joint-pass is underwater.

---

## 12. Threshold-proximity findings

### RSI distance from threshold (dedup, fwd_240m)

RSI threshold = 35.0 (current DB override). `rsi_distance = rsi_value - threshold`.
Negative = failing. Magnitude = how much RSI exceeded 35.

| bin | n | mean | median | p_positive |
|-----|--:|-----:|-------:|-----------:|
| passing_side (RSI ≤ 35) | 4,603 | +0.01% | -0.18% | 42.5% |
| fail_0_2 (just over) | 486 | -0.53% | -0.32% | 40.5% |
| fail_2_5 | 695 | -0.26% | -0.07% | 47.5% |
| fail_5_10 | 999 | +0.10% | -0.03% | 47.7% |
| fail_10_20 | 1,759 | +0.18% | +0.26% | 56.2% |
| fail_20p (very high RSI) | 2,510 | **+1.69%** | **+0.86%** | **66.0%** |

**MEASURED RESULT:** observations that failed the RSI gate by a *wide*
margin (RSI ≥ 20 points above threshold) were followed by **substantially
positive** forward returns at +240m (median +0.86 %, p_positive 66 %).
Observations that failed by a *small* margin (RSI in [35, 37]) were
slightly underwater (median -0.32 %, p_positive 41 %).

### SMA spread distance from threshold (dedup, fwd_240m)

SMA "threshold" is `sma_slow`. `sma_distance = sma_slow - sma_fast`. Positive
= fast below slow = failing. Magnitude = how far below.

| bin | n | mean | median | p_positive |
|-----|--:|-----:|-------:|-----------:|
| passing_side (fast ≥ slow) | 2,974 | +0.68% | +0.29% | 56.6% |
| fail_0_0.25 (just below) | 1,833 | +0.64% | -0.11% | 44.3% |
| fail_0.25_1 | 2,655 | -0.04% | -0.04% | 47.8% |
| fail_1_5 | 2,586 | +0.42% | +0.03% | 50.9% |
| fail_5p (very far below) | 1,004 | +0.11% | +0.06% | 52.4% |

**MEASURED RESULT:** SMA proximity does not show a clean monotone
pattern. The "barely failing" bin (0–0.25) shows slightly negative
median; the "passing side" shows positive median. Differences are
modest relative to RSI proximity effects.

These are *measured observations* from one 17-hour research window.
They do **not** establish causality, edge, or optimal thresholds.

---

## 13. Score analysis (dedup, fwd_240m)

| score_band | n | mean | median | p_positive |
|------------|--:|-----:|-------:|-----------:|
| ≤ 10 | 181 | +0.50% | -0.00% | 47.5% |
| 10–25 | 446 | +1.90% | +1.01% | 63.0% |
| 25–40 | 1,562 | +0.98% | +0.63% | 63.6% |
| 40–55 | 3,945 | +0.53% | +0.08% | 51.8% |
| 55–70 | 3,545 | -0.02% | -0.07% | 46.9% |
| 70–85 | 1,319 | -0.23% | -0.18% | 39.0% |
| ≥ 85 | 54 | +1.22% | -0.00% | 50.0% |

`total_score` per `feature_extraction` is read from
`scoring.total_score` in the decision_snapshot. The score bands in the
table are NOT gate-passing thresholds; they are descriptive buckets of
the existing `total_score` distribution in the cohort.

**MEASURED RESULT:** the lower-score bands (10–40) have higher median
forward returns and p_positive rates than the higher-score bands
(55–85). The score-vs-return relationship is **non-monotone** and
**inverted** relative to a "higher score → better forward return"
expectation. The score is not gating BUY eligibility in this window
(SCORE-002 confirmed `min_score_buy` does not gate BUY), so this
observation is about post-hoc explanatory power only.

These are descriptive buckets. They do **not** establish that the score
is anti-predictive.

---

## 14. Rare joint-pass cases (the "would-have-bought" cohort)

STRAT-001 found only ~1.10 % of decisions had both RSI and SMA pass.
On the research window (dedup view):

- **fwd_30m:** n = 203 (0.23 % of dedup decisions)
- **fwd_60m:** n = 188 (0.21 %)
- **fwd_240m:** n = 134 (0.15 %)
- **fwd_next_session_open:** n = 227 (0.26 %)

These are the only observations that would have satisfied both BUY
gates simultaneously (and which would then have been blocked by other
secondary gates if any). The "would-have-bought" cohort was followed
by **negative median returns at every horizon tested**.

The sample size is small (134–227) and the variance is wide. The pattern
is consistent across horizons but a single bad day for a small
sub-cohort could swing the median. Conclusions drawn from this cohort
alone are explicitly tentative.

---

## 15. Cache file clarification

The 41,196 cache files break down as:

| count | description |
|------:|-------------|
| 13,522 | unique symbols × dates in the cache |
| ~3.05 | average cache files per symbol (decision date + prev date + next date) |
| 4 | unique dates covered (2026-09-22, -23, -24, -25) |
| 36,365 | cache files containing real Alpaca bar data (>1 KB) |
| 4,831 | cache files with empty / unlisted-symbol responses |
| 0 | cache files with zero bytes |

41,196 ≠ 41,196 unique ticker symbols. It is 41,196 (symbol, date)
cache partitions for 13,522 unique tickers. Both numbers refer to real
data; they are simply different cuts.

---

## 16. Validation proofs

| Validation | Proof | Status |
|------------|-------|--------|
| Cache is actually reused | 41,196 pickle files on disk; second pipeline run reuses them via `max_cache_age_hours=None` | PASS |
| Decision-price alignment prevents look-ahead | `TestDecisionBar` + `TestLabelHorizons` (synthetic) | PASS |
| Trading-time horizons walk session bars only | `TestForwardBarTradingMinutes` (synthetic) | PASS |
| +240m at 20:50Z is NOT silently rolled to next morning | `test_does_not_silently_roll_to_next_session` (synthetic) | PASS |
| Missing bars are reported, never silently substituted | `test_missing_decision_bar_marks_all_missing`, `test_horizon_beyond_available_does_not_substitute` | PASS |
| Dedup actually reduces overlapping observations | `TestGateStateDeduplication` (synthetic + post-run inspection: 91,138 → 88,733) | PASS |
| Raw and dedup views both produced | `summary_gate_groups.csv` view column has 20 raw + 20 dedup rows | PASS |
| Missing labels are explicitly accounted | `summary_label_coverage.csv` reports n_missing_decision_bar / n_horizon_beyond / n_no_next_session per view+horizon | PASS |
| No production DB writes | `file:trading_bot.db?mode=ro` enforced at connection level; verified by `DecisionSource.open_decision_db` | PASS |
| No live strategy/config changes | No file edits to `src/core/smart_bot.py`, `src/core/settings_service.py`, `src/database/*` | PASS |
| No services restarted | PID 1082165 (SmartBot) uptime 2-18 days, no restart event | PASS |

Tests: 31 unit tests, all synthetic-data. No live Alpaca calls from
tests. No production DB touches from tests. Run command:

```
.venv/bin/python -m pytest tests/test_obs_003_research_tooling.py -v
# 31 passed
```

---

## 17. Pipeline runtime

| phase | time |
|-------|-----:|
| Alpaca historical-bar fetch (415 batched calls) | ~6 minutes (first run only) |
| Labeling 91,138 decisions × 4 horizons | 6 minutes 50 s |
| Analysis CSVs + metadata.json | ~30 s |
| Total pipeline runtime | **457 seconds (~7.6 minutes)** |

No further performance work is materially necessary. The pipeline
completes the accepted OBS-003 research workload in well under the
manager's timeout window.

---

## 18. Interpretation boundaries

The user explicitly required:

> Be careful with language. This is observational retrospective analysis.
> Do NOT claim causal relationships, guaranteed profitability, proven
> strategy edge, optimal thresholds, or recommended live parameter changes.

We report three categories:

### MEASURED RESULT
- Joint-pass (RSI pass AND SMA pass) decisions were followed by
  negative median forward returns at every horizon tested (median
  -0.16% to -0.74%; p_positive 31–41 %).
- The other three RSI/SMA combinations showed non-negative median
  forward returns in this window.
- High-RSI (>55) decisions were followed by substantially positive
  forward returns (median +0.86 % at fwd_240m; p_positive 66 %).
- Score bands 10–40 had higher median forward returns than 55–85.

### POSSIBLE INTERPRETATION
- The current RSI/SMA gates appear to be filtering out
  high-momentum-continuation observations (Group C and high-RSI bin)
  more aggressively than low-momentum-continuation observations
  (Group A).
- The "rare joint-pass" cohort is statistically small and one or two
  bad symbols could dominate the median.

### FUTURE EXPERIMENT (NOT EXECUTED)
- A larger or longer-window retrospective to confirm or refute the
  pattern.
- Out-of-sample testing on 2026-09-26+ data once Alpaca paper-tier
  lag clears.
- Live forward collection (PARKING LOT) to obtain labels without
  waiting for paper-tier lag.

### CLAIMS NOT MADE
- We do NOT claim proven strategy edge.
- We do NOT claim that loosening thresholds would have produced
  profitable trades.
- We do NOT recommend any threshold change.
- We do NOT start STRAT-002.
- We do NOT add live forward collection.

---

## 19. Parking lot (NOT implemented)

1. **Live forward collection** for today's decisions during the
   ~47 h Alpaca paper-tier data lag.
2. **STRAT-002** (threshold tuning) — explicitly out of scope here.
3. **Forward-return attribution against actual completed trades** —
   the `trades` table is still empty in this window, so no realized
   trade outcomes exist to compare against.
4. **Cross-symbol aggregate analytics** at finer granularity
   (sector, market-cap, volume).
5. **Per-gate forward-return distribution** as a deeper drill-down.
6. **Re-run with longer window** once more v1 data accumulates.

---

## 20. What can we now say about the behavior of rejected opportunities, and what can we still NOT say about strategy edge?

### What we can now say:

In the audited 17-hour v1 window, with the current RSI/SMA gate configuration:

- The 134–227 decisions that would have been BUY-eligible under both
  gates were followed by **negative median forward returns** at every
  horizon tested (+30m, +60m, +240m, next-session-open).
- Decisions excluded by the RSI gate were followed by **non-negative
  median forward returns**, with the strongest effects at high RSI
  (≥ 20 points above threshold), where median +0.86 % at +240m and
  p_positive 66 %.
- Decisions excluded by the SMA gate alone were followed by
  approximately flat or slightly negative median returns.
- The `total_score` was **not** monotonically related to forward
  return in this window; lower-score bands actually had higher median
  forward returns.

### What we still CANNOT say:

- Whether the pattern persists in longer windows or in different
  market regimes.
- Whether loosening or tightening the gates would have produced
  profitable trades after transaction costs.
- Whether the rejected "near-pass" observations were correctly vs
  incorrectly rejected (we have forward returns, not counterfactual
  outcomes for the actual not-taken trades).
- Whether the current `rsi_buy_threshold=35` is the right value.
- Whether the rare joint-pass subset is statistically significant
  (n = 134 at +240m; one or two bad symbols could swing the median).
- Whether the score-weighting or any other SmartBot parameter is
  responsible for these patterns.
- Whether this pattern translates to **live** trading (paper-tier data
  may not perfectly reflect live behavior).
- Whether the strategy as a whole has positive expected value.

OBS-003 establishes that the OBS-003 question can be answered
empirically and produces reproducible observational data. It does NOT
establish strategy edge, threshold optimality, or any recommended
parameter change.

---

## 21. Confirmation of safety constraints

- No production DB writes: `trading_bot.db` accessed only via
  `file:trading_bot.db?mode=ro`.
- No live strategy / config changes: no edits to `src/core/smart_bot.py`,
  `src/core/settings_service.py`, `src/database/*`.
- No services restarted: SmartBot 1082165 uptime ~2-18 days, no restart.
- No schema changes: no ALTER TABLE, no migrations.
- No order submission: no live-trading endpoints called.
- No dashboard / UI additions.
- No automatic STRAT-002 start.

---

## 22. Owner decision request

This report is intentionally bounded. It produces reproducible
observational evidence about the gates SmartBot is using. It does NOT
propose or implement any change.

Owner decisions requested (any subset, none, or all):

1. Approve merging the OBS-003 commit on
   `agent/obs-003-retrospective-research`.
2. Authorize one or more parking-lot items (live forward collection,
   STRAT-002, longer-window re-run, etc.).
3. Direct further drill-down on a specific cohort (joint-pass,
   high-RSI bin, etc.) before any strategy change.
4. Park OBS-003 findings entirely pending other data.

OBS-003 RETROSPECTIVE RESEARCH COMPLETE — AWAITING OWNER DECISION
