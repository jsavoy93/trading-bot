# STRAT-025 Part 2 — Pre-Registered Swing Setup Research — Detailed Report

- Task: STRAT-025 Part 2 — pre-registered swing setup research (9 setup variants)
- Branch: `research/strat-025-swing` (worktree `/root/.openclaw/workspace/strat-025`)
- Commit: none (task explicitly forbids committing; all Part 2 changes are uncommitted)
- Run ID: `strat025-part2-20261031T023037Z`
- Start: 2026-10-31T01:01Z (task start) · End: 2026-10-31T02:30Z · Elapsed ≈ 1h29m wall
  (backtest execution dominated: ~2h of dev grid runs across sessions/processes)
- Continuity: continuous (no prior Part 2 state; Part 1 by another agent left untouched)
- Reporting mode: implementation (repository content modified)
- Status: DONE — REJECT all nine setups; holdout never touched

## 1. Executive summary

Nine pre-registered swing setups were screened on daily bars 2020–2026 over the union of
the STRAT-022/C4 universe (STRAT-006R 379 liquid names + 276 usable delisted additions
from the STRAT-023 recheck) and the main-bot quality watchlist — 672 symbols, 475 with
at least one eligible symbol-day. **Zero setups passed the frozen development bar**
(STRAT-022 §10 evidence bar: mean net > 0 at 10 bps/side AND t ≥ 2.0 AND ≥ +10 bps over
a matched 1000-draw random-entry baseline). Therefore **the holdout window
(2025-01-01 → 2026-10-28) was never touched** — `run_setup.py holdout` was executed and
skipped all ten variants. The PASS list is empty (`signals.py::PASSING_SETUPS == []`).
No production/paper wiring is justified.

## 2. Pre-registration (frozen before any backtest)

- File: `experiments/strat_025/setup_research/PREREGISTRATION.md`
- sha256: `cf55863311f4b06e9d4aaadabf16c1a68f736c4f1d437662919d63b15f5aaa2d`
  (recorded in `PREREGISTRATION.sha256`; verified after the run — unchanged)
- Contents: exact entry/exit rules for all 9 variants, universe definition, point-in-time
  eligibility filter, 10 bps/side cost, 1000-draw seeded baseline (seed 20261031),
  dev window 2021-01-01→2024-12-31 / holdout 2025-01-01→2026-10-28, per-setup grid,
  frozen selection rule (≥100 dev trades → highest per-trade Sharpe at 10 bps), frozen
  pass bar (mean net > 0, t ≥ 2.0, edge ≥ +10 bps over baseline; holdout: mean > baseline
  and t > 0), and 6 pre-declared caveats.

### Citation of the STRAT-022 t-stat bar
`experiments/strat_022/PREREGISTRATION.md` §10 ("Pass/fail bar (frozen)"):

> A config PASSES dev if BOTH: 1. mean net per-trade return (10 bps) > 0 with t-stat
> ≥ 2.0, AND 2. mean net per-trade return (10 bps) exceeds the matched baseline mean by
> ≥ 0.10% (10 bps) per trade. Holdout PASSES if the selected config's holdout mean net
> return (10 bps) > baseline holdout mean and t-stat > 0.

This bar is reproduced verbatim in STRAT-025 PREREGISTRATION §10.

## 3. Data and universe

| Item | Value |
|---|---|
| Source (listed names) | Alpaca **paper** data API, `adjustment=raw`, daily, 2020-06-01→2026-10-28 |
| Source (delisted names) | DATA-001 parquet shards (read-only, untracked) at `trading-bot/experiments/data_001/bars_shards` |
| Universe | STRAT-006R curated 379 ∪ 276 delisted (of 307; 31 had <220 in-window bars) ∪ 101 watchlist tickers (incl. ETFs) = 672 CSVs |
| Eligible symbols | 475 (≥1 symbol-day passing vol20 ≥ 1M and close ≥ $5) |
| Eligible symbol-days (dev) | 333,154 |
| Liquidity/price filter | trailing 20d mean volume ≥ 1,000,000 AND close ≥ $5 (point-in-time) |
| Cost | 10 bps per side (net = gross − 20 bps) |
| Baseline | 1000 seeded random-entry draws/config, matched per-symbol counts, same exits/costs; comparison metric = mean of per-draw mean net |
| Download calls | 397 Alpaca calls (listed) + 87 parquet shard reads (delisted) |

## 4. Acceptance evidence (criterion → proof → result → status)

Criterion 1 — "Pre-registration exists BEFORE any backtest, with sha256."
- Proof: file existence + `sha256sum`; git-independent timestamp ordering
  (`PREREGISTRATION.md` written and hashed before `prepare_data.py` ran).
- Result: `PREREGISTRATION.sha256` = `cf558633…5aaa2d`; re-verified after the run
  (unchanged). No backtest was executed before the file existed (`_run_dev.log`
  timestamps precede no earlier run; the only pre-prereg commands were doc reads and
  universe construction).
- Status: **PASS**

Criterion 2 — "Nine setup variants, tested separately, exact rules pre-registered."
- Proof: `PREREGISTRATION.md` §4 (a,b,c,d1,d2,e,f1,f2,g) vs implementation in
  `setups.py`; parity test `test_signals.py::test_parity_with_research_implementations`.
- Result: all 8 pure-DailyFrame setups byte-identical between research (`setups.py`) and
  shipped (`signals.py`) implementations; RS setup implemented cross-sectionally in
  `run_setup.py` (rank over eligible names vs SPY, 63d and 126d windows) exactly as
  pre-registered (top decile entry event, close > SMA200).
- Status: **PASS**

Criterion 3 — "Method: ATR stop + time stop exits; 10 bps per side; random-entry baseline
with ≥1000 draws; same t-stat bar as STRAT-022; tune on dev only; holdout once at end."
- Proof: `engine.py` (`hypothetical_trades`, `anti_overlap`, `baseline_draws` with
  `n_draws=1000`), `run_setup.py` (SEED=20261031, N_DRAWS=1000, T_BAR=2.0,
  EDGE_OVER_BASE=0.0010), `results/summary.csv` (`baseline_draws` = 1000 for every row).
- Result: every config shows `baseline_draws=1000`; exits = ATR stop + time stop (plus
  the pre-registered gap-fill profit exit for d2); dev-only selection; holdout executed
  once and skipped for all 10 variants (`results/run_holdout.txt`).
- Status: **PASS**

Criterion 4 — "Outputs: scripts, per-setup results JSON/CSV, RESULTS.md with table
(trades, avg net return, t-stat dev/holdout, baseline comparison, PASS/FAIL), standalone
DataFrame→bool signal functions for each PASSING setup."
- Proof: directory listing + `results/summary.csv` + `RESULTS.md` §3 + `signals.py`.
- Result: all scripts present; 10 per-setup dev JSONs + selection JSON + supplement
  JSONs + `summary.csv`/`summary.json`; `RESULTS.md` table has trades, avg net, t-stat,
  baseline mean (and p5/p95 in CSV), edge, PASS/FAIL, plus an explicitly empty holdout
  column set (no holdout run); `signals.py` ships all nine standalone functions with
  `PASSING_SETUPS = []` (no passing setup ⇒ no PASS-list functions, registry asserted
  empty by test).
- Status: **PASS**

Criterion 5 — "Do not modify experiments/strat_025/engine/**."
- Proof: `git status` / file mtimes — Part 1 engine files untouched by Part 2.
- Result: Part 2 wrote only under `experiments/strat_025/setup_research/`, plus a
  `.gitignore` addition and this report's files. No `engine/**` file was created,
  edited or deleted.
- Status: **PASS**

Criterion 6 — "Never relax C4/BUY thresholds; paper only; no merges/pushes; do not git
commit; do not touch trading-bot (read-only copying of data files fine)."
- Proof: `git log` (no new commit on the branch), `git status` (changes uncommitted),
  no writes outside the strat-025 worktree except reads from trading-bot.
- Result: branch head still `3ab729a`; all Part 2 output uncommitted; only read access
  to `/root/.openclaw/workspace/trading-bot` (its `.env` for paper creds, its
  `analysis_status.json`, its DATA-001 parquet shards — read-only).
- Status: **PASS**

Criterion 7 — "Final output: per-setup results table, which setups passed holdout and at
what t-stat, PASS-list function file paths, caveats/risks."
- Proof: `RESULTS.md` §3/§5/§7 + summary CSV + this report §5.
- Result: table delivered; **no setup passed dev, so no setup was run on holdout
  (none passed holdout; no holdout t-stat exists)**; PASS-list file path =
  `experiments/strat_025/setup_research/signals.py` with an empty registry; caveats
  enumerated.
- Status: **PASS**

## 5. Per-setup results (dev window only — no holdout was run)

| # | Setup | Selected cfg (ATR×time) | Dev trades | Dev avg net (10 bps) | Dev t | Baseline avg net | Edge (bps) | Dev verdict |
|---|---|---|---|---|---|---|---|---|
| a | EMA20 pullback in uptrend | 3.0× / 10d | 13,657 | −0.2192% | −2.591 | +0.1111% | −33.0 | FAIL |
| b | 20d-high breakout, volume > 1.5× | 3.0× / 10d | 6,821 | −1.0304% | −4.472 | −0.0266% | −100.4 | FAIL |
| c | Minervini VCP (≥2 contractions + pivot) | 3.0× / 10d | 368 | −0.2695% | −0.572 | +0.2315% | −50.1 | FAIL |
| d1 | Gap-and-go (>2% gap, holds) | 3.0× / 10d | 8,182 | −1.7810% | −7.099 | −0.1923% | −158.9 | FAIL |
| d2 | Gap-fill fade (>2% gap down, reclaims) | 2.5× / 10d | 12,851 | −0.2446% | −0.685 | −0.2380% | −0.7 | FAIL |
| e | RS leaders vs SPY (63d / 3m) | 3.0× / 20d | 2,881 | −0.4134% | −1.644 | +0.6306% | −104.4 | FAIL |
| e | RS leaders vs SPY (126d / 6m) | 3.0× / 20d | 2,597 | +0.0698% | +0.246 | +0.7597% | −69.0 | FAIL |
| f1 | Inside day | 3.0× / 10d | 15,444 | −0.2491% | −3.044 | +0.1105% | −36.0 | FAIL |
| f2 | NR7 | 2.5× / 10d | 17,445 | −0.2942% | −4.157 | +0.1150% | −40.9 | FAIL |
| g | RSI(2) < 10 uptrend, exit distinct from C4 | 3.0× / 5d | 12,249 | +0.0413% | +0.802 | −0.0663% | +10.8 | FAIL (edge ✓, t ✗) |

Holdout column: **not applicable / not run for any variant** (prereg §7/§10 lock the
holdout to dev-passers). Machine-readable: `results/summary.csv`, `results/summary.json`,
`results/<setup>_dev.json`, `results/_dev_selection.json`, logs `results/run_dev.txt`,
`results/run_holdout.txt`.

### RSI(2) variant vs C4 — documented difference
- Entry: identical to STRAT-022 C4 (`close > SMA200` AND Wilder RSI(2) < 10 + liquidity filter).
- Exit: **different** — C4 exits on `close > SMA5` (profit exit) / 3.0×ATR stop / 10-day
  time stop; setup g removes the SMA5 profit exit entirely and uses only ATR stop +
  time stop (grid 2.5/3.0 × ATR, 5/10 days).
- Outcome: removing the SMA5 exit leaves +4.1 bps/trade at t=0.80 (edge bar barely met)
  → FAIL; the C4 edge appears to come from the SMA5 profit exit. C4's own numbers are
  not re-derived here (STRAT-023 / PR 116 remain authoritative for C4).

### Supporting diagnostics
- Baseline draw spread (dev, per setup): p5/p95 of per-draw mean net are roughly
  −0.19%/+0.18% (e.g. EMA20 pullback) — the +10.8 bps edge of setup g is inside the
  baseline's own sampling noise, so it is not a near-miss worth re-tuning (re-tuning is
  forbidden by prereg §12.4).
- Setup b and d1 buy strength after extended moves (next-open fill after a volume/price
  thrust) and lose 1.0–1.8% per trade net of costs.
- Setup e's baseline is strong (+0.63%/+0.76% per trade) because the ATR-3/20-day exit
  engine on this universe captures the 2021–2024 drift; the RS entry adds no edge.

## 6. Supplementary (post-hoc, labelled) universe robustness check

Context: STRAT-025 **Part 1** (same branch, different agent) established that the live
bot's actual watchlist is the key set of `trading-bot/analysis_status.json` (200 symbols),
191 of which were outside the frozen prereg §3 universe (mostly micro-caps). The frozen
prereg used the `quality_symbols` list in `src/core/smart_bot.py`.

- Downloaded the 191 extras (189 with usable bars) → `data_watchlist_extra/` (gitignored).
- Only **49 of 189** ever satisfy the prereg point-in-time filter → 13,479 eligible
  symbol-days vs 333,154 (≈4%); signal-day contribution ≈3–4% per setup
  (RSI(2): +512/16,231; EMA20 pullback: +1,095/31,088; VCP: +8/269).
- Dev sensitivity re-run on the union universe (861 symbols) for the three closest
  setups (`results/*_dev_union_supplement.json`, `results/run_supplement.txt`):

| Setup | cfg | n | avg net | t | baseline | edge (bps) | Verdict |
|---|---|---|---|---|---|---|---|
| rsi2_uptrend_v2 | 3.0×/5d | 15,486 | +0.015% | +0.32 | −0.072% | +8.7 | FAIL (unchanged) |
| gap_fill_fade | 2.5×/10d | 15,581 | +0.922% | +1.26 | −0.250% | +117.2 | FAIL (unchanged; t < 2.0) |
| rs_leaders_126 | 3.0×/20d | 2,711 | +0.105% | +0.36 | +0.755% | −65.0 | FAIL (unchanged) |

No verdict changed; no setup becomes a PASS; the holdout remains untouched. The
gap-fill fade's positive result in the union universe is driven by illiquid small-caps
and is explicitly flagged as micro-cap concentration, not as evidence.

## 7. Files changed (Part 2, all uncommitted)

New — `experiments/strat_025/setup_research/`:
- `PREREGISTRATION.md`, `PREREGISTRATION.sha256`
- `universe.py`, `prepare_data.py`, `prepare_watchlist_extra.py`
- `engine.py`, `setups.py`, `run_setup.py`, `run_extra_sensitivity.py`,
  `check_watchlist_extra.py`, `make_summary.py`
- `signals.py`, `test_signals.py`
- `RESULTS.md`
- `results/` — `_dev_selection.json`, `_watchlist_extra_check.json`, 10 `*_dev.json`,
  3 `*_dev_union_supplement.json`, `summary.csv`, `summary.json`, `run_dev.txt`,
  `run_holdout.txt`, `run_supplement.txt`
- `_data_manifest.json`, `_watchlist_extra_manifest.json` (provenance logs)
- `data/`, `data_watchlist_extra/` — daily bars, **gitignored** (regenerable)

Modified:
- `.gitignore` — added `experiments/strat_025/setup_research/data/` and
  `.../data_watchlist_extra/` (672+191 CSVs must not enter the repo; mirrors the
  STRAT-022 convention).
- `REPORT.md`, `reports/2026-10-31_023037_STRAT-025-part2-setup-research.md` (this file),
  `ITERATION_PROGRESS_LOG.md` (Part 2 entry appended).

Not touched: `experiments/strat_025/engine/**` (Part 1 agent's scope), `src/`,
config, DBs, secrets, OpenClaw config, `/root/.openclaw/workspace/trading-bot`
(read-only).

## 8. Tests run

Command:
`/root/.openclaw/workspace/trading-bot/.venv/bin/python -m pytest experiments/strat_025/setup_research/test_signals.py -p no:cacheprovider -q`

Exact summary: **12 passed, 1 warning in 1.80s** (warning: pre-existing
`pytest.ini` unknown option `timeout`).
Coverage: indicator preparation; parity of all 8 pure-DataFrame signal functions
between `setups.py` and `signals.py`; bool-Series/shape contract; PASS-registry state;
no-lookahead truncation equivalence (signal on a truncated series == prefix of the full
series) for the 7 non-cross-sectional setups.

Backtest "tests" (research runs, logs in `results/`):
- `results/run_dev.txt` — dev grid, 10 variants, 39 grid configs, 1000 baseline draws each.
- `results/run_holdout.txt` — holdout invoked once; all 10 variants skipped (no dev passer).
- `results/run_supplement.txt` — post-hoc union-universe sensitivity (3 setups).

## 9. Known risks and caveats

1. Survivorship bias upward (only partially mitigated: 276 of 307 delisted names had
   enough bars; the universe remains anchored on currently-known names) → observed
   negative edges are conservative.
2. Close-checked ATR stops; gaps through stops priced at the worse close (no intraday data).
3. `adjustment=raw` (unadjusted) prices — split artifacts possible ($5 floor mitigates).
4. Multiple comparisons: 10 variants × ≤4 configs screened on one dev window; no
   multiplicity correction (pre-declared, STRAT-022-consistent); the untouched holdout and
   the +10 bps edge guard are the protection.
5. Baseline anti-overlap asymmetry (baseline may hold more concurrent positions than the
   strategy) — inherited STRAT-022 convention.
6. RS decile ranks computed over the eligible subset only; names without SPY-matched
   history excluded; the RS baseline is unusually strong, compressing measured edge.
7. VCP sample size small (368 dev trades; ≥100 bar satisfied) — the exact pre-registered
   daily-bar contraction definition is restrictive; no looser variant was explored.
8. The post-hoc union-universe check cannot promote any setup; gap-fill fade's positive
   union result is micro-cap driven and must not be read as a PASS.
9. No holdout evidence exists for any setup. Any revival must be a NEW pre-registered
   task on a fresh holdout window, not a re-read of 2025–2026.
10. `signals.py` is research code shipped for reuse/testing only; it is NOT wired into
    `experiments/strat_025/engine/**` (Part 1's registry keeps all setups OFF).

## 10. Manager decision

**REJECT all nine swing setups.** No paper sleeve, no production wiring, no threshold
changes, no holdout use. The only edge-positive variant (g: RSI(2) entry, no SMA5 exit)
is t=0.80 — far below the t ≥ 2.0 bar and inside baseline sampling noise. Recommendation:
close STRAT-025 Part 2 as a negative result, keep the pre-registration + harness for
future reuse, and (only with Josh's approval) consider a separate pre-registered task
with a genuinely different exit/risk structure (e.g. position sizing / portfolio-level
risk overlay rather than per-name entry rules) on a fresh holdout window.

## 11. Next recommended action

1. Josh reviews this report and the PR/branch diff (nothing is committed or pushed by
   this subtask per instructions).
2. If the screen is accepted as final: merge/archive decisions belong to Josh — no merge
   or push was performed here.
3. Optional follow-up (needs explicit approval + new prereg): portfolio-risk-overlay
   study on a fresh holdout; do not re-use 2025–2026 for these nine setups.

Recommended paste-able prompt for the manager:
"STRAT-025 Part 2 is complete (REJECT, no commit). Review
experiments/strat_025/setup_research/RESULTS.md and
reports/2026-10-31_023037_STRAT-025-part2-setup-research.md; confirm that zero
holdout use and the empty PASS registry are acceptable, then decide merge/archive."
