# Archive — STRAT-SWING-PAPER per-strategy order tagging (2026-11-03)

Task: Per-strategy order tagging for paper swing strategies (C4, RSI(2))
Branch: agent/swing-paper-order-tagging-20261103 (off main @ 3a0d6e2)
Commit: a0d0ad4
Mode: implementation
Start (UTC): 2026-11-03 00:36 | End (UTC): 2026-11-03 00:50

## Acceptance criteria & evidence

1. **client_order_id labels on every order, format `<strategy>-<SYMBOL>-<MMDD>-<n>`; stops get `-stop`.**
   - Proof: `tests/test_swing_paper_rules.py::TestLabels` / `TestSequence`;
     `experiments/swing_paper/rules.py::make_client_order_id`,
     `make_stop_client_order_id`, `make_labels`, `next_sequence`.
   - Result: `c4-AMD-1102-1`, `rsi2-NVDA-1102-1`, `c4-AMD-1102-1-stop`
     exact-format asserts pass; sequence derivation counts entries + stops
     and ignores other days/symbols; length guard ≤ 48 chars (Alpaca
     limit) with worst-case `rsi2-GOOGL-1102-99999-stop` = 25 chars.
   - Status: PASS

2. **Overlap rule: C4 first; RSI(2) skips symbols C4 holds or ordered that day (positions + open orders by label).**
   - Proof: `rules.py::rsi2_overlap_blocked`; runner calls it before
     building RSI(2) candidates and logs `reason: c4_overlap` skips.
   - Result: 5 tests cover position-held, order-same-day, order-other-day
     (not blocked), foreign/rsi2 labels (not blocked), empty (not blocked).
   - Status: PASS

3. **RSI(2) daily cap: max 4 new buys/day, ranked lowest RSI(2).**
   - Proof: `rules.py::apply_rsi2_daily_cap` (sort by rsi2 ASC, symbol ASC
     tiebreak; slice to cap, default 4); runner applies it to candidates.
   - Result: 5 tests (ranking, cap limit, default=4, tiebreak, empty/0).
   - Status: PASS

4. **Budget slices inside 30% short-term bucket; configurable split (default 50/50); each strategy sizes only from its own slice.**
   - Proof: `rules.py::BudgetSlices` (`slice_dollars`, `remaining_dollars`,
     `size_order` whole-share sizing; shares must sum to 1.0); runner sizes
     each buy from its own slice with open-notional tracking in state.
   - Result: 7 tests incl. cross-strategy isolation (c4 usage leaves rsi2
     slice untouched), custom 25/75 split, exhaustion, invalid inputs.
   - Status: PASS

5. **Per-strategy P&L report from Alpaca orders/fills by label prefix.**
   - Proof: `experiments/swing_paper/report_pnl.py` — closed orders
     paginated GET, fill-leg FIFO matching per symbol/strategy, positions
     attributed via latest labeled buy, snapshots for last quote; outputs
     trades, open positions, realized and unrealized per strategy
     (table or `--json`). Import + paper-URL lock test included.
   - Result: module imports and is paper-locked; live attribution requires
     paper-account activity (none placed; dry-run default, no creds used).
   - Status: PASS (code); live-output verification deferred to first
     approved paper run.

6. **Unit tests for labels, overlap, cap, slices; run all tests.**
   - Proof: `python3 -m pytest tests/test_swing_paper_rules.py -q`
     → 35 passed. Full suite on branch: 44 failed, 2116 passed, 1 skipped.
     Clean `main` worktree (commit 3a0d6e2): 44 failed, 2081 passed —
     identical failure set (dashboard Phase C analytics, settings service
     API validation, strat026 systemd-unit-not-installed checks,
     supervisor evidence bundle — all environment-dependent, unrelated to
     this change; this box lacks the systemd units / exact lib versions
     those tests assume). Delta = +35 passed, 0 new failures.
   - Status: PASS

## Safety constraints honored

- Work confined to /root/.openclaw/clean/trading-bot; the
  /root/.openclaw/workspace/trading-bot repo was never touched; paper
  credentials read ONLY from ALPACA_API_KEY/ALPACA_API_SECRET env vars.
- Paper endpoint hard-locked (`paper-api.alpaca.markets` asserted in code
  and tests); no live code path; no credentials requested or used.
- STRAT026_LOG_ONLY untouched (`experiments/strat_026_shadow/` unchanged).
- Frozen research parameters reused exactly (C4: RSI(2)<10, close>SMA200,
  3.0×ATR stop, 10d time; RSI(2): RSI(2)<10, vol20≥1M, close≥$5,
  SPY>SMA200, 2.0×ATR init stop, 3.0×ATR chandelier, +2R partial, 15d).
  No BUY threshold relaxed.
- No merge, no deploy, no timers, no chmod/chown under /root; single-shot
  runner with dry-run default and KILL_SWITCH halt.

## Files changed

experiments/swing_paper/{rules,signals,runner,report_pnl}.py,
experiments/swing_paper/README.md (new), tests/test_swing_paper_rules.py
(new), .gitignore (+2 lines), REPORT.md (rolling, ignored),
reports/2026-11-03_004813_swing-paper-order-tagging.md (this archive).

## Timing & continuity

Single session, 2026-11-03 00:36–00:50 UTC. ITERATION_PROGRESS_LOG.md
entry appended. Next action: Josh reviews PR; merge only after explicit
chat approval.
