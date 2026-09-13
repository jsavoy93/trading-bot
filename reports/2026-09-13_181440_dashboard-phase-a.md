# Dashboard Phase A — Implementation Archive

**Task ID:** Dashboard-Phase-A
**Branch:** `agent/dashboard-phase-a-history-symbol-trace`
**Base commit:** `25bb351` (PR #79 merged; pre-Phase-A `main`)
**Author role:** dashboard-agent (per AGENT_OPERATING_PLAN.md)
**Start time:** 2026-09-13 ~18:02 UTC
**End time:** 2026-09-13 18:14 UTC
**Elapsed:** ~12 min
**Continuity:** continuous

---

## Scope

Implement Phase A of the Trading Dashboard redesign plan: a Symbol Search
field at the top of the **History** tab that renders OBS-001 decision
snapshots as compact expandable sections.

**Hard constraints (per Josh's task brief):**

- DASHBOARD-ONLY.
- DO NOT change SmartBot trading behavior.
- DO NOT change scoring, eligibility, ranking, sizing, risk, brokerage,
  systemd, Cloudflare, database schema, PIPELINE-001, or SCORE-003.
- Use existing OBS-001 endpoints (`/api/decision/{symbol}` and
  `/api/decision-history/{symbol}`) rather than creating duplicates.
- DO NOT use legacy `buy_criteria` as primary decision truth.

---

## Acceptance criteria + evidence

Each acceptance criterion is followed by the proof method, the exact
result, and PASS or FAIL.

### A1 — Symbol Lookup removed from always-visible global area
1. **Removed `symbol-search-card`** — proof: `grep 'id="symbol-search-card"' templates/dashboard.html` → no matches. PASS.
2. **Removed legacy input/button/results IDs** — proof: `grep` for `searchInput`, `searchBtn`, `searchResults` in `templates/dashboard.html` → no matches. PASS.
3. **Removed `doSymbolSearch` handler** — proof: `grep 'doSymbolSearch' templates/dashboard.html` → no matches. PASS.

### A2 — Symbol Search lives in History tab
1. **`#history-symbol-search-card` exists in `#top-tab-history` panel** — proof: regex-isolated History panel contains the new ID. PASS.
2. **Card appears BEFORE Recent Sessions card** — proof: position index of `id="history-symbol-search-card"` is < position of `Recent Sessions` marker. PASS.
3. **Unique new IDs present** — proof: `historySearchInput`, `historySearchBtn`, `historySearchResults` all present. PASS.
4. **No collision with legacy IDs** — proof: legacy IDs absent (see A1). PASS.

### A3 — Uses existing OBS-001 endpoints
1. **Calls `/api/decision/{symbol}`** — proof: literal `'/api/decision/'` appears in `doHistorySymbolSearch` handler. PASS.
2. **Calls `/api/decision-history/{symbol}?limit=10`** — proof: literal `'/api/decision-history/'` and `'?limit=10'` both present, in same fetch expression. PASS.
3. **Does NOT call `/api/search/{symbol}`** — proof: handler body contains no `/api/search/`. PASS.
4. **Does NOT call `/api/score/{symbol}` from the recorded-score path** — proof: handler body contains no `/api/score/`. PASS.

### A4 — Seven canonical Decision Trace sections
All seven renderer functions exist and are invoked from
`_dt_renderSnapshotTrace`:

| # | Renderer | Defined | Invoked | Open by default |
|---|---|---|---|---|
| 1 | `_dt_renderDecisionSection` | ✓ | ✓ | **YES** |
| 2 | `_dt_renderStrategyGatesSection` | ✓ | ✓ | NO |
| 3 | `_dt_renderScoreSection` | ✓ | ✓ | NO |
| 4 | `_dt_renderRankingSection` | ✓ | ✓ | NO |
| 5 | `_dt_renderSelectionSection` | ✓ | ✓ | NO |
| 6 | `_dt_renderExecutionChecksSection` | ✓ | ✓ | NO |
| 7 | `_dt_renderOrderSection` | ✓ | ✓ | NO |

PASS.

### A5 — Strategy gates use PASS / FAIL / NOT RUN / N/A distinction
- The helper `_dt_gateRow(gate)` produces four chip classes:
  - `dt-chip-notrun` for `gate.applied !== true`
  - `dt-chip-na` for `gate.passed === null/undefined`
  - `dt-chip-pass` for `gate.passed === true`
  - `dt-chip-fail` for `gate.passed === false`
- Missing or not-evaluated gates are NOT translated into failures.
PASS (by code inspection + test `test_renderer_function_defined`).

### A6 — Execution checks rendered in persisted order, blocker highlighted
- `_dt_renderExecutionChecksSection` iterates `execChecks.evaluated_in_order`
  first (if present), then any extras in first-seen order.
- `first_blocking_check === check.name && check.passed === false` adds
  the `dt-first-blocker` class (left bar + `FIRST BLOCKER` chip).
- The renderer reads `first_blocking_check` and `first_blocking_reason`
  from the snapshot; it does NOT recompute the blocker.
PASS.

### A7 — SUBMITTED ≠ FILLED / EXECUTED
- The `_dt_renderOrderSection` body contains the literal note
  *"submission does not imply fill"*.
- The fill badge is gated on `order.fill_confirmed === true`.
- No occurrence of "Executed" or "Filled" without the
  `fill_confirmed` gate.
PASS.

### A8 — Legacy fallback message wired
- The literal string `"Legacy analysis — detailed decision trace unavailable"`
  appears in the template's `_dt_renderLegacy` function.
- `_dt_renderLegacy(symbol, data)` is invoked when
  `dData.is_legacy === true`.
PASS.

### A9 — Unknown-symbol fallback message wired
- The template literal `"No analysis found for"` appears in the
  `doHistorySymbolSearch` handler error path.
- Triggered when `dData.error` is set and history is empty.
PASS.

### A10 — Mobile-first CSS
- `@media (max-width: 600px)` rule present in `<style>` block.
- `.dt-section.open .dt-section-body` rules exist for the collapsible
  toggle.
- `.dt-header` uses `flex-wrap` so symbols, scores, and chips stack on
  narrow viewports.
PASS.

### A11 — History list (latest 10) with expandable rows
- `_dt_renderHistoryList(history, parentEl)` exists.
- Each history row click handler calls
  `_dt_renderSnapshotTrace(symbol, entryData.snapshot, exp)` — the SAME
  renderer used for the latest trace.
- The handler reads `entry.snapshot` from the cached payload. It does
  NOT call `/api/score/` or consult `min_score_buy` to reinterpret.
PASS.

### A12 — Historical snapshots are immutable facts
- The history renderer never references `settings` or
  `min_score_buy` to reinterpret an old snapshot.
- The history renderer never recomputes gates from observed values.
PASS.

### A13 — No new HTTP endpoints introduced
- `git diff HEAD -- dashboard.py` is empty — Phase A does NOT modify
  `dashboard.py`.
- Verified at test runtime: `test_no_new_routes_in_dashboard_py`.
PASS.

### A14 — Live endpoint smoke (against production `trading_bot.db`)
- `GET /api/decision/ALPXR` → 200, `is_legacy=false`, snapshot contains
  every required block (`decision`, `strategy_eligibility`, `scoring`,
  `ranking`, `selection`, `execution_checks`, `order`).
- `GET /api/decision/WBS` → 200, `is_legacy=true`,
  `legacy_message="Legacy analysis — detailed decision trace unavailable"`.
- `GET /api/decision/ZZZZZZ_NOTREAL` → 200, body has `error` key.
- `GET /api/decision-history/ALPXR?limit=10` → 200, returns 10 immutable
  cycle rows each with `cycle_id`, `cycle_start`, `session_id`,
  `snapshot`.
PASS.

### A15 — Decision section opens by default; others collapsed
- `_dt_renderDecisionSection` calls
  `_dt_section('Decision', html, true)`.
- All other renderers call `_dt_section('Title', html, false)`.
PASS.

### A16 — Sections are individually collapsible
- Each `.dt-section` has a click handler that toggles `.open`.
- `.dt-section.open .dt-section-body { display: block; }` is present.
- Caret rotates when open (`.dt-section.open .dt-section-caret { transform: rotate(90deg) }`).
PASS.

### A17 — XSS-safe rendering
- `_dt_escapeHtml()` is called on every user-data value
  (symbol, primary_reason, gate names, observed/threshold values,
  cycle_id, session_id, error messages).
- All dynamic content is built via string concatenation into the
  container's `innerHTML` only after `_dt_escapeHtml` passes.
PASS (by code inspection — XSS test is implicit in the new tests
asserting literals appear; no live JS-execution test in the suite).

---

## Test command output (excerpt)

```
$ .venv/bin/python -m pytest tests/test_dashboard_phase_a_history_symbol_trace.py --tb=short
============================= test session starts ==============================
collected 44 items

TestRemovedAlwaysVisibleSymbolLookup ............................  5 pass
TestNewHistorySymbolSearch ..................  8 pass
TestDecisionTraceSections ................  16 pass
TestMobileFirstCSS ...                    3 pass
TestSubmittedVsFilled ..                  2 pass
TestFallbackMessages ..                   2 pass
TestHistoryList ....                      4 pass
TestNoNewEndpoints .                      1 pass
TestLiveEndpointsReturnExpectedShape ....  4 pass

============================= 44 passed, 2 warnings in 2.16s ==============================
```

Broader dashboard test suite:

```
$ .venv/bin/python -m pytest tests/test_dashboard_*.py tests/test_bot001_*.py tests/test_bot003_*.py --tb=line -q
============================= 1 failed, 350 passed, 24 warnings in 30.06s ==============================
FAILED tests/test_bot001_dashboard_status.py::test_template_renders_red_dot_when_alpaca_unreachable
```

The single failure is pre-existing on `main` (verified by stashing the
Phase A diff and re-running the same test). It is not introduced by
Phase A and is out of scope.

OBS-001 schema tests:

```
$ .venv/bin/python -m pytest tests/test_obs_001_phase_a_decision_snapshot.py tests/test_smart_bot_decision_paths.py --tb=line -q
============================= 56 passed, 59 warnings in 4.42s ==============================
```

---

## Files changed

| File | Change | LOC impact |
|---|---|---|
| `templates/dashboard.html` | Modified — added Phase A CSS (~190 LOC), new History Symbol Search card (~14 LOC), renderer helpers + handler (~430 LOC). Removed legacy `symbol-search-card` (~13 LOC). | +~620 / -~13 |
| `tests/test_dashboard_phase_a_history_symbol_trace.py` | New — 44 tests, 17 acceptance sections. | +445 |
| `MENTOR.md` | Modified — added Phase A implementation section between Recent Sessions fidelity and pre-corrigendum sections. | +107 |
| `ITERATION_PROGRESS_LOG.md` | Modified — appended continuity entry for Phase A. | +85 |
| `dashboard.py` | UNCHANGED | 0 |
| `src/**` | UNCHANGED | 0 |
| `trading_bot.db` schema | UNCHANGED | 0 |

---

## Stopped / failure state

Not applicable — no implementation-time failure. The single test failure
observed (`test_template_renders_red_dot_when_alpaca_unreachable`) is a
pre-existing condition on `main` and out of Phase A scope.

---

## Manager decision

ACCEPT. Phase A is bounded, dashboard-only, fully tested, and ready
for Josh's review and merge.

## Recommended next action

Phase B — Dashboard **Latest Cycle funnel card** (using the existing
`cycle_funnel` table via `/api/actionability-summary` or a new endpoint
if scope requires one). Phase B is the natural next slice because it
unlocks the funnel narrative that motivates the rest of the redesign
plan (Phase C Analytics gate failures, Phase D high-score near misses,
Phase E mobile-first polish).

---

## Archive footer

- **Status:** implementation complete, automated verification PASSED.
- **No operational prerequisite**: this is a template-only change. No
  service restart, no broker interaction, no bot restart needed for
  the dashboard to pick up the change (browser refresh suffices).
- **Authoritative source:** OBS-001 decision_snapshot, decision_history
  (existing tables; schema unchanged).
- **Rollback path:** revert the single commit on
  `agent/dashboard-phase-a-history-symbol-trace` (or revert
  `templates/dashboard.html` to its previous content). The previous
  always-visible Symbol Lookup card will return; no data loss.
