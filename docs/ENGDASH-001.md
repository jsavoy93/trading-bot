# ENGDASH-001 — Engineering Dashboard Read Model

ENGDASH-001 adds a reusable read-only engineering dashboard snapshot model under
`dashboard-api/engineering_read_model.py`.

## Boundary

The read model is intentionally separate from the legacy trading `dashboard.py`.
It must not import trading modules, Alpaca/brokerage clients, trading database
code, or dashboard controls. It does not execute shell commands, mutate GitHub,
start agents, create routes, render UI, or update repository/workflow/backlog
state.

## Architecture

`EngineeringDashboardReadModel` aggregates injected read-only sources:

- `QuerySnapshotReader`: usually `EngineeringQueryService.snapshot()`, for
  backlog, workflow, timeline, tests, report, and approval facts.
- `RepositorySummaryReader`: a precomputed read-only repository summary.
  ENGDASH-001 deliberately does not run Git itself.
- `PullRequestMetadataReader`: optional read-only PR metadata for URL, number,
  state, target branch, head branch, and mergeability.
- `ReportIndex`: bounded local report/audit archive index.

Individual source failures become `HealthWarning` entries and do not fail the
whole snapshot.

## Typed output

The exported dataclasses are:

- `DashboardSnapshot`
- `RepositorySummary`
- `BacklogSummary`
- `WorkflowSummary`
- `ApprovalSummary`
- `TestSummary`
- `PullRequestSummary`
- `ReportSummary`
- `HealthWarning`

Missing optional data is represented as `None`, empty tuples/maps, or
`PullRequestSummary.available == False`.

## Limits and freshness

Snapshot generation is synchronous and bounded. Timeline events use the model's
`event_limit`; report indexing defaults to ten report files and checks only
known report roots. `DashboardSnapshot.data_freshness_timestamp` records the
UTC snapshot construction time.
