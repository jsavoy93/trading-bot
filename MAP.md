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
- `src/core/allocation_policy.py` — REBAL-40-50-10 pure-math allocation
  policy (Josh, 2026-11-25; supersedes the 2026-11-09 REBAL-60-30-10
  split): budget/cash-floor math, swing-runner attribution (order-label
  prefixes + runner SQLite DBs, read-only), ledger-proven ownership
  (FILLED rows only), position classification (OWN / SWING /
  UNATTRIBUTED; unattributed = untouchable but budget-counted).
  `DEFAULT_SWING_ORDER_LABEL_PREFIXES` = `sl_short-` (C4), `rsi2-`
  (RSI(2)), `gf_mid-` (GAPFADE midcap). `normalize_swing_order_label_prefixes`
  widens a persisted value that exactly equals a superseded shipped default
  (`LEGACY_SWING_ORDER_LABEL_PREFIXES`) to the current defaults; custom
  lists and malformed values are untouched / fail closed.
  REBAL-40-50-10 also ships `normalize_persisted_long_term_budget_pct`:
  a one-time, explicit, idempotent rewrite of a persisted
  `long_term_budget_pct` that exactly equals the old shipped default
  (`LEGACY_LONG_TERM_BUDGET_PCT` = 60.0) to the schema default (40.0)
  through `settings_service.save_typed`; custom/malformed values are
  untouched and a second run is a no-op. Called once from the bot's
  `_load_effective_strategy_settings`.
- `src/core/settings_service.py` — `STRATEGY_SETTINGS_SCHEMA` is the
  authoritative default source for every tunable (incl.
  `long_term_budget_pct` default **40.0**, `cash_floor_pct` default
  **10.0** — the remaining **50%** is the external short-term swing
  sleeve, `swing_order_label_prefixes`, `swing_runner_db_paths`);
  DB overrides applied via `load_effective_strategy_settings`.
  NOTE: `smart_bot.py` imports `core.*` (src on sys.path), i.e. a SECOND
  module instance of these files; patch both when a test needs the bot's
  objects.
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

### REBAL-40-50-10 runner sizing (reference, no live configs changed here)
- Sleeve shares are FIXED fractions of account equity and each runner has a
  FAIL-CLOSED hard total open-exposure cap at its own sleeve:
  - main bot 40% (`budget_room` / `max_buy_cost`);
  - C4 `sl_short-` 10% sleeve (`SLEEVE_EQUITY_FRAC`,
    `experiments/strat_023/live_paper_runner.py`), enforced by
    `position_qty` + the sleeve-total check/entry-loop queries;
  - RSI(2) `rsi2-` 10% sleeve (`RSI2_SLEEVE_PCT`,
    `--sleeve-pct` in `experiments/swing_paper/deploy/run_daily.sh`
    (live copy: /root/deploy/rsi2-swing)), enforced
    by `size_rsi2_position` over `rsi2_open_notional`;
  - GAPFADE `gf_mid-` 30% (`DEFAULT_SIZE_FRACTION` 0.03 per entry x
    `MAX_OPEN_POSITIONS` 10, `TOTAL_EXPOSURE_CAP_FRACTION` 0.30), enforced
    by `rules.total_cap_remaining` over
    `runner.open_notional` (positions + submitted unfilled buys).
- Worst case: 40 main + 10 + 10 + 30 = 90% deployed, 10% cash floor
  reserved. An unreadable exposure makes that runner place NO entry.

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

## NOBUY-FIX-001 (no-buy hygiene)
- `src/core/smart_bot.py` — `check_liquidity` uses the real bid/ask spread
  (Fix 1). Fix 2 is TWO ZERO-API stages:
  (a) `get_all_us_symbols()` drops only warrants/rights/units via the pure
  O(1) `is_junk_security` string match — it makes NO `get_market_data()`
  calls, so it stays safe over the ~10k-symbol universe;
  (b) the `universe_min_price` floor (default `UNIVERSE_MIN_PRICE_DEFAULT`
  = $5, tunable via `universe_min_price` in `STRATEGY_SETTINGS_SCHEMA`) is
  enforced in `analyze_multi_timeframe()` / `analyze_symbol()` right after
  `price = latest['close']`, via the pure `_passes_price_floor_value(price)`
  (fail-open on missing/non-finite price). A sub-floor symbol returns
  `_price_floor_ineligible_result()`: HOLD, no scoring, flagged
  `universe_price_floor_ineligible` so `_build_decision_snapshot()` records
  `strategy_eligible=False` with reason "below universe price floor"
  (outcome `HOLD_INELIGIBLE`). Never add a per-symbol price fetch to the
  universe scan.
  `_format_hold_log_line`, `_safe_float`, `_bollinger_position_pct`,
  `_volatility_tier_from_atr_pct` back both HOLD/WEAK log paths, and the
  MTF/single-timeframe `analysis_result` now carries
  `macd`/`bb_upper`/`bb_lower`/`bb_pos`/`vwap_distance`/`volatility_tier`/
  `atr_pct` (Fix 3); `_maybe_cleanup_cooldowns` gates cooldown cleanup on
  100 research writes or an hourly floor (Fix 4).
- `src/database/sqlite_db.py` — `cleanup_old_cooldowns` returns the number
  of deleted rows and logs INFO only when rows were removed (Fix 4).
- Tests: `tests/test_nobuy_fix_001_{liquidity,universe,hold_log,cooldown_cleanup}.py`.
- `src/brokerage/mock.py` — `MockAsset(name=...)` for the junk-filter tests.

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

## Experiments (paper-only, separate state)
- `experiments/gapfade_midcap_paper/` — GAPFADE-MIDCAP-PAPER: S&P 400
  gap-fade experiment (gap <= -4% pre-market, profit-at-open / 2xATR14
  close stop / 3-day cap exits, two shadow trades per entry). PAPER only;
  labels `gf_mid-*`; state under /root/trading-data/gapfade-midcap-paper.
  `rules.py` (pure decisions), `guards.py` (paper guard, exit 2),
  `runner.py` (CLI, dry run default), `state.py`, `broker.py` (part-2
  boundary, stubs), `report_pnl.py` (real vs both shadows PnL from this
  trial's state.json; `--cost-bps` per side, default 27), `early_stop.py`
  (flag only: records a trip in state.json, manages no process;
  `--record`), `systemd/` (USER-unit templates, committed disabled:
  timers `[Install] WantedBy=timers.target`, services none;
  `WorkingDirectory=/root/strat-gapfade`, the post-merge checkout; install
  steps in the README are NOT EXECUTED). Tests:
  `tests/test_gapfade_{rules,guards,passes,runner,report,early_stop,broker,membership}.py`
  plus `tests/test_gapfade_holiday_guard.py`
  plus `tests/test_gapfade_systemd_units.py` (unit templates: ExecStart uses
  `/root/strat-gapfade/.venv/bin/python`, services stay un-enableable).
  S&P 400 membership (`membership.py`): PRIMARY Wikipedia
  `List_of_S%26P_400_companies`, FALLBACK `slickcharts.com/sp400`, both
  parsed from the Symbol column with stdlib `html.parser`; 380-420 unique
  symbols required or the source is skipped; cached `sp400_members.txt`,
  cache only used within 10 days and with a LOUD stale warning, else
  `MembershipError` (fail closed). The old SSGA MDY CSV is dead (404) and
  iShares IJH is 403 from this box — do not reintroduce them.
  Tests: `pytest tests/test_gapfade_*.py -q` with `/usr/bin/python3`'s
  pytest (the runner venv has no pytest).
  Holiday guard (fail closed): BOTH real passes read today's session from
  `GET /v2/calendar` via the existing client before any data load
  (`runner._market_calendar` / `runner._holiday_guard`). A non-session day
  logs one line naming the day and exits 0 with zero orders; an unreadable
  calendar exits 1 with zero orders. `_resolve_day()`'s last-trading-day
  fallback never decides the order day (both builders also refuse when the
  resolved date != today ET). "already ran" now STOPS submission (was
  print-only); `--force-pass` still overrides. `after_close` gates on the
  calendar's real close (13:00 ET on early closes), checked before inputs.
  The timer still fires Mon-Fri; the runner owns the holiday decision.
  GAPFADE-SNAP-FIX: market data uses Alpaca's v2 data API only —
  `GET /v2/stocks/bars` (daily) and `GET /v2/stocks/snapshots?feed=iex`
  (`alpaca.snapshot_prices`, batched `SNAPSHOT_BATCH_SIZE`=200 symbols
  per request; flat `{SYM: {latestTrade, minuteBar, prevDailyBar, ...}}`
  body). The beta `/v1beta1/stocks/snapshots` endpoint 404s and must not
  come back; `tests/test_gapfade_snapshots_v2.py` guards it (fixture
  `tests/fixtures/alpaca_v2_snapshots_iex.json`).

## External (read-only for this bot)
- C4 runner: /root/strat-swing (DB
  experiments/strat_023/live_paper.db, labels `sl_short-*`).
- GAPFADE midcap paper runner: /root/strat-gapfade (labels `gf_mid-*`;
  checkout with its own `.venv`; state under
  /root/trading-data/gapfade-midcap-paper). Attribution is order-label
  only — no runner DB is configured for it.
- RSI(2) runner: /root/deploy/rsi2-swing (labels `rsi2-*`; its
  logs/state.json contains unfilled dry-run shadow entries — do NOT
  trust for attribution).
- Live log/DB/.env: /root/.openclaw/workspace/trading-bot (never
  modify; DB reads via `mode=ro` URI only).
