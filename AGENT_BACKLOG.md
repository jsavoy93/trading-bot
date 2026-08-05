# Agent Repair Backlog

Agents may work only on items listed here or explicitly approved by Josh.

## Status Values

- TODO
- IN_PROGRESS
- BLOCKED
- REVIEW
- DONE

---

## Approved Engineering Platform Priority Order (2026-08-05)

Josh approved the next engineering-platform priorities after ENGDASH-004 merged
via PR #13 at merge commit `31f455fb04a6ffff7adbec2bfbf743bc4b1ac1ed`.

Priority order:

1. `ENGPLAT-001` — Project Registration and Managed-Project Configuration
2. `ENGDASH-005` — Engineering Timeline and Historical Activity
3. `ENGPLAT-002` — Repository and Project Adapter Boundaries
4. `ENGDASH-006` — Live Agent Activity and Execution Visibility
5. `ENGCTRL-001` — Safe Engineering Control Panel
6. `CONFIG-002` — Dashboard-to-engine synchronization
7. `ENGPLAT-003` — Reusable Engineering Platform Repository Extraction (explicitly deferred)

Roadmap constraints:

- `CONFIG-002` remains queued behind the engineering-platform work unless Josh
  later changes the priority.
- Future implementation tasks remain non-executable until each receives narrow
  allowed areas and explicit Josh approval for that implementation slice.
- Do not extract a reusable engineering-platform repository until `ENGPLAT-001`
  and `ENGPLAT-002` have been proven through normal use.

---

## Phase O — Governance

### OPS-017 — Add bounded OPS-015 launcher and manual smoke setup

Status: REVIEW
Review classification: Manual Operational Verification Pending
Owner: trading-manager
Priority: P1

Depends on: OPS-014, OPS-015

Verification state:

- IMPLEMENTATION COMPLETE
- AUTOMATED VERIFICATION PASSED
- MANUAL OPERATIONAL VERIFICATION PENDING — one denial check from an
  unauthorized second Telegram account remains unavailable. No software defect
  is known; do not reopen implementation or create rework for this prerequisite.

Acceptance criteria:

- Runtime configuration uses exactly `ENGINEERING_TELEGRAM_BOT_TOKEN` and
  `ENGINEERING_TELEGRAM_JOSH_CHAT_ID`. Real values exist only in
  `/etc/trading-bot/ops-015.env`, outside Git, with mode `0600`, owned by the
  invoking operator, and are never requested in chat, committed, printed,
  logged, persisted, included in reports, or passed as command-line values.
- A repository-owned launcher provides the exact supported command
  `.venv/bin/python -m engineering.telegram_service --env-file
  /etc/trading-bot/ops-015.env --smoke --event-store
  .agent-state/telegram-smoke-events.sqlite3 --max-polls 20 --max-seconds
  300`. It validates the environment file, repository paths, both finite
  bounds, fixed Telegram origin, and exact isolated smoke event-store path
  before making a network request.
- Smoke mode is foreground-only, uses Telegram long polling, performs at most
  20 polls and runs at most 300 elapsed seconds, stopping when either bound is
  reached. It handles `SIGINT`/`SIGTERM` with graceful lease release, never
  daemonizes, never polls endlessly, never restarts automatically, and never
  installs, starts, enables, or modifies a service.
- Before the real smoke test, an operator verifies that Josh initiated a
  private chat and that the observed `message.chat.id` and `message.from.id`
  are identical to the configured positive numeric allowlist. The token and
  inbound message text are not displayed as evidence.
- The approved Josh private chat receives deterministic bounded responses for
  `/status`, `/current`, and `/report`; exact observed responses are recorded
  with secret-free state context. No other read command, raw report, log,
  artifact, file, environment value, or unbounded output is exposed.
- An unauthorized second Telegram account receives no response. Exactly one
  sanitized `telegram.access_denied` audit event is verified in the isolated
  `.agent-state/telegram-smoke-events.sqlite3` store, and neither the
  unauthorized ID nor inbound content is persisted or logged. The normal
  `.agent-state/engineering-events.sqlite3` database is never opened, created,
  read, or modified by smoke mode.
- `/pause` and `/resume` are exercised only through
  `EngineeringControlService`; the smoke record proves revisioned idempotent
  state changes and corresponding `manager.paused` and `manager.resumed`
  events in the isolated smoke store. The isolated manager pause record is
  restored to its exact pre-smoke state during cleanup, including when either
  the poll or elapsed-time bound ends the run; the normal engineering manager
  event/control database remains untouched.
- The launcher emits bounded one-line structured JSON logs to stderr for
  startup, configuration validation, poll lifecycle, authorized command type,
  denied access, delivery retry/dead-letter summary, shutdown, and terminal
  outcome. Logs use an explicit field allowlist and never contain tokens,
  chat/sender IDs, message text, query payloads, exceptions with request URLs,
  or raw Telegram responses.
- A competing consumer lease fails closed before polling and exits with code
  `3`. A permanent Telegram transport error is distinguishable from a normal
  bounded smoke completion and exits with code `4`; configuration errors exit
  `2`, runtime/store failures exit `5`, graceful success exits `0`, and signal
  interruption follows the documented shell convention. Transient errors
  retain bounded retry/backoff behavior.
- Automated tests use fake Telegram transport, query/control boundaries,
  clocks, signals, and sleepers and make no external request. After focused
  tests and the full safe suite pass, the separately approved real smoke test
  verifies the full ordered sequence `/status`, `/current`, `/report`,
  `/pause`, repeated `/pause`, `/resume`, and unauthorized `/status`. Cleanup
  stops the launcher, releases or expires leases, restores the pre-smoke
  isolated pause state even at either bound, preserves auditable state,
  removes no file without Josh's approval, and documents token revocation plus
  branch/commit rollback procedures.

Allowed areas:

- engineering/telegram_service.py
- engineering/telegram_adapter.py
- engineering/telegram_transport.py
- engineering/engineering_control.py
- engineering/event_store.py
- engineering/query_service.py
- tests/test_engineering_telegram_service.py
- tests/test_engineering_telegram_adapter.py
- tests/test_engineering_telegram_transport.py
- tests/test_engineering_control.py
- tests/test_engineering_event_store.py
- AGENT_BACKLOG.md
- MENTOR.md
- TRADING_BOT_AUTONOMOUS_ENGINEERING_HANDOFF.md
- ITERATION_PROGRESS_LOG.md

### OPS-015 — Add allowlisted Telegram engineering adapter

Status: REVIEW
Owner: trading-manager
Priority: P1

Depends on: OPS-014

Acceptance criteria:

- A standalone adapter uses Telegram Bot API long polling with finite
  connect/read timeouts, finite exponential backoff, persisted update offset,
  graceful shutdown, and a one-process consumer lease.
- Only the configured Josh chat ID and matching private-chat sender ID are
  accepted; every other chat, sender, forwarded message, channel post, and
  group message is denied and security-audited without echoing its content.
- Task completion, failure, blocked, stale, PR-ready, and approval-required
  events are delivered from the OPS-014 outbox with idempotent delivery
  records, bounded retries, and dead-letter behavior.
- `/status`, `/current`, `/next`, and `/report` read only from
  `EngineeringQueryService` and return deterministic, bounded, sanitized
  responses.
- `/pause` and `/resume` modify only the revisioned deterministic engineering
  manager pause flag through a narrow control service, are idempotent, emit
  audited events, and never signal processes or affect the trading bot.
- Unknown, malformed, or oversized commands return bounded help or rejection;
  command length, arguments, update batches, response size, retries, polling
  duration, and rate are strictly bounded.
- The Telegram bot token and allowlisted identity come from environment or an
  injected runtime provider and are never persisted in events, logs, reports,
  exceptions, test snapshots, or Telegram responses.
- The adapter never imports or invokes interactive Codex, launches agents,
  reads raw stdout/stderr artifacts, executes shell or Git commands, merges,
  pushes, deploys, or changes trading settings.
- All tests use fake Telegram transport, query service, control service,
  clocks, and sleepers; tests make no network call and require no real token.
- Focused Telegram/control/event tests pass, followed by the complete safe
  suite with the existing live-brokerage test gate intact.

Allowed areas:

- engineering/telegram_adapter.py
- engineering/telegram_transport.py
- engineering/engineering_control.py
- engineering/engineering_events.py
- engineering/event_store.py
- engineering/query_service.py
- engineering/manager_driver.py
- tests/test_engineering_telegram_adapter.py
- tests/test_engineering_telegram_transport.py
- tests/test_engineering_control.py
- tests/test_engineering_events.py
- tests/test_engineering_event_store.py
- tests/test_engineering_query_service.py
- tests/test_engineering_manager_driver.py
- AGENT_BACKLOG.md
- MENTOR.md
- TRADING_BOT_AUTONOMOUS_ENGINEERING_HANDOFF.md
- ITERATION_PROGRESS_LOG.md

### OPS-014 — Add durable engineering events and transactional outbox

Status: DONE
Owner: trading-manager
Priority: P0

Acceptance criteria:

- A versioned immutable event schema represents workflow transitions,
  completion, failure, blocked, stale, PR-ready, approval-required,
  delegation, QA, report, pause, and resume facts with UTC times and stable
  identities.
- An isolated SQLite event/outbox store uses an injected path, bounded
  transactions, schema versioning, and ignored `.agent-state` production
  storage; it never uses `trading_bot.db`.
- Event append and destination outbox creation are atomic and idempotent;
  deterministic event IDs and `(event_id, destination)` uniqueness prevent
  duplicates across retries and restarts.
- Claim, lease, recovery, send, retry, and dead-letter transitions are finite,
  validated, crash recoverable, and preserve bounded diagnostics.
- Persisted workflow evidence reconciles into deterministic events after
  saves and completion archival; replay is harmless and event failures are
  reported without losing the durable workflow record.
- A shared bounded query/projection service exposes current task, timeline,
  delegation, backlog, criteria, QA, report, PR, goal/gap, next-step, and pause
  views without raw artifacts or secrets.
- Legacy workflow records remain compatible; no destructive migration,
  trading-data change, network action, subprocess, or Codex launch occurs in
  the event/query layer.
- Focused event/store/projection/query/manager tests and the full safe suite
  pass using temporary databases, fake clocks, and no external services.

Allowed areas:

- engineering/engineering_events.py
- engineering/event_store.py
- engineering/event_projection.py
- engineering/query_service.py
- engineering/workflow_store.py
- engineering/manager.py
- engineering/manager_driver.py
- tests/test_engineering_events.py
- tests/test_engineering_event_store.py
- tests/test_engineering_event_projection.py
- tests/test_engineering_query_service.py
- tests/test_engineering_workflow_store.py
- tests/test_engineering_manager.py
- tests/test_engineering_manager_driver.py
- AGENT_BACKLOG.md
- MENTOR.md
- TRADING_BOT_AUTONOMOUS_ENGINEERING_HANDOFF.md
- ITERATION_PROGRESS_LOG.md

### OPS-013 — Add a bounded restart-safe manager driver

Status: DONE
Owner: trading-manager
Priority: P0

Depends on: OPS-010, OPS-012

Acceptance criteria:

- The existing `python -m engineering.manager` invocation remains the default one-state behavior; repeated advancement requires explicit `--drive` opt-in.
- Drive mode validates finite positive step, elapsed-time, WAIT poll-interval, and WAIT poll-count bounds, with defaults of 8 steps, 900 seconds, 30 seconds, and 20 polls.
- The driver reloads persisted state before every step and atomically persists every result before continuing, waiting, or stopping.
- One driver invocation advances at most one approved backlog task and never starts another task after REPORT or COMPLETE.
- Existing deterministic delegation identity prevents duplicate launches across retries and process restarts; WAIT_FOR_AGENT remains status-only.
- PENDING and ACTIVE delegation states are polled only within all configured finite bounds and through an injectable sleeper; tests never sleep in real time.
- FAILED or TIMED_OUT delegation, failed or timed-out QA, REVIEW rework/rejection, stale state, malformed evidence, exceptions, persistence failures, unchanged non-wait states, and exhausted bounds stop immediately for human review.
- REPORT may generate and persist COMPLETE, after which drive mode stops without dispatching COMPLETE, archiving, clearing, or selecting another task. COMPLETE found at startup also stops for explicit one-state completion handling.
- Persisted backward-compatible driver metadata records UTC timing, accumulated elapsed time, steps, polls, continuity, last stop reason, blocked status, stale status, and resume explanation. Inactivity beyond 48 hours stops as stale.
- Driver tests use fake handlers, stores, clocks, sleepers, and agent statuses; they are deterministic and never sleep in real time.
- Tests fail closed if real Codex, brokerage, subprocess-launch, or network boundaries are invoked; existing live-brokerage safety gates remain unchanged.
- The driver never merges, pushes, deploys, enables live trading, bypasses approval gates, generates tasks, schedules multiple agents, configures cron/systemd, extracts repositories, or starts another backlog task.
- Focused manager-driver and workflow tests pass, followed by the full safe suite.
- `MENTOR.md`, `TRADING_BOT_AUTONOMOUS_ENGINEERING_HANDOFF.md`, and `ITERATION_PROGRESS_LOG.md` document behavior and evidence.

Allowed areas:

- engineering/manager.py
- engineering/manager_driver.py
- engineering/workflow_store.py
- tests/test_engineering_manager.py
- tests/test_engineering_manager_driver.py
- tests/test_engineering_workflow_store.py
- tests/test_engineering_workflow_engine.py
- tests/test_engineering_delegate.py
- tests/test_engineering_wait_for_agent.py
- tests/test_engineering_qa.py
- tests/test_engineering_review.py
- tests/test_engineering_report.py
- tests/test_engineering_complete.py
- tests/test_engineering_executor.py
- tests/test_engineering_codex_cli_wrapper.py
- AGENT_BACKLOG.md
- MENTOR.md
- TRADING_BOT_AUTONOMOUS_ENGINEERING_HANDOFF.md
- ITERATION_PROGRESS_LOG.md

### GOV-001 — Eliminate reporting and merge deadlocks

Status: DONE
Owner: trading-manager
Priority: P0

Acceptance criteria:

- Normal repository-modifying engineering tasks continue to overwrite ignored root `REPORT.md` and write authoritative timestamped archives under reviewable repository `reports/`.
- Merge readiness, merge execution, audit, review, dependency-gate, preflight-validation, verification-only, documentation-inspection, and repository-nonmodifying tasks never create or modify repository reporting artifacts.
- Read-only tasks write their authoritative archive to `/root/.openclaw/audit-archives/<repository-name>/YYYY-MM-DD_HHMMSS_<task>.md` using the existing detailed archive format.
- Reporting mode is selected automatically before artifacts are created; uncertain classification fails safe to read-only mode and never requires a user-requested exception.
- Any task requiring `git diff --check`, clean `git status`, merge, push, or merge-readiness verification uses read-only reporting so reporting cannot dirty the repository.
- Report format, archive format, acceptance evidence, PASS/FAIL semantics, and the concise terminal summary format remain unchanged; only artifact location selection changes.
- Governance documentation consistently describes implementation reporting, read-only reporting, automatic selection, external archive location, and the clean-tree guarantee.
- No engineering, trading, brokerage, workflow implementation, test, dashboard, secret, deployment, or OpenClaw runtime file changes.
- `git diff --check` passes and the branch contains only approved governance/reporting artifacts before review.

Allowed areas:

- AGENTS.md
- AGENT_OPERATING_PLAN.md
- AGENT_BACKLOG.md
- MENTOR.md
- ITERATION_PROGRESS_LOG.md
- REPORT.md
- reports/

### OPS-011 — Implement the repository-owned idempotent Codex CLI wrapper foundation

Status: DONE
Owner: trading-exec
Priority: P0

Acceptance criteria:

- One repository-owned command wrapper is the sole implementation point for invoking `codex exec`; it accepts a bounded prompt through stdin, runs non-interactively in the specified repository and branch context with `workspace-write` sandboxing, and never uses approval- or sandbox-bypass flags.
- Launch requests use a documented deterministic request ID. The wrapper atomically claims each request before spawning work, and concurrent or repeated matching launches return the same run ID without starting another Codex process.
- Each durable request record includes the request ID, run ID, assigned agent, feature branch, prompt digest, lifecycle status, worker identity, start and update timestamps, timeout deadline, stdout and stderr artifact paths, exit code, completion timestamp, and bounded failure or timeout reason.
- Reuse of a request ID with a different agent, branch, or prompt digest is rejected as an identity conflict and never starts a Codex process.
- Run records and output artifacts are written atomically beneath a repository-local Git-ignored runtime directory; separate wrapper invocations can recover and inspect the same run without observing partial JSON.
- Codex stdout and stderr are captured separately from process start, persisted in bounded artifacts, and represented in returned metadata by paths and bounded diagnostic summaries.
- Every Codex subprocess has a configurable finite timeout with a conservative default. Timeout handling terminates the process group, waits for a bounded grace period, force-kills remaining processes if necessary, persists exit code `124`, and records `TIMED_OUT`.
- Successful completion persists `COMPLETE` and exit code zero. Launch failures and nonzero exits persist `FAILED`, the exact available exit code, and all available bounded stdout/stderr evidence. Terminal runs are never automatically relaunched.
- Status inspection rejects unknown, missing, malformed, or conflicting run records. A stale claimed or active record whose verified worker is no longer running is reconciled deterministically to `FAILED` or `TIMED_OUT` instead of remaining active indefinitely.
- Wrapper tests execute the real repository wrapper as a subprocess while injecting a temporary fake Codex executable that covers success, concurrent duplicate launch, identity conflict, slow execution, process-group timeout, malformed behavior, and nonzero exit.
- Automated tests cannot resolve or invoke a real Codex executable, contact the Codex service, require Codex authentication, or access the network. Test setup explicitly injects the fake executable and fails closed if any real Codex invocation is attempted.
- Focused wrapper tests and the complete test suite pass. Existing live-brokerage safety gates remain unchanged and passing.
- `MENTOR.md`, `TRADING_BOT_AUTONOMOUS_ENGINEERING_HANDOFF.md`, and `ITERATION_PROGRESS_LOG.md` document the wrapper command contract, durable runtime location, recovery behavior, test isolation, verification evidence, and remaining operational risks.
- OPS-011 does not modify or integrate `DELEGATE`, `WAIT_FOR_AGENT`, workflow delegation records, or workflow-state transitions.

Allowed areas:

- engineering/codex_cli_wrapper.py
- tests/test_engineering_codex_cli_wrapper.py
- .gitignore
- AGENT_BACKLOG.md
- MENTOR.md
- TRADING_BOT_AUTONOMOUS_ENGINEERING_HANDOFF.md
- ITERATION_PROGRESS_LOG.md

### OPS-012 — Integrate the Codex wrapper with DELEGATE and WAIT_FOR_AGENT

Status: DONE
Owner: trading-exec
Priority: P0

Depends on: OPS-011

Acceptance criteria:

- The configured engineering launcher invokes only the repository-owned OPS-011 wrapper for launch and status operations; it preserves prompt stdin and applies finite wrapper-command timeouts.
- `DELEGATE` derives the deterministic request ID from the authoritative task ID and feature branch and sends the approved specialist, branch, bounded prompt, and request ID to the wrapper exactly once per workflow attempt.
- `DELEGATE` validates and persists the request ID and returned run metadata before advancing immutably to `WAIT_FOR_AGENT`.
- If the manager stops after the wrapper claims or launches a request but before workflow persistence, retrying `DELEGATE` submits the same identity and recovers the existing wrapper run without launching duplicate work.
- Existing delegation metadata prevents a new launch. Wrapper identity conflicts, malformed metadata, invalid initial states, or mismatched request/run identity stop delegation without advancing the workflow.
- `WAIT_FOR_AGENT` requires persisted delegation metadata, queries only the existing run, and never invokes launch or causes automatic relaunch.
- Wrapper lifecycle states map deterministically to workflow statuses: claimed or queued to `PENDING`, running to `ACTIVE`, zero exit to `COMPLETE`, launch or nonzero-exit failure to `FAILED`, and deadline expiry to `TIMED_OUT`.
- `PENDING` and `ACTIVE` remain in `WAIT_FOR_AGENT` with refreshed persisted metadata. `COMPLETE` advances immutably to `QA`. `FAILED` and `TIMED_OUT` remain stopped in `WAIT_FOR_AGENT` and are not automatically polled or relaunched.
- Workflow delegation records persist and validate request ID, run ID, specialist, status, start/update/completion timestamps, deadline, stdout/stderr artifact paths, exact exit code, timeout state, and bounded terminal reason while remaining backward compatible with existing workflow records.
- Manager, shell, and wrapper-command restarts resume monitoring from the same persisted request ID and run ID.
- Executor tests prove wrapper argument construction, prompt transport, finite command timeouts, complete metadata validation, and rejection of malformed or conflicting responses without invoking real Codex or the network.
- `DELEGATE`, `WAIT_FOR_AGENT`, workflow-store, and dispatcher tests prove idempotent crash recovery, immutable transitions, terminal-state behavior, backward-compatible persistence, and the absence of launch calls from `WAIT_FOR_AGENT`.
- Focused executor, `DELEGATE`, `WAIT_FOR_AGENT`, workflow-store, dispatcher, and OPS-011 wrapper tests pass, followed by the complete test suite. Existing live-brokerage safety gates remain unchanged and passing.
- `MENTOR.md`, `TRADING_BOT_AUTONOMOUS_ENGINEERING_HANDOFF.md`, and `ITERATION_PROGRESS_LOG.md` document the integrated launch/status contract, persisted recovery behavior, verification evidence, and remaining operational risks.

Allowed areas:

- engineering/executor.py
- engineering/models.py
- engineering/workflow/delegate.py
- engineering/workflow/wait_for_agent.py
- engineering/workflow_engine.py
- engineering/workflow_store.py
- tests/test_engineering_executor.py
- tests/test_engineering_delegate.py
- tests/test_engineering_wait_for_agent.py
- tests/test_engineering_workflow_engine.py
- tests/test_engineering_workflow_store.py
- tests/test_engineering_codex_cli_wrapper.py
- AGENT_BACKLOG.md
- MENTOR.md
- TRADING_BOT_AUTONOMOUS_ENGINEERING_HANDOFF.md
- ITERATION_PROGRESS_LOG.md

### OPS-010 — Complete and clear finished workflows

Status: DONE
Owner: trading-manager
Priority: P0

Acceptance criteria:

- COMPLETE requires a persisted, internally consistent accepted report.
- The manager prints the final report before removing active workflow state.
- The completed workflow is cleared exactly once; no new task starts in the same invocation.
- Non-COMPLETE workflows retain the existing save behavior.
- Missing or malformed completion evidence is rejected without clearing state.
- Focused COMPLETE, manager, workflow-store, dispatcher, and full-suite tests pass.

Allowed areas:

- engineering/manager.py
- engineering/workflow/complete.py
- engineering/workflow_engine.py
- engineering/workflow_store.py
- tests/test_engineering_complete.py
- tests/test_engineering_manager.py
- tests/test_engineering_workflow_engine.py
- tests/test_engineering_workflow_store.py
- AGENT_BACKLOG.md
- MENTOR.md
- TRADING_BOT_AUTONOMOUS_ENGINEERING_HANDOFF.md
- ITERATION_PROGRESS_LOG.md

### OPS-009 — Generate deterministic engineering reports

Status: DONE
Owner: trading-manager
Priority: P0

Acceptance criteria:

- REPORT requires complete delegation, successful QA, and an ACCEPT review, and resolves the authoritative backlog task.
- The report includes task, branch, agent, elapsed time, changed files, test command/results, every criterion result, risks, recommendation, and next action.
- Elapsed time and report content are derived deterministically from persisted workflow evidence.
- The report persists backward compatibly, is not regenerated automatically, and advances immutably to COMPLETE.
- Incomplete, inconsistent, or malformed report evidence is rejected.
- Focused reporter, REPORT-handler, workflow-store, dispatcher, and full-suite tests pass.

Allowed areas:

- engineering/reporter.py
- engineering/workflow/report.py
- engineering/workflow_engine.py
- engineering/workflow_store.py
- tests/test_engineering_reporter.py
- tests/test_engineering_report.py
- tests/test_engineering_workflow_engine.py
- tests/test_engineering_workflow_store.py
- AGENT_BACKLOG.md
- MENTOR.md
- TRADING_BOT_AUTONOMOUS_ENGINEERING_HANDOFF.md
- ITERATION_PROGRESS_LOG.md

### OPS-008 — Review acceptance criteria deterministically

Status: DONE
Owner: trading-manager
Priority: P0

Acceptance criteria:

- REVIEW requires successful persisted QA evidence and resolves the stored task from the authoritative backlog.
- Review evidence covers every acceptance criterion exactly once with a proof method, exact result, and PASS or FAIL status.
- Missing, duplicate, unknown, blank, malformed, or oversized review evidence is rejected.
- The recommendation is derived deterministically: all PASS produces ACCEPT and advances to REPORT; any FAIL produces REWORK and remains stopped in REVIEW.
- Criterion-level review evidence and recommendation persist backward compatibly through the workflow store and are never regenerated automatically.
- Focused reviewer, REVIEW-handler, workflow-store, dispatcher, and full-suite tests pass.

Allowed areas:

- engineering/models.py
- engineering/reviewer.py
- engineering/workflow/review.py
- engineering/workflow_engine.py
- engineering/workflow_store.py
- tests/test_engineering_reviewer.py
- tests/test_engineering_review.py
- tests/test_engineering_workflow_engine.py
- tests/test_engineering_workflow_store.py
- AGENT_BACKLOG.md
- MENTOR.md
- TRADING_BOT_AUTONOMOUS_ENGINEERING_HANDOFF.md
- ITERATION_PROGRESS_LOG.md

### OPS-007 — Persist deterministic QA evidence

Status: DONE
Owner: trading-manager
Priority: P0

Acceptance criteria:

- QA requires a completed delegated run and refuses missing or incomplete delegation metadata.
- QA runs only an explicitly configured, bounded pytest command with test-safe environment flags.
- QA persists the command, exit code, runtime, bounded output summary, changed files, completion time, and timeout status.
- Successful QA advances immutably to REVIEW; failed or timed-out QA remains stopped in QA and is not rerun automatically.
- QA evidence remains backward compatible through workflow-store save and load.
- Focused QA-runner, QA-handler, workflow-store, dispatcher, and full-suite tests pass.

Allowed areas:

- engineering/models.py
- engineering/qa_runner.py
- engineering/workflow/qa.py
- engineering/workflow_engine.py
- engineering/workflow_store.py
- tests/test_engineering_qa_runner.py
- tests/test_engineering_qa.py
- tests/test_engineering_workflow_engine.py
- tests/test_engineering_workflow_store.py
- AGENT_BACKLOG.md
- MENTOR.md
- TRADING_BOT_AUTONOMOUS_ENGINEERING_HANDOFF.md
- ITERATION_PROGRESS_LOG.md

### OPS-006 — Wait for delegated agent runs

Status: DONE
Owner: trading-manager
Priority: P0

Acceptance criteria:

- Delegation uses a deterministic request ID that the configured wrapper can treat idempotently across launch retries.
- WAIT_FOR_AGENT requires persisted delegation metadata and never launches another agent.
- PENDING and ACTIVE runs remain in WAIT_FOR_AGENT with refreshed persisted status metadata.
- COMPLETE runs advance immutably to QA; FAILED and TIMED_OUT runs remain stopped with explicit persisted terminal status.
- The configured wrapper status contract is bounded and rejects malformed results.
- Focused delegation, monitoring, workflow-store, dispatcher, and full-suite tests pass.

Allowed areas:

- engineering/executor.py
- engineering/models.py
- engineering/workflow/delegate.py
- engineering/workflow/wait_for_agent.py
- engineering/workflow_engine.py
- engineering/workflow_store.py
- tests/test_engineering_executor.py
- tests/test_engineering_delegate.py
- tests/test_engineering_wait_for_agent.py
- tests/test_engineering_workflow_engine.py
- tests/test_engineering_workflow_store.py
- AGENT_BACKLOG.md
- MENTOR.md
- TRADING_BOT_AUTONOMOUS_ENGINEERING_HANDOFF.md
- ITERATION_PROGRESS_LOG.md

### OPS-005 — Delegate bounded agent runs

Status: DONE
Owner: trading-manager
Priority: P0

Acceptance criteria:

- DELEGATE builds a deterministic prompt containing task scope, acceptance criteria, allowed areas, safety constraints, branch, required tests, and reporting requirements.
- DELEGATE selects only the approved specialist named by the backlog task owner.
- DELEGATE launches exactly one configured agent run and rejects duplicate delegation metadata.
- The workflow store persists run ID, specialist, start time, and status while remaining backward compatible with existing workflow records.
- DELEGATE advances immutably to WAIT_FOR_AGENT only after a successful launch.
- Focused executor, DELEGATE-handler, workflow-store, dispatcher, and full-suite tests pass.

Allowed areas:

- engineering/executor.py
- engineering/models.py
- engineering/workflow/delegate.py
- engineering/workflow_engine.py
- engineering/workflow_store.py
- tests/test_engineering_executor.py
- tests/test_engineering_delegate.py
- tests/test_engineering_workflow_engine.py
- tests/test_engineering_workflow_store.py
- AGENT_BACKLOG.md
- MENTOR.md
- TRADING_BOT_AUTONOMOUS_ENGINEERING_HANDOFF.md
- ITERATION_PROGRESS_LOG.md

### OPS-004 — Prepare workflow feature branches

Status: DONE
Owner: trading-manager
Priority: P0

Acceptance criteria:

- PREPARE_BRANCH verifies the repository and expected base branch.
- PREPARE_BRANCH refuses dirty repositories and unrelated current branches.
- PREPARE_BRANCH safely creates or resumes the stored feature branch.
- PREPARE_BRANCH advances immutably to DELEGATE only after successful preparation.
- Focused Git-service, PREPARE_BRANCH-handler, dispatcher, and full-suite tests pass.

Allowed areas:

- engineering/git_service.py
- engineering/workflow/prepare_branch.py
- engineering/workflow_engine.py
- tests/test_engineering_git_service.py
- tests/test_engineering_prepare_branch.py
- tests/test_engineering_workflow_engine.py
- AGENT_BACKLOG.md
- MENTOR.md
- TRADING_BOT_AUTONOMOUS_ENGINEERING_HANDOFF.md
- ITERATION_PROGRESS_LOG.md

### OPS-003 — Implement deterministic PLAN workflow

Status: DONE
Owner: trading-manager
Priority: P0

Acceptance criteria:

- Execution plans include acceptance criteria, allowed areas, deterministic risk, and deterministic complexity.
- PLAN resolves the stored task from the authoritative backlog.
- PLAN rejects a missing task or inconsistent feature branch.
- PLAN presents the concrete execution plan and transitions to PREPARE_BRANCH.
- Focused planner, PLAN-handler, dispatcher, and full-suite tests pass.

Allowed areas:

- engineering/models.py
- engineering/planner.py
- engineering/workflow/plan.py
- engineering/workflow_engine.py
- tests/test_engineering_planner.py
- tests/test_engineering_plan.py
- tests/test_engineering_workflow_engine.py
- AGENT_BACKLOG.md
- MENTOR.md
- TRADING_BOT_AUTONOMOUS_ENGINEERING_HANDOFF.md
- ITERATION_PROGRESS_LOG.md

### OPS-002 — Portable trading bot log path

Status: DONE
Owner: trading-exec
Priority: P0

Acceptance criteria:

- Trading bot logging does not depend on a host-specific absolute path.
- Production logging defaults to `trading_bot.log` in the repository root.
- `TRADING_BOT_LOG_PATH` overrides the default destination.
- Automated tests do not write runtime logs into the repository.
- The focused regression tests and full test suite pass.

Allowed areas:

- src/core/smart_bot.py
- tests/
- AGENT_BACKLOG.md
- MENTOR.md

### OPS-001 — Manager timebox and stale-task reporting

Status: DONE
Owner: trading-manager
Priority: P0

Acceptance criteria:

- Every manager task report includes task start time, task end time, and elapsed time.
- Every manager task report states whether the task ran continuously or was resumed later.
- The manager reports blocked or stale status when a task cannot complete in reasonable time.
- Before continuing a resumed task, the manager explains why it was paused and what changed.

Allowed areas:

- AGENTS.md
- AGENT_OPERATING_PLAN.md
- AGENT_BACKLOG.md

## Phase EP — Engineering Platform Roadmap

### ENGPLAT-001 — Project Registration and Managed-Project Configuration

Status: TODO
Owner: trading-manager
Priority: P1

Purpose:

Create a project-local configuration contract allowing the engineering platform
to manage a repository without hard-coded trading-bot paths, commands, names,
or ownership assumptions.

Execution gate:

- Non-executable until Josh approves this narrow implementation plan with allowed
  areas.
- This roadmap entry does not authorize runtime migration, repository
  extraction, deployment changes, secrets changes, or live trading changes.

---

## Governance Remediation (this section)

This block is the approved narrow implementation plan for ENGPLAT-001. It defines
allowed areas, implementation boundaries, the project registration contract,
acceptance criteria, test strategy, and risks. Josh approved this remediation
on 2026-08-05.

---

### Allowed Areas

The following files are approved for modification by ENGPLAT-001 implementation:

1. `engineering/models.py` — add typed `ProjectConfig` dataclass and `validate_project_config()` validation function
2. `tests/test_engineering_project_config.py` — add focused tests for project configuration model and validation
3. `AGENT_BACKLOG.md` — update ENGPLAT-001 entry to reference the completed remediation (no structural change)
4. `MENTOR.md` — add architecture note for project configuration contract (no structural change)
5. `TRADING_BOT_AUTONOMOUS_ENGINEERING_HANDOFF.md` — add architecture note for project configuration contract (no structural change)
6. `ITERATION_PROGRESS_LOG.md` — append continuity entry when implementation completes

**Rationale for each allowed file:**

- `engineering/models.py` — The typed model definition belongs alongside existing workflow models (`BacklogTask`, `StoredWorkflow`, enums). Adding it here keeps related types in one file and avoids creating a new module until the contract is proven.
- `tests/test_engineering_project_config.py` — New test file for project configuration model. Tests belong next to the code they cover. No directory-wide permissions.
- `AGENT_BACKLOG.md` — Governance document; requires update to record the completed remediation. No runtime code.
- `MENTOR.md` — Code-map document; requires architecture note documenting the project configuration contract. No runtime code.
- `TRADING_BOT_AUTONOMOUS_ENGINEERING_HANDOFF.md` — Handoff document; requires architecture note documenting the project configuration contract. No runtime code.
- `ITERATION_PROGRESS_LOG.md` — Progress log; requires continuity entry per governance rules. No runtime code.

**No other files are authorized.** Directory-wide permissions (e.g., `engineering/`, `src/`, `dashboard_api/`) are not granted. No files outside this list may change during ENGPLAT-001 implementation.

---

### Implementation Boundaries

#### What ENGPLAT-001 WILL implement

1. **Typed project configuration model** — A frozen dataclass `ProjectConfig` in `engineering/models.py` defining all required configuration fields. The model is immutable and suitable for use as a shared configuration contract.

2. **Typed project registry model** — A frozen dataclass `ProjectRegistry` in `engineering/models.py` holding a mapping of registered projects. Supports at least one project (trading-bot).

3. **Configuration validation** — A `validate_project_config()` function in `engineering/models.py` that returns a deterministic list of validation errors and fails closed on any ambiguous or missing input.

4. **Trading-bot configuration instance** — A frozen `TRADING_BOT_PROJECT` constant in `engineering/models.py` demonstrating that the trading-bot can be represented using the contract, without modifying any runtime behavior.

5. **Tests** — Focused tests in `tests/test_engineering_project_config.py` proving the contract behaves correctly.

6. **Governance documentation updates** — Architecture notes in `MENTOR.md` and `TRADING_BOT_AUTONOMOUS_ENGINEERING_HANDOFF.md` documenting the project configuration contract.

#### What ENGPLAT-001 WILL NOT implement

The following are explicitly out of scope and must not be attempted during ENGPLAT-001:

- Repository adapters (belongs to ENGPLAT-002)
- Dashboard implementation or changes (belongs to ENGDASH-005/006)
- Engineering control panel (belongs to ENGCTRL-001)
- Workflow engine modifications
- GitHub API integration
- Runtime adapter implementations
- CONFIG-002 dashboard-to-engine synchronization
- Trading behavior changes
- Secret or credential modifications
- Deployment or infrastructure changes
- Project extraction (deferred, Phase 6)
- Persistence of project configuration to disk or database
- Migration of existing hard-coded paths
- Any change to files not listed in Allowed Areas above

---

### Project Registration Contract (Architectural Definition)

This section defines the architectural contract — the information every managed
project must eventually provide — without specifying a serialization format.
ENGPLAT-001 implements the typed model; future tasks implement the registry
and adapters.

Every managed project must expose the following information through a typed
`ProjectConfig`:

| Contract Field | Type | Description |
|---|---|---|
| `project_id` | `str` | Unique project identifier (slug format) |
| `display_name` | `str` | Human-readable project name |
| `repository_root` | `str \| Path` | Absolute path to the managed repository |
| `authoritative_base_branch` | `str` | Base branch for all feature work (e.g., `main`) |
| `governance_files.backlog_path` | `str \| Path` | Path to the authoritative backlog |
| `governance_files.operating_plan_path` | `str \| Path` | Path to the agent operating plan |
| `governance_files.owners_path` | `str \| Path` | Path to the owners file |
| `governance_files.handoff_path` | `str \| Path` | Path to the engineering handoff document |
| `workflow_files.workflow_store_path` | `str \| Path` | Path to the workflow persistence file |
| `workflow_files.event_store_path` | `str \| Path` | Path to the engineering event store |
| `workflow_files.report_dir` | `str \| Path` | Directory for engineering reports |
| `qa_commands` | `tuple[str, ...]` | Pre-configured safe pytest commands (exact strings) |
| `qa_timeout_seconds` | `int` | Maximum allowed QA runtime in seconds |
| `prohibited_operations` | `tuple[str, ...]` | Operations banned for this project (e.g., `no_live_trading`) |
| `agents_may_merge` | `bool` | Whether agents may merge (always `False` initially) |
| `owner_ids` | `tuple[str, ...]` | Human owner identifiers |
| `agent_owners` | `tuple[str, ...]` | Authorized agent identities for this project |

**Validation rules (fail-closed):**
- Missing required fields → error returned
- Invalid field types → error returned
- Repository root does not exist → error returned
- Empty project ID or display name → error returned
- Conflicting settings (e.g., `agents_may_merge=True` with no approval policy) → error returned
- Unknown/missing governance files → warning (governance files may not exist in early projects)

**Format TBD**: The serialization format (JSON, YAML, TOML, Python module) is not
defined by ENGPLAT-001. The typed model is the canonical contract.

---

### Acceptance Criteria

The following criteria must all pass for ENGPLAT-001 implementation to be
considered complete:

| # | Criterion | Proof Method |
|---|---|---|
| 1 | `ProjectConfig` dataclass exists in `engineering/models.py` with all required fields | Import check: `from engineering.models import ProjectConfig` |
| 2 | `ProjectConfig` is a frozen dataclass | `dataclasses.is_dataclass(config)` and `dataclasses.fields(config)` |
| 3 | `validate_project_config()` exists in `engineering/models.py` | Import check: `from engineering.models import validate_project_config` |
| 4 | `TRADING_BOT_PROJECT` constant exists and passes validation | `validate_project_config(TRADING_BOT_PROJECT)` returns empty error list |
| 5 | Missing required field returns error | Test: omit `project_id`; assert error returned |
| 6 | Invalid repository root returns error | Test: invalid root path; assert error returned |
| 7 | Conflicting `agents_may_merge=True` with no approval policy returns error | Test: explicit conflict; assert error returned |
| 8 | Valid configuration returns empty error list | Test: `TRADING_BOT_PROJECT`; assert no errors |
| 9 | `ProjectRegistry` dataclass exists in `engineering/models.py` | Import check: `from engineering.models import ProjectRegistry` |
| 10 | Registry roundtrip (dict → registry → dict) is lossless | JSON serialize/deserialize cycle; assert identity |
| 11 | `git diff --check` passes | `git diff --check` exits 0 |
| 12 | Full safe test suite passes | `TESTING=1 UNIT_TESTING=1 pytest -q` → 375+ passed |
| 13 | No runtime behavior changes | Trading bot, dashboard, workflow engine unchanged (spot-check imports) |
| 14 | Architecture notes added to `MENTOR.md` and `TRADING_BOT_AUTONOMOUS_ENGINEERING_HANDOFF.md` | Read sections; confirm contract documented |

---

### Test Strategy

ENGPLAT-001 implementation produces one new test file:

`tests/test_engineering_project_config.py`

Tests required (minimum):

1. **Model instantiation** — `ProjectConfig` can be constructed with valid trading-bot values
2. **Model is frozen** — attempting to mutate a field raises `dataclasses.FrozenInstanceError`
3. **Validation: valid config** — `validate_project_config(TRADING_BOT_PROJECT)` returns empty list
4. **Validation: missing required field** — omit `project_id`; assert non-empty error list returned
5. **Validation: missing `display_name`** — assert non-empty error list returned
6. **Validation: invalid `repository_root`** — path does not exist; assert non-empty error list returned
7. **Validation: empty `project_id`** — blank string; assert non-empty error list returned
8. **Validation: invalid type** — wrong type for integer field; assert non-empty error list returned
9. **Validation: `agents_may_merge=True` without approval policy** — conflict detected; assert error returned
10. **Registry: single project** — `ProjectRegistry` with one trading-bot entry; roundtrip preserves identity
11. **Registry: multiple projects** — `ProjectRegistry` with two entries; no conflict on distinct IDs
12. **Registry: duplicate project_id** — second entry with same ID; assert error returned

No tests for: adapters, dashboard, workflow transitions, brokerage, trading, or persistence.

---

### Implementation Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Contract is incomplete for ENGPLAT-002 needs | Medium | ENGPLAT-001 only defines the model; ENGPLAT-002 adapter work will reveal gaps; gaps are addressed in ENGPLAT-002 without reworking the model contract |
| Trading-bot `TRADING_BOT_PROJECT` instance has wrong values | Medium | Values are verified against actual repository paths; spot-check during implementation |
| Forward-compatibility: future fields not allowed | Low | Model uses `**extra: dict` or equivalent pattern to allow unknown fields without silently ignoring them |
| Backward compatibility: existing workflow JSON breaks | Low | ENGPLAT-001 adds new types; does not modify `StoredWorkflow`, `BacklogTask`, or existing workflow types |
| Validation is non-deterministic | Low | Validation function is pure; no I/O, no time dependencies; same input always returns same output |
| Contract is too rigid for future projects | Medium | Contract fields are intentionally general (string paths, tuple-of-strings for lists); specific formats are chosen at adapter time |

---

### Execution Gate (Repeat)

ENGPLAT-001 remains non-executable until:

1. This governance remediation plan is approved by Josh.
2. A feature branch is created for the implementation.
3. The implementation plan includes exactly the files listed in Allowed Areas above.
4. Josh explicitly approves the implementation plan.

No implementation may begin before these conditions are met.

### ENGPLAT-002 — Repository and Project Adapter Boundaries

Status: TODO
Owner: trading-manager
Priority: P1

Depends on: ENGPLAT-001

Purpose:

Move project-specific filesystem, Git, governance, report, and workflow access
behind narrow interfaces suitable for future extraction into a reusable
engineering-platform repository.

Execution gate:

- Non-executable until `ENGPLAT-001` is accepted and Josh approves a narrow
  implementation plan with allowed areas for this task.
- No broad rewrite or repository extraction is authorized by this roadmap entry.

Acceptance criteria:

- Engineering services receive project/repository dependencies through typed
  interfaces or adapters.
- Hard-coded “trading-bot” text and paths are removed from reusable components.
- Governance documents remain project-local.
- Existing trading-bot engineering behavior remains compatible.
- No broad rewrite or repository extraction occurs yet.
- Tests prove adapters cannot mutate outside their configured project root.

### ENGDASH-005 — Engineering Timeline and Historical Activity

Status: TODO
Owner: dashboard-agent
Priority: P1

Depends on: ENGDASH-004

Purpose:

Add a bounded historical timeline showing engineering tasks, workflow stages,
agent delegations, test runs, reviews, approvals, commits, PR activity,
failures, and merges.

Execution gate:

- Non-executable until Josh approves a narrow implementation plan with allowed
  areas for this task.
- Should consume the `ENGPLAT-001` project boundary where practical.
- No controls or mutation endpoints are authorized by this roadmap entry.

Acceptance criteria:

- Timeline data is read-only.
- Results are bounded, ordered, and timestamped.
- Event-source failures degrade to health warnings.
- Dynamic text is escaped and sanitized.
- No unbounded Git, report, event, or filesystem scans.
- The timeline supports project configuration rather than hard-coded paths.
- No controls or mutation endpoints are added.

### ENGDASH-006 — Live Agent Activity and Execution Visibility

Status: TODO
Owner: dashboard-agent
Priority: P1

Depends on: ENGDASH-004

Purpose:

Expose the current and most recent bounded agent execution state without
revealing private reasoning, secrets, raw credentials, or unbounded output.

Execution gate:

- Non-executable until Josh approves a narrow implementation plan with allowed
  areas for this task.
- Must avoid creating a competing workflow-state model.
- No arbitrary process inspection or shell access is authorized by this roadmap
  entry.

Acceptance criteria:

- Show agent identity/role, task, workflow stage, start time, elapsed time,
  latest safe status, blocker, timeout state, and last completed action.
- Do not expose private chain-of-thought or hidden reasoning.
- Agent output excerpts are sanitized and bounded.
- Missing or stale activity is clearly labeled.
- No arbitrary process inspection or shell access is exposed through the UI.
- The dashboard remains usable when no agent is active.

### ENGCTRL-001 — Safe Engineering Control Panel

Status: TODO
Owner: trading-manager
Priority: P1

Depends on: ENGDASH-005, ENGDASH-006

Purpose:

Add narrowly authorized, audited workflow controls after the read-only
dashboard and adapter boundaries are stable.

Execution gate:

- Non-executable until stable dashboard/query boundaries exist after
  `ENGDASH-005` and `ENGDASH-006`, and Josh approves a separate read-only design
  review plus a narrow implementation plan with allowed areas.
- No merge button, safety bypass, live-trading control, arbitrary shell, Git,
  file-edit, deployment, secret, brokerage, or trading action is authorized.

Acceptance criteria:

- Only pause may be considered as a candidate control.
- Only resume may be considered as a candidate control.
- Only cancel an active bounded workflow may be considered as a candidate
  control.
- Only retry an explicitly retryable failed step may be considered as a
  candidate control.
- Only start the next already-approved workflow step may be considered as a
  candidate control.
- Only record a human approval or rejection may be considered as a candidate
  control.
- Every action is authenticated, authorized, validated, and audited.
- Controls call EngineeringControlService or equivalent bounded services.
- No arbitrary shell, Git, file-edit, deployment, secret, brokerage, or trading
  action is exposed.
- No merge button.
- No safety bypass.
- No live-trading control.
- Control implementation requires a separate Josh approval after read-only
  design review.

### ENGPLAT-003 — Extract Reusable Engineering Platform Repository

Status: TODO
Owner: trading-manager
Priority: P3

Depends on: ENGPLAT-001, ENGPLAT-002

Purpose:

Future placeholder for extracting reusable engineering-platform code into a
separate repository after configuration and adapter boundaries have been proven
through normal use.

Execution gate:

- Explicitly deferred. Non-executable.
- Extraction must not start until `ENGPLAT-001` and `ENGPLAT-002` are stable
  through normal project use and Josh separately approves cross-repository
  planning.
- Project-local governance remains in each managed repository.
- Cross-repository versioning, deployment, authentication, and migration require
  separate planning and human approval.

Acceptance criteria:

- Extraction occurs only after configuration and adapter boundaries are stable.
- Project-local governance remains in each managed repository.
- Cross-repository versioning requires separate planning and human approval.
- Cross-repository deployment requires separate planning and human approval.
- Cross-repository authentication requires separate planning and human approval.
- Cross-repository migration requires separate planning and human approval.

---

## Phase A — Trustworthy Tests

### TEST-001 — Prevent live brokerage calls from tests

Status: DONE
Owner: trading-exec
Priority: P0

Acceptance criteria:

- Automated tests cannot contact a live brokerage endpoint.
- Paper or mocked brokerage clients are used.
- A test fails if live mode is enabled.

Allowed areas:

- tests/
- test configuration
- brokerage client abstractions

### TEST-002 — Indicator calculation tests

Status: DONE
Owner: trading-exec
Priority: P0

Acceptance criteria:

- Known inputs produce expected RSI, SMA, MACD, Bollinger, and volume values.
- Bullish, neutral, and bearish examples are covered.

Approved volume semantics:

- Raw input volume is preserved unchanged.
- `volume_sma_20` is the arithmetic mean of the current row and preceding 19
  rows; the first 19 rows remain NaN.
- `volume_ratio` is `volume / volume_sma_20`; a zero, missing, or non-finite
  denominator leaves the ratio NaN rather than normalizing it.
- Scope is limited to deterministic `calculate_indicators` tests. Persistence,
  database, API, dashboard, liquidity, catalyst, ranking, signal confirmation,
  and production behavior changes are excluded.

### TEST-003 — Decision-path tests

Status: DONE
Owner: trading-exec
Priority: P0

Acceptance criteria:

- BUY, SELL, and HOLD paths are tested separately.
- Owned and unowned position behavior is tested.
- Duplicate-order prevention is tested.

### TEST-004 — Settings loading tests

Status: DONE
Owner: trading-exec
Priority: P0

Acceptance criteria:

- Defaults load correctly.
- Database/dashboard overrides load correctly.
- Invalid settings are rejected.

---

## Phase B — Configuration

### CONFIG-001 — Authoritative strategy configuration

Status: DONE
Owner: trading-exec
Priority: P1

Acceptance criteria:

- One schema defines effective strategy settings.
- The bot, tests, logs, and dashboard use the same schema.
- Effective values are logged at startup.

Completion evidence (2026-08-04):

- `src/core/settings_service.py` now owns `STRATEGY_SETTINGS_SCHEMA`, typed
  validation, effective loading, dashboard metadata derivation, and bounded
  startup-log formatting.
- `src/core/smart_bot.py` loads schema-defined effective settings during
  initialization and logs the non-secret effective values.
- `dashboard.py` derives displayed settings and persisted updates from the
  shared schema.
- Validation passed after initial implementation: `git diff --check`; `TESTING=1 UNIT_TESTING=1 .venv/bin/python -m pytest tests/test_settings_service.py -q`
  (`22 passed, 1 warning`); `TESTING=1 UNIT_TESTING=1 .venv/bin/python -m pytest tests/test_smart_bot_decision_paths.py -q`
  (`7 passed, 2 warnings`); `TESTING=1 UNIT_TESTING=1 .venv/bin/python -m pytest -q`
  (`327 passed, 82 warnings`).
- Review-fix validation passed after addressing PR #9 blocking findings:
  `git diff --check`; `TESTING=1 UNIT_TESTING=1 .venv/bin/python -m pytest tests/test_settings_service.py -q`
  (`32 passed, 2 warnings`); `TESTING=1 UNIT_TESTING=1 .venv/bin/python -m pytest tests/test_settings_service.py -k dashboard -q`
  (`10 passed, 22 deselected, 2 warnings`); `TESTING=1 UNIT_TESTING=1 .venv/bin/python -m pytest tests/test_smart_bot_decision_paths.py -q`
  (`7 passed, 2 warnings`); `TESTING=1 UNIT_TESTING=1 .venv/bin/python -m pytest -q`
  (`337 passed, 82 warnings`).
- Final PR #9 review-fix validation passed after sanitizing invalid legacy
  warnings and tightening loop-delay input behavior: `git diff --check`;
  `TESTING=1 UNIT_TESTING=1 .venv/bin/python -m pytest tests/test_settings_service.py -q`
  (`38 passed, 2 warnings`); `TESTING=1 UNIT_TESTING=1 .venv/bin/python -m pytest tests/test_settings_service.py -k dashboard -q`
  (`10 passed, 28 deselected, 2 warnings`); `TESTING=1 UNIT_TESTING=1 .venv/bin/python -m pytest tests/test_smart_bot_decision_paths.py -q`
  (`7 passed, 2 warnings`); `TESTING=1 UNIT_TESTING=1 .venv/bin/python -m pytest -q`
  (`343 passed, 80 warnings`).

Allowed areas:

- `src/core/settings_service.py` — define and validate the authoritative
  strategy settings schema, typed effective-value loading, defaults, and
  dashboard metadata at the shared settings boundary.
- `src/core/smart_bot.py` — consume the shared effective strategy settings and
  emit bounded startup logging of non-secret effective values.
- `dashboard.py` — replace duplicated dashboard strategy-setting metadata with
  the shared schema so displayed and saved settings map to engine settings.
- `tests/test_settings_service.py` — prove schema defaults, typed overrides,
  validation, dashboard metadata, and effective-value behavior.
- `tests/test_smart_bot_decision_paths.py` — adjust or extend focused bot-path
  coverage only if consuming the shared schema changes initialization or
  decision-threshold expectations.
- `AGENT_BACKLOG.md` — record the explicit CONFIG-001 allowed areas and status
  evidence for this task.
- `MENTOR.md` — document the authoritative strategy settings contract if
  CONFIG-001 implementation changes settings architecture.
- `TRADING_BOT_AUTONOMOUS_ENGINEERING_HANDOFF.md` — update handoff notes if
  CONFIG-001 changes the engineering roadmap or settings architecture.
- `ITERATION_PROGRESS_LOG.md` — record bounded iteration continuity and final
  evidence as required by governance.
- `REPORT.md` and `reports/` — write the required implementation report and
  timestamped archive when CONFIG-001 implementation completes or stops.

### CONFIG-002 — Dashboard-to-engine synchronization

Status: TODO
Owner: dashboard-agent
Priority: P1

Queued behind: ENGPLAT-001, ENGPLAT-002, ENGDASH-005, ENGDASH-006, ENGCTRL-001

Backlog treatment:

- Preserve the existing task definition and priority metadata.
- Remain queued behind the engineering-platform work unless Josh later changes
  the priority.
- Do not perform the prior proposed governance remediation yet.
- Do not implement this task as part of engineering-platform roadmap work.

Acceptance criteria:

- Every displayed strategy setting maps to a real engine setting.
- Saving a setting changes the next approved paper session.
- Invalid settings are rejected.

---

## Phase C — Scoring

### SCORE-001 — Normalize indicator scores

Status: TODO
Owner: trading-exec
Priority: P2

Acceptance criteria:

- Each indicator has a documented bounded range.
- Combined score remains within 0–100.
- MACD cannot dominate through incompatible scale.
- Bullish, neutral, and bearish tests pass.

### SCORE-002 — Separate eligibility from ranking

Status: TODO
Owner: trading-exec
Priority: P2

Acceptance criteria:

- Core strategy gates determine eligibility.
- Score ranks otherwise eligible candidates.
- Score does not silently duplicate strategy gates.

---

## Phase D — Execution Paths

### EXEC-001 — Repair daily-only analysis

Status: TODO
Owner: trading-exec
Priority: P2

### EXEC-002 — Verify multi-timeframe analysis

Status: TODO
Owner: trading-exec
Priority: P2

### EXEC-003 — Verify BUY and SELL order paths

Status: TODO
Owner: trading-exec
Priority: P2

### EXEC-004 — Verify restart and recovery behavior

Status: TODO
Owner: trading-exec
Priority: P2

---

## Phase E — Stock Universe

### UNIVERSE-001 — Common-stock filtering

Status: TODO
Owner: trading-exec
Priority: P3

Acceptance criteria:

- ETFs, funds, warrants, units, rights, preferred shares, OTC assets, inactive assets, and nontradable assets are excluded.
- Representative classification tests exist.

### UNIVERSE-002 — Liquidity and history filters

Status: TODO
Owner: trading-exec
Priority: P3

---

## Phase ED — Engineering Dashboard

### ENGDASH-001 — Engineering dashboard read model

Status: DONE
Owner: dashboard-agent
Priority: P1

Acceptance criteria:

- A reusable, read-only dashboard snapshot model represents project identity,
  repository state, backlog summary, workflow state, blockers, approvals,
  latest execution/test results, latest commit, optional PR metadata, recent
  events, recent reports, health warnings, and freshness timestamp.
- The read model does not import trading, brokerage, Alpaca, trading database,
  or legacy trading dashboard modules.
- The read model is dependency-injected, bounded, deterministic, and converts
  unavailable sources into health warnings.
- Focused read-model tests and relevant engineering/dashboard regressions pass.

Completed evidence:

- Branch `agent/engdash-001-dashboard-read-model` merged via PR #10.
- Merge commit on `main`: `d88331d`.

### ENGDASH-002 — Read-only engineering dashboard API/UI

Status: DONE
Owner: dashboard-agent
Priority: P1

Depends on: ENGDASH-001

Approved decisions:

- Start from latest remote `main` after PR #10 merge.
- Use branch `agent/engdash-002-read-only-api-ui`.
- Deliberately migrate `dashboard-api/` to importable package
  `dashboard_api/`; do not retain both paths.
- Build a separate read-only engineering dashboard app/router with an
  independently runnable entry point.
- Do not mount into or modify legacy trading `dashboard.py`.
- Use ENGDASH-001 `DashboardSnapshot` as the sole engineering data source.
- Use the injected read-only PR summary interface; do not add a live GitHub
  adapter in this task.

Allowed areas:

- `AGENT_BACKLOG.md`
- `dashboard_api/**`
- `tests/**`
- `docs/**`
- `engineering/**` only if a small shared read-only boundary is genuinely
  required

Explicit exclusions:

- `dashboard.py`
- trading strategy logic
- brokerage integrations
- live trading code
- `.env` files, credentials, secrets, repository settings, CI/CD secrets, and
  branch protection

Acceptance criteria:

- One importable `dashboard_api` package exists and old `dashboard-api/` no
  longer exists.
- A separate engineering dashboard app can start without importing
  `dashboard.py`.
- `GET /api/engineering/snapshot` returns a stable bounded read-only JSON
  payload from `DashboardSnapshot.to_dict()`.
- `GET /engineering` renders the same snapshot data in a separate engineering
  dashboard page.
- The page renders health, repository state, backlog summary, current
  workflows/tasks, blockers, approvals, recent reports, recent events/timeline,
  PR metadata when supplied, and degradation warnings.
- Missing sources produce bounded warnings rather than server failures.
- No mutation routes, controls, approval actions, retries, pause/resume,
  execution, merge, or write APIs exist.
- No trading, brokerage, Alpaca, trading database, or legacy `dashboard.py`
  imports are introduced.
- Rendered values are escaped/sanitized and do not expose secrets, environment
  variables, raw exception traces, unbounded filesystem contents, or sensitive
  data beyond approved report metadata.
- Focused API/UI tests, ENGDASH-001 read-model tests, relevant engineering and
  legacy dashboard regression tests, and the full safe suite pass.

Required tests:

- `dashboard_api` package imports normally.
- Old `dashboard-api/` path no longer exists.
- Stable JSON response shape and typed serialization.
- Healthy and degraded snapshot responses.
- Missing PR metadata and reader/source failures.
- Bounded backlog, report, and event lists with deterministic ordering.
- HTML rendering with empty and populated snapshots.
- Warnings render safely without raw traces/secrets.
- No mutation HTTP methods/routes.
- No trading or brokerage imports.
- No import, filename, or route collision with `dashboard.py`.
- Independent app creation/startup.
- Existing ENGDASH-001 read-model behavior.
- Relevant engineering regression tests and full safe suite.

Completed evidence:

- Branch: `agent/engdash-002-read-only-api-ui`.
- Focused API/read-model tests: `23 passed, 2 warnings`.
- Relevant focused/regression tests after final route-surface fix: `33 passed, 33 deselected, 2 warnings`.
- Full safe suite: `366 passed, 82 warnings`.

### ENGDASH-003 — EngineeringQueryService-backed dashboard provider

Status: DONE
Owner: dashboard-agent
Priority: P1

Depends on: ENGDASH-002

Approved scope:

- Start from latest merged `main` after PR #11 merge.
- Use branch `agent/engdash-003-query-service-provider`.
- Wire the separate read-only engineering dashboard app to a real
  `EngineeringQueryService`-backed provider.
- Preserve the exact approved HTTP surface: `GET /api/engineering/snapshot`
  and `GET /engineering` only.
- Keep FastAPI automatic `/openapi.json`, `/docs`, and `/redoc` routes
  disabled.
- Keep PR metadata injected-only; do not add a live GitHub adapter.

Allowed areas:

- `AGENT_BACKLOG.md`
- `dashboard_api/**`
- `engineering/**`
- `tests/**`
- `docs/**`
- `reports/**` only for task reports

Explicit exclusions:

- `dashboard.py`
- trading strategy code
- brokerage integrations
- live trading execution
- `.env` files, credentials, secrets, repository settings, CI/CD secrets, and
  branch protection

Acceptance criteria:

- The default engineering dashboard app uses a real
  `EngineeringQueryService`-backed provider.
- The default app no longer shows degraded mode solely because no provider was
  wired.
- Missing optional sources still degrade safely to bounded warnings.
- The API and UI consume the same typed snapshot.
- The exact two-route HTTP surface is preserved.
- No controls or write operations exist.
- No trading, brokerage, Alpaca, or trading database imports exist.
- Focused provider/API/read-model tests, relevant engineering regressions, and
  the full safe suite pass.
- Repository is clean at completion.
- PR is open against `main` and ready for Josh's review.

Required tests:

- Real `EngineeringQueryService`-backed provider construction.
- Provider-to-snapshot mapping.
- Healthy real-provider snapshot.
- Partial/missing source degradation.
- Source exceptions converted to bounded warnings.
- Deterministic ordering.
- Bounded backlog, reports, events, and workflow lists.
- Missing PR metadata.
- No raw exception, secret, environment, or sensitive filesystem leakage in
  public JSON/HTML output.
- App factory injection and default app startup.
- JSON endpoint and HTML page use the real provider snapshot.
- Exact two-route HTTP surface.
- `/openapi.json`, `/docs`, and `/redoc` return 404.
- No mutation HTTP methods/routes.
- No trading, brokerage, Alpaca, trading database, or `dashboard.py` imports.
- Existing ENGDASH-001 and ENGDASH-002 behavior.
- Relevant engineering regression tests and full safe suite.

Completed evidence:

- Branch: `agent/engdash-003-query-service-provider`.
- PR #12 merged into `main` at merge commit `e285bc3`.
- Focused provider/API/read-model tests: `30 passed, 2 warnings`.
- Relevant engineering regression tests: `40 passed, 61 deselected, 2 warnings`.
- Full safe suite: `373 passed, 83 warnings`.
- Route inventory: `/api/engineering/snapshot` GET only; `/engineering` GET only;
  `/openapi.json`, `/docs`, and `/redoc` return 404.

### ENGDASH-004 — Live engineering status and workflow aggregation

Status: DONE
Owner: dashboard-agent
Priority: P1

Depends on: ENGDASH-003

Approved scope:

- Start from latest merged `main` after PR #12 merge.
- Use branch `agent/engdash-004-live-engineering-status`.
- Extend the read-only engineering dashboard into an operational status view
  that answers engineering health, agent activity, blockers, approval needs,
  recent test outcomes, report output, repository safety, and degraded providers
  without opening another page.
- Preserve the exact approved HTTP surface: `GET /api/engineering/snapshot`
  and `GET /engineering` only.
- Keep FastAPI automatic `/openapi.json`, `/docs`, and `/redoc` disabled.
- Keep all controls and writes out of the dashboard API/UI.

Allowed areas:

- `AGENT_BACKLOG.md`
- `dashboard_api/**`
- `engineering/**`
- `tests/**`
- `docs/**`
- `reports/**` only for task reports

Explicit exclusions:

- `dashboard.py`
- trading strategy code
- brokerage integrations
- live trading execution
- `.env` files, credentials, secrets, repository settings, CI/CD secrets, and
  branch protection

Acceptance criteria:

- The typed dashboard snapshot exposes explicit operational health, current
  agent/task activity, blockers, approval needs, testing status, report output,
  repository safety, and degraded provider state.
- The JSON endpoint and HTML page render the same typed snapshot data.
- Operational aggregates are deterministic, bounded, and tolerate missing
  metadata or unavailable providers through health warnings.
- The exact two-route HTTP surface is preserved.
- `/openapi.json`, `/docs`, and `/redoc` return 404.
- No mutation HTTP methods, control routes, approval actions, retries,
  pause/resume, execution, merge, or write APIs exist.
- No trading, brokerage, Alpaca, trading database, or `dashboard.py` imports
  are introduced.
- Focused dashboard/provider/read-model tests, relevant engineering regression
  tests, and the full safe suite pass.
- Repository is clean at completion.
- PR is open against `main` and ready for Josh's review.

Required tests:

- Live status aggregation for healthy and degraded snapshots.
- Current agent/task activity mapping from `EngineeringQueryService` data.
- Blockers and approval needs from workflow/report/timeline/warnings.
- Testing status, report summaries, and provider degradation metadata.
- Bounded lists and deterministic ordering.
- HTML and JSON rendering of the same operational snapshot.
- Exact route inventory and disabled `/openapi.json`, `/docs`, `/redoc`.
- Read-only verification: no mutation routes or controls.
- No trading, brokerage, Alpaca, trading database, or `dashboard.py` imports.
- Relevant engineering regression tests and full safe suite.

Completed evidence:

- Branch: `agent/engdash-004-live-engineering-status`.
- Focused dashboard/provider/read-model tests: `32 passed, 2 warnings`.
- Relevant engineering/dashboard regression tests: `69 passed, 2 warnings`.
- Full safe suite: `375 passed, 82 warnings`.
- Route inventory: `/api/engineering/snapshot` GET only; `/engineering` GET only;
  `/openapi.json`, `/docs`, and `/redoc` return 404; mutation methods return 405.
- PR #13: https://github.com/jsavoy93/trading-bot/pull/13 — merged into `main`.
- Merge commit on `main`: `31f455fb04a6ffff7adbec2bfbf743bc4b1ac1ed`.
- Implementation commit before evidence-only remediation: `b71279c9deded45bc1abc6110fa4a2e1241c4d7a`.
- Evidence-only remediation/final reviewed branch tip: `cd0260bd72496e6e9d5a7446e8a4a050c7cc52bc`.

---

## Phase F — Dashboard

### DASH-001 — Existing settings inventory

Status: TODO
Owner: dashboard-agent
Priority: P1

### DASH-002 — Read-only bot status page

Status: TODO
Owner: dashboard-agent
Priority: P1

### DASH-003 — Decision funnel

Status: TODO
Owner: dashboard-agent
Priority: P2

### DASH-004 — Per-symbol explanation

Status: TODO
Owner: dashboard-agent
Priority: P2

### DASH-005 — Versioned settings and rollback

Status: TODO
Owner: dashboard-agent
Priority: P3

### DASH-006 — Restricted paper-bot controls

Status: BLOCKED
Owner: dashboard-agent
Priority: P4

Blocked until:

- Non-root paper user exists.
- Restricted control wrapper exists.
- Paper safety tests pass.
