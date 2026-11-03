# Agent File Ownership

Ownership determines which specialist should normally implement a task.

## engineering-manager

Primary responsibilities:

- Own planning and review for ALL code changes (trading bot AND Benny's Games).
- Act as the dispatch target for code work.
- Plan the task, delegate implementation to the appropriate specialist, review the
  resulting diff, verify acceptance evidence, and open the PR.
- Maintain the engineering planning and review documentation.

The engineering-manager does NOT write production code itself and never merges.

## trading-manager

Primary responsibilities:

- Strategy and research planning only:
  - Backlog of STRAT tasks.
  - Research reviews.
- AGENT_OPERATING_PLAN.md
- AGENT_BACKLOG.md
- OWNERS.md
- MENTOR.md
- Planning and review documentation

This role explicitly does NOT plan code implementation and does NOT dispatch code
work; code implementation planning and code dispatch belong to the
engineering-manager.

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

Code-file coordination goes through the engineering-manager. Strategy and
configuration shared files still coordinate with the trading-manager.

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

