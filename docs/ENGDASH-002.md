# ENGDASH-002 — Read-only Engineering Dashboard API/UI

ENGDASH-002 adds a separate read-only engineering dashboard application under
`dashboard_api/`. It deliberately replaces the ENGDASH-001 `dashboard-api/`
path with the importable Python package `dashboard_api/`; both paths must not
coexist.

## Architecture

The app is independent from the legacy trading `dashboard.py` module. It is not
mounted into that dashboard and does not import trading, brokerage, Alpaca,
trading database, or trading dashboard code.

The only engineering data source is an injected ENGDASH-001
`EngineeringDashboardReadModel` provider. The app serializes
`DashboardSnapshot.to_dict()` for JSON and renders the same snapshot into HTML.
A safe degraded default provider exists only so the app factory can start
without secrets, shell commands, database access, or trading imports.

## Routes

- `GET /api/engineering/snapshot` — stable bounded JSON snapshot.
- `GET /engineering` — separate read-only engineering dashboard HTML page.

No POST, PUT, PATCH, DELETE, approval, retry, pause, resume, execution, merge,
or write/control routes are provided.

## Launch command

```bash
python -m dashboard_api.app --host 127.0.0.1 --port 8010
```

This starts only the separate engineering dashboard app. It does not start or
modify the trading dashboard.

## Rendered sections

- Overall health and freshness
- Repository state
- Backlog summary and counts
- Workflow/task state
- Blockers
- Pending approvals
- Latest execution/test summary
- Recent reports
- Recent timeline/events
- Pull-request metadata when supplied
- Degradation warnings

## Security boundaries

- Read-only only: no mutation endpoints or controls.
- All rendered text is HTML-escaped.
- JSON warnings suppress raw diagnostic detail to avoid leaking exception text.
- HTML warnings show source, severity, and message only.
- Lists are bounded by the ENGDASH-001 snapshot model and deterministic render
  ordering.
- Missing PR metadata or unavailable sources degrade to warnings rather than
  server failures.

## Limitations

- Live GitHub PR metadata is not fetched in ENGDASH-002; use the injected
  ENGDASH-001 `PullRequestSummary` interface.
- Repository metadata collection remains external to the read model/app.
- The standalone default app is intentionally degraded until wired to a real
  read-model provider.
