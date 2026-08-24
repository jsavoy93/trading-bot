# Trading Bot — Code Map

> **EVERY TIME I work on this codebase: I must say out loud:
> "I'm reading MENTOR.md for instructions"
> before I answer any question about the trading bot.**

**⚠️ This is a living document. When I discover something important about how the code works, make a significant change, or uncover a bug/mistake — I must update this file. Don't let important knowledge live only in my head or session history.**

This file is the first thing to read before touching any part of this codebase. It exists so I (the AI) don't have to reverse-engineer everything from scratch every session.

---

## Mandatory Iteration Continuity Process

Every agent must update `ITERATION_PROGRESS_LOG.md` after every bounded
implementation iteration, before giving the final report to Josh. Session
history, chat messages, and uncommitted workflow state are not sufficient
handoff records. Read-only reporting tasks must not update this log or any
repository file; their authoritative handoff is the external timestamped
archive under `/root/.openclaw/audit-archives/<repository-name>/`.

For each iteration, append a short entry to `ITERATION_PROGRESS_LOG.md`
containing:

- UTC date and time
- Backlog item and objective
- Branch and commit (or `none`)
- Status: `DONE`, `BLOCKED`, `REWORK`, or `IN_PROGRESS`
- Files changed
- Tests/backtests run and their exact results
- Important decisions, discoveries, or remaining risks
- The exact next action and whether Josh's approval is required

Additional requirements:

1. Update relevant architectural or troubleshooting sections when an
   iteration changes how the system works; the progress log alone is not a
   substitute for maintaining the code map.
2. Never claim an iteration is complete without acceptance evidence.
3. If work stops because of a dirty tree, failed test, stale workflow, safety
   concern, or unclear scope, log the blocker and the recovery step.
4. When starting a new session, read the latest entry and any unresolved
   `BLOCKED`, `REWORK`, or `IN_PROGRESS` entries in
   `ITERATION_PROGRESS_LOG.md`, then verify them against Git and persisted
   workflow state. Consult older history when needed.
5. Keep entries concise and factual. Never leave the only record of current
   status in chat history.

### Reporting location and clean-tree continuity

Reporting mode is selected automatically before artifacts are created.
Implementation tasks overwrite ignored `REPORT.md`, append the required
implementation continuity entry here, and write a reviewable archive under
`reports/`. Merge readiness, merge execution, audits, reviews, dependency
gates, preflight checks, verification-only work, documentation inspections,
tasks promising no repository changes, and any workflow requiring a clean
tree use read-only reporting mode. Read-only mode writes only to
`/root/.openclaw/audit-archives/<repository-name>/` and never changes this
repository. Uncertain classification fails safe to read-only mode, so clean
tree checks cannot be invalidated by mandatory reporting.

### Infrastructure-dependent acceptance

Track implementation, automated verification, and manual operational
verification as separate states. If implementation is complete and safe tests
pass, but an external prerequisite (for example a second account, DNS,
certificate, email delivery, Telegram, or Slack) is unavailable, keep the
machine-parseable backlog status as `REVIEW` and record `Manual Operational
Verification Pending` as review classification/detail; do not classify the task
as failed or reopen implementation unless evidence reveals a software defect.
Merge requires Josh's explicit acceptance of the documented residual operational
risk, or the pending check may wait until its prerequisite is available.

---

## Architecture Overview

**Entry point:** `main.py` → starts `SmartBot` in continuous mode
**Dashboard:** `dashboard.py` → `FastHTML` web UI on port 8000
**Database:** `trading_bot.db` (SQLite) + `analyzed_stocks` table

The bot runs in a loop:
1. Pull a batch of symbols (30 at a time, RS-ranked)
2. Analyze each for BUY/SELL/HOLD signals
3. Execute trades if conditions are met
4. Sleep 5 minutes → repeat

---

## Key Files

| File | Role |
|---|---|
| `src/core/smart_bot.py` | Main trading logic — analysis, execution, loops |
| `src/database/sqlite_db.py` | All SQLite reads/writes |
| `src/core/settings_service.py` | Bot parameters (thresholds, toggles, etc.) |
| `dashboard.py` | Web dashboard (charts, search, status) |
| `templates/dashboard.html` | Dashboard HTML + JavaScript |

---

## How Analysis Works

### Two analysis paths

**`analyze_symbol()`** — single-timeframe (daily only), used by position rotation
**`analyze_multi_timeframe()`** — daily + hourly, used by main analysis loop

Both paths:
1. Pull data from Alpaca (`get_market_data()`)
2. Calculate indicators: RSI, SMA fast/slow, MACD, Bollinger Bands, VWAP
3. Build a total score
4. Determine signal: BUY / SELL / HOLD

### The score components (daily, each 0-100 scale in different ranges)

| Component | Range | Description |
|---|---|---|
| RSI | -100 to 100 | Oversold = bullish, overbought = bearish |
| SMA | -100 to 100 | Price above SMA = bullish |
| MACD | -100 to 100 | MACD line vs signal line |
| Bollinger Bands | -100 to 100 | Price position within bands |
| Regime | -20 to 20 | Market regime (VIX-based) |
| Catalyst | 0 to ~35 | Gap up / volume surge bonus |

**Total score** = RSI + SMA + MACD + BB + regime + catalyst

### BUY signal requirements (ALL must pass)
- Score ≥ `min_score_buy` (default 50, loaded from settings)
- RSI < `rsi_buy_threshold` (oversold, default 30)
- SMA in uptrend
- MACD positive
- Volume ≥ average

If ALL pass → BUY signal. If ANY fail → HOLD (logged to `failed_analyses`).

---

## The `failed_analyses` Table — Critical Distinction

**This table does NOT track errors or data failures.**

It logs **every successful analysis that resulted in HOLD** — i.e., a symbol was fully analyzed but didn't meet BUY criteria. The `blocked_by` field shows the **first** criterion that failed.

| `blocked_by` value | Meaning |
|---|---|
| `Score ≥ 50` | Score too low (most common) |
| `SMA uptrend` | Price below SMA slow |
| `Volume ≥ avg` | Volume below average |
| `No market data` | Alpaca returned no bars |
| `No BUY signal` | Generic fallback |

**Only 1,810 "No market data" failures out of 36,000+ in 6h** = the real data problem to watch. Everything else is just normal HOLD signals.

---

## The `analyzed_stocks` Table — Failure Counters

**This table tracks analysis health, not trade decisions.**

Columns:
- `analysis_successes` — incremented by `increment_analysis_success()` when a symbol's analysis completes successfully
- `analysis_failures` — incremented by `increment_analysis_failure()` ONLY when `analyze_symbol()` throws an exception or returns None (bad data, NaN indicators)
- `last_analyzed` — timestamp of last analysis attempt

**Ban logic:** Symbols with `analysis_failures >= 3` are excluded from the rolling analysis queue.

**Unban mechanisms:**
1. **7-day cooldown** — `get_consistently_failing_symbols(min_failures=3, cooldown_days=7)` checks `last_analyzed < now - 7 days`. After 7 days pass, the symbol is retried.
2. **Success unban** — `increment_analysis_success()` sets `analysis_failures = 0` when `analysis_successes >= 2`.

---

## Symbol Selection — The Rolling Queue

`_get_rolling_ticker_list()` builds the analysis queue at the start of each cycle:

1. Get ALL US symbols from Alpaca
2. Exclude: portfolio holdings, pending orders, failing symbols (3+ failures)
3. RS-rank the first 300 candidates (relative strength vs SPY)
4. Queue = ranked top 150 + remainder

Each loop processes 30 symbols from the queue, then sleeps 5 minutes.

---

## Critical Settings (from `settings_service`)

| Setting | Default | Meaning |
|---|---|---|
| `min_score_buy` | **50** | Minimum score for BUY signal |
| `min_score_sell` | 65 | Minimum score for SELL signal |
| `rsi_buy_threshold` | 30 | RSI must be below this for BUY |
| `rsi_sell_threshold` | 70 | RSI must be above this for SELL |
| `sma_fast` | 10 | Fast SMA period |
| `sma_slow` | 30 | Slow SMA period (also min bars needed) |
| `enable_multi_timeframe` | True | Require daily + hourly agreement |
| `enable_rotation` | True | Sell weak positions to buy stronger ones |

---

## Common Mistakes to Avoid

1. **Never say "X% of symbols failed" without checking `failed_analyses.blocked_by`** — the vast majority of records are normal HOLD signals, not errors.

2. **`increment_analysis_failure` is NOT called for HOLD signals** — it's only called when `analyze_symbol()` returns None or throws.

3. **`analyzed_stocks.analysis_failures` and `failed_analyses` are completely independent tables** with different purposes.

4. **The bot is working correctly when most symbols score 0-30** — that's just the market. The score threshold (`min_score_buy`) is the real trade trigger.

5. **Alpaca API is fine** — the 97% figure was HOLD signals. Only "No market data" records indicate a data problem.

6. **`filter_results` always empty in DB?** — Three bugs in `analyze_multi_timeframe` caused silent failures → returns None:
   - `ai_research` referenced outside its `try` block (use `ai_research = None` before the block)
   - `total_score` used in `buy_criteria` but never computed in MTF function
   - `vol_ratio` typo (should be `volume_ratio`)
   → **All three fixed.** `ai_research = None` is now set unconditionally before the block, `total_score` is computed before `buy_criteria` is built, and `volume_ratio` is used consistently throughout.

7. **Dashboard JS errors (e.g. "Cannot access 'bb' before initialization")** — check `templates/dashboard.html` for duplicate `const` declarations in the same scope. The filter dashboard section uses `const bb = secs.blocked_by` twice — the second redeclaration causes a ReferenceError in strict JS mode. Dashboard errors are often template bugs, not API bugs — always verify the API response with `curl` first.

8. **Logging paths must be portable** — `src/core/smart_bot.py` resolves `trading_bot.log` relative to the repository, supports `TRADING_BOT_LOG_PATH`, and uses the null device during automated tests. Do not restore a host-specific absolute path or let test imports modify runtime log files.

---

## How to Check Bot Health

```bash
# Is it running?
ps aux | grep main.py | grep -v grep

# Recent activity
tail -50 /tmp/bot.log

# How many loops completed?
grep -c "✅ Loop #" /tmp/bot.log

# Real data failures (actual Alpaca problems)
grep "No market data" /tmp/bot.log | wc -l

# Current score distribution
grep "Score:" /tmp/bot.log | sed 's/.*Score:\([0-9-]*\)\/100.*/\1/' | sort -n | tail -10

# DB state
cd trading-bot && .venv/bin/python -c "
from src.database.sqlite_db import sqlite_db
rows = sqlite_db.get_analysis_results(limit=100000)
by_fail = {}
for r in rows:
    f = r.get('analysis_failures', 0)
    by_fail[f] = by_fail.get(f, 0) + 1
for f in sorted(by_fail): print(f'{f} failures: {by_fail[f]} symbols')
"
```

---

## Test Brokerage Safety

`TEST-001` is complete as of the audit at main commit `32b84db`. Pytest sets
`TESTING=1` and `UNIT_TESTING=1` before project imports in `tests/conftest.py`.
The session configuration rejects known live Alpaca endpoints, enabled live
mode flags, disabled paper-mode flags, and non-paper API-key prefixes before
tests run. Shared brokerage fixtures use `MockBrokerageClient` and
`MockMarketDataClient`; those implementations return in-memory deterministic
data and do not create network clients. Subprocess safety tests prove live
mode, live endpoints, disabled paper mode, and non-test keys make pytest exit
nonzero, while paper/test configurations pass.

Do not weaken this gate, replace the mock fixtures with real clients, or infer
that a normal green test run alone proves the gate: retain the negative
subprocess tests in `tests/test_brokerage_safety_enforcement.py`.

## Indicator Calculation Test Contract

`TEST-002` is covered by `tests/test_smart_bot_indicators.py` without
constructing `SmartTradingBot` or any brokerage/data client. The focused tests
invoke `calculate_indicators` with a lightweight object containing only the
configured SMA and RSI periods.

MACD is the repository's existing 12/26/9 calculation: pandas exponential
moving averages with `adjust=False`, initialized recursively from the first
value. Bollinger bands use a 20-row rolling mean and pandas' sample standard
deviation. Known rising, alternating, and falling price inputs pin RSI, SMA,
MACD, Bollinger, and volume results.

The volume contract is exact: input `volume` is unchanged;
`volume_sma_20` includes the current row and preceding 19 rows; its first 19
rows are NaN; and `volume_ratio` is raw volume divided by that SMA. A zero,
missing, or non-finite denominator leaves the ratio NaN. These are
timeframe-agnostic dataframe semantics, not persistence, API, dashboard,
liquidity, catalyst, ranking, or signal-confirmation requirements.

## Autonomous Engineering Workflow

The deterministic engineering manager lives under `engineering/`.

### Core files

| File | Role |
|---|---|
| `engineering/manager.py` | Loads or creates the persisted workflow, dispatches one state, then saves the result |
| `engineering/manager_driver.py` | Opt-in bounded loop that reloads and persists the active workflow around every state dispatch |
| `engineering/workflow_engine.py` | Routes the current `WorkflowState` to the matching state handler |
| `engineering/workflow_store.py` | Persists and reloads the current workflow |
| `engineering/workflow/` | Contains one independently testable handler module per workflow state |

### Workflow states

```text
DISCOVER
  ↓
PLAN
  ↓
PREPARE_BRANCH
  ↓
DELEGATE
  ↓
WAIT_FOR_AGENT
  ↓
QA
  ↓
REVIEW
  ↓
REPORT
  ↓
COMPLETE




```

### Bounded manager drive mode

`python -m engineering.manager` remains the one-state default. Repeated
advancement is available only through `--drive`, with finite defaults of eight
steps, 900 elapsed seconds, a 30-second WAIT interval, and 20 WAIT polls.
Every result is atomically persisted before another step. WAIT polling is
status-only and uses the OPS-012 delegation identity, so restarts recover the
same wrapper run rather than launching another one.

Drive mode stops for delegated failure/timeout, failed QA, REVIEW rework,
stale state older than 48 hours, malformed state, handler failure, an exhausted
bound, or an unchanged non-WAIT state. REPORT may persist COMPLETE, but the
driver then stops. It never dispatches COMPLETE, archives or clears state, or
selects another task; the ordinary one-state invocation retains that explicit
approval-gated OPS-010 behavior.

Driver timing and continuity metadata is stored backward compatibly with the
workflow. Runtime limits use monotonic time; audit timestamps use UTC. Tests
inject handlers, clocks, sleepers, and agent status, never sleep in real time,
and fail if real Codex, brokerage, subprocess-launch, or network boundaries are
used.

### State-handler contract

Each workflow handler follows this contract:

```python
def run(workflow: StoredWorkflow) -> StoredWorkflow:
    # Validate preconditions
    # Perform this state's work
    # Return the resulting workflow
```

Handlers must not directly invoke the next handler. They return a workflow with the next state, and the manager persists it before the next run.

### Immutable workflow state

`StoredWorkflow` is a frozen dataclass. Do not mutate fields directly.

Incorrect:

```python
workflow.state = WorkflowState.PLAN
```

Correct:

```python
from dataclasses import replace

return replace(
    workflow,
    state=WorkflowState.PLAN,
)
```

This makes state transitions explicit and prevents accidental mutation.

### Current implemented transitions

`engineering/workflow/discover.py` performs:

```text
DISCOVER → PLAN
```

`engineering/workflow/plan.py` deterministically resolves the stored task from
`AGENT_BACKLOG.md`, validates its generated feature branch, builds and prints a
concrete execution plan, and performs:

```text
PLAN → PREPARE_BRANCH
```

The execution plan includes the task's acceptance criteria and allowed areas,
plus deterministic risk and complexity estimates. The richer plan is rebuilt
from the authoritative backlog and repository state; it is not persisted in
the compact workflow record.

`engineering/workflow/prepare_branch.py` uses the Git service to validate the
repository, require a clean tree, verify the expected base branch, and create
or resume the stored feature branch. Existing feature branches are resumed
only when the expected base is an ancestor. Unrelated current branches and
unrelated feature histories are rejected. After successful preparation it
performs:

```text
PREPARE_BRANCH → DELEGATE
```

The default expected base branch is `main`; tests can inject a different base
and Git service without changing the persisted workflow schema.

`engineering/workflow/delegate.py` resolves the stored task, accepts only the
approved `trading-exec` and `dashboard-agent` owners, builds a deterministic
bounded prompt, and launches through the repository-owned OPS-011 wrapper.
Successful launches validate and persist the deterministic request ID plus
the wrapper's complete identity, lifecycle, timing, artifact, and terminal
metadata before:

```text
DELEGATE → WAIT_FOR_AGENT
```

Existing delegation metadata blocks a second launch. A retry after the wrapper
claims work but before the manager persists the workflow derives the same
request ID from task and branch; the wrapper therefore returns the existing
run instead of launching duplicate work. Legacy workflow JSON without the
OPS-012 fields remains loadable. Automated tests inject fake wrapper commands
or fake launchers and never start real Codex or contact the network.

`engineering/workflow/wait_for_agent.py` queries only the persisted run through
the same repository-owned wrapper and never launches work. `CLAIMED` maps to
`PENDING`, `RUNNING` maps to `ACTIVE`, and wrapper terminal records map to
`COMPLETE`, `FAILED`, or `TIMED_OUT`. `PENDING` and `ACTIVE` remain in
`WAIT_FOR_AGENT`; `FAILED` and `TIMED_OUT` are persisted as stopped terminal
states and are not polled again; `COMPLETE` performs:

```text
WAIT_FOR_AGENT → QA
```

Every status result must match the persisted request, run, specialist, and
branch identity. It refreshes all wrapper-owned timestamps, deadline,
stdout/stderr paths, exit code, completion time, and bounded reason. Restarted
manager or shell processes resume from the same durable identifiers. Tests use
fake monitors only.

`engineering/workflow/qa.py` requires a completed delegated run and invokes
the configured `ENGINEERING_QA_COMMAND` through `engineering/qa_runner.py`.
Only Python `-m pytest` commands are accepted. The subprocess is bounded to
five minutes and receives forced `TESTING=1` and `UNIT_TESTING=1` flags.

QA persists the exact command, exit code, runtime, parsed passed/failed counts,
a bounded output summary, changed files, completion time, and timeout status. Successful evidence
performs:

```text
QA → REVIEW
```

Failed or timed-out evidence remains in `QA` and prevents an automatic rerun.
Legacy workflow JSON without QA evidence remains valid, while malformed QA
records are rejected.

`engineering/workflow/review.py` reconstructs the stored task from the
authoritative backlog and requires successful persisted QA. It then loads a
repository-local JSON manifest configured by
`ENGINEERING_REVIEW_EVIDENCE_PATH`. The manifest is limited to 65,536 bytes
and must cover every acceptance criterion exactly once, in authoritative
order, with a nonblank proof method, exact result, and `PASS` or `FAIL`.

The recommendation is derived rather than supplied: all criteria passing
produces `ACCEPT` and performs `REVIEW → REPORT`; any failure produces
`REWORK` and remains stopped in `REVIEW`. Persisted review evidence prevents
automatic regeneration. REVIEW deliberately does not treat a passing test
suite alone as proof that every acceptance criterion is satisfied.

`engineering/workflow/report.py` requires delegation, successful QA, and an
`ACCEPT` review. It resolves the authoritative task and uses
`engineering/reporter.py` to produce a structured, human-readable report with
task, branch, agent, elapsed time, changed files, test command/results, every
criterion result, risks, recommendation, and next action. Elapsed time is
calculated from the persisted delegation start and report generation time.

The report is persisted in the workflow audit record, is never regenerated
automatically, explicitly requires Josh's approval, and performs:

```text
REPORT → COMPLETE
```

REPORT never merges, pushes, deploys, or enables live trading.

`engineering/workflow/complete.py` validates that COMPLETE has a persisted
ACCEPT report whose task, branch, and criterion results match the active
workflow. It prints the final report before manager cleanup. The manager then
archives the complete workflow record under `.git/engineering-reports/` and
clears `.git/engineering-workflow.json`, returning the manager to idle without
starting another task in the same invocation. Invalid completion evidence is
rejected before archive or cleanup; non-COMPLETE states retain normal atomic
save behavior.

The transition is tested independently in:

```text
tests/test_engineering_discover.py
tests/test_engineering_plan.py
tests/test_engineering_prepare_branch.py
tests/test_engineering_delegate.py
tests/test_engineering_wait_for_agent.py
tests/test_engineering_qa.py
tests/test_engineering_review.py
tests/test_engineering_report.py
tests/test_engineering_complete.py
tests/test_engineering_manager.py
```

Dispatcher routing is tested separately in:

```text
tests/test_engineering_workflow_engine.py
```

The dispatcher test verifies routing and preserved workflow data. State-specific tests verify the behavior unique to each handler.

### Repository-owned Codex wrapper foundation (OPS-011)

`engineering/codex_cli_wrapper.py` is the only repository-owned implementation
that invokes `codex exec`. Its standalone contract is:

```text
python engineering/codex_cli_wrapper.py launch \
  --agent <specialist> --branch <checked-out-branch> \
  --request-id <deterministic-id> --repo <repository>

python engineering/codex_cli_wrapper.py status --run-id <run-id>
```

Launch reads a bounded prompt from stdin, verifies a clean assigned branch,
and invokes Codex non-interactively with `exec --sandbox workspace-write --cd
<repository> -`. It never adds approval- or sandbox-bypass flags. OPS-012 now
connects this wrapper to DELEGATE and WAIT_FOR_AGENT through the validated
metadata contract described above.

The default durable runtime is `.agent-state/codex-runs/`, which is ignored by
Git. A deterministic request ID maps to one run directory containing atomic
`run.json`, bounded `stdout.log` and `stderr.log`, and the bounded prompt.
Initial claim publication uses create-once atomic publication. A matching
concurrent launcher waits at most one second for the complete claim record and
returns the same run; an incomplete claim beyond that bound becomes an
explicit `FAILED` record requiring human review. Identity reuse with a
different agent, branch, or prompt digest is rejected.

Workers have a finite default deadline, run in their own process group, and
receive bounded TERM/KILL timeout handling. Status verifies PID plus Linux
process start identity, reconciles dead workers and expired deadlines, and
never relaunches terminal work. Output artifacts and diagnostic summaries are
bounded.

Tests execute the wrapper itself as a subprocess but set
`ENGINEERING_CODEX_COMMAND` to a temporary fake executable. When `TESTING=1`
or `UNIT_TESTING=1`, the wrapper fails closed if no injected command exists or
if the configured executable is named `codex`; tests therefore do not contact
the Codex service or require authentication.

### Current verification checkpoint

The autonomous workflow checkpoint at commit `3cfe9ea` had:

```text
67 tests passed
```

After making the trading-bot log path portable and preventing test-mode log
writes, the full suite reached:

```text
70 tests passed
```

After implementing the deterministic PLAN state, the full suite reached:

```text
76 tests passed
```

After implementing PREPARE_BRANCH, the full suite reached:

```text
87 tests passed
```

After implementing DELEGATE, the full suite reached:

```text
101 tests passed
```

After implementing WAIT_FOR_AGENT, the full suite reached:

```text
118 tests passed
```

After implementing deterministic QA evidence, the full suite reached:

```text
132 tests passed
```

After implementing deterministic criterion-level REVIEW, the full suite reached:

```text
152 tests passed
```

After implementing deterministic REPORT generation, the full suite reached:

```text
163 tests passed
```

After implementing COMPLETE archival and active-state cleanup, the full suite
reached:

```text
173 tests passed
```

After implementing the standalone OPS-011 Codex wrapper foundation, the
focused wrapper suite passed `13 tests` and the full safe suite reached:

```text
186 tests passed
```

After integrating the wrapper with DELEGATE and WAIT_FOR_AGENT in OPS-012, the
focused integration suite passed `84 tests` and the full safe suite reached:

```text
196 tests passed
```

The test safety guard confirms that live brokerage calls remain blocked during tests.

## TEST-003 decision-path safety contract

`SmartTradingBot.execute_trade()` fails closed unless the signal is exactly
`BUY` or `SELL`. `HOLD`, falsey values, and unknown signals return before any
account, position, pending-order, or submission call. Do not restore the old
truthiness check or infer SELL from “not BUY.” Deterministic mocked tests cover
exact BUY/SELL sides, HOLD and unknown rejection, owned and unowned SELLs, and
pending-order duplicate prevention. TEST-003 passed 7 focused tests and the
full safe suite passed 229 tests.

## TEST-004 settings loading contract

`src/core/settings_service.py` is tested against an isolated temporary SQLite
database; tests must never point it at `trading_bot.db`. Missing typed settings
return the caller-supplied default. Dashboard-format persisted strings load as
`int`, `float`, `bool`, or `str`, including the documented true/false spellings.
Malformed numeric values raise `ValueError`, and a failed typed numeric save
does not persist a row. TEST-004 passed 12 focused tests and the full safe suite
passed 241 tests. CONFIG-001 later established the authoritative cross-process
strategy settings schema described below.

## CONFIG-001 authoritative strategy settings contract

`src/core/settings_service.py` is the single source of truth for strategy
settings through `STRATEGY_SETTINGS_SCHEMA`. Each schema entry defines the key,
default, type, bounds, step, dashboard description, and dashboard category.
Effective configuration precedence is schema defaults first, then persisted
SQLite overrides from `settings`; invalid new overrides are rejected by
`save_typed()`/`validate_typed()` rather than silently clamped.

`dashboard.py` must derive `/api/settings` metadata and persisted updates from
`dashboard_parameters()` and `save_typed()`, not from a duplicated local
parameter dictionary. `src/core/smart_bot.py` must load
`load_effective_strategy_settings()` during initialization after constructor
default attributes exist, apply schema-backed values to matching bot attributes,
and log a bounded non-secret deterministic line using
`format_effective_strategy_settings_for_log()`.

The schema default for `min_score_buy` is `50`, matching the existing bot BUY
threshold behavior. Dashboard metadata now uses that same default instead of the
old duplicated dashboard-only `65` value.

Dashboard setting updates must validate the full submitted batch before any
write. If any known submitted value is invalid, the API returns HTTP 400 and no
submitted values are persisted. Valid batches persist normalized typed values
through `save_typed()`.

Legacy persisted values can predate schema validation. Effective loading now
falls back per invalid key to the schema default and logs one bounded warning,
allowing bot startup and dashboard metadata rendering to continue safely while
strict validation remains enforced for new writes. The warning format is:
`Invalid persisted strategy setting key=<key> value_type=<type> reason=<bounded reason>; using default=<default>`.
It must not include the raw persisted value; validation errors are normalized so
Python conversion exceptions cannot echo sensitive or long DB-controlled input.

`atr_position_size_pct` is the dashboard/schema percent value for ATR sizing;
`SmartTradingBot.risk_per_trade` is the internal decimal derived from it.
`loop_delay_seconds` controls normal continuous-loop delay when no explicit
caller/CLI delay is supplied. Explicit `run_continuous_loop(..., loop_delay=...)`
or `--delay` values take precedence over configuration. `None` means use config;
positive numeric values override config; `0` is a valid explicit no-delay value;
negative, boolean, or non-numeric direct method values raise `ValueError` before
the continuous loop starts. Invalid CLI values fail through argparse type
parsing.

CONFIG-001 initial focused tests passed 22 settings tests and 7 smart-bot
decision-path tests; the full safe suite passed 327 tests. PR #9 review fixes
expanded coverage to 32 settings/dashboard/bot-consumption tests and the full
safe suite passed 337 tests. The final PR #9 review fixes expanded focused
settings/dashboard/bot coverage to 38 tests and the full safe suite passed 343
tests.

## Engineering events and outbox (OPS-014)

The deterministic manager still uses `.git/engineering-workflow.json` as the
workflow authority. `engineering/event_store.py` adds a separate versioned
SQLite event/outbox store at ignored `.agent-state/engineering-events.sqlite3`.
It must never use or modify `trading_bot.db`.

Workflow saves reconcile immutable evidence into deterministic, sanitized
events. Event append and per-destination outbox creation share one transaction;
stable event IDs and `(event_id, destination)` uniqueness make replay safe.
Outbox delivery uses bounded claims, leases, retries, and dead-letter state.
The unavoidable JSON/SQLite crash window is closed by reconciliation on later
saves or completion archival; a reconciliation error is surfaced after the
workflow JSON is already durable.

`EngineeringQueryService` is the shared bounded read model intended for future
Telegram and engineering-dashboard consumers. It exposes no raw agent output,
prompts, environment, secrets, or arbitrary paths. Missing PR and goal records
are reported explicitly rather than inferred. The revisioned pause flag stops
only the bounded engineering driver before dispatch; it does not signal Codex,
control the interactive TUI, or affect the trading bot.

OPS-014 focused tests passed 57 tests and the full safe suite passed 257 tests.
CONFIG-001 remains paused.

## Allowlisted Telegram engineering adapter (OPS-015)

`engineering/telegram_transport.py` is the only real Telegram HTTP boundary.
It uses the fixed `https://api.telegram.org` origin, finite HTTPS and long-poll
timeouts, bounded update batches and messages, and environment-only
`ENGINEERING_TELEGRAM_BOT_TOKEN` and `ENGINEERING_TELEGRAM_JOSH_CHAT_ID`
credentials. Tests always inject a fake transport or replace the HTTP call;
they never use a real token or network request.

`engineering/telegram_adapter.py` accepts only a private message whose chat ID
and sender ID both equal Josh's configured numeric ID. Groups, channels,
forwards, mismatched senders, malformed commands, and oversized commands fail
closed. The only commands are `/status`, `/current`, `/next`, `/report`,
`/pause`, and `/resume`. Read commands use only `EngineeringQueryService` and
return bounded sanitized summaries; they never read raw reports, agent output,
prompts, files, environment, or logs.

The adapter persists its update offset and a single-consumer lease in the
isolated engineering event database. It delivers only the six approved
notification event types from the OPS-014 outbox. Delivery receipts,
retry/backoff, lease recovery, and dead-letter behavior are idempotent and
bounded. Unauthorized access audits contain no inbound content or chat ID.

`engineering/engineering_control.py` is the sole Telegram control layer.
Pause/resume use revision compare-and-set and atomically append audited
manager-paused/resumed events. The existing bounded manager driver reads that
flag before dispatch. It does not signal a process, control Codex/TUI, invoke
Git, affect the trading bot, or represent approval.

OPS-015 focused tests passed 61 tests and the full safe suite passed 291 tests.
No adapter service was installed, enabled, or started. DASH-007, OPS-016, and
CONFIG-001 remain unstarted.

## Bounded Telegram manual smoke launcher (OPS-017)

`python -m engineering.telegram_service` is the only supported manual OPS-015
launcher. It accepts only smoke mode, the external
`/etc/trading-bot/ops-015.env` credential file, the exact isolated
`.agent-state/telegram-smoke-events.sqlite3` store, and exact bounds of 20 polls
and 300 monotonic seconds. It is a finite foreground process: no daemonization,
systemd, endless polling, child process, or automatic restart exists.

Smoke mode must never open, create, read, migrate, lock, or modify the normal
`.agent-state/engineering-events.sqlite3` database. Its query, control, audit,
offset, lease, and delivery state all use the isolated smoke database. The
pre-smoke isolated pause boolean is restored in unconditional cleanup after
success, failure, signals, competing pollers, and either finite bound.

The launcher reads exactly `ENGINEERING_TELEGRAM_BOT_TOKEN` and
`ENGINEERING_TELEGRAM_JOSH_CHAT_ID` from a regular, nonsymlink, operator-owned
0600 file. Structured JSON stderr logs contain only allowlisted bounded fields
and fixed reason codes. Exit codes are 0 success, 2 configuration, 3 competing
poller, 4 permanent Telegram failure, 5 runtime/cleanup/incomplete sequence,
130 SIGINT, and 143 SIGTERM.

OPS-017 automated focused tests passed 67 tests and the full safe suite passed
317 tests. No real token, external secret file, Telegram request, smoke state,
push, or PR was created. OPS-017 remains BLOCKED pending Josh's separate
approval for external secret provisioning and the real seven-interaction smoke
sequence. DASH-007, OPS-016, and CONFIG-001 remain unstarted.

## Engineering Platform Roadmap Approved 2026-08-05

ENGDASH-004 is merged through PR #13 at merge commit
`31f455fb04a6ffff7adbec2bfbf743bc4b1ac1ed`. The next approved
engineering-platform priority order (reconciled to current `main` `cb30809`) is:

1. `ENGPLAT-001` — Project Registration and Managed-Project Configuration ✅ DONE
2. `ENGPLAT-002A` — ProjectContext Contracts and Composition Boundary ✅ DONE
3. `ENGPLAT-002B` — Local Read Adapters and Manager Integration ✅ DONE
4. `ENGDASH-005` — Engineering Timeline and Historical Activity ✅ DONE
5. `ENGPLAT-002C1` — Git Adapter Implementation (Slice 1 of ENGPLAT-002C) ✅ DONE
6. `ENGPLAT-002C2` — QAAdapter Implementation (Slice 2 of ENGPLAT-002C) ✅ DONE
7. `ENGPLAT-002C3` — FileReadAdapter Implementation (Slice 3 of ENGPLAT-002C) ✅ DONE
8. `ENGPLAT-002C Generic QA` — npm/vitest-safe QA execution ✅ DONE
9. `ENGPLAT-003A` — Project Bootstrap Planning + Filesystem Creation ✅ DONE
10. `ENGSUP-001` — Automated Engineering Supervisor Phase 1/2 ✅ DONE
11. `ENGPLAT-003B` — Project Registry Persistence / Activation ✅ DONE
12. `ENGDASH-006` — Live Agent Activity and Execution Visibility 🟡 REVIEW
13. `ENGCTRL-001` — Safe Engineering Control Panel
14. `CONFIG-002` — Dashboard-to-engine synchronization
15. `ENGPLAT-004` — Reusable Engineering Platform Repository Extraction (explicitly deferred)

Important constraints:

- `ENGPLAT-002` depends on `ENGPLAT-001`.
- `ENGDASH-005` depends on `ENGPLAT-001` and `ENGDASH-004`; should consume the
  project boundary from `ENGPLAT-001` where practical.
- `ENGSUP-001` depends on `ENGPLAT-001` and `ENGPLAT-002C`; Phase 1 (prompt
  generation) begins after adapters are proven; auto-dispatch requires separate
  Phase 2 approval. ENGPLAT-002C is merged; ENGSUP-001 Phase 1 and Phase 2 are merged through PRs #43-#46, with auto-dispatch disabled by default.
- `ENGDASH-006` is implemented in the pending review branch as a read-only dashboard/query slice. It depends on `ENGDASH-004`, avoids a competing workflow-state model, and derives live/recent activity from existing workflow, delegation, driver, event, query-service, and persisted runtime records.
- `ENGCTRL-001` follows stable dashboard/query boundaries after `ENGDASH-005`
  and `ENGDASH-006`, and requires separate Josh approval after read-only design
  review.
- `ENGPLAT-003B` is complete through PRs #47, #49, #50, and #51. Bootstrap still has no authorized CLI or registry mutation command.
- `ENGPLAT-004` extraction remains deferred; do not start extraction until configuration and adapter boundaries have been proven in normal use and Josh approves separate cross-repository planning.
- `CONFIG-002` stays queued behind the platform work unless Josh changes the
  priority.
- New roadmap tasks are non-executable until each receives narrow allowed areas
  and explicit Josh approval. Do not use the roadmap as authorization for broad
  rewrites, runtime migration, API controls, deployment changes, secrets work,
  trading behavior changes, or repository extraction.

### Live Agent Activity Dashboard (ENGDASH-006)

ENGDASH-006 adds normalized read-only dashboard activity summaries without a new
activity-state store. `dashboard_api.engineering_read_model.AgentActivitySummary`
represents the current workflow/delegation/driver-derived activity, and
`RecentExecutionSummary` represents bounded recent completed, failed, or timed-out
executions. The existing `/api/engineering/snapshot` payload now carries
`live_activity` and `recent_executions`; the HTML dashboard renders "Live agent
activity" and "Recent executions" sections. The initial `GET /engineering` response
renders the dashboard shell and current snapshot, then a bounded client-side poll
updates the visible sections from `GET /api/engineering/snapshot` every 15
seconds without full-page navigation or reload.

Safety boundary: activity status is derived at read time from existing
`StoredWorkflow`, `DelegationRecord`, `DriverRecord`, query-service snapshot
fields, and bounded event projections. The dashboard must not expose prompts,
private reasoning, raw stdout/stderr paths/content, secrets, credentials,
arbitrary shell output, live process inspection, or write controls. Default
`TRADING_BOT_PROJECT` dashboard routing remains a v1.1 limitation; explicit
provider configuration continues to preserve project isolation.

### Project Configuration Contract (ENGPLAT-001)

ENGPLAT-001 is implemented. `engineering/models.py` contains the typed
`ProjectConfig`, `GovernanceFiles`, `WorkflowFiles`, `ProjectRegistry` frozen
dataclasses; `parse_project_config()` (structural parsing); `validate_project_config()`
(semantic validation); and `TRADING_BOT_PROJECT` constant.

Key design points:
- `parse_project_config(mapping) -> ParseResult`: structural errors only
  (missing fields, unknown fields, type errors, schema version)
- `validate_project_config(config) -> list[str]`: semantic errors only
  (path safety, file existence, QA safety, policy conflicts)
- `ProjectRegistry.from_projects(list)`: raises `DuplicateProjectId` on collision
- Schema version `"1.0"`; unknown versions are rejected
- QA safety: rejects destructive, live-trading, shell-operator, and secret-printing commands

See `AGENT_BACKLOG.md` ENGPLAT-001 for the full contract table, validation
rules, acceptance criteria, and implementation risks.

---

## ProjectContext Architecture (Post-ENGPLAT-001)

ENGPLAT-001 delivered the typed `ProjectConfig` contract but no service consumes it.
The platform has the vocabulary but not yet the grammar of project-agnostic design.

ENGPLAT-002A (merged, audited PASS) defines the `ProjectContext` contract:
a read-only runtime dependency container encapsulating all project-specific
dependencies. Services receive a `ProjectContext` rather than constructing their
own paths. Factory contract: Option B (concrete construction deferred to 002B).

ENGPLAT-002B (governance in revision — PR #26) implements concrete
GovernanceAdapter, WorkflowAdapter, EventAdapter, and integrates `manager.py`
with `ProjectContext`. Corrected design decisions (post Josh review):

- `main()` is a clean deprecation shim: emits one bounded `DeprecationWarning`,
  then delegates entirely to `_manager_main(TRADING_BOT_PROJECT)`. No hardcoded paths.
- `_manager_main(config)` uses `build_project_context(config)` and concrete adapters.
  Single authoritative event-store path from `config.workflow_files.event_store_path`.
- `CapabilityUnavailable` replaces `NotImplementedError` for deferred capabilities.
- `EventAdapterImpl` uses lazy construction: no filesystem side effect at factory time.
- No data at old `.agent-state/engineering-events.sqlite3` path; no migration needed.

### The Architectural Rule

> **No engineering service may directly access repository-specific filesystem paths,
> filenames, or repository names except through approved platform adapters.**

The current named-file list covers existing services: `manager.py`, `backlog.py`,
`reporter.py`, `qa_runner.py`, `event_store.py`, `workflow_store.py`,
`query_service.py`, `git_service.py`, `config.py`. Future services
(ENGDASH-005, ENGSUP-001, ENGDASH-006, ENGCTRL-001) must follow the same pattern.

### ProjectContext composition

```
ProjectContext
├── config: ProjectConfig          # The typed project configuration
├── git: GitReadAdapter           # Read-only Git operations (mutations deferred)
├── governance: GovernanceAdapter  # Governance document access
├── workflow: WorkflowAdapter      # Workflow and event persistence
├── qa: QAAdapter                 # QA configuration (execution deferred to 002B)
├── files: FileReadAdapter         # Bounded filesystem access (writes deferred)
├── events: EventAdapter           # Bounded event operations
└── metadata: ProjectMetadata       # Project identity and policy
```

### Propagation Rule

The application entry point (manager) receives `ProjectContext`.
Downstream services receive only the narrow adapter or data dependency they require.
Do not pass the entire `ProjectContext` into every service by default.
This avoids replacing one global dependency with a `ProjectContext` "god object."

### Factory Contract

`build_project_context(config)` must:
- Validate the `ProjectConfig` internally via `validate_project_config()`
- Fail closed on semantic validation errors (deterministic, sanitized errors)
- Perform no side effects: no file creation, no network calls, no Git mutation

### Foundation Platform Contracts

Three contracts form the foundation of the engineering platform:

| Contract | Defined by | Purpose |
|---|---|---|
| `ProjectConfig` | ENGPLAT-001 | Describes a managed project (schema, paths, governance files, workflow files, QA config) |
| `ProjectContext` | ENGPLAT-002A | Provides project-scoped engineering services via adapter Protocol types |
| `EvidenceBundle` | ENGSUP-001 Phase 1 | Provides verified decision evidence for supervisor workflows |

`ProjectConfig` is the input. `ProjectContext` is the runtime service container.
`EvidenceBundle` is the supervisor's verified evidence record.

### Adapter protocol responsibilities

| Adapter | Wraps | Path consumed from | Migration phase |
|---|---|---|---|
| `GitReadAdapter` | `GitService` | `config.repository_root` | 002C |
| `GovernanceAdapter` | `backlog.py`, file reads | `governance_files.*_path` | 002B |
| `WorkflowAdapter` | `WorkflowStore`, `EventStore` | `workflow_files.*_path` | 002B |
| `QAAdapter` | `QA_runner` | `qa_commands`, `qa_timeout_seconds` | 002A (Protocol defined); execution (run_qa) in 002C |
| `FileReadAdapter` | Direct filesystem access | `config.repository_root` | 002C |
| `EventAdapter` | `EngineeringEventStore` | `workflow_files.event_store_path` | 002B |

### Service migration matrix

| Service | Problem | Phase | Notes |
|---|---|---|---|
| `manager.py` | `Path.cwd()`, hard-coded paths | **002B** | Receives `ProjectContext`; passes adapters to sub-services |
| `workflow_store.py` | DI with `state_path: Path` | Later | Already path-injected; wrap or compose rather than rewrite |
| `event_store.py` | `DEFAULT_EVENT_STORE_PATH` constant | **002B** | Adapter composition via WorkflowAdapter |
| `query_service.py` | DI; needs wiring | 002C | Already DI-compliant; wire to `ProjectContext` |
| `git_service.py` | Already DI with `repo_root: Path` | 002C | Wrap in `GitAdapterImpl` |
| `reporter.py` | Hard-coded `RISKS`, `NEXT_ACTION` | 002C | Use `GovernanceAdapter` for strings |
| `qa_runner.py` | `QA_TIMEOUT_SECONDS = 300` constant | 002C | Use `QAAdapter.timeout_seconds()` |
| `config.py` | `REQUIRED_REPOSITORY_PATHS` hard-coded list | 002C | Validate against `governance_files` |
| `backlog.py` | Hard-coded path logic | 002B | Covered by GovernanceAdapter; no direct migration needed |
| `codex_cli_wrapper.py` | Default runtime `.agent-state/codex-runs` | Later | Accept `runtime_root` from config or env |

### Backward-compatibility shim

`TRADING_BOT_PROJECT` constant feeds the adapter factory for the trading-bot project.
A `DeprecationWarning` is emitted when the shim is used. Removal milestone:
"after ENGPLAT-002C is complete AND a second managed project has passed integration
testing" — not a fixed calendar date.

### Extraction readiness (ENGPLAT-004)

The platform should not be extracted until all 16 readiness criteria are met
(defined in AGENT_BACKLOG.md ENGPLAT-004). Key criteria:
- All services consume `ProjectContext` via adapters (002A through 002C)
- Adapter signatures stable through ≥3 normal workflow runs
- At least one non-trading-bot managed project exists
- Supervisor Phase 1 operational (ENGSUP-001 Phase 1)
- Engineering timeline operational (ENGDASH-005) ✅ DONE
- Versioning, authentication, migration plans approved
- Josh separately approves cross-repository planning

### Automated Engineering Supervisor (ENGSUP-001)

ENGSUP-001 replaces the manual copying of agent reports and next-step prompts
with a governed supervisor service. The supervisor consumes structured completion
packets, independently verifies evidence, selects the next permitted workflow
transition, and generates bounded instructions — while always preserving explicit
human approval gates.

Key design elements:

- **Structured completion packet**: typed, versioned record containing project ID,
  task ID, workflow/run ID, stage, branch/commit/status, files changed, test
  results, report paths, PR metadata, risks, blockers, requested human action,
  prior findings, and iteration count. Retains Markdown reports for humans;
  supervisor does not infer state from prose.

- **Supervisor decision contract**: typed outcomes (CONTINUE, RUN_QA,
  RUN_READ_ONLY_REVIEW, RETRY, REQUEST_CHANGES, WAIT_FOR_HUMAN_APPROVAL,
  READY_FOR_MERGE_APPROVAL, BLOCKED, ESCALATE_POLICY_CONFLICT, COMPLETE).
  Each decision includes reason codes, supporting evidence, next agent role,
  bounded instruction, and human-approval flag.

- **Independent evidence verification**: supervisor verifies git status, commit,
  branch, diff, test results, PR state independently — never trusts agent
  summaries as sole source of truth.

- **Human approval gates**: supervisor always stops for Josh before merge,
  new task authorization, scope expansion, destructive Git, deployment, secret
  changes, live trading, safety policy changes, and repository extraction.

- **Automatic transitions**: routine transitions identified for Phase 2
  auto-dispatch (e.g., QA_PASS → REVIEW). Auto-dispatch disabled by default;
  requires per-transition Josh approval.

- **Loop protection**: max 3 implementation-review cycles, identical finding
  detection, scope-drift detection, stale decision expiry, dirty-tree prevention.
  Supervisor escalates rather than looping indefinitely.

- **Privacy**: supervisor does not expose chain-of-thought; note capped at
  5 sentences; evidence blobs sanitized and truncated at 50 lines.

- **Phased implementation**: Phase 1 (prompt generation / typed supervisor) and
  Phase 2 (routine auto-dispatcher with default kill switch) are merged. Phase 3
  dashboard approval inbox and Phase 4 multi-project supervisor remain future work.

See `AGENT_BACKLOG.md` ENGSUP-001 for the full backlog entry including schemas,
matrices, acceptance criteria, planned tests, and risks.

---

## ENGPLAT-003A Bootstrap Capability

ENGPLAT-003A adds a library-only bootstrap module in `engineering/bootstrap.py`.
It plans and applies creation of a new managed-project skeleton at an explicit
destination. It returns an in-memory `ProjectConfig` through bootstrap result
objects and does not persist that config.

Important boundaries:
- API surface: `plan_bootstrap(...)` and `apply_bootstrap(...)`.
- Destination is explicit; there is no `Path.cwd()` fallback or implicit repo discovery.
- Successful apply creates exactly five files: `AGENTS.md`, `AGENT_BACKLOG.md`,
  `AGENT_OPERATING_PLAN.md`, `OWNERS.md`, and `AUTONOMOUS_ENGINEERING_HANDOFF.md`.
- Templates must stay generic. Do not add trading-bot identifiers, brokerage
  assumptions, trading-specific policies, trading paths, Josh-specific defaults,
  secrets, or fantasy-app content.
- Known validation or conflict failures perform zero writes. Unexpected write
  failures are fail-fast and may leave partial state; report written paths and
  the failed target without claiming rollback.
- Bootstrap preflight validates intrinsic `ProjectConfig` semantics before
  managed artifact writes. Existing runtime-readiness validation still belongs
  to runtime components; do not create `engineering/`, `reports/`, or other
  runtime directories merely to satisfy readiness checks in 003A.
- 003A deliberately does not implement registry persistence, activation, CLI,
  overwrite/force behavior, Git initialization, reports directory creation,
  `.agent-state`, `.gitignore`, `pyproject.toml`, `pytest.ini`, or QA execution.

ENGPLAT-003B is now complete for registry loading, project activation, runtime directory initialization, manager `--project-id` selection, and repository-root propagation. Bootstrap still has no authorized CLI or registry mutation/update command.

### Mobile-first Engineering Dashboard command center

The `/engineering` page is a mobile-first read-only command center. It renders a compact Overview by default and organizes deeper data into six client-side tabs: Overview, Activity, Backlog, Timeline, Reports, and Health. The tab bar is sticky on narrow screens. The page still uses the existing smooth-refresh polling model: `GET /api/engineering/snapshot` every 15 seconds, no full-page meta refresh, no WebSockets, no server push, and no write/control actions.

The Overview tab intentionally contains only high-value status fields: health, project, branch, repository safety, current task/agent/execution/phase, elapsed/latest activity/last completed action, blocker summary, and DONE/REVIEW/TODO/BLOCKED backlog counts. Detail tabs render activity, backlog, events, reports, and health as compact cards/lists rather than wide tables. Selected tab state is preserved during polling and stored in browser `localStorage` across ordinary reloads. Dashboard rendering must continue to escape all snapshot values and must not expose raw stdout/stderr, prompts, private reasoning, secrets, unbounded logs, process inspection, or write controls.

### Engineering Dashboard Chat tab

The `/engineering` dashboard includes a seventh tab, Chat. Slice 1 added bounded
history reading through `dashboard_api.chat_gateway.GatewayChatHistoryClient`,
which calls Gateway `chat.history` server-side for a fixed/resolved OpenClaw
`trading-manager` session and exposes only bounded visible fields to the browser
(`role`, `text`, `timestamp`, plus session `agent`/`status`). Slice 2 adds the
only dashboard chat write path: `POST /api/engineering/chat/send`, which accepts
exactly `{ "message": "..." }`, trims text, rejects empty/non-text/over-4,000
character payloads, and calls Gateway `chat.send` server-side.

The shared manager target is fixed to `trading-manager`; the preferred shared
Telegram-named session key is `agent:trading-manager:telegram:direct:8455029949`.
As of Slice 2 verification the current authoritative session id is
`fc12bb64-f6f9-458b-84a7-d7414e0a7196` and the configured/session model is
`minimax-portal/MiniMax-M3`. Earlier Slice 1 metadata (`MiniMax-M2.7` and session
id `e31c5f61-2d71-4078-bc59-eab62ef92067`) was stale; do not hardcode stale
session ids. If the exact key moves, the adapter resolves the current
`trading-manager` session server-side and never allows browser-supplied agent
IDs, session keys/session ids, Gateway URLs, tokens, or RPC methods.

Do not add abort, interrupt, arbitrary agent/session selection, generic Gateway
RPC, workflow controls, Git/merge/deploy controls, brokerage/trading controls,
direct browser Gateway access, private model/tool data exposure, or a second
conversation database without a separate approved slice. The Chat tab must not
expose private reasoning/thinking blocks, hidden prompts, tool arguments/results,
raw stdout/stderr, secrets, credentials, or unbounded transcript content.

Chat UI state is intentionally client-persistent across ordinary dashboard
snapshot polling. `/api/engineering/snapshot` polling still replaces the broader
`#dashboard-content` shell, so the refresh script must capture and restore Chat
history/status/textarea/send-button/scroll state around `renderSnapshot()` when
Chat is selected. Do not reintroduce a design where `renderSnapshot()` exposes
`chatTab()`'s initial `Loading trading-manager history…` markup over already
loaded messages during background polling.

Chat compose state is separate from history/manager-response state. `chat.send`
success must clear the textarea and return controls to idle as soon as the
Gateway accepts the message; it must not wait for `chat.history`, manager reply
arrival, or a later polling cycle. If snapshot polling replaces the Chat DOM
while a send is in flight, send completion must update the current DOM from the
persistent compose cache, not stale detached textarea/button references.

### Chat agent working indicator (live run-state)

The Chat tab renders a compact `<div id="chat-status" data-agent-status="...">` pill directly under the existing chat-state banner and above the message list, so it stays visible above the fold on mobile. The label is one of:

- `Trading manager · Idle` (gray dot) — chat-history returned `sessionInfo.hasActiveRun=false` and `sessionInfo.status` is not in `{failed,killed,timeout}`.
- `Trading manager · Working…` (amber dot, 1.05s pulse animation) — `sessionInfo.hasActiveRun=true`.
- `Trading manager · Failed` (red dot) — `sessionInfo.status ∈ {failed, killed, timeout}`.
- `Trading manager · Loading…` (gray dot) — first-load placeholder before the first successful poll.

Authoritative source is the existing `/api/engineering/chat/history` JSON which already carries `session.has_active_run` and `session.run_status` (projected from `chat.history`'s `sessionInfo.hasActiveRun` and `sessionInfo.status` in `dashboard_api.chat_gateway._project_gateway_history`). The polling cadence is the existing 15-second `setInterval(refreshChatHistory, CHAT_POLL_INTERVAL_MS)`. No WebSocket, no new RPC, no new OpenClaw session, no new database.

Compose state (PR #61) and run state are strictly separated:

- Compose state (`idle`/`sending`): driven by `setComposeState()` only.
- Run state (`idle`/`working`/`failed`/`loading`): driven by `setAgentStatus()` only, projected from authoritative Gateway data.
- A failed `chat.send` (or a send that returns `ok=false`) NEVER sets Working. Only a successful `chat.send` followed by a subsequent poll showing `has_active_run=true` projects Working.

Snapshot-poll survival: `chatStateCache.agentStatus` is captured by `captureChatUiState()` before `#dashboard-content.innerHTML` is replaced and re-applied by `renderAgentStatus()` inside `restoreChatUiState()`. The `data-agent-status` attribute on `#chat-status` is the DOM source of truth that survives snapshot replacement.

Temporary poll failure: `refreshChatHistory()` catches and re-routes through `renderChatHistory({session: status: "unavailable"})`. This branches into `setChatState('Chat history unavailable; keeping last known messages.', true)` which updates ONLY the chat-state banner. `setAgentStatus()` is NOT called in this branch — Working is preserved across temporary poll failure. There is no client-side timer. "Interrupted" is not projected from elapsed time; only terminal `sessionInfo.status` values produce the Failed indicator.

Hard privacy rules (re-stated): the run-state indicator must never expose `inFlightRun.text` (streamed model reasoning), raw `chat.history` keys, raw Gateway internals, prompts, tool arguments, raw stdout/stderr, secrets, or `run_id`. `dashboard_api.chat_gateway._project_gateway_history` intentionally reads only `sessionInfo.hasActiveRun` (bounded to literal `True`) and `sessionInfo.status` (clamped to `ALLOWED_RUN_STATUSES = {"running","idle","done","failed","killed","timeout"}`). Unknown values become `False`/`None`; the browser only ever sees `has_active_run: bool` and `run_status: string|null`.
