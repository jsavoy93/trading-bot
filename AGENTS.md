# Trading Bot Workspace

> **EVERY TIME I work on this codebase: I must say out loud:\n> \"I'm reading MENTOR.md for instructions\"\n> before I answer any question about the trading bot.**

> **⚠️ This is a living document. When I discover something important, make a significant change, or fix a bug — I must update MENTOR.md and/or MEMORY.md so the knowledge persists.**

**FIRST: Read `MENTOR.md` before answering any questions about this codebase.**
Also read `TRADING_BOT_AUTONOMOUS_ENGINEERING_HANDOFF.md` before making architectural or workflow changes.
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
- **Architectural constraint**: No engineering service may directly access
  repository-specific filesystem paths, filenames, repository names, or workflow
  store locations except through approved platform adapters. All project access
  must flow through `ProjectContext` once ENGPLAT-002B is complete.

  The current named-file list covers existing reusable engineering services:
  `manager.py`, `backlog.py`, `reporter.py`, `qa_runner.py`, `event_store.py`,
  `workflow_store.py`, `query_service.py`, `git_service.py`, `config.py`.

  Future reusable engineering services — including ENGDASH-005, ENGSUP-001,
  ENGDASH-006, and ENGCTRL-001 — must also follow the same adapter-boundary rule
  and are expected to be added to the named-file list when their implementation
  is approved.

  Adapter implementations are the approved filesystem-access boundary. Tests,
  bootstrap tools, migration tools, and project-local application code are not
  automatically governed by this rule; they require their own explicit scope.
  Ordinary trading-bot runtime code under `src/` is not being prohibited from
  legitimate project-local filesystem access by this platform rule.
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

## Reporting Requirements

For every completed or stopped task, the agent must select exactly one reporting mode before creating any report artifact.

### Automatic reporting-mode selection

Use **read-only reporting mode** when any part of the task is a merge readiness check, merge execution, audit, review, dependency gate check, preflight validation, verification-only task, documentation inspection, or promises not to modify the repository. Also use read-only reporting mode whenever the task requires `git diff --check`, a clean `git status`, a merge, a push, or merge-readiness verification. These rules take precedence over implementation reporting even when the operation changes Git refs or combines verification with an otherwise modifying workflow.

Use **implementation reporting mode** only for a normal engineering task that modifies repository content and does not match any read-only condition above. If classification is uncertain, select read-only reporting mode so reporting cannot dirty the repository.

The selection is automatic. The user must never need to request a reporting exception.

### Implementation reporting mode

Creating `REPORT.md` and `reports/<timestamp>_<task>.md` is considered part of the reporting process rather than part of implementation scope. These files may be created even when they are not listed in the allowed implementation files, unless the task explicitly prohibits repository-local reports.

REPORT.md is the current rolling report. It must be completely overwritten for every task. Never append to an existing REPORT.md.

1. Write a concise executive report to `REPORT.md` in the repository root.
2. Create `reports/` if it does not exist, then archive a timestamped copy at:
   `reports/YYYY-MM-DD_HHMMSS_<TASK-ID-or-purpose>.md`.
3. Use a UTC timestamp and a filesystem-safe task ID or purpose in the archived filename.
4. Write both report files before printing the terminal response.
5. Never overwrite an archived report. If the intended archive path already exists, use a new timestamp or another unique filesystem-safe purpose suffix.

`REPORT.md` must always contain:

- Executive summary
- Task or purpose
- Branch
- Commit
- Files changed
- Tests run
- Exact test summary
- Overall acceptance result
- Known risks
- Manager decision
- Next recommended action

`REPORT.md` must not contain:

- Full criterion-by-criterion acceptance evidence
- Long command output
- Complete diffs
- Stack traces longer than a short diagnostic excerpt
- Repeated sections
- Full archived-report contents

`REPORT.md` must contain no more than 150 lines, with no repeated headings or duplicated content.

The timestamped archive at:

`reports/YYYY-MM-DD_HHMMSS_<task>.md`

must contain the complete detailed record, including:

- Every acceptance criterion
- Proof method
- Exact result
- PASS or FAIL
- Full relevant diagnostics
- Timing and continuity fields
- Detailed risks
- Complete stopped or failure state when applicable

The archive is the authoritative audit record. `REPORT.md` is only the current executive summary.

### Read-only reporting mode

Read-only reporting must never create or modify `REPORT.md`, `reports/`, `ITERATION_PROGRESS_LOG.md`, or any other repository file. Write only the authoritative timestamped archive outside the repository at:

`/root/.openclaw/audit-archives/<repository-name>/YYYY-MM-DD_HHMMSS_<task>.md`

Derive `<repository-name>` from the repository root directory name. Use the same UTC timestamp, filesystem-safe task name, archive format, acceptance evidence, diagnostics, timing, continuity, risks, and stopped/failure requirements as the repository-local archive. Never overwrite an external archive; choose a new timestamp or unique filesystem-safe suffix on collision.

The external archive is the authoritative audit record for a read-only task. Do not create a rolling `REPORT.md` for that task. If the external archive cannot be written, stop and report the archive failure without falling back to a repository-local artifact.

This mode guarantees that reporting cannot change `git status` during clean-tree validation, merge, push, audit, review, or other read-only work.

The terminal response is only a completion summary.

It must:
- never exceed 15 lines,
- always fit on a single terminal screen without scrolling,
- never include the contents of REPORT.md,
- never include the contents of archived reports,
- contain only:

Task:
Decision:
Branch:
Commit:
Tests:
Report:
Archive:
Next:

Before the terminal summary, implementation reporting mode must write both `REPORT.md` and `reports/YYYY-MM-DD_HHMMSS_<task>.md`. Read-only reporting mode must write only `/root/.openclaw/audit-archives/<repository-name>/YYYY-MM-DD_HHMMSS_<task>.md` and must use `Report: Not created (read-only task)` in the terminal summary.

If an implementation task fails or stops early, the agent must still write `REPORT.md` and its repository-local archive before printing the terminal response. If a read-only task fails or stops early, it must still write its external archive without modifying the repository. The authoritative archive must include the exact failure, stopped state, files changed, tests run, and approval needed, in addition to all otherwise applicable archive fields. An implementation `REPORT.md` must contain only the concise executive summary of that stopped or failed task.

`REPORT.md` is the current rolling report and must remain listed in `.gitignore`. The `reports/` directory must not be added to `.gitignore`; archived reports are reviewable and may be committed when the task requires it.


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

Every manager task report must follow the standardized bounded format below.
Timing and status fields (start time, end time, elapsed, continuity, stale/blocked)
are preserved and should appear within the executive summary or decision packet as
appropriate.

#### 1. Telegram Executive Summary

Always begin with exactly these fields:

```
Task:
Decision:
Branch:
Commit:
Tests:
Report:
PR:
Blockers:
Next:
```

Rules:
- Keep under 15 lines
- One value per field
- If unavailable: write "Unavailable"
- Do not repeat this section later in the same response

#### 2. Decision Packet

After the executive summary, include only sections relevant to the current decision.

Allowed headings:
- Scope
- Files Changed
- Acceptance Criteria
- Test Evidence
- Blocking Findings
- Non-Blocking Findings
- Risks
- Decision Required
- Recommended Next Action

Rules:
- Do not restate the executive summary
- Keep each section bounded
- Prefer tables only when they materially improve comparison
- Do not include full source-code signatures unless Josh specifically requested them
- Distinguish verified facts, proposed design, assumptions, and recommendations

#### 3. Detailed Archive

Put exhaustive details in `REPORT.md` and the timestamped archive at
`reports/YYYY-MM-DD_HHMMSS_<task>.md`. Telegram summarizes the archive rather
than reproducing it. The archive contains:
- Full protocol signatures
- Complete acceptance criteria
- Complete test plan
- Line-by-line review evidence
- Dependency matrices
- Long architectural rationale

#### 4. Duplicate Prevention

Every manager run has a unique `run_id`. Before emitting a completion packet:
- Compare task_id, branch, head_commit, decision, and run_id with the last emitted packet
- Do not emit an identical packet twice
- On retry: "Duplicate delivery suppressed; prior packet remains authoritative."

#### 5. Message Chunking

If the platform splits a long Telegram response:
- Label chunks as Part 1/N, Part 2/N
- Never restart the packet from the beginning
- Keep the executive summary entirely in Part 1
- Put exhaustive details in the archive instead of additional Telegram chunks

#### 6. Evidence Discipline

For every completion packet:
- Verify branch, commit, working-tree state, PR state, and test results directly
- Do not trust prior prose as the sole source
- Identify proposed details as proposals rather than verified implementation
- Josh remains the approval authority

#### 7. Recommended Next Prompt

End every manager packet with one bounded, ready-to-paste recommended prompt when
another action is needed. The prompt must:
- Reflect the current verified state
- Preserve human approval gates
- Avoid repeating the full history
- State exact scope and stop conditions
- Not be included when the correct action is simply to wait

#### 8. Apply Going Forward

All future manager tasks use this format. Do not rewrite historical reports.

#### Stale Task Rule

A task is considered stale when inactive for more than 48 hours without a status
update. The manager must report stale status to Josh before continuing a resumed task.
