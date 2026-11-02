# Agent Branching Rules

## Branch prefixes

Trading backend and strategy work:

`agent/trading-<TASKID>-<YYYYMMDD-HHMM>`

Dashboard work:

`agent/dashboard-<TASKID>-<YYYYMMDD-HHMM>`

Integration work:

`agent/integration-<TASKID>-<YYYYMMDD-HHMM>`

## Other accepted branch forms

Alongside the agent prefixes above, real practice also uses these forms. All
of them are permitted as long as they follow the rules below:

- Research: `research/strat-NNN-<slug>` — including stacked bases on other
  research branches (e.g. `research/strat-027-x` based on
  `research/strat-026-y`).
- Documentation: `docs/<slug>`
- Bug fixes: `fix/<slug>`
- Dashboard (non-agent): `dashboard/<slug>`
- Features: `feature/<TASKID>-<slug>`

## Rules

- Every new branch name must contain the task ID (the backlog/issue ID, e.g.
  `ENG-04c`, `STRAT-026`, `BOT-003`). `docs/`, `fix/`, `dashboard/`, and
  `feature/` branches satisfy this by including the ID in the slug or as a
  `<TASKID>-` component; `research/strat-NNN-<slug>` satisfies it with the
  `strat-NNN` number.
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
