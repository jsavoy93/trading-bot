# Trading Bot Workspace

> **EVERY TIME I work on this codebase: I must say out loud:\n> \"I'm reading MENTOR.md for instructions\"\n> before I answer any question about the trading bot.**

> **⚠️ This is a living document. When I discover something important, make a significant change, or fix a bug — I must update MENTOR.md and/or MEMORY.md so the knowledge persists.**

**FIRST: Read `MENTOR.md` before answering any questions about this codebase.**
It contains the correct understanding of how analysis, failures, scores, and the database tables actually work — and documents several mistakes to avoid.

Key things that were misunderstood in past sessions:
- `failed_analyses` = every HOLD signal, NOT errors. 97% "failure rate" was normal.
- `analysis_failures` in `analyzed_stocks` = actual data/API failures only
- `min_score_buy` defaults to 50 (check settings_service, not the class default of 65)
- Only "No market data" in failed_analyses = real Alpaca data problem

## Agent Operating Safety Rules

Agents in this repository must follow `AGENT_OPERATING_PLAN.md`.

Hard rules:

- Do not enable live trading.
- Do not use or request live brokerage credentials.
- Do not merge branches.
- Do not push directly to main.
- Do not delete, move, or archive whole files without Josh's explicit approval.
- Do not run endless autonomous loops.
- Do not modify secrets, `.env`, database files, generated result files, or OpenClaw config unless explicitly authorized.
- Stop if the repository is dirty.
- Stop if tests fail after the allowed attempt.
- Stop if the task requires destructive migration.
- Stop if acceptance criteria are unclear.
- Prefer branch-based, test-backed work.
- Report exact files changed, tests run, risks, and next step.


## Controlled Agent Workflow

All agents must follow `AGENT_OPERATING_PLAN.md`, `AGENT_BACKLOG.md`, and `OWNERS.md`.

Before modifying files:

1. Verify the repository is clean.
2. Select one approved backlog item.
3. State measurable acceptance criteria.
4. State the files allowed to change.
5. Create or use the assigned agent branch.

Hard restrictions:

- Never merge into main.
- Never push directly to main.
- Never enable live trading.
- Never use live brokerage credentials.
- Never modify secrets or OpenClaw configuration without explicit authorization.
- Never delete, move, or archive whole files without Josh's approval.
- Never expand the assigned scope.
- Never run an endless improvement loop.
- Stop if another iteration is already active.
- Stop if the repository is dirty.
- Stop if required tests fail.
- Stop if live trading configuration is detected.
- Stop if acceptance criteria cannot be explained clearly.

Every final report must include:

- Backlog item
- Branch
- Commit
- Files changed
- Tests run
- Test results
- Backtest results, when relevant
- Known risks
- Manager review decision
- Clear request for Josh's approval

### Acceptance Evidence (Required)

For every completed backlog item, the report must include an **Acceptance Evidence** section with:

1. **Each acceptance criterion** — the original criterion text
2. **Proof method** — exact command or inspection used to verify it
3. **Exact result** — the actual output, output snippet, or observation
4. **Status** — `PASS` or `FAIL`

If an acceptance criterion cannot be directly proven, mark the item **incomplete** and request a follow-up instead of claiming completion. Do not assume or assert a criterion is met without evidence.

### Manager Task Reporting Requirements

Every manager task report must include the following timing and status fields:

- **Task start time** — timestamp (UTC) when work began
- **Task end time** — timestamp (UTC) when work stopped
- **Elapsed time** — total wall-clock duration of the task
- **Continuity** — whether the task ran continuously or was resumed after a gap
- **Stale/blocked status** — if the task could not complete in a reasonable time, the manager must report it as blocked or stale
- **Resumed-task explanation** — before continuing work on a resumed task, the manager must explain why it was paused and what has changed to allow resumption

A task is considered stale when it has been inactive for more than 48 hours without a status update. The manager must report stale status to Josh before continuing any resumed task.

