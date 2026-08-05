# ENGDASH-003 — EngineeringQueryService-backed provider

ENGDASH-003 wires the separate read-only engineering dashboard app to the
existing engineering query boundary instead of the intentionally degraded
ENGDASH-002 default provider.

## Launch

```bash
python -m dashboard_api.app --host 127.0.0.1 --port 8010
```

## Provider architecture

`dashboard_api.providers:create_engineering_dashboard_provider()` builds the
production default provider from explicit configuration:

- `EngineeringQueryService` supplies current workflow, backlog, tests, reports,
  pause state, recommended next step, and recent engineering events.
- `WorkflowStore` reads the active workflow from `.git/engineering-workflow.json`.
- `ReadOnlyEngineeringEventStore` reads `.agent-state/engineering-events.sqlite3`
  without creating, migrating, or writing the database.
- `GitRepositorySummaryReader` reads branch, dirty paths, sync state, and latest
  commit metadata through bounded `git` commands.
- `ReportIndex` supplies bounded recent repository and optional audit reports.
- ENGDASH-001 `EngineeringDashboardReadModel` remains the single typed snapshot
  adapter consumed by the API and HTML UI.

Tests can still inject any object with `snapshot() -> DashboardSnapshot` through
`create_app(snapshot_provider=...)`.

## Data-source mapping

| Dashboard field | Source |
| --- | --- |
| Repository state | `GitRepositorySummaryReader` |
| Backlog summary/items | `EngineeringQueryService` from `AGENT_BACKLOG.md` |
| Active workflow/stage | `EngineeringQueryService` via `WorkflowStore` |
| Agent/test/report state | `EngineeringQueryService` active workflow records |
| Recent events/timeline | `EngineeringQueryService` via read-only event store |
| Recent report files | `ReportIndex` |
| PR metadata | injected `PullRequestMetadataReader` only; no live adapter |
| Health/degradation warnings | ENGDASH-001 read model source-failure handling |

## HTTP surface

Only these routes are registered:

- `GET /api/engineering/snapshot`
- `GET /engineering`

FastAPI automatic routes remain disabled:

- `/openapi.json` returns 404
- `/docs` returns 404
- `/redoc` returns 404

There are no POST, PUT, PATCH, DELETE, WebSocket, approval, retry,
pause/resume, execution, merge, or write endpoints.

## Degraded-mode behavior

The default app no longer degrades solely because no provider is wired. It now
uses the real `EngineeringQueryService`-backed provider by default.

Missing optional sources are still non-fatal:

- missing workflow state means no active workflow;
- missing event database yields an empty timeline and default unpaused state;
- missing or failing query/repository/report/PR sources become bounded health
  warnings;
- public JSON suppresses warning details;
- HTML renders warning source/severity/message only.

## Security and read-only boundaries

- `dashboard_api` does not import trading strategy code, brokerage integrations,
  Alpaca, trading databases, or legacy `dashboard.py`.
- The read-only event store opens SQLite with `mode=ro&immutable=1` and does not
  create schema files.
- The provider does not read environment variables or secrets.
- Raw exception detail is not exposed in public JSON/HTML output.
- Dependency injection remains the testing and extension boundary.

## Remaining risks

- PR metadata is still injected-only by design; no live GitHub adapter exists.
- Runtime service/deployment wiring is not included.
- SQLite immutable reads may lag a concurrently written WAL until checkpointed;
  the dashboard degrades safely if the event database cannot be read.
