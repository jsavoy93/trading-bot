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
