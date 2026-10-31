# STRAT-025 Part 1 — Paper-Only Intraday Swing Engine

## Executive summary

Built the standalone paper-only intraday swing engine (skeleton + risk/order
machinery) in `experiments/strat_025/engine/`, mirroring the strat_023
standalone pattern. All setup/entry flags default OFF. 37/37 unit tests pass.
No commit made (per task instruction). Paper-only guard, stop-after-fill
sequencing, kill switch, sector cap, daily loss halt, and earnings blackout
are implemented and unit-tested against a mock broker.

## Task / purpose

STRAT-025 Part 1: intraday scanner loop + EOD entries + risk controls +
setup registry placeholders, paper-only, in the strat-025 worktree.

## Branch / commit

- Branch: `research/strat-025-swing` (pre-existing, checked out in
  `/root/.openclaw/workspace/strat-025`)
- Commit: `3ab729a` (base — no new commit; changes left uncommitted per task)

## Files changed (all new, uncommitted)

- `experiments/strat_025/engine/__init__.py`
- `experiments/strat_025/engine/config.py` — flags/env, ALL DEFAULT OFF
- `experiments/strat_025/engine/guard.py` — hard paper-only guard
- `experiments/strat_025/engine/broker.py` — Alpaca PAPER REST wrapper, `sl_short`-tagged orders
- `experiments/strat_025/engine/universe.py` — C4 ∪ main-bot watchlist (read-only)
- `experiments/strat_025/engine/setups.py` — setup registry, placeholders default OFF
- `experiments/strat_025/engine/risk.py` — sizing, sector cap, daily loss halt, earnings blackout, kill switch
- `experiments/strat_025/engine/engine.py` — scan loop, stop-after-fill, 15:45 ET EOD eval
- `experiments/strat_025/engine/README.md` — full docs
- `tests/test_strat_025_engine.py` — 37 tests

## Tests run

Command:
`/root/.openclaw/workspace/trading-bot/.venv/bin/python -m pytest tests/test_strat_025_engine.py -p no:cacheprovider -v`

Exact summary: **37 passed, 1 warning in 1.37s** (warning: unrelated
PytestConfigWarning about `timeout` option from repo pytest.ini).

## Acceptance result

PASS — all 8 build items delivered; safety constraints honored (paper URL
pinned, guard refuses without `TRADING_BOT_PAPER_ONLY=1`, no live creds, no
merge/push, no main-workspace writes, no commit, C4/BUY thresholds untouched).

## Known risks

- Alpaca has no earnings-calendar API → earnings dates are pluggable
  (injected map or `STRAT025_EARNINGS_JSON`); absent data means no blackout.
- Sector map must be provided; unmapped symbols share a conservative UNKNOWN bucket.
- Intraday/EOD bar providers and real setup predicates are Part 2
  (`setup_research/`); placeholders never fire.
- Stop is a market stop; gap-through slippage not modeled (paper acct reflects it).

## Manager decision

Ready for Part 2 wiring (setup predicates + live bars provider) and Josh's
review of the default-OFF flag set.

## Next recommended action

Part 2 agent registers real setup predicates via
`setups.register_setup()` and implements the `bars_provider`; then a
supervised paper dry-run with all flags still OFF before any opt-in.

# Archive — detailed acceptance evidence

Start: 2026-10-31T01:01Z (task received). End: 2026-10-31T01:07Z. Elapsed ~6 min.
Continuity: fresh subagent run; read AGENTS.md, MENTOR.md, AGENT_OPERATING_PLAN
(in workspace context), strat_023 README + shadow_runner.py before coding.

## Acceptance criteria & evidence

1. **Standalone engine in experiments/strat_025/engine/, decoupled from src/**
   - Proof: `grep -rn "from src" experiments/strat_025/` → no matches; engine imports only stdlib/pandas/requests.
   - Result: PASS
2. **Intraday scanner loop, 1–5 min interval, union universe (C4 + main watchlist, read-only)**
   - Proof: `config.py` clamps `STRAT025_SCAN_INTERVAL_SEC` to 60–300;
     `engine.SwingEngine.run_loop` gates on `broker.is_market_open()`;
     `universe.universe_symbols()` = C4 (strat_006r file) ∪ keys of main
     workspace `analysis_status.json` (read-only).
   - Result: PASS
3. **Broker STOP immediately after every entry fill; sequencing verified**
   - Proof: `engine.enter_with_stop` submits market buy → `wait_for_fill` →
     `submit_stop_sell` (GTC). Stop failure → immediate market flatten +
     DB row `status='failed'`. Tests `TestStopAfterFill` (3 tests) with
     MockBroker assert order sequence [(market,buy),(stop,sell)] and
     [(market,buy),(market,sell)] on stop failure.
   - Result: PASS
4. **15:45 ET EOD daily-bar evaluation**
   - Proof: `engine.run_eod` + `eod_eval_time_reached` (configurable
     `EOD_EVAL_TIME_ET="15:45"`, once per day).
   - Result: PASS
5. **Risk controls: 1% cap, sector cap, daily loss halt, earnings blackout**
   - Proof: `risk.py` (`position_size`, `sector_cap_ok`, `DailyLossLimit`,
     `EarningsBlackout`, `RiskManager.approve`); tests TestRiskCaps (6),
     TestSectorCap (3), TestDailyLossLimit (4), TestRiskManagerGates (3).
   - Result: PASS
6. **Paper-only guard identical friction to strat_023**
   - Proof: `guard.enforce_paper_only` refuses without
     `TRADING_BOT_PAPER_ONLY=1`, refuses non-paper URL, refuses any live
     opt-in; tests TestPaperGuard (4).
   - Result: PASS
7. **Kill switch documented + configurable flatten**
   - Proof: `risk.kill_switch_active` (env `STRAT025_KILL_SWITCH=1` or KILL
     file), `engine.handle_kill_switch` flattens when
     `STRAT025_KILL_FLATTEN=1`; tests TestKillSwitch (5); README section.
   - Result: PASS
8. **Setup registry behind config flags DEFAULT OFF**
   - Proof: `setups.register_setup/is_setup_enabled/enabled_setups/evaluate*`;
     placeholders `pullback_trend, breakout_range, gap_reclaim` always False;
     tests TestSetupRegistry (4) incl. default-off.
   - Result: PASS
9. **Unit tests run, exact results**
   - Command: `/root/.openclaw/workspace/trading-bot/.venv/bin/python -m pytest tests/test_strat_025_engine.py -p no:cacheprovider -v`
   - Result: 37 passed, 1 warning (PytestConfigWarning: unknown 'timeout'
     option, pre-existing from repo pytest.ini), 1.37s. PASS
10. **Write scope respected**
    - Proof: `git status -s` → only `experiments/strat_025/` (engine dir +
      README + gitignored swing-free), `tests/test_strat_025_engine.py`, plus
      ignored REPORT.md/reports/. Main checkout never touched. No commit.
    - Result: PASS

## Safety verification

- Paper URL pinned: `config.PAPER_TRADING_URL = https://paper-api.alpaca.markets`;
  `AlpacaPaperBroker` raises on non-paper base_url; guard re-checks at loop start.
- Live trading not enabled anywhere; no credentials read beyond paper env/.env (read-only).
- No merge, no push, no main-branch work, no C4/BUY threshold changes.
