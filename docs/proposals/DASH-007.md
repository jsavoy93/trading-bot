# DASH-007 — Add read-only engineering dashboard

## Objective

Add a separate loopback-only engineering dashboard that renders the shared
OPS-014 authoritative read projection: current task, workflow timeline,
delegation status, backlog, acceptance evidence, tests, reports, PR links,
goals, gaps, and recommended next steps. It must remain isolated from the
trading dashboard and expose no mutation capability.

## Priority

P1

## Owner

dashboard-agent

## Exact acceptance criteria

1. A separate engineering dashboard renders current task, workflow-state
   timeline, agent/run status, backlog and priorities, acceptance criteria,
   tests, reports, PR links, current goals, remaining gaps, and recommended next
   steps exclusively from `EngineeringQueryService`.
2. Every panel handles no active workflow, legacy/incomplete evidence,
   malformed or unavailable source state, failed, blocked, stale, PR-ready,
   approval-required, and completed states explicitly and safely.
3. The dashboard is read-only: only GET and HEAD routes exist; no route, form,
   WebSocket, background worker, or client script mutates state or invokes
   shell, Git, GitHub, Telegram, Codex, brokerage, or trading operations.
4. Responses are bounded, escaped, paginated, secret-redacted, and use stable
   deterministic ordering derived from the shared projection.
5. The server binds to loopback by default. Public exposure, TLS, authentication,
   and reverse-proxy configuration are external deployment concerns and are not
   added by this task.
6. Timeline and notification views consume the same OPS-014 event records used
   by Telegram; route and template code do not reconstruct an independent
   workflow model.
7. PR links render only validated persisted HTTPS GitHub metadata. Missing PR
   links display `not recorded`; goals render only explicit durable goal data,
   otherwise `no current goal recorded`.
8. No raw stdout/stderr, prompts, full diffs, arbitrary files, environment,
   tokens, credentials, trading positions, or account information is exposed.
9. Tests use temporary event/workflow stores and injected view models; no
   brokerage client, external network, or production database is constructed.
10. Focused dashboard/query/security tests pass, followed by the complete safe
    suite with the live-brokerage safety gate intact.

## Allowed files

- `engineering/dashboard.py` (new)
- `engineering/dashboard_service.py` (new)
- `engineering/query_service.py`
- `engineering/event_projection.py`
- `templates/engineering_dashboard.html` (new)
- `static/engineering_dashboard.css` (new, only if required)
- `tests/test_engineering_dashboard.py` (new)
- `tests/test_engineering_dashboard_service.py` (new)
- `tests/test_engineering_query_service.py`
- `tests/test_engineering_event_projection.py`
- `AGENT_BACKLOG.md`
- `MENTOR.md`
- `TRADING_BOT_AUTONOMOUS_ENGINEERING_HANDOFF.md`
- `ITERATION_PROGRESS_LOG.md`
- required reporting artifacts under the repository reporting policy

## Prohibited files

- `dashboard.py`, `templates/dashboard.html`, and existing trading-dashboard
  routes/assets
- `src/core/`, `src/analysis/`, `src/execution/`, and brokerage integrations
- `main.py`
- `.env`, secrets, credentials, and OpenClaw configuration
- `trading_bot.db` and generated trading/backtest results
- deployment, reverse-proxy, TLS, service, cron, or systemd files
- Codex wrapper launch logic and interactive TUI integration
- `main` branch history

## Security constraints

- Separate module/process from the trading dashboard; importing it must not
  construct trading or brokerage clients.
- Loopback bind by default and strict host validation; no built-in public mode.
- GET/HEAD only, with mutation methods returning 404 or 405.
- Escape all untrusted values; use a restrictive CSP and security headers; do
  not inject event/report text as trusted HTML.
- No arbitrary path/query-to-file mapping and no raw archive downloads.
- Enforce server-side page, task, event, criterion, and excerpt limits.
- Use the shared sanitizer/projection; never pass through unknown event fields.
- Persist no browser session, control intent, token, or analytics data.

## Implementation sequence

1. Confirm OPS-014 is merged and stabilize the shared query response contract.
2. Add a pure dashboard view model/service with no web framework dependency.
3. Add bounded read-only JSON endpoints.
4. Add the server-rendered overview and timeline.
5. Add backlog, criteria, tests, report, PR, goal, gap, and next-step panels.
6. Add security headers, loopback defaults, pagination, and explicit error/no-data
   states.
7. Run focused route/view/query/security tests and the full safe suite.
8. Stop for Josh's review; do not deploy or expose the dashboard.

## Focused tests

- View-model mapping for every required panel and deterministic ordering.
- No-active, active, failed, blocked, stale, report-ready, PR-ready,
  approval-required, completed, legacy, and malformed-source fixtures.
- Route method matrix proving only GET/HEAD are available.
- Loopback bind default and invalid host/config rejection.
- XSS, HTML/script injection, path traversal, oversized payload, pagination, and
  secret-canary tests.
- Missing/invalid PR URL and missing goal behavior.
- Corrupted/unavailable event or workflow store returns a bounded safe error.
- Import test proving no brokerage client, trading client, network call,
  subprocess, GitHub, Telegram, or Codex boundary is constructed.
- Shared projection contract tests proving UI code does not rebuild workflow
  state independently.

## Full-suite verification

After focused tests pass, run:

```text
TESTING=1 UNIT_TESTING=1 .venv/bin/python -m pytest -q
```

The safety banner must confirm paper defaults and live brokerage calls blocked.
No browser automation may contact an external host.

## Known risks

- Report and workflow evidence currently exist in multiple durable locations;
  precedence must remain centralized in the query service.
- PR and goal producers may initially be absent, so the UI must display gaps
  rather than inferred values.
- Public exposure without authentication leaks engineering metadata; this
  proposal permits loopback only.
- A large event history can degrade rendering without pagination and limits.
- Reusing trading-dashboard code could initialize brokerage-facing globals;
  this is explicitly prohibited.

## Dependencies

- OPS-014 merged and verified.
- Josh review and explicit approval of this proposal.
- Addition of DASH-007 to `AGENT_BACKLOG.md` with these criteria and paths.
- Dedicated DASH-007 feature branch created from current `main`.
- No dependency on OPS-015 for initial implementation; both consume the same
  OPS-014 query contract, but shared files must be edited sequentially.

## Definition of done

- Every acceptance criterion has criterion-level PASS evidence.
- Every requested panel is present and handles missing/legacy state safely.
- Focused and full safe suites pass with exact evidence.
- Security tests prove read-only methods, escaping, limits, loopback default,
  and absence of brokerage/network/process side effects.
- Only allowed files and required reporting artifacts changed.
- A review PR is created without merge or deployment.

## Explicitly out of scope

- Any write/control/approval endpoint
- Telegram transport or notification delivery
- Public hosting, TLS, authentication proxy, deployment, or service management
- Trading dashboard changes or combined trading/engineering UI
- Raw logs, artifacts, prompts, diffs, files, trading positions, or account data
- PR creation or GitHub polling; only persisted validated links are displayed
- Goal creation or modification
- Interactive Codex TUI control or agent launch
- CONFIG-001 and all strategy/settings work
