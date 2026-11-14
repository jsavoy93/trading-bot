# MAP.md — Trading Bot Repo Map

Quick orientation for agents working in this repository. Keep entries
short; link files, not paragraphs.

## Core runtime
- `src/core/smart_bot.py` — SmartTradingBot: analysis cycle
  (`run_analysis` ~:6503), BUY/SELL execution (`execute_trade`),
  portfolio actions (`execute_portfolio_actions` ~:8699→ now
  `get_allocation_budget_view` reporting), sell scan
  (`analyze_current_positions_for_selling`), rotation
  (`get_position_scores`).
- `src/core/allocation_policy.py` — REBAL-60-30-10 pure-math allocation
  policy: budget/cash-floor math, swing-runner attribution (order-label
  prefixes + runner SQLite DBs, read-only), ledger-proven ownership
  (FILLED rows only), position classification (OWN / SWING /
  UNATTRIBUTED; unattributed = untouchable but budget-counted).
- `src/core/settings_service.py` — `STRATEGY_SETTINGS_SCHEMA` is the
  authoritative default source for every tunable (incl.
  `long_term_budget_pct`, `cash_floor_pct`,
  `swing_order_label_prefixes`, `swing_runner_db_paths`);
  DB overrides applied via `load_effective_strategy_settings`.
- `src/core/market_calendar.py` — DB-GROWTH-FIX: DST-correct
  `America/New_York` market calendar (session 09:30–16:00 ET, 13:00 ET
  early closes, NYSE holiday lists for 2024–2027). Pure helper:
  `is_market_open`, `is_trading_day`, `ny_trading_date`,
  `ny_day_bounds_utc`, `retention_cutoff`.
  DB-GROWTH-FIX-4 pins the 2026/2027 sets to the official NYSE
  calendar: 2026-11-03 is not a holiday, Good Friday 2027 is
  2027-03-26, and there is no July 2 early close (2026-07-03 is a full
  closure, 2027-07-03 is a Saturday).
- `src/core/sleeve_positions.py` — Sleeve S (short-term) vs L ledger
  tagging. Non-authoritative; ownership questions go through
  allocation_policy instead.
- `src/database/sqlite_db.py` — trades ledger (`get_all_trades`), OBS-001
  decision_history/cycle_funnel tables.
- `src/analysis/portfolio_optimizer.py` — logs rebalance
  RECOMMENDATIONS only; never auto-executes.

## Conventions that bite
- Tests build bots via `SmartTradingBot.__new__` and stub every method
  `execute_trade` calls — new checks must be added to the SimpleNamespace
  helpers in tests/test_exec_003_1_trade_persistence.py,
  tests/test_sleeve_p1_tagging.py, tests/test_smart_bot_decision_paths.py,
  and to `TestExecuteTradeTraceCollection._make_bot` in
  tests/test_obs_001_phase_a_decision_snapshot.py (plus its NOT-RUN
  count literal).
- OBS-001: `OBS_001_EXECUTION_CHECK_ORDER` is asserted against a
  hard-coded tuple in test_obs_001_phase_a_decision_snapshot.py.
- `tests/test_strat026_shadow.py::test_units_not_installed` asserts the
  HOST has no systemd units installed — fails on machines where the
  RSI(2) shadow units are deployed; pre-existing, environmental.

## Database growth controls (DB-GROWTH-FIX-3, observability-only)
- `src/core/smart_bot.py` — `_decision_history_writes_allowed` (closed
  market), `_should_suppress_decision_row` (unchanged HOLD),
  `_maybe_run_retention_prune` (once per NY day, closed market only),
  `run_decision_history_retention_prune`.
- `src/database/sqlite_db.py` — `get_latest_decision_history`,
  `_archive_decision_history_rows` (gzip JSONL, all columns),
  `_count_archive_rows` (read-back verification),
  `prune_decision_history` (small batches, children before parents,
  archive verified BEFORE delete).
- `src/core/settings_service.py` — `Database Growth` settings group:
  `decision_history_market_hours_only`,
  `decision_suppress_unchanged_holds`,
  `decision_snapshot_score_tolerance`,
  `decision_history_retention_days` (default 30),
  `decision_history_prune_enabled`,
  `decision_history_prune_batch_size`,
  `decision_history_archive_dir` (default `research_data/db_archive`).
- Suppression applies ONLY to the `HOLD_INELIGIBLE` outcome and only
  against the LAST stored row for that symbol. There is NO same-day
  heartbeat exception. BUY/SELL/other outcomes are always persisted.
  Any lookup/calendar error falls back to writing the full row.
- DB-GROWTH-FIX-4: the closed-market control is OUTCOME SPECIFIC. While
  the market is closed only `HOLD_INELIGIBLE` and `SKIPPED_INVALID_DATA`
  rows are skipped; every BUY/SELL outcome and its
  `decision_gate_evaluations` / `decision_execution_checks` children are
  always persisted.
- DB-GROWTH-FIX-4: the retention prune writes a SEPARATE gzip JSONL
  archive per table (`decision_history`, `decision_gate_evaluations`,
  `decision_execution_checks`; all columns, BLOBs base64-encoded) in a
  dated sub-folder of the archive dir (default
  `research_data/db_archive` beside the resolved database path). Each
  archive is written, closed, fsynced, read back and its line count
  verified before that table's rows are deleted. No usable archive dir
  => `archive_dir_unavailable` and no rows are deleted. The nightly cap
  is `decision_history_prune_max_rows_per_night` (default 200000, batch
  size 5000) and the prune is skipped with a warning below 2 GB free.
  DB-GROWTH-FIX-5: that max-rows setting is the ONLY nightly limit — the
  batch count is derived from it (`ceil(max_rows / batch_size)`); the old
  hidden 10-batch cap stopped the prune at 50 000 rows.
- Tests: `tests/test_db_growth_fix_acceptance.py` (8 acceptance criteria),
  `tests/test_db_growth_fix_5.py` (no hidden batch limit, direct + bot path),
  `tests/test_db_growth_fix_hold_suppression.py`,
  `tests/test_db_growth_fix_market_calendar.py`,
  `tests/test_db_growth_fix_4.py` (calendar pin, outcome-specific
  closed-market writes, per-table archives, prune cap, disk guard).
  All use temporary databases; the production `trading_bot.db` is
  fail-closed.

## Ledger backfill (PAPER-only)
- `scripts/backfill_symbol_fills.py` — LEDGER-BACKFILL-001: backs up
  broker-confirmed FILLED trades that are missing from the ledger, using
  REAL Alpaca PAPER order history. Dry run by default; a write needs BOTH
  `--apply` AND `--db`. PAPER only (no live escape flag), broker access is
  read-only, timestamped backup before every write, post-write
  verification, and fail-closed quantity reconciliation via
  `allocation_policy.own_net_qty_from_ledger_rows`. Swing fills (`sl_short-`,
  `rsi2-` labels, or ids present in a runner DB) are excluded.
- `tests/test_ledger_backfill_001.py` — regression suite for the tool plus
  the case-insensitive `_broker_position_qty` symbol match.

## External (read-only for this bot)
- C4 runner: /root/strat-swing (DB
  experiments/strat_023/live_paper.db, labels `sl_short-*`).
- RSI(2) runner: /root/deploy/rsi2-swing (labels `rsi2-*`; its
  logs/state.json contains unfilled dry-run shadow entries — do NOT
  trust for attribution).
- Live log/DB/.env: /root/.openclaw/workspace/trading-bot (never
  modify; DB reads via `mode=ro` URI only).
