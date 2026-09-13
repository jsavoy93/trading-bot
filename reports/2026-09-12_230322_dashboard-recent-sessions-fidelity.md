# Dashboard Recent Sessions fidelity fix — full report

- **Repository**: trading-bot
- **UTC date**: 2026-09-12 23:03
- **Mode**: Implementation reporting
- **Task ID**: dashboard-recent-sessions-fidelity (informal; no
  AGENT_BACKLOG slot — small read-model fix)
- **Branch**: `fix/dashboard-recent-sessions-fidelity`
- **Commit**: `b80eecb` — Fix Dashboard History > Recent Sessions data fidelity
- **PR URL**: https://github.com/jsavoy93/trading-bot/pull/new/fix/dashboard-recent-sessions-fidelity
- **Trace archive**: `/root/.openclaw/audit-archives/trading-bot/2026-09-12_135106_dashboard-recent-sessions-trace.md`

## 1. ROOT CAUSE

History → Recent Sessions rendered `Symbols` and `Trades` from the
`trading_sessions.total_symbols_processed` and
`trading_sessions.total_trades_executed` scalar columns. Those scalars
are only persisted by `SmartTradingBot.end_session()` as per-session
deltas against baselines captured at `start_session()`. The baseline
capture happens *before* the cumulative counters are reset, so for
every session after the first the delta is `0`. This makes the scalar
unusable as a per-session count. The `trades` table (the only
authoritative record of submitted orders) has 0 rows currently, so
`Trades = 0` happened to be factually correct by coincidence.

The future-dated fixture `id = 71804` is a `2099-01-01` row originally
seeded by `tests/test_bot003_active_session_selection.py`
(`_seed_session(session_start="2099-01-01T00:00:00+00:00", ...)`). The
dashboard query `get_sessions()` orders by `session_start DESC` and
the string compare `'2099' > '2026'` puts it first. BOT-003 fixed
the active-session selector to use `id DESC` and a runner
start-time window, but the dashboard read query was never updated.

## 2. SYMBOL COUNT IMPLEMENTATION

For each `trading_sessions` row in the Recent Sessions list:

```
IF EXISTS (SELECT 1 FROM decision_history WHERE session_id = X)
   Symbols = COUNT(DISTINCT decision_history.symbol) WHERE session_id = X
ELSE
   Symbols = trading_sessions.total_symbols_processed  -- legacy fallback
```

`DISTINCT symbol` ensures that repeated analysis of the same symbol
across multiple cycles in one session counts once (the user's
explicit example: 30 symbols analyzed across 10 cycles → Symbols =
30, NOT 300). The DISTINCT semantic also scales correctly when one
future session contains multiple cycles.

Implementation lives in
`dashboard.get_recent_sessions_with_truthful_counts()` /
`_aggregate_session_counts()`. The aggregation query runs once per
dashboard render with `WHERE session_id IN (...)` and `GROUP BY
session_id`.

## 3. TRADE COUNT IMPLEMENTATION

For each `trading_sessions` row in the Recent Sessions list:

```
Trades = COUNT(trades.id) WHERE trades.session_id = X
```

The `trades` table is the only source. The legacy
`trading_sessions.total_trades_executed` scalar is NOT consulted.
Historical pre-OBS-001 trades that cannot be reliably associated with
a specific session are simply not counted, rather than inventing a
count. The implementation note in MENTOR.md documents this honestly.

## 4. FUTURE SESSION FILTER

Read-layer only; no DB row is mutated, hidden, or deleted. The
BOT-003-style skew bound is applied at query time:

```
session_start <= now(UTC) + DASHBOARD_RECENT_SESSIONS_MAX_FUTURE_SKEW_SECONDS
```

where `DASHBOARD_RECENT_SESSIONS_MAX_FUTURE_SKEW_SECONDS = 300`,
matching `CLOCK_SKEW_SECONDS = 300` in
`src/database/sqlite_db.py` (used by `get_active_session_for_runner`).

Session 71804 (`session_start = "2099-01-01T00:00:00+00:00"`) is
~73 years in the future and is excluded by this filter. It remains
present in `trading_sessions` and is still queryable directly via
`db.get_sessions()`; it simply does not qualify for the Recent
Sessions read model. `test_future_fixture_excluded_and_db_row_unchanged`
explicitly proves both directions: the fixture does not appear in the
list, AND the underlying `trading_sessions` row is byte-identical
before and after the read.

A session whose `session_start == now + 300s` exactly is INCLUDED
(`<=` semantics), matching BOT-003.

## 5. SORTING

After filtering, the read model sorts as:

```
ORDER BY session_start DESC, id DESC
```

The `id DESC` tiebreak makes ordering deterministic when two
sessions share the same `session_start` (e.g. seed fixtures in
tests). `test_recent_sessions_sort_deterministically` and
`test_recent_sessions_sort_tiebreak_by_id_desc` cover both shapes.

## 6. LEGACY FALLBACK

For any session that has zero rows in `decision_history` (i.e. a
pre-OBS-001 session), the read model falls back to
`trading_sessions.total_symbols_processed`. This is the only place
the legacy scalar is consulted. Once OBS-001 Phase A is active and
all new cycles persist decisions, the fallback is essentially
dead code — but it keeps the read model honest about the truth of
historical pre-OBS sessions.

`test_legacy_session_without_decision_history_uses_scalar` proves
this directly.

## 7. UI RESULT

Columns kept exactly as before:

- ID — `trading_sessions.id` (auto-increment primary key).
- Start Time — `trading_sessions.session_start` rendered with the
  existing `to_central` filter.
- Trades — now reads `session.trades_count` (new field from the
  helper).
- Symbols — now reads `session.symbols_count` (new field from the
  helper).
- Action — existing Logs button (`viewSessionLogs({{ session.id }})`).

Cycles column was explicitly NOT added per the user's spec.

For the live data:

- Session 78077 (recent ENDED, post-OBS-001): now renders
  `Symbols = 30`, `Trades = 0` (was Symbols = 0, Trades = 0).
- Session 71804 (2099 fixture): now absent from the top of the list
  (was first).
- Session 9002 / 9001 (real recent): now first and second (were
  buried below the 2099 fixture).

## 8. TESTS

`tests/test_dashboard_recent_sessions_fidelity.py` (new, 12 tests)
proves the 9 acceptance criteria in the user's spec:

1. `test_post_obs_session_shows_distinct_symbols_from_decision_history` —
   AC-1: 30 decision_history symbols → Symbols = 30.
2. `test_repeated_symbol_across_cycles_counts_once` —
   AC-2: two cycles with overlapping symbols → Symbols = 3
   (the union, not the sum).
3. `test_legacy_session_without_decision_history_uses_scalar` —
   AC-3: legacy scalar fallback.
4. `test_trade_count_comes_from_trades_table` —
   AC-4: trades count = COUNT(trades.id), not the broken scalar.
5. `test_session_with_no_trades_shows_zero` —
   supplementary: zero-trade sessions render 0.
6. `test_future_fixture_excluded_and_db_row_unchanged` —
   AC-5 + AC-6: 2099-01-01 fixture is excluded AND the DB row is
   unchanged after the read.
7. `test_recent_sessions_sort_deterministically` —
   AC-7: session_start DESC ordering.
8. `test_recent_sessions_sort_tiebreak_by_id_desc` —
   AC-7: id DESC tiebreak on same session_start.
9. `test_sessions_with_no_trades_render_zero` —
   AC-8: zero-trade rows.
10. `test_read_model_does_not_mutate_decision_history_or_trades` —
    supplementary: read-layer only.
11. `test_no_bot_runtime_files_modified_by_helper` —
    AC-9: code-shape guard against accidentally moving the helper
    into `SmartTradingBot`.
12. `test_session_at_skew_boundary_is_included` —
    boundary: `session_start == now + 300s` is INCLUDED (BOT-003
    `<=` semantics).

## 9. FULL SUITE

```
$ TESTING=1 UNIT_TESTING=1 .venv/bin/python -m pytest tests/test_dashboard_recent_sessions_fidelity.py
======================== 12 passed, 2 warnings in 2.86s ========================

$ TESTING=1 UNIT_TESTING=1 .venv/bin/python -m pytest tests/test_bot001_dashboard_status.py \
    tests/test_bot001_session_counters.py tests/test_bot001_session_lifecycle.py \
    tests/test_bot003_active_session_selection.py tests/test_bot003_amend_future_fixture_bug.py \
    tests/test_dashboard_api_app.py tests/test_dashboard_security.py tests/test_dashboard_timeline.py \
    tests/test_dashboard_recent_sessions_fidelity.py
================= 1 failed, 204 passed, 24 warnings in 28.99s =================

$ TESTING=1 UNIT_TESTING=1 .venv/bin/python -m pytest --ignore=tests/archive
=========== 5 failed, 1245 passed, 83 warnings in 88.61s (0:01:28) ===========
```

The 5 failures across the full suite are pre-existing on `main`
(verified by stashing the commit and re-running):

- `tests/test_bot001_dashboard_status.py::test_template_renders_red_dot_when_alpaca_unreachable`
  — env-dependent (depends on `is_smartbot_runner_active` returning
  False in the test environment).
- `tests/test_bot002_paper_only_guard.py::test_main_py_installs_sigterm_handler`
  — bot002 SIGTERM handler test.
- `tests/test_score_002_eligibility_and_ranking.py::test_score_45_all_non_score_gates_pass_yields_buy_signal`
- `tests/test_score_002_eligibility_and_ranking.py::test_score_80_all_non_score_gates_pass_yields_buy_signal`
- `tests/test_score_002_eligibility_and_ranking.py::test_min_score_buy_does_not_gate_buy_eligibility`

All five are unrelated to the Recent Sessions read model and are
documented as pre-existing failures in the commit message.

## 10. SEMANTIC TRADING CHANGES

**NONE.** No changes to:

- `SmartTradingBot.start_session()` or `end_session()` (the
  per-session baseline bug is **explicitly NOT** fixed in this task
  per the user's spec).
- session counter behavior.
- scoring, eligibility, ranking, execution, sizing, risk, brokerage.
- OBS-001 decision persistence / decision_snapshot schema.
- fixture data (session 71804 is not modified or deleted).

The change is purely read-model:

- New dashboard helper (`get_recent_sessions_with_truthful_counts`)
  and a thin aggregation helper (`_aggregate_session_counts`).
- Two field name swaps in the Recent Sessions template row.
- The legacy `get_recent_sessions()` wrapper is preserved for
  backward compatibility with any external consumer that still
  reads the unfiltered, unsorted raw row list.

## 11. FILES CHANGED

```
MENTOR.md                                        |  66 +++
dashboard.py                                     | 193 ++++++-
templates/dashboard.html                         |   4 +-
tests/test_dashboard_recent_sessions_fidelity.py | 560 +++++++++++++++++++++++
4 files changed, 816 insertions(+), 7 deletions(-)
```

## 12. BRANCH

`fix/dashboard-recent-sessions-fidelity`

## 13. COMMIT

`b80eecb` — Fix Dashboard History > Recent Sessions data fidelity

Pushed to `origin/fix/dashboard-recent-sessions-fidelity`.

## 14. PR

https://github.com/jsavoy93/trading-bot/pull/new/fix/dashboard-recent-sessions-fidelity

Not merged (per AGENT_OPERATING_PLAN rules: never merge into main,
always require Josh's approval).

## 15. BLOCKERS

None. The fix is complete, tested, and pushed.

Open follow-up (out of scope for this task):
`SmartTradingBot.start_session()` / `end_session()` baseline bug
makes `trading_sessions.total_symbols_processed` and
`total_trades_executed` scalars always 0 after the first session.
This task side-steps the bug at the read layer; the underlying bug
should be addressed in a separate Josh-approved task.
