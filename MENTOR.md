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

**Engineering Dashboard:** `dashboard_api/app.py` (FastAPI) runs under a
user-level systemd service (`dashboard.service`, loopback `127.0.0.1:8010`)
mirroring the `openclaw-gateway.service` layout. Reboot-survival via
`Linger=yes` for the root user. See `docs/infrastructure/dashboard-systemd.md`.

**Cloudflare Tunnel + Access (PR2):** `cloudflared` runs as a second
user-level systemd service (`~/.config/systemd/user/cloudflared.service`,
egress-only, never `0.0.0.0`). Tunnel credentials live in
`/root/.cloudflared/<TUNNEL_ID>.json` (chmod 0600) and the dashboard
runtime env (`.dashboard.env` + `.cloudflared.env`) — none are
committed. `dashboard_api/security.py` validates the
`Cf-Access-Jwt-Assertion` against the team's published JWKS, enforces
Origin/Referer on mutating routes, and rate-limits
`/api/engineering/chat/send`. The `/healthz` endpoint is localhost-only
(it rejects requests with `Cf-Connecting-Ip` so tunneled requests are
never confused with direct loopback). See
`docs/infrastructure/cloudflare-tunnel-access.md`.

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

### The score components (daily, SCORE-001 normalized)

The published **`total_score` is always in 0..100**, centered at 50, and
is the result of `_clamp_total_score(50 + sum(components))` on a signed
raw sum that is preserved internally for SELL detection.

| Component | Documented range (BEFORE final clamp) | Formula |
|---|---|---|
| RSI | -25 .. +25 | `25 - (RSI / 2)`; RSI=0 → +25, RSI=50 → 0, RSI=100 → -25 |
| SMA | -25 .. +25 | `% separation × 5`; ±25 at 5% separation, saturates beyond |
| MACD | -25 .. +25 | `(macd_histogram / ATR) × 25`; 1 ATR of histogram = ±25 |
| Bollinger Bands | -25 .. +25 | `25 - (bb_position × 50)`; lower band = +25, upper band = -25 |
| Catalyst | 0 .. +25 | additive bonus (gap-up / volume surge) |
| Regime | -20 .. +20 | market-regime penalty/bonus (optional) |

After the documented low-volatility RSI multiplier (1.3x) or
high-volatility SMA multiplier (1.3x) the affected component can
briefly span ±32.5 in either direction. That is the documented worst
case; the final `_clamp_total_score()` is the authoritative 0..100
guard and keeps the published `total_score` contract intact regardless
of intermediate inputs.

The MACD formula is **dimensionless** (macd_histogram / ATR), so a
high-volatility symbol and a low-volatility symbol receive comparable
MACD contribution and MACD cannot dominate via raw price scale.

Monotonicity contract: stronger bullish evidence (lower RSI, wider
positive SMA separation, larger positive MACD, price closer to lower
BB) NEVER reduces the score. Stronger bearish evidence NEVER increases
it. See `tests/test_smart_bot_score_normalization.py` for the
proof.

### BUY signal requirements (SCORE-002 — non-score strategy gates)
- RSI < `rsi_buy_threshold` (oversold, default 30)
- SMA in uptrend (fast > slow)
- MACD histogram positive
- Volume ≥ average (when `enable_volume_confirmation` is True)

If ALL of these pass → candidate is **BUY-eligible**. Score does
**not** gate eligibility; total_score only ranks otherwise-eligible
BUY candidates. min_score_buy is preserved in the schema as a
deprecated setting for backward compatibility but is no longer
consulted by the BUY/SELL/HOLD decision.

If ANY non-score gate fails → HOLD (logged to `failed_analyses`).

### Score as rank (SCORE-002)
- total_score (published 0..100) is the quality rank for eligible BUY
  candidates.
- Eligible BUY candidates are sorted by total_score DESC, then symbol
  ASC for deterministic tiebreak.
- The top `max_trades` of the sorted list are executed; the rest are
  skipped (`no_trade_reasons['max_trades_reached']`).

### Score as sizing (unchanged)
- total_score still derives STRONG (≥65) / MEDIUM (<65) signal
  strength.
- calculate_position_size() maps STRONG→2.0%, MEDIUM→1.5%, WEAK→1.0%
  of portfolio. Sizing is rank-derived, not a duplicate gate.

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

## OBS-001 Phase A — Decision Snapshot & Dashboard Observability

**OBS-001 Phase A is OBSERVABILITY ONLY. No new trading gates.
No changes to candidate population, ranks, attempt order,
strategy gates, risk limits, sizing, slot semantics, SELL
behavior, or brokerage behavior.**

The bot persists a structured `decision_snapshot` for every
analysis and a `cycle_funnel` row at the end of each cycle.
The dashboard reads the snapshot rather than recomputing from
raw indicators. This makes the bot's actual decision process
auditable, including for historical rows that pre-date Phase A.

### Snapshot schema (deployed version)

`decision_snapshot` is a JSON blob stored in
`analyzed_stocks.decision_snapshot`. The deployed schema version
is `1`. Legacy rows have `decision_snapshot IS NULL` and render
as "Legacy analysis — detailed decision trace unavailable".

Snapshot blocks:

| Block | What it records |
|---|---|
| `identity` | symbol, timestamp, session_id, bot_version, timeframe_mode, cycle_id |
| `strategy_eligibility` | non-score strategy gates; signal/strength from the bot |
| `scoring` | SCORE-001 components, total_score, score_invalid_data |
| `ranking` | SCORE-002 candidate_rank, eligible_candidate_count, tiebreak_basis |
| `selection` | attempted flag, slots_available_at_attempt |
| `execution_checks` | current state at attempt + 9 ordered checks with `_gap_note` |
| `order` | submit_order outcome, slot_consumed, fill_confirmed=False |
| `decision` | canonical outcome enum + primary_reason |
| `baseline_diagnostics` | OBSERVED-ONLY baseline; never affects trading |

### Outcome enum

Currently reachable (10 values): `BUY_ORDER_SUBMITTED`,
`BUY_ORDER_FAILED`, `BUY_ELIGIBLE_NOT_SELECTED`,
`BUY_BLOCKED_DYNAMIC`, `HOLD_INELIGIBLE`,
`SELL_ORDER_SUBMITTED`, `SELL_ORDER_FAILED`,
`SELL_BLOCKED_NO_POSITION`, `SELL_BLOCKED_DYNAMIC`,
`SKIPPED_INVALID_DATA`.

Reserved (5 values; NEVER produced by current code):
`BUY_FILLED`, `SELL_FILLED`, `BUY_BLOCKED_PRE_RANK`,
`SELL_BLOCKED_PRE_RANK`, `BUY_PRE_RANK_EXCLUDED`.

### Slot-consumption semantics (v1)

`slot_consumed=True` iff `submit_order` returned an order
object. NOT a fill confirmation. Future fill-polling would bump
`slot_consumed_semantics_version` to 2. `fill_confirmed` is
always `False` in Phase A.

### Cycle funnel (current behavior, no invented counters)

| Counter | Definition |
|---|---|
| `analyzed_count` | Symbols that completed L1 analysis |
| `strategy_eligible_count` | BUY or SELL signal produced |
| `ranked_candidate_count` | Added to `buy_candidates` (BUY only) |
| `execution_attempt_count` | `execute_trade` was called |
| `execution_blocked_count` | `execute_trade` returned False |
| `order_submission_attempt_count` | `submit_order` was reached |
| `order_submitted_count` | `submit_order` returned an order |
| `order_failed_count` | `submit_order` raised or returned None |
| `not_attempted_count` | Ranked but never reached `execute_trade` |
| `not_attempted_reason` | Only `slots_filled` is currently proven |

Invariants (mathematically true for current runtime):
- `analyzed_count >= strategy_eligible_count >= ranked_candidate_count`
- `ranked_candidate_count == execution_attempt_count + not_attempted_count`
- `execution_attempt_count == execution_blocked_count + order_submission_attempt_count`
- `order_submission_attempt_count == order_submitted_count + order_failed_count`

`pre_rank_actionable_count` is intentionally NOT a column.
There is no active pre-rank gate in current code.

### exposure-fidelity gaps (current behavior, not bugs)

Each `_gap_note` on `execution_checks.checks[]` documents where
current code does NOT include pending exposure:

- `position_concentration_check`: reads `get_open_position(symbol)`;
  does NOT include pending adds from earlier candidates.
- `sector_concentration_check`: reads `get_all_positions()` via
  `get_sector_allocation()`; does NOT include pending exposure.
- `correlation_check`: reads `get_all_positions()`; does NOT include
  pending exposure.
- `beta_check`: reads `get_all_positions()` via
  `get_portfolio_beta()`; does NOT include pending exposure.
- `buying_power_check`: reads `account.cash`; whether cash is reserved
  after `submit_order` is NOT empirically tested.

These gaps are documented honestly. They are NOT patched inside
OBS-001 Phase A; patching requires separate Josh-approved tasks.

### Trace collector (post-corrigendum architecture)

The OBS-001 trace is populated by the **real `execute_trade` path** at
the EXACT location of each existing check. There is NO duplicate check
run for observation. The trace collector is a passive side-channel:

- `_obs_001_begin_attempt(symbol, signal)` is called immediately before
  `execute_trade`. It opens a fresh empty trace.
- Inside the real `execute_trade`, every existing check calls
  `_obs_001_trace_record(name, applied, passed, observed_value,
  threshold_value, reason, gap_note)` adjacent to the check. The trace
  records the actual values used by the real code.
- `_obs_001_finalize_attempt(returned)` is called immediately after
  `execute_trade` returns. It back-fills `applied=False, passed=None`
  entries for the checks the real code did not reach (due to
  short-circuit). Each back-fill carries a reason like
  "NOT RUN: real execute_trade short-circuited before reaching this check"
  or "NOT RUN: cooldown_check applies to BUY only (signal=SELL)".
- The `first_blocking_check` is the FIRST recorded check with
  `passed=False` — i.e. the EXACT check that caused the real
  `execute_trade` to return.

**Forbidden**: do not add a parallel function that re-runs the checks
for observation. Do not reorder checks. Do not change short-circuit
behavior. Do not duplicate broker/API reads.

### Per-symbol decision_history finalize (post-corrigendum)

`_persist_obs_001_decision_snapshot(symbol, entry, cycle_id,
cycle_start_iso)` is called IMMEDIATELY when each symbol's terminal
outcome is known:

- After each SELL inline `execute_trade` call (BUY and SELL inline)
- After each BUY ranked-walk `execute_trade` call
- After each slots_filled SELL/BUY skip
- After each HOLD outcome (in the `elif analysis:` branch)

The `decision_history` row is durable BEFORE the cycle continues. The
UNIQUE(cycle_id, symbol) constraint ensures idempotency if the helper
is called twice for the same symbol. The `cycle_funnel` row is still
written ONCE at cycle end.

### Legacy dashboard fidelity (post-corrigendum)

For rows where `decision_snapshot IS NULL` (legacy rows written before
Phase A shipped), `api_opportunities` reads `signal` and
`signal_strength` directly from the persisted `analyzed_stocks.signal`
and `analyzed_stocks.signal_strength` columns. It MUST NOT re-derive
them from `total_score` thresholds — doing so would rewrite the
meaning of an old analysis under current thresholds. Changing dashboard
score thresholds MUST NOT alter a legacy persisted signal/strength.

### Dashboard History → Recent Sessions (read-model fix)

The Recent Sessions card on the History tab is a **read-model** view
over `trading_sessions` + `decision_history` + `trades`. The fix lives
entirely in `dashboard.get_recent_sessions_with_truthful_counts()` and
the matching template fields. No SmartTradingBot / scoring / execution
behavior changes.

- **Symbols semantic** (per row in the table):
  - If `decision_history` has any rows for the session:
    `Symbols = COUNT(DISTINCT decision_history.symbol)`. Repeated
    analysis of the same symbol across multiple cycles counts ONCE
    (the DISTINCT semantic). This is the authoritative post-OBS-001
    value.
  - Else (legacy session with no OBS-001 decision rows):
    `Symbols = trading_sessions.total_symbols_processed`. The legacy
    scalar is only consulted when `decision_history` has no rows for
    the session — it is NOT used when decision_history exists.

- **Trades semantic** (per row):
  - `Trades = COUNT(trades.id) WHERE trades.session_id = X`.
  - The legacy `trading_sessions.total_trades_executed` scalar is NOT
    consulted; the `trades` table is the only source. Historical
    pre-OBS-001 trades that cannot be reliably associated with a
    specific session are simply not counted (rather than invented).

- **Future-fixture filter (BOT-003-style skew)**:
  - Rows where `session_start > now(UTC) + 300 seconds` are excluded
    from Recent Sessions. This is the same 5-minute future bound that
    BOT-003 uses for active-session selection.
  - The filter is **read-layer only**. No `trading_sessions` row is
    mutated, hidden, or deleted. Session 71804 (the `2099-01-01`
    fixture seeded by `tests/test_bot003_active_session_selection.py`)
    remains queryable directly via `db.get_sessions()`; it simply does
    not qualify for the normal Recent Sessions read model.

- **Sort**: after filtering, `ORDER BY session_start DESC, id DESC`.
  The id tiebreak makes ordering deterministic when two sessions share
  the same `session_start`.

- **Session → Cycle → Decision** (preserved understanding):
  - `trading_sessions` is one row per logical session.
  - `decision_history.cycle_id` groups rows within a session.
  - `decision_history.symbol` is one row per symbol per cycle.
  - In current runtime one session is approximately one cycle (the bot
    calls `start_session`/`end_session` per loop iteration). The
    DISTINCT semantic is correct for this shape AND scales correctly if
    a future session spans multiple cycles.

- **Implementation contract**:
  - File: `dashboard.py` defines
    `get_recent_sessions_with_truthful_counts(limit,
    max_future_skew_seconds=300)`.
  - File: `templates/dashboard.html` reads `session.symbols_count` and
    `session.trades_count` for Recent Sessions.
  - File: `tests/test_dashboard_recent_sessions_fidelity.py` proves the
    nine acceptance criteria (DISTINCT semantic, legacy fallback,
    trades-table count, fixture exclusion, fixture unchanged in DB,
    deterministic sort, zero-trade rows, no mutation, no bot-runtime
    surface).
  - The bot-side baseline bug in
    `SmartTradingBot.start_session`/`end_session` (which makes
    `total_symbols_processed` always 0 after the first session) is
    **deliberately NOT fixed here**. It is documented as a separate
    task.

### Dashboard Phase A — History Symbol Search + OBS-001 Decision Trace (2026-09-13)

Phase A is the first slice of the dashboard redesign plan. It is
**dashboard-only** and does not change SmartBot, scoring, eligibility,
ranking, sizing, risk, brokerage, systemd, Cloudflare, the database
schema, PIPELINE-001, or SCORE-003.

What Phase A does:

- **Move Symbol Lookup** out of the always-visible global area at
  the top of the page into a full-width **Symbol Search** card at
  the top of the **History** tab. The History tab is now the single
  entry point for per-symbol decision inspection.
- **Render OBS-001 decision snapshots** directly from the existing
  `/api/decision/{symbol}` and `/api/decision-history/{symbol}`
  endpoints. No new HTTP endpoints are introduced.
- **Never re-derive from current settings**. The renderer reads
  the persisted `decision_snapshot` JSON only. It does NOT call
  `/api/score/{symbol}` (live recompute) for the recorded-score
  path, and it does NOT use the legacy `buy_criteria` field as
  primary decision truth.
- **Seven compact expandable sections**: DECISION (open by default),
  STRATEGY GATES, SCORE, RANKING, SELECTION, EXECUTION CHECKS, ORDER.
  Each section is collapsible via the `.dt-section.open` toggle.
- **Strategy gates** render with PASS / FAIL / NOT RUN / N/A
  chips. `NOT RUN` means the gate was not evaluated (data missing,
  or N/A for the current signal). `FAIL` means the gate was
  evaluated and did not pass. The renderer must NOT translate
  missing or not-evaluated checks into failures.
- **Execution checks** render in the exact persisted
  `evaluated_in_order` order, then APPEND any persisted
  `checks[]` entries that were not named in `evaluated_in_order`
  (in their persisted `checks[]` order). The
  `first_blocking_check` row is highlighted (left bar +
  `FIRST BLOCKER` chip) wherever it appears — inside
  `evaluated_in_order` or only inside `checks[]`. Live ALPXR
  (SELL_BLOCKED_DYNAMIC, snapshot v1) has its blocker
  (`position_existence_check`) only in `checks[]` and absent
  from `evaluated_in_order`; the renderer must still surface
  the row and the highlight. The renderer must NOT recompute
  the blocker — it reads the persisted field.
- **Order section** distinguishes SUBMITTED from FILLED/EXECUTED.
  `order.fill_confirmed === true` is the only field that may flip
  a submitted order into a fill state in the UI. The Order
  section includes a literal note: *"submission does not imply
  fill"*.
- **History list** below the latest trace renders the most recent
  10 cycles (`limit=10`). Each row is expandable and renders
  the cached historical snapshot via the SAME renderer that
  produced the latest trace — historical snapshots are immutable
  facts, never reinterpreted using current settings.
- **Legacy fallback**: rows with `decision_snapshot IS NULL`
  render a literal "Legacy analysis — detailed decision trace
  unavailable" message, plus the persisted legacy `signal`,
  `signal_strength`, `total_score`, and `last_analyzed` columns.
- **Unknown symbol fallback**: the literal "No analysis found for
  SYMBOL" is rendered when both `/api/decision/{symbol}` and the
  history endpoint return no rows.
- **Mobile-first**: no element requires horizontal scroll on a
  phone. Header uses `flex-wrap`. Sections stack vertically.
  Decision section is the first thing visible on expand.

Implementation contract:

- File: `templates/dashboard.html` adds a new card
  `#history-symbol-search-card` at the top of
  `#top-tab-history` and removes the legacy
  `#symbol-search-card`. New renderer helpers `_dt_*` are
  scoped under the `doHistorySymbolSearch()` entry point.
- File: `tests/test_dashboard_phase_a_history_symbol_trace.py`
  proves 17 acceptance criteria including: removal of the
  always-visible card; presence and ordering of the new card;
  unique new IDs; reuse of `/api/decision/` and
  `/api/decision-history/`; absence of `/api/search/` and
  `/api/score/` calls in the new code path; seven section
  renderers wired; Decision open by default, others closed;
  submitted ≠ filled; legacy and unknown-symbol fallback
  messages present; history list default 10; historical
  snapshots rendered via the same renderer; `dashboard.py`
  unchanged (no new HTTP routes).
- No `dashboard.py` changes. No `src/` changes. No
  `trading_bot.db` schema changes.

What Phase A does NOT do (deferred to later phases):

- Removal of the Filter Analysis card, Failed Analysis Breakdown
  card, and the Timing / By RSI inner tabs on Analytics.
- Top Opportunities is not yet SCORE-002-ranked.
- Latest Cycle funnel card on Dashboard.
- High-Score Near Misses, Strategy Gate Failure Frequency,
  Execution-Time Blockers, Outcome Distribution widgets on
  Analytics.
- Settings deprecated treatment for `min_score_buy`.
- Per-card silent refresh / SPA-state preservation.
- Symbol drill-down from Recent Sessions / Positions / Orders.

## Original (pre-corrigendum) text below

These gaps are documented honestly. They are NOT patched inside
OBS-001 Phase A; patching requires separate Josh-approved tasks.

### PROPOSED FOLLOW-UP PIPELINE ARCHITECTURE (documentation only)

```
L1 Strategy Eligibility
  → L2 Pre-Rank Portfolio Actionability   [FUTURE — PIPELINE-001]
  → L3 Actionable Ranking
  → L4 Sequential Selection
  → L5 Dynamic Rechecks
  → L6 Order Result
```

L2 is documented here as a PROPOSED FOLLOW-UP. It is NOT active.
Phase A's `baseline_diagnostics.potential_l2_blockers_for_this_symbol`
captures OBSERVED-ONLY evidence to inform the future L2 decision.

### Dashboard endpoints

- `GET /api/opportunities` — reads `decision_snapshot` when
  present; falls back to legacy score-derivation for snapshot=NULL
  rows. Renders canonical decision outcome and rank.
- `GET /api/decision/{symbol}` — full snapshot detail.
- `GET /api/decision-history/{symbol}` — cycle-over-cycle history.
- `GET /api/actionability-summary` — latest cycle_funnel row.

### Storage

- `analyzed_stocks.decision_snapshot TEXT` (nullable; legacy = NULL)
- `analyzed_stocks.decision_schema_version INTEGER DEFAULT 0`
- `decision_history` table (one row per (cycle_id, symbol);
  UNIQUE constraint; INSERT-only)
- `cycle_funnel` table (one row per cycle; UNIQUE constraint;
  INSERT-only)

`_get_rolling_ticker_list()` builds the analysis queue at the start of each cycle:

1. Get ALL US symbols from Alpaca
2. Exclude: portfolio holdings, pending orders, failing symbols (3+ failures)
3. RS-rank the first 300 candidates (relative strength vs SPY)
4. Queue = ranked top 150 + remainder

Each loop processes 30 symbols from the queue, then sleeps 5 minutes.

---

## Phase C `rows_in_cohort` SQL Pattern (PHASE-C14B-2D)

The C14B-2C hybrid analytics path counts parents satisfying
the SELECTED cohort AND the SELECTED range. Both filters must
be applied per version:

```sql
SELECT COUNT(*) FROM decision_history dh
WHERE (v0_cohort_sql AND v0_range_sql)
   OR (v1_cohort_sql AND v1_range_sql)
```

Params must be bound in cohort-then-range order per version:
`(v0_cohort_params + v0_range_params + v1_cohort_params + v1_range_params)`.
The `latest` range adds no `?` (range fragment is a self-contained
subquery), so `v?_range_params` is `()` for `latest` and
`(cutoff,)` for time windows.

**PHASE-C14B-2D bug** (introduced by C14B-2C): the per-version
range fragments and params were dropped, so `rows_in_cohort`
became range-agnostic — returning ~1.31M for every range. Fix at
two call sites: `api_phase_c_strategy_gates` line 2354 and
`api_phase_c_execution_blockers` line 2607 in `dashboard.py`.

**EXPLAIN**:
- Corrected: `MULTI-INDEX OR` → 2× `SEARCH dh USING COVERING
  INDEX idx_decision_history_cycle_start (cycle_start>?)`. Two
  indexed range scans, fast.
- Buggy: `SCAN dh USING COVERING INDEX
  idx_decision_history_cycle_start`. Full index scan over all
  post-OBS-002 (~1.32M rows), ~9s on the index alone.

## Decision Funnel — SELL inline-bypass explanation (PHASE-C14B-2D)

The funnel's forward path visually shows `Ranked Candidates → ... →
Execution Attempted`, but the code has two branches that converge
at `execution_attempt_count`:

1. **BUY branch** (matches visual flow): Strategy Eligible (BUY)
   → appended to `buy_candidates` → `ranked_candidate_count += 1`
   → after analysis loop, ranked → chosen → `execution_attempt_count += 1`
   → `execute_trade()`.

2. **SELL branch** (does NOT match visual flow): Strategy Eligible
   (SELL) → inline `execution_attempt_count += 1` → `execute_trade()`
   → no ranking step.

So `RANKED CANDIDATES = 0` while `EXECUTION ATTEMPTED = N` is
**consistent** when all signals are SELL: zero BUY signals → zero
ranked; N SELL signals → N inline `execute_trade` calls. The funnel
label is technically accurate but visually misleading because it
places Execution Attempted strictly downstream of Ranked Candidates
without annotating the SELL inline-bypass.

`strategy_eligible_count == execution_attempt_count == execution_blocked_count`
is also consistent: every SELL signal that triggered `execute_trade`
resulted in `execute_trade` returning False (all blocked by
position existence check).

## v0 JSON extraction performance bottleneck (PHASE-C14B-2D)

The v0 analytics path reads from `decision_history.decision_snapshot`
JSON via `json_each(json_extract(...))`. This is inherently slow
at large scale:

- 24h v0 (24h contains 0 v0 parents, but path still runs): <1s
- 7d v0 (~776k v0 parents): ~50-56s for `strategy-gates 7d`

EXPLAIN shows `SCAN gate VIRTUAL TABLE INDEX 1` (json_each) over
every row in the range window. The `rows_in_cohort` fix does NOT
eliminate this cost; it just makes it range-limited.

Mitigation deferred: materialize v0 facts at write-time to eliminate
runtime JSON parse, OR add a v0 child-table parity so v0 reads
from a child table like v1 does.

## Single-worker uvicorn concurrency (PHASE-C14B-2D)

`trading-dashboard.service` runs uvicorn without `--workers`:
- Single uvicorn process (PID 1122171), 7 threads (asyncio default)
- systemd `ExecStart` has no `--workers`
- Synchronous SQLite/FastAPI handlers serialize on the asyncio
  event loop

When multiple expensive requests arrive concurrently (e.g., user
hitting Analytics tab while the bot is running), requests queue
and last request can take very long (148s observed during C14B-2B
deploy). The rows_in_cohort fix reduces individual request
duration but does not address concurrency.

**Do NOT increase worker count** without first measuring SQLite
contention — multiple workers multiply SmartBot contention on the
same DB.

## Legacy warning UX — data-unaware (PHASE-C14B-2D)

`templates/dashboard.html:3591-3638`: the "Legacy history included"
warning visibility is purely controlled by the
`phase-c-include-legacy` checkbox. It shows whenever `cohort=all`
is selected, regardless of whether the time window contains
pre-OBS-002 rows.

For the 24h window (100% v1), the warning is technically misleading
because no legacy rows are actually present. Making it data-aware
(count v0 parents in window, only show warning if nonzero) is a
small follow-up, currently deferred.

## Test data anchoring — `_dt.now(timezone.utc)` not fixed REF (PHASE-C14B-2D)

Phase C tests that build synthetic datasets with cycle_starts
MUST anchor to `_dt.now(timezone.utc)` (with appropriate
`timedelta` offsets) rather than a fixed wall-clock string. Wall
clock advances during testing; a cycle_start that was "14 hours
ago" when the test was written is "26 hours ago" 12 hours later,
and falls outside the live 24h window the test is asserting against.

Fixed `CYCLE_STARTS` dict with a static `REF=2026-09-25T00:00:00Z`
will rot. Use helper methods that compute cycle_starts dynamically:
```python
@classmethod
def _in_range_start(cls, range_name):
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    now = _dt.now(_tz.utc)
    if range_name == "24h":
        return cls._iso(now - _td(hours=1))
    if range_name == "7d":
        return cls._iso(now - _td(days=1))
    ...
```

---

## Critical Settings (from `settings_service`)

| Setting | Default | Meaning |
|---|---|---|
| `min_score_buy` | **50** | **[DEPRECATED by SCORE-002]** Preserved in schema for compatibility; no longer gates BUY eligibility. |
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

4. **SCORE-002: `min_score_buy` is no longer the trade trigger.** Non-score strategy gates (RSI / SMA / MACD / Volume) determine eligibility; total_score only ranks otherwise-eligible BUY candidates. Most symbols still HOLD (97%+) because the non-score gates are narrow.

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

The schema default for `min_score_buy` is `50`. SCORE-002 marks this
setting deprecated; the bot no longer consults it for BUY eligibility
but the schema entry is preserved for backward compatibility with
existing persisted overrides and dashboard renders. Dashboard metadata
uses that same default.

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

**OpenClaw Gateway `chat.send` is an accepted-with-runId RPC.** The Gateway
returns as soon as the run has been queued and a `runId` has been assigned;
the manager then continues asynchronously and the dashboard tracks progress
via the 15s `chat.history` poll. The dashboard MUST NOT block on the manager
run and MUST NOT inject **any** `timeoutMs` into the `chat.send` RPC payload.
OpenClaw treats `chat.send`'s `timeoutMs` field as the agent RUN deadline
(it flows straight into `registerChatAbortController` /
`resolveChatRunExpiresAtMs`); passing any value — even a tiny 15s
accept-only value — would silently shadow the operator-owned
`agents.defaults.timeoutSeconds = 1800` configured in
`/root/.openclaw/openclaw.json`. Worse, OpenClaw's
`resolveChatRunExpiresAtMs` clamps the resulting `expiresAtMs` to a
hard 2-minute floor (`minMs = 2 * 6e4`), so any small dashboard
override produced the observable "chat run timed out" abort at
~2 m 20 s on the long engineering tasks of 2026-08-25 and
2026-08-26. The dashboard now leaves the RUN deadline entirely to
OpenClaw runtime config: omitting `timeoutMs` lets `resolveAgentTimeoutMs`
fall back to `clampTimerTimeoutMs(cfg.agents.defaults.timeoutSeconds * 1000)
= 1_800_000`, giving an effective ~31-minute run deadline
(`1800 s + 60 s grace`, bounded to `[120 s, 24 h]`). The dashboard proxy
keeps `GATEWAY_SEND_TIMEOUT_SECONDS = 15` solely as the bounded
subprocess `timeout=` for the Node wrapper (so a wedged Node call cannot
hang the proxy forever); it is NOT injected into the RPC payload.

**Inbound response bound is `CHAT_MESSAGE_MAX_CHARS = 64_000`** (the bound
that ships to the browser per projected assistant message). Outbound user-
input bound is `CHAT_SEND_MAX_CHARS = 4_000` (separate concern, unchanged).
The same bound is passed as the `maxChars` parameter to the OpenClaw
Gateway `chat.history` RPC (`CHAT_HISTORY_MAX_CHARS = 64_000`) so the
Gateway does not pre-truncate at its own 8,000-char default
(`DEFAULT_CHAT_HISTORY_TEXT_MAX_CHARS` in
`/usr/lib/node_modules/openclaw/dist/chat-display-projection-CSlqmWmw.js`).
The 64K inbound bound was raised from the previous 16K so the largest
observed Trading-Manager audit response (18,354 chars on 2026-08-27
~12:02:57 UTC) ships verbatim to the browser instead of being silently
cut. 64K gives ~3.5x headroom over the largest observed audit response.
64K × 50 messages worst-case ≈ 3.2M characters, well within the 25 MiB
WebSocket payload cap.

When the inbound bound is hit, the projection flags `truncated: true` on
the message and the UI shows a small explicit **"Response truncated"**
badge — the cut is never silent. The marker used is the ASCII string
` [Response truncated]`, which survives clipboard / email / JSON transit.
Each projected message also carries a `truncation_source` field which is
one of:

  * `null` — text was NOT truncated (the common case).
  * `"gateway"` — the OpenClaw Gateway `chat.history` projection
    already truncated this text (the canonical `\n...(truncated)...`
    suffix was detected at the end of the projected text); the
    dashboard did NOT add its own bound here.
  * `"dashboard"` — the dashboard's own `_bounded_text` safety bound
    cut this text at `CHAT_MESSAGE_MAX_CHARS`.

The upstream truncation marker is detected by **suffix position** (text
ends with the literal marker) so legitimate user content that
incidentally contains the substring `...(truncated)...` elsewhere in
the body (e.g. quoting a previous truncated message) is NEVER
misclassified as upstream truncation.

A normal Trading-Manager report of up to ~18,354 chars passes through
verbatim and Copy / Copy-since-last-message deliver the complete
projected text. Pathological responses above 64K are explicitly
surfaced as `truncated: true, truncation_source: "dashboard"` with
the visible ` [Response truncated]` marker.

**Future fallback for responses >64K (out of scope, documented for next iteration):**
OpenClaw Gateway already exposes a supported RPC for full per-message
retrieval: `chat.message.get` with parameters `{sessionKey, agentId,
messageId, maxChars: 1..2_000_000}`. The handler reads the full message
from the indexed transcript via `readSessionMessageByIdAsync` and respects
the visibility filter (`dropPreSessionStartAnnouncePairs`) and the
`MAX_TRANSCRIPT_PARSE_LINE_BYTES = 256 KiB` safety cap. Live
verification on 2026-08-27 confirmed the RPC returns the full 18,354-
char text byte-for-byte when the bound is raised. The dashboard does NOT
need to read session log files directly and does NOT need to modify the
OpenClaw dist. If a future Trading-Manager response legitimately exceeds
64K, a follow-up PR can add `/api/engineering/chat/message.get` to fetch
the full message on demand; the dashboard's `ChatMessage` would need to
carry `messageId` (from `__openclaw.id` on the projected message) for
the browser to call it.

**Stale Failed recovery.** The `projectAgentStatus` rule is asymmetric:
a fresh `hasActiveRun=true` ALWAYS clears any prior terminal Failed
indicator and flips to Working. This guards against the dashboard
showing "Trading manager · Failed" after the manager has already accepted
a new dispatch and is working on it (the 2026-08-25 ~15:54 UTC incident).
Only `(runStatus ∈ {failed, killed, timeout})` AND no active run is
interpreted as Failed.

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

### Chat message projection filter (authoritative)

The Chat UI projection in `dashboard_api/chat_gateway._project_message` must show
**only** the actual manager conversation. Three categories survive:

1. `role="user"` messages are always kept (conversational; OpenClaw only mirrors
   assistant replies back into the transcript, never user turns).
2. `role="assistant"` messages are kept **only** when **both** hold:
   - `stopReason == "stop"` (the actual final response, not an intermediate turn)
   - NOT an OpenClaw delivery-mirror (`model != "delivery-mirror"`)
3. Everything else is dropped: `system`, `tool`, `function`, `developer`,
   `toolResult`, assistant `toolUse` / `max_tokens` / `end_turn` / `abort` /
   unknown / missing-stopReason rows, and every delivery-mirror duplicate.

The durable distinctions are `role`, `stopReason`, and the explicit OpenClaw
delivery-mirror marker `model == "delivery-mirror"` written by
`mirrorTelegramAssistantReplyToTranscript` in
`/usr/lib/node_modules/openclaw/dist/bot-B-OxOCyH.js` (matches
`isOpenClawDeliveryMirrorAssistantMessage` in
`/usr/lib/node_modules/openclaw/dist/transcript-hciCrqol.js`). The filter
deliberately does **not** key on the trading-manager's configured
provider/model, so the rule survives any future model change.

### Chat copy controls (DASH-007)

`dashboard_api/app.py` adds two client-side copy controls on top of the
already-projected chat history. They must NEVER read from the raw
OpenClaw transcript — both controls operate on `chatStateCache.messages`,
the array populated by `renderChatHistory` from the server-projected
payload (PR #63). Hidden toolUse / toolResult / delivery-mirror rows
cannot reach the clipboard.

* Per-message Copy button: rendered on every assistant card with
  `data-copy-index="${i}"`. Click handler reads
  `chatStateCache.messages[i].text` (plain text only) and writes it via
  `navigator.clipboard.writeText`. After success the label toggles to
  "Copied" for ~1.8 s then restores. On failure the label becomes
  "Copy failed" for ~2.4 s, a bounded chat-state banner surfaces the
  reason, and chat history is not mutated.
* "Copy since my last message" button: header-level action in the same
  `.chat-status-row` as the agent indicator. Initially `hidden disabled`.
  The payload is computed by `computeSinceLastUserText(messages)`, which
  finds the most recent `role="user"` index in the projected array and
  joins every `role="assistant"` row after it with `\n\n`. The boundary
  resets automatically after each new user turn, and the payload
  naturally tracks new final manager responses as they arrive.
* Re-bind points: `switchTab`, `restoreChatUiState`, `refreshDashboard`,
  and initial script run all call `bindChatCopyControls`. The
  `dataset.copyBound` / `dataset.sinceBound` flags make the bind
  idempotent so the handlers survive `#dashboard-content` snapshot-poll
  replacement and `#chat-history` re-render without stacking.
* Test injection hook: `window.__chatClipboardWriteText` (set by the
  test harness) takes precedence over `navigator.clipboard` so the
  clipboard can be stubbed under headless Node. In production the hook
  is undefined and the script falls through to the browser API.

## Durable engineering dashboard chat history (PR3)

The dashboard's live `/api/engineering/chat/history` endpoint reads from
the OpenClaw Gateway `chat.history` RPC, which is bounded to the current
trajectory / session. OpenClaw rotates sessions periodically; the prior
conversation does not disappear from the Gateway but the dashboard's
"current" session view can. PR3 adds a parallel durable store so the
historical conversation survives rotation, restart, and reconciliation.

Storage: `.agent-state/engineering-chat.sqlite3` (WAL, mode 0600 root:root,
gitignored). Schema is in `dashboard_api/chat_persistence.py`. The store is
authoritative for the durable conversation boundary; the live Gateway
projection remains the authoritative visibility filter.

Visibility contract (NEVER re-implement here):
- Persist ONLY `user` rows and `assistant` rows with
  `stopReason == "stop"` AND `model != "delivery-mirror"`.
- The single source of truth for the filter is
  `dashboard_api.chat_gateway.project_message` (formerly the
  underscore-private `_project_message`; renamed in PR3 so the
  persistence layer can call it directly without bypassing the
  abstraction).
- The persistence layer (`dashboard_api/chat_persistence`) trusts the
  caller to have already projected; it enforces only the SQLite CHECK
  constraint on `role IN ('user','assistant')`.

Identifier semantics (locked 2026-09-08):
- `conversation_id` is the STABLE logical conversation
  (`agent:trading-manager:telegram:direct:8455029949`); it does NOT
  rotate across OpenClaw trajectory rotation.
- `openclaw_session_id` is the ROTATING trajectory UUID.
- Session rotation therefore APPENDS new rows under the same
  `conversation_id` with a new `openclaw_session_id`. Prior rows are
  preserved verbatim; never delete or reorder.

Dedup_key synthesis (deterministic):
- Assistant priority order: `mid:{__openclaw.id}|sid:{sid}` →
  `rid:{responseId}|sid:{sid}` → `ts:{ms}|sid:{sid}|sha:{text_hash[:16]}`.
- User priority order: `run:{run_id}` → `ts:{second_bucket}|sid:{sid}|sha:{text_hash[:16]}` → `local:{uuid4}`.
- The `__openclaw.id` and `responseId` are passed through to the
  persistence layer as INTERNAL-ONLY fields on `ChatMessage`
  (`source_message_id`, `response_id`, `raw_timestamp_ms`). They are
  intentionally EXCLUDED from `to_dict()` so the public
  `/api/engineering/chat/history` payload is byte-equivalent to the
  pre-PR3 surface.
- `UNIQUE(dedup_key)` + `INSERT OR IGNORE` makes 15-second polling,
  page refresh, OpenClaw restart, and reconciliation all idempotent.

Send-failure contract (PR3 default, NOT to be loosened without sign-off):
- `chat.send` accept (run_id present) → persist user row,
  `delivery_status="accepted"`.
- `chat.send` reject (pre-RPC) → NOT persisted.
- `chat.send` failure (transport / RPC) → NOT persisted.
- There are no `delivery_status="failed"` or `delivery_status="rejected"`
  rows in PR3 by design. This is the cleanest contract; loosening it
  requires a separate spec.

API (PR4 will switch the browser; PR3 only adds):
- `GET /api/engineering/chat/history/durable?limit=&before_id=`
- Default limit 50; hard ceiling 200.
- Oldest → newest ordering.
- Project / agent scoped; the browser never picks the
  conversation_id; the lazy resolver
  (`dashboard_api.app._LazyConversationDurableProvider`) discovers the
  stable OpenClaw session key on the first read and caches it.
- `DASHBOARD_CHAT_PERSISTENCE_ENABLED` env var (default `1`) is the
  kill switch for the write side; the read endpoint stays available.

Wiring (`dashboard_api/chat_persistence_integration.py`):
- `PersistingChatHistoryProvider` wraps the underlying
  `GatewayChatHistoryClient` (or any `ChatHistoryProvider`-shaped
  object). It exposes the same interface so app.py uses it
  transparently.
- Pass-through tolerance: if the underlying provider returns something
  other than a `ChatHistory` / `ChatSendResult` (e.g. a plain dict in a
  test fixture), the wrapper returns it unchanged. The production
  wiring uses `GatewayChatHistoryClient` which always returns the typed
  dataclasses.
- Persistence failures NEVER change the user-visible chat.send /
  chat.history response (the Gateway run is already accepted;
  surfacing a local DB failure as a send failure would be a lie).

Out of scope (explicit, locked 2026-09-08):
- PR4 — UI switch.
- PR5 — historic backfill.
- Cloudflare Tunnel / Access changes.
- Raw trajectory / reset file parsing.

## PR4 — Durable + Live Chat UI for the Engineering Dashboard (DASH-009)

**Branch:** `agent/dashboard-chat-durable-ui-pr4`
**Status (2026-09-09):** Ready for review. 1001/1001 tests pass.
**Approval gate:** Josh explicit merge approval (do NOT auto-merge).

What PR4 does:

- On Chat tab open, `GET /api/engineering/chat/history/durable?limit=50`
  fires FIRST. Rows render immediately, oldest → newest.
- Live `/api/engineering/chat/history` continues to poll on the existing
  15-second cadence for `Working / Idle / Failed`, `has_active_run`,
  `run_status`, and reconciliation of new visible messages.
- Session rotation does NOT remove previously rendered rows.

Deterministic client merge (in `dashboard_api/app.js`):

  - Three sources: `durableRows`, `liveRows`, `optimisticRows`.
  - Dedup key priority:
    1. `source_message_id` (assistant; raw `__openclaw.id`)
    2. `openclaw_run_id` (user; chat.send accept)
    3. `durable_id` (durable rows)
    4. Fallback: `(role, text, ts_bucket)` where
       `ts_bucket = floor(epoch_ms / 1000)`. Used only when no identity
       field is available. NEVER dedup on text alone.
  - Live rows are deduped against each other AND against durable rows
    (the pre-PR4 logic only deduped live vs durable).
  - Optimistic rows dedup against durable and live; collapse into the
    durable row when both share `openclaw_run_id`.

Auto-scroll (iPhone Safari safe):

  - Stick to bottom only when user was near bottom BEFORE the render
    (gap = `preRenderScrollHeight - preRenderScrollTop <= clientHeight + 48`).
  - The pre-PR4 sticky `wasNearBottom` was REMOVED because it yanked
    users who scrolled up between renders (especially noticeable after
    a touch-scroll bounce).
  - "Load older" preserves viewport via scrollHeight delta math (see
    `loadOlderDurable`).

Status pill recovery (PR4 correction, Josh 2026-09-09 02:25 UTC):

  - `projectAgentStatus()` now follows:
    1. `has_active_run=true` → `working`
    2. `run_status` ∈ `{failed, killed, timeout}` → `failed` (genuine
       terminal)
    3. Otherwise → `idle` (clears stale cached Failed)
  - The pre-PR4 rule required a future `has_active_run=true` to clear
    a stale Failed pill. That meant a backgrounded / throttled tab
    stayed stuck on `Trading manager · Failed` indefinitely after the
    runtime issue observed 2026-09-08 17:59 UTC.
  - The pill recovery is pill-only: the durable chat history rows are
    never cleared or reordered by the status transition.

Other preserved behavior:

  - Per-message Copy + "Copy since my last message" (PR #64 / #65 /
    PR3 contract preserved).
  - iOS-Safari clipboard fallback (`fallbackCopyToClipboard`).
  - Truncation UI (`Response truncated` badge).
  - 4,000-character outbound bound + non-text rejection in `sendChatMessage`.
  - Optimistic send row added BEFORE POST; collapsed into durable row on
    catch-up; removed on failure with bounded failure banner.
  - `refreshChatHistory` now always re-fetches durable AND live
    (previously only on first call). This is what enables the
    live-only → durable collapse on subsequent polls.

Out of scope (still locked):

- PR5 (historic backfill) — not started.
- Cloudflare Tunnel / Access changes — not started.
- Raw trajectory / reset file parsing — not started.
- Auto-merge — requires explicit Josh approval.

Files changed in PR4:

  - `dashboard_api/app.py` — chat JS embedded template rewritten.
  - `dashboard_api/chat_gateway.py` — identity field exposure on `to_dict`.
  - `dashboard_api/chat_history_durable.py` — `_to_chat_message`
    populates identity fields.
  - `tests/test_dashboard_api_app.py` — pre-PR4 test mock updated for
    new durable endpoint and new near-bottom scroll behavior.
  - `tests/test_pr4_durable_chat_ui.py` — NEW 22 tests.

## SCORE-001 — Normalize indicator scores

**Branch:** `agent/score-001-normalize-indicator-scores`
**Status:** PR-ready; awaiting Josh review/merge.

Two new helpers in `src/core/smart_bot.py`:

- `SmartTradingBot._score_components(latest)` — returns a dict with
  individually-clamped bounded components per the documented ranges
  above. Each component is clamped to its ±25 range BEFORE any
  volatility-tier multiplier is applied.
- `SmartTradingBot._clamp_total_score(raw)` — explicit defense-in-depth
  clamp of the published `total_score` to 0..100; NaN / unparseable
  input degrades safely to 0.

Both `analyze_symbol()` and `analyze_multi_timeframe()` now route
through these helpers. The MTF path additionally fixed two pre-existing
bugs that were in the buy_criteria scoring block:

- MACD was previously `min(30, macd_hist * 50)` — raw, unclamped, and
  price-scale-dependent. Now uses the ATR-normalized helper.
- BB position was reading `BB_width` (bandwidth, a percentage) instead of
  `(price - BB_lower) / (BB_upper - BB_lower)`. Now reads the proper
  position.

### Strategy preservation (SCORE-002 amendments)

- BUY eligibility is determined by non-score strategy gates (RSI /
  SMA / MACD / Volume). `min_score_buy` is no longer consulted for
  eligibility; the setting is preserved in the schema as
  deprecated.
- BUY ranking: eligible candidates are sorted by `total_score DESC,
  symbol ASC` and the top `max_trades` are executed.
- SELL detection uses an internal signed `blended_signed` score against
  the existing hardcoded `-50` threshold (and `<= 20` for STRONG
  SELL). The threshold values are unchanged; only the variable being
  checked changed (from the now-clamped `total_score` to a separate
  signed `blended_signed`). This is the minimum-impact way to keep
  SELL semantics working on a bounded score.
- Volatility-tier multipliers (1.3x on RSI for low-vol, 1.3x on SMA
  for high-vol) are preserved.
- The insider-trading +10 boost is clamped to 0..100 after application.

### SELL threshold impact (and why it is unchanged)

With `total_score` clamped to 0..100, the old `total_score <= -50`
SELL check is unreachable. Rather than change the strategy threshold
or remove SELL detection, an internal signed `blended_signed` score
keeps the existing SELL threshold semantics exactly. The published
0..100 score is the only externally-visible value, and the existing
`-50` and `20` thresholds are unchanged. If a future iteration
wishes to migrate SELL detection to the 0..100 scale, that is a
deliberate threshold change that requires a separate review.

### Tests

`tests/test_smart_bot_score_normalization.py` (NEW, 56 tests):

- Component bound tests (RSI / SMA / MACD / BB each within ±25, with
  endpoints verified)
- MACD ATR-normalization property: identical normalized MACD gives
  identical scores regardless of price scale
- MACD dominance proof: 50-ATR MACD histogram alone cannot push the
  score past 75
- Extreme MACD bounded tests (10^3, 10^6, negative)
- `_clamp_total_score` tests (raw=-10000 → 0; raw=10000 → 100; NaN → 0;
  unparseable → 0)
- Bullish / neutral / bearish scenario fixtures (custom-constructed
  pd.Series of indicator values) with the required thresholds
  (≥65 / 45..55 / ≤35)
- Identical inputs deterministic (pure function, no state mutation)
- Monotonicity: RSI lower → score higher; SMA wider → higher;
  BB closer to lower → higher; MACD larger positive → higher
- Realistic ranking: bullish > neutral > bearish; strong bullish
  > mild bullish
- Integration: full `calculate_indicators` → `_score_components` →
  `_clamp_total_score` round-trip

## SCORE-001 — Amend #1, #2, #3 (PR #76 in-place amendment)

**Status:** Follow-up commits on the same branch
`agent/score-001-normalize-indicator-scores`; PR #76 is updated
in-place. The three amends close a fail-OPEN NaN path, add
post-multiplier bound tests, and disclose two collateral behavior
changes the original PR did not document.

### Amend #1 — fail-closed on invalid / non-finite indicator data

The original SCORE-001 helpers silently substituted `0` for any
NaN/missing indicator, and `_clamp_total_score(NaN) == 0.0`. This
opened two real risks:

- A **published 0/100 score** from invalid data (looks like the most
  extreme bearish possible value, and any dashboard / alert that
  reads `analysis['total_score']` would surface a misleading
  signal). This is "fail-OPEN" — invalid data appears as a valid
  extreme-bearish score.
- A **false BUY** for partial-NaN inputs. A bullish RSI/SMA/BB with
  NaN MACD (treated as 0) could sum to 100 (clamped) and trigger
  BUY. The SELL branch is incidentally safe because `blended_signed
  <= -50` is not reachable through NaN, but the BUY branch was open.

**Fix:** the helpers now return `None` for invalid input rather than
silently coercing to a numeric value. The new `_is_finite_number`
helper centralizes the validation and is the single source of truth
for "is this a valid real number that can participate in score math?"

- `_score_components(latest, catalyst_score)` returns `None` if ANY
  required input is non-finite: RSI, SMA fast/slow, MACD_histogram,
  ATR, BB upper/lower, close, or the catalyst score. ATR is
  additionally required to be strictly positive (0 ATR makes the
  MACD ratio undefined).
- `_clamp_total_score(raw)` returns `None` for NaN, inf, unparseable
  string, or non-numeric input.
- `analyze_symbol()` and `analyze_multi_timeframe()` both check the
  helper return value and abort with `return None` (matching the
  existing "no data" pattern) so the caller treats the symbol as
  non-actionable. **No BUY, no SELL, no published 0/100 score.**
- The volatility-tier multiplier path, the hourly blend, and the
  insider-trading +10 boost each check the clamp's return for None
  and abort if it appears.

### Amend #2 — post-multiplier bounds

The original PR documented "components are clamped to ±25" but the
volatility-tier 1.3x multiplier (applied in `analyze_symbol` AFTER
the helper) extends the AUTHORITATIVE post-multiplier bound to
±32.5 for RSI (low-vol) and SMA (high-vol). The final
`_clamp_total_score` is the 0..100 guard, but the per-component
contribution bound is wider than the helper output.

**Authoritative documented bounds (POST-multiplier, PRE-final-clamp):**

| Component | Pre-multiplier (helper) | Post-multiplier (`analyze_symbol`) | Worst case |
|---|---|---|---|
| RSI | ±25 | ±32.5 (low-vol `atr_pct < 2.0` only) | ±32.5 |
| SMA | ±25 | ±32.5 (high-vol `atr_pct > 5.0` only) | ±32.5 |
| MACD | ±25 | ±25 (no multiplier) | ±25 |
| BB | ±25 | ±25 (no multiplier) | ±25 |
| Catalyst | 0..+25 | 0..+25 (no multiplier) | 0..+25 |

**Worst-case envelope:**

- Bullish raw signed sum: 32.5 + 32.5 + 25 + 25 + 25 = **+140** →
  `clamp(50 + 140)` = **100**.
- Bearish raw signed sum: -32.5 + -32.5 + -25 + -25 + 0 = **-115** →
  `clamp(50 - 115)` = **0**.

`tests/test_smart_bot_score_normalization.py` adds explicit tests
that verify the post-multiplier ±32.5 bound for both RSI and SMA,
the worst-case envelope (sum doesn't exceed +140 / -115), and that
the final clamp still holds the published total_score in 0..100
across the full envelope.

### Amend #3 — collateral disclosures

The original PR understated two adjacent behaviors that the review
surfaced. They are now explicitly documented:

**3A. MTF `buy_criteria[0]['passed']` "Score ≥ 65" behavior changed.**

The pre-SCORE-001 MTF total_score was a signed value (range
roughly -185..+135) with a "/100" display label. The check
`bool(total_score >= 65)` was therefore comparing a signed value
against a 0..100-style threshold, which was internally
inconsistent. The post-SCORE-001 MTF total_score is genuinely in
0..100 and the check is now consistent. **Net effect: moderate
bullish MTF setups (e.g., rsi=40, sma=+2%, half-ATR MACD, near
lower BB) that previously failed the signed-vs-65 check (signed
value ~25) now pass the 0..100-vs-65 check (published value
~65+).** This is a documented collateral behavior change from
the SCORE-001 scale fix.

**3B. MEDIUM SELL is unreachable both before and after SCORE-001.**

The SELL branch contains two nested checks:

```python
elif blended_signed <= -50:
    signal = "SELL"
    if blended_signed <= 20:
        signal_strength = "STRONG"
    else:
        signal_strength = "MEDIUM"  # unreachable
```

Algebraically, `blended_signed <= -50` already implies
`blended_signed <= 20`, so the "MEDIUM SELL" branch is unreachable
in BOTH the pre- and post-SCORE-001 code. All SELL signals are
STRONG. This is a pre-existing bug that SCORE-001 deliberately
preserved (the user spec said "STOP and report before changing
buy/sell thresholds"). A separate backlog item should be created
to fix this in a future iteration (e.g., redefining MEDIUM SELL
on a 0..100 scale, e.g., total_score in [20, 50]).

### Updated test count

`tests/test_smart_bot_score_normalization.py` now contains 86 tests
(up from 56 in the original PR). New tests cover:

- `_score_components` returns None for NaN RSI / SMA fast / SMA slow /
  MACD_histogram / ATR (NaN) / ATR (0) / BB upper / BB lower / close
- `_score_components` returns None for NaN / None / unparseable-string
  catalyst
- Partial-NaN (bullish others, NaN MACD) cannot produce a bullish
  score (the previously documented false-BUY path)
- `_clamp_total_score` returns None for NaN / inf / None / unparseable
  string / unparseable object (parametrized)
- Post-multiplier bound tests: RSI low-vol +32.5; SMA high-vol +32.5;
  RSI bearish -32.5; SMA bearish -32.5; MACD +25; BB +25; catalyst
  0..+25
- Worst-case envelope: bullish sum = +140 → clamped to 100; bearish
  sum = -115 → clamped to 0; published score always 0..100
- MTF `Score ≥ 65` collateral disclosure test
- MEDIUM SELL algebraic-unreachable invariant test

Full safe suite: **1125/1125 pass** (was 1095 pre-amend, +30 new).
`git diff --check` clean. Brokerage safety gate unchanged. SmartBot
remains OFF. BOT-002 not enabled.

### Phase A controlled activation (2026-09-12 03:42-03:50 UTC)

- PR #78 merged to main at `eefd7e9`. Both local and origin main at
  the same commit.
- SmartBot restarted via `systemctl --user restart smartbot-runner.service`.
  New PID 761649. Paper-only env preserved.
- Both dashboards restarted:
  - `trading-dashboard.service` (port 8000) — PID 761736. Loads OBS-001
    dashboard code (api_opportunities, api_decision, api_decision-history,
    api_actionability-summary).
  - `dashboard.service` (port 8010, dashboard.mooseops.com.co) — PID
    761807. Engineering Dashboard; OBS-001 changes are NOT here.
- Migration is idempotent and was effectively applied during PR review
  tests. Schema verified: analyzed_stocks.decision_snapshot +
  decision_schema_version, decision_history UNIQUE(cycle_id, symbol),
  cycle_funnel UNIQUE(cycle_id). 13,749 analyzed_stocks rows preserved
  (10,352 pre-OBS rows still snap=NULL).
- 16+ cycles ran post-activation. All 30 symbols/cycle, all HOLD_INELIGIBLE
  (deep low-signal market today). No forced trades, no symbol/score
  changes, no threshold changes.
- Audit of 6 stocks (MNST, VCOB, RAVI, MNR, RBIL, ALRS) and a worked
  example (MODD, cycle_76246) showed identical data across all four
  sources: runtime journal ↔ analyzed_stocks ↔ decision_history ↔
  dashboard API.
- Absent categories this activation: BUY candidates, SELL candidates,
  execution-blocked, order-submitted, ranked-not-attempted. These will
  be audited when natural market conditions produce them.

### Known pre-existing issue (out of OBS-001 scope)

`trading-dashboard.service` (PID 761736) does NOT set
`TRADING_BOT_PAPER_ONLY=1` in its unit env. When the dashboard's
`/api/positions` endpoint instantiates `SmartTradingBot()`, the
BOT-002 paper-only guard fails. The endpoint returns empty lists
with an error logged. This is PRE-EXISTING (verified by journal scan
of pre-OBS-001 PID 655875). NOT introduced by OBS-001 merge.
NOT in scope for this activation. The OBS-001 endpoints are NOT
affected.

### Dashboard URL mapping (unchanged)

| Cloudflare hostname | Local service | Local port |
|---|---|---|
| dashboard.mooseops.com.co | dashboard.service (Engineering Dashboard) | 8010 |
| trading.mooseops.com.co | trading-dashboard.service (Trading Dashboard / dashboard.py) | 8000 |

## Phase B — Latest Cycle Funnel + Truthful Top Candidates (DASHBOARD-ONLY)

A dashboard-only slice that replaces the legacy "Top Opportunities"
concept (highest total_score rows from analyzed_stocks) with two
views that are truthful under SCORE-002:

### Source authority

- **Latest Cycle funnel card** reads ONLY `cycle_funnel` via
  `/api/actionability-summary`. Authoritative bot-written facts.
- **Top Candidates card** reads ONLY `decision_history` via
  `/api/cycle-candidates/{cycle_id}`. `decision_history` is
  immutable, append-only.
- The renderer never infers candidate status from `total_score`,
  `signal`, `passes_all_buy_criteria`, or `analyzed_stocks` rows.

### Path-of-truth for "ranked candidate"

A symbol is a "ranked candidate" ONLY when its persisted
`decision_snapshot.ranking.candidate_rank` is non-null. That field
is populated exclusively by SCORE-002 when ranking actually executes
in a cycle. Until SCORE-002 produces candidates (live today:
`ranked_candidate_count = 0` for every cycle recorded), the Top
Candidates card shows the ZERO-CANDIDATE state.

The endpoint enforces this at the SQL boundary:
```sql
SELECT symbol FROM decision_history
WHERE cycle_id = ?
  AND json_extract(decision_snapshot, '$.ranking.candidate_rank') IS NOT NULL
ORDER BY CAST(json_extract(decision_snapshot, '$.ranking.candidate_rank') AS INTEGER) ASC,
         CAST(json_extract(decision_snapshot, '$.scoring.total_score') AS REAL) DESC
```

### Visual funnel policy (NEVER imply divergent outcomes)

The Latest Cycle card renders FIVE forward stages in declared order:
`Analyzed → Strategy Eligible → Ranked Candidates →
Execution Attempted → Orders Submitted`

Plus TWO off-path outcomes rendered in their own row, with a
red background and `(off-path)` label suffix:
`Execution Blocked (off-path)` and `Orders Failed (off-path)`

Off-path outcomes are visually separated so the UI never implies
`Execution Blocked → Orders Submitted` (which is mathematically
false — they are divergent outcomes).

### Zero-candidate state

When `ranked_candidate_count == 0`, the Top Candidates card shows:
- a headline like "0 of N strategy eligible"
- a "X symbols were analyzed in this cycle" subtitle
- a "HIGH-SCORE NEAR MISSES (top 3)" table sourced from
  `decision_history` rows where
  `decision.outcome = 'HOLD_INELIGIBLE' AND
   ranking.candidate_rank IS NULL`, ordered by
  `scoring.total_score DESC`, capped at 3.

These are explicitly NOT candidates — they are near-misses labeled
as such, with the persisted `failed_strategy_gates` names attached
so the user can see WHY they failed strategy eligibility.

Phase B does NOT implement "distance from pass" math. That belongs
to a later phase.

### Phases that came before

- Phase A (merged, deployed): History Symbol Search + OBS-001
  Symbol Decision Trace.
- Phase A.1 (merged, deployed): Execution Checks renderer fidelity
  (fixed display bug when `first_blocking_check` falls outside
  `evaluated_in_order`).
- Phase B (this slice): Latest Cycle funnel + Top Candidates.

---

## OBS-002 — Terminal Decision Coverage

**Target invariant for a normally completed cycle:**

```
cycle_funnel.analyzed_count
==
COUNT(DISTINCT decision_history.symbol WHERE cycle_id = current_cycle)
```

Every symbol counted in `cycle_funnel.analyzed_count` MUST receive
exactly ONE terminal `decision_history` row during a normally
completed cycle. The invariant makes the dashboard's funnel
denominator (`analyzed_count`) match the dashboard's per-cycle
source-of-truth (`decision_history`).

### Gap paths covered by `_persist_skipped_terminal_decision`

| # | Code path | Outcome | Reason captured in `decision.primary_reason` |
|---|---|---|---|
| G1 | `if not analysis:` no-market-data sub-case | `SKIPPED_INVALID_DATA` | `"no market data (needs N bars)"` |
| G2 | `if not analysis:` fallback HOLD compute (analyze returned None) | `SKIPPED_INVALID_DATA` | `"analyze_symbol returned None (fallback HOLD score saved but no real strategy-gate evaluation)"` |
| G3 | NaN in fallback score compute (pre-checked before `int(total)`) | `SKIPPED_INVALID_DATA` | `"NaN in fallback score compute (X is NaN)"` |
| G4 | BUY/SELL signal with `signal_strength == "WEAK"` | `HOLD_INELIGIBLE` | `"weak signal strength (WEAK); [liquidity_filter|sector_filter: ...]"` |
| G5 | BUY/SELL signal with `signal_strength == "CONFLICTED"` | `HOLD_INELIGIBLE` | `"AI conflicts with technical signal (CONFLICTED); not actionable"` |
| G6 | Outer per-symbol `except Exception` (incl. NaN-to-int) | `SKIPPED_INVALID_DATA` | `"analysis-body exception: TypeName: msg"` |

### Semantic decisions

- **No new outcome enum values.** All six paths reuse existing
  canonical outcomes. `OBS_001_SCHEMA_VERSION` remains 1.
- **G4/G5 reuse `HOLD_INELIGIBLE`** (not a new outcome) — they
  represent "strategy passed but signal strength made the symbol
  unactionable". The snapshot truthfully records `signal=BUY/SELL`
  (gates did pass) and `outcome=HOLD_INELIGIBLE` (bot did not act).
- **G1/G2/G3/G6 reuse `SKIPPED_INVALID_DATA`** — these are
  "no valid analysis produced" failures. NaN inputs in the analysis
  dict are normalized to `None` so the snapshot round-trips through
  JSON without NaN corruption.

### Snapshot truthfulness contract

For every skipped path, the snapshot explicitly states:

| Block | Truth |
|---|---|
| `strategy_eligibility.strategy_eligible` | `False` (bot did not execute) |
| `strategy_eligibility.gates` | `[]` (no strategy gates were evaluated) |
| `scoring.total_score` | `None` for SKIPPED_INVALID_DATA; preserved for HOLD_INELIGIBLE |
| `scoring.score_invalid_data` | `True` for SKIPPED_INVALID_DATA; `False` for HOLD_INELIGIBLE |
| `ranking.applicable` | `False` |
| `ranking.candidate_rank` | `None` |
| `selection.attempted` | `False` |
| `execution_checks.checks` | `[]` (no check ran) |
| `execution_checks.evaluated_in_order` | full documented order (Phase A convention) |
| `order.submitted` | `False` |
| `order.slot_consumed` | `False` |

### Persist contract

- **Terminal-path coverage is the OBS-002 responsibility.** The
  six gap-path call sites (G1–G6) are responsible for ensuring
  every symbol counted in `cycle_funnel.analyzed_count` ATTEMPTS
  one terminal `decision_history` write via the helper. The
  helper builds the snapshot and routes it through Phase A's
  `db.finalize_decision_history`.
- **`UNIQUE(cycle_id, symbol)` guarantees at most one persisted
  row per (cycle_id, symbol).** It is a duplicate-call safety
  net that swallows `IntegrityError` on the rare race path where
  a Phase A persist and the OBS-002 helper both attempt to
  finalize the same (cycle_id, symbol) within one cycle.
- **Together, for a normal completed cycle with no process
  interruption, the intended invariant is:**

  ```text
  cycle_funnel.analyzed_count
  ==
  COUNT(DISTINCT decision_history.symbol WHERE cycle_id = current_cycle)
  ```

  The `UNIQUE` constraint by itself does NOT guarantee
  completeness; it only guarantees at-most-one row per
  (cycle_id, symbol) and prevents duplicate writes. The
  invariant holds because OBS-002's gap-path call sites cover
  every symbol that reaches the `analyzed_count` counter.
- `decision_history` is INSERT-only. The helper reuses
  `db.finalize_decision_history` from Phase A.
- `analyzed_stocks.decision_snapshot` is upserted (latest per-
  symbol state), reusing `db.save_analysis_result` from Phase A.
- No schema migration. `decision_history` and `analyzed_stocks`
  columns are unchanged.

### NaN metric side effect

OBS-002's NaN hardening (PART 3) changes how NaN / invalid-input
cases are tallied in the session-end `errors_count` metric:

- NaN / invalid inputs now persist a `SKIPPED_INVALID_DATA` row
  BEFORE reaching the prior outer error path (the
  `except Exception as e:` block). Because the persist happens
  before the exception is raised, the outer handler never fires
  for the pre-checked NaN path; `errors_count` is therefore not
  incremented for these NaN cases in PR #85.
- This is an **observability / metrics side effect only**. The
  valid finite analysis path, trading decisions, ranking,
  sizing, risk limits, and order behavior are all unchanged.
- PR #85 does not modify `errors_count` deliberately to preserve
  the prior metric shape. Restoring the prior `errors_count`
  behavior would be a separate, explicitly-approved change.
- Other categories of exceptions (network blips, indicator
  calc failures that are not pre-checked NaN, etc.) still flow
  through the outer `except` and DO increment `errors_count`
  exactly as in `main`.

### Phase C guidance note

OBS-002 reuses `HOLD_INELIGIBLE` for G4 (WEAK signal strength)
and G5 (CONFLICTED AI signal) gap paths. Future Phase C
Analytics aggregations MUST observe the following:

- `HOLD_INELIGIBLE` does NOT necessarily mean "failed strategy
  gate". It can also mean "gates passed, but post-gate filter
  (WEAK / CONFLICTED / etc.) rejected execution".
- G4 (WEAK) and G5 (CONFLICTED) may persist `HOLD_INELIGIBLE`
  without ordinary gate-failure semantics. Distinguish via:
  - `decision.primary_reason` (mentions "weak" or "conflicted")
  - `strategy_eligibility.signal_strength` (WEAK or CONFLICTED)
  - `strategy_eligibility.gates[*].passed` (likely `true` for
    WEAK/CONFLICTED)
- Phase C MUST aggregate gate failures from
  `strategy_eligibility.gates`, not infer failed gates from
  `outcome` alone. Two symbols with `outcome=HOLD_INELIGIBLE`
  can have very different gate states.
- `SKIPPED_INVALID_DATA` MUST be reported separately from gate
  failures. SKIPPED rows represent "no valid analysis
  produced"; they are not gate rejections.

This convention applies to the existing Phase A `elif
analysis:` HOLD path too, which can produce
`signal='HOLD'` + `outcome=HOLD_INELIGIBLE` with non-failed
gates. OBS-002 extends the same convention to the previously-
unpersisted WEAK/CONFLICTED gap paths.

### What this invariant does NOT cover

- Symbols skipped for `has_pending_orders` (smart_bot.py:5593) are
  NOT counted in `analyzed_count`. They are correctly excluded from
  the invariant.
- The invariant applies to **normally completed cycles**. If the
  bot crashes mid-cycle, the cycle_funnel row may be missing
  entirely; the invariant is undefined for that case.
- The invariant is a cycle-level property. Aggregations across
  many cycles are valid only when every cycle in the window is
  complete.

### NaN hardening (PART 3)

The recurring `❌ Error with SYM: cannot convert float NaN to
integer` exception (previously caught by the per-symbol outer
`except Exception as e:` at smart_bot.py:6035) now has TWO
guarantees:

1. The fallback score compute path (smart_bot.py inside `if not
   analysis:`) checks for NaN components BEFORE calling
   `int(total)`. If any NaN is detected, the symbol is persisted
   as `SKIPPED_INVALID_DATA` with reason `"NaN in fallback score
   compute"`, and `int(total)` is never called.
2. The outer per-symbol `except Exception as e:` block now also
   persists a `SKIPPED_INVALID_DATA` row INSIDE the same handler
   that already logs and swallows the exception. The exception
   propagation is UNCHANGED relative to main — the existing log +
   `errors_count += 1` + swallow path completes identically; only
   one additional INSERT is performed inside the handler before
   control flows back to the per-symbol for-loop's next iteration.
   ANY exception (not just NaN) during the analysis body is now
   recorded as a `SKIPPED_INVALID_DATA` row, so OBS-001 persistence
   is never bypassed by an unexpected exception.

This is the minimum necessary change to prevent the exception from
bypassing OBS persistence; it does NOT touch the underlying
indicator-calc robustness (out of scope).

**Exception propagation relative to `main`: NONE.** PR #85 does
not change what the outer handler does after the new INSERT —
the exception remains swallowed exactly as in `main`. No `raise`,
`raise e`, or `raise from` is added.

### Test coverage

`tests/test_obs_002_terminal_decision_coverage.py` (20 tests):

- `TestOBS002Contract` — schema version unchanged, enum unchanged.
- `TestSkippedHelperSnapshots` — each of G1/G4/G5/G6 builds the
  correct snapshot; bogus outcome and missing cycle_id are
  rejected.
- `TestSkippedHelperPersistence` — `finalize_decision_history` and
  `save_analysis_result` are called with the right schema version;
  UNIQUE-violation safety net swallows IntegrityError without
  propagating.
- `TestSkippedHelperSnapshotFields` — `total_score` not fabricated
  for invalid data, `candidate_rank` is None, `checks` is empty,
  `fill_confirmed=False`, WEAK preserves real `total_score`.
- `TestNaNHardening` — NaN inputs don't raise; NaN is normalized
  to None in JSON; rsi NaN doesn't propagate into gates.
- `TestTerminalCoverageInvariant` — mixed gap paths for the same
  cycle_id each produce one finalize call.

### Tab navigation

- `View analysis →` button on the Latest Cycle card switches to
  the Analytics tab (no Analytics redesign implemented yet).
- Clicking a Top Candidates row or a near-miss row opens the
  Phase A Symbol Decision Trace for that symbol via pre-filled
  History tab search.

### Endpoint added (Phase B only)

- `GET /api/cycle-candidates/{cycle_id}?near_miss_limit=N`
- Reads from `decision_history` (immutable) + `cycle_funnel` (read-only)
- Returns `candidates`, `near_misses`, `cycle_known`, `cycle_*`
  counter envelope.
- All other endpoints (`/api/opportunities`, `/api/decision`,
  `/api/decision-history`, `/api/actionability-summary`) UNCHANGED.

### Safety / semantics

- Does NOT modify SmartBot trading behavior.
- Does NOT modify scoring, eligibility, ranking, sizing, risk,
  brokerage, OBS-001 persistence, schema, PIPELINE-001, SCORE-003.
- SmartBot `ActiveEnterTimestamp` must remain unchanged across
  deployment.
- DASHBOARD ONLY.

## Dashboard Phase C — OBS Analytics (2026-09-17)

Phase C is a dashboard-only, read-only OBS Analytics redesign of the
Analytics tab. It exists to answer one question: **"why, over time?"**
That is, given the persisted decision footprint, what does the bot
actually do cycle after cycle — how many symbols it analyzes, how
many become eligible, how many reach execution, how many fail each
gate or check, how outcomes distribute, and where the historical
record is complete vs. incomplete. It is NOT a redesign of the
trading bot itself.

**Scope: dashboard / read-model only.** Phase C does NOT modify:
SmartBot scoring, eligibility, ranking, sizing, risk, brokerage,
OBS-001/OBS-002 persistence, the `trading_bot.db` schema,
PIPELINE-001, SCORE-003, OpenClaw runtime/config, or any service
configuration. No `src/` changes. No settings changes. SmartBot
`ActiveEnterTimestamp` must remain unchanged across deployment.

### Purpose and contract

- Persisted OBS facts are the only authority. Every Phase C value
  comes from a persisted column or a persisted JSON field on
  `cycle_funnel` or `decision_history.decision_snapshot`.
- No historical recomputation from current settings. The bot's
  current scoring or eligibility rules are never consulted to
  derive Phase C values; only the structured persisted snapshot is.
- `HOLD_INELIGIBLE` does NOT automatically mean a failed gate. A
  HOLD row's gate record is counted only if its structured
  `gates[].passed == false` says so. The outcome enum is never
  used to infer gate failure.
- `SKIPPED_INVALID_DATA` remains distinct. It is its own outcome
  row in the Outcome Distribution card, never lumped with HOLD,
  rendered in a distinct color and labeled "Invalid data (no
  score, no rank)".
- Candidate existence requires `ranking.candidate_rank IS NOT NULL`
  (Phase B contract; unchanged by Phase C).
- Submitted orders are not fills. A submitted order is shown as
  submitted, never as filled, unless `fill_confirmed === true`.
  Phase C does not introduce or change any order-fill endpoint.
- Pre-OBS-002 history may be incomplete and is never backfilled.
  Pre-OBS-002 cycles are surfaced separately in the coverage card
  with an explicit "does not backfill" note; the structured
  `gates[]` and `execution_checks.checks[]` arrays are sparse or
  absent for those cycles.

### Gate-aggregation contract (audited)

Phase C was preceded by a schema-v1 audit of persisted decision
snapshots (287,066 post-OBS-002 rows sampled). The audit resolved the
"structured failed_gates vs primary_reason text" question definitively:

| Snapshot block | Population | Status | Phase C use |
|----------------|-----------|--------|-------------|
| `strategy_eligibility.failed_gates` | 0 / 287,066 rows | **never populated** in v1 | NOT USED. The structured `gates[]` array replaces it. |
| `strategy_eligibility.gates[]` | 275,296 / 287,066 rows (96%) | canonical structured array with `{name, category, applied, passed, observed_value, threshold_value, reason}` per gate; **100% of HOLD_INELIGIBLE rows have it** | YES. **Strategy Gate Failure Frequency** reads this directly. No text parsing. No `primary_reason` substring heuristic. |
| `execution_checks.checks[]` | 516 / 287,151 rows (0.18%) | structured array; only SELL_BLOCKED_DYNAMIC paths reach `execute_trade` and therefore produce checks[]; **every other outcome has NO checks[]** | YES (with explicit universe caveat). **Execution Blocker Frequency** reads this and labels its universe as "decision_history rows where the symbol reached `execute_trade`" — currently only SELL paths produce this. |
| `decision.outcome` | 287,066 / 287,066 rows | canonical enum | YES. **Outcome Distribution** plus explicit SKIPPED_INVALID_DATA separation. |
| `decision.signal_strength` | 1,437 / 1,437 sampled HOLD_INELIGIBLE rows = NULL; some HOLD rows in early post-OBS-002 sample = `WEAK` | per-outcome, NOT a strategy-gate indicator | NOT used to infer gate failure. The structured `gates[].passed = false` is the only authoritative gate-failure signal. |

**Conclusion**: Phase C uses `strategy_eligibility.gates[].passed =
false` (canonical structured source) for Strategy Gate Failure
Frequency. **No heuristic parsing of `decision.primary_reason` is
performed.** `decision.primary_reason` is free-form English (e.g.,
"Strategy ineligible: failed gates: rsi_oversold, sma_uptrend") and
is therefore NOT canonical for aggregation. It is preserved exactly
as persisted and exposed verbatim in tooltips / drill-down panels
(Phase A behavior), but never parsed into structured gate fields by
the dashboard.

### Time-range selector

The Analytics tab header gains a `<select id="phase-c-range">` with
four options:

| Option | Window | Source of `start_time` |
|--------|--------|-------------------------|
| Latest | the latest single completed cycle (the same one Phase B's Latest Cycle card shows) | `cycle_funnel.cycle_start` of the row with the largest `cycle_end` that is non-null |
| Today | 00:00:00.000000 → 23:59:59.999999 of the **server's UTC calendar day** | `datetime.now(timezone.utc).strftime("%Y-%m-%d")` — UTC day boundary, computed on the server. |
| 24h | now − 24h → now | `datetime.now(timezone.utc) - timedelta(hours=24)` |
| 7d | now − 7×24h → now | `datetime.now(timezone.utc) - timedelta(days=7)` |

All four windows are interpreted on the **persisted
`cycle_funnel.cycle_start` and `decision_history.cycle_start` columns**
(always stored as ISO-8601 UTC with `+00:00` suffix). SQLite
lexicographic comparison on these strings is a correct chronological
comparison. The browser does NOT compute the boundary; the server
computes it on the server's UTC clock and only accepts the choice
enum from the browser.

Each endpoint also accepts `cohort="post_obs002"|"all"` (default
`"post_obs002"`). `"all"` includes pre-OBS-002 cycles. **The
`cohort=all` selection is gated behind an explicit user action in
the UI.** As of PHASE-C8 (2026-09-18), the visible cohort control
is a checkbox labelled "Include legacy pre-OBS-002 cycles"
(`<input type="checkbox" id="phase-c-include-legacy">`), which is
unchecked by default. The backend `<select id="phase-c-cohort">`
remains in the DOM (hidden) so `_pc_getCohort()` continues to
work, but its value is mirrored from the checkbox state. Until
the box is ticked, the cohort is `post_obs002` (complete coverage
only) and the user cannot silently include pre-OBS-002 cycles.

When the legacy opt-in checkbox is enabled:

- `cohort` becomes `"all"`.
- A visible coverage warning banner
  (`<div id="phase-c-legacy-warning">`) appears immediately
  below the selector card. It states that pre-OBS-002 cycles may
  be incomplete, that no backfill was performed, and that
  percentages/counts in this view may reflect incomplete
  historical coverage.
- `loadPhaseCAll()` re-syncs the banner visibility on every
  reload so the warning reflects the current state, not stale
  state from a previous session.

Pre-OBS-002 cycles have sparse or absent
`strategy_eligibility.gates[]` and `execution_checks.checks[]`,
so even with `cohort=all` the Strategy Gate Failure Frequency
and Execution Blocker Frequency cards will populate with whatever
pre-OBS-002 data exists — which for the gates card is typically
empty, and for the blockers card is always empty. The default
`cohort=post_obs002` is the recommended mode.

### Phase C cards on the Analytics tab

Phase C does NOT remove or restructure the legacy Analytics cards.
The legacy Performance Analytics, Failed Analysis Breakdown, and
Filter Analysis cards are preserved UNCHANGED and remain visible at
the bottom of the Analytics tab. Phase C adds six new cards
**above** the legacy cards:

1. **Time-range selector card** (`#phase-c-selector-card`)
   - 4-option range selector (`latest` / `today` / `24h` / `7d`).
   - 2-option cohort selector (`post_obs002` / `all`).
   - Coverage status banner (the JS sets `range=X · cohort=Y` text;
     full coverage stats are in the dedicated coverage card).

2. **Decision Funnel card** (`#phase-c-funnel-card`)
   - Reads from `cycle_funnel` only — never `decision_history` (which
     would double-count per-symbol decisions).
   - Forward path stages (rendered as connected blocks in the JS
     with `›` arrows between them):
     Analyzed → Strategy Eligible → Ranked Candidates → Execution
     Attempted → Orders Submitted.
   - Off-path outcomes (rendered in a separate row in the JS,
     explicitly labeled "(off-path)", NEVER as funnel steps that
     flow into submission):
     Execution Blocked, Orders Failed, Not Attempted.
   - Largest dropoff callout computed pairwise over the forward
     path. Drop-off counts are computed from the persisted
     counters, NOT from `decision_history` rows.
   - Card meta line shows `Cycles`, `Range`, and `Cohort`. The
     zero-state headline ("No cycles in this window.") renders when
     `cycles == 0`.

3. **Strategy Gate Failure Frequency card** (`#phase-c-gate-freq-card`)
   - Reads `decision_history.decision_snapshot → $.strategy_eligibility.gates[]`
     for every row in the cohort (HOLD_INELIGIBLE rows are where this
     is most populated).
   - Renders an HTML table with columns: Gate, Total, Passed,
     Failed, Failure Rate, Applied.
   - Each gate row carries a tooltip with the sampled
     `observed_value` and `threshold_value` ranges (sampled from
     persisted JSON only — never from live settings).
   - Default sort: failures DESC, then gate_name ASC (deterministic).
   - Empty state: literal text "No structured strategy gate data in
     this window. Pre-OBS-002 cycles did not persist
     `strategy_eligibility.gates[]`."
   - **Authoritative source**: `gates[].name`, `gates[].passed`.
   - **Limitations**:
     1. **`applied=false` is NOT excluded from the failure count.**
        See "Gate-aggregation future-proofing note" below. Current
        data is unaffected (every persisted gate record has
        `applied=1`), but a schema change that begins writing
        `applied=false` records would inflate failure counts.
     2. **The pre-OBS-002 population is sparse.** Pre-OBS-002
        rows that lack structured `gates[]` will not contribute.
        The test `test_no_hold_ineligible_to_failed_gate_inference`
        enforces that all HOLD_INELIGIBLE rows in post-OBS-002
        must carry `gates[]`.

4. **Execution Blocker Frequency card** (`#phase-c-execution-blockers-card`)
   - Reads `decision_history.decision_snapshot → $.execution_checks.checks[]`.
   - Renders an HTML table with columns: Check, Total, Passed,
     Failed, Applied. A second table shows the
     `first_blocking_check` distribution.
   - Honest universe caveat (rendered in a yellow box with `⚠`):
     "Blocker data only exists for decision_history rows whose
     OBS-001 trace reached `execute_trade`. Currently this is the
     SELL path (SELL_BLOCKED_DYNAMIC). BUY paths and HOLD outcomes
     are not in this universe."
   - `rows_in_cohort` and `rows_with_checks` are exposed so the UI
     can render the gap between the cohort size and the universe
     that actually carries execution-check data.

5. **Outcome Distribution card** (`#phase-c-outcome-card`)
   - HTML table (NOT a Chart.js chart) of `decision.outcome` enum
     values, one row per distinct outcome.
   - Columns: Outcome, Count, Pct.
   - SKIPPED_INVALID_DATA is rendered in its own color (amber
     border) and explicitly labeled "Invalid data (no score, no
     rank)" instead of the raw enum string.
   - SELL_BLOCKED_DYNAMIC, BUY_BLOCKED_DYNAMIC,
     SELL_BLOCKED_NO_POSITION are each rendered as separate table
     rows (not as separate "bars").
   - HOLD_INELIGIBLE is the dominant row in current data.
   - Sort: count DESC, then outcome enum ASC (deterministic).
   - Total pcts sum to exactly 1.0.

6. **Historical completeness / coverage indicator card** (`#phase-c-coverage-card`)
   - Two bands: pre-OBS-002 (legacy) and post-OBS-002 (complete).
   - Per-band, shows: cycle count, complete_cycles, incomplete_cycles,
     total_missing_symbol_decisions, complete-rate.
   - Per-cycle coverage = "complete" iff `cycle_funnel.analyzed_count
     == COUNT(DISTINCT decision_history.symbol for that cycle)`
     (the OBS-002 invariant).
   - The pre-OBS-002 band explicitly states: "Pre-OBS-002 cycles may
     be incomplete. decision_history is sparse for these cycles; the
     dashboard does not backfill missing decisions from current
     settings or raw indicators."
   - The post-OBS-002 band explicitly states: "Post-OBS-002 cycles
     carry a complete decision_history footprint (terminal decision
     coverage invariant)." The OBS-002 long-run validation
     confirmed this for the post-OBS-002 window; see
     `reports/2026-09-14_155755_obs-002-terminal-decision-coverage.md`
     for the validation report.

### Endpoints added (Phase C only)

Five read-only GET endpoints under `/api/phase-c/*`. All return JSON.
All return `range` and `cohort` (where applicable) in the response
envelope. All return `{"error": "..."}` envelopes on validation
failure or runtime exception (HTTP 200 with an error envelope; never
a 5xx).

| Endpoint | Purpose | Authoritative tables/fields | Time-window behavior | Bounded reads | Legacy / incomplete handling |
|---|---|---|---|---|---|
| `GET /api/phase-c/funnel` | Decision Funnel counts for a window (forward path + off-path + largest dropoff). | `cycle_funnel` (`analyzed_count`, `strategy_eligible_count`, `ranked_candidate_count`, `execution_attempt_count`, `execution_blocked_count`, `order_submission_attempt_count`, `order_submitted_count`, `order_failed_count`, `not_attempted_count`). | `range` ∈ `{latest, today, 24h, 7d}` filters `cycle_funnel.cycle_start`. `cohort` ∈ `{post_obs002, all}` defaults `post_obs002`. | Reads `cycle_funnel` only (not `decision_history`); `cycle_funnel` is bounded per cycle so the SUM is finite. | With `cohort=post_obs002`, pre-OBS-002 cycles are excluded. With `cohort=all`, the pre-band cycles contribute honestly (their funnel counters may be 0 or sparse). |
| `GET /api/phase-c/strategy-gates` | Strategy Gate Failure Frequency per gate name. | `decision_history.decision_snapshot → $.strategy_eligibility.gates[]` (`name`, `category`, `applied`, `passed`, `observed_value`, `threshold_value`). | `range` and `cohort` as above. Cohort/range filter `decision_history.cycle_start`. | `json_each(...)` flattens the `gates[]` array. `GROUP BY gate_name, gate_category`. No unbounded loop. Sort: failures DESC, name ASC (deterministic). | Pre-OBS-002 rows without structured `gates[]` contribute zero (they are filtered by `json_each` naturally). Empty state surfaced explicitly. See "Gate-aggregation future-proofing note" below. |
| `GET /api/phase-c/execution-blockers` | Execution Blocker Frequency per check name + `first_blocking_check` distribution. | `decision_history.decision_snapshot → $.execution_checks.checks[]` and `$.execution_checks.first_blocking_check`. | `range` and `cohort` as above. | `json_each(...)` flattens `checks[]`. Two `SELECT`s: per-check aggregation + blocker distribution. Rows are bounded by the cohort. | Universe caveat exposed in response: only `decision_history` rows whose OBS-001 trace reached `execute_trade` carry `checks[]` — currently only the SELL path. `rows_in_cohort` vs `rows_with_checks` exposed for the UI to render the gap. |
| `GET /api/phase-c/outcomes` | Per-decision outcome distribution. | `decision_history.decision_snapshot → $.decision.outcome`. | `range` and `cohort` as above. | One `SELECT ... GROUP BY outcome`. Bounded by the cohort. Sort: count DESC, outcome ASC. | SKIPPED_INVALID_DATA rendered as its own row with distinct label. All outcomes from the `decision.outcome` enum surface, including `None` (rendered as `NULL`). |
| `GET /api/phase-c/coverage` | Pre/post OBS-002 cycle completion coverage. | `cycle_funnel` + `decision_history` (per-cycle distinct-symbol count via correlated subquery). | `range` as above (no `cohort` param; the endpoint itself always splits by band). | One `SELECT` against `cycle_funnel` with a `COUNT(DISTINCT ...)` subquery per row. The query is bounded by the range; pre/post split happens in Python. | Pre-OBS-002 cycles without `decision_history` rows have `distinct_symbols == 0` (LEFT-JOIN-style behavior via the subquery); they contribute `analyzed_count` to `total_missing_symbol_decisions`. They are NEVER backfilled. |

All five endpoints accept `?range=latest|today|24h|7d` and
`?cohort=post_obs002|all` query params with safe defaults
(`range=7d`, `cohort=post_obs002` for the four that take a cohort).

All other endpoints (`/api/opportunities`, `/api/decision`,
`/api/decision-history`, `/api/actionability-summary`,
`/api/cycle-candidates/{cycle_id}`, `/api/score/{symbol}`,
`/api/orders/*`, `/api/positions*`) are UNCHANGED by Phase C.

### Source-of-truth enforcement

- NO endpoint ever recomputes a decision from current settings or
  raw indicators. Every value comes from a persisted column or a
  persisted JSON field.
- `decision.primary_reason` is shown verbatim where surfaced (Phase
  A behavior, preserved); it is NEVER parsed by the dashboard into
  structured fields.
- `analyzed_stocks.signal` is NOT consulted for any Phase C
  analytics (it is consulted only for legacy pre-OBS-001 reads in
  Phase A's renderer fidelity path, which is preserved untouched).
- `failed_analyses.blocked_by` is NOT consulted for Phase C
  analytics (it is consulted only by the legacy Filter Analysis
  card, which is preserved untouched per the Phase A/B "no
  replacement of legacy functionality" rule).
- `fill_confirmed` semantics apply unchanged: a submitted order is
  never shown as filled unless `fill_confirmed === true`. Phase C
  does not introduce or change any order-fill endpoint.
- `ranking.candidate_rank IS NOT NULL` is the only authoritative
  candidate existence test (Phase B contract preserved).

### Gate-aggregation future-proofing note (PHASE-C7, 2026-09-17) — CLOSED

**Status: CLOSED as of PHASE-C7 (2026-09-17 18:14 UTC).** The
`applied=false` defensive gap identified in PHASE-C6 has been
hardened at the SQL level. Only persisted `applied=true` gate
evaluations contribute to the aggregation. The semantic mapping is
locked in by regression tests in
`tests/test_dashboard_phase_c_obs_analytics.py ::
TestGateAggregationAppliedFilter`.

**Semantic mapping (locked):**

| gate.applied | gate.passed | Effect on the response |
|---|---|---|
| `true` (JSON boolean, SQL `1`) | `true` | `passed += 1`, `total += 1` |
| `true` (JSON boolean, SQL `1`) | `false` | `failed += 1`, `total += 1` |
| `false` (JSON boolean, SQL `0`) | (any) | contributes 0 to passed, failed, total |
| missing / malformed / NULL | (any) | contributes 0 to passed, failed, total (fails closed) |

**SQL change (PHASE-C7):** The aggregation's `WHERE` clause now
includes a join-level filter:

```sql
WHERE <cohort_clause> AND <range_clause>
  AND COALESCE(json_extract(gate.value, '$.applied'), 0) = 1
```

This restricts the `json_each` join to rows whose persisted
`applied` value is JSON `true` (or, defensively, the integer `1`).
JSON `false` and missing/malformed values are coalesced to `0` and
excluded. `COUNT(*)` per group therefore equals the count of
`applied=true` gate evaluations (the denominator); `failure_rate`
= `failed / total` uses only the `applied=true` denominator.

**Response-shape invariants (preserved):**
- `total_evaluations` = number of persisted `applied=true` gate
  evaluations (the denominator).
- `applied_count` is still surfaced in the response for backward
  compatibility with the Phase C test surface; by construction it
  now equals `total_evaluations` (every row in the join has
  `applied=true`).
- `failed = total - passed` (unchanged). Because the join is
  filtered to `applied=true`, this counts only
  `applied=true + passed=false` rows.
- `failure_rate = failed / total` when `total > 0`, else `0.0`.
- `rows_in_cohort` and `gate_rows_aggregated` are surfaced as
  before. `gate_rows_aggregated` now equals the count of
  `applied=true` gate evaluations across all gates.

**`inference_rule` string** (now documents the filter):

```
failures counted only where gate.passed == false AND
gate.applied == true; gate.applied == false and
malformed/missing applied both contribute 0 (fail closed);
never from HOLD_INELIGIBLE outcome
```

**Regression coverage (PHASE-C7):**
`tests/test_dashboard_phase_c_obs_analytics.py ::
TestGateAggregationAppliedFilter` — 8 test cases driven by a
`synthetic_phase_c_db` pytest fixture that builds an in-memory
sqlite DB with 6 controlled `decision_history` rows:

| Row | applied | passed | Outcome expectation |
|---|---|---|---|
| `c1` | `true` | `true` | contributes `passed=1, total=1` |
| `c2` | `true` | `false` | contributes `failed=1, total=1` |
| `c3` | `false` | `null` | contributes 0 (excluded) |
| `c4` | missing field | `true` | contributes 0 (fails closed) |
| `c5` | `true` | `false` (sma_uptrend) | contributes `failed=1, total=1` |
| `c6` | (HOLD_INELIGIBLE, empty `gates[]`) | n/a | contributes 0 to any gate |

The fixture monkey-patches `dashboard._phase_c_open_db` to return
the in-memory DB for the duration of each test; pytest restores
the original opener after the test exits.

**No code changes** outside `dashboard.py` (the
`api_phase_c_strategy_gates` endpoint) and the test file. SmartBot
persistence schema, the OBS-001 / OBS-002 invariants, and
`trading_bot.db` are unchanged. The ground-truth DB check on
2026-09-17 confirmed all 718,526 persisted gate records have
`applied=true`, so the new filter is identity for current data and
guards against future schema changes (OBS-003+).

### Mobile-first (current behavior)

- No element requires horizontal scroll on a phone.
- Time-range and cohort selectors use native `<select>` (native
  mobile picker).
- Phase C cards stack vertically (they are `<div class="card">`
  elements inside the existing `.top-tab-panel` flex container).
- **Phase C uses HTML `<table>` elements for the Funnel,
  Strategy Gate Failure Frequency, Execution Blocker Frequency,
  and Outcome Distribution cards.** Chart.js is used by the LEGACY
  cards only (Performance Analytics, Failed Analysis Breakdown,
  Filter Analysis). Phase C does not depend on Chart.js.
- Touch target sizing: the latest/today/24h/7d `<select>`, the
  legacy opt-in `<label class="phase-c-legacy-toggle">`, the
  read-only `<span id="phase-c-cohort-display">` chip, and the
  "🔄 Refresh" button all carry `min-height: 44px` (or rely on
  the inherited 44px media-query override) so the controls meet
  Apple HIG / WCAG 2.5.5 mobile touch target expectations.
- Table overflow: each Phase C table is wrapped in a
  `<div class="phase-c-card-table-wrap">` which carries
  `overflow-x: auto` and `-webkit-overflow-scrolling: touch` in
  the 600px media query. Tables scroll horizontally inside the
  card rather than causing the whole page to scroll.
- Mobile selector layout: under 600px viewport the time-range,
  cohort display chip, and legacy opt-in toggle stack vertically
  (flex-direction: column) with full-width controls and 8px
  gap; the meta banner reflows to the left edge.
- Phase C card padding is reduced under 600px so cards do not
  waste horizontal space.

### Test coverage (tests/test_dashboard_phase_c_obs_analytics.py)

The test file (760 lines, 13 test classes, 59 test functions, ~33 KB
AST-parsed) covers at least:

- Time-window boundaries (`today`, `24h`, `7d`, `latest`) per
  endpoint; UTC-aware, server-clock-based; browser does not compute.
- Funnel aggregation: forward path counts from `cycle_funnel` SUM,
  off-path from `cycle_funnel` SUM; never from `decision_history`
  rows (which would double-count).
- Off-path separation: Execution Blocked and Orders Failed never
  appear in the forward path; they're in a separate field
  `off_path`.
- Terminal outcome aggregation: per outcome enum, sorted count DESC
  + name ASC.
- SKIPPED_INVALID_DATA separation: distinct color, distinct label,
  NOT lumped with HOLD.
- Candidate-rank semantics: `candidate_rank IS NOT NULL` only;
  Phase B's contract preserved (analyzed_count ==
  COUNT(DISTINCT decision_history.symbol) invariant checked against
  the latest completed cycle).
- NO HOLD_INELIGIBLE → failed-gate inference test: a HOLD_INELIGIBLE
  row with empty `gates[]` MUST contribute zero gate failures to
  the Strategy Gate Failure Frequency card (asserted via direct DB
  query in `test_no_hold_ineligible_to_failed_gate_inference`).
- Gate aggregation contract: gate names come from `gates[].name`
  only; never from `primary_reason` substring matching (asserted
  via static grep of the Phase C JS block, plus response-shape
  assertions).
- Zero-state behavior: each card renders a deterministic zero-state
  message (no fabricated counts).
- Legacy rows: pre-OBS-002 cycles never contribute to the funnel
  numerators or denominators when `cohort=post_obs002`.
- Cycle completion invariant: the coverage card uses the same
  `analyzed_count == COUNT(DISTINCT decision_history.symbol)`
  invariant the OBS-002 long-run validation proved.

### Implementation contract

- File: `dashboard.py` adds 5 new read-only endpoints under
  `/api/phase-c/*`. NO existing endpoint changes.
- File: `templates/dashboard.html` adds 6 new `<div class="card">`
  elements with `id="phase-c-*"` (selector + funnel + gates +
  blockers + outcomes + coverage) inside the Analytics tab. The
  legacy Performance Analytics, Failed Analysis Breakdown, and
  Filter Analysis cards are preserved UNCHANGED below the Phase C
  cards; they are NOT moved behind a disclosure and are NOT
  removed. The Phase C JS adds renderers for the 5 data cards and
  a `loadPhaseCAll()` orchestrator that fires when the Analytics
  tab becomes visible (via a MutationObserver on the tab's `style`
  attribute).
- File: `tests/test_dashboard_phase_c_obs_analytics.py` (new)
  covers all acceptance criteria listed above.
- File: `MENTOR.md` (this section) is the source-of-truth spec.
- No `src/` changes. No `trading_bot.db` schema changes. No
  settings changes. No SmartBot runtime changes. No
  OpenClaw runtime/config changes.
- SmartBot `ActiveEnterTimestamp` must remain unchanged across
  deployment.
- The trading dashboard (`dashboard.service`) MAY be restarted ONCE
  for live verification only; this is the same Phase B-approved
  dashboard-restart pattern. SmartBot and the engineering dashboard
  must NOT be restarted.

### PHASE-C8 status (2026-09-18)

PHASE-C8 closed two follow-up items from PHASE-C7's
"Remaining Phase C Risks" list and added the legacy opt-in
contract. The dashboard.html template was extended (not the
backend SQL — `dashboard.py` was not touched in PHASE-C8):

1. **Legacy cohort opt-in (read-only safety):** The
   `cohort=all` value is no longer directly selectable in the
   UI. The visible cohort control is a checkbox
   (`#phase-c-include-legacy`), unchecked by default. The
   backend `<select id="phase-c-cohort">` remains in the DOM
   for `_pc_getCohort()` compatibility but is hidden. The
   checkbox's `onchange` handler mirrors the value into the
   hidden select, updates a read-only display chip
   (`#phase-c-cohort-display`), toggles the coverage warning
   banner (`#phase-c-legacy-warning`), and calls
   `loadPhaseCAll()`. `loadPhaseCAll()` also re-syncs the
   banner visibility on every reload so the warning always
   reflects current state.
2. **Mobile / touch UX:** The 600px media query now enforces
   44px minimum touch target height on the time-range
   `<select>`, the legacy opt-in `<label>`, and the read-only
   cohort display chip; stacks selector controls vertically;
   reflows the meta banner to the left edge; reduces Phase C
   card padding; and wraps every Phase C table inside
   `<div class="phase-c-card-table-wrap">` which carries
   `overflow-x: auto` and `-webkit-overflow-scrolling: touch`
   so tables scroll horizontally inside the card rather than
   causing the page to overflow.
3. **Race-test stabilization:** The pre-existing
   `TestGateAggregationContract::test_no_hold_ineligible_to_failed_gate_inference`
   test was failing intermittently because it issued two
   separate SELECTs against `decision_history` while SmartBot
   was writing concurrently (~21s/cycle). PHASE-C8 combined
   the two SELECTs into a single atomic query using
   `SUM(CASE WHEN ... THEN 1 ELSE 0 END)` so both counts
   derive from one snapshot. Verified 3/3 consecutive runs
   pass against the live DB. The semantic assertion is
   unchanged: every post-OBS-002 HOLD_INELIGIBLE row must
   carry a populated `strategy_eligibility.gates[]` array.

PHASE-C8 added two new test classes
(`TestPhaseCLegacyCohortOptIn`, `TestPhaseCMobileUx`) totaling
18 new test functions to
`tests/test_dashboard_phase_c_obs_analytics.py`.

**Backend (`dashboard.py`) is unchanged in PHASE-C8.** All
PHASE-C7 semantics (`applied=true` filter, `inference_rule`,
fail-closed behavior, response shape) remain intact. The
backend continues to accept both `cohort=post_obs002` and
`cohort=all` as documented.
- The gateway was restarted ONCE during the CHAT-RETRY-001
  deployment on 2026-09-17 00:21:03 UTC (separately tracked in the
  CHAT-RETRY-001 audit archives). No gateway restart is required
  by Phase C itself.

### Reconciliation status (PHASE-C6, 2026-09-17)

This section was reconciled against the existing implementation
in PHASE-C6 (2026-09-17 11:50 UTC). The reconciliation corrected:

- Removed Chart.js references (Phase C uses HTML `<table>`, not
  Chart.js). Chart.js usage in the file is for legacy cards only.
- Replaced "server's local trading date" with "server's UTC
  calendar day" for the `today` range. Implementation uses
  `datetime.now(timezone.utc)` (UTC), not `date('now',
  'localtime')`.
- Replaced "rendered as connected blocks with arrows" with the
  actual JS `›`-separator inline markup.
- Replaced "Headline: 'Analyzed N symbols across M cycles'" with
  the actual meta line ("Cycles, Range, Cohort").
- Replaced "Bar chart"/"rendered as separate bars" with "HTML
  table"/"rendered as separate table rows" for the Outcome
  Distribution card.
- Removed the unverifiable "OBS-002 long-run validation passed
  9,392/9,392 cycles" claim; replaced with a pointer to the OBS-002
  validation report under `reports/`.
- Added documentation of `first_blocking_check` distribution and
  `applied_count` column on the Execution Blocker Frequency card.
- Added explicit per-endpoint table (purpose, fields, time-window
  behavior, bounded reads, legacy/incomplete handling).
- Added "Gate-aggregation future-proofing note" documenting the
  `applied=false` defensive gap and the planned PHASE-C7 fix.
- Corrected "restructured from…into" to "adds to (legacy cards
  remain)"; removed the misleading "advanced/legacy disclosure"
  claim.
- Corrected the "cohort=all gated behind a toggle" claim to
  reflect the actual UI (currently exposed directly in a
  `<select>`, with PHASE-C8 hardening planned).
- Reconciled the Implementation contract to accurately describe
  the `<select>`-based cohort UI and the lack of a disclosure.

## Dashboard Phase C — Test Isolation (PHASE-C10D, 2026-09-20)

PHASE-C10D relocates the 13 LIVE_DB_READ (observational) tests out
of the deterministic Phase C test file so that default pytest
collection excludes them.

**The split:**

- **Deterministic Phase C tests** (`tests/test_dashboard_phase_c_obs_analytics.py`)
  use synthetic in-memory SQLite fixtures wired into
  `dashboard._phase_c_open_db` via `monkeypatch`. They are
  hermetic, fast, and CI-safe. 151/151 PASSED in ~4.6s on
  2026-09-20. ZERO live DB access.

- **Live observational tests** (`tests/observational/test_phase_c_live_observational.py`)
  read the live `trading_bot.db` produced by the running
  SmartBot. They verify invariants that only hold in real
  operation (recent cycle completion, post-OBS-002 coverage,
  gate structure consistency under actual SmartBot writes, etc.).
  13 tests, each marked `pytest.mark.observational` via
  module-level `pytestmark`.

**Default collection policy (pytest.ini):**

- `addopts = -v --tb=short -m "not observational"` — default
  pytest runs do NOT collect `observational` tests.
- `markers = observational: empirical checks against the live
  trading_bot.db; not collected by default pytest runs ...` —
  the marker is registered so pytest does not emit
  "unknown marker" warnings.

**Explicit invocation (release gate / smoke / post-deploy):**

```
pytest -m observational tests/observational/test_phase_c_live_observational.py
```

**Hard rules for observational tests** (documented in
`tests/observational/test_phase_c_live_observational.py` doctring):

- Read-only: never write to `trading_bot.db` (the `_db()`
  helper opens the DB in `mode=ro` URI as defense in depth).
- Never pause, stop, or restart SmartBot.
- Never mutate production state.
- Never call brokerage or network services.
- Tolerate concurrent SmartBot writes: where the assertion
  requires internal consistency across two derived counts,
  use ONE atomic SQL statement (SQLite serializes writes via
  the database lock, so a single statement observes one
  snapshot). The
  `test_no_hold_ineligible_to_failed_gate_inference` test
  preserves the PHASE-C8 atomic-query formulation.
- Avoid repeated expensive endpoint calls when one bounded
  read can prove the invariant.

**Static guard:**

`TestPhaseC10AFactorySelfTest::test_no_deterministic_test_uses_live_db_path`
walks the file's AST and asserts that no test method (other
than the guard itself) is structured to read the live
`trading_bot.db`. The check covers:

- direct `_db()` helper calls;
- `live_phase_c_db` fixture arguments (the documented opt-in
  escape hatch that no test currently uses);
- the `shared_client` and `gates_response_today_post`
  module-scoped live fixtures.

If any of these patterns is reintroduced into this file's
test bodies, the guard fails at run time so the regression
is caught BEFORE it can run live during CI.

**13 LIVE_DB_READ observational tests** (full list of node IDs,
in `tests/observational/test_phase_c_live_observational.py`):
- `TestGateAggregationContract::test_no_hold_ineligible_to_failed_gate_inference` (atomic SQL, OBS-002 regression guard)
- `TestFunnelAggregation::test_funnel_latest_returns_one_cycle`
- `TestFunnelAggregation::test_funnel_24h_returns_something`
- `TestFunnelAggregation::test_funnel_cohort_all_includes_pre_obs002`
- `TestExecutionBlockersHonestUniverse::test_per_check_totals_sum_to_with_checks_rows`
- `TestOutcomeDistribution::test_pct_sums_to_1`
- `TestCoverageIndicator::test_post_band_invariant_holds_in_observation_window`
- `TestCandidateRankSemantics::test_decision_history_invariant`
- `TestPhaseCSharedHelper::test_strategy_gates_response_shape_after_refactor`
- `TestPhaseCSharedHelper::test_strategy_gates_cohort_all_is_superset_of_post_obs002`
- `TestPhaseCSharedHelper::test_funnel_cohort_all_superset_of_post_obs002`
- `TestPhaseCSharedHelper::test_latest_returns_one_or_zero_cycles`
- `TestPhaseCSharedHelper::test_applied_filter_still_excludes_applied_false`

**Implementation contract (PHASE-C10D):**

- File: `pytest.ini` adds `observational` marker and updates
  `addopts` to include `-m "not observational"`.
- File: `tests/observational/test_phase_c_live_observational.py`
  (NEW) holds the 13 relocated tests + minimal live-DB helpers
  (`_db`, `_client`, `shared_client`, `gates_response_today_post`).
  The module declares `pytestmark = pytest.mark.observational`.
- File: `tests/test_dashboard_phase_c_obs_analytics.py` removes
  the 13 relocated tests and the dead helpers
  (`shared_client`, `gates_response_today_post`, `_db`). Adds
  the static guard test (152 tests total, 152/152 pass).
- No `dashboard.py` changes. No `templates/dashboard.html`
  changes. No `src/` changes. No `trading_bot.db` schema changes.
  No SmartBot runtime changes. No OpenClaw config changes.
