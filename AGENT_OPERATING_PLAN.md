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
- Record task start time before beginning work.
- Delegate implementation to the appropriate specialist.
- Review the resulting diff.
- Verify tests and backtests.
- Accept or reject the work.
- Record task end time and compute elapsed time.
- Report continuity status (continuous or resumed) in every report.
- Report blocked or stale status if the task cannot complete in reasonable time.
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
- Only one GLM-heavy job may run at a time (see Model-Quota Discipline).
- Every final report must state which model actually completed the work.
- An independent reviewer-agent verdict is required before asking Josh to
  merge (see Review Gate).

## Session and Dispatch Conventions (ENG-01)

When dispatching a task to an agent session (e.g. `openclaw` subagent dispatch),
the dispatcher must follow:

- **One fresh session key per task**: `agent:<agent>:<TASKID>-<unix-time>`.
  Never reuse a session key such as `grokbot`.
- Use `--thinking low` unless the task needs more.

## Model-Quota Discipline (ENG-05)

`zai/glm-5.1` is a metered provider. Its weekly/monthly allowance is shared
across every agent lane, so concurrent GLM-heavy jobs can exhaust the quota
and then fail over mid-task.

### One GLM-heavy job at a time

- Run only **one** GLM-heavy (judgement-heavy) job at a time. Do not fan the
  same subtask out to several GLM-backed lanes concurrently.
- A GLM-heavy job is any task whose success depends on design, debugging,
  code-change judgement, or multi-step analysis.
- Documentation-only and mechanical text work should be routed to the
  docs-agent (see Model Routing), which does not consume GLM quota.
- If a GLM request fails over because the quota is exhausted, **wait for the
  reset** rather than retrying GLM in a loop. Retrying a quota-exhausted
  provider consumes wall-clock time and produces nothing.

### Quota exhaustion signature

Weekly/monthly quota exhaustion appears as:

```
429 Weekly/Monthly Limit Exhausted. Your limit will reset at <UTC timestamp>
```

Observed reset for the 2026-10-31 exhaustion: **2026-11-02 20:33:34 UTC**.
The reset timestamp is carried in the error text; quote it rather than
guessing. When the provider chain is exhausted entirely, the agent lane
reports `FailoverError: ⚠️ Weekly/Monthly Limit Exhausted`.

### Reporting which model actually did the work

Every final report — manager packet, iteration log entry, archive report —
**must state the model that actually completed the work**, not the model that
was requested. Never report the primary model when a fallback produced the
result.

Evidence sources, in priority order:

1. `executionTrace.winnerModel` — the model that produced the final result.
2. `executionTrace.fallbackUsed` — whether a fallback occurred at all.
3. `model_fallback_decision` lines in `/tmp/openclaw/*.log` — the
   `decision: "candidate_succeeded"` entry names
   `candidateProvider` / `candidateModel` (the model that won), and
   `previousAttempts` lists what failed first.

Example evidence from `/tmp/openclaw/openclaw-2026-10-31.log`:

```json
{"event":"model_fallback_decision","decision":"candidate_succeeded",
 "requestedProvider":"zai","requestedModel":"glm-5.1",
 "candidateProvider":"minimax-portal","candidateModel":"MiniMax-M3",
 "attempt":2,"total":2,
 "fallbackStepFromFailureReason":"rate_limit","status":429,
 "errorPreview":"429 Weekly/Monthly Limit Exhausted. Your limit will reset at 2026-11-02 20:33:34",
 "previousAttempts":[{"provider":"zai","model":"glm-5.1","reason":"rate_limit","status":429}]}
```

Reading it: the requested model was `zai/glm-5.1`; attempt 1 failed with a
429 quota error; the fallback `minimax-portal/MiniMax-M3` succeeded. The
report must therefore say **MiniMax-M3 completed the work**, and note that
GLM's quota was exhausted.

A report that omits the completing model, or that names the requested model
instead of the completing one, is incomplete.

## Model Routing (ENG-06)

Work is routed by **task kind**, not by convenience. The cheaper model handles
mechanical text; the judgement model handles anything where quality of
reasoning determines the outcome.

### Decision table

| Task kind | Examples | Primary | Fallback |
|---|---|---|---|
| Documentation / mechanical text | final reports, `ITERATION_PROGRESS_LOG.md` entries, `reports/` archive files, log excerpts, changelog text | `docs-agent` — `minimax-portal/MiniMax-M3` | `zai/glm-5.1` |
| Design & architecture | API/interface design, migration plans, trade-off decisions | `zai/glm-5.1` | `minimax-portal/MiniMax-M3` |
| Code changes | backend, strategy, execution, dashboard implementation, test authoring | `zai/glm-5.1` | `minimax-portal/MiniMax-M3` |
| Debugging | failing tests, runtime faults, data-pipeline diagnosis | `zai/glm-5.1` | `minimax-portal/MiniMax-M3` |
| Multi-step analysis | forensic analysis of trades/failures, cross-table investigations | `zai/glm-5.1` | `minimax-portal/MiniMax-M3` |
| Review / verdict | independent reviewer-agent APPROVE/BLOCK verdict | `zai/glm-5.1` | `minimax-portal/MiniMax-M3` |

### Lanes

- **`docs-agent`** (primary `minimax-portal/MiniMax-M3`, fallback
  `zai/glm-5.1`) handles **only** reports, `ITERATION_PROGRESS_LOG.md`
  entries, `reports/` archive files, and other documentation/mechanical text
  work. It does not write production code and does not make design
  decisions. Its work is still subject to the Model-Quota Discipline above:
  because it does not consume GLM quota, it is the correct lane to run
  *concurrently* with a GLM-heavy job, but only one GLM-heavy job at a time.
- **`zai/glm-5.1`** backs `trading-manager`, `trading-exec`, `dashboard-agent`,
  and `engineering-manager`. These lanes handle design, code changes,
  debugging, multi-step analysis, and anything else where judgement quality
  matters.

### Routing rules

- Choose the lane by the **highest-quality requirement** in the task. A task
  that is "mostly docs" but contains a design decision routes to the GLM
  lane for the design portion.
- Never route a design or debugging decision to `docs-agent` because the
  quota is currently exhausted. Route it to the GLM lane and accept the
  fallback, or wait for the reset — but do not downgrade the reasoning
  quality of the work.
- The routing decision, the lane used, and the completing model are recorded
  in the task's final report.
- When the primary model for the lane is exhausted and a fallback model
  completes the work, the report states the completing model explicitly (see
  Model-Quota Discipline).

## Review Gate (ENG-07)

An **independent reviewer-agent verdict** (`APPROVE` or `BLOCK` with reasons)
is a **required** step before asking Josh to merge.

- The reviewer is a separate agent session that did not author the change.
- The reviewer works **read-only**: it reads the PR diff against the task's
  acceptance criteria and returns a verdict. It does not edit, commit, push,
  or merge.
- A `BLOCK` verdict **must be resolved** — fixed and re-reviewed, or
  explicitly accepted by Josh with the reason recorded — before the merge
  request is presented. An unresolved `BLOCK` stops the task as
  `REWORK`/`BLOCKED` with the reasons copied verbatim; it never escalates
  into a merge request.
- The verdict, the reviewer agent id, and the reasons are recorded in the
  report and the `ITERATION_PROGRESS_LOG.md` entry.

The full mechanics (how the verdict is obtained, what the reviewer may and
may not do) live in `BRANCHING.md` under "Review Gate Before Asking Josh to
Merge".

## Iteration Flow

1. Verify safe environment.
2. Verify clean repository.
3. Select one approved backlog item.
4. Define acceptance criteria.
5. Record task start time.
6. Create an agent branch.
7. Delegate implementation.
8. Run tests.
9. Run a backtest when relevant.
10. Review the exact diff.
11. Obtain an independent reviewer-agent verdict (`APPROVE` / `BLOCK`).
12. Resolve any `BLOCK` verdict before requesting a merge.
13. Record task end time and elapsed time.
14. Report continuity status (continuous or resumed).
15. Report blocked or stale status if applicable.
16. Commit and push the branch.
17. Report through Telegram, stating which model completed the work.
18. Stop.

## Infrastructure-Dependent Verification States

For work that depends on an external operational prerequisite such as a second
account, DNS, certificates, email delivery, Telegram, or Slack, report these
facts independently:

- `IMPLEMENTATION COMPLETE` — the approved repository changes are finished.
- `AUTOMATED VERIFICATION PASSED` — required deterministic safe tests pass.
- `MANUAL OPERATIONAL VERIFICATION PENDING` — a real-environment check cannot
  run because its external prerequisite is unavailable.

When implementation and automated verification are complete and no software
defect is known, an unavailable external prerequisite is not a failure. Keep
the machine-parseable backlog status as `REVIEW` and record `Manual Operational
Verification Pending` as review classification/detail, not as the status enum.
Do not reopen implementation, create rework, or request code changes solely to
satisfy the missing prerequisite. Recommend merge only after Josh explicitly
accepts the documented residual operational risk, or defer the one-time check
until the prerequisite becomes available.

Use `FAIL`, `REWORK`, or an implementation blocker only when evidence identifies
an actual software defect, failed automated requirement, unsafe configuration,
or incomplete implementation. Name the missing prerequisite, exact evidence
still required, and residual risk for every pending operational check.

## Deterministic Reporting Location

- Normal repository-modifying engineering tasks use implementation reporting mode: overwrite ignored root `REPORT.md` and write the authoritative timestamped archive under repository `reports/`.
- Merge readiness, merge execution, audit, review, dependency gates, preflight validation, verification-only work, documentation inspection, and any task promising no repository modification use read-only reporting mode.
- Tasks requiring `git diff --check`, clean `git status`, merge, push, or merge-readiness verification also use read-only reporting mode, even when combined with another operation.
- Read-only reporting writes no repository artifact. Its only archive is `/root/.openclaw/audit-archives/<repository-name>/YYYY-MM-DD_HHMMSS_<task>.md`.
- If classification is uncertain, use read-only mode. Never require the user to request an exception, and never fall back to a repository-local report if the external archive fails.

## Scope Control (project reset 2026-09-26)

Durable rules to prevent rabbit-hole optimization chains after a feature lands. Full exposition lives in `MENTOR.md` under "Scope Control — Stop When Done". The condensed operating rules:

1. **Acceptance criteria define done.** When criteria are satisfied, close the task. Possible future improvements do not keep the task open.
2. **Classify findings before acting.** `BLOCKER` / `SHOULD-FIX` / `PARKING LOT`. A parking-lot item must not automatically become the next task.
3. **No automatic adjacent work.** Completing task X does not authorize task X+1. Explicit owner authorization is required for every new implementation.
4. **Two-hop circuit breaker.** If a follow-up produces another follow-up, stop and return to the roadmap before drilling further downward.
5. **Performance requires a target.** "Could be faster" is not sufficient reason for another optimization phase. Once a usability target is met, optimization stops.
6. **Transitional problems.** Do not heavily optimize temporary/transitional behavior unless it creates a material current problem. Prefer letting transitional states age out when doing so is safe and cheaper.
7. **Audits do not authorize implementation.** Audit findings must be reported; they are not implemented unless the owner explicitly authorizes implementation.
8. **Final stop question.** Every substantial final report should answer: "If we stop here, what material capability or risk remains unresolved?" If the answer is effectively none, recommend closure rather than another adjacent phase.
9. **Roadmap reset.** After a substantial feature chain or two levels of follow-up work, return to the project roadmap before proposing additional adjacent work. The goal is to improve the overall trading system, not to perfect the current subsystem.

## Approved Engineering-Platform Roadmap Priority (2026-08-05)

After ENGDASH-004 merged via PR #13 at merge commit
`31f455fb04a6ffff7adbec2bfbf743bc4b1ac1ed`, Josh approved this next
platform priority order:

1. `ENGPLAT-001` — Project Registration and Managed-Project Configuration
2. `ENGDASH-005` — Engineering Timeline and Historical Activity
3. `ENGPLAT-002` — Repository and Project Adapter Boundaries
4. `ENGSUP-001` — Automated Engineering Supervisor and Structured Handoff Protocol
5. `ENGDASH-006` — Live Agent Activity and Execution Visibility
6. `ENGCTRL-001` — Safe Engineering Control Panel
7. `CONFIG-002` — Dashboard-to-engine synchronization
8. `ENGPLAT-003` — Reusable Engineering Platform Repository Extraction (explicitly deferred)

Operational constraints for this roadmap:

- `ENGPLAT-002` depends on `ENGPLAT-001`.
- `ENGDASH-005` depends on `ENGPLAT-001` and `ENGDASH-004`; should consume the
  project boundary from `ENGPLAT-001` where practical.
- `ENGSUP-001` depends on `ENGPLAT-001` and `ENGPLAT-002`; Phase 1 (prompt
  generation) begins after adapters are proven; auto-dispatch requires separate
  Phase 2 Josh approval.
- `ENGDASH-006` depends on `ENGDASH-004` and must avoid a competing
  workflow-state model.
- `ENGCTRL-001` follows stable dashboard/query boundaries after `ENGDASH-005`
  and `ENGDASH-006`, and requires separate Josh approval after read-only design
  review.
- `ENGPLAT-003` repository extraction is explicitly deferred until
  `ENGPLAT-001` and `ENGPLAT-002` are proven through normal use and Josh
  separately approves cross-repository planning.
- `CONFIG-002` remains queued behind these engineering-platform priorities
  unless Josh later changes the priority.
- Roadmap entries do not authorize broad allowed areas. Each implementation
  requires a separate narrow allowed-area remediation and Josh approval.
