# ENGDASH-005 Implementation — Detailed Archive

## Timing and continuity

- UTC timestamp: 2026-08-11T01:34:59Z
- Branch: `agent/engdash-005-implementation`
- Base after PR #29 merge: `7de8003`
- Status: implemented, validated, ready for PR
- Continuity: ENGDASH-005 work was preserved in stash during PR #29 remediation, then restored onto updated main. The stash apply conflict was limited to `ITERATION_PROGRESS_LOG.md` and was resolved by preserving both the ENGDASH-005 stopped entry and the PR #29 remediation entry.

## Implementation summary

### `dashboard_api/providers.py`

- Removed direct repository discovery from provider construction.
- Removed `_discover_repo_root()`, `Path.cwd()` fallback, `DEFAULT_EVENT_STORE_PATH`, and `DEFAULT_WORKFLOW_STATE_PATH`.
- `create_engineering_dashboard_provider()` now requires an explicit `EngineeringDashboardProviderConfig`.
- `create_engineering_query_service()` uses explicit configured paths.
- Added read-only adapter-compatible event/workflow wrappers to keep dashboard reads from creating/mutating event DB state.

### `dashboard_api/app.py`

- Default app composition uses `TRADING_BOT_PROJECT` + `build_project_context(TRADING_BOT_PROJECT)` to construct explicit `EngineeringDashboardProviderConfig`.
- Preserved module-level `app = create_app()` startup.
- Preserved exact route surface:
  - `GET /engineering`
  - `GET /api/engineering/snapshot`
- Added escaped payload rendering in the existing events section.

### `engineering/query_service.py`

- Added `event_source` and `workflow_source` parameters while preserving old `event_store`/`workflow_store` aliases.
- Supports `EngineeringEventStore | EventAdapter` and `WorkflowStore | WorkflowAdapter` callers.
- Normalizes `WorkflowAdapter` exactly once in `__init__`.
- Does not import or accept `ProjectContext`.
- Sorts timeline after bounded projection by:
  1. `occurred_at` ascending
  2. `sequence` descending
  3. `event_id` ascending
- Handles missing/None/malformed ordering values safely.

### Tests

- Updated existing dashboard tests for explicit config and behavioral event-source assertions.
- Added `tests/test_dashboard_timeline.py` with 22 behavioral tests covering the merged governance.

## Acceptance evidence

| Criterion | Proof method | Exact result | Status |
|---|---|---|---|
| Explicit provider config required | Focused tests | `42 passed, 2 warnings` | PASS |
| No cwd/repo discovery/provider defaults | grep + focused tests | No runtime matches for `Path.cwd`, `_discover_repo_root`, `DEFAULT_EVENT_STORE_PATH`, `DEFAULT_WORKFLOW_STATE_PATH` | PASS |
| Existing routes only | grep + route tests | Only `@app.get(SNAPSHOT_ROUTE)` and `@app.get(DASHBOARD_ROUTE)` | PASS |
| Existing snapshot/read-model path preserved | Dashboard tests | `tests/test_dashboard_api_app.py` and `tests/test_dashboard_api_provider.py` passed | PASS |
| Query-service backward compatibility | Focused tests | `tests/test_engineering_query_service.py` passed | PASS |
| Adapter-backed query-service compatibility | Timeline tests | Event-source and workflow-adapter tests passed | PASS |
| Deterministic bounded timeline | Timeline tests | All ordering/limit/missing/malformed tests passed | PASS |
| HTML escaping | Timeline test | `test_timeline_html_escaping` passed | PASS |
| Scope | `git diff --name-only` + grep | Only allowed implementation/test files plus reporting artifacts changed | PASS |
| Full safe suite | Full pytest | `489 passed, 82 warnings` | PASS |
| Whitespace | `git diff --check` | PASS | PASS |

## Validation commands and results

### Focused ENGDASH-005 tests

Command:
```bash
TESTING=1 UNIT_TESTING=1 .venv/bin/python -m pytest tests/test_dashboard_timeline.py tests/test_dashboard_api_provider.py tests/test_dashboard_api_app.py tests/test_engineering_query_service.py -q
```

Result:
```text
42 passed, 2 warnings in 2.77s
```

### Relevant regressions

Command:
```bash
TESTING=1 UNIT_TESTING=1 .venv/bin/python -m pytest tests/test_engineering_project_context.py tests/test_engineering_event_projection.py tests/test_dashboard_api_app.py tests/test_dashboard_api_provider.py -q
```

Result:
```text
62 passed, 2 warnings in 2.37s
```

### Full safe suite

Command:
```bash
TESTING=1 UNIT_TESTING=1 .venv/bin/python -m pytest -q
```

Result:
```text
489 passed, 82 warnings in 33.57s
```

### Diff check

Command:
```bash
git diff --check
```

Result: PASS, no output.

## Scope audit

Changed implementation/test files:
- `dashboard_api/app.py`
- `dashboard_api/providers.py`
- `engineering/query_service.py`
- `tests/test_dashboard_api_app.py`
- `tests/test_dashboard_api_provider.py`
- `tests/test_dashboard_timeline.py`

Reporting/continuity files:
- `ITERATION_PROGRESS_LOG.md`
- `reports/2026-08-11_002616_engdash-005-implementation-stopped.md`
- `reports/2026-08-11_013459_engdash-005-implementation.md`

No `AGENT_BACKLOG.md` diff remains in the ENGDASH-005 implementation branch after PR #29 landed.

## Non-blocking follow-up

The backlog parser task-id regex still does not recognize suffix task IDs such as `ENGPLAT-002A`, `ENGPLAT-002B`, and `ENGPLAT-002C`. This was preserved as a non-blocking follow-up and was not changed in ENGDASH-005.

## Manager decision

Open an implementation PR targeting main and stop for Josh read-only review. Do not merge.
