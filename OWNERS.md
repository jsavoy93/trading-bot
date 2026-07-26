# Agent File Ownership

Ownership determines which specialist should normally implement a task.

## trading-manager

Primary responsibilities:

- AGENT_OPERATING_PLAN.md
- AGENT_BACKLOG.md
- OWNERS.md
- MENTOR.md
- Planning and review documentation

The manager coordinates shared-file changes but should not normally implement production code.

## trading-exec

Primary areas:

- src/core/
- src/analysis/
- src/execution/
- tests/
- backtest-related source code
- brokerage integrations
- strategy logic
- risk controls

Preferred branch prefix:

`agent/trading-`

## dashboard-agent

Primary areas:

- dashboard.py
- templates/
- static/
- frontend/
- dashboard-related API routes
- settings UI
- dashboard tests
- observability and explanation views

Preferred branch prefix:

`agent/dashboard-`

## Shared Files

These require manager coordination:

- Strategy configuration schema
- Settings service
- Shared API contracts
- Database migrations
- Requirements and dependency files
- Deployment scripts
- Service configuration

Only one agent may edit a shared file during an iteration.

## Forbidden Without Explicit Approval

- .env files
- Secret files
- OpenClaw configuration
- Live brokerage configuration
- Production service files
- Database files
- Generated backtest results
- main branch history
- Destructive migrations

