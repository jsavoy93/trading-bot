Trading Bot Autonomous Engineering Manager — 
Handoff Guide Purpose This document gives a new AI 
coding agent enough context to continue development 
of the autonomous engineering manager without 
having to reconstruct the project history. The 
broader project is a trading bot repository, but 
the work currently in progress is the engineering 
automation layer that will eventually plan, 
delegate, test, review, and report on code changes 
made by coding agents. The design goal is not to 
create a swarm of uncontrolled autonomous agents. 
The goal is to create a deterministic engineering 
manager that controls the workflow, persists state, 
applies safety checks, delegates narrowly scoped 
work, and requires evidence before accepting 
changes. ──────── Repository Repository path: 
```text ~/.openclaw/workspace/trading-bot ``` 
Current working branch: ```text 
agent/ops-autonomous-workflow-v1 ``` Important 
rule: ```text Do not make live trades. Do not 
weaken brokerage safety checks. Do not modify 
secrets or .env files. Use small, tested commits. 
Avoid giant rewrites. ``` Before making changes, 
read: ```text MENTOR.md ``` The existing MENTOR.md 
contains the trading bot code map, known bugs, 
health checks, and the current autonomous workflow 
architecture. ──────── Product Vision The 
engineering manager should eventually execute this 
lifecycle: ```text DISCOVER
  ↓ PLAN ↓ PREPARE_BRANCH ↓ DELEGATE ↓ 
WAIT_FOR_AGENT
  ↓ QA ↓ REVIEW ↓ REPORT ↓ COMPLETE ``` The manager 
remains deterministic. AI agents may be used later 
for implementation or review, but AI must not 
control orchestration, workflow persistence, safety 
gates, or final acceptance logic. The manager 
should behave like an engineering lead: 1. Select 
the next valid backlog task. 2. Understand the task 
and produce a concrete execution plan. 3. Validate 
repository state. 4. Create or resume a safe 
feature branch. 5. Delegate the implementation with 
a tightly scoped prompt. 6. Persist the delegated 
run and resume after interruptions. 7. Run 
automated validation. 8. Compare evidence against 
acceptance criteria. 9. Produce a human-readable 
report. 10. Mark the workflow complete and return 
to idle. ──────── Current Architecture The intended 
architecture is: ```text engineering/manager.py
        │ ▼ load or create StoredWorkflow │ ▼ 
engineering/workflow_engine.py
        │ ▼ engineering/workflow/<state>.py │ ▼ 
return a new immutable StoredWorkflow
        │ ▼ WorkflowStore.save() ``` 
Responsibilities are intentionally separated. 
engineering/manager.py Responsibilities: • Load an 
existing persisted workflow or create a new one. • 
Dispatch exactly one workflow state per manager 
run. • Save the returned workflow. • Print useful 
status information. The manager should orchestrate 
only. It should not contain state-specific business 
logic. engineering/workflow_engine.py 
Responsibilities: • Map each WorkflowState to the 
correct handler. • Call the handler. • Return the 
resulting workflow. The dispatcher should not 
contain planning, Git, QA, or reporting logic. 
engineering/workflow/<state>.py Each state should 
live in its own independently testable module. 
Expected handler contract: ```python def 
run(workflow: StoredWorkflow) -> StoredWorkflow:
    # Validate preconditions. Perform this state's 
    # work. Decide the next state. Return a new 
    # workflow.
``` A state handler must not directly invoke the 
next state handler. engineering/workflow_store.py 
Responsibilities: • Persist the active workflow. • 
Load it safely. • Reject malformed or unknown 
states. • Save atomically. • Clear completed 
workflow state. ──────── Important Model Behavior 
StoredWorkflow is a frozen dataclass. It must not 
be mutated directly. Incorrect: ```python 
workflow.state = WorkflowState.PLAN ``` Correct: 
```python from dataclasses import replace return 
replace(
    workflow, state=WorkflowState.PLAN, ) ``` 
Immutable transitions are deliberate. They make 
workflow changes explicit and reduce accidental 
state corruption. ──────── Current Models At the 
current checkpoint, engineering/models.py includes: 
• TaskStatus • WorkflowState • Priority • 
BacklogTask • RepositoryState • ExecutionPlan 
Current WorkflowState values: ```text DISCOVER PLAN 
PREPARE_BRANCH DELEGATE WAIT_FOR_AGENT QA REVIEW 
REPORT COMPLETE ``` Current ExecutionPlan fields: 
```python @dataclass(frozen=True) class 
ExecutionPlan:
    task: BacklogTask repository: RepositoryState 
    feature_branch: str workflow_state: 
    WorkflowState = WorkflowState.DISCOVER
``` This model is still minimal and will likely 
need to grow during the PLAN milestone. ──────── 
Current Planning Infrastructure 
engineering/planner.py already contains: • 
select_next_task() • priority ordering • 
build_feature_branch() • build_execution_plan() The 
planner currently: 1. Selects the highest-priority 
available task. 2. Uses backlog order as the 
tie-breaker. 3. Builds a safe feature branch slug. 
4. Creates the minimal ExecutionPlan. This work is 
deterministic and should remain deterministic. 
──────── Current Workflow Persistence 
engineering/workflow_store.py currently persists: 
```text task_id feature_branch state ``` The stored 
workflow intentionally contains only compact 
workflow identity and state. The richer plan is not 
yet persisted. A future design decision will be 
required: Option A: Rebuild planning context when 
needed The PLAN state reloads the backlog task and 
repository state from source files. Advantages: • 
Small workflow JSON. • Less migration complexity. • 
Source-of-truth remains backlog and repository. 
Risks: • Backlog changes during an active workflow 
could change the reconstructed plan. Option B: 
Persist generated planning artifacts Persist 
acceptance criteria, allowed areas, risk, 
complexity, and other plan fields. Advantages: • 
Fully resumable historical context. • Stable plan 
even if backlog changes. Risks: • More 
schema/versioning complexity. Do not refactor 
persistence casually. Make this decision explicitly 
and add migration-safe tests. ──────── Completed 
Milestones 1. Engineering foundation Implemented 
before the current continuation: • Engineering 
manager scaffolding. • Backlog selection. • 
Repository validation. • Git service. • Initial 
workflow models. • Workflow persistence. • 
Startup/resume behavior. 2. Workflow dispatcher 
Added: ```text engineering/workflow_engine.py ``` 
The dispatcher supports every workflow state. 
Initially, most states use a placeholder handler 
that prints the state and returns the unchanged 
workflow. 3. State package Added: ```text 
engineering/workflow/ 
engineering/workflow/__init__.py 
engineering/workflow/discover.py ``` 4. Manager 
dispatch integration The manager now: • loads or 
creates the workflow, • dispatches the current 
state, • saves the returned workflow. 5. First real 
state transition engineering/workflow/discover.py 
now performs: ```text DISCOVER → PLAN ``` It uses 
dataclasses.replace() because StoredWorkflow is 
immutable. 6. Tests Added focused tests for: • 
Workflow dispatcher routing. • DISCOVER state 
behavior. • Workflow store. • Planner. • Git 
service. • Repository configuration. • Brokerage 
safety. At the most recent verified checkpoint: 
```text 67 tests passed ``` The suite also emitted 
existing warnings related to: • unknown pytest 
timeout config, • deprecated websockets.legacy, • 
deprecated datetime.utcnow() usage. These warnings 
did not block the workflow milestone. 7. 
Documentation MENTOR.md was updated with: • 
workflow architecture, • state sequence, • 
immutable transition contract, • current DISCOVER → 
PLAN implementation, • testing separation. ──────── 
Recent Commits Relevant recent commits: ```text 
36fb4c9 Add workflow state dispatcher 6a2bc69 
Extract discover workflow handler 1b871e3 Dispatch 
persisted engineering workflows 3cfe9ea Advance 
discover workflow to planning cbc7825 Document 
autonomous engineering workflow ``` Verify current 
history before relying on these hashes: ```bash git 
log --oneline -10 ``` ──────── Test Safety 
Requirements The test suite contains explicit 
safety enforcement. Expected test startup output 
includes: ```text TESTING environment: 1 
UNIT_TESTING environment: 1 ALPACA_BASE_URL: 
default (paper) Live brokerage calls are BLOCKED in 
test mode ``` Never remove or bypass this behavior. 
Important test-related requirement: ```text Tests 
must not contact a live brokerage endpoint. ``` The 
current backlog includes or previously included: 
```text TEST-001 Prevent live brokerage calls from 
tests ``` TEST-001 was audited complete at main commit `32b84db`: the
pytest session gate rejects live endpoints, live-mode flags, disabled paper
mode, and non-test key prefixes before test execution; shared fixtures use
the network-free mock brokerage and market-data clients; and focused negative
and safe-path coverage passed 36 tests. Preserve both the session gate and
the subprocess rejection tests. ──────── Completed Milestone:
Implement PLAN PLAN is now a real state rather than a placeholder. Goal
The PLAN state should produce a concrete 
deterministic execution plan from the selected 
backlog task and repository context. The plan 
should eventually include: ```text Task ID Title 
Priority Owner Acceptance criteria Allowed areas or 
paths Feature branch Risk estimate Complexity 
estimate Dependencies or blockers Recommended 
execution agent ``` No AI is required for the first 
implementation. Use explicit rules and heuristics. 
Expected PLAN behavior Given a stored workflow in 
PLAN: 1. Resolve the task from the backlog using 
workflow.task_id. 2. Validate that the task still 
exists. 3. Validate that task identity and feature 
branch are consistent. 4. Load or inspect 
repository state. 5. Build a richer execution plan. 
6. Present or persist the plan. 7. Return a new 
workflow with: ```text PLAN → PREPARE_BRANCH ``` 
Important design question The state handler 
currently receives only StoredWorkflow. The PLAN 
handler may need access to: • backlog path, • 
repository root, • parsed tasks, • repository 
service, • planning service, • persistence for the 
generated plan. Avoid hidden globals if possible. 
Potential approaches: 1. Keep run(workflow) and 
load known project paths internally. 2. Add 
optional dependency injection arguments. 3. 
Introduce a workflow context object. 4. Add a state 
service class. Prefer the smallest change that 
remains testable. Do not introduce a large 
framework prematurely. Suggested first slice A safe 
incremental milestone would be: 1. Add 
deterministic RiskLevel and Complexity enums. 2. 
Extend ExecutionPlan with:
  • acceptance_criteria • allowed_areas • risk • 
  complexity
3. Add pure functions in planner.py: • 
  estimate_risk(task) • estimate_complexity(task)
4. Test those pure functions. 5. Do not wire PLAN 
into the workflow until model and planner tests are 
green. 6. Commit. 7. Then add 
engineering/workflow/plan.py. 8. Add its focused 
behavior test. 9. Wire PLAN in workflow_engine.py. 
10. Run the full suite and commit. This keeps the 
milestone small and reversible. ──────── Completed Milestone: PREPARE_BRANCH
Responsibilities: •
Verify repository exists. • Verify expected base 
branch. • Verify working tree cleanliness. • Detect 
whether the target feature branch already exists. • 
Create or safely resume the feature branch. • 
Refuse unsafe branch operations. • Transition to 
DELEGATE. Existing Git-service tests are reused
rather than duplicating Git logic. The implementation defaults to `main` as
the expected base, requires a clean repository, validates ancestry before
resuming an existing feature branch, and advances immutably to DELEGATE.
──────── Completed Milestone: DELEGATE Responsibilities: • Build a narrow coding-agent
prompt from the execution plan. • Include:
  • task, • allowed areas, • acceptance criteria, • 
  safety constraints, • branch, • required tests, • 
  reporting format.
• Launch the configured agent. • Persist run ID, 
timestamps, and status. • Transition to 
WAIT_FOR_AGENT. The coding agent must not be 
allowed to decide which task to work on. DELEGATE now accepts only approved
specialist owners, produces the required bounded prompt, launches only through
the repository-owned OPS-011 wrapper, validates and persists its complete run
metadata, blocks duplicate metadata, and advances immutably. Deterministic
task-and-branch request IDs recover an already claimed run after a manager or
shell restart instead of launching duplicate work.
──────── Completed Milestone: WAIT_FOR_AGENT Responsibilities: • Resume safely
after process restart. • Check whether the 
delegated run is:
  • pending, • active, • complete, • failed, • 
  timed out.
• Avoid duplicate delegation. • Transition to QA 
only when implementation is complete. WAIT_FOR_AGENT now requires complete
persisted run metadata, invokes only wrapper status for that run, validates
request/run/agent/branch identity, and never launches work. It refreshes
lifecycle timestamps, deadline, artifact paths, exact exit code, completion
time, and bounded reason. Pending and active runs remain waiting, complete runs
advance immutably to QA, and failed or timed-out runs stop without polling or
automatic relaunch.
──────── Completed Milestone: QA
Responsibilities: • Run configured tests. • Record:
  • command, • exit status, • passed/failed counts, 
  • runtime, • relevant output, • changed files.
• Enforce brokerage safety before and during tests. 
• Transition to REVIEW only when evidence exists. QA now requires a completed
delegation, accepts only an explicitly configured Python `-m pytest` command,
enforces test-mode flags and a five-minute bound, and persists command, exit
code, runtime, passed/failed counts, bounded output, changed files, completion time, and timeout
status. Successful evidence advances to REVIEW; failures and timeouts remain
stopped in QA without an automatic rerun.
──────── Completed Milestone: REVIEW
Responsibilities: • Compare code and QA
evidence against every acceptance criterion. • 
Produce explicit criterion-level results. • Do not 
reduce review to “tests passed.” • Recommend:
  • ACCEPT, • REJECT, • REWORK, • BLOCKED. A future 
AI reviewer may assist, but deterministic evidence 
and safety gates must remain authoritative. REVIEW now requires a bounded,
repository-local JSON evidence manifest that matches every authoritative
criterion exactly once with proof method, exact result, and PASS/FAIL status.
It derives ACCEPT only when all pass and otherwise derives REWORK, persists
the criterion-level result, and never regenerates an existing review.
──────── Completed Milestone: REPORT
Responsibilities: Produce a clear engineering 
report containing: ```text Task Branch Agent 
Elapsed time Files modified Tests run Results 
Acceptance criteria status Risks Recommendation 
Next action ``` REPORT now derives those fields from persisted delegation,
QA, review, and authoritative backlog evidence; stores both structured and
rendered output; refuses incomplete or inconsistent evidence; never regenerates
an existing report; requires human approval; and advances to COMPLETE without
merging, pushing, or deploying.
──────── Completed Milestone: COMPLETE
COMPLETE validates the persisted accepted report, prints the final report,
archives the full workflow record under `.git/engineering-reports/`, and then
clears the active workflow state. Invalid or inconsistent completion evidence
is rejected before cleanup. The manager returns to idle and does not select a
new task in the same invocation; non-COMPLETE states retain their existing
atomic save behavior.
──────── Completed Foundation: OPS-011 Codex CLI Wrapper
`engineering/codex_cli_wrapper.py` now owns the bounded `codex exec`
subprocess boundary. It accepts `launch` and `status` commands; OPS-012 connects
those commands to DELEGATE and WAIT_FOR_AGENT without moving subprocess
ownership into either handler.

Launch verifies the clean checked-out branch, reads a bounded prompt from
stdin, uses `codex exec --sandbox workspace-write --cd <repo> -`, and never
uses dangerous sandbox or approval bypasses. Deterministic request IDs map to
durable records under `.agent-state/codex-runs/`. Request directories are
claimed atomically and the complete initial JSON record is published
create-once; matching concurrent requests wait for at most one second and
return the same run. A publication that remains incomplete becomes an
explicit failed claim for human review. Agent, branch, and prompt-digest
conflicts are rejected.

The worker captures bounded stdout and stderr separately, persists exact exit
codes, applies a finite timeout to the complete process group, and records
`COMPLETE`, `FAILED`, or `TIMED_OUT` terminal evidence. Status verifies worker
PID plus process-start identity and reconciles dead or expired active records
without automatic relaunch.

Wrapper tests run the actual repository command around temporary fake Codex
executables. Test mode requires explicit injection and rejects executables
named `codex`, preventing real service, authentication, or network use. The
OPS-011 focused suite passed `13 passed, 1 warning`; the complete safe suite
passed `186 passed, 2 warnings`, with live brokerage calls blocked. Remaining
operational risks are Linux `/proc` dependence for worker identity, runtime
directory durability/permissions, and filesystem support for atomic hard-link
publication.

──────── Completed Integration: OPS-012 DELEGATE and WAIT_FOR_AGENT
The executor invokes the repository-owned wrapper with a finite 30-second
command timeout, transports the bounded prompt on stdin, and parses the full
wrapper record. DELEGATE persists validated wrapper identity and lifecycle
metadata before moving to WAIT_FOR_AGENT. Deterministic request IDs make a
pre-persistence retry recover the wrapper's existing run.

WAIT_FOR_AGENT performs status-only inspection of the persisted run. It maps
claimed/running/terminal wrapper states deterministically, refreshes the full
record, advances only COMPLETE to QA, and leaves FAILED or TIMED_OUT stopped
for human review. Existing workflow JSON remains loadable. All automated
execution uses injected fake wrappers or fake Codex executables; focused tests
passed `84 passed, 1 warning` and the complete safe suite passed `196 passed, 2
warnings`, with the live-brokerage safety gate passing. Remaining operational
risks are the OPS-011 filesystem and Linux process-identity dependencies plus
operator responsibility for durable runtime retention.

──────── Completed Milestone: OPS-013 bounded manager driver

The manager's no-argument command still advances exactly one state. Explicit
`--drive` mode reloads the persisted workflow before each dispatch and saves
each result before continuing. Every run has finite step, elapsed-time, WAIT
interval, and WAIT poll bounds. WAIT uses status-only polling and persisted
OPS-012 identity; failures, timeouts, QA failure, REVIEW rework, stale state,
exceptions, and bound exhaustion stop for human review.

REPORT may advance to COMPLETE, but drive mode stops immediately without
dispatching COMPLETE, clearing state, starting a new task, merging, pushing, or
deploying. A later ordinary one-state manager invocation remains the explicit
approval-gated completion path. Driver persistence records timing, continuity,
resume explanation, stale/blocked state, counters, and the last stop reason.

──────── Desired Long-Term Artifact Model
The architecture may eventually evolve from one 
mutable-looking workflow record into staged 
artifacts: ```text StoredWorkflow
    ↓ PlanningContext ↓ ExecutionPlan ↓ 
ExecutionResult
    ↓ QAResult ↓ ReviewResult ↓ EngineeringReport 
``` This is a reasonable direction, but it should 
be introduced gradually. Do not perform a large 
refactor merely to reach this shape. ──────── 
Engineering Principles Deterministic orchestration 
The manager controls: • task selection, • allowed 
scope, • state transitions, • persistence, • 
safety, • acceptance gates. Narrow agent authority 
Agents may implement work only within the plan. 
They should not: • select arbitrary tasks, • alter 
safety settings, • switch to live trading, • change 
secrets, • rewrite unrelated architecture, • mark 
themselves accepted. Evidence over confidence Every 
transition after delegation should be supported by 
stored evidence. Resume-safe behavior A reboot or 
interrupted shell session should not: • lose the 
task, • delegate twice, • create duplicate 
branches, • skip QA, • forget review results. Small 
commits Use one focused commit per milestone. 
Examples: ```text Add deterministic plan risk 
estimates Implement PLAN workflow handler Prepare 
workflow feature branches Persist delegated agent 
runs Record QA evidence Review acceptance criteria 
Generate engineering workflow report ``` Test 
separation Keep tests focused: • dispatcher tests 
verify routing, • state tests verify state-specific 
behavior, • planner tests verify pure planning 
logic, • Git tests verify repository operations, • 
store tests verify persistence, • manager tests 
verify orchestration. Avoid testing every layer in 
one giant integration test. ──────── Recommended 
First Commands for a New Agent ```bash cd 
~/.openclaw/workspace/trading-bot cat MENTOR.md git 
status git branch --show-current git log --oneline 
-10 sed -n '1,260p' engineering/models.py sed -n 
'1,320p' engineering/planner.py sed -n '1,340p' 
engineering/manager.py sed -n '1,320p' 
engineering/backlog.py sed -n '1,280p' 
engineering/workflow_engine.py sed -n '1,320p' 
engineering/workflow_store.py find 
engineering/workflow -maxdepth 2 -type f -print 
-exec sed -n '1,220p' {} \; .venv/bin/python -m 
pytest ``` Do not make edits until the current tree 
and tests are understood. ──────── Current Human 
Workflow Preference The project owner prefers: • 
one SSH command at a time when working manually, • 
small, tested commits, • clear explanations of why 
a change is being made, • no giant rewrites, • no 
hidden background work. Once Codex CLI is 
installed, the agent may edit files and run 
commands directly, but should still preserve the 
small-milestone approach. ──────── Definition of 
Success The engineering manager is successful when 
it can safely take one backlog task from TODO to a 
final engineering report with: • deterministic task 
selection, • explicit plan, • safe branch, • 
bounded delegation, • persisted state, • automated 
QA, • criterion-level review, • clear report, • no 
live brokerage risk,
• no manual copy/paste required for routine operation.

──────── Durable Engineering Events and Outbox (OPS-014)

The manager now creates an isolated versioned SQLite event store under
`.agent-state/engineering-events.sqlite3` and injects it into WorkflowStore.
Workflow JSON remains authoritative. Every persisted workflow snapshot is
reconciled into deterministic sanitized events, so retries and restarts do not
duplicate event or notification intent. Notification outbox rows are inserted
in the same transaction as their event and use finite claim leases, retries,
and dead-letter state.

The event store must never point at `trading_bot.db`. Tests inject temporary
paths. Payloads are allowlisted and bounded; raw agent stdout/stderr, prompts,
environment values, secrets, and arbitrary paths are excluded.

`EngineeringQueryService` provides the common read projection for the planned
Telegram adapter and read-only engineering dashboard. Missing goal or PR data
is explicit. The control table's pause flag gates only deterministic manager
dispatch; it does not operate the paper bot, processes, Codex wrapper, or TUI.
Telegram transport, dashboard routes, and approval actions are not part of
OPS-014 and require later separately approved tasks. CONFIG-001 remains paused.

──────── Telegram Engineering Adapter (OPS-015)

OPS-015 adds an independent Telegram long-poll adapter over the OPS-014 event,
outbox, query, and pause contracts. Runtime credentials are read only from
`ENGINEERING_TELEGRAM_BOT_TOKEN` and
`ENGINEERING_TELEGRAM_JOSH_CHAT_ID`; neither is committed or persisted.
Only a private message where chat and sender IDs both match Josh is accepted.

The command set is closed: `/status`, `/current`, `/next`, `/report`, `/pause`,
and `/resume`. Read commands consume the bounded `EngineeringQueryService`
snapshot. Pause/resume go through `EngineeringControlService`, use revisioned
compare-and-set storage, and atomically append audit events. The control flag
gates deterministic manager dispatch only.

Telegram update offsets, the one-consumer lease, outbox delivery leases,
receipts, retries, and dead letters live in the isolated engineering event
database. Only completion, failure, blocked, stale, PR-ready, and
approval-required events produce Telegram notifications. The adapter has no
shell, Git, brokerage, trading, Codex/TUI, raw artifact, deployment, or service
management capability.

The focused OPS-015 suite passed 61 tests and the full safe suite passed 291
tests with live brokerage blocked. No real Telegram request was made and no
service was installed or started. Operational credential provisioning and
service deployment remain explicitly outside OPS-015.

──────── Bounded Telegram Smoke Launcher (OPS-017)

OPS-017 adds `engineering.telegram_service`, a foreground-only launcher for a
manual real-bot smoke test. The command is fixed to an external 0600 secret
file, the isolated `.agent-state/telegram-smoke-events.sqlite3` state database,
20 polls, and 300 seconds. The first bound reached stops the launcher. There is
no daemonization, supervisor, systemd integration, endless loop, or automatic
restart.

The smoke database owns all temporary query/control/audit/lease state. The
normal `.agent-state/engineering-events.sqlite3` database is rejected by path
validation and must remain unopened and unchanged. Unconditional cleanup
releases the adapter lease and restores the pre-smoke isolated pause boolean
after success, every failure, SIGINT, SIGTERM, competing poller, max-polls, or
max-seconds.

The external secret parser accepts only `ENGINEERING_TELEGRAM_BOT_TOKEN` and
`ENGINEERING_TELEGRAM_JOSH_CHAT_ID` from an operator-owned nonsymlink regular
file with exact mode 0600. Structured stderr logs are bounded JSON with an
explicit safe field set. Deterministic exit codes distinguish configuration,
competing poller, permanent Telegram, runtime/cleanup, and signal outcomes.

Automated tests use fake Telegram boundaries. Focused OPS-017 tests passed 67
tests and the full safe suite passed 317 tests with live brokerage blocked.
The real secret file and real Telegram smoke test have not been authorized or
performed, so OPS-017 remains blocked before push and PR creation.
