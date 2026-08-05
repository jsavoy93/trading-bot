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
4. `ENGSUP-001` — Automated Engineering Supervisor and Structured Handoff Protocol
5. `ENGDASH-006` — Live Agent Activity and Execution Visibility
6. `ENGCTRL-001` — Safe Engineering Control Panel
7. `CONFIG-002` — Dashboard-to-engine synchronization
8. `ENGPLAT-003` — Reusable Engineering Platform Repository Extraction (explicitly deferred)

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

## Governance Remediation (second pass — addresses 12 design issues)

This section replaces the first-pass governance remediation. Josh identified 12 issues
requiring corrections to the implementation approval packet before ENGPLAT-001 can be
approved. This block contains the corrected design.

---

### Allowed Areas

The following files are approved for modification by ENGPLAT-001 governance correction
(this branch) or ENGPLAT-001 implementation:

1. `engineering/models.py` — add typed `ProjectConfig`, `GovernanceFiles`, `WorkflowFiles`,
   `ProjectRegistry` dataclasses; add `parse_project_config()`, `validate_project_config()`,
   `parse_project_registry()`, `validate_project_registry()` functions
2. `tests/test_engineering_project_config.py` — new file; 22 test cases covering parsing,
   validation, registry, and safety
3. `AGENT_BACKLOG.md` — update ENGPLAT-001 entry to reflect this corrected remediation
4. `MENTOR.md` — add architecture note for project configuration contract
5. `TRADING_BOT_AUTONOMOUS_ENGINEERING_HANDOFF.md` — add architecture note for project
   configuration contract
6. `ITERATION_PROGRESS_LOG.md` — append continuity entry when implementation completes
7. `REPORT.md` — implementation rolling report (generated, in .gitignore, never committed)
8. `reports/` — timestamped archives; generated outside the committed tree or in `reports/`
   (directory already in .gitignore)

**No other files are authorized.** No directory-wide permissions. No files outside this
list may change during ENGPLAT-001 implementation.

---

### Branch Workflow

**Governance correction branch** (this branch): `agent/engplat-001-governance-correction`
- Based on current `main`
- Only governance document changes (this block)
- Opens a PR targeting `main`; does not implement the code

**Implementation branch** (future):
- Switch to `main`, fetch and fast-forward to `origin/main`
- Verify clean working tree (untracked ignored files are safe)
- Create: `git checkout -b agent/engplat-001-project-config`
- Implement only the 8 Allowed Areas listed above
- Open a PR targeting `main`; do not merge

---

### Project Registration Contract (Corrected Design)

#### Schema Versioning

`ProjectConfig` carries an explicit `schema_version: str` field.

| Property | Value |
|---|---|
| Supported version | `"1.0"` |
| Missing version (mapping has no `schema_version` key) | `ParseError` returned by `parse_project_config()` |
| Unsupported version (not `"1.0"`) | `ParseError` returned by `parse_project_config()` |
| Version conversion | Out of scope; ENGPLAT-001 does not migrate between versions |
| Future extension | New schema version required; version is part of the contract identity |

#### Unknown-Field Policy

**Reject unknown fields.** `parse_project_config()` returns a `ParseError` list when a
mapping contains field names not defined in `ProjectConfig`. Unknown fields are not
silently ignored and are not stored in an unvalidated `extra` dict.

Rationale: fail-closed is safer than silent acceptance. Forward compatibility is
achieved through explicit new schema versions, not through lenient parsing.

#### Parsing Boundary

Python's `@dataclass` raises `TypeError` for missing required fields at construction
time, before any application code can run. Because `validate_project_config()` receives
an already-constructed `ProjectConfig`, it cannot report _missing constructor
arguments_. To cover all structural errors deterministically:

```
parse_project_config(mapping: dict) -> ParseResult

  ParseResult is a structured result with:
    .config: ProjectConfig | None      — non-None only when all structural checks pass
    .errors: list[ParseError]          — deterministic, sanitized error strings
    .warnings: list[ParseWarning]     — bounded warnings for optional conditions

  ParseResult errors include:
    - missing required field
    - unknown field name
    - invalid field type for known field
    - unsupported schema_version
    - empty/malformed nested struct (GovernanceFiles, WorkflowFiles)
```

```
validate_project_config(config: ProjectConfig) -> list[str]

  Semantic validation (receives a valid ParseResult.config):
    - empty project_id or display_name
    - repository_root does not exist or is not absolute
    - resolved governance/workflow paths escape repository_root (path traversal)
    - required governance file missing (file does not exist on disk)
    - optional workflow file parent missing (parent directory does not exist)
    - conflicting settings (agents_may_merge=True with no approval policy path)
    - unsafe QA command patterns detected
    - non-positive qa_timeout_seconds
    - duplicate owner_ids or agent_owners
    - empty owner_ids (at least one human owner required)
```

**Responsibility separation:**
- `parse_project_config()`: structural (can a valid `ProjectConfig` be built from this input?)
- `validate_project_config()`: semantic (does this `ProjectConfig` make sense in the world?)

#### File Classification: Required vs. Optional

| Path field | Required? | Missing \-> | Notes |
|---|---|---|---|
| `governance_files.backlog_path` | **Required** | Error | Must exist; authoritative backlog |
| `governance_files.operating_plan_path` | **Required** | Error | Must exist; agent operating plan |
| `governance_files.owners_path` | **Required** | Error | Must exist; owners file |
| `governance_files.handoff_path` | **Required** | Error | Must exist; engineering handoff |
| `workflow_files.workflow_store_path` | **Required parent** | Warning | Parent dir must exist; file may not exist yet |
| `workflow_files.event_store_path` | **Required parent** | Warning | Parent dir must exist; file may not exist yet |
| `workflow_files.report_dir` | **Optional** | Warning | May be created by the platform; not required to pre-exist |

"Required" means the file must exist on disk at the time `validate_project_config()` is
called. "Required parent" means the parent directory must exist; the file itself may be
created later. "Optional" means neither the file nor its parent must pre-exist.

Path safety: all resolved governance and workflow paths must resolve to a location
under `repository_root`. Path traversal components (`..`) in any path field produce a
`ValidationError`. Symbolic link traversal is resolved and checked.

#### QA Command Safety Policy

| Rule | Input | Result |
|---|---|---|
| Empty tuple | `qa_commands = ()` | `ValidationError` |
| Destructive command | Any element contains `rm -rf`, `shutil.rmtree`, or equivalent | `ValidationError` |
| Live trading flag | Any element contains `--live`, `--production`, `--real`, `-l` | `ValidationError` |
| Secret printing | Any element contains `$SECRET`, `$API_KEY`, `$TOKEN`, or `print.*secret` | `ValidationError` |
| Shell operators | Any element contains `&&`, `\|\|`, `;`, `\|` (pipe to arbitrary command) | `ValidationError` |
| Non-test command | Any element starts with a command other than `pytest`, `python -m pytest`, `python -m unittest`, or explicitly approved tool | `ValidationError` |
| Non-positive timeout | `qa_timeout_seconds <= 0` | `ValidationError` |

Rationale: ENGPLAT-001 defines the contract. It does not execute QA commands. But the
contract must not store commands that would be unsafe to execute. A conservative reject
list is appropriate; a allowlist is better and should be added by a future task if needed.

#### Owner and Agent ID Policy

| Field | Rule |
|---|---|
| `owner_ids` | Non-empty `tuple[str, ...]`; at least one human owner required |
| `owner_ids` duplicates | `ValidationError` if duplicate strings detected |
| `agent_owners` | Non-empty `tuple[str, ...]` |
| `agent_owners` duplicates | `ValidationError` if duplicate strings detected |
| Normalization | Case-sensitive comparison; no automatic lowercasing or whitespace stripping |
| `agents_may_merge` | Always `False` for trading-bot; `ValidationError` if set to `True` without a full approval policy defined in governance files |

---

#### ProjectConfig Fields (exact final list)

Top-level fields:

| # | Field | Type | Required | Default | Notes |
|---|---|---|---|---|---|
| 1 | `schema_version` | `str` | Yes | `"1.0"` | Only `"1.0"` supported; any other value → `ParseError` |
| 2 | `project_id` | `str` | Yes | — | Non-empty slug; no whitespace |
| 3 | `display_name` | `str` | Yes | — | Non-empty; human-readable |
| 4 | `repository_root` | `Path` | Yes | — | Absolute path; must exist |
| 5 | `authoritative_base_branch` | `str` | Yes | — | Non-empty branch name |
| 6 | `governance_files` | `GovernanceFiles` | Yes | — | Nested; all 4 sub-fields required |
| 7 | `workflow_files` | `WorkflowFiles` | Yes | — | Nested; all 3 sub-fields required |
| 8 | `qa_commands` | `tuple[str, ...]` | Yes | — | Non-empty; safety-validated |
| 9 | `qa_timeout_seconds` | `int` | Yes | — | Positive integer (> 0) |
| 10 | `prohibited_operations` | `tuple[str, ...]` | Yes | `()` | May be empty |
| 11 | `agents_may_merge` | `bool` | Yes | `False` | Must be `False` initially |
| 12 | `owner_ids` | `tuple[str, ...]` | Yes | — | Non-empty; no duplicates |
| 13 | `agent_owners` | `tuple[str, ...]` | Yes | — | Non-empty; no duplicates |

Nested `GovernanceFiles`:

| # | Sub-field | Type | Required | Notes |
|---|---|---|---|---|
| G1 | `backlog_path` | `Path` | Yes | Must exist |
| G2 | `operating_plan_path` | `Path` | Yes | Must exist |
| G3 | `owners_path` | `Path` | Yes | Must exist |
| G4 | `handoff_path` | `Path` | Yes | Must exist |

Nested `WorkflowFiles`:

| # | Sub-field | Type | Required | Notes |
|---|---|---|---|---|
| W1 | `workflow_store_path` | `Path` | Yes | Parent dir must exist |
| W2 | `event_store_path` | `Path` | Yes | Parent dir must exist |
| W3 | `report_dir` | `Path` | Yes | Parent dir must exist |

**Field count summary:**
- Top-level `ProjectConfig` fields: 13 (including `schema_version`)
- `GovernanceFiles` sub-fields: 4
- `WorkflowFiles` sub-fields: 3
- **Total: 20 contract fields**

---

#### ProjectRegistry Representation

`ProjectRegistry` uses a design that can detect duplicate project IDs before they are
stored:

```python
@dataclass(frozen=True)
class ProjectRegistry:
    _projects: tuple[tuple[str, ProjectConfig], ...]
    version: str = "1.0"

    @classmethod
    def from_projects(cls, projects: sequence[ProjectConfig]) -> ProjectRegistry:
        """Build registry from a sequence. Raises DuplicateProjectId if IDs collide."""
        seen: dict[str, ProjectConfig] = {}
        for p in projects:
            if p.project_id in seen:
                raise DuplicateProjectId(p.project_id)
            seen[p.project_id] = p
        return cls(_projects=tuple(seen.items()), version="1.0")

    @property
    def projects(self) -> dict[str, ProjectConfig]:
        return dict(self._projects)


class DuplicateProjectId(Exception):
    """Raised when a project_id collision is detected in ProjectRegistry construction."""
```

`DuplicateProjectId` is a new exception class added to `engineering/models.py`.
Duplicate detection is proven by tests using `from_projects()` with a sequence
containing two configs with the same `project_id`.

---

### Acceptance Criteria (updated — 24 criteria)

| # | Criterion | Proof Method |
|---|---|---|
| 1 | `GovernanceFiles`, `WorkflowFiles`, `ProjectConfig`, `ProjectRegistry` dataclasses exist in `engineering/models.py` | Import checks |
| 2 | All dataclasses are frozen | `dataclasses.is_dataclass()` + `FrozenInstanceError` on mutation |
| 3 | `schema_version` field exists on `ProjectConfig`, default `"1.0"` | Instantiate with no arg; check field default |
| 4 | `parse_project_config(mapping)` exists and returns `ParseResult` | Import check; call with valid mapping |
| 5 | Missing required field returns `ParseError` (not `TypeError`) | Omit `project_id`; assert `.errors` non-empty, `.config` is None |
| 6 | Unknown field returns `ParseError` | Pass `{"unknown_field": "value"}`; assert error |
| 7 | Invalid field type returns `ParseError` | Pass `qa_timeout_seconds="not_an_int"`; assert error |
| 8 | Unsupported `schema_version` returns `ParseError` | Pass `schema_version="2.0"`; assert error |
| 9 | `validate_project_config(config)` exists and returns `list[str]` | Import check |
| 10 | Valid `TRADING_BOT_PROJECT` passes both parse and validate with empty error list | `pr = parse_project_config(asdict(TRADING_BOT_PROJECT)); assert pr.errors == []; assert pr.config is not None; assert validate_project_config(pr.config) == []` |
| 11 | Non-existent `repository_root` returns `ValidationError` | Valid parse result + invalid root; assert error list non-empty |
| 12 | Path traversal in governance path returns `ValidationError` | Valid parse result + `backlog_path=Path("../escape")`; assert error |
| 13 | Resolved path outside `repository_root` returns `ValidationError` | Valid parse result + symlink/relative path that escapes; assert error |
| 14 | Missing required governance file returns `ValidationError` | Valid parse result + file does not exist; assert error |
| 15 | Missing workflow file parent directory returns `ValidationWarning` (not error) | Valid parse result + parent missing; assert warning in result.warnings |
| 16 | `agents_may_merge=True` without approval policy in governance files returns `ValidationError` | Conflict setup; assert error |
| 17 | Unsafe QA command (e.g., `rm -rf`, `--live`) returns `ValidationError` | Valid parse result + unsafe command; assert error |
| 18 | `qa_timeout_seconds <= 0` returns `ValidationError` | Valid parse result + timeout=0; assert error |
| 19 | `ProjectRegistry.from_projects(sequence)` raises `DuplicateProjectId` on duplicate IDs | Two configs same ID; assert `DuplicateProjectId` raised |
| 20 | `ProjectRegistry.from_projects(list_of_configs)` roundtrip preserves all configs | Build registry; assert `.projects` dict equals input |
| 21 | `git diff --check` passes | `git diff --check` exits 0 |
| 22 | Full safe test suite passes | `TESTING=1 UNIT_TESTING=1 pytest -q` → 375+ passed |
| 23 | No runtime behavior changes | Trading bot, dashboard, workflow engine unchanged |
| 24 | Architecture notes added to `MENTOR.md` and `TRADING_BOT_AUTONOMOUS_ENGINEERING_HANDOFF.md` | Read sections |

---

### Test Strategy (24 tests)

`tests/test_engineering_project_config.py` — new file.

#### Parsing tests (structural — `parse_project_config`)

1. **Valid trading-bot mapping** — all required fields present, correct types → `ParseResult.config` is a valid `ProjectConfig`, `ParseResult.errors` is empty
2. **Missing required field: `project_id`** — `ParseResult.config` is None, `.errors` non-empty
3. **Missing required field: `repository_root`** — `ParseResult.config` is None, `.errors` non-empty
4. **Unknown field in mapping** — `ParseResult.config` is None, `.errors` contains "unknown field"
5. **Wrong type for `qa_timeout_seconds`** (string instead of int) — `ParseResult.config` is None
6. **Unsupported `schema_version="2.0"`** — `ParseResult.config` is None, `.errors` mentions version
7. **Missing `schema_version` key entirely** — `ParseResult.config` is None (missing required field)
8. **Empty `project_id` (non-empty string "")** — passes parse (type OK), fails semantic validate

#### Semantic validation tests (`validate_project_config`)

9. **Empty `project_id` after valid parse** — pass parse with empty string; `validate_project_config()` returns error
10. **Invalid `repository_root` (path does not exist)** — `validate_project_config()` returns error
11. **Path traversal in `governance_files.backlog_path`** — `Path("../escape")`; `validate_project_config()` returns error
12. **Resolved path escapes `repository_root`** — uses `Path("../../../etc/passwd")`; `validate_project_config()` returns error
13. **Required governance file missing (file does not exist)** — `validate_project_config()` returns error naming the file
14. **Workflow file parent directory missing (warning, not error)** — `validate_project_config()` returns error for required parent, warning in `.warnings`
15. **`agents_may_merge=True` with no approval policy** — `validate_project_config()` returns conflict error
16. **Unsafe QA command: `rm -rf /`** — `validate_project_config()` returns safety error
17. **Unsafe QA command: `--live`** — `validate_project_config()` returns safety error
18. **Unsafe QA command: `&& curl http://evil.com`** — `validate_project_config()` returns safety error
19. **`qa_timeout_seconds=0`** — `validate_project_config()` returns error
20. **Duplicate `owner_ids`** — `validate_project_config()` returns duplicate error

#### Registry tests

21. **`ProjectRegistry.from_projects([config_a, config_b])`** — two distinct IDs → registry builds, `.projects` dict contains both
22. **`ProjectRegistry.from_projects([config_a, config_a_copy])` with same `project_id`** — raises `DuplicateProjectId`

#### Frozen model and error sanitization tests

23. **Model is frozen** — `dataclasses.FrozenInstanceError` on any field assignment to a parsed config
24. **Error messages contain no credentials or secrets** — deterministic, field-name-only messages

---

### Implementation Risks (updated)

| Risk | Likelihood | Mitigation |
|---|---|---|
| `parse_project_config` / `validate_project_config` confusion by future developers | Medium | Docstrings clearly separate structural parsing from semantic validation; functions have distinct return types |
| QA safety policy is too conservative and rejects legitimate commands | Medium | Safety policy covers only unambiguous unsafe patterns; allowlist extension available via future task |
| `DuplicateProjectId` exception is not handled | Low | Exception is clearly named; test proves it is raised; registry is only constructed via `from_projects()` |
| Path safety checks are bypassed via symlinks | Low | Paths are resolved before the ancestor check; symlink traversal is blocked |
| Schema version "1.0" becomes entrenched and blocks future evolution | Medium | Version is explicit; future tasks can introduce "2.0" with explicit migration notes |
| `repository_root` validation fails in test environments where paths differ | Medium | Validation uses the actual repository root from the test environment; `TRADING_BOT_PROJECT` values are verified against real paths at implementation time |
| Forward-compatibility via unknown-field rejection blocks early projects | Low | Unknown-field rejection is the safer default; can be relaxed in a future schema version if genuinely needed |

---

### Execution Gate (updated)

ENGPLAT-001 remains non-executable until ALL of the following are true:

1. This corrected governance remediation is approved by Josh on the PR for
   `agent/engplat-001-governance-correction`.
2. A **new** implementation branch is created from current `main`:
   `git checkout main && git pull --ff-only origin/main && git checkout -b agent/engplat-001-project-config`
3. The implementation plan includes exactly the 8 files listed in Allowed Areas above.
4. Josh explicitly approves the implementation plan by commenting `/approve` on the
   implementation PR.
5. The implementation PR is reviewed and approved; do not merge without explicit
   Josh authorization.

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

Depends on: ENGPLAT-001, ENGDASH-004

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

### ENGSUP-001 — Automated Engineering Supervisor and Structured Handoff Protocol

Status: TODO
Owner: trading-manager
Priority: P1

Depends on: ENGPLAT-001, ENGPLAT-002

Purpose:

Replace the manual copying of agent reports and next-step prompts between Josh and
external advisors with a governed supervisor service that consumes structured
completion evidence, verifies repository state independently, selects the next
permitted workflow transition, and generates a bounded instruction for the
appropriate agent — while always preserving explicit human approval gates.

Execution gate:

- Non-executable until Josh approves a narrow implementation plan with allowed
  areas for this task.
- This roadmap entry does not authorize automatic agent dispatch, dashboard
  control surface additions, runtime workflow changes, secrets changes, or
  live trading changes.

---

## Governance Remediation (this section)

This block is the approved architectural design for ENGSUP-001. Josh approved
this design on 2026-08-05.

---

### Context: The Manual Relay Loop

Current process:

1. Engineering agent completes a task and produces a report.
2. Josh copies the report to an external advisor.
3. The advisor evaluates and generates the next prompt.
4. Josh copies the prompt back to the engineering agent.
5. The cycle repeats until a human approval gate is reached.

Problems with the manual loop:

- Josh is a bottleneck for every signal and instruction.
- Agent reports are prose; state must be re-verified by hand.
- No structured record of decisions, evidence, or reasoning chains.
- No bounded instruction generation — prompts can expand scope unintentionally.
- No loop protection; cycles can continue indefinitely.
- No systematic way to distinguish routine transitions from approval-gated ones.

---

### Supervisor Architecture

The supervisor is a typed service layer between the workflow engine and agents.
It does not replace the workflow engine. It replaces the manual relay loop.

```
Workflow Engine
    │
    │  emits structured completion packet
    ▼
Supervisor Service
    │
    ├── reads: completion packet + repository state + event store
    ├── verifies: evidence independently (does not trust agent summaries alone)
    ├── decides: typed outcome from SupervisorDecision contract
    ├── gates: human approval required? → stop and dispatch to inbox
    ├── generates: bounded next instruction (if not stopped)
    └── routes: next instruction to appropriate agent role
```

Phase 1 of the supervisor is read-only evaluation and prompt generation.
Josh manually approves dispatch. No agent is dispatched automatically.

---

### Structured Completion Packet

The supervisor consumes a typed, versioned completion record. This is the minimum
required schema. The version field allows future evolution without breaking
existing consumers.

```python
@dataclass(frozen=True)
class CompletionPacket:
    version: str  # e.g. "1.0" — supervisor rejects unknown versions

    # Identity
    project_id: str
    task_id: str
    workflow_run_id: str
    workflow_stage: str  # e.g. "REPORT", "QA", "REVIEW"

    # Repository state at completion
    branch: str
    base_branch: str  # e.g. "main"
    head_commit: str  # full commit hash
    working_tree_status: str  # "clean" | "dirty" | "untracked"
    files_changed: tuple[str, ...]  # list of file paths changed
    lines_changed: tuple[tuple[str, int, int], ...]  # (path, additions, deletions)

    # Evidence
    test_results: TestResultSummary  # see below
    report_path: str | None  # absolute path to the human-readable report
    archive_path: str | None  # absolute path to the detailed archive
    pr_url: str | None
    pr_state: str  # "open" | "closed" | "merged"

    # Decision-support
    risks_identified: tuple[str, ...]
    blockers: tuple[str, ...]
    requested_human_action: str | None
    prior_review_findings: tuple[str, ...]
    iteration_number: int  # how many implementation-review cycles this task has had

    # Timestamps
    started_at: str  # ISO-8601
    completed_at: str  # ISO-8601

    # Chain of evidence (all supervisor-verifiable)
    evidence_refs: tuple[EvidenceRef, ...]  # see below


@dataclass(frozen=True)
class TestResultSummary:
    total: int
    passed: int
    failed: int
    skipped: int
    warnings: int
    duration_seconds: float
    command: str  # the exact pytest command run
    output_path: str | None  # path to full output if captured


@dataclass(frozen=True)
class EvidenceRef:
    kind: str  # "git_status" | "git_log" | "test_output" | "diff" | "pr_state" | "report"
    path: str | None  # file path or URL if applicable
    verified: bool  # supervisor has independently confirmed this evidence
    hash: str | None  # content hash if applicable
```

**Design principles:**

- The packet is a fact container, not a narrative. It contains what happened,
  not what the agent interpreted.
- `evidence_refs` lists every piece of evidence the supervisor may verify.
  An agent cannot suppress evidence by omitting it from prose.
- `verified: bool` on each ref tracks whether the supervisor independently
  confirmed the evidence — not whether the agent claimed it was true.
- `report_path` retains the human-readable Markdown report for human reviewers.
  The supervisor does not infer state from prose.
- `working_tree_status` is verified by the supervisor, not taken from the agent.

---

### Supervisor Decision Contract

The supervisor produces a typed decision. Every decision includes reason codes,
supporting evidence, next agent role, bounded instruction, and whether a human
approval gate is required.

```python
class SupervisorDecisionKind(str, Enum):
    # Human-gated (must stop for Josh before continuing)
    WAIT_FOR_HUMAN_APPROVAL = "WAIT_FOR_HUMAN_APPROVAL"
    READY_FOR_MERGE_APPROVAL = "READY_FOR_MERGE_APPROVAL"
    BLOCKED = "BLOCKED"
    ESCALATE_POLICY_CONFLICT = "ESCALATE_POLICY_CONFLICT"

    # Routine — may be authorized for auto-dispatch in Phase 2
    CONTINUE = "CONTINUE"
    RUN_QA = "RUN_QA"
    RUN_READ_ONLY_REVIEW = "RUN_READ_ONLY_REVIEW"
    RETRY = "RETRY"
    REQUEST_CHANGES = "REQUEST_CHANGES"

    # Terminal
    COMPLETE = "COMPLETE"


@dataclass(frozen=True)
class SupervisorDecision:
    kind: SupervisorDecisionKind
    reason_codes: tuple[str, ...]  # e.g. "TESTS_PASS", "NO_EVIDENCE_OF_FAILURE"
    supporting_evidence: tuple[str, ...]  # human-readable evidence summary
    next_agent_role: str | None  # e.g. "trading-manager", "dashboard-agent"
    next_workflow_stage: str | None  # e.g. "QA", "DELEGATE"
    generated_instruction: str | None  # bounded instruction for next agent
    human_approval_required: bool  # True for WAIT_FOR_HUMAN_APPROVAL kinds
    josh_approval_required: bool  # True for merge, scope expansion, safety changes
    permitted_files: tuple[str, ...] | None  # explicit file list or None=all
    prohibited_operations: tuple[str, ...] | None  # explicit prohibited list
    expiration_seconds: int | None  # if decision expires; None=never
    stale_conditions: tuple[str, ...] | None  # conditions that make this decision stale
    alternative_decisions: tuple[SupervisorDecisionKind, ...] | None  # what else was considered
    supervisor_note: str | None  # concise reasoning summary (not chain-of-thought)
```

**Decision kinds and when each applies:**

| Decision | When it applies |
|---|---|
| `CONTINUE` | Task done, no blockers, next task is available and within current allowed areas |
| `RUN_QA` | Implementation complete; QA not yet run; tests authorized |
| `RUN_READ_ONLY_REVIEW` | QA passed; review is routine; reviewer role is authorized |
| `RETRY` | QA failed; failure is bounded and fixable; retry authorized |
| `REQUEST_CHANGES` | Review found issues; changes are bounded and specific |
| `WAIT_FOR_HUMAN_APPROVAL` | Decision requires Josh to evaluate and approve dispatch |
| `READY_FOR_MERGE_APPROVAL` | All criteria met; human must approve the merge itself |
| `BLOCKED` | Task depends on incomplete predecessor; no workaround available |
| `ESCALATE_POLICY_CONFLICT` | Agent requested something prohibited; cannot proceed |
| `COMPLETE` | Task fully done; no further action needed |

---

### Evidence Verification

The supervisor must independently verify evidence before trusting it. Agent
summaries are never the sole source of truth.

**Verifiable evidence and verification method:**

| Evidence | Verification method |
|---|---|
| `working_tree_status` | Run `git status`; compare to agent's reported status |
| `head_commit` | Run `git rev-parse HEAD`; compare to packet |
| `branch` | Run `git branch --show-current`; compare to packet |
| `files_changed` | Run `git diff --name-only` vs packet |
| `lines_changed` | Run `git diff --stat` vs packet |
| `test_results` | Parse actual pytest output; compare to packet summary |
| `pr_state` | Query GitHub API or run `gh pr view`; compare to packet |
| `report_path` exists | `pathlib.Path(report_path).exists()` |
| `archive_path` exists | `pathlib.Path(archive_path).exists()` |

**Verification rules:**

- Every `EvidenceRef` with `verified=False` must be verified before the decision
  is trusted.
- If evidence cannot be verified (e.g., path does not exist), the supervisor
  must note the discrepancy and treat the decision as unverified.
- Mismatch between agent-reported evidence and independently verified evidence
  → `ESCALATE_POLICY_CONFLICT` with reason code `EVIDENCE_MISMATCH`.
- The supervisor never trusts an agent's summary of its own work.

---

### Human Approval Gate Matrix

The supervisor must always stop and route to Josh before:

| Situation | Why it stops | What Josh receives |
|---|---|---|
| Merge requested | No autonomous merges | Decision + evidence + bounded instruction |
| New task scope authorization | Agents may not self-authorize | Task ID + proposed scope + allowed areas |
| Allowed-area expansion | Expanding scope requires human judgment | Current scope + proposed expansion + risks |
| Destructive Git operations | Safety-critical | Operation description + evidence + risks |
| Deployment or infrastructure change | Safety-critical | Change description + evidence + risks |
| Secret or credential modification | Safety-critical | Change description + evidence + risks |
| Live trading or brokerage action | Safety-critical | Action description + evidence + risks |
| Safety policy change | Platform-level governance | Proposed change + rationale + risks |
| Repository extraction | Irreversible architectural change | Extraction plan + prerequisites + risks |
| Unresolved policy conflict | Agent requested something prohibited | Conflict description + agent's request + supervisor's reasoning |
| `READY_FOR_MERGE_APPROVAL` | Human must approve the merge itself | PR link + diff summary + test results + risks |
| `ESCALATE_POLICY_CONFLICT` | Cannot proceed safely | Conflict description + evidence + supervisor note |
| `BLOCKED` with no workaround | Cannot proceed safely | Blocker description + evidence |

**Josh's approval inbox receives:** decision kind, supervisor recommendation,
supporting evidence, generated bounded instruction, required action, and
permitted/prohibited lists. Josh may then approve, reject, or edit the instruction.

---

### Automatic Transition Matrix

Routine transitions identified as candidates for Phase 2 auto-dispatch.
Automatic execution is **disabled by default** and requires separate Josh approval
for each transition kind.

| From stage | Trigger | To stage | Conditions for auto-dispatch |
|---|---|---|---|
| IMPLEMENTATION | Implementation complete | QA | Tests not yet run; working tree clean; no blockers |
| QA | All tests pass | REVIEW | Reviewer role authorized; no failures in evidence |
| QA | Non-critical failures | IMPLEMENTATION (fix) | Failures are bounded, non-safety, retry count < 3 |
| REVIEW | Changes requested | IMPLEMENTATION (fix) | Findings are bounded, specific, not safety-related |
| REVIEW | All criteria met | REPORT | Recommendation is ACCEPT; no open blockers |
| REPORT | Report complete | WAIT_FOR_APPROVAL | Human gate for merge |
| WAIT_FOR_APPROVAL | Josh approved | MERGE_PREPARATION | Josh explicitly approved |
| TIMED_OUT | Agent timed out | IMPLEMENTATION (retry) | Timeout is retryable; retry count < max |
| EVIDENCE_INCONSISTENT | Evidence mismatch detected | EVIDENCE_CORRECTION | Mismatch is correctable without new implementation |

**Auto-dispatch conditions (all must be true):**

1. Decision kind is in the auto-dispatch whitelist (approved per-transition by Josh).
2. `josh_approval_required` is `False` on the decision.
3. No `stale_conditions` are currently met.
4. Supervisor has verified all evidence in the completion packet.
5. Retry count for this task is below the loop-protection maximum.
6. Working tree is clean (supervisor verifies independently).
7. Head commit matches the completion packet's reported commit.

---

### Loop Protection Rules

The supervisor must escalate rather than loop indefinitely.

| Rule | Threshold | Action when exceeded |
|---|---|---|
| Implementation-review cycles | > 3 cycles on same task | Supervisor escalates to Josh with cycle summary |
| Identical `REQUEST_CHANGES` findings | Same finding repeated 2+ times | Supervisor notes repetition; requires Josh approval to continue |
| Identical QA failure | Same failure 2+ times | Supervisor escalates; no auto-retry |
| Impossible requirement detected | Supervisor cannot satisfy a listed criterion | `ESCALATE_POLICY_CONFLICT` with reason `IMPOSSIBLE_REQUIREMENT` |
| Stale decision | Expiration reached without action | Supervisor re-evaluates from current state |
| Changed PR head | Commit changed since decision | Supervisor re-evaluates; invalidates prior decision |
| Dirty working tree at dispatch | Tree is dirty when agent should start | Supervisor does not dispatch; escalates `DIRTY_TREE` |
| Task scope drift | Files changed outside current allowed areas | Supervisor does not dispatch; escalates `SCOPE_DRIFT` |
| Reviewer disagreement | Reviewer and supervisor disagree on outcome | Supervisor defers to human reviewer; notes discrepancy |

**Escalation always routes to Josh.** The supervisor never consumes an escalation
itself.

---

### Privacy and Security Constraints

The supervisor is an engineering tool, not a surveillance tool. These constraints
are non-negotiable.

**The supervisor must NOT expose:**

- Raw agent chain-of-thought or internal reasoning
- Arbitrary command output beyond what is in the completion packet evidence
- Credentials, secrets, API keys, or tokens of any kind
- Internal error messages that reveal system architecture
- More than 50 lines of any single diff in supervisor output
- Agent memory state or session history
- Any information about non-project communications

**The supervisor MAY expose:**

- Concise reasoning summaries (2–5 sentences maximum)
- Evidence-to-reason codes (what fact led to what decision)
- Diff paths and summary statistics (not full content)
- Test result pass/fail with exact counts
- Report excerpts (bounded to 200 lines)
- Supervisor decision and recommendation
- Bounded next instruction

**Sanitization requirements:**

- Every `generated_instruction` must be scanned for secret patterns before
  inclusion in the approval inbox.
- Any evidence blob exceeding 50 lines must be summarized, not raw-dumped.
- Credentials in evidence refs must be redacted before the inbox display.

---

### Dashboard Integration Design (Non-executable)

This section describes the future approval inbox design. No dashboard controls
may be added during ENGSUP-001 implementation. This is an architectural
reference for future work.

**Approval inbox — minimum viable display:**

```
Pending Decision
──────────────────────────────────────────
Task: ENGDASH-005 Engineering Timeline
Stage: REVIEW
Branch: agent/engdash-005-timeline
Commit: a3f7c1d
──────────────────────────────────────────
Supervisor recommendation: READY_FOR_MERGE_APPROVAL
Reason codes: TESTS_PASS, REVIEW_ACCEPT, NO_BLOCKERS
Evidence: tests (50 passed), git status (clean), PR (open)

Generated instruction (Josh approval required for merge):
  "Merge agent/engdash-005-timeline into main.
   All acceptance criteria verified. No blockers."

Permitted files: none (merge only)
Prohibited: none
──────────────────────────────────────────
Actions:
  [Approve Merge]  [Reject + Comment]  [Edit Instruction]
```

**Display requirements:**

- One decision per task visible at a time.
- Supervisor recommendation is prominent but clearly labeled as a recommendation.
- Evidence is verifiable — clicking any evidence item triggers independent
  supervisor verification (not agent's word).
- Action buttons are explicit and audited.
- Supervisor reasoning summary is visible but not expandable into raw chain-of-thought.

---

### Roadmap Placement and Priority Order

ENGSUP-001 is inserted between ENGPLAT-002 and ENGDASH-006.

**Updated priority order:**

1. `ENGPLAT-001` — Project Registration and Managed-Project Configuration
2. `ENGDASH-005` — Engineering Timeline and Historical Activity
3. `ENGPLAT-002` — Repository and Project Adapter Boundaries
4. `ENGSUP-001` — Automated Engineering Supervisor and Structured Handoff Protocol ← NEW
5. `ENGDASH-006` — Live Agent Activity and Execution Visibility
6. `ENGCTRL-001` — Safe Engineering Control Panel
7. `CONFIG-002` — Dashboard-to-engine synchronization
8. `ENGPLAT-003` — Reusable Engineering Platform Repository Extraction (explicitly deferred)

**Rationale for placement:**

- Supervisor builds on project configuration (ENGPLAT-001) and adapters (ENGPLAT-002).
- Supervisor does not require ENGDASH-005 timeline display (Phase 1 is prompt generation only).
- Supervisor enables better governance for ENGDASH-006 and ENGCTRL-001 by automating routine handoffs.
- CONFIG-002 synchronization remains last; it coordinates dashboard ↔ engine, which supervisor uses.

---

### Phased Implementation

ENGSUP-001 is implemented in four phases. Each phase requires separate Josh
approval before the next begins.

#### Phase 1 — Supervisor Prompt Generation

**Goal**: Replace the manual copying of reports and prompts with a supervised
read-only evaluation loop.

- Supervisor reads completion packets from the event store.
- Supervisor independently verifies evidence.
- Supervisor produces a typed decision with bounded instruction.
- Josh receives the decision and instruction; Josh manually approves dispatch.
- No agent is dispatched automatically.
- Dashboard shows pending decision (read-only display; no controls).

**Entry criteria**: ENGPLAT-001 and ENGPLAT-002 complete and stable.

#### Phase 2 — Routine Auto-Dispatch

**Goal**: Eliminate manual copying for routine, low-risk transitions.

- Josh approves specific transition kinds for auto-dispatch (e.g., `QA_PASS → REVIEW`).
- Supervisor dispatches approved transitions without Josh manually copying prompts.
- All dispatch events are audited in the event store.
- Supervisor re-verifies evidence before every dispatch.

**Entry criteria**: Phase 1 stable; Josh has approved the auto-dispatch transition whitelist.

#### Phase 3 — Dashboard Approval Inbox

**Goal**: Provide a structured human-approval interface replacing informal copying.

- Dashboard shows supervisor decisions with evidence, reasoning summary, and
  bounded instruction.
- Josh approves, rejects, or edits instructions from the dashboard.
- All Josh decisions are audited.
- Supervisor routes decisions to the appropriate agent only after Josh acts.

**Entry criteria**: Phase 2 stable; dashboard has a read-only approval inbox display.

#### Phase 4 — Multi-Project Supervisor

**Goal**: Same supervisor service manages multiple managed projects.

- Supervisor reads project configuration (ENGPLAT-001) for each registered project.
- Supervisor dispatches to different agents for different projects.
- Inter-project routing is governed by project registry.
- Cross-project escalation routes to Josh.

**Entry criteria**: Phase 3 stable; at least two projects registered with the platform.

---

### Acceptance Criteria

The following criteria apply to ENGSUP-001 implementation as a whole (all phases).
Individual phases have their own entry criteria.

| # | Criterion | Proof Method |
|---|---|---|
| 1 | `CompletionPacket` dataclass exists in `engineering/models.py` with all required fields | Import check |
| 2 | `SupervisorDecision` and `SupervisorDecisionKind` exist in `engineering/models.py` | Import check |
| 3 | `SupervisorDecision` is frozen | `dataclasses.is_dataclass` check |
| 4 | Supervisor can independently verify Git working-tree status | Unit test: mock `git status` output; verify supervisor detects clean vs dirty |
| 5 | Supervisor detects evidence mismatch between agent packet and verified state | Unit test: packet claims clean; git shows dirty → decision is `ESCALATE_POLICY_CONFLICT` |
| 6 | Supervisor decision includes correct `human_approval_required` flag for `WAIT_FOR_HUMAN_APPROVAL` | Unit test: assert `human_approval_required=True` for merge request |
| 7 | Supervisor decision includes correct `human_approval_required` flag for `CONTINUE` | Unit test: assert `human_approval_required=False` for routine continue |
| 8 | Supervisor does not include raw chain-of-thought in `supervisor_note` | Unit test: note is ≤ 5 sentences |
| 9 | Loop protection: supervisor detects repeated identical QA failures | Unit test: 3rd identical failure → `ESCALATE_POLICY_CONFLICT` not `RETRY` |
| 10 | Loop protection: supervisor detects scope drift | Unit test: files changed outside allowed areas → does not dispatch |
| 11 | Privacy: supervisor sanitizes evidence blobs > 50 lines | Unit test: long diff → truncated with summary note |
| 12 | `git diff --check` passes | `git diff --check` exits 0 |
| 13 | Full safe test suite passes | `TESTING=1 UNIT_TESTING=1 pytest -q` → 375+ passed |
| 14 | No runtime workflow engine changes | Workflow engine code unchanged (spot-check) |
| 15 | No automatic agent dispatch in Phase 1 | Supervisor only generates instructions; dispatch requires Josh approval |

---

### Planned Tests

`tests/test_engineering_supervisor.py`

1. **CompletionPacket: valid construction** — all required fields present; version check
2. **CompletionPacket: frozen** — mutation raises `FrozenInstanceError`
3. **SupervisorDecision: WAIT_FOR_HUMAN_APPROVAL requires human approval** — flag is `True`
4. **SupervisorDecision: CONTINUE does not require human approval** — flag is `False`
5. **Evidence verification: clean tree** — git output shows clean → supervisor verifies as clean
6. **Evidence verification: mismatch detected** — agent says clean, git shows dirty → `EVIDENCE_MISMATCH`
7. **Evidence verification: commit matches** — `git rev-parse HEAD` matches packet head_commit
8. **Evidence verification: commit mismatch** — commit differs → decision flags mismatch
9. **Loop protection: 3rd identical QA failure** — repeated same failure → escalation not retry
10. **Loop protection: scope drift detected** — files outside allowed areas → no dispatch
11. **Privacy: supervisor_note length** — note is ≤ 5 sentences
12. **Privacy: evidence truncation** — diff > 50 lines → truncated with "..." marker
13. **Auto-dispatch: not authorized in Phase 1** — decision generated but dispatch flag is `False`
14. **Auto-dispatch whitelist: only authorized kinds dispatch** — non-whitelisted kinds require approval

---

### Implementation Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Supervisor becomes a new bottleneck if its decisions are wrong | Medium | Fail-closed: supervisor escalates rather than proceeding on ambiguity; every decision can be overridden by Josh |
| Agent adversarial behavior: agent poisons completion packet | Medium | Independent evidence verification catches discrepancies; supervisor never trusts agent's word alone |
| Scope drift during auto-dispatch (Phase 2) | Medium | Explicit allowed-area checking before every dispatch; loop protection thresholds; no dispatch if tree is dirty |
| Supervisor reasoning is wrong for a complex case | Medium | Supervisor never proceeds on safety-critical decisions without Josh; complex cases always escalate |
| Completion packets become stale / PR head changes between decision and dispatch | Low | Stale-conditions field on every decision; supervisor re-evaluates if head changes |
| Privacy violation: supervisor exposes chain-of-thought | Low | `supervisor_note` is capped at 5 sentences; evidence blobs sanitized; automated linting on new supervisor output types |
| Supervisor adds latency to the engineering loop | Low | Phase 1 is read-only; verification is bounded; no network calls in critical path |
| Phase 2 auto-dispatch approved transitions prove too broad | Medium | Auto-dispatch whitelist is per-transition-kind; Josh approves each kind explicitly; whitelist is audited |

---

### Execution Gate (Repeat)

ENGSUP-001 remains non-executable until:

1. Josh approves this design and the roadmap placement.
2. A feature branch is created for the implementation.
3. The implementation plan includes exactly the files listed in the Allowed Areas for the current phase.
4. Josh explicitly approves the Phase 1 implementation plan.
5. Separate Josh approval is obtained before Phase 2 auto-dispatch begins.

No automatic dispatch begins without explicit Phase 2 approval.


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
