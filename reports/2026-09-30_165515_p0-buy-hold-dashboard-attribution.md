# P0 BUY→HOLD Dashboard Attribution — Implementation Archive

**Task ID:** P0 BUY→HOLD Dashboard Phase 11
**Branch:** `p0-buy-hold-dashboard-attribution` (off `main @ 4335688`)
**Head commit:** `a89e92a`
**PR:** [#107](https://github.com/jsavoy93/trading-bot/pull/107)
**Archive timestamp:** 2026-09-30 16:55 UTC
**Reporter:** trading-exec subagent
**Reporting mode:** implementation reporting (per AGENTS.md)

---

## Executive summary

Two priorities, both completed:

1. **P0-A BUY→HOLD blocker attribution UI.** Two new read-only
   endpoints (`/api/buy-blockers/summary`, `/api/buy-blockers`) and
   one new BUY→HOLD Attribution card on the BUY Eligibility tab.
   The verified P0 root cause — every analyzer-stage BUY is
   downgraded to HOLD by the post-MTF liquidity filter because
   `avg_volume < 1,000,000` — is now visible on the dashboard.

2. **P0-B Active view preservation across data refresh.** Owner-
   reported refresh bug closed: `setInterval(refresh, 30000)`
   (which called `window.location.reload()`) replaced with
   `setInterval(refreshData, 30000)` (data-only). Hash-based URL
   state for tab + range preserves the active view across manual
   browser refresh (F5).

---

## P0 Dashboard Root-Cause View

### New endpoints

`/api/buy-blockers/summary` — counts + reason buckets, no rows.

`/api/buy-blockers` — same envelope plus paginated `rows[]`
(limit 1-500, offset 0+).

Both:
- Validate `range ∈ {latest, today, 24h, 7d}` (via
  `_phase_c_range_clause`).
- Validate `cohort ∈ {post_p0_attribution, all}`.
- Reuse the existing `_phase_c_open_db` semantics via a parallel
  `_p0_open_db` helper (so a future reroute of P0-only reads can
  target a separate DB without touching Phase C readers).
- Compute `downgrade_rate_pct` and per-reason `pct` against
  `analyzer_buy_count` as the cohort denominator.

### Cohort semantics

`cohort='post_p0_attribution'` (default):
- `cycle_start >= 2026-09-30T14:10:34+00:00` (the PR #106 deploy
  boundary; mirrored from `dashboard.P0_PROSPECTIVE_BOUNDARY_UTC`).
- Only rows with populated `signal_pipeline` block counted toward
  the `analyzer_buy_count` denominator.

`cohort='all'`:
- All rows in range. Pre-PR #106 rows (no `signal_pipeline`) fall
  under `attribution_unavailable_count` — NEVER counted as
  `unknown_count` downgrades.

### Reason buckets (deterministic, read-only)

| Key | Trigger |
|---|---|
| `low_average_volume` | liq=FAILED AND `avg_volume < min_daily_volume` |
| `high_high_low_range` | liq=FAILED AND `avg_volume >= min_daily_volume` AND `high_low_pct > effective_max_high_low_pct` |
| `could_not_evaluate_fail_open` | liq=COULD_NOT_BE_EVALUATED (NOT a downgrade; surfaced separately) |
| `other_liquidity` | liq=FAILED AND neither low_vol nor high_low_pct fired |
| `non_liquidity` | downgraded AND liq != FAILED |
| `unknown` | downgraded AND no `downgrade_reason` captured (should be 0 in current runtime) |
| (internal) `passed` | liq=PASSED AND NOT downgraded → contributes to `final_buy_count`, NOT to a reason bucket |

Pure-function classifier: `_p0_classify_reason(liq_result, avg_volume,
min_volume, hl_pct, eff_max)`.

### Live verified sample (current DB at 2026-09-30 ~16:55 UTC)

```
GET /api/buy-blockers/summary?range=7d&cohort=post_p0_attribution

summary:
  analyzer_buy_count            = 51
  final_buy_count               =  0
  downgraded_count              = 51
  unknown_count                 =  0
  attribution_unavailable_count = 13029  (pre-PR #106, NOT counted)
  downgrade_rate_pct            = 100.0

reasons:
  low_average_volume         = 51 (100.0%)
  high_high_low_range        =  0
  could_not_evaluate_fail_open =  0
  other_liquidity            =  0
  non_liquidity              =  0

ranges.low_average_volume:
  applied_threshold = 1000000.0  (decision-time, NOT recomputed)
  min    = 24.5
  median = 1962.95
  max    = 80490.85

population:
  total_in_window       = 13080
  rows_with_pipeline    = 51
  rows_without_pipeline = 13029
```

Shape: identical to the prospective-validation sample
(19 → now 51 because the bot has been running longer).
Live-verification test (`TestLiveVerification`) asserts
`analyzer_buy_count >= 19` plus the hard invariants —
catches any divergence between code and the authoritative
forensic sample.

---

## Refresh bug — exact root cause + exact fix

### Root cause (exact previous code)

```
$ git show main:templates/dashboard.html | grep -n "setInterval\|window.location.reload"
3474:        setInterval(refresh, 30000);
2124:        function refresh() {
2125:            window.location.reload();
2126:        }
```

Every 30 s the browser did a full-page reload. SmartBot completing
a cycle coincided with this reload (because a successful fetch
from `/api/analysis` etc. happens during the cycle, which the
in-flight `setInterval` is unaware of), so the user-visible
symptom was: "I clicked the BUY Eligibility tab and got dumped
back to the Main Dashboard when the bot finished a cycle."

### Fix (exact new code)

```
templates/dashboard.html:
    // Refresh() is now reserved for the MANUAL Refresh button
    // only. It still does a full page reload because that's the
    // explicit, user-clicked action.
    function refresh() {
        window.location.reload();
    }

    // P0 BUY→HOLD DASHBOARD: data-only auto-refresh.
    async function refreshData() {
        await Promise.allSettled([
            typeof loadLatestCycle === 'function' ? loadLatestCycle() : null,
            typeof loadTopCandidates === 'function' ? loadTopCandidates() : null,
            typeof loadBuyFunnel === 'function' ? loadBuyFunnel() : null,
            typeof loadPhaseCAll === 'function' ? loadPhaseCAll() : null,
            typeof loadBuyBlockers === 'function' ? loadBuyBlockers() : null,
        ].filter(Boolean));
    }

    setInterval(refreshData, 30000);
```

### Navigation state model (hash-based URL)

```
templates/dashboard.html:
    function getCurrentTopTab() { ... }   // reads .top-tab.active
    function setTopTabFromHash() { ... }  // parses #tab=...&range=...
    function updateHash() {               // writes via replaceState
        const tab = getCurrentTopTab();
        const range = ...;
        window.history.replaceState(
            null, '',
            '#tab=' + tab + (range ? '&range=' + range : '')
        );
    }
```

Wired into:
- `showTopTab(tabName, btn)` — calls `updateHash()` after switching
- `loadBuyFunnel()` — calls `updateHash()` after range change
- `DOMContentLoaded` — calls `setTopTabFromHash()` (initial restore)
- `window.addEventListener('hashchange', setTopTabFromHash)` —
  back/forward navigation

**`refreshData()` does NOT call `updateHash()`.** Auto-refresh does
not touch the URL.

### Manual browser refresh (F5) preserves state

After F5, `DOMContentLoaded` runs `setTopTabFromHash()`, which
restores the active tab + range from the URL hash. Addressable
URL:
`http://host:8000/dashboard#tab=analytics&range=7d`.

---

## Acceptance Evidence

| Acceptance criterion | Proof | Result |
|---|---|---|
| New endpoints read `decision_snapshot.signal_pipeline` | `grep json_extract.*signal_pipeline dashboard.py` (5 occurrences) | PASS |
| Cohort boundary constant in code | `dashboard.P0_PROSPECTIVE_BOUNDARY_UTC = "2026-09-30T14:10:34+00:00"` | PASS |
| Range filter matches Phase C | Reuses `_phase_c_range_clause(range_name, "cycle_start")` | PASS |
| Pre-PR #106 rows reported as `attribution_unavailable_count` (NOT unknown) | `tests/test_dashboard_p0_buy_blockers.py::TestPrePR106AttributionUnavailable` | PASS |
| Refresh bug removed | `tests/test_dashboard_refresh_state.py::TestRefreshDataAutoRefresh::test_setInterval_refresh_is_gone` | PASS |
| Refresh fix wired | `tests/test_dashboard_refresh_state.py::TestRefreshDataAutoRefresh::test_setInterval_refreshData_is_present` | PASS |
| `refreshData` does NOT call `location.reload` | `tests/test_dashboard_refresh_state.py::TestRefreshDataAutoRefresh::test_refreshData_does_not_call_location_reload` | PASS |
| `refresh()` STILL calls `location.reload()` (manual button) | `tests/test_dashboard_refresh_state.py::TestRefreshDataAutoRefresh::test_refresh_function_still_calls_location_reload` | PASS |
| `updateHash` uses `replaceState` (not `pushState`) | `tests/test_dashboard_refresh_state.py::TestUrlStateHelpers::test_updateHash_uses_replaceState_not_pushState` | PASS |
| `showTopTab` calls `updateHash` | `tests/test_dashboard_refresh_state.py::TestUrlStateHelpers::test_showTopTab_calls_updateHash` | PASS |
| `loadBuyFunnel` calls `updateHash` | `tests/test_dashboard_refresh_state.py::TestUrlStateHelpers::test_loadBuyFunnel_calls_updateHash` | PASS |
| `refreshData` does NOT call `updateHash` | `tests/test_dashboard_refresh_state.py::TestUrlStateHelpers::test_refreshData_does_not_call_updateHash` | PASS |
| `hashchange` listener registered | `tests/test_dashboard_refresh_state.py::TestUrlStateHelpers::test_hashchange_listener_registered` | PASS |
| `DOMContentLoaded` calls `setTopTabFromHash` | `tests/test_dashboard_refresh_state.py::TestUrlStateHelpers::test_domcontentloaded_calls_setTopTabFromHash` | PASS |
| BUY→HOLD card in buyfunnel tab | `tests/test_dashboard_refresh_state.py::TestBuyBlockersCardWiring::test_buy_blockers_card_in_buyfunnel_panel` | PASS |
| Endpoints called from JS | `tests/test_dashboard_refresh_state.py::TestBuyBlockersCardWiring::test_buy_blockers_endpoints_called` | PASS |
| XSS-safe text interpolation | `tests/test_dashboard_refresh_state.py::TestBuyBlockersCardWiring::test_xss_safe_text_interpolation` | PASS |
| Live verification: `analyzer_buy_count >= 19` | `tests/test_dashboard_p0_buy_blockers.py::TestLiveVerification::test_live_summary_reproduces_verified_shape` | PASS |
| Live verification: `final_buy_count == 0` | same | PASS |
| Live verification: `unknown_count == 0` | same | PASS |
| Live verification: `low_average_volume count >= 19` | same | PASS |
| 19 low-volume rows reproduce verified stats | `tests/test_dashboard_p0_buy_blockers.py::TestLowVolumeSummaryMath::test_19_low_volume_rows_reproduce_verified_stats` | PASS |
| Mixed buckets classify correctly | `tests/test_dashboard_p0_buy_blockers.py::TestMixedSummaryMath::test_mixed_buckets_classify_correctly` | PASS |
| High-low rows classified correctly | `tests/test_dashboard_p0_buy_blockers.py::TestHighLowClassification::test_high_low_rows_classified_correctly` | PASS |
| Range filtering 24h vs 7d | `tests/test_dashboard_p0_buy_blockers.py::TestRangeFiltering::test_24h_filters_out_old_rows` | PASS |
| Pagination | `tests/test_dashboard_p0_buy_blockers.py::TestDetailRowsEndpoint::test_pagination` | PASS |

**Total: 29 new tests, all PASS.** Existing tests unchanged.

---

## Tests run

```
.venv/bin/python -m pytest tests/test_dashboard_p0_buy_blockers.py -v
  7 PASSED, 0 FAILED

.venv/bin/python -m pytest tests/test_dashboard_refresh_state.py -v
  22 PASSED, 0 FAILED

.venv/bin/python -m pytest tests/test_dashboard_p0_buy_blockers.py \
                         tests/test_dashboard_refresh_state.py \
                         tests/test_obs_001_phase_a_decision_snapshot.py \
                         tests/test_p0_buy_hold_liquidity_attribution.py -v
  100 PASSED, 0 FAILED  (the new + adjacent regression subset)
```

### Pre-existing failures (NOT introduced by this PR)

Confirmed on `main @ 4335688` BEFORE this branch's changes:

- `tests/test_settings_service.py`:
  - `test_dashboard_batch_with_invalid_value_persists_nothing`
    — `AttributeError: module 'dashboard' has no attribute 'api_update_settings'`
  - `test_dashboard_fully_valid_batch_persists_all_normalized_values`
    — same
  - `test_dashboard_api_route_returns_http_400_for_invalid_schema_input`
    — `405 == 400` (test asserts 400 but endpoint returns 405)

- `tests/test_dashboard_phase_c_obs_analytics.py::TestGateAggregationAppliedFilter`:
  - 6 failures (gate aggregation atomicity tests; same baseline
    failure count was noted in the PR #106 semantic review).

- `tests/test_bot001_dashboard_status.py::test_template_renders_red_dot_when_alpaca_unreachable`
  — template assertion fails.

These baselines were verified by `git stash`-ing the branch's
changes, running each failing test, and observing the same
failure on `main @ 4335688`. None of these are introduced by this
PR.

---

## Safety review

| Item | Decision | Evidence |
|---|---|---|
| Live trading | NOT enabled | `ALPACA_BASE_URL=default(paper)`; no live env touched |
| SmartBot logic | UNCHANGED | No `src/` changes; no `main.py` restart |
| Trading strategy | UNCHANGED | No strategy files touched |
| `settings_service.py` | UNCHANGED | No settings schema change |
| DB schema | UNCHANGED | `signal_pipeline` is OPTIONAL/ADDITIVE (PR #106); this PR only READS it |
| Historical rows | NOT TOUCHED | No UPDATE/INSERT to existing rows |
| `setInterval` cadence | UNCHANGED (30000ms) | Only the target function changed (`refresh` → `refreshData`) |
| Service restart | NOT triggered | No systemd action; no SmartBot PID change |
| Manual Refresh button | PRESERVED | `refresh()` still calls `window.location.reload()` |
| OpenClaw / cloudflared | UNCHANGED | No config touched |

---

## Files changed

```
$ git diff --stat a89e92a^..a89e92a
dashboard.py                         | +634 -0
templates/dashboard.html             | +503 -2
tests/test_dashboard_p0_buy_blockers.py | +NEW 257 lines (7 tests)
tests/test_dashboard_refresh_state.py | +NEW 367 lines (22 tests)
MENTOR.md                            | +168 -0
ITERATION_PROGRESS_LOG.md            | +124 -0
6 files changed, 2435 insertions(+), 2 deletions(-)
```

`git diff --check`: clean (no conflicts).

---

## PR

- **Branch:** `p0-buy-hold-dashboard-attribution`
- **Base:** `main`
- **Head commit:** `a89e92a`
- **PR #:** [#107](https://github.com/jsavoy93/trading-bot/pull/107)
- **URL:** https://github.com/jsavoy93/trading-bot/pull/107
- **State:** OPEN — NOT MERGED, NOT DEPLOYED
- **Auto-merge:** disabled
- **Server-side diff (via `gh pr diff 107`):**
  - 6 files changed, 2435 insertions, 2 deletions
  - Additions: dashboard.py (+634), templates/dashboard.html (+503),
    tests/test_dashboard_p0_buy_blockers.py (+new), tests/test_dashboard_refresh_state.py (+new),
    MENTOR.md (+168), ITERATION_PROGRESS_LOG.md (+124)
  - Deletions: 2 lines in templates/dashboard.html (the buggy
    `setInterval(refresh, 30000)` line — replaced by the data-only
    `setInterval(refreshData, 30000)`)

---

## Parked items (NOT in this PR)

- Strategy threshold review (STRAT-002+) — owner has not authorized
- MACD 17/18 — out of scope
- Hourly data availability — out of scope
- Unrelated dashboard performance — out of scope
- `tests/test_settings_service.py` 3 pre-existing failures — out of
  scope (would need a separate API contract change)
- `tests/test_dashboard_phase_c_obs_analytics.py` 6 pre-existing
  failures — out of scope (gate aggregation atomicity, closed
  PR #106 semantic review)

---

## Backlog IDs covered by this PR

- **P0 BUY→HOLD DASHBOARD ATTRIBUTION** — Phase 11 (new)
- Includes:
  - P0-A: BUY→HOLD blocker attribution UI (endpoints + card)
  - P0-B: Active view preservation across data refresh (bug fix)

---

## Risks

1. **Live verification test depends on real DB.** If the live DB
   is unavailable or the bot has not produced ≥19 analyzer BUYs
   yet (which would be a regression in itself), the test will fail
   or be skipped. The test uses `pytest.skip` if `trading_bot.db`
   does not exist, and the live assertions use `>= 19` (not `== 19`)
   for `analyzer_buy_count` to allow for future growth.

2. **Hash-based URL state assumes no other tabs in the same
   origin.** If the dashboard is ever framed or shared with other
   state, the `#tab=...&range=...` hash could conflict. Currently
   the dashboard is the only thing on this origin — no risk.

3. **`refreshData` may increase dashboard load during normal
   operation.** Each 30s cycle now hits 5 endpoints (loadLatestCycle,
   loadTopCandidates, loadBuyFunnel, loadPhaseCAll, loadBuyBlockers)
   where it previously did only a full reload. This is by design —
   we trade one big request for several smaller ones, all in
   parallel via `Promise.allSettled`.

4. **Forward-compat with future top tabs.** `getCurrentTopTab()`
   hard-codes the 5 tab labels. If a new tab is added, this function
   MUST be updated, or the new tab will land users back on the
   dashboard after F5. This is documented in the JSDoc.

---

## Recommendation

APPROVE for merge. SmartBot unchanged, trading-dashboard unchanged,
DB schema unchanged. This is a strictly-additive observability
PR + a deterministic UI bug fix. No follow-up deployment action
required — the dashboard service hot-reloads.

---

## Next Action (requires owner authorization)

1. Owner reviews PR #107 + REPORT.md + this archive.
2. Owner merges (operator-controlled; no auto-merge).
3. No SmartBot restart required. No dashboard service restart
   required (hot-reloads file changes).
4. Operator validates on dashboard:
   - Open BUY Eligibility tab → BUY→HOLD Attribution card shows
     4 tiles + reason counts + detail table.
   - Change range selector → URL hash updates
     (`#tab=buyfunnel&range=7d`).
   - Wait 30 s (or trigger SmartBot cycle) → tiles refresh but
     the tab stays on BUY Eligibility and the URL hash is unchanged.
   - Press F5 → same tab + range restored from URL hash.
5. Spot-check `tests/test_dashboard_p0_buy_blockers.py::TestLiveVerification`
   passes against the live DB on the deployed branch (it does
   against the current DB as of this archive).