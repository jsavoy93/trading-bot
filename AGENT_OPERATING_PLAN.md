# Agent Operating Plan

This repository uses OpenClaw agents as a controlled development and paper-testing system.

## Roles

### Moose
Supervisor and reporter. Communicates through Telegram, summarizes progress, reports status, and accepts high-level goals. Moose does not normally edit trading strategy code.

### trading-manager
Planner and reviewer. Runs one bounded improvement cycle at a time. Selects approved backlog items, defines acceptance criteria, delegates to trading-exec, reviews diffs, verifies tests, pushes branches, reports, then stops. It never merges into main.

### trading-exec
Implementation agent. Makes only requested code changes, adds or updates tests, runs checks, commits to assigned branch, pushes branch, and reports results. It does not choose strategy direction or merge its own work.

### dashboard-agent
Dashboard, observability, settings, controls, and explanations agent. Makes the dashboard a trustworthy control center. It may create branches and commits, but cannot merge. It must not invent trading strategy logic.

## Core Safety Rules

- No agent may merge into main.
- No agent may enable live trading.
- No agent may use live brokerage credentials.
- No agent may delete, move, or archive whole files without explicit approval.
- No agent may run continuous unbounded loops.
- No agent may make strategy changes without tests and acceptance criteria.
- No agent may touch secrets, credentials, generated database files, or live trading settings unless explicitly authorized.
- Each iteration must stop after one bounded task.
- Repo must be clean before each iteration starts.
- User approval is required before merging or deploying.

## Development Flow

1. Verify safe environment.
2. Confirm clean repository.
3. Select one approved backlog item.
4. Define acceptance criteria.
5. Create branch.
6. Delegate implementation.
7. Run tests and lightweight checks.
8. Review diff.
9. Commit and push branch.
10. Report through Telegram.
11. Stop.

## Initial Backlog Priority

1. Establish trustworthy tests.
2. Fix settings/config synchronization.
3. Normalize score calculations.
4. Repair broken execution paths.
5. Clean the stock universe.
6. Simplify pullback strategy.
7. Build dashboard decision funnel.
8. Add safe paper-bot controls.
9. Run bounded paper tests only after approval.

