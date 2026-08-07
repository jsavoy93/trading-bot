# Agent Repair Backlog

Agents may work only on items listed here or explicitly approved by Josh.

## Status Values

- TODO
- IN_PROGRESS
- BLOCKED
- REVIEW
- DONE

---

## Approved Engineering Platform Priority Order (Revised 2026-08-06)

ENGPLAT-001 (ProjectConfig contract) is complete and merged. The typed project
configuration contract exists but is not yet consumed by any engineering service.
The platform has the vocabulary but not yet the grammar of project-agnostic design.

Josh approved the revised engineering-platform priorities on 2026-08-06.

Priority order:

1. `ENGPLAT-001` — Project Registration and Managed-Project Configuration ✅ DONE
2. `ENGPLAT-002A` — ProjectContext Contracts and Composition Boundary
3. `ENGPLAT-002B` — Local Read Adapters and Manager Integration
4. `ENGDASH-005` — Engineering Timeline and Historical Activity
5. `ENGPLAT-002C` — Remaining Adapters and Service Migration
6. `ENGPLAT-003` — Project Bootstrap
7. `ENGSUP-001` — Automated Engineering Supervisor and Structured Handoff Protocol
8. `ENGDASH-006` — Live Agent Activity and Execution Visibility
9. `ENGCTRL-001` — Safe Engineering Control Panel
10. `CONFIG-002` — Dashboard-to-engine synchronization
11. `ENGPLAT-004` — Reusable Engineering Platform Repository Extraction (deferred)

**Rationale for ordering:**

- ENGPLAT-002A defines contracts without implementing adapters or touching any
  service. It is the smallest possible first step.
- ENGPLAT-002B implements read-oriented adapters needed by the manager and
  unblocks ENGDASH-005. ENGDASH-005 needs only stable read interfaces for
  governance, workflow history, and events — it does not need Git, QA, or
  write adapters.
- ENGPLAT-002C completes the remaining adapters (Git, QA, File) and migrates
  remaining services. It is intentionally placed after ENGDASH-005 so that
  the dashboard does not wait for the full adapter suite.
- ENGPLAT-003 (Bootstrap) waits until the full adapter contract is stable
  (after 002C), since bootstrap generates ProjectConfig instances for new
  repositories and should not proceed until the adapter layer is complete.
- Repository extraction (ENGPLAT-004) is correctly deferred until the platform
  has proven itself with multiple projects.

Roadmap constraints:

- `CONFIG-002` remains queued behind the engineering-platform work unless Josh
  later changes the priority.
- Future implementation tasks remain non-executable until each receives narrow
  allowed areas and Josh approval for that implementation slice.
- Do not extract a reusable engineering-platform repository until all preceding
  tasks (ENGPLAT-002A, ENGPLAT-002B, ENGPLAT-002C, ENGPLAT-003, ENGDASH-005,
  ENGSUP-001 Phase 1) have been proven through normal use and Josh separately
  approves cross-repository planning.
- ENGPLAT-004 extraction is explicitly deferred; see extraction readiness
  criteria in MENTOR.md.

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

Status: DONE
Owner: trading-manager
Priority: P1
Implemented by: agent/engplat-001-project-config
Implementation commit: 6e5ee89 (squash-merged as part of PR #16)
Completed: 2026-08-05

Purpose:

Create a project-local configuration contract allowing the engineering platform
to manage a repository without hard-coded trading-bot paths, commands, names,
or ownership assumptions.

---

## Implementation Summary

The corrected governance remediation was approved in PR #15 and implemented on
`agent/engplat-001-project-config` (commit `6e5ee89`). PR #16 is open for this
implementation.

### Files Changed

| File | Change |
|---|---|
| `engineering/models.py` | Added `GovernanceFiles`, `WorkflowFiles`, `ProjectConfig`, `ProjectRegistry`, `ParseResult`, `DuplicateProjectId`, `parse_project_config()`, `validate_project_config()`, `TRADING_BOT_PROJECT` |
| `tests/test_engineering_project_config.py` | New file; 51 test cases |
| `AGENT_BACKLOG.md` | Updated ENGPLAT-001 status to DONE |
| `MENTOR.md` | Added architecture note |
| `TRADING_BOT_AUTONOMOUS_ENGINEERING_HANDOFF.md` | Added architecture note |
| `ITERATION_PROGRESS_LOG.md` | Continuity entry appended |

### Contract Summary

- **20 contract fields**: 13 top-level + 4 GovernanceFiles + 3 WorkflowFiles
- **Parse/validate separation**: `parse_project_config()` (structural) ≠ `validate_project_config()` (semantic)
- **Schema version**: explicit `"1.0"`; unknown versions rejected
- **Unknown-field policy**: reject unknown fields (fail-closed)
- **Registry**: `from_projects()` classmethod; `DuplicateProjectId` on collision
- **QA safety**: conservative reject list (rm -rf, --live, shell operators, etc.)
- **Tests**: 51 passed (24 planned + additional edge cases)
- **Full safe suite**: 426 passed, 84 warnings

### ENGPLAT-002A — ProjectContext Contracts and Composition Boundary

Status: TODO
Owner: trading-manager
Priority: P1

Depends on: ENGPLAT-001

Purpose:

Define the reusable dependency contracts — the Protocol types, the
ProjectContext dataclass, and the factory contract — without implementing
concrete adapters or integrating existing services.

Execution gate:

- Non-executable until Josh approves a narrow implementation plan with allowed
  areas for this task.
- Do not implement concrete adapter classes.
- Do not modify `manager.py` or any other engineering service.
- Do not add a backward-compatibility shim.
- Do not migrate any service.

---

### ENGPLAT-002A allowed areas (implementation governance)

Exact files authorized for 002A implementation. No other files may be created or modified.

**New files**:

| File | Purpose |
|---|---|
| `engineering/adapters.py` | Protocol definitions + `ProjectContext` dataclass + `ProjectMetadata` |
| `engineering/context.py` | `build_project_context()` factory stub (Option B: raises `NotImplementedError`) |
| `tests/test_engineering_project_context.py` | Structural contract tests |

**Governance files updated** (no runtime code changes):

| File | Change |
|---|---|
| `AGENT_BACKLOG.md` | This allowed-areas section added |
| `MENTOR.md` | Optional: update adapter protocol responsibilities table |

**Not authorized** (explicit prohibitions):

- `engineering/` — any existing files (manager.py, backlog.py, etc.)
- `src/` — any runtime trading code
- `tests/` — any existing test files
- `dashboard/` — any dashboard code
- `reports/` — report generation
- `config.py`, `git_service.py`, `qa_runner.py`, `reporter.py`, `query_service.py`
- Any concrete adapter implementations (GovernanceAdapter, WorkflowAdapter, etc.)
- Any `manager.py` changes
- Any backward-compatibility shim
- Any filesystem artifacts outside `engineering/adapters.py`, `engineering/context.py`

### ENGPLAT-002A implementation risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Protocol signatures drift from `engineering/models.py` types | Medium | Signatures reference `models.` namespace; update if models.py changes |
| Factory stub confuses future implementors (002B) | Low | Contract documentation explicitly states stub; 002B implements concrete adapters |
| Structural tests insufficient to catch signature errors | Medium | Use `runtime_checkable()`; test import and Protocol membership |
| Import cycles if adapters.py imports models.py circularly | Low | models.py already imported by other engineering modules; verify no cycle |

### ENGPLAT-002A stop criteria

Implementation stops immediately if:
- Any change attempts to modify an existing engineering/ file
- Any change attempts to implement a concrete adapter class
- Any change attempts to modify manager.py
- Any change attempts to add a backward-compatibility shim
- Structural tests fail after the allowed attempt
- Repository becomes dirty (uncommitted changes to prohibited files)

---

### ENGPLAT-002A scope

- Define `@dataclass(frozen=True) class ProjectContext` with all 8 fields
- Define `GitReadAdapter` Protocol (read-only; no mutations)
- Define `GovernanceAdapter` Protocol
- Define `WorkflowAdapter` Protocol
- Define `QAAdapter` Protocol (configuration only; no `run_qa`)
- Define `FileReadAdapter` Protocol (read-only; no `write_text`)
- Define `EventAdapter` Protocol
- Define `@dataclass(frozen=True) class ProjectMetadata`
- Define the `build_project_context(config: ProjectConfig)` factory contract
  (Option B: deferred concrete construction to 002B; 002A defines signature + validation)
- Define the propagation rule
- Define the validation and error contract (deterministic, sanitized, no fallback)
- Tests: structural tests proving dataclass immutability, Protocol checkability,
  factory fails closed on invalid config, no side effects

### ENGPLAT-002A acceptance criteria

- `ProjectContext` frozen dataclass with all 8 fields defined
- `GitReadAdapter` Protocol defined (read-only; no mutation methods)
- `GovernanceAdapter` Protocol defined with `load_backlog`, `load_owners`,
  `load_operating_plan`, `load_handoff`
- `WorkflowAdapter` Protocol defined with `workflow_store`, `event_store`,
  `archive_completed`
- `QAAdapter` Protocol defined with `configured_command`, `timeout_seconds`
  (no `run_qa`)
- `FileReadAdapter` Protocol defined with `resolve`, `exists`, `read_text`
  (no `write_text`); `resolve` must raise `ValueError` on path escape
- `EventAdapter` Protocol defined with `append`, `list_events`, `pause_state`;
  `list_events` has mandatory limit
- `ProjectMetadata` frozen dataclass with all 9 fields defined
- `build_project_context()` factory contract documented: validates internally,
  fails closed, deterministic errors, no side effects
- Validation error contract documented: all 6 failure conditions with error types
- Propagation rule documented
- Option B chosen: concrete adapter construction deferred to 002B
- Structural tests: invalid `ProjectConfig` produces deterministic `ValueError`
- No concrete adapter implementations
- No `manager.py` changes
- No `Path.cwd()` or hard-coded trading-bot fallback in contract path
- Full test suite passes (no new failures introduced by type/contract definitions)

---

## ProjectContext Architectural Design

ProjectContext is a read-only runtime dependency container encapsulating everything
an engineering service needs to operate on a managed project. Engineering services
receive a ProjectContext rather than constructing their own paths or hard-coding
repository-specific filenames.

### Propagation Rule

The application entry point (manager) receives `ProjectContext`.
Downstream services receive only the narrow adapter or data dependency they require.
Do not pass the entire `ProjectContext` into every service by default.
This avoids replacing one global dependency with a `ProjectContext` "god object."

Example:
```python
# Manager receives full context
ctx = build_project_context(config)
manager.run(ctx)

# Delegated services receive only what they need
def review_workflow(governance: GovernanceAdapter, events: EventAdapter,
                    task_id: str) -> ReviewDecision:
    task = governance.load_backlog()
    evts = events.list_events(limit=100)
    ...
```

---

## Factory Contract: Option B (Deferred Construction)

**Recommendation: Option B -- define the factory contract in 002A; defer concrete
adapter construction to 002B.**

Rationale: 002A is about type definitions and contracts. Creating stub adapter
implementations in 002A that would be replaced in 002B adds technical debt.
Defining only the contract signature in 002A keeps 002A clean and 002B responsible
for actual adapter construction.

The factory contract requires:
- `build_project_context(config: ProjectConfig) -> ProjectContext`
- Validation of `ProjectConfig` via `validate_project_config()` internally
- Fail-closed on semantic errors; deterministic sanitized errors
- No side effects
- `NotImplementedError` or similar for adapter construction (since 002A does not
  implement adapters -- 002B provides concrete adapters)

---

## Protocol Definitions

All types prefixed with `models.` refer to `engineering.models`.

### GitReadAdapter -- read-only Git operations

```python
class GitReadAdapter(Protocol):
    def current_branch(self) -> str: ...
    def is_clean(self) -> bool: ...
    def repository_state(self) -> models.RepositoryState: ...
    def branch_exists(self, branch: str) -> bool: ...
    def is_ancestor(self, ancestor: str, descendant: str) -> bool: ...
```

Mutation methods (e.g., `prepare_feature_branch`) are deferred to a future
`GitMutationAdapter`. Do not include mutation methods in `GitReadAdapter`.

### GovernanceAdapter -- governance document access

```python
class GovernanceAdapter(Protocol):
    def load_backlog(self) -> tuple[models.BacklogTask, ...]: ...
    def load_owners(self) -> str: ...
    def load_operating_plan(self) -> str: ...
    def load_handoff(self) -> str: ...
```

`load_backlog()` returns `tuple[BacklogTask, ...]`. Other methods return raw
document strings. Implementations may cache documents for the adapter lifetime.

### WorkflowAdapter -- workflow and event persistence access

```python
class WorkflowAdapter(Protocol):
    def workflow_store(self) -> models.WorkflowStore: ...
    def event_store(self) -> models.EngineeringEventStore: ...
    def archive_completed(self, workflow: models.StoredWorkflow) -> Path: ...
```

Store types are already project-scoped via DI. The adapter scopes access to
`ProjectConfig.workflow_files`. Read-only in 002A.

### QAAdapter -- QA configuration access

```python
class QAAdapter(Protocol):
    def configured_command(self) -> tuple[str, ...]: ...
    def timeout_seconds(self) -> int: ...
```

`run_qa()` is deferred to 002B. Do not include in the 002A contract.

### FileReadAdapter -- project-root-bounded filesystem access

```python
class FileReadAdapter(Protocol):
    def resolve(self, relative_path: Path) -> Path: ...
    def exists(self, relative_path: Path) -> bool: ...
    def read_text(self, relative_path: Path) -> str: ...
```

`resolve()` must raise `ValueError` if the path escapes `repository_root`.
Write methods deferred to a future `FileWriteAdapter`.

### EventAdapter -- bounded event append and query

```python
class EventAdapter(Protocol):
    def append(self, event: models.EngineeringEvent) -> bool: ...
    def list_events(self, limit: int = 100) -> tuple[models.StoredEvent, ...]: ...
    def pause_state(self) -> dict[str, object]: ...
```

`list_events` has a mandatory limit. `append` accepts only `EngineeringEvent`.

---

## ProjectContext

```python
@dataclass(frozen=True)
class ProjectContext:
    config: ProjectConfig
    git: GitReadAdapter
    governance: GovernanceAdapter
    workflow: WorkflowAdapter
    qa: QAAdapter
    files: FileReadAdapter
    events: EventAdapter
    metadata: ProjectMetadata
```

All fields required. No optional adapters.

## ProjectMetadata

```python
@dataclass(frozen=True)
class ProjectMetadata:
    project_id: str
    display_name: str
    repository_root: Path
    authoritative_base_branch: str
    agents_may_merge: bool
    owner_ids: tuple[str, ...]
    agent_owners: tuple[str, ...]
    prohibited_operations: tuple[str, ...]
```

---

## Factory Contract

```python
def build_project_context(config: ProjectConfig) -> ProjectContext: ...
```

Contract requirements:
- Validates `config` internally via `validate_project_config()`
- Raises `ValueError` (deterministic, sanitized) on semantic validation failure
- Returns a `ProjectContext` with all 7 adapter fields
- No side effects: no file creation, network, Git mutation, QA, or workflow start
- Error messages contain no secrets, raw paths, or command output
- On `NotImplementedError` from adapter construction (002A only): 002B replaces
  with a concrete implementation

---

## Validation and Error Contract

| Condition | Error | Behavior |
|---|---|---|
| Invalid `ProjectConfig` | `ValueError`, sanitized | Fail immediately |
| Missing adapter factory (Option B) | `TypeError` | Fail at construction |
| Adapter factory returns wrong type | `TypeError` | Fail immediately |
| Path escape via `FileReadAdapter.resolve()` | `ValueError` | Fail immediately |
| Unsupported schema version | `ValueError` from `validate_project_config()` | Fail immediately |
| Duplicate project_id | `DuplicateProjectId` | Fail immediately |

No fallback to `Path.cwd()`, hard-coded filenames, or `TRADING_BOT_PROJECT`
inside the 002A contract path. Errors are deterministic.

---

## Architectural Rule

> **No engineering service may directly access repository-specific filesystem paths,
> filenames, or repository names except through approved platform adapters.**

The current named-file list (`manager.py`, `backlog.py`, `reporter.py`, `qa_runner.py`,
`event_store.py`, `workflow_store.py`, `query_service.py`, `git_service.py`, `config.py`)
covers existing reusable engineering services.

Future reusable engineering services -- including ENGDASH-005, ENGSUP-001, ENGDASH-006,
and ENGCTRL-001 -- must also follow the same adapter-boundary rule and are expected
to be added to the named-file list when their implementation is approved.

Adapter implementations are the approved filesystem-access boundary. Tests, bootstrap
tools, migration tools, and project-local application code are not automatically governed
by this rule; they require their own explicit scope. Ordinary trading-bot runtime code
under `src/` is not being prohibited from legitimate project-local filesystem access
by this platform rule.

---

### ENGPLAT-002B — Local Read Adapters and Manager Integration

Status: GOVERNANCE_DRAFT
Owner: trading-manager
Priority: P1

Depends on: ENGPLAT-002A

Purpose:

Implement the smallest working vertical slice needed by the manager and
unblocked by ENGDASH-005. Implement read-oriented adapters and integrate
the manager.

Execution gate:

- Non-executable until Josh approves this governance plan and opens an
  implementation PR from a feature branch.
- Do not implement GitAdapter, QAAdapter, or FileAdapter in this task.
  Those belong to 002C.
- Do not migrate reporter.py, query_service.py, git_service.py, qa_runner.py,
  config.py, or codex_cli_wrapper.py. Those belong to 002C.
- Do not implement Project Bootstrap.

---

## Design Decisions (resolved before implementation)

### D1: Single authoritative event-store path (revised)

**Finding:** `manager.py` hardcodes the event store path as
`repo_root / ".agent-state" / "engineering-events.sqlite3"`.
`TRADING_BOT_PROJECT.workflow_files.event_store_path` is set to
`engineering / "event_store.db"`. These differ.

**Verification:**
- `.agent-state/engineering-events.sqlite3` does not exist in the repository
  (confirmed: no such file; no Git history for that path).
- No historical workflow event data exists at the hardcoded path.
- No migration is required.

**Resolution (corrected):**

The legacy `main()` entry point must NOT preserve two runtime configuration
authorities. The authoritative event-store path is:

```
TRADING_BOT_PROJECT.workflow_files.event_store_path
  = <repo_root> / "engineering" / "event_store.db"
```

`manager.py` is refactored so:

- **`main()`** (legacy entry): emits one bounded `DeprecationWarning`, then
  delegates entirely to `_manager_main(TRADING_BOT_PROJECT)`. It contains no
  hardcoded paths of its own.
- **`_manager_main(config: ProjectConfig)`** (new entry): constructs
  `build_project_context(config)` and uses concrete adapters for all store access.
  Uses paths derived exclusively from `config.workflow_files`.
- No `Path.cwd()` usage in either entry point.
- No second runtime configuration authority remains in `manager.py`.
- No migration logic for `.agent-state/engineering-events.sqlite3`.

The single authoritative path is used by both the new `_manager_main` and
the `EventAdapterImpl` constructed within it.

**Rationale:** The approved architecture prohibits maintaining two runtime
configuration authorities merely to avoid a migration that is not needed.
Since no historical data exists at the hardcoded path, no migration is required.

### D2: Adapter composition vs store modification

**Decision:** Compose existing stores rather than modifying them.

- `GovernanceAdapter` wraps `backlog.load_backlog()` and `Path.read_text()`.
  No store modification needed.
- `WorkflowAdapter` wraps a `WorkflowStore` instance constructed with
  `config.workflow_files.workflow_store_path` and passes the `EngineeringEventStore`
  as the `event_store=` kwarg.
- `EventAdapter` wraps an `EngineeringEventStore` instance constructed lazily.

No `WorkflowStore` or `EngineeringEventStore` constructor is modified.

### D3: Deferred adapter fields — explicit CapabilityUnavailable

**Problem:** The draft said `build_project_context(TRADING_BOT_PROJECT)` must
"produce usable context" while `git`, `qa`, `files` raise `NotImplementedError`.
A context that fails unexpectedly on 3 of 8 fields is not "usable" — it
violates the rule: "A ProjectContext must not appear fully usable while
containing production capabilities that fail unexpectedly."

**Decision:** Adopt Option B — explicit unavailable-capability contract.

1. Add `class CapabilityUnavailable(Exception)` to `engineering/adapters.py`:

   ```python
   @dataclass(frozen=True)
   class CapabilityUnavailable(Exception):
       project_id: str     # which project this context is for
       capability: str     # one of: "git", "qa", "files"
   ```

2. In `engineering/context.py`, the deferred adapters for `git`, `qa`, `files`
   raise `CapabilityUnavailable(project_id, capability)` instead of
   `NotImplementedError`.

3. The `CapabilityUnavailable` message is deterministic and contains only
   `project_id` and `capability` — no raw paths, no secrets, no command output.

4. This preserves the 002A contract (all 8 fields present, frozen dataclass)
   while making the deferred capabilities explicit and typed.

5. Callers can catch `CapabilityUnavailable` specifically if they need to
   detect unavailable capabilities before use.

**What changes:**
- `engineering/adapters.py` gains `CapabilityUnavailable` dataclass
- `engineering/context.py` replaces `_DEFERRED_MSG` with `CapabilityUnavailable`
- `tests/test_engineering_project_context.py` updates deferred-capability tests
- The 002A contract is not changed (no new context type, no `ReadProjectContext`)

### D4: Lazy event-store construction (side-effect-free factory)

**Finding:** `EngineeringEventStore.__init__()` calls `_ensure_schema()` which:
- Creates the parent directory: `self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)`
- Creates the schema file via `CREATE TABLE` SQL
- Changes file permissions

The `build_project_context()` contract states: "No side effects: no file
creation, network, Git mutation, QA execution, or workflow state changes."

Accepting this side effect would contradict the approved factory contract.

**Decision:** `EventAdapterImpl` uses **lazy construction**.

```python
class EventAdapterImpl:
    def __init__(self, event_store_path: Path):
        self._event_store_path = event_store_path
        self._store: EngineeringEventStore | None = None   # not constructed yet

    def _get_store(self) -> EngineeringEventStore:
        if self._store is None:
            self._store = EngineeringEventStore(self._event_store_path)
        return self._store

    def append(self, event: EngineeringEvent) -> bool:
        return self._get_store().append(event)

    def list_events(self, limit: int = 100) -> tuple[StoredEvent, ...]:
        return self._get_store().list_events(limit=limit)

    def pause_state(self) -> dict[str, object]:
        return self._get_store().pause_state()
```

**Effect:**
- `build_project_context()` constructs `EventAdapterImpl` but does NOT call
  `_get_store()`, so the event database directory and schema are NOT created
  at factory time.
- The database is created only when the first event operation occurs
  (`append`, `list_events`, or `pause_state`).
- This preserves the "no side effects at construction time" contract.
- `WorkflowAdapterImpl` should NOT eagerly construct `EngineeringEventStore`
  at init time either; it should receive an `EventAdapterImpl` instance and
  call `adapter.event_store()` only when needed (lazy from the `EventAdapterImpl`
  perspective).

**Verification:** `WorkflowStore.__init__()` has no side effects — it only stores
its arguments. It is safe to construct eagerly.

**Summary:** `build_project_context()` creates no filesystem artifacts.

---

## Allowed Areas

### Required (must change)

1. **`engineering/context.py`**
   - Replace deferred `_DeferredGovernanceAdapter`, `_DeferredWorkflowAdapter`,
     `_DeferredEventAdapter` with concrete implementations
   - Add concrete `GovernanceAdapterImpl`, `WorkflowAdapterImpl`, `EventAdapterImpl`
     classes
   - Update `build_project_context()` to construct concrete adapters for
     `governance`, `workflow`, `events`; retain deferred stubs for `git`, `qa`, `files`
   - Retain all existing deferred adapter classes at module level (importable by tests)
   - **Conditional:** If the concrete classes exceed 200 lines total, split into
     `engineering/governance_adapter.py`, `engineering/workflow_adapter.py`,
     `engineering/event_adapter.py`; otherwise keep in `context.py`
   - Do NOT modify the factory signature or `ProjectContext` dataclass structure

### Required (must change)

2. **`engineering/manager.py`**
   - Add `_manager_main(config: ProjectConfig) -> int` — new ProjectContext-aware entry
   - `_manager_main` constructs `context = build_project_context(config)` and uses
     `context.governance`, `context.workflow`, `context.events` for all store access
   - Remove direct `EngineeringEventStore(...)` and `WorkflowStore(...)` construction
     from `_manager_main`
   - Keep the existing `main()` function as the backward-compat shim
   - The shim emits `warnings.warn(DeprecationWarning(...))` then calls
     `_manager_main(TRADING_BOT_PROJECT)`
   - Shallow propagation: `drive_workflow`, `dispatch_workflow`, `persist_workflow_result`
     receive only the specific adapters they need (not the full `ProjectContext`)
   - `persist_workflow_result` signature updated to accept `WorkflowAdapter` instead of
     `WorkflowStore` so it can call `adapter.archive_completed(workflow)`
   - **Do NOT change** `main()` hardcoded paths (those ARE the backward-compat path)
   - **Do NOT change** existing CLI argument parsing or task-selection logic

### Required (must add)

3. **`tests/test_engineering_project_context.py`** (update existing file)
   - Update `TestProtocolConformance` to test concrete adapters instead of deferred stubs
   - Add new `TestGovernanceAdapter`, `TestWorkflowAdapter`, `TestEventAdapter` classes
     proving real behavior (not just `isinstance`)
   - Update `TestDeferredFactoryBehavior` → `TestConcreteFactoryBehavior`
   - Keep all existing tests that verify frozen dataclasses, no `cwd` fallback,
     no trading-bot fallback, no side effects, import boundary

### Conditional (if adapter classes exceed 200 lines)

4. **`engineering/governance_adapter.py`** (new file)
   - Concrete `GovernanceAdapterImpl` class
   - Required only if concrete adapter code exceeds governance guidance

5. **`engineering/workflow_adapter.py`** (new file)
   - Concrete `WorkflowAdapterImpl` class
   - Required only if concrete adapter code exceeds governance guidance

6. **`engineering/event_adapter.py`** (new file)
   - Concrete `EventAdapterImpl` class
   - Required only if concrete adapter code exceeds governance guidance

### Governance files (authorized)

7. **`AGENT_BACKLOG.md`** — update ENGPLAT-002B governance with this plan
8. **`MENTOR.md`** — add ENGPLAT-002B as a known active governance item
9. **`ITERATION_PROGRESS_LOG.md`** — append governance planning entry
10. **`REPORT.md`** — update current report
11. **`reports/`** — archive current report

---

## Implementation Notes

### GovernanceAdapter implementation

```python
class GovernanceAdapterImpl:
    def __init__(self, governance_files: GovernanceFiles): ...
    def load_backlog(self) -> tuple[BacklogTask, ...]: ...
    def load_owners(self) -> str: ...
    def load_operating_plan(self) -> str: ...
    def load_handoff(self) -> str: ...
```

- `load_backlog()` calls `parse_backlog(path.read_text())` using
  `self._governance_files.backlog_path`
- All `load_*()` methods call `path.read_text(encoding="utf-8")` on their
  respective paths from `GovernanceFiles`
- All methods raise `ValueError` if the path does not exist (fails closed)
- No caching required for 002B (the adapter is constructed per workflow run)
- No hard-coded filenames; all paths come from `GovernanceFiles`

### WorkflowAdapter implementation

```python
class WorkflowAdapterImpl:
    def __init__(self, workflow_files: WorkflowFiles, event_store: EngineeringEventStore): ...
    def workflow_store(self) -> WorkflowStore: ...
    def event_store(self) -> EngineeringEventStore: ...
    def archive_completed(self, workflow: StoredWorkflow) -> Path: ...
```

- `WorkflowStore` constructed with `state_path=workflow_files.workflow_store_path`
  and `event_store=self._event_store`
- `event_store()` returns `self._event_store` (owned by this adapter)
- `archive_completed()` delegates to `self._workflow_store.archive_completed(workflow)`
- No new files created at construction time

### EventAdapter implementation

```python
class EventAdapterImpl:
    def __init__(self, event_store_path: Path): ...
    def append(self, event: EngineeringEvent) -> bool: ...
    def list_events(self, limit: int = 100) -> tuple[StoredEvent, ...]: ...
    def pause_state(self) -> dict[str, object]: ...
```

- `EngineeringEventStore` constructed at init time (creates parent dir + schema)
- `append()`, `list_events(limit)`, `pause_state()` delegate directly to the store
- `list_events` enforces `limit` parameter (store already validates 1-500)
- No Telegram/network coupling at the adapter level

### Manager propagation rule

Downstream functions receive only what they need:

| Function | Receives | Why |
|---|---|---|
| `load_backlog()` | Already a module-level function; no change | Pure function |
| `drive_workflow()` | `WorkflowStore` (not full context) | Only needs workflow persistence |
| `dispatch_workflow()` | `StoredWorkflow`, `GitReadAdapter` (002C) | Only reads workflow state |
| `persist_workflow_result()` | `WorkflowAdapter` | Needs `archive_completed()` |
| `select_next_task()` | `tuple[BacklogTask, ...]` | Only needs task list |

---

## Test Plan

### New behavioral tests (prove actual behavior, not just source text)

1. `test_governance_adapter_load_backlog_returns_tasks`
   - Create temp governance files matching a `ProjectConfig`
   - Construct `GovernanceAdapterImpl` from that config
   - Call `load_backlog()` and assert it returns `tuple[BacklogTask, ...]`
   - Assert task_id, title match the temp file content

2. `test_governance_adapter_load_owners_returns_string`
   - Same pattern; verify `load_owners()` returns non-empty string

3. `test_governance_adapter_path_escape_fails_closed`
   - Provide a `GovernanceFiles` with a path outside `repository_root`
   - Assert `ValueError` is raised before any file is read

4. `test_workflow_adapter_uses_configured_paths`
   - Create temp workflow paths in a `ProjectConfig`
   - Construct `WorkflowAdapterImpl` from that config
   - Assert `adapter.workflow_store().state_path` matches configured path

5. `test_event_adapter_append_and_list`
   - Create temp event store path
   - Construct `EventAdapterImpl`
   - Append a test event, list it back, assert it is returned

6. `test_event_adapter_list_limit_enforced`
   - Append 10 events, call `list_events(limit=3)`, assert exactly 3 returned

7. `test_event_adapter_pause_state_returns_dict`
   - Construct `EventAdapterImpl`, call `pause_state()`, assert dict returned

8. `test_build_project_context_produces_usable_adapters`
   - With valid `TRADING_BOT_PROJECT`, call `build_project_context()`
   - Assert `context.governance.load_backlog()` returns non-empty tuple
   - Assert `context.events.list_events(limit=10)` returns tuple
   - Assert `context.workflow.workflow_store()` returns `WorkflowStore` instance

9. `test_manager_context_entry_uses_configured_paths`
   - Call `_manager_main(TRADING_BOT_PROJECT)`
   - Verify it does not use `.agent-state/engineering-events.sqlite3`
   - Verify it uses `engineering/event_store.db` (from `TRADING_BOT_PROJECT`)

10. `test_manager_legacy_main_emits_deprecation_warning`
    - Capture `warnings` during `main([])` call
    - Assert exactly one `DeprecationWarning` is emitted
    - Assert the warning message contains the removal condition text

11. `test_no_deprecation_warning_in_workflow_loop`
    - Simulate `_manager_main` being called repeatedly
    - Assert `DeprecationWarning` is NOT emitted from adapter operations

12. `test_no_git_mutation_in_adapter_construction`
    - Mock `subprocess.run`
    - Call `build_project_context()` with a valid config
    - Assert `subprocess.run` was never called

13. `test_no_qa_execution_in_adapter_construction`
    - Mock `subprocess.run`
    - Call `build_project_context()` with a valid config
    - Assert no QA command was run

14. `test_manager_uses_project_config_paths_not_hardcoded`
    - Create a `ProjectConfig` with custom paths
    - Call `_manager_main(config)`
    - Verify adapters use the custom paths, not hardcoded defaults

### Keep existing tests (verify no regression)

- All 40 existing tests in `test_engineering_project_context.py` continue to pass
- All 426 existing tests in the full suite continue to pass
- `test_engineering_manager.py` existing tests continue to pass

---

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Event store path switch breaks in-flight workflows | Low | Medium | No historical data at old path; single authoritative path from `TRADING_BOT_PROJECT` |
| `EngineeringEventStore` schema migration | Low | High | Schema is at version 1; `engineering/event_store.db` is a new file |
| Adapter leaks raw store internals | Medium | Low | Adapters are typed Protocol wrappers; documented as known limitation for 002C |
| `DeprecationWarning` appears inside workflow loops | Low | Low | Warning emitted only in `main()` shim, not in adapter operations |
| Circular import from `context.py` importing concrete adapter modules | Medium | Medium | Keep adapter classes in `context.py`; split only if size exceeds 200 lines |
| `WorkflowStore` created with wrong `event_store` reference | Low | High | `WorkflowAdapterImpl` owns both stores; passes its own `EngineeringEventStore` as `event_store=` kwarg |
| `CapabilityUnavailable` mistaken for `NotImplementedError` | Low | Medium | Tests prove `CapabilityUnavailable` type specifically |
| Lazy initialization thread-safety | Low | Low | `EngineeringEventStore` constructed once per adapter instance |
| Old event-store path retained as dead code | Low | Low | Governance prohibits two runtime authorities; no data at old path |

---

## Non-Blocking Findings from ENGPLAT-002A Audit (addressed in 002B)

1. `TYPE_CHECKING` annotation exposure in `adapters.py` — `EngineeringEventStore`
   and `WorkflowStore` imported only under `TYPE_CHECKING`. 002B concrete adapters
   are defined in `context.py` which imports these types at module level, resolving
   the annotation gap. No action needed in `adapters.py`.

---

## Backward-Compatibility Shim (Formal Contract)

The shim is the existing `main()` function in `manager.py`. It:

1. Uses `TRADING_BOT_PROJECT` constant to feed the adapter factory
2. Emits exactly one `DeprecationWarning` with text:
   `"Manager is using a legacy single-project entry path. "
   "This path is deprecated and will be removed after ENGPLAT-002C is complete "
   "AND a second managed project has passed integration testing. "
   "Do not rely on this path in new code."`
3. Calls `_manager_main(TRADING_BOT_PROJECT)` which uses concrete adapters
4. Does NOT create a second `ProjectConfig` instance
5. Does NOT bypass `ProjectContext`
6. Does NOT emit repeated warnings inside workflow loops

---

### ENGPLAT-002B acceptance criteria

- [ ] `CapabilityUnavailable` dataclass added to `engineering/adapters.py`
- [ ] Deferred adapters (`git`, `qa`, `files`) raise `CapabilityUnavailable` (not `NotImplementedError`)
- [ ] `EventAdapterImpl` uses lazy construction (`_store` initialized on first method call)
- [ ] `build_project_context()` creates zero filesystem artifacts at factory time
- [ ] GovernanceAdapter, WorkflowAdapter, EventAdapter implemented and passing tests
- [ ] `manager.py` refactored — `_manager_main(config)` uses `ProjectContext`; `main()` is a clean deprecation shim
- [ ] No hardcoded `.agent-state/engineering-events.sqlite3` path in any manager entry function
- [ ] `build_project_context(TRADING_BOT_PROJECT)` produces a context where
      `context.governance.load_backlog()` returns non-empty task tuple
- [ ] `context.events.list_events(limit=N)` correctly limits results
- [ ] `context.workflow.archive_completed()` delegates to `WorkflowStore.archive_completed()`
- [ ] Backward-compat shim (`main()`) emits exactly one `DeprecationWarning`
- [ ] No `DeprecationWarning` emitted from adapter operations inside workflow loops
- [ ] `context.git`, `context.qa`, `context.files` raise `CapabilityUnavailable` with correct `project_id` and `capability` fields
- [ ] All 22 new tests pass
- [ ] No `git_service.py`, `qa_runner.py`, `reporter.py`, `query_service.py`,
      `config.py`, or `codex_cli_wrapper.py` migration
- [ ] Full test suite passes without modifying existing test assertions
- [ ] ENGDASH-005 can begin after 002B (it needs only stable read interfaces)

### ENGPLAT-002C — Remaining Adapters and Service Migration

Status: TODO
Owner: trading-manager
Priority: P1

Depends on: ENGPLAT-002B

Purpose:

Complete the adapter layer and migrate remaining reusable engineering services.

Candidate scope (requires separate Josh approval per slice):

- GitAdapter implementation wrapping `GitService`
- QAAdapter implementation wrapping QA execution
- FileAdapter implementation wrapping safe filesystem access
- reporter.py migration: use `GovernanceAdapter` for risks/action; use
  `WorkflowAdapter.archive_completed()` for report output
- query_service.py migration: wire to `ProjectContext`
- git_service.py integration: wrap in `GitAdapterImpl`
- qa_runner.py migration: use `QAAdapter.timeout_seconds()` from config
- config.py migration: validate against `governance_files` from `ProjectConfig`
- codex_cli_wrapper.py migration: accept `runtime_root` from config or env override
- backlog.py: no direct migration unless required by approved adapter implementation
  (already covered by GovernanceAdapter in 002B)
- Remove remaining hard-coded repository-specific assumptions

Requirements:

- Each migration remains bounded. ENGPLAT-002C may require multiple
  implementation slices, each with separate narrow allowed areas and Josh approval.
- Do not grant broad `engineering/` directory write access.
- Backward-compatibility shim removed after 002C is complete AND a second managed
  project has passed integration testing.
- No broad rewrite or repository extraction.

---

### ENGPLAT-003 — Project Bootstrap

Status: TODO
Owner: trading-manager
Priority: P1

Depends on: ENGPLAT-002C

Purpose:

Enable the engineering platform to create a new managed project from a template
rather than only managing an existing project with a pre-existing `ProjectConfig`.

The platform currently manages repositories that already have governance files,
backlog, and engineering infrastructure. Bootstrap closes that gap by generating
the initial project structure for a brand-new repository.

### Bootstrap scope

- Generate a `ProjectConfig` for a new repository (interactive prompts or
  template-driven configuration)
- Create governance files from templates: `AGENT_BACKLOG.md`, `OWNERS.md`,
  `AGENT_OPERATING_PLAN.md`, handoff document
- Create engineering defaults: `.agent-state/` directory structure, initial
  `.gitignore` entries for `.agent-state/`, `reports/`
- Create initial QA configuration: `pyproject.toml` or `pytest.ini` defaults
- Register the new project in a `ProjectRegistry` or add to the existing
  registry file
- Validate generated `ProjectConfig` via `parse_project_config()` and
  `validate_project_config()`

### Bootstrap safety constraints

Bootstrap is non-executable until Josh separately approves this task.

Safety constraints that must appear as explicit acceptance criteria:

- **Dry-run must be explicit, not inferred**: bootstrap must support a dry-run
  mode that shows what would be created without writing any files.
- **Validation alone does not guarantee files will not be overwritten**:
  the tool must check for existing files and halt with explicit human approval
  before overwriting any existing file, even if the file would pass validation.
- **No existing file may be replaced without explicit human approval**:
  this is a separate acceptance criterion, not implied by validation.
- **No secrets or credentials are generated**: bootstrap generates configuration
  and governance structure only; it does not create or request API keys,
  tokens, passwords, or any secrets.
- **No arbitrary shell commands are executed**: bootstrap performs only
  structured file creation from templates and registry updates.
- **Requires separate governance remediation and Josh approval** before
  implementation begins.

### ENGPLAT-003 is separated from ENGPLAT-002A/002B because

1. 002A/002B are about adapting existing services to consume ProjectConfig
2. 003 is about generating new `ProjectConfig` instances from templates
3. 003 depends on the full adapter contract being stable (after 002C)
4. They have different allowed-file sets, different safety considerations,
   and different review boundaries
5. Keeping them as separate roadmap items provides cleaner approval gates

Execution gate:

- Non-executable until ENGPLAT-002C is complete and Josh approves a separate
  narrow implementation plan for this task.

### ENGPLAT-003 acceptance criteria

- Generates a valid `ProjectConfig` that passes `parse_project_config()` and
  `validate_project_config()`
- Creates governance files from templates without overwriting existing files
- Registers project in `ProjectRegistry`
- Supports dry-run mode
- Halts with explicit approval required before overwriting any existing file
- Emits no secrets; performs no arbitrary shell execution
- Passes integration tests

### ENGDASH-005 — Engineering Timeline and Historical Activity

Status: TODO
Owner: dashboard-agent
Priority: P1

Depends on: ENGPLAT-001, ENGPLAT-002B, ENGDASH-004

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

Depends on: ENGPLAT-001, ENGPLAT-002C

Phase: 1 of 3

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

**Entry criteria**: ENGPLAT-001 and ENGPLAT-002 complete and stable; Phase 1b REPORT.md reading rules defined.
#### Phase 1b — Supervisor REPORT.md Reading Rules

**Goal**: Allow the supervisor to read REPORT.md as a bounded supporting source,
without treating it as authoritative or allowing it to override directly verified state.

##### Source Priority

Authoritative sources, in order of precedence:
1. Directly verified repository and PR state
2. Structured completion packet
3. Structured test/evidence records
4. REPORT.md and timestamped `reports/` as supporting narrative only

**REPORT.md must never override directly verified state.**

##### REPORT.md Usage

The supervisor may extract from REPORT.md:
- Executive summary
- Implementation rationale
- Known risks
- Compatibility concerns
- Manual verification recommendations
- Unresolved questions
- Reviewer findings
- Proposed next action

##### Verification Requirements

Any factual claim from REPORT.md concerning the following must be independently
verified before use in a generated prompt:
- Branch
- Commit
- Working-tree state
- Tests
- PR state
- Mergeability
- Task status
- Approval state

##### Staleness Handling

If REPORT.md conflicts with verified state:
1. Mark the report as stale
2. Identify the conflicting fields
3. Use verified state
4. Do not fail the entire supervisor run unless the conflict affects safety or scope

##### Bounded Reading

- Read only a bounded maximum size (configurable; default 64 KB)
- Prefer the executive summary and relevant sections
- Do not ingest unbounded logs
- Sanitize excerpts
- Never expose secrets or raw credentials
- Record the report path, modification time, and content hash used

##### Multiple Reports

If both REPORT.md and timestamped `reports/` archives exist:
- REPORT.md = latest rolling narrative
- Timestamped reports = immutable historical evidence
- Prefer the archive matching the current task/run
- Do not combine reports from different runs without explicit task/run matching

##### Prompt Generation

The generated prompt may cite report-derived risks or rationale, but must clearly
distinguish:
- Verified facts (from direct verification)
- Report claims (from REPORT.md)
- Supervisor inference
- Recommendations

##### Planned Tests

`tests/test_engineering_supervisor_report_reading.py`

1. **REPORT.md matches verified state** — current REPORT.md branch/commit matches Git state → no staleness flag
2. **Stale REPORT.md** — REPORT.md claims HEAD=abc but Git shows xyz → staleness flagged, verified state used
3. **Oversized report truncation** — REPORT.md > 64 KB → truncated at bound, excerpt flag set
4. **Secret-like content redaction** — content matches secret patterns → redacted in supervisor output
5. **Wrong-task report ignored** — report is for task X, supervisor evaluating task Y → task mismatch flagged
6. **Timestamped archive preferred** — both REPORT.md and matching timestamped archive exist → archive used
7. **Report unavailable** — no REPORT.md and no matching archive → supervisor proceeds without it; no blocking
8. **Report claims verified independently** — report claims tests=426, supervisor re-runs → verified
9. **Report claims conflict with verified** — report claims merged, verified shows open → staleness + verified used
10. **Prompt distinguishes sources** — generated prompt labels each fact as verified / report-claim / inference

---

##### EvidenceBundle Architecture

All supervisor decisions operate on a single verified `EvidenceBundle` assembled from
authoritative evidence sources. Future supervisor components consume only the
EvidenceBundle rather than opening files independently.

**Evidence priority order** (highest to lowest):

| Priority | Source | Type |
|---|---|---|
| 1 | Repository verification | Directly verified Git state |
| 2 | Structured completion packet | `CompletionPacket` from event store |
| 3 | Structured QA/test evidence | Typed test results |
| 4 | REPORT.md | Rolling narrative (latest) |
| 5 | Matching timestamped implementation report | `reports/YYYYMMDD_HHMMSS_<task>.md` |
| 6 | Matching audit/review report | `reports/YYYYMMDD_HHMMSS_<review>.md` |
| 7 | PR metadata | GitHub API state |

**Evidence rules**:

- Supervisor independently verifies evidence at priorities 1, 2, 3, and 7.
- REPORT.md (priority 4) is informative; never authoritative by itself.
- Completion packets (priority 2) are never blindly trusted.
- Repository state (priority 1) always overrides reported state.
- Missing evidence produces deterministic bounded recommendations; does not block.
- Stale evidence must be detected and flagged.
- Conflicting evidence must produce explicit review findings, not assumptions.

**EvidenceBundle lifecycle**:

```
Collect → Verify → Normalize → Build EvidenceBundle → Decision Engine → Prompt Generator → Prompt Validation
```

1. **Collect**: Gather evidence from all sources (event store, filesystem, Git, GitHub API)
2. **Verify**: Independently verify repository state, commit, tests, PR status
3. **Normalize**: Convert heterogeneous evidence to typed structures
4. **Build EvidenceBundle**: Assemble verified evidence with source labels and timestamps
5. **Decision Engine**: Apply supervisor rules to EvidenceBundle → `SupervisorDecision`
6. **Prompt Generator**: Produce bounded instruction from `SupervisorDecision` + EvidenceBundle
7. **Prompt Validation**: Verify prompt does not exceed bounds, contains no secrets, preserves gates

**Benefits of EvidenceBundle approach**:
- Reduces duplicated parsing across supervisor components
- Enables evidence-level caching (verified state cached; re-verified on conflict)
- Supports dashboard evidence visualization (show sources per fact)
- Enables multi-project supervisor (one EvidenceBundle per project)
- Supports audit trail (EvidenceBundle serialized to event store)

##### Decision Pipeline and Recommendation Architecture

**Pipeline flow**:

```
ProjectContext → EvidenceBundle → Decision Engine → Recommendation Generator → Prompt Generator → Human
```

The Decision Engine consumes only an `EvidenceBundle`. It never reads repository
files directly. It never trusts completion packets without independent verification.
It produces deterministic, reproducible output: identical evidence always produces
identical decisions.

**DecisionResult contract**:

```python
@dataclass(frozen=True)
class DecisionResult:
    decision_type: DecisionType          # e.g., CONTINUE, ESCALATE, WAIT_FOR_APPROVAL
    severity: Severity                   # e.g., BLOCKING, WARNING, INFO
    confidence: Confidence               # VERIFIED, HIGH, MEDIUM, LOW, UNKNOWN
    evidence_used: tuple[str, ...]       # evidence IDs or source labels used
    missing_evidence: tuple[str, ...]    # evidence types that were unavailable
    blockers: tuple[str, ...]           # blocking issues requiring resolution
    warnings: tuple[str, ...]            # non-blocking concerns
    human_action_required: bool          # True if Josh must act before proceeding
    recommended_next_step: str           # exactly one bounded next action
    bounded_prompt: str                  # instruction for the next agent (bounded, no secrets)
    explanation_summary: str             # ≤ 5 sentences; no raw chain-of-thought
    evidence_conflicts: tuple[EvidenceConflict, ...]  # any conflicts detected
    stale_evidence: tuple[str, ...]     # evidence IDs flagged as stale
```

**Recommendation priority order** (highest to lowest authority):

1. **Repository truth** — directly verified Git/GitHub state
2. **Verification failures** — conflicts between reported and verified state
3. **Blocking engineering defects** — test failures, broken builds, dirty trees
4. **Required human approvals** — merge requests, scope changes, safety-sensitive decisions
5. **Warnings** — non-blocking concerns, degraded confidence
6. **Optimization suggestions** — low-risk improvements, style, cleanup

**Conflict resolution rules**:

| Conflict | Authoritative Source | Supervisor Action | Recommendation |
|---|---|---|---|
| REPORT claims clean; Git shows dirty | Git | Flag mismatch; use git | BLOCKING: dirty tree |
| QA reports pass; completion packet says fail | QA results | Flag mismatch | BLOCKING: QA evidence conflict |
| Completion packet says merged; GitHub API shows open | GitHub API | Flag mismatch | BLOCKING: PR state conflict |
| PR metadata stale (HEAD mismatch) | Git HEAD | Flag staleness | ESCALATE: PR metadata stale |
| Audit report missing | N/A | Log missing | WARNING: no audit evidence |
| Regression results unavailable | N/A | Flag missing | WAIT: regression evidence required |
| Workflow state unknown | Git log | Flag unknown | ESCALATE: unclear workflow state |
| REPORT.md stale | Event store timestamp | Flag stale | Use verified; ignore report claim |

**Confidence scoring**:

| Level | Definition | Required Evidence |
|---|---|---|
| VERIFIED | Independently confirmed by ≥2 sources | Git + GitHub API agree |
| HIGH | Single authoritative source confirmed | Git state verified |
| MEDIUM | Corroborated but not independently verified | Completion packet + partial corroboration |
| LOW | Single uncorroborated source | Report only |
| UNKNOWN | No evidence available | None |

Confidence depends only on available evidence. The supervisor never infers
confidence heuristically. If no evidence exists for a claim, confidence is UNKNOWN.

**Bounded recommendation rules**:

Every supervisor recommendation must:
- Stay inside the approved scope for the current phase
- Identify exactly one next action
- Never expand scope or propose unapproved work
- Preserve human approval gates (never bypass Josh authorization)
- Include explicit stop criteria
- List missing evidence when evidence is unavailable

**Shared output model**:

All interfaces (dashboard, Telegram, CLI, future integrations) consume the same
`DecisionResult` rather than generating independent recommendations.

```
Dashboard: displays DecisionResult fields; shows evidence sources per claim
Telegram: renders DecisionResult as formatted message; links to evidence
CLI: outputs DecisionResult as structured JSON or table
API: serializes DecisionResult; includes evidence bundle reference
Future: same DecisionResult; no independent recommendation generation
```

This guarantees that dashboard, Telegram, CLI, and future interfaces all show
the same decision and evidence. The supervisor is the single source of
recommendation truth.

##### Engineering Workflow State Machine

The canonical workflow state machine governs every engineering task. All platform
components derive state from the same immutable event history.

**Workflow states** (in order):

```
TODO
↓
PLANNING
↓
APPROVED_FOR_IMPLEMENTATION
↓
IMPLEMENTING
↓
IMPLEMENTATION_COMPLETE
↓
QA_RUNNING
↓
QA_FAILED | QA_PASSED
↓
READ_ONLY_REVIEW
↓
CHANGES_REQUESTED | APPROVED_FOR_MERGE
↓
MERGED
↓
ARCHIVED
```

**State definitions and required evidence**:

| State | Entry Criteria | Exit Criteria | Required EvidenceBundle | DecisionResult |
|---|---|---|---|---|
| `TODO` | Task in backlog | Manager begins work | Backlog entry, ProjectContext | None |
| `PLANNING` | Manager drafts plan | Josh approves plan | Plan draft, ProjectContext | `WAIT_FOR_APPROVAL` |
| `APPROVED_FOR_IMPLEMENTATION` | Josh approves plan | Agent begins work | Approved plan, scope | `CONTINUE` |
| `IMPLEMENTING` | Agent starts work | Implementation complete | Allowed-areas diff, git state | None |
| `IMPLEMENTATION_COMPLETE` | Diff complete, tests pass locally | QA begins | Full diff, test results | `CONTINUE` |
| `QA_RUNNING` | QA invoked | QA result available | QA execution record | None |
| `QA_FAILED` | QA exit ≠ 0 | Agent fixes or scopes back | QA failure evidence | `WAIT_FOR_APPROVAL` |
| `QA_PASSED` | QA exit = 0 | Review begins | QA pass evidence | `CONTINUE` |
| `READ_ONLY_REVIEW` | Manager opens PR | Review complete | PR metadata, diff | `WAIT_FOR_APPROVAL` |
| `CHANGES_REQUESTED` | Reviewer requests changes | Agent addresses | Review comments | `CONTINUE` (→ IMPLEMENTING) |
| `APPROVED_FOR_MERGE` | Josh approves PR | Merge begins | Approval evidence | `CONTINUE` |
| `MERGED` | Merge complete | Archive initiated | Merge commit, event | `CONTINUE` |
| `ARCHIVED` | Workflow archived | Terminal | Archive path | None |

**Transition rules**:

| From | To | Allowed? | Gate | Trigger |
|---|---|---|---|---|
| TODO | PLANNING | Yes | None | Manager picks task |
| PLANNING | APPROVED_FOR_IMPLEMENTATION | Yes | Josh approval | `APPROVED_FOR_MERGE` DecisionResult |
| PLANNING | TODO | Yes | None | Josh rejects plan |
| APPROVED_FOR_IMPLEMENTATION | IMPLEMENTING | Yes | None | Agent starts work |
| IMPLEMENTING | IMPLEMENTATION_COMPLETE | Yes | None | All changes complete |
| IMPLEMENTATION_COMPLETE | QA_RUNNING | Yes | None | QA invoked |
| QA_RUNNING | QA_PASSED | Yes | None | QA exit 0 |
| QA_RUNNING | QA_FAILED | Yes | None | QA exit ≠ 0 |
| QA_FAILED | IMPLEMENTING | Yes | None | Agent begins fixes |
| QA_PASSED | READ_ONLY_REVIEW | Yes | None | PR opened |
| READ_ONLY_REVIEW | APPROVED_FOR_MERGE | Yes | Josh approval | `APPROVED_FOR_MERGE` |
| READ_ONLY_REVIEW | CHANGES_REQUESTED | Yes | None | Reviewer requests changes |
| CHANGES_REQUESTED | IMPLEMENTING | Yes | None | Agent begins fixes |
| CHANGES_REQUESTED | READ_ONLY_REVIEW | Yes | None | Agent re-submits |
| APPROVED_FOR_MERGE | MERGED | Yes | None (future: auto or Josh) | Merge committed |
| MERGED | ARCHIVED | Yes | None | Archive initiated |
| Any | ARCHIVED | No | — | Invalid: no resurrection |
| QA_PASSED | IMPLEMENTING | No | — | Invalid: cannot regress |
| CHANGES_REQUESTED | APPROVED_FOR_MERGE | No | — | Invalid: must re-review |

**Workflow invariants** (enforced):

- Exactly one active state per workflow at any time
- Every transition is recorded in the event store with full audit fields
- Every transition is attributable to an actor (manager, supervisor, Josh)
- No approval gate may be skipped
- No transition occurs without a corresponding DecisionResult (for state changes)
- No hidden or undocumented transitions exist
- All transitions are deterministic: same EvidenceBundle + state → same next state
- `ARCHIVED` is terminal: no transitions out
- `TODO` is the only valid initial state

**Workflow event model**:

Each transition records:

```python
@dataclass(frozen=True)
class WorkflowTransitionEvent:
    event_id: str
    workflow_id: str
    previous_state: WorkflowState
    new_state: WorkflowState
    timestamp: datetime
    actor: str                    # manager | supervisor | josh | system
    trigger: str                  # e.g., "qa_passed", "josh_approved"
    evidence_bundle_id: str        # reference to EvidenceBundle used
    decision_result_id: str        # reference to DecisionResult (if applicable)
    notes: str                     # human-readable note (bounded, no secrets)
    allowed_areas_changed: bool    # True if scope expanded during this transition
```

**Canonical state derivation**:

Manager, Supervisor, Dashboard, Telegram, and CLI all derive current workflow state
from the same immutable event history. No component maintains its own state copy.

```
EventStore.query(workflow_id) → ordered transitions → current_state
```

This guarantees that all interfaces show identical state because they read
the same event history.

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

`tests/test_engineering_supervisor_evidence_bundle.py`

15. **EvidenceBundle: priority-1 overrides priority-4** — REPORT.md claims clean; Git shows dirty → bundle marks git_verified=True, report_stale=True
16. **EvidenceBundle: stale detection** — REPORT.md timestamp older than event store packet → stale flag set
17. **EvidenceBundle: conflicting evidence** — packet says merged, GitHub API shows open → explicit conflict finding, no assumption
18. **EvidenceBundle: missing non-critical evidence** — REPORT.md unavailable → supervisor proceeds with available evidence
19. **EvidenceBundle: missing critical evidence** — repository verification unavailable → supervisor escalates, does not proceed
20. **EvidenceBundle: priority order enforced** — priority-1 state contradicts priority-4 → priority-1 used, priority-4 flagged
21. **EvidenceBundle: lifecycle — Collect** — all sources gathered without blocking
22. **EvidenceBundle: lifecycle — Verify** — git/gitHub verified independently; no blind trust
23. **EvidenceBundle: lifecycle — Normalize** — heterogeneous evidence converted to typed structures
24. **EvidenceBundle: lifecycle — Build** — bundle includes source labels, timestamps, content hashes
25. **EvidenceBundle: lifecycle — Prompt Validation** — prompt within bounds, no secrets, gates preserved
26. **EvidenceBundle: bounded reading** — REPORT.md > 64 KB → truncated at bound
27. **EvidenceBundle: secret redaction** — content matches secret patterns → redacted in supervisor output
28. **EvidenceBundle: wrong-task report ignored** — report for task X, supervisor evaluating Y → task mismatch flagged

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

### ENGPLAT-004 — Extract Reusable Engineering Platform Repository

Status: TODO
Owner: trading-manager
Priority: P3

Depends on: ENGPLAT-002C, ENGPLAT-003, ENGDASH-005, ENGSUP-001 Phase 1

Purpose:

Extract reusable engineering-platform code into a separate repository after
configuration, adapter boundaries, supervisor, and timeline services have been
proven through normal use.

Execution gate:

- Explicitly deferred. Non-executable.
- Extraction must not start until all preceding tasks are stable through normal
  project use and Josh separately approves cross-repository planning.
- Project-local governance remains in each managed repository.
- Cross-repository versioning, deployment, authentication, and migration require
  separate planning and human approval.

---

## Extraction Readiness Criteria

Extraction (ENGPLAT-004) must NOT begin until ALL of the following are true:

| # | Criterion | Evidence Required |
|---|---|---|
| 1 | All reusable engineering services consume `ProjectConfig` via approved adapters | No service in `engineering/` imports `Path.cwd()` or constructs repository paths from string literals; test proves adapter injection works without hard-coded fallback |
| 2 | Adapter protocol signatures have remained unchanged through at least three completed engineering workflows | Three workflow runs logged with adapter signatures unchanged between runs; any breaking change resets the evidence window |
| 3 | `TRADING_BOT_PROJECT` is the only instantiation; a second non-trading-bot managed project exists in testing | `ProjectRegistry` contains ≥2 entries; one has `project_id` not equal to `trading-bot`; both pass `validate_project_config()` |
| 4 | Governance files are loaded through `GovernanceFiles` adapter | Test proves `load_backlog()` works with adapter; no hard-coded `AGENT_BACKLOG.md` string in service code |
| 5 | Workflow persistence uses `WorkflowFiles` paths via adapter | Test proves `WorkflowStore` uses path from `workflow_files.workflow_store_path`; no hard-coded `.git/engineering-workflow.json` in service code |
| 6 | Event store uses `WorkflowFiles.event_store_path` via adapter | Test proves event store construction uses `workflow_files.event_store_path`; no hard-coded `.agent-state/engineering-events.sqlite3` in service code |
| 7 | Report output uses `WorkflowFiles.report_dir` via adapter | Test proves report output goes to `report_dir`; no hard-coded `reports/` string in reporter service |
| 8 | QA commands + timeout come from `ProjectConfig` via adapter | Test proves QA timeout comes from config; no `QA_TIMEOUT_SECONDS = 300` constant in `qa_runner.py` |
| 9 | Cross-project tests demonstrate adapter isolation | Adapter tests run against both managed projects without modification; no project-specific branching in adapters |
| 10 | Supervisor Phase 1 acceptance criteria are complete and in production use | All acceptance criteria in ENGSUP-001 Phase 1 are marked DONE; supervisor has produced bounded next-step recommendations for ≥3 completed workflows with human gates preserved |
| 11 | Engineering timeline (ENGDASH-005) acceptance criteria are complete | All acceptance criteria in ENGDASH-005 are marked DONE; timeline displays structured task, QA, review, approval, PR, and merge events from ≥3 completed workflows |
| 12 | Adapter stability through actual usage | All 6 adapter protocol signatures unchanged across ≥3 completed workflows involving normal project operations |
| 13 | Versioning and deployment plan exists | Documented plan for cross-repository versioning, published package strategy, and deployment approach; approved by Josh |
| 14 | Authentication and security plan exists | Documented plan for cross-repository authentication, secret management, and access control; approved by Josh |
| 15 | Migration and rollback plan exists | Documented plan for migrating existing trading-bot managed-project setup to extracted platform; includes rollback procedure; approved by Josh |
| 16 | Josh separately approves cross-repository planning | Explicit governance action approving ENGPLAT-004 start |

**Statement of correctness:**

> "The engineering platform should not be extracted into its own repository until
> all core engineering services consume `ProjectContext` rather than repository-specific
> assumptions."

This statement was correct when ENGPLAT-001 was the only done task. It remains
correct and is now more precisely stated: extraction requires full adapter
consumption (ENGPLAT-002A through 002C), a second project (ENGPLAT-003),
supervisor Phase 1 (ENGSUP-001 Phase 1), and timeline (ENGDASH-005) — not just
the contract.

Acceptance criteria:

- Extraction occurs only after all 16 readiness criteria above are satisfied.
- At least one non-trading-bot project is successfully managed by the platform.
- All 6 adapter protocol signatures are stable through ≥3 normal workflow runs.
- Versioning, authentication, and migration plans are documented and approved.
- Project-local governance remains in each managed repository.
- Cross-repository versioning, deployment, authentication, and migration each
  require separate planning and human approval.

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
