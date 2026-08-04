# ENGDASH-003 Query Service Provider — Authoritative Archive

Task start time: 2026-08-04T23:45:54Z
Task end time: 2026-08-04T23:56:52Z
Elapsed time: 10 minutes 58 seconds
Continuity: continuous until runtime timeout, then resumed immediately from verified repository state
Repository: /root/.openclaw/workspace/trading-bot
Branch: agent/engdash-003-query-service-provider
Base commit: d78c198492309d5d3a0bc19ca66bf3c27c4e4dff
Commit at archive-write time: pending
Status: DONE implementation, pending commit/push/PR creation

## Summary of changes

- Added `dashboard_api.providers` with a production read-only provider backed by `EngineeringQueryService`.
- Replaced ENGDASH-002's degraded default app provider with the real provider.
- Preserved app-factory injection through `create_app(snapshot_provider=...)`.
- Added read-only repository summary and read-only event-store adapters.
- Updated read-model approval detection to support real query timeline `type` keys as well as existing test `event_type` keys.
- Added provider tests covering healthy, degraded, no-leak, route, and import boundaries.
- Updated ENGDASH-002 docs with ENGDASH-003 supersession note.
- Added ENGDASH-003 docs and implementation audit.
- Updated backlog and iteration continuity records.

## Provider architecture

`dashboard_api.providers:create_engineering_dashboard_provider()` creates an ENGDASH-001 `EngineeringDashboardReadModel` with:

- `EngineeringQueryService` for workflow/backlog/report/test/timeline query data.
- `WorkflowStore` for `.git/engineering-workflow.json`.
- `ReadOnlyEngineeringEventStore` for `.agent-state/engineering-events.sqlite3`.
- `GitRepositorySummaryReader` for bounded Git metadata.
- `ReportIndex` for bounded report metadata.
- Optional explicit `EngineeringDashboardProviderConfig` for tests and deployment.

The default FastAPI app calls `create_default_read_model()`, which now delegates to the real provider factory.

## EngineeringQueryService integration details

The provider reuses `EngineeringQueryService.snapshot(timeline_limit=...)` and adapts its output through the existing ENGDASH-001 typed read model. No dashboard-specific duplicate parsing of backlog/workflow query fields was added beyond provider construction.

The event-store adapter implements only the query methods required by `EngineeringQueryService`:

- `list_events(limit=...)`
- `pause_state()`

It opens SQLite using `file:<path>?mode=ro&immutable=1`, returns empty/default values when the event DB is absent, and never creates or migrates the database.

## Data-source mapping

| Dashboard field | Source |
| --- | --- |
| Repository state | `GitRepositorySummaryReader` |
| Backlog summary/items | `EngineeringQueryService` from `AGENT_BACKLOG.md` |
| Active/recent workflow state | `EngineeringQueryService` via `WorkflowStore` |
| Blockers | `EngineeringDashboardReadModel` from query gaps/failure reasons |
| Approvals | report recommendation or approval timeline events |
| Recent reports | `ReportIndex` |
| Recent events/timeline | `EngineeringQueryService` via read-only event store |
| Health/degradation warnings | ENGDASH-001 read-model source-failure handling |
| PR metadata | injected PR reader only; no live GitHub adapter |

## Degraded-mode behavior

- Default app no longer degrades solely due to missing provider wiring.
- Missing workflow state means no active workflow.
- Missing event database yields empty timeline and default unpaused state.
- Query/repository/report/PR failures become bounded warnings.
- Public JSON suppresses warning details.
- HTML renders warning source, severity, and message only.
- Missing PR metadata remains non-fatal.

## Route inventory

Route/startup smoke result:

```text
[('/api/engineering/snapshot', ['GET'], 'engineering_snapshot'), ('/engineering', ['GET'], 'engineering_dashboard')]
/api/engineering/snapshot 200
/engineering 200
/openapi.json 404
/docs 404
/redoc 404
```

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

## Acceptance criteria

1. Default app uses real `EngineeringQueryService`-backed provider.
   - Proof: `test_engineering_query_service_backed_provider_construction`; `test_independent_default_app_startup_uses_real_read_only_provider`.
   - Result: PASS.

2. Default app no longer shows degraded mode solely because no provider was wired.
   - Proof: default app test asserts repository root is not `unavailable` and legacy message is absent.
   - Result: PASS.

3. Missing optional sources degrade safely.
   - Proof: missing source and missing event DB tests.
   - Result: PASS.

4. API and UI consume same typed snapshot.
   - Proof: app tests and provider injection path.
   - Result: PASS.

5. Exact two-route HTTP surface is preserved.
   - Proof: route smoke and tests.
   - Result: PASS.

6. No controls or write operations exist.
   - Proof: mutation method tests and route inventory.
   - Result: PASS.

7. No trading or brokerage imports exist.
   - Proof: AST import scans.
   - Result: PASS.

8. Full suite passes.
   - Proof: full pytest command.
   - Result: PASS.

9. Repository is clean.
   - Proof: final post-commit `git status` required.
   - Result: pending at archive-write time.

10. PR open against main and ready for Josh review.
    - Proof: final `gh pr` check required.
    - Result: pending at archive-write time.

## Security and read-only verification

- No mutation routes or controls.
- No POST, PUT, PATCH, DELETE, or WebSocket routes.
- No approval, retry, pause/resume, execution, merge, or write endpoint.
- No trading strategy, brokerage, Alpaca, trading DB, or `dashboard.py` imports.
- Read-only event DB adapter does not create missing DB files.
- Public JSON/HTML do not expose raw exception or test secret strings.

## Remaining risks

- PR metadata remains injected-only; no live GitHub adapter was added.
- Runtime service/deployment wiring is not included.
- SQLite immutable reads may not include uncheckpointed concurrent WAL updates, but dashboard reads degrade safely if the event DB cannot be read.

## Final state at archive write

Final branch: agent/engdash-003-query-service-provider
Final commit: pending
Repository status: dirty with intended implementation/report files only
PR URL/status: pending creation
Audit report path: docs/2026-08-04_235331_ENGDASH-003-implementation-audit.md
