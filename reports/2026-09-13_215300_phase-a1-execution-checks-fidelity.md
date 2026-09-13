# Phase A.1 — Execution Checks Renderer Fidelity (full archive)

**Backlog item**: Phase A.1 — Dashboard-only follow-up to PR #81.
**Branch**: `agent/dashboard-phase-a1-execution-checks-fidelity`
**Mode**: Implementation reporting (touches repo content; not a merge/audit/review pass).
**Approval**: Josh explicit request, controlled workflow.

---

## 1. Read-only trace

### 1.1 Persisted ALPXR evidence (the live regression case)

`SELECT decision_snapshot FROM analyzed_stocks WHERE symbol='ALPXR'` (via the OBS-001 v1 row currently in the DB) yields `execution_checks`:

```
first_blocking_check:   'position_existence_check'
first_blocking_reason: 'Position check raised: {"code":40410000,"message":"position does not exist"}'

evaluated_in_order (9 items):
  margin_check, pending_order_check, cooldown_check,
  position_concentration_check, sector_concentration_check,
  correlation_check, beta_check, buying_power_check,
  quantity_post_sizing_check

checks[] (10 items, all with applied ∈ {True, False}, passed ∈ {True, False, None}):
  margin_check                            applied=True  passed=True
  pending_order_check                     applied=True  passed=True
  position_existence_check                applied=True  passed=False   ← THE BLOCKER (not in evaluated_in_order)
  cooldown_check                          applied=False passed=None
  position_concentration_check            applied=False passed=None
  sector_concentration_check              applied=False passed=None
  correlation_check                       applied=False passed=None
  beta_check                              applied=False passed=None
  buying_power_check                      applied=False passed=None
  quantity_post_sizing_check              applied=False passed=None
```

`position_existence_check` is the unique persisted check that is NOT in `evaluated_in_order`. That single absence is what triggered the bug.

### 1.2 Pre-fix renderer behaviour

`_dt_renderExecutionChecksSection` (located at `templates/dashboard.html` line ~1657) had this iteration logic:

```js
const checks = Array.isArray(execChecks.checks) ? execChecks.checks : [];
const order = Array.isArray(execChecks.evaluated_in_order) ? execChecks.evaluated_in_order : [];
const byName = {};
const seenOrder = [];
for (let i = 0; i < checks.length; i++) {
    const c = checks[i];
    if (!c || !c.name) continue;
    if (!byName[c.name]) { seenOrder.push(c.name); }
    byName[c.name] = c;
}
// ...emit "First blocker" summary header...
const ordered = order.length ? order.slice() : seenOrder;     // ← falls back to seenOrder ONLY WHEN order is empty
for (let i = 0; i < ordered.length; i++) {
    const name = ordered[i];
    const c = byName[name];
    if (!c) continue;
    // ...emit one dt-row per name...
}
```

This is the root cause: with a non-empty `order`, `ordered = order.slice()` and `position_existence_check` is silently skipped. There is no "append extras" pass.

In practice, for ALPXR, the rendered output was:
- "First blocker: position_existence_check" (summary header — read directly from `first_blocking_check`).
- 9 dt-rows for the 9 names in `evaluated_in_order`.
- The 10th persisted check (`position_existence_check`) was missing from the list.

### 1.3 Acknowledged constraint

There was no opportunity to test this bug during Phase A unit testing because no OBS-001 v1 snapshot was reachable in test fixtures at the time with `first_blocking_check ∉ evaluated_in_order`. ALPXR's SELL_BLOCKED_DYNAMIC snapshot is the first such case in 12,712 OBS-001 v1 rows, surfaced only by the live dashboard verification of PR #81.

---

## 2. Renderer fix

### 2.1 Minimal 11-line change (`templates/dashboard.html`)

Replaced:

```js
// Render in declared evaluation order, then any extras.
let html = '';
if (execChecks.first_blocking_check) {
    html += _dt_row('First blocker',
        '<span class="dt-chip dt-chip-blocker">' + _dt_escapeHtml(execChecks.first_blocking_check) + '</span>' +
        (execChecks.first_blocking_reason ? ' ' + _dt_escapeHtml(execChecks.first_blocking_reason) : ''));
} else if (checks.length > 0) {
    html += _dt_row('First blocker', '<span style="color:#2ea043;">none — all checks passed</span>');
}
const ordered = order.length ? order.slice() : seenOrder;
```

With:

```js
let html = '';
if (execChecks.first_blocking_check) {
    html += _dt_row('First blocker',
        '<span class="dt-chip dt-chip-blocker">' + _dt_escapeHtml(execChecks.first_blocking_check) + '</span>' +
        (execChecks.first_blocking_reason ? ' ' + _dt_escapeHtml(execChecks.first_blocking_reason) : ''));
} else if (checks.length > 0) {
    html += _dt_row('First blocker', '<span style="color:#2ea043;">none — all checks passed</span>');
}
// Render in declared evaluation order first; then append any
// persisted checks[] that were not declared in evaluated_in_order,
// preserving their persisted checks[] order. This guarantees the
// rendered list contains every persisted check exactly once, even
// when the first_blocking_check falls outside evaluated_in_order.
const ordered = order.length ? order.slice() : [];
const orderedSet = new Set(ordered);
for (let i = 0; i < seenOrder.length; i++) {
    const name = seenOrder[i];
    if (!orderedSet.has(name)) {
        ordered.push(name);
        orderedSet.add(name);
    }
}
```

### 2.2 Why this is the minimal correct change

| Invariant | Mechanism |
|---|---|
| Render `evaluated_in_order` first, in declared order | `ordered = order.slice()` |
| Append unseen names from `checks[]` | Iteration over `seenOrder` with `Set` membership check |
| Preserve persisted `checks[]` order for extras | `seenOrder` itself was built by iterating `checks[]` in order |
| Never duplicate a check | `Set` membership test before appending |
| Highlight `first_blocking_check` row wherever it appears | Existing `isBlocker` condition (`execChecks.first_blocking_check === name && c.passed === false`) automatically applies — untouched |
| Never invent checks | The renderer's `byName[name]` lookup skips any name with no `checks[]` entry — `byName` is built strictly from `checks[]` |

There is no need to touch the `isBlocker` branch, the chip classifier (`PASS/FAIL/NOT RUN/N/A`), the "First blocker" summary header, or any neighbouring section renderer.

---

## 3. Renderer semantics after the fix

For input `(execChecks)`:
1. **If `execChecks` is null**, render the "No execution checks block." message (unchanged).
2. **Build `byName` and `seenOrder`** from `execChecks.checks` (unchanged).
3. **Emit "First blocker" header** from `execChecks.first_blocking_check` (unchanged).
4. **Render rows**: iterate over `ordered`, where:
   - `ordered` is initialized from `execChecks.evaluated_in_order` (in order).
   - Then any name from `seenOrder` not already in `ordered` is appended, in `checks[]` order.
5. Each row is rendered with the same chip classifier (`PASS / FAIL / NOT RUN / N/A`) and the same `dt-first-blocker` highlight rule as before.
6. **Empty fallback** ("No execution checks ran for this symbol.") is unchanged.

---

## 4. Regression tests

### 4.1 Test infrastructure (new in Phase A.1)

`TestExecutionChecksFidelity` extracts `_dt_renderExecutionChecksSection` from the template using a brace-aware walker that respects JS string/template-literal boundaries and is anchored to the codebase's uniform 8-space indentation. The function is compiled under Node.js alongside:
- A minimal `document` stub (`{ createElement: () => ({ className: '', innerHTML: '', appendChild() {} }) }`)
- A stub `_dt_section(title, bodyHtml, open)` returning a plain HTML string tagged `<section data-title="…" data-open="…">…</section>` for easy introspection
- The unmodified `_dt_escapeHtml` and `_dt_row` helpers (extracted from the template)

The Node program runs `_dt_renderExecutionChecksSection(<payload>)` for each scenario, `JSON.stringify`s the resulting HTML, and Python `json.loads` it. The renderer output is a single HTML string.

Two helpers traverse the rendered HTML robustly:
- `_row_count(html, name)` — counts `<span class="dt-chip dt-chip-{status}">{status}</span>{name}` patterns (PASS/FAIL/NOT RUN/N/A). This avoids false positives from `class="data-value"` or summary-header mentions of the name.
- `_first_row_pos(html, name)` — returns the index of the first such pattern, used for ordering assertions so the "First blocker" summary header does not pollute the order.

### 4.2 Test scenarios

| # | Scenario | Asserts |
|---|---|---|
| 1 | `test_blocker_present_in_evaluated_in_order` | Blocker is highlighted in place; declared order preserved |
| 2 | `test_blocker_absent_from_evaluated_in_order` | **(THE BUG)** Blocker row appended, highlighted exactly once; declared order before the extras is preserved |
| 3 | `test_multiple_extras_appended_in_checks_order` | Multiple extras preserve their persisted `checks[]` order |
| 4 | `test_duplicates_between_order_and_checks` | Overlap between `evaluated_in_order` and `checks[]` is rendered once |
| 5 | `test_name_absent_from_checks_array_is_never_invented` | A name in `evaluated_in_order` but not in `checks[]` is dropped (no fabrication) |
| 6 | `test_not_run_extra_appended_remains_not_run` | `applied=False / passed=None` stays NOT RUN even when named as `first_blocking_check` — no demotion to FAIL |
| 7 | `test_ordinary_phase_a_decision_rendering_unchanged` | When every persisted check is already in `evaluated_in_order`, the output is unchanged from Phase A |
| 8 | `test_live_alpxr_snapshot_renders_blocker_row_and_highlight` | Live regression: reads `/api/decision/ALPXR` snapshot, runs the renderer, asserts `position_existence_check` row appears exactly once with `.dt-first-blocker`, every persisted check appears exactly once |

### 4.3 Test results

```
TestExecutionChecksFidelity::test_blocker_present_in_evaluated_in_order          PASS
TestExecutionChecksFidelity::test_blocker_absent_from_evaluated_in_order         PASS
TestExecutionChecksFidelity::test_multiple_extras_appended_in_checks_order       PASS
TestExecutionChecksFidelity::test_duplicates_between_order_and_checks            PASS
TestExecutionChecksFidelity::test_name_absent_from_checks_array_is_never_invented PASS
TestExecutionChecksFidelity::test_not_run_extra_appended_remains_not_run          PASS
TestExecutionChecksFidelity::test_ordinary_phase_a_decision_rendering_unchanged   PASS
TestExecutionChecksFidelity::test_live_alpxr_snapshot_renders_blocker_row_and_highlight PASS
8/8 PASS
```

Full Phase A + Phase A.1 test file: 63/63 PASS.

---

## 5. Test evidence: live ALPXR

After the fix, the rendered Execution Checks section for ALPXR is (excerpt from the live test run):

```
First blocker | [BLOCKER chip] position_existence_check  Position check raised: {...}
PASS  margin_check
PASS  pending_order_check
NOT RUN  cooldown_check                         ← in evaluated_in_order order
NOT RUN  position_concentration_check
NOT RUN  sector_concentration_check
NOT RUN  correlation_check
NOT RUN  beta_check
NOT RUN  buying_power_check
NOT RUN  quantity_post_sizing_check
FAIL [FIRST BLOCKER]  position_existence_check  ← appended at end, correctly highlighted
```

The blocker row appears once, is highlighted, and is rendered in `chip-Fail` chip + `FIRST BLOCKER` chip — exactly as the spec requires.

---

## 6. Full safe suite results

```
$ .venv/bin/python -m pytest tests/ -q --ignore=tests/integration
...
5 failed, 1308 passed, 83 warnings in 82.94s (0:01:22)
```

Pre-existing failures (reproduced on `main@ddfdabb`):
- `tests/test_bot001_dashboard_status.py::test_template_renders_red_dot_when_alpaca_unreachable`
- `tests/test_bot002_paper_only_guard.py::test_main_py_installs_sigterm_handler`
- `tests/test_score_002_eligibility_and_ranking.py::test_score_45_all_non_score_gates_pass_yields_buy_signal`
- `tests/test_score_002_eligibility_and_ranking.py::test_score_80_all_non_score_gates_pass_yields_buy_signal`
- `tests/test_score_002_eligibility_and_ranking.py::test_min_score_buy_does_not_gate_buy_eligibility`

**NEW failures introduced by Phase A.1: 0.**

`git diff --check`: clean.

`git diff --stat`:
```
templates/dashboard.html                           |  15 +-
.../test_dashboard_phase_a_history_symbol_trace.py | 409 +++++++++++++++++++++
2 files changed, 423 insertions(+), 1 deletion(-)
```

Plus `MENTOR.md` (paragraph edit) + `ITERATION_PROGRESS_LOG.md` (append) + `REPORT.md` (rolling) + this archive.

---

## 7. Semantic safety

- `dashboard.py`: untouched.
- `src/**`: untouched.
- Schema, scoring, ranking, settings, execution logic: untouched.
- OBS-001 persistence: untouched.
- SmartBot: not restarted, not signalled.
- Dashboard service: not restarted for this slice. The fix is staged only — it does not reach `127.0.0.1:8000` until Josh merges and the controller triggers a dashboard restart (which Josh did not authorize for this slice).
- No changes to environment, secrets, cron, OpenClaw config.

**SEMANTIC TRADING CHANGES: NONE.**

---

## 8. Risks

- The renderer still trusts the persisted snapshot. If a future OBS-001 schema bump changes `checks[]` or `evaluated_in_order` semantics, the renderer output may drift. The Phase A acceptance test (`test_decision_endpoint_returns_snapshot_envelope`) pins the current block shapes.
- Phase A.1's regression tests rely on a working `node` runtime at `/usr/bin/node`. The test file imports `subprocess` lazily; if `node` becomes unavailable, the test class errors out with the standard `FileNotFoundError`, not silently. This matches the existing `TestOrderAutoOpenScenarios` infrastructure from Phase A's follow-up.
- The `_row_count` helper counts `<span class="dt-chip dt-chip-{status}">{status}</span>{name}` chip patterns. If the template ever wraps the name in additional markup (e.g. `<strong>`), the helper would need updating. The current template is straightforward and the helper is asserted explicitly by the live ALPXR test.

---

## 9. Approval gates

- **SmartBot**: do not restart.
- **Dashboard service**: do not restart (for this slice).
- **Merge**: STOP. Wait for Josh.
- **Phase B**: do not start.

---

## 10. Footer

This archive is the authoritative Phase A.1 record. `REPORT.md` is the rolling executive summary. Phase A.1's lifecycle state is STAGED — changes have not been committed at archive time; the commit and PR-open step follows.
