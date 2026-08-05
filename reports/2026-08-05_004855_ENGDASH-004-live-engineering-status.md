# ENGDASH-004 Live Engineering Status & Workflow Aggregation — Detailed Report

## Task

ENGDASH-004 — Live Engineering Status & Workflow Aggregation

## Timing and continuity

- Task start time: 2026-08-05 00:18 UTC
- Task end time: 2026-08-05 00:48 UTC
- Elapsed time: approximately 30 minutes
- Continuity: continuous after PR #12 merge verification
- Stale/blocked status: not stale; blocker cleared when PR #12 merge commit appeared on `main`
- Resumed-task explanation: Josh confirmed merge; Git showed `main`/`origin/main` at merge commit `e285bc3`

## Branch

`agent/engdash-004-live-engineering-status`

## Pull request

- PR: #13
- URL: https://github.com/jsavoy93/trading-bot/pull/13
- State verified before evidence correction: OPEN
- Mergeability verified before evidence correction: MERGEABLE
- Target branch: `main`
- Merge state: not merged

## Commit record

- Implementation commit: `b71279c9deded45bc1abc6110fa4a2e1241c4d7a`.
- Evidence-only remediation commit: this evidence-only correction commit on PR #13; exact hash verified after commit/push in the terminal report because a commit cannot record its own final object ID.
- Final branch tip: this evidence-only correction commit; verified after push to match PR #13 `headRefOid` in the terminal report.

## Base

`main` at `e285bc3` (`Merge pull request #12 from jsavoy93/agent/engdash-003-query-service-provider`)

## Summary of implementation

- Added typed operational dashboard summaries for engineering health, current tasks, blockers, and testing status.
- Enriched `EngineeringQueryService.snapshot()` with read-only QA/delegation metadata (`started_at`, `completed_at`, test command, duration, and output summary).
- Updated `/engineering` HTML rendering to show operational sections without adding routes or controls.
- Kept `/api/engineering/snapshot` as the same typed snapshot serialized to public JSON with warning details sanitized.
- Added focused tests for healthy and degraded live engineering status aggregation and updated API/UI tests for the new JSON shape and HTML sections.
- Added ENGDASH-004 documentation and implementation audit.

## Screens/pages added

No new URL paths were added. The existing `/engineering` page gained these sections:

- Engineering health
- Current agent activity
- Blockers and approvals needed
- Testing status

## Files modified

- `AGENT_BACKLOG.md`
- `dashboard_api/__init__.py`
- `dashboard_api/app.py`
- `dashboard_api/engineering_read_model.py`
- `engineering/query_service.py`
- `tests/test_dashboard_api_app.py`
- `tests/test_dashboard_engineering_read_model.py`

## Files added

- `docs/ENGDASH-004.md`
- `docs/2026-08-05_004808_ENGDASH-004-implementation-audit.md`
- `reports/2026-08-05_004855_ENGDASH-004-live-engineering-status.md`

## Provider architecture

The dashboard still uses the ENGDASH-003 provider architecture:

- `dashboard_api.providers:create_engineering_dashboard_provider()` constructs the production read-only provider.
- `EngineeringQueryService.snapshot()` supplies bounded workflow/query data.
- `EngineeringDashboardReadModel.snapshot()` maps provider data into typed dashboard summaries.
- `dashboard_api.app:create_app()` renders the injected provider snapshot as JSON and HTML.

ENGDASH-004 does not add a live GitHub adapter, direct process-control adapter, mutation API, or trading-system dependency.

## EngineeringQueryService integration details

`EngineeringQueryService.snapshot()` now includes the following additional read-only fields where available:

- `agent_run.started_at`
- `agent_run.completed_at`
- `tests.command`
- `tests.duration_seconds`
- `tests.output_summary`

These are consumed by the read model to populate operational activity and testing summaries.

## Data-source mapping

| Dashboard question | Source | Result |
| --- | --- | --- |
| Is engineering healthy? | repository summary, latest test result, blockers, warnings | `engineering_health` |
| What is every active agent doing? | current task, delegation metadata, workflow state | `current_tasks` |
| What is blocked? | workflow blocker, remaining gaps, approval state, warnings | `blockers` |
| What needs Josh's approval? | report recommendation and approval events | `approval` + `blockers` |
| What tests most recently passed? | latest QA/test evidence | `testing` + `latest_test_result` |
| What reports were produced? | bounded report index | `recent_reports` with `outcome` |
| Is the repository safe? | Git repository summary | `engineering_health.repository_safe` + `repository` |
| Is any provider degraded? | health warnings | `engineering_health.degraded_sources` |

## Degraded-mode behavior

- Source exceptions become bounded `HealthWarning` records.
- Public JSON sets `health_warnings[*].detail` to `null`.
- Missing PR metadata remains informational degradation, not a server failure.
- Missing active workflow returns empty current task/activity summaries and an explicit no-active-workflow gap.

## Route inventory

Command:

```bash
.venv/bin/python - <<'PY'
from fastapi.testclient import TestClient
from dashboard_api.app import create_app, SNAPSHOT_ROUTE, DASHBOARD_ROUTE
app = create_app()
routes = {route.path: sorted(route.methods) for route in app.routes if hasattr(route, 'methods')}
print(routes)
client = TestClient(app)
print('/openapi.json', client.get('/openapi.json').status_code)
print('/docs', client.get('/docs').status_code)
print('/redoc', client.get('/redoc').status_code)
for method in ('post','put','patch','delete'):
    for path in (SNAPSHOT_ROUTE, DASHBOARD_ROUTE):
        print(method.upper(), path, getattr(client, method)(path).status_code)
PY
```

Result:

```text
{'/api/engineering/snapshot': ['GET'], '/engineering': ['GET']}
/openapi.json 404
/docs 404
/redoc 404
POST /api/engineering/snapshot 405
POST /engineering 405
PUT /api/engineering/snapshot 405
PUT /engineering 405
PATCH /api/engineering/snapshot 405
PATCH /engineering 405
DELETE /api/engineering/snapshot 405
DELETE /engineering 405
```

Status: PASS

## Security verification

- No raw warning details are exposed in public JSON.
- HTML rendering escapes values.
- No secret/environment leakage tests regressed.
- No trading, brokerage, Alpaca, trading database, or legacy `dashboard.py` runtime imports were introduced.
- Existing import AST tests passed.

## Read-only verification

- Only two GET routes are registered.
- Mutation methods return 405 on both approved routes.
- `/openapi.json`, `/docs`, and `/redoc` return 404.
- No controls, approval actions, retries, pause/resume, execution, merge, or write endpoints were added.

## Acceptance evidence

| Acceptance criterion | Proof method | Exact result | Status |
| --- | --- | --- | --- |
| Dashboard answers whether engineering is healthy. | New `engineering_health` typed summary and focused tests. | Healthy fixture returns `HEALTHY`; degraded fixture returns `DEGRADED`. | PASS |
| Dashboard answers what every active agent is doing. | New `current_tasks` summary from `current_task` + `agent_run`. | Test asserts task `ENGDASH-001`, assigned agent `dashboard-agent`, and completion percent `70`. | PASS |
| Dashboard answers what is blocked. | New `blockers` aggregate from workflow blocker, approval, gaps, warnings. | Degraded test includes `required test failed` and repository warning blocker. | PASS |
| Dashboard answers what needs Josh's approval. | Existing approval summary plus blocker aggregation. | Existing `test_pending_human_approval_from_report` passed and approval blockers are included when pending. | PASS |
| Dashboard answers what tests most recently passed. | `testing.latest_status`, latest test result, and query-service QA metadata. | Healthy test returns `PASS`; degraded test returns `FAIL` and warning count `3`. | PASS |
| Dashboard answers what reports were produced. | `ReportIndex` remains bounded and now parses bounded `outcome`. | Existing report bounding/determinism tests passed. | PASS |
| Dashboard answers whether the repository is safe. | `engineering_health.repository_safe` and repository summary. | Healthy test returns `True`; degraded test produces repository warning. | PASS |
| Dashboard answers whether any provider is degraded. | `engineering_health.degraded_sources` and `health_warnings`. | Partial source failure test returns warnings for query, repository, and GitHub sources. | PASS |
| JSON endpoint uses real provider/default app and same typed snapshot. | API/provider tests. | Default app startup and JSON shape tests passed. | PASS |
| HTML page uses the same typed snapshot. | HTML render tests. | HTML contains Engineering health, Current agent activity, Blockers and approvals needed, Testing status. | PASS |
| Exact two-route HTTP surface preserved. | Route inventory script. | Only `/api/engineering/snapshot` and `/engineering` GET routes. | PASS |
| No mutation/write/control routes exist. | Route inventory script and tests. | POST/PUT/PATCH/DELETE return 405; no control routes present. | PASS |
| No forbidden trading/brokerage imports introduced. | AST tests and focused grep. | Tests passed; no runtime forbidden imports found. | PASS |
| Focused tests pass. | Focused pytest command. | `32 passed, 2 warnings in 2.36s`. | PASS |
| Relevant regression tests pass. | Engineering/dashboard regression pytest command. | `69 passed, 2 warnings in 2.46s`. | PASS |
| Full suite passes. | Full pytest command. | `375 passed, 82 warnings in 35.61s`. | PASS |
| Repository is clean. | `git status --short --branch` before evidence-only correction and final status after push. | Clean before correction on `agent/engdash-004-live-engineering-status`; final clean status is verified in the terminal report after the correction commit. | PASS |
| PR is open against `main`. | `gh pr view 13 --json number,state,mergeable,url,headRefName,baseRefName,headRefOid,isDraft`. | PR #13 is OPEN, MERGEABLE, non-draft, head `b71279c9deded45bc1abc6110fa4a2e1241c4d7a`, base `main`, URL `https://github.com/jsavoy93/trading-bot/pull/13` before evidence-only correction. Final PR head after push is verified in the terminal report. | PASS |

## Test commands and exact results

### Focused dashboard/provider/read-model

```bash
.venv/bin/python -m pytest tests/test_dashboard_engineering_read_model.py tests/test_dashboard_api_app.py tests/test_dashboard_api_provider.py
```

Result:

```text
32 passed, 2 warnings in 2.36s
```

### Relevant engineering/dashboard regression

```bash
.venv/bin/python -m pytest tests/test_engineering_query_service.py tests/test_engineering_events.py tests/test_engineering_event_projection.py tests/test_engineering_event_store.py tests/test_engineering_workflow_store.py tests/test_dashboard_engineering_read_model.py tests/test_dashboard_api_app.py tests/test_dashboard_api_provider.py
```

Result:

```text
69 passed, 2 warnings in 2.46s
```

### Full suite

```bash
.venv/bin/python -m pytest tests
```

Result:

```text
375 passed, 82 warnings in 35.61s
```

## Warnings

- Pytest unknown `timeout` config option warning.
- `websockets.legacy` deprecation warning.
- Existing datetime deprecation warnings in trading/database tests.

## Remaining risks

- Current activity is limited to the currently persisted/shared engineering workflow/delegation model; ENGDASH-004 does not add a separate live process scanner.
- Full-suite classification is best-effort from the latest QA command.
- Report outcome parsing is intentionally bounded and heuristic.

## Manager decision

Commit, push, open PR against `main`, and stop for Josh's review. Do not merge.

## Next recommended action

Josh reviews the ENGDASH-004 PR after it is opened.
