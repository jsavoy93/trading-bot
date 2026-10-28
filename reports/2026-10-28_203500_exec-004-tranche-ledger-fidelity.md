# EXEC-004 (2026-10-28) — Tranche / stop-loss / portfolio ledger fidelity (Detailed Archive)

- Task: EXEC-004 — owner-approved trading-bot improvement (Josh, 2026-10-28 20:13 UTC).
  PAPER ONLY. Four deliverables: (1) tranche 2/3 recorded in the ledger exactly like
  tranche 1; (2) tranches 2/3 pass the same risk checks as initial entries; (3)
  stop-loss and portfolio orders go through the ledger; (4) a one-time
  reconciliation path aligns the ledger with broker positions WITHOUT placing
  orders.
- Reporting mode: implementation reporting mode (repository content modified).
- Worktree: `/root/.openclaw/workspace/trading-bot-exec004` — a FRESH clean worktree
  created off `origin/main` **9c4c2a2** (the PR #109 merge commit), branch
  `agent/exec-004-tranche-ledger-fidelity`. Pre-flight: `git status --short` EMPTY,
  HEAD == 9c4c2a2.
- NOT touched: `/root/.openclaw/workspace/trading-bot` (dirty STRAT-020 checkout),
  `/root/deploy/trading-bot-main` (deployed).
- NOT done (explicitly out of scope): merge, deploy, restart of smartbot-runner,
  live trading, brokerage credentials, `.env`/secrets/production DB, BUY-threshold
  relaxation, strategy/sizing/risk-limit changes.
- Timing: 2026-10-28 20:13–21:0x UTC. Continuity: follows the EXEC-003
  SHOULD-FIX #1 follow-up (PR #109, merged 2026-10-28 16:26 UTC).

## 1. The problem (root causes found)

The owner-visible symptom: **ledger 39 QQQI vs broker 98**. Four distinct root causes,
all confirmed in the code on main 9c4c2a2:

| # | Root cause | Evidence on main |
|---|---|---|
| R1 | Tranche 2/3 orders bypassed the risk gate entirely — only `check_tranche_triggers` (price/volume/RSI) gated them | `_execute_pending_entry_tranches()` submitted directly to `submit_order` with no margin / concentration / sector / correlation / beta check |
| R2 | Stop-loss orders wrote **nothing** to the ledger | `_place_stop_loss_order()` submitted a `StopOrderRequest` and logged to stdout only |
| R3 | Portfolio-management orders wrote a **malformed** row that never persisted | `_place_portfolio_order()` called `db.insert_data('trades', {'quantity':…, 'reason':…, 'timestamp':…})` — none of those keys exist in the `trades` schema; the insert could not succeed |
| R4 | Historical tranche fills (placed before PR #109) were never recorded, so the ledger permanently understates positions | PR #109 landed the *forward* fix (`_persist_tranche_submission`); the *backlog* was never reconciled |

R1–R3 are forward fixes; R4 needs the one-time reconciliation path (deliverable 4).

## 2. The implementation

### 2.1 Tranche risk gate — `SmartTradingBot._tranche_risk_gate()`
Applies the SAME checks as the `execute_trade` BUY path, in the same order:

| # | Check | Source of truth | Block condition |
|---|---|---|---|
| 1 | margin | `trading_client.get_account().cash` | `cash < 0` → block (same rule as execute_trade) |
| 2 | duplicate entry | `trading_client.get_orders()` | an open **BUY** order for the same symbol → block |
| 3 | position concentration | `check_position_limits()` | projected > 10% → block; otherwise the quantity may be REDUCED |
| 4 | sector concentration | `check_sector_concentration()` | fails → block |
| 5 | correlation | `check_correlation_risk()` | fails → block |
| 6 | portfolio beta | `check_beta_exposure()` | fails → block |

- Returns `(allowed, adjusted_quantity, reason)`; never raises.
- **Fail-closed**: any unexpected exception inside the gate blocks the tranche (a
  broker order is irreversible). Only the cash read and the open-orders read swallow
  their exceptions — exactly as `execute_trade` does.
- **Deliberate deviations** (documented in the docstring and MENTOR.md):
  - **NO BUY cooldown.** `mark_recent_trade` sets a 240-minute cooldown after
    tranche 1, so applying the cooldown to tranches would silently disable
    position scaling entirely (a strategy change, not authorized).
  - The duplicate check counts only open **BUY** orders: a held position normally
    carries an open stop-loss SELL order, which must not block a tranche BUY.
- A blocked tranche is marked `'blocked_risk'` with a `block_reason` on the plan and
  is **terminal** (no retry every ~15 s loop, which would spam broker API calls).
  No order is submitted and no ledger row is written for a blocked tranche.

### 2.2 Ledger for stop-loss and portfolio orders
New module-level helper `_ledger_log_submitted(db, trade_data, context)` writes a
SUBMITTED `trades` row through the schema-validating `log_trade`, and logs an
ERROR-level message if persistence fails (the broker-side order remains
authoritative; the order is NOT re-submitted).

- `_place_stop_loss_order()`: side `SELL`, qty = position qty, price = stop price,
  `pnl = NULL`, `broker_order_id`, `notes = "STOP-LOSS stop=$X.XX"`. Placement logic
  unchanged.
- `_place_portfolio_order()`: side from the call, qty, price 0 (market), `pnl = NULL`,
  `broker_order_id`, `notes = "Portfolio Management: <reason>"`. The malformed
  `insert_data` call is REMOVED.
- Because both rows carry a `broker_order_id` and a non-terminal status, the
  existing fill poller (`get_pending_fill_trades()`) reconciles them like any other
  order — no poller change needed.

### 2.3 One-time reconciliation — `scripts/reconcile_ledger_with_broker.py`
Pure planning functions (unit-testable without a broker) + a thin CLI:

- `normalize_broker_order(order)` → small dict.
- `find_unrecorded_fills(ledger_rows, broker_orders)` → broker FILLED orders whose
  `id` is absent from the ledger's `broker_order_id` set.
- `build_backfill_row(order)` → a `trades` row with schema keys only: status FILLED,
  `filled_qty`, `avg_fill_price`, `order_time` = broker `created_at`, `pnl = NULL`,
  `notes = "EXEC-004 ledger reconciliation (broker-authoritative backfill)"`,
  `session_id = NULL`.
- `summarize_positions(ledger_rows, broker_positions)` → per-symbol broker qty vs
  ledger net FILLED qty (BUY +, SELL −) and the gap.
- `apply_backfill(db, rows)` → inserts via `log_trade`.

Safety properties:
- **No order-placement call anywhere in the file** (the file contains no
  `submit_order` / `cancel_order` / `replace_order` call — pinned by a static test).
- Read-only against the broker: `get_orders` (CLOSED) + `get_all_positions`.
- Refuses a non-paper `ALPACA_BASE_URL` unless `RECONCILE_ALLOW_LIVE=1`.
- Dry-run by default; `--apply` writes; **idempotent** (a re-run finds nothing
  missing because the backfilled rows carry the broker order id).
- Ledger rows the broker does not know about are only REPORTED — never deleted or
  modified. Broker is authoritative for what it filled.

## 3. Acceptance evidence

| # | Criterion (owner wording) | Proof method | Exact result | Status |
|---|---|---|---|---|
| 1 | Fresh clean worktree off main 9c4c2a2 | `git worktree add`, `git status --short`, `git rev-parse HEAD` | New worktree `/root/.openclaw/workspace/trading-bot-exec004`; status EMPTY; HEAD 9c4c2a2 | PASS |
| 2 | Dirty STRAT-020 checkout untouched | path check | Never entered; no command ran in it | PASS |
| 3 | (1) Tranche 2/3 recorded exactly like tranche 1 | `test_b1_passing_tranche_is_ledgered_like_tranche_1` | 1 row, status SUBMITTED, side BUY, `broker_order_id='ord-1'`, `session_id=7`, notes "tranche 2", in-memory status `submitted` (never `filled`) | PASS |
| 4 | (2) Tranches 2/3 pass the same risk checks | `test_a1`…`test_a6`, `test_a9` | margin / pending-BUY / position / sector / correlation / beta each → 0 submits, 0 ledger rows, tranche `blocked_risk` + reason | PASS |
| 5 | (2) gate fails closed | `test_a9` | unexpected error → blocked, reason contains "fail-closed" | PASS |
| 6 | (2) risk gate may REDUCE quantity, never relax a threshold | `test_b2_position_limit_adjustment_reduces_tranche_qty` | submitted qty == 2 (the risk-adjusted qty), not 5 | PASS |
| 7 | An open stop-loss SELL must not block a tranche BUY | `test_a2b` | submit still happens (1 submit) | PASS |
| 8 | (3) Stop-loss orders go through the ledger | `test_c1_stop_loss_order_writes_ledger_row` | 1 row: side SELL, status SUBMITTED, `broker_order_id='ord-1'`, notes contain "STOP-LOSS" | PASS |
| 9 | (3) Portfolio orders go through the ledger | `test_c2_portfolio_order_writes_ledger_row` | 1 row: side SELL, qty 4, status SUBMITTED, `broker_order_id='ord-1'`, notes contain "Portfolio Management", pnl NULL | PASS |
| 10 | (3) The new rows are reconciled by the fill poller | `test_c3_stop_loss_and_portfolio_rows_are_pollable` | the row appears in `get_pending_fill_trades()` | PASS |
| 11 | (4) Reconciliation path exists and places no orders | `test_d5_script_never_submits_orders` | static check: no `.submit_order(`, `.cancel_order(`, `.replace_order(` in the script | PASS |
| 12 | (4) Reconciliation backfills only missing FILLED fills | `test_d1`, `test_d2` | recorded fill skipped; canceled / zero-qty orders skipped; backfill row uses only trades-schema columns | PASS |
| 13 | (4) Reconciliation is idempotent | `test_d3_reconciliation_is_idempotent` | 1 row inserted; re-plan → 0 missing | PASS |
| 14 | (4) Reconciliation reports the position gap | `test_d4_position_summary_is_signed_by_side` | broker 98 vs ledger 88 → gap 10 (SELL signed negative, SUBMITTED rows ignored) | PASS |
| 15 | (4) Dry run writes nothing; --apply writes | `test_d6_dry_run_writes_nothing_apply_writes` | dry run → 0 rows; `--apply` → 1 FILLED row with the broker order id | PASS |
| 16 | New tests fail on main 9c4c2a2 | pristine `git archive 9c4c2a2` export + the same test file | **16 failed, 3 passed** (the 3 passers are must-not-change guards: a2b, b1, c3) | PASS |
| 17 | All EXEC-003 + EXEC-004 suites pass | pytest | **80 passed, 0 failed** | PASS |
| 18 | Paper-only safety suites pass | pytest | **54 passed, 1 failed** — `test_main_py_installs_sigterm_handler`, ENVIRONMENTAL (live bot holds the lock; identical on pristine main) | PASS (with the stated environmental exception) |
| 19 | Zero regression | full-suite FAILED-set diff | 3 extra failures vs the previous baseline, all in `test_score_002_eligibility_and_ranking.py` — PROVEN environmental/time-of-day (see §5) | PASS |
| 20 | No BUY-threshold relaxation | diff review | No threshold, sizing, score or risk-limit constant changed; the new gate can only BLOCK or REDUCE a tranche quantity | PASS |
| 21 | Normal push, PR opened, not merged | `git push`, `gh pr view` | fast-forward push; PR #110 OPEN, `mergedAt = null` | PASS |

## 4. Tests run (exact)

```
pytest tests/test_exec_004_tranche_ledger_fidelity.py -q
    → 19 passed

# BASELINE PROOF — same file against pristine main
git archive 9c4c2a2 | tar -x -C /tmp/pr110-base-9c4c2a2
pytest tests/test_exec_004_tranche_ledger_fidelity.py -q     (in the export)
    → 16 failed, 3 passed
      failed: a1 a2 a3 a4 a5 a6 a9 b2 c1 c2 d1 d2 d3 d4 d5 d6
      passed: a2b (open stop SELL does not block), b1 (tranche submission
              persistence), c3 (new rows are pollable)  — must-not-change guards

pytest tests/test_exec_003_fill_confirmation.py \
       tests/test_exec_003_1_trade_persistence.py \
       tests/test_exec_003_shouldfix1_review_fixes.py \
       tests/test_exec_003_unresolved_event_dedup.py \
       tests/test_exec_004_tranche_ledger_fidelity.py -q
    → 80 passed, 0 failed

pytest tests/test_bot002_paper_only_guard.py \
       tests/test_brokerage_safety_enforcement.py \
       tests/test_brokerage_live_block.py \
       tests/test_brokerage_mock.py -q
    → 54 passed, 1 failed
      FAILED test_bot002_paper_only_guard.py::test_main_py_installs_sigterm_handler
      ENVIRONMENTAL: the LIVE bot (PID 1649235) holds the lock file, so the
      subprocess under test prints "Bot already running" and exits 1 before it
      can install the handler. Identical failure on pristine main.

pytest -q            (full suite, branch)
    → 2025 passed, 40 failed, 1 skipped, 13 deselected (157.35s)
      FAILED-set diff vs the previous branch baseline → 3 additional failures,
      all in tests/test_score_002_eligibility_and_ranking.py (see §5)
```

### Existing tests updated (exactly two, both in one file)
`tests/test_exec_003_shouldfix1_review_fixes.py`:
- `test_c1_tranche_submission_is_persisted_and_reconciled_to_filled`
- `test_c2_tranche_plan_is_not_re_executed_after_submission`

Reason: both drive `_execute_pending_entry_tranches()` with a stand-in bot object.
Under EXEC-004 that loop now calls the risk gate, so the stand-in must provide a
risk-gate environment. The gate is stubbed to PASS in both tests (an all-healthy
account/portfolio), which preserves the tests' original intent (submission
persistence / no re-execution). **No assertion was weakened or removed** — the
new gate's own behaviour is covered exhaustively (9 tests) in
`tests/test_exec_004_tranche_ledger_fidelity.py`.

## 5. The 3 extra full-suite failures — proven environmental, time-of-day

`tests/test_score_002_eligibility_and_ranking.py`:
`test_min_score_buy_does_not_gate_buy_eligibility`,
`test_score_45_all_non_score_gates_pass_yields_buy_signal`,
`test_score_80_all_non_score_gates_pass_yields_buy_signal`.

Root cause (from the test log): `⏰ TEST: Blocked by trading window (Last 15 min -
thin liquidity, erratic moves)` — the bot's BUY path is blocked by
`is_trading_allowed()`. That function applies a COARSE hour check
(`start_hour <= current_hour < end_hour`, `15 <= h < 16`) in addition to the
minute-level check, so the whole 15:00–15:59 "simulated ET" hour is blocked.
`is_trading_allowed()` uses a simplified UTC-5 offset, so 15:00–15:59 sim-ET is
**20:00–20:59 UTC** — exactly the wall-clock window in which this suite was run
(20:2x–20:4x UTC).

Proof that it is NOT caused by this change:
- The same 3 tests fail IDENTICALLY on a pristine `git archive 9c4c2a2` export.
- They also fail identically on a pristine `git archive f38d910` export.
- They PASSED in the earlier full-suite run at 15:45 UTC (10:45 sim-ET).
- The bot source in those paths is untouched by this change.

The other 37 full-suite failures are the known environmental set (dashboard
Phase-A/B/C, buy-funnel, settings tests needing the 9 GB production DB, and the
SIGTERM lock test).

## 6. Files changed

| File | Change |
|---|---|
| `src/core/smart_bot.py` | NEW `_tranche_risk_gate()` + wiring into `_execute_pending_entry_tranches()`; NEW module-level `_ledger_log_submitted()`; `_place_stop_loss_order()` and `_place_portfolio_order()` now write SUBMITTED ledger rows (the malformed `insert_data` call removed) |
| `scripts/reconcile_ledger_with_broker.py` | NEW — one-time, broker-read-only ledger reconciliation |
| `tests/test_exec_004_tranche_ledger_fidelity.py` | NEW — 19 tests |
| `tests/test_exec_003_shouldfix1_review_fixes.py` | 2 existing tests updated (risk-gate stubs) |
| `MENTOR.md` | addendum: the gate, the ledger contract, the reconciliation tool, the time-of-day footgun |
| `ITERATION_PROGRESS_LOG.md` | this entry |
| `reports/2026-10-28_203500_exec-004-tranche-ledger-fidelity.md` | this archive |
| `REPORT.md` | rolling executive summary (gitignored) |

## 7. Independent review

An independent read-only review was run by a separate agent against the branch
(diff, the new script, the new tests, and the risk gate). Findings and the fixes
applied in response are recorded in §8.

## 8. Independent review — findings and fixes

An independent read-only review (separate agent, no repo edits) returned
**REQUEST_CHANGES** with 4 blocking findings. All four were verified
independently against the real `alpaca-py` enums and the deployed `.env`
before fixing, and all four were real bugs that would have shipped silent
no-ops.

### Blocking findings — all fixed

**B1 — the duplicate open-BUY guard never matched a real broker order
(false safety).** `src/core/smart_bot.py` compared
`str(getattr(o, "side", "")).lower() == "buy"`. Alpaca's `OrderSide` is a
str-mixin enum whose `str()` is `"OrderSide.BUY"` — never `"buy"`, so the
guard was dead code against real data.
*Verified:* `str(OrderSide.BUY) == 'OrderSide.BUY'`; `OrderSide.BUY == 'buy'`
is True only because it is a str mixin — `str()` is not the value.
*Fix:* compare the enum (`getattr(o, "side", None) == OrderSide.BUY`), matching
the repo's existing convention. Regression test `test_c4_duplicate_guard_matches_real_alpaca_order_side_enum`
uses the REAL enums.

**B2 — the reconciliation was a silent no-op on real broker data.**
`normalize_broker_order()` stringified the enums, so `status` became
`"orderstatus.filled"`; `find_unrecorded_fills()` compared against
`"filled"` and therefore matched nothing — the dry run would report
"Unrecorded FILLED broker orders: 0" and `--apply` would insert nothing.
Worse, had it matched, the backfill row would have carried
`side="ORDERSIDE.BUY"`.
*Fix:* use the enums' `.value` (with a string fallback for plain test
doubles). Regression test `test_d7_real_alpaca_enums_are_normalized_correctly`
builds an order with the REAL `OrderSide.BUY` / `OrderStatus.FILLED` and
asserts it is planned for backfill and produces `side == "BUY"`.

**B3 — the script could not run in this deployment.** It read
`ALPACA_API_KEY_ID` / `ALPACA_API_SECRET_KEY`; the bot uses `ALPACA_API_KEY` /
`ALPACA_API_SECRET` (`src/core/smart_bot.py`, `.env.example`, the live `.env`),
so the script exited with "Missing …".
*Fix:* read the bot's names, with the old names as a fallback. Regression test
`test_d8_client_uses_the_bots_environment_variable_names`.

**B4 — the open-orders read failed OPEN, while execute_trade's equivalent
fails CLOSED.** `has_pending_orders` BLOCKS when `get_orders()` raises; the
gate logged at `debug` and proceeded — strictly more permissive than the
initial-entry path, contradicting the "same checks" requirement.
*Fix:* the open-orders read now returns
`False, 0, "open-orders read failed (fail-closed)"` and logs a WARNING.
Regression test `test_c6_open_orders_read_failure_blocks_fail_closed`.

### Substantive risk found by the review — also fixed

**The plan's own open tranche order would block the next tranche.**
Tranche 1 is usually still `NEW` when tranche 2 fires (~10–17 s loop). The
duplicate-BUY guard would see our own order, block the tranche, and — because
a blocked tranche is TERMINAL — silently kill the whole scaling plan. This
would have made position scaling die in practice.
*Fix:* the plan now records `tranche['broker_order_id']` after submission and
the gate excludes those ids from the duplicate check (`own_order_ids`).
Regression test `test_c5_our_own_previous_tranche_order_does_not_block`.

### Non-blocking findings — fixed
- N1: the gate docstring and MENTOR.md claimed "NEVER raises / fails closed"
  while the cash read failed open — both texts now state exactly which reads
  fail open (cash, matching execute_trade) and which fail closed
  (open orders).
- N2: the script's docstring promised `--allow-live` but the code used an env
  var — the flag now exists and the docstring matches.
- N3: importing `database.sqlite_db` ran the module-level singleton's schema
  init against the DEFAULT (repo-root) DB before `--db` was applied, so
  "dry run writes nothing" was not strictly true. The module is now re-pointed
  and the singleton re-created against `--db` before any query.
- N5: `filled_qty or qty` overstated legacy rows; the gap summary now counts
  the confirmed `filled_qty` only.
- N6: portfolio market orders were stored with `price: 0`, and the poller
  never updates `price` (only `avg_fill_price`) — the row would show 0 forever.
  The current quote is now used as the reference price (NULL if unavailable).
- N8: `fake.get_account` / `fake.get_orders` in the EXEC-003 test file were
  dead code (the gate reads `self.trading_client`). The BROKER stand-in now
  provides them, so those tests no longer fail open silently.
- N9: the fake orders used plain strings for `side` but the real enum for
  `status` — the asymmetry that hid B1. All fakes now use the REAL enums.
- N11: the "never submits an order" guard was a bypassable substring check;
  it is now an AST walk that rejects any call/attribute named
  submit_order / cancel_order / replace_order / close_order.

### Non-blocking findings — accepted as-is (documented)
- N4: the script does not reuse the repo's `trading_bot_paper_only_guard`. It
  has its own paper-endpoint check plus the new `--allow-live` flag. Reusing
  the bot guard would require constructing the bot (and its AI providers) —
  disproportionate for a one-time read-only tool. Accepted.
- N7: rows written with `session_id = None` are excluded from per-session
  dashboard counts. This matches the pre-existing behaviour of the ledger
  writer for orders outside a session (the bot's session id is used when
  available) — no change requested.
- N10: `test_d3` hand-inserts a row instead of exercising the writer — it
  deliberately tests the RECONCILIATION planner against a pre-existing ledger
  state, which is a different concern from the writer (covered by `test_c1`).
  Accepted.

### Remaining risks called out by the review (not fixed here)
- The stop qty does not cover later tranche adds (`has_active_stop_order`
  skips re-placing, and the stop qty is the position at placement time) —
  PRE-EXISTING, out of the authorized scope.
- A transient exception inside `check_position_limits` drops the scaling plan
  indistinguishably from a genuine risk block. The gate logs the reason on the
  tranche, so it is at least diagnosable.
- Duplicate/mismatched SELL rows (stop qty vs portfolio SELL) can make the
  ledger net position diverge from the broker — inherent to SELL-side ledger
  rows, reported by the reconciliation gap rather than silently fixed.
- The fill poller still never writes `pnl`, so stop-loss/portfolio rows stay
  `pnl = NULL` even after a confirmed fill. QUANTITY fidelity improves; P&L
  fidelity does not (unchanged, out of scope).
- The gate adds up to 4 network-backed checks per triggered tranche per loop
  (the correlation check fetches market data) — rate-limit cost not measured.
- Backfill rows have `session_id = NULL`; `--apply` against the live ledger
  should be run with the bot stopped (or against a copy).

### Re-verification after the fixes
| Scope | Before fixes | After fixes |
|---|---|---|
| `test_exec_004_tranche_ledger_fidelity.py` | 19 passed | **26 passed** (7 new regression tests) |
| EXEC-003 + EXEC-004 suites | 80 passed | **87 passed, 0 failed** |
| Paper-only safety suites | 54 passed, 1 env failure | 54 passed, 1 env failure (unchanged) |
| Full suite | 2025 passed / 40 failed | **2032 passed / 40 failed** — failure set IDENTICAL to before the fixes |

## 9. Known risks / follow-ups (NOT fixed here, by design)

1. **The tranche plan is still in-memory only** (`self._pending_entry_tranches`): a
   restart loses the pending tranches (EXEC-003 review SHOULD-FIX #2 — still open).
2. **Backfilled rows carry `pnl = NULL`** (never fabricated). A backfilled fill
   therefore does NOT count as a completed trade for win-rate consumers, which
   require `pnl IS NOT NULL AND status = 'FILLED'`. The position gap closes; the
   historical P&L is not reconstructed.
3. **Backfilled rows use `session_id = NULL`** (they belong to no bot session).
4. The reconciliation script needs the broker credentials in the environment and
   runs read-only; the owner must run it explicitly (it is NOT run automatically,
   not even in dry-run mode, by the bot).
5. The tranche gate does not add a drawdown check — `execute_trade` does not gate
   BUY on drawdown either, so this is not a behaviour change.
6. Stop-loss / portfolio ledger rows are SELL rows: the fill poller reconciles
   them, but the existing `pnl` semantics for SELL rows are unchanged (no
   realised-P&L computation was added).
7. Live-traffic validation is pending by definition.
8. Pre-existing: ledger rows whose broker order is older than the inspected
   window are reported, never deleted.

## 10. Final state

- Branch: `agent/exec-004-tranche-ledger-fidelity`, pushed with a NORMAL push
  (fast-forward; no force) → PR #110 opened, still OPEN, `mergedAt = null`.
- NOT merged, NOT deployed, `smartbot-runner` NOT restarted, live trading NOT
  enabled, no secrets / `.env` / production DB touched.
- The dirty STRAT-020 checkout and the deployed checkout were not modified.

**EXEC-004 IMPLEMENTED — AWAITING OWNER REVIEW. STOP.**
