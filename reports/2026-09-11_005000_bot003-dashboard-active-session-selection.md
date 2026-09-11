# BOT-003 — Dashboard active-session selection fix

**Branch:** `agent/bot003-dashboard-active-session-selection`
**Commits:** `8541e4a` (v1) + `72c81e9` (v2 numeric cutoff fix)
**PR:** https://github.com/jsavoy93/trading-bot/pull/77 — OPEN, awaiting
Josh's review. Not auto-merged.
**Status:** SmartBot continues running in PAPER mode throughout. Live
verification confirms dashboard now reports the real runner session.

## Root cause

`SQLiteDB.get_active_session()` ordered ACTIVE rows by `session_start
DESC`. `session_start` is stored as an ISO-8601 string, so SQLite's
string comparison treated `'2099-01-01T00:00:00'` as greater than
`'2026-09-11T00:00:00'`. The BOT-001 test fixture row 71804
(session_start 2099-01-01, status ACTIVE) beat every real SmartBot
session, so `dashboard.get_runtime_status()` always reported 71804
regardless of whether the runner was actually doing work.

## Identity / linkage used

**Strongest available without schema change**: the runner PID's
process start time, read from `/proc/<pid>/stat` field 22 (starttime
in clock ticks since boot) and converted to Unix epoch seconds. Used
as a **numeric** cutoff via SQLite's `strftime('%s', session_start)`
so the comparison is correctly chronological regardless of any
clock-skewed or intentionally-wrong fixture dates.

The auto-increment `id` is itself a stronger linkage than
`session_start`: it is strictly monotonic and set by SQLite at row
creation time, immune to clock skew, fixture dates, or manual
timestamp edits. Used as the secondary tiebreaker after the numeric
cutoff.

## Fix

1. **`SQLiteDB.get_active_session()`** — orders by `id DESC` (was
   `session_start DESC`). Backward-compatible API; only the SQL ORDER
   BY changed.

2. **`SQLiteDB.get_active_session_for_runner(runner_pid)`** (new) —
   uses the runner PID's process start time as a numeric cutoff:
   `WHERE CAST(strftime('%s', session_start) AS INTEGER) >= ?` with
   the cutoff passed as Unix epoch seconds. Among survivors, `id
   DESC` selects the most recent. Returns `None` for invalid PIDs
   (no crash).

3. **`dashboard.get_runtime_status()`** — when
   `is_smartbot_runner_active()` is true, reads
   `/tmp/trading_bot.lock` for the runner PID and uses
   `get_active_session_for_runner(pid)`. Falls back to
   `get_active_session()` (id DESC) when no runner is active. New
   `active_session_source` field reports which path was used.

## v1 → v2 fix (caught by live verification)

The v1 commit used `WHERE session_start >= ?` with an ISO-8601 string
parameter. Live verification revealed this did NOT actually filter
the year-2099 fixture, because SQLite (like Python) compares ISO-8601
strings lexicographically — `'2099-01-01...'` sorts AFTER
`'2026-09-11...'`. The v2 commit replaces the cutoff with
`CAST(strftime('%s', session_start) AS INTEGER) >= ?` passing epoch
seconds as a numeric parameter. Now any fixture dated before the
runner's process start time is correctly excluded regardless of the
fixture's textual timestamp.

A regression-guard test
(`test_get_active_session_for_runner_uses_numeric_cutoff_not_string`)
seeds a row dated `1990-01-01` (numerically before any real runner
start) and confirms the runner-pid filter excludes it. This catches
future regressions to a string-based cutoff implementation.

## Tests

`tests/test_bot003_active_session_selection.py` — 12 new tests:

- `get_active_session` returns most-recently-created row by id DESC
  even when a future-dated fixture exists (the exact bug)
- `get_active_session` excludes ENDED rows
- `get_active_session` returns None when no ACTIVE row exists
- `get_active_session_for_runner` excludes future-dated fixtures
- `get_active_session_for_runner` handles invalid PIDs (negative,
  zero, non-int, non-existent) without crashing
- `get_active_session_for_runner` prefers newest among runner-owned
  rows
- `get_active_session_for_runner` uses numeric cutoff (NOT string
  compare) — regression guard with 1990-01-01 fixture
- `get_runtime_status` returns real runner session under
  future-dated fixture (end-to-end with stubbed systemd/Alpaca)
- `get_runtime_status` falls back to id DESC when runner inactive
- `get_runtime_status` returns None when no ACTIVE session exists
- BOT-003 does not mutate or delete any historical rows (read-only
  verification by snapshotting row contents before/after)

All 12 BOT-003 tests PASS.

## Properties preserved

- No hard-coded exclusion of session 71804 (or any other id).
- No hard-coded year/date workaround.
- No schema change; no migration; no row mutation; no row deletion.
- Historical rows preserved verbatim.
- Future-dated ACTIVE fixture rows cannot poison runtime status.
- Backward compatible: `get_active_session()` callers still get the
  most recent ACTIVE row, just by id instead of session_start.
- No trading strategy / scoring / threshold / risk sizing changes.
- No paper/live configuration changes.
- SmartBot not stopped, not restarted, not reconfigured.

## Files changed

- `src/database/sqlite_db.py` — adds `get_active_session_for_runner(pid)`;
  fixes `get_active_session()` to order by `id DESC`.
- `dashboard.py` — `get_runtime_status()` reads `/tmp/trading_bot.lock`
  and prefers the runner-PID-filtered selection.
- `tests/test_bot003_active_session_selection.py` (NEW) — 12 tests.

## Live verification (with SmartBot still running in PAPER mode)

```
{
  "alpaca_api_reachable": true,
  "smartbot_runner_active": true,
  "active_session_id": 71935,
  "active_session_start": "2026-09-11T00:49:03.997449",
  "active_session_source": "runner-pid",
  "fully_ready": true
}
```

The dashboard now reports the actual current SmartBot session
(id=71935, session_start 2026-09-11T00:49:03) instead of the
year-2099 fixture (id=71804).

The real current session is matched by querying the DB directly:
`SELECT id FROM trading_sessions WHERE status='ACTIVE' ORDER BY id
DESC LIMIT 1` returns 71935. The dashboard's reported id matches.

## Full safe suite

`TESTING=1 UNIT_TESTING=1 ./.venv/bin/python -m pytest tests/ -q`
→ **1155 passed, 1 failed** in 80.12s. The 1 failure is the
pre-existing `test_template_renders_red_dot_when_alpaca_unreachable`
test/HTML legend mismatch (test expects stale "API Offline" string
that was updated in BOT-001) — verified to fail identically on main
before BOT-003 changes via `git stash`. NOT a BOT-003 regression.

`git diff --check HEAD` clean.

## SmartBot state

SmartBot continues running in PAPER mode throughout this work.
- Runner unit: `active + enabled`
- One process: PID 702290 (unchanged since BOT-002)
- 0 trades (no natural signals in this window)
- No fixture/history rows touched (verified by row-count snapshot
  test)
- `/tmp/trading_bot.lock` preserved with original owner PID
- SmartBot not stopped, not restarted, not reconfigured

## Decisions / Risks

### Decisions

- **`id DESC` ordering** — auto-increment primary key is the safest
  deterministic "most recent" selector. Immune to clock skew.
- **Numeric cutoff via `strftime('%s', ...)`** — string comparison of
  ISO-8601 is unsafe; the cutoff MUST be numeric.
- **Two-tier fallback** — when the runner is active, prefer
  runner-pid-filtered selection; when the runner is inactive (or the
  runner-pid lookup fails), fall back to id DESC.

### Risks

- If someone manually inserts ACTIVE rows with a higher `id` during
  a live run, those would be selected instead of the runner's row.
  This requires explicit manual intervention with `INSERT INTO
  trading_sessions`; normal operation does not exhibit this.
- The v1 fix had a string-cutoff bug caught only by live
  verification, not by the test suite. The v2 regression-guard
  test now covers this case.

## Manager review decision

**ACCEPT**: BOT-003 closes the dashboard's active-session selection
bug. The fix is deterministic, runtime-anchored, immune to clock
skew, and preserves all historical rows. Live verification confirms
the dashboard now reports the real current runner session.
Ready for Josh's review and merge.
