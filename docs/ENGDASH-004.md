# ENGDASH-004 — Live Engineering Status & Workflow Aggregation

## Summary

ENGDASH-004 extends the read-only engineering dashboard from a data viewer into an operational engineering status dashboard. The dashboard still exposes only:

- `GET /api/engineering/snapshot`
- `GET /engineering`

FastAPI automatic `/openapi.json`, `/docs`, and `/redoc` remain disabled.

## Architecture

The dashboard remains layered and read-only:

1. `EngineeringQueryService.snapshot()` provides bounded engineering workflow facts from existing query/read boundaries.
2. `EngineeringDashboardReadModel.snapshot()` converts those facts into typed dashboard summaries.
3. `dashboard_api.app` renders the same typed snapshot as JSON and HTML.

No trading dashboard, brokerage, Alpaca, trading database, mutation, execution, approval, retry, pause/resume, merge, or control route is introduced.

## New typed summaries

ENGDASH-004 adds these read-only summaries to `DashboardSnapshot`:

- `engineering_health` — overall health, repository safety, branch/commit, last successful regression timestamp, degraded sources, and warning count.
- `current_tasks` — active task/agent status, current phase, priority, started/updated timestamps, blocker, and completion percentage.
- `blockers` — deterministic, bounded blocker and approval strings derived from workflow, gaps, approvals, and warnings.
- `testing` — latest test status, warning count, and best-effort focused/regression/full-suite classification.

Existing fields remain present, including repository, backlog, workflow, approval, latest execution/test result, pull request, recent events, recent reports, health warnings, and freshness timestamp.

## Data-source mapping

| Dashboard question | Source |
| --- | --- |
| Is engineering healthy? | repository summary, latest test result, health warnings, blockers |
| What is every active agent doing? | `current_task`, `agent_run`, backlog, workflow summary |
| What is blocked? | workflow blocker, remaining gaps, approval state, warnings |
| What needs Josh's approval? | report recommendation, approval timeline events, approval summary |
| What tests most recently passed? | `tests` command/result/completed timestamp from `EngineeringQueryService` |
| What reports were produced? | bounded `ReportIndex` metadata |
| Is the repository safe? | `RepositorySummary.is_clean`, branch, commit, sync metadata |
| Is any provider degraded? | `health_warnings` and `engineering_health.degraded_sources` |

## HTML rendering

The `/engineering` page now includes operational sections for:

- Engineering health
- Current agent activity
- Blockers and approvals needed
- Testing status
- Repository
- Workflow
- Approval
- Execution/tests
- Pull request
- Backlog
- Recent reports
- Recent timeline/events
- Degradation warnings

All rendered values are escaped and warning details remain omitted from public API output.

## Degraded mode

Unavailable or partial sources are converted into bounded health warnings. Public JSON sanitizes `health_warnings[*].detail` to `null` so raw exceptions, traces, secrets, environment values, and sensitive filesystem details are not exposed.

## Launch command

```bash
python -m dashboard_api.app --host 127.0.0.1 --port 8010
```

## Verification expectations

ENGDASH-004 should be verified with focused dashboard/provider/read-model tests, relevant engineering query regressions, route inventory checks, import-safety checks, and the full safe test suite.
