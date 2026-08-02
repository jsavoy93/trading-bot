# Agent Branching Rules

## Branch prefixes

Trading backend and strategy work:

`agent/trading-<issue-id>-<YYYYMMDD-HHMM>`

Dashboard work:

`agent/dashboard-<issue-id>-<YYYYMMDD-HHMM>`

Integration work:

`agent/integration-<issue-id>-<YYYYMMDD-HHMM>`

## Rules

- Start from the approved base branch.
- Never work directly on main.
- Never force-push main.
- One backlog item per branch.
- One bounded iteration per invocation.
- Commits must mention the backlog issue ID.
- Agents may push branches.
- Agents may prepare pull requests.
- Agents may not merge pull requests.
- Josh approves or rejects every merge.

