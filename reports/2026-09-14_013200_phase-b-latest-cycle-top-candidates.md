# Phase B — Latest Cycle Funnel + Truthful Top Candidates (full archive)

**Backlog item**: Phase B — Dashboard-only slice succeeding Phase A + A.1.
**Branch**: `agent/dashboard-phase-b-latest-cycle-top-candidates`
**Mode**: Implementation reporting (touches repo content; not a merge/audit).
**Approval**: Josh's explicit "Start Phase B of the Trading Dashboard redesign" instruction (2026-09-14 01:23 UTC).

---

## 1. Read-only trace (pre-implementation facts)

### 1.1 Dashboard layout (relevant skeleton)

```
templates/dashboard.html
  Line 715:  <div id="top-tab-dashboard" class="top-tab-panel">
  Line 860:  <div class="card">  ← legacy "Top Opportunities" card heading
  Line 881:  <div class="card">  ← Recent Orders card
  Line 921:  <div id="top-tab-analytics" ...>
  Line 1036: <div id="top-tab-settings" ...>
  Line 1051: <div id="top-tab-history" ...>
```

### 1.2 Cycle funnel persistence (`/api/actionability-summary` source)

```sql
SELECT cycle_id, session_id, cycle_start, cycle_end,
       analyzed_count, strategy_eligible_count, ranked_candidate_count,
       execution_attempt_count, execution_blocked_count,
       order_submission_attempt_count, order_submitted_count,
       order_failed_count, not_attempted_count, not_attempted_reason,
       bot_version, schema_version
FROM cycle_funnel
ORDER BY cycle_start DESC LIMIT 1
```

Already on `main`. Phase B reuses this as the funnel card source. No new endpoint.

Latest live snapshot observed (`cycle_84651_2026-09-14T01-23-44`):
```
analyzed_count            = 30
strategy_eligible_count   = 0
ranked_candidate_count    = 0
execution_attempt_count   = 0
execution_blocked_count   = 0
order_submitted_count     = 0
order_failed_count        = 0
```

### 1.3 Top Opportunities (`/api/opportunities`) — legacy concept

```sql
SELECT symbol, ..., decision_snapshot, decision_schema_version, last_analyzed
FROM analyzed_stocks
ORDER BY total_score DESC LIMIT ?
```

This source is **legacy**: it ranks the most-recent analyzed row per symbol by `total_score DESC`, ignoring candidate_rank. Phase B does NOT modify it (other code paths depend on it). Phase B stops USING it from the dashboard.

### 1.4 decision_history (immutable candidate source)

```sql
SELECT id, symbol, cycle_start, session_id, decision_snapshot
FROM decision_history
WHERE cycle_id = ?
  AND json_extract(decision_snapshot, '$.ranking.candidate_rank') IS NOT NULL
ORDER BY CAST(json_extract(decision_snapshot, '$.ranking.candidate_rank') AS INTEGER) ASC,
         CAST(json_extract(decision_snapshot, '$.scoring.total_score') AS REAL) DESC
```

OBS-001 v1 rows contain:
- `ranking.candidate_rank` (filled by SCORE-002 when it actually ranks a symbol)
- `ranking.eligible_candidate_count`
- `ranking.tiebreak_basis`
- `scoring.total_score`
- `decision.outcome` ∈ {BUY_*, SELL_*, HOLD_*, SKIPPED_*}
- `strategy_eligibility.gates[]` — named; `passed` flag; `kind === 'rank'` distinguishes SCORE-002 rank entries.

Live state observed: **0 rows in `decision_history` have `ranking.candidate_rank` populated**. SCORE-002 has not produced any candidates yet. The zero-candidate state is the realistic default and must work.

### 1.5 cycle_id alignment

`cycle_funnel.cycle_id` and `decision_history.cycle_id` use the same string format. Verified aligned in the live snapshot.

### 1.6 Data-shape summary

- No match between the two tables: `cycle_funnel` is the cycle-level aggregate (no per-symbol rows); `decision_history` is the per-symbol immutable record. Phase B needed both: the funnel for the card and a new endpoint that joins the cycle_funnel counters with the per-symbol candidates/near-misses (no JOIN was implemented at the SQL level; the endpoint does a separate funnel lookup for its own purposes — the renderer calls `/api/actionability-summary` first for the funnel card, and the candidates card uses the cycle_id from that response).

### 1.7 Decision: minimal modification

- **Reuse existing endpoint**: `/api/actionability-summary` for the funnel card.
- **Add ONE endpoint**: `GET /api/cycle-candidates/{cycle_id}?near_miss_limit=N` reading from `decision_history` only.
- **Do NOT modify**: `/api/opportunities`, `/api/decision/*`, schema, SmartBot, scoring/ranking/sizing/risk/brokerage code.

---

## 2. Endpoint implementation

`dashboard.py`:

```python
@app.get("/api/cycle-candidates/{cycle_id}")
def api_cycle_candidates(cycle_id: str, near_miss_limit: int = 3):
    """OBS-001 Phase B: persistent candidate + near-miss facts for one cycle.

    Reads from `decision_history` (immutable, append-only) — never writes.
    The renderer, never the bot, derives the truth from these rows.
    """
    # ... defensive clamp on near_miss_limit ...
    # SELECT cycle_funnel where cycle_id=?  (for cycle_known + count envelope)
    # SELECT candidates   WHERE ranking.candidate_rank IS NOT NULL ORDER BY candidate_rank ASC
    # SELECT near_misses  WHERE decision.outcome='HOLD_INELIGIBLE'
    #                       AND ranking.candidate_rank IS NULL
    #                       ORDER BY scoring.total_score DESC LIMIT ?
    # Return structured envelope.
```

`cycle_known` field distinguishes "no such cycle" from "cycle exists but produced zero rows of this kind" — this matters when the UI calls the endpoint for older cycles (the funnel counter goes back to 8418 cycles total; only ~8 cycles ever produced `strategy_eligible_count > 0`).

### 2.1 SQL evidence

```sql
-- Query against the live DB to confirm the endpoint SQL is sound.
SELECT COUNT(*) FROM decision_history WHERE cycle_id = 'cycle_84651_...'
-- = 27 rows (the same cycle_funnel says analyzed_count=30; 3 rows are
-- outside decision_history for unknown reasons — bot-side quirk; the
-- endpoint returns the 27 known rows correctly).
```

### 2.2 Live contract verification

```
$ curl -s http://127.0.0.1:8000/api/cycle-candidates/cycle_DOES_NOT_EXIST
{"cycle_id":"cycle_DOES_NOT_EXIST","cycle_known":false,
 "candidates":[],"candidates_found":0,
 "near_misses":[],"near_misses_found":0,"near_miss_limit":3}

$ curl -s http://127.0.0.1:8000/api/cycle-candidates/cycle_84651_...
{"cycle_id":"cycle_84651_...","cycle_known":true,
 "session_id":84651,"cycle_start":"...","cycle_end":"...",
 "analyzed_count":30,"strategy_eligible_count":0,"ranked_candidate_count":0,
 "candidates":[],"candidates_found":0,
 "near_misses":[{"symbol":"ADIL","total_score":92.07,...},
                {"symbol":"CRUX","total_score":75.51,...},
                {"symbol":"...","...":...}],
 "near_misses_found":3,"near_miss_limit":3}
```

---

## 3. UI changes (`templates/dashboard.html`)

### 3.1 Cards layout (Dashboard tab, top to bottom)

```
Account → Trading Status (conditional) → Actions →
Positions → 📊 Latest Cycle (Phase B, NEW) →
🎯 Top Candidates (Phase B, replaces "Top Opportunities") →
Recent Orders
```

### 3.2 CSS additions (under `/* Phase B — Latest Cycle funnel card + Top Candidates */`)

```
.lc-funnel-row              flex-wrap row holding the 5 forward stages
.lc-funnel-stage            single forward stage box (dark border)
.lc-funnel-offpath          single off-path stage box (RED border + bg)
.lc-stage-num               the numeric counter inside each stage
.lc-stage-label             uppercase label below the number
.lc-funnel-arrow            ">" separator between adjacent forward stages
.lc-dropoff                 orange left-bar callout for the largest drop
.lc-cycle-meta              flex-wrap row of cycle id / start / end / bot version
.lc-cycle-empty             empty state message
.lc-view-analysis-btn       "View analysis →" link styled as a button
.lc-cand-row                Top Candidates table row (clickable)
.lc-cand-symbol             bold symbol text
.lc-cand-rank               "#N/M" rank label
.lc-cand-score              right-aligned score
.lc-cand-outcome            decision.outcome short label, color-coded
.lc-cand-meta               primary-reason line in score/outcome cell
.lc-cand-time               CT timestamp
.lc-zero-state              zero-candidate layout container
.lc-zero-headline           "0 of N strategy eligible" big text
.lc-zero-sub                "N symbols were analyzed" subtitle
.lc-nearmiss-table          high-score near-miss table
.lc-nearmiss-gates          failed gate name list (orange)
```

Plus `@media (max-width: 600px)` shrink variant.

### 3.3 JS additions

| Symbol | Purpose |
|---|---|
| `_lc_escapeHtml` | XSS-safe HTML escape (only inline JS helper, mirrors `_dt_escapeHtml`) |
| `_lc_fmtIso` | Format ISO timestamp in `America/Chicago` CT timezone |
| `_lc_outcomeColor` | Map canonical decision.outcome → color hex |
| `_lc_outcomeShort` | Replace `_` with ` ` for display |
| `_lc_renderLatestCycle(funnel)` | Build the funnel HTML from the `/api/actionability-summary` funnel object |
| `_lc_renderCandidatesTable(cands)` | Build the table rows from the `/api/cycle-candidates` candidates list |
| `_lc_renderZeroState(nearMisses)` | Build the zero-candidate HTML (headline + near-miss table) |
| `_lc_viewAnalysis(e)` | "View analysis →" click handler; switches to the Analytics tab |
| `_lc_openHistorySymbol(symbol)` | Switches to History tab, prefills `#historySearchInput`, calls `doHistorySymbolSearch()` |
| `loadLatestCycle()` | ONE-step fetch (`/api/actionability-summary`); render via `_lc_renderLatestCycle` |
| `loadTopCandidates()` | TWO-step fetch (`/api/actionability-summary` → cycle_id; then `/api/cycle-candidates/{id}`); render candidates OR zero-state |
| DOMContentLoaded wiring | `loadLatestCycle(); loadTopCandidates();` — replaces the legacy `loadOpportunities()` call |

### 3.4 Forward funnel policy (the most important semantic)

The renderer:
- Renders `Analyzed → Strategy Eligible → Ranked Candidates → Execution Attempted → Orders Submitted` in `lc-funnel-stage` boxes with `lc-funnel-arrow` separators.
- Renders `Execution Blocked (off-path)` and `Orders Failed (off-path)` in a SECOND row of `lc-funnel-offpath` boxes (red border + dark red bg + orange text).
- The 5 forward arrows live in the first row, the 2 off-path boxes live in a separate row underneath.
- This guarantees the user can NEVER confuse `Execution Blocked` (a divergent off-path outcome) for a forward funnel stage that feeds `Orders Submitted`.

### 3.5 Zero-candidate state semantics

When `candidates_found === 0`:
- Headline: "0 of N strategy eligible" (orange, attention).
- Subtitle: "N symbols were analyzed in this cycle".
- Label: "HIGH-SCORE NEAR MISSES (top 3)" — explicit label that these are NOT candidates.
- Table: top 3 `HOLD_INELIGIBLE` rows whose `ranking.candidate_rank IS NULL`, ordered by `scoring.total_score DESC`. Each row shows symbol, score, "HOLD" label, persisted `failed_strategy_gates` names.

### 3.6 Click-to-history wiring

```
<tr class="lc-cand-row" onclick="_lc_openHistorySymbol('AAPL')">
```

`_lc_openHistorySymbol('AAPL')`:
1. Find the History tab button (regex match on `history` in tab text).
2. Click it (so the tab becomes active).
3. Set `historySearchInput.value = 'AAPL'`.
4. Call `doHistorySymbolSearch()` — which is the Phase A function that fetches `/api/decision/AAPL` and runs `_dt_renderSnapshotTrace`.

This reuses Phase A's renderer with zero new code on the trace side.

---

## 4. Test file: `tests/test_dashboard_phase_b_latest_cycle_top_candidates.py`

5 classes, 28 tests:

| Class | Count | Purpose |
|---|---|---|
| `TestCycleCandidatesEndpoint` | 4 | Endpoint shape, SQL surface, near_miss_limit clamping, live zero-candidate invariant |
| `TestLatestCycleCard` | 7 | Card markers, fetch contract, no recompute, forward order, off-path separation, empty state, view-analysis navigates to Analytics |
| `TestTopCandidatesCard` | 5 | Card position, two-step fetch, rank-ASC contract, click-to-history, zero-state math |
| `TestLcRendererFidelityViaNode` | 9 | Node-eval of the actual JS, asserting rendered HTML for non-trivial scenarios + outcome color mapping |
| `TestPhaseBDoesNotBreakLegacyDashboards` | 4 | `/api/opportunities` intact, Phase A+A.1 endpoints intact, Phase A markers intact, Phase B's NEW JS does not call bot-control |

---

## 5. Test results

### 5.1 New file

```
$ .venv/bin/python -m pytest tests/test_dashboard_phase_b_latest_cycle_top_candidates.py
======================== 28 passed, 2 warnings in 2.97s ========================
```

### 5.2 Phase A + A.1 still pass (no regressions in Phase A contract)

```
$ .venv/bin/python -m pytest tests/test_dashboard_phase_a_history_symbol_trace.py tests/test_dashboard_phase_b_latest_cycle_top_candidates.py
======================== 91 passed, 2 warnings in 4.91s ========================
```

### 5.3 Full safe suite

```
$ .venv/bin/python -m pytest tests/ --ignore=tests/integration
...
============ 5 failed, 1336 passed, 83 warnings in 90.38s (0:01:30) ============
```

Pre-existing failures (unchanged from `main`):
- `tests/test_bot001_dashboard_status.py::test_template_renders_red_dot_when_alpaca_unreachable`
- `tests/test_bot002_paper_only_guard.py::test_main_py_installs_sigterm_handler`
- `tests/test_score_002_eligibility_and_ranking.py::test_score_45_all_non_score_gates_pass_yields_buy_signal`
- `tests/test_score_002_eligibility_and_ranking.py::test_score_80_all_non_score_gates_pass_yields_buy_signal`
- `tests/test_score_002_eligibility_and_ranking.py::test_min_score_buy_does_not_gate_buy_eligibility`

**NEW failures introduced by Phase B: 0.**

### 5.4 Git hygiene

```
$ git diff --check
(empty = clean)
$ git diff --stat
 dashboard.py                                                              | 110 ++++++++++++++++
 templates/dashboard.html                                                  | 695 ++++++++++++++++++...
 tests/test_dashboard_phase_a_history_symbol_trace.py                      |   9 +-
 tests/test_dashboard_phase_b_latest_cycle_top_candidates.py               | 719 +++++++++++...
```

`git diff --check` clean. No whitespace/terminator issues.

---

## 6. Live verification (headless Chromium 390 × 844 against live dashboard)

### 6.1 Latest Cycle card

```
Cycle: cycle_84677_2026-09-14T01-32-12
Started: Sep 13, 2026, 8:32:12 PM CT
Ended:   Sep 13, 2026, 8:32:21 PM CT
Bot: v2.1.0 · schema v1

30 ANALYZED → 0 STRATEGY ELIGIBLE → 0 RANKED CANDIDATES
   → 0 EXECUTION ATTEMPTED → 0 ORDERS SUBMITTED

Off-path row (separate, red bg):
   0 EXECUTION BLOCKED (OFF-PATH)    0 ORDERS FAILED (OFF-PATH)

Dropoff callout: "30 analyzed → 0 strategy eligible (largest drop)"
View analysis →  (button)
```

### 6.2 Top Candidates card (zero-state)

```
0 of 0 strategy eligible
30 symbols were analyzed in this cycle.

HIGH-SCORE NEAR MISSES (TOP 3)
SYMBOL   SCORE   OUTCOME   FAILED STRATEGY GATES
PSNL      82     HOLD      rsi_oversold
CGTL      80     HOLD      rsi_oversold, sma_uptrend
CGSM      79     HOLD      sma_uptrend
```

### 6.3 Click-to-history wiring

Click on PSNL row → tab switches to History; `#historySearchInput.value = "PSNL"`; OBS-001 decision trace sections render for PSNL.

### 6.4 Mobile 390 px

Document page `scrollWidth = 417, clientWidth = 390`. Pre-existing 27 px overflow caused by the global `.top-tabs` nav (this existed before Phase A+A.1 and Phase B; documented in the PR #81 audit archive and not in Phase B scope).

### 6.5 Console / page errors

NONE.

---

## 7. Semantic safety

- `dashboard.py`: read-only additions (one new endpoint that reads only).
- `src/`: untouched.
- Schema: untouched.
- Scoring/ranking/eligibility/sizing/risk/brokerage: untouched.
- OBS-001 persistence: untouched.
- PIPELINE-001: untouched.
- SCORE-003: untouched.
- SmartBot `smartbot-runner.service`: **active** since 2026-09-12 03:42:35 UTC across the entire Phase A + A.1 + B cycle. NOT restarted, NOT signalled.
- The `trading-dashboard.service` was restarted ONCE for live verification only — Phase B is a dashboard-only slice by definition.
- All `systemctl` actions strictly limited to the Trading Dashboard service. `dashboard.service`, `cloudflared`, `openclaw-gateway.service` remain untouched.

**SEMANTIC TRADING CHANGES: NONE.**

---

## 8. Risks and follow-ups

- The `cycle-candidates` endpoint relies on `decision_history` having rows for the cycle. Today's `cycle_funnel` records 30 analyzed, but `decision_history` only has 27 for that cycle (3 rows are missing). Phase B does not patch this asymmetry; the renderer simply renders the 27 rows that exist.
- The HIGH-SCORE NEAR MISSES section currently shows the top 3 by score. A future slice can add filter controls (e.g., by gate name).
- The renderer could later fetch cycle-level historical (not just the latest) data; Phase B stays narrow.
- Distance-from-pass math is intentionally OUT of scope for Phase B (per Josh's note).

## 9. Approval gates

- **SmartBot**: do not restart.
- **Trading Dashboard service**: restarted ONCE for live verification. Approved by Phase B's mandatory dashboard-only deployment.
- **Merge**: STOP. Wait for Josh.
- **Phase C** (or whatever follows): do not start.

## 10. Footer

- Mode: implementation reporting.
- Authoritative archive: this file.
- Commit TBD at commit time; nothing committed yet at archive time.
- Tests: 28 new (Phase B class), 63 pre-existing (Phase A + A.1), 5 pre-existing-on-main failures, 0 new regressions.
- SmartBot untouched.
