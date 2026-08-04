# ENGDASH-003 Implementation Audit — EngineeringQueryService-backed provider

Task start time: 2026-08-04T23:45:54Z
Task end time: 2026-08-04T23:53:31Z
Elapsed time: 7 minutes 37 seconds
Continuity: continuous
Branch: agent/engdash-003-query-service-provider
Base: latest merged `main` after PR #11 merge commit `d78c198492309d5d3a0bc19ca66bf3c27c4e4dff`
Commit: pending at audit-write time

## Summary

ENGDASH-003 replaces the ENGDASH-002 intentionally degraded default provider
with a real read-only `EngineeringQueryService`-backed provider while preserving
`create_app(snapshot_provider=...)` dependency injection for tests.

The HTTP surface remains exactly two GET routes:

- `GET /api/engineering/snapshot`
- `GET /engineering`

FastAPI automatic `/openapi.json`, `/docs`, and `/redoc` routes remain disabled.

## Provider architecture

`dashboard_api.providers` adds explicit provider construction:

- `EngineeringDashboardProviderConfig` defines repo root, backlog path,
  workflow state path, event store path, optional audit archive root, project
  identity, and clock.
- `create_engineering_query_service()` constructs the existing
  `EngineeringQueryService` with read-only dashboard dependencies.
- `create_engineering_dashboard_provider()` wraps that query service in the
  ENGDASH-001 `EngineeringDashboardReadModel`.
- `GitRepositorySummaryReader` supplies bounded repository metadata through
  read-only `git` commands.
- `ReadOnlyEngineeringEventStore` implements only `list_events()` and
  `pause_state()` for `EngineeringQueryService`; it never creates schema or
  writes the event database.

`dashboard_api.app:create_default_read_model()` now returns this real provider.

## Data-source mapping

| Snapshot area | Source |
| --- | --- |
| Repository state | `GitRepositorySummaryReader` |
| Backlog counts/current task | `EngineeringQueryService` + `AGENT_BACKLOG.md` |
| Workflow state | `EngineeringQueryService` + `WorkflowStore` |
| Test/report/delegation fields | active `StoredWorkflow` via `EngineeringQueryService` |
| Recent timeline/events | `EngineeringQueryService` + read-only event store |
| Recent report files | `ReportIndex` |
| PR metadata | injected ENGDASH-001 PR reader only |
| Health warnings | ENGDASH-001 read-model degradation handling |

## Degraded-mode behavior

- Default app no longer degrades solely because no query provider is wired.
- Missing workflow state is represented as no active workflow.
- Missing event database returns an empty timeline and default unpaused state.
- Missing/failing source reads become bounded health warnings.
- Public JSON strips warning details.
- HTML renders warning source/severity/message only.
- Missing PR metadata remains non-fatal and injected-only.

## Route inventory

Smoke result after implementation:

```text
[('/api/engineering/snapshot', ['GET'], 'engineering_snapshot'), ('/engineering', ['GET'], 'engineering_dashboard')]
/api/engineering/snapshot 200
/engineering 200
/openapi.json 404
/docs 404
/redoc 404
```

## Security and read-only verification

- No mutation routes or controls were added.
- No POST, PUT, PATCH, DELETE, or WebSocket routes are registered.
- No approval, retry, pause/resume, execution, merge, or write endpoints exist.
- `dashboard_api` does not import trading strategy code, brokerage integrations,
  Alpaca, trading databases, or legacy `dashboard.py`.
- SQLite event access uses `mode=ro&immutable=1`.
- Missing event DB does not create `.agent-state` files.
- Public JSON/HTML tests verify raw exception/secret strings are not leaked.

## Acceptance evidence

1. The default engineering dashboard app uses a real `EngineeringQueryService`-backed provider.
   - Proof method: provider construction test and default app startup test.
   - Exact result: `test_engineering_query_service_backed_provider_construction PASSED`; `test_independent_default_app_startup_uses_real_read_only_provider PASSED`.
   - Status: PASS.

2. The default app no longer shows degraded mode solely because no provider was wired.
   - Proof method: default app JSON endpoint test.
   - Exact result: repository root is not `unavailable` and legacy "No engineering query source is configured" message is absent.
   - Status: PASS.

3. Missing optional sources still degrade safely.
   - Proof method: missing source and read-only event-store tests.
   - Exact result: `test_provider_missing_sources_degrade_to_bounded_warnings PASSED`; `test_read_only_event_store_does_not_create_missing_database PASSED`.
   - Status: PASS.

4. The API and UI consume the same typed snapshot.
   - Proof method: existing ENGDASH-002 app tests and provider injection path.
   - Exact result: JSON and HTML endpoint tests passed.
   - Status: PASS.

5. Exact two-route HTTP surface is preserved.
   - Proof method: route smoke and tests.
   - Exact result: only `/api/engineering/snapshot` GET and `/engineering` GET are registered; `/openapi.json`, `/docs`, and `/redoc` return 404.
   - Status: PASS.

6. No controls or write operations exist.
   - Proof method: mutation route tests and source/route grep.
   - Exact result: mutation methods return 405; route inventory contains no controls.
   - Status: PASS.

7. No trading or brokerage imports exist.
   - Proof method: AST import tests.
   - Exact result: `test_real_provider_sources_use_no_trading_or_brokerage_imports PASSED`; existing app import scan passed.
   - Status: PASS.

8. Full suite passes.
   - Proof method: full pytest command.
   - Exact result: `373 passed, 83 warnings in 33.96s`.
   - Status: PASS.

9. Repository is clean at completion.
   - Proof method: final post-commit status required.
   - Exact result: pending at audit-write time.
   - Status: pending final commit/push verification.

10. PR is open against main and ready for Josh's review.
    - Proof method: final `gh pr` check required after push.
    - Exact result: pending at audit-write time.
    - Status: pending final PR creation.

## Test commands and exact results

Focused provider/API/read-model:

```bash
git diff --check && .venv/bin/python -m pytest tests/test_dashboard_api_provider.py tests/test_dashboard_engineering_read_model.py tests/test_dashboard_api_app.py
```

Result: `30 passed, 2 warnings in 2.18s`.

Relevant engineering regression:

```bash
git diff --check && .venv/bin/python -m pytest tests/test_dashboard_api_provider.py tests/test_dashboard_engineering_read_model.py tests/test_dashboard_api_app.py tests/test_settings_service.py -k 'dashboard or engineering_read_model' tests/test_engineering_query_service.py tests/test_engineering_event_projection.py tests/test_engineering_event_store.py tests/test_engineering_workflow_store.py
```

Result: `40 passed, 61 deselected, 2 warnings in 4.15s`.

Full suite:

```bash
git diff --check && .venv/bin/python -m pytest tests
```

Result: `373 passed, 83 warnings in 33.96s`.

Warnings are existing pytest config, websockets deprecation, and datetime UTC deprecation warnings.

## Remaining risks

- PR metadata remains injected-only; no live GitHub adapter was added.
- Runtime service/deployment wiring is not included.
- SQLite immutable reads may not include uncheckpointed concurrent WAL updates;
  dashboard reads degrade safely if the event DB cannot be read.

## Final state

Branch: agent/engdash-003-query-service-provider
Commit: pending at audit-write time
Repository status: pending final commit verification
PR: pending creation
