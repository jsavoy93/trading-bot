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
