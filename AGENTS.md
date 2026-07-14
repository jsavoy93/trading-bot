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

