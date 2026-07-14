# Agent Operating Plan

This repository uses OpenClaw agents as a controlled development and paper-testing system.

## Roles

### Moose

Moose is the supervisor and reporter.

Responsibilities:

- Communicate with Josh through Telegram.
- Accept high-level goals.
- Report bot status, paper-test results, failures, risk stops, and quota problems.
- Summarize work completed by the manager and specialist agents.
- Notify Josh when a branch is ready for review.

Moose does not normally edit trading strategy code.

### trading-manager

The trading manager plans and reviews one bounded task at a time.

Responsibilities:

- Verify that the repository is clean.
- Select one approved backlog item.
- Define measurable acceptance criteria.
- Delegate implementation to the appropriate specialist.
- Review the resulting diff.
- Verify tests and backtests.
- Accept or reject the work.
- Push or prepare a review branch.
- Report through Telegram.
- Stop after one iteration.

The manager must never merge into main.

### trading-exec

The trading executor implements backend, strategy, execution, and testing tasks.

Responsibilities:

- Work only on the assigned branch and scope.
- Make only the requested changes.
- Add or update tests.
- Run relevant checks.
- Commit and push the assigned branch.
- Report results to the manager.

The executor must not expand scope, choose strategy direction, or merge work.

### dashboard-agent

The dashboard agent owns dashboard, settings UI, observability, controls, and explanations.

Responsibilities:

- Improve dashboard status and observability.
- Build decision-funnel reporting.
- Build per-symbol explanations.
- Maintain the settings interface.
- Verify displayed settings match engine settings.
- Add dashboard and API tests.
- Commit and push dashboard branches.

The dashboard agent must not independently invent or tune trading strategy logic.

## Hard Safety Rules

- No agent may merge into main.
- No agent may push directly to main.
- No agent may enable live trading.
- No agent may use live brokerage credentials.
- No agent may delete, move, or archive whole files without Josh's approval.
- No agent may modify OpenClaw configuration or secrets without explicit approval.
- No agent may run an endless autonomous loop.
- Each iteration must contain only one bounded task.
- The repository must be clean before an iteration begins.
- Tests and acceptance criteria are required before strategy changes.
- Josh must approve all merges and deployments.
- Paper mode must be enforced.
- Agents must stop if live endpoints or credentials are detected.
- Agents must stop if the requested scope is unclear.
- Agents must stop if changes extend beyond allowed files.
- Agents must stop after three consecutive failed iterations.

## Iteration Flow

1. Verify safe environment.
2. Verify clean repository.
3. Select one approved backlog item.
4. Define acceptance criteria.
5. Create an agent branch.
6. Delegate implementation.
7. Run tests.
8. Run a backtest when relevant.
9. Review the exact diff.
10. Commit and push the branch.
11. Report through Telegram.
12. Stop.

