# BOT-003 Amendment — Upper bound on session_start

**Branch:** `agent/bot003-dashboard-active-session-selection`
**Commit:** `d522c15` — "BOT-003 amendment: add upper bound to filter future-dated fixtures"
**PR:** https://github.com/jsavoy93/trading-bot/pull/77 — OPEN, MERGEABLE, NOT
auto-merged. Awaiting Josh's explicit approval.

## Status

**Amendment applied and live-verified.** SmartBot continues running in
PAPER mode throughout. Live verification confirms the dashboard now
correctly reports the real current runner session regardless of any
future-dated fixture rows.

## What changed since the v2 commit (`72c81e9`)

Josh caught a contradiction in the BOT-003 v2 report. The single-sided
lower bound

    session_start_epoch >= runner_process_start_epoch

does NOT exclude future-dated ACTIVE rows. A future-dated row's epoch
seconds (e.g. 2099-01-01 → ~4.1e9) are numerically GREATER than the
runner's start epoch (2026-09-11 → ~1.8e9), so the row passes the
lower-bound filter. The fixture 71804 only lost under v2 because it
had a lower auto-increment id than 71935. If a future-dated row were
inserted with id > 71935, it would have won under v2.

## Bug demonstration (committed as a regression test)

`tests/test_bot003_amend_future_fixture_bug.py::test_DEMONSTRATE_future_higher_id_wins_under_v2`
inserts a real runner session (id=1, session_start=now) and a future
fixture (id=2, session_start=2099-01-01), then asserts the real
session wins. Under v2 the test FAILS — the future fixture wins.
After the amendment, the test PASSES.

## Fixed selection rule

A row qualifies as the runner's active session iff ALL hold:

    status = 'ACTIVE'
    AND session_end IS NULL
    AND runner_start_epoch <= session_start_epoch
    AND session_start_epoch <= now + CLOCK_SKEW_SECONDS

where:

- `runner_start_epoch` is read from `/proc/<pid>/stat` field 22
  (starttime in clock ticks since boot) and converted to Unix epoch
  seconds via `/proc/uptime`.
- `now` is the current Unix epoch seconds at lookup time.
- `CLOCK_SKEW_SECONDS = 300` (5 minutes). This is a documented
  tolerance for clock drift between the runner process and the
  lookup process, plus a small buffer for in-flight scheduling. It
  is NOT a hard-coded year.

Among qualifying rows, `id DESC` selects the most recently created.

## Linkage limitation (documented)

This is a **runtime-window heuristic**, not PID ownership. The
`trading_sessions` schema does NOT store the runner PID, so we cannot
assert that any specific row was created by this specific PID. We can
only assert that the row's `session_start` is chronologically
consistent with the runner's process lifetime. In production, the
SmartBot's single-instance lock (`/tmp/trading_bot.lock`) prevents
concurrent runners, so this heuristic is reliable.

If two runners were to run concurrently (e.g. a test harness
overlapping the live runner), this heuristic could not distinguish
their rows. This is a known limitation documented in the method
docstring. A future schema change could add a `runner_pid` column for
true PID ownership; that change is out of scope for BOT-003.

## Tests added (9 in `tests/test_bot003_amend_future_fixture_bug.py`)

1. `test_DEMONSTRATE_future_higher_id_wins_under_v2` — proves the
   bug class: under v2, future-dated ACTIVE row with HIGHER id wins.
   This test would have failed against v2.
2. `test_old_future_fixture_with_lower_id_cannot_win` — future
   fixture with lower id cannot win.
3. `test_future_fixture_with_HIGHER_id_cannot_win` — future fixture
   with HIGHER id cannot win (the key bug case).
4. `test_normal_real_runner_session_is_selected` — real runner
   session selected when no fixtures pollute.
5. `test_stale_pre_runner_ACTIVE_row_cannot_win` — pre-runner ACTIVE
   row excluded by lower bound.
6. `test_multiple_legitimate_post_start_sessions_select_newest_valid`
   — among multiple legitimate sessions, newest wins (id DESC).
7. `test_runner_inactive_returns_none_does_not_falsely_report` —
   invalid/non-existent PIDs return None.
8. `test_no_mutation_history_rows_preserved` — before/after
   snapshots match; no rows mutated or deleted across 10 lookups.
9. `test_get_active_session_fallback_still_uses_id_desc` — documents
   that the fallback (used when runner is not active) is
   intentionally non-runner-aware and returns the highest-id ACTIVE
   row regardless of timestamp validity.

All 9 tests PASS after the amendment.

## Properties preserved

- No hard-coded exclusion of session 71804 (or any other id).
- No hard-coded year/date workaround.
- No schema change; no migration; no row mutation; no row deletion.
- Historical rows preserved verbatim.
- Future-dated ACTIVE fixture rows cannot poison runtime status.
- Stale pre-runner ACTIVE rows cannot poison runtime status.
- Backward compatible: `get_active_session()` callers still get the
  most recent ACTIVE row.
- No trading strategy / scoring / threshold / risk sizing changes.
- No paper/live configuration changes.
- SmartBot not stopped, not restarted, not reconfigured.

## Files changed

- `src/database/sqlite_db.py` — `get_active_session_for_runner(pid)`
  amended with upper bound `session_start_epoch <= now + 300`.
- `tests/test_bot003_amend_future_fixture_bug.py` (NEW) — 9 tests
  including the bug demonstration.

## Live verification (SmartBot still running in PAPER mode)

```json
{
  "alpaca_api_reachable": true,
  "smartbot_runner_active": true,
  "active_session_id": 71962,
  "active_session_start": "2026-09-11T01:01:44.977325",
  "active_session_source": "runner-pid",
  "fully_ready": true
}
```

DB direct query:
- Most recent ACTIVE row by id DESC: 71962 (real current runner)
- 71804 (year-2099 fixture) is correctly excluded by the upper bound
  (its epoch ~4.1e9 is far past `now + 300s` ~1.8e9).

## Full safe suite

`TESTING=1 UNIT_TESTING=1 ./.venv/bin/python -m pytest tests/ -q`
→ **1164 passed, 1 failed** in 78.92s.

The 1 failure is `test_template_renders_red_dot_when_alpaca_unreachable`.
**Reconfirmed pre-existing**: verified to fail identically on clean
current main (without BOT-003 changes) via `git stash`. The test
expects a stale "API Offline" legend string that was updated in
BOT-001 to "Runner Active, No Session". NOT a BOT-003 regression.
Out of scope per "Do NOT fix unrelated dashboard issues" constraint.

`git diff --check HEAD` clean.

## SmartBot state

- Runner unit: `active + enabled`
- Exactly 1 process: **PID 702290** (unchanged)
- `/tmp/trading_bot.lock` preserved with original owner PID
- 0 trades (no natural signals in this window)
- PAPER-ONLY mode: `ALPACA_BASE_URL=https://paper-api.alpaca.markets/v2`,
  `TRADING_BOT_PAPER_ONLY=1`
- No fixture/history rows touched (verified by row-count snapshot
  test)
- No crash/restart loop (`NRestarts=0`)
- SmartBot not stopped, not restarted, not reconfigured during the
  amendment.

## Decisions / Risks

### Decisions

- **Two-sided window** (lower AND upper bound) is the minimum
  deterministic filter that catches both pre-runner and
  future-dated ACTIVE rows.
- **CLOCK_SKEW_SECONDS = 300 (5 minutes)** is generous for any
  realistic clock drift and tolerant of test-environment noise,
  but tight enough to exclude any session dated beyond the near
  future.
- **`id DESC` ordering** remains the tiebreaker for "most recently
  created" — strictly monotonic and clock-independent.

### Risks

- If the runner's clock drifts more than 5 minutes ahead of the
  dashboard's clock (or vice versa), legitimate sessions could be
  excluded. This is unlikely in production (NTP-corrected
  systems drift <1s; even uncorrected VMs typically drift
  tens of seconds at most). Documented as a limitation.
- The runtime-window heuristic cannot distinguish concurrent
  runners. Production is protected by the single-instance lock.
  Test harnesses must not run concurrently with the live bot.
- The 71804 fixture row remains in the DB unchanged. This is
  intentional (per "do NOT delete historical rows" constraint).
  The fix excludes it via the upper bound; the row itself is
  untouched.

## Manager review decision

**ACCEPT**: the BOT-003 amendment closes the contradiction Josh
identified. The fix is now actually robust against future-dated
fixtures with any id ordering. Live verification confirms the
dashboard reports the real current runner session (71962) instead
of the year-2099 fixture (71804). All 9 amendment tests pass; all
12 original BOT-003 tests still pass; full safe suite 1164/1165
(the 1 failure is the documented pre-existing unrelated failure).

Ready for Josh's review and merge.
