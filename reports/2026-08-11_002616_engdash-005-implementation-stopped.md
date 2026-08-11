# ENGDASH-005 Implementation — Detailed Stopped Archive

## Timing and continuity

- Start context: resumed after PR #28 merge and post-merge gate.
- Gate passed: local `main` fast-forwarded to `origin/main` at `fd5a37d`; PR #28 state `MERGED`; no active workflow files found.
- Branch: `agent/engdash-005-implementation`
- Base commit: `fd5a37d`
- Archive timestamp: `2026-08-11T00:26:16Z`
- Status: STOPPED
- Continuity: implementation work remains uncommitted in working tree.

## Scope

Authorized runtime files:
- `dashboard_api/providers.py`
- `dashboard_api/app.py`
- `engineering/query_service.py`

Authorized existing tests:
- `tests/test_dashboard_api_provider.py`
- `tests/test_dashboard_api_app.py`

Authorized new test:
- `tests/test_dashboard_timeline.py`

Reporting/continuity artifacts:
- `REPORT.md`
- `reports/2026-08-11_002616_engdash-005-implementation-stopped.md`
- `ITERATION_PROGRESS_LOG.md`

## Files changed before stop

Implementation/test files:
- `dashboard_api/app.py`
- `dashboard_api/providers.py`
- `engineering/query_service.py`
- `tests/test_dashboard_api_app.py`
- `tests/test_dashboard_api_provider.py`
- `tests/test_dashboard_timeline.py`

Reporting/continuity files:
- `REPORT.md`
- `reports/2026-08-11_002616_engdash-005-implementation-stopped.md`
- `ITERATION_PROGRESS_LOG.md`

## Implementation summary

- `dashboard_api/app.py`: default app composition now uses `TRADING_BOT_PROJECT` and `build_project_context(TRADING_BOT_PROJECT)` to construct an explicit `EngineeringDashboardProviderConfig`. Existing `GET /engineering` and `GET /api/engineering/snapshot` routes are preserved.
- `dashboard_api/providers.py`: removed `_discover_repo_root`, `Path.cwd()` fallback, `DEFAULT_EVENT_STORE_PATH`, and `DEFAULT_WORKFLOW_STATE_PATH`. Provider/query service construction requires explicit config. Added read-only adapter-compatible wrappers around event/workflow sources to preserve dashboard read-only behavior.
- `engineering/query_service.py`: accepts `event_source`/`workflow_source` while preserving existing `event_store`/`workflow_store` aliases. Normalizes `WorkflowAdapter` once. Sorts timeline after bounded projection by `occurred_at` ascending, `-sequence` descending, `event_id` ascending, with safe handling of missing/None/malformed keys.
- Existing dashboard tests updated for explicit config/behavioral assertions.
- Added 22 planned behavioral tests in `tests/test_dashboard_timeline.py`.

## Acceptance evidence

| Criterion | Proof method | Exact result | Status |
|---|---|---|---|
| Explicit config required | Focused tests | `test_provider_requires_explicit_config` passed | PASS |
| No cwd fallback/discovery | grep + focused tests | No runtime matches for `Path.cwd`, `def _discover_repo_root`, `DEFAULT_EVENT_STORE_PATH`, `DEFAULT_WORKFLOW_STATE_PATH`; tests passed | PASS |
| Existing routes preserved | grep + focused tests | Only route decorators remain `@app.get(SNAPSHOT_ROUTE)` and `@app.get(DASHBOARD_ROUTE)`; route tests passed | PASS |
| Query service backward compatibility | Focused tests | `tests/test_engineering_query_service.py` passed; timeline compatibility tests passed | PASS |
| Timeline deterministic/bounded behavior | Focused tests | All 22 `tests/test_dashboard_timeline.py` tests passed | PASS |
| Existing dashboard behavior | Focused + regression tests | `tests/test_dashboard_api_provider.py` and `tests/test_dashboard_api_app.py` passed | PASS |
| Relevant adapter/projection regressions | Regression command | `62 passed, 2 warnings` | PASS |
| Full safe suite | Full pytest | `1 failed, 488 passed, 84 warnings` | FAIL |
| Whitespace diff hygiene | `git diff --check` | no output | PASS |

## Test commands and results

### Focused ENGDASH-005 tests

Command:
```bash
TESTING=1 UNIT_TESTING=1 .venv/bin/python -m pytest tests/test_dashboard_timeline.py tests/test_dashboard_api_provider.py tests/test_dashboard_api_app.py tests/test_engineering_query_service.py -q
```

Result:
```text
42 passed, 2 warnings in 2.35s
```

### Relevant dashboard/query-service regressions

Command:
```bash
TESTING=1 UNIT_TESTING=1 .venv/bin/python -m pytest tests/test_engineering_project_context.py tests/test_engineering_event_projection.py tests/test_dashboard_api_app.py tests/test_dashboard_api_provider.py -q
```

Result:
```text
62 passed, 2 warnings in 2.89s
```

### Full safe suite

Command:
```bash
TESTING=1 UNIT_TESTING=1 .venv/bin/python -m pytest -q
```

Result:
```text
1 failed, 488 passed, 84 warnings in 37.61s
```

Failure excerpt:
```text
FAILED tests/test_engineering_workflow_engine.py::test_dispatch_workflow_handles_every_state[PLAN]
engineering.backlog.BacklogParseError: Task ENGPLAT-001 has invalid status: GOVERNANCE_DRAFT
```

### Diff check

Command:
```bash
git diff --check
```

Result: PASS, no output.

## Blocking finding

The full safe suite fails because the backlog parser cannot parse `AGENT_BACKLOG.md` on merged main after PR #28. The failing status is `GOVERNANCE_DRAFT`, which is not a valid `TaskStatus` enum value (`TODO`, `IN_PROGRESS`, `BLOCKED`, `REVIEW`, `DONE`). The failure occurs in `tests/test_engineering_workflow_engine.py::test_dispatch_workflow_handles_every_state[PLAN]` while loading the real backlog during workflow PLAN handling.

This blocker is outside the ENGDASH-005 authorized runtime/test implementation files. Per Josh's instruction, the manager stopped and did not commit, push, or open a PR.

## Stopped state

- Branch: `agent/engdash-005-implementation`
- Commit: `fd5a37d` plus uncommitted working-tree changes
- Implementation PR: not opened
- Commit: not created
- Push: not performed

## Recommended next action

Authorize a narrow backlog-status remediation for invalid `GOVERNANCE_DRAFT` entries in `AGENT_BACKLOG.md`, or land that correction separately on main, then rerun the full safe suite and continue ENGDASH-005 implementation completion if all checks pass.
