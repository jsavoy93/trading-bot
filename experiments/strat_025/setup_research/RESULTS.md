# STRAT-025 Part 2 — Swing Setup Screen: Results

> RESEARCH ONLY — paper data, no production code/config/DB/broker changes, no BUY-threshold
> changes, no merges/pushes, no commits from this subtask.
> Pre-registration: `PREREGISTRATION.md` (frozen BEFORE any backtest),
> sha256 `cf55863311f4b06e9d4aaadabf16c1a68f736c4f1d437662919d63b15f5aaa2d`
> (see `PREREGISTRATION.sha256`).

## 1. Headline

**No setup passed the frozen development bar. The holdout was therefore NEVER TOUCHED.**
Zero of the nine pre-registered swing setups produced positive net expectancy at 10 bps
per side that beats the matched 1000-draw random-entry baseline by the pre-registered
10 bps margin with a t-stat ≥ 2.0 (the STRAT-022 §10 evidence bar, cited verbatim in
PREREGISTRATION §10).

Consequences:
- **Holdout runs: 0.** PREREGISTRATION §7/§10 lock the holdout to dev-passing setups
  only; `run_setup.py holdout` was executed and correctly skipped all ten variants
  ("skip holdout (did not pass dev)" — `_run_holdout.log`).
- **PASS list is EMPTY**: `signals.py::PASSING_SETUPS == []` (asserted by
  `test_signals.py::test_passing_registry_is_empty_and_documented`).
- No production or paper-trading wiring is justified by this evidence.

## 2. Method (as executed, unchanged from the frozen pre-registration)

- **Data**: daily OHLCV, 2020-06-01 → 2026-10-28. Alpaca **paper** API
  (`adjustment=raw`) for listed names; the 307 STRAT-023 delisted additions sourced
  read-only from the DATA-001 parquet shards (`trading-bot/experiments/data_001/`,
  untracked). 672 usable symbols, 475 with ≥1 eligible symbol-day.
- **Universe**: STRAT-022/C4 universe (STRAT-006R curated 379 + 276 delisted with ≥220
  in-window bars) ∪ main-bot quality watchlist (101 tickers incl. ETFs) = 672 files.
- **Eligibility (point-in-time)**: trailing 20-day mean volume ≥ 1,000,000 AND close ≥ $5
  on signal day, plus indicator warm-up. Identical for strategy and baseline.
- **Execution**: signal on close of day t → entry at next trading day's open; one open
  position per symbol; exits on close only (ATR stop + time stop, plus the pre-registered
  gap-fill profit exit for setup d2).
- **Costs**: 10 bps per side (net = gross − 20 bps round-trip).
- **Baseline**: 1000 seeded random-entry draws per setup-config (seed 20261031),
  matched per-symbol entry counts, same exit engine, same costs; comparison metric is
  the mean of per-draw mean net returns.
- **Windows**: dev 2021-01-01 → 2024-12-31 (all tuning/selection); holdout
  2025-01-01 → 2026-10-28 (locked, never touched).

## 3. Per-setup results (dev = the only window run)

Selection rule (PREREG §9): among configs with ≥100 dev trades, highest per-trade
Sharpe at 10 bps. Pass bar (PREREG §10): mean net > 0 AND t ≥ 2.0 AND
mean − baseline ≥ +10 bps.

| # | Setup | Selected cfg (ATR×time) | Dev trades | Dev avg net | Dev t | Baseline avg net (1000 draws) | Edge vs baseline | Dev verdict |
|---|---|---|---|---|---|---|---|---|
| a | EMA20 pullback in uptrend | 3.0× / 10d | 13,657 | −0.219% | −2.59 | +0.111% | −33.0 bps | **FAIL** |
| b | 20d-high breakout, vol > 1.5× | 3.0× / 10d | 6,821 | −1.030% | −4.47 | −0.027% | −100.4 bps | **FAIL** |
| c | Minervini VCP | 3.0× / 10d | 368 | −0.270% | −0.57 | +0.232% | −50.1 bps | **FAIL** |
| d1 | Gap-and-go | 3.0× / 10d | 8,182 | −1.781% | −7.10 | −0.192% | −158.9 bps | **FAIL** |
| d2 | Gap-fill fade | 2.5× / 10d | 12,851 | −0.245% | −0.69 | −0.238% | −0.7 bps | **FAIL** |
| e | RS leaders vs SPY, 3m (63d) | 3.0× / 20d | 2,881 | −0.413% | −1.64 | +0.631% | −104.4 bps | **FAIL** |
| e | RS leaders vs SPY, 6m (126d) | 3.0× / 20d | 2,597 | +0.070% | +0.25 | +0.760% | −69.0 bps | **FAIL** |
| f1 | Inside day | 3.0× / 10d | 15,444 | −0.249% | −3.04 | +0.111% | −36.0 bps | **FAIL** |
| f2 | NR7 | 2.5× / 10d | 17,445 | −0.294% | −4.16 | +0.115% | −40.9 bps | **FAIL** |
| g | RSI(2) uptrend, distinct exit | 3.0× / 5d | 12,249 | +0.041% | +0.80 | −0.066% | +10.8 bps | **FAIL** (edge ✓, t ✗) |

**Holdout columns are absent by design** — no setup qualified for a holdout run.
Machine-readable copies: `results/summary.csv`, `results/summary.json`,
per-setup `results/<setup>_dev.json`, selection `results/_dev_selection.json`.

### Notes per setup
- **b (breakout)** and **d1 (gap-and-go)** are the worst: entry at the next open after
  an extended move buys supply; both lose ~1.0–1.8% per trade net at 10 bps.
- **e (RS leaders)** has a *high baseline* (+0.63% / +0.76% per trade) because the
  baseline inherits the same ATR-3/20-day exit engine on a universe with a strong
  2021–2024 drift; the RS entry rule adds no edge over it (edge −104 / −69 bps).
- **g (RSI(2), distinct exit)** is the only setup that satisfies the +10 bps edge bar
  (+10.8 bps) but its t-stat is +0.80 (bar: ≥ 2.0) → FAIL. Its edge is also inside the
  baseline's own draw-to-draw noise band (p5 −13.3 bps / p95 +0.9 bps), so it is not
  treated as a near-miss that warrants re-tuning (re-tuning is forbidden by PREREG §12.4).

### RSI(2) variant vs C4 (documented difference)
- **Entry** identical to STRAT-022 C4: `close > SMA200` AND Wilder RSI(2) < 10 (+ the
  same liquidity/price filter).
- **Exit different from C4**: C4 exits on `close > SMA5` (profit exit), 3.0×ATR stop,
  or a 10-day time stop. Setup g **removes the SMA5 profit exit entirely** and uses only
  ATR stop + time stop (grid 2.5/3.0 × ATR, 5/10 days). This was pre-registered
  (PREREG §4g) and is the only axis changed.
- Result: removing the SMA5 exit turns C4's edge into a flat +4 bps/trade with t=0.80 —
  i.e. **the C4 profit exit is doing the work**, and the C4 numbers themselves are not
  re-derived here (STRAT-023/PR 116 remain authoritative for C4).

## 4. Supplementary (post-hoc, clearly labeled) universe robustness check

Part 1 of STRAT-025 (same branch, another agent) established that the running bot's
actual watchlist is the key set of `analysis_status.json` (200 symbols), 191 of which
were **outside** the frozen prereg §3 universe (mostly micro-caps; the prereg used the
`quality_symbols` list in `src/core/smart_bot.py`). This check is POST-HOC and cannot
promote a setup into a PASS.

- Downloaded all 191 extra names (189 with usable bars) → `data_watchlist_extra/`.
- Eligibility impact (dev window): only **49 of 189** names ever satisfy the prereg
  point-in-time filter (vol20 ≥ 1M AND close ≥ $5) → 13,479 eligible symbol-days vs
  333,154 in the prereg universe (≈4%).
- Signal contribution: e.g. RSI(2) setup +512 of 16,231 signal days (3.2%), EMA20
  pullback +1,095 of 31,088 (3.5%), VCP +8 of 269 (3.0%) — i.e. uniformly ~3–4%.
- **Sensitivity re-run** (`run_extra_sensitivity.py`, union universe of 861 symbols) on
  the three closest setups: see §5 — no verdict changed.

## 5. Supplementary sensitivity results (union universe, dev only)

| Setup | Selected cfg | Dev trades | Dev avg net | Dev t | Baseline avg net | Edge | Verdict (same as frozen?) |
|---|---|---|---|---|---|---|---|
| rsi2_uptrend_v2 | 3.0x / 5d | 15,486 | +0.015% | +0.32 | -0.072% | +8.7 bps | FAIL (same; edge now below +10 bps bar too) |
| gap_fill_fade | 2.5x / 10d | 15,581 | +0.922% | +1.26 | -0.250% | +117.2 bps | FAIL (same; t < 2.0) |
| rs_leaders_126 | 3.0x / 20d | 2,711 | +0.105% | +0.36 | +0.755% | -65.0 bps | FAIL (same) |

Interpretation: adding the 191 extra bot-watchlist micro-caps does **not** rescue any
setup. `gap_fill_fade` turns strongly positive only because illiquid small-caps supply
most of its new trades (+0.92%/trade, t=1.26 - below the t >= 2.0 bar, and a micro-cap /
liquidity concentration that the $5 + 1M-share filter only partly controls);
`rsi2_uptrend_v2` stays flat (t=0.32) and its edge shrinks below the +10 bps bar; the RS
setup is unchanged. **No verdict changed; no setup becomes a PASS; the holdout remains
untouched.** Raw output: `results/<setup>_dev_union_supplement.json`, `_run_supplement.log`.


## 6. Files produced

- `PREREGISTRATION.md`, `PREREGISTRATION.sha256` — frozen plan + integrity hash.
- `universe.py` — universe construction (C4 universe ∪ watchlist, delisted additions).
- `prepare_data.py` — Alpaca paper download + parquet extraction for delisted names.
- `prepare_watchlist_extra.py` — supplementary download of the 191 extra watchlist names.
- `engine.py` — indicators, honest-stop trade engine, anti-overlap, 1000-draw baseline,
  metrics (STRAT-022 conventions).
- `setups.py` — the nine research signal functions used for the backtests.
- `run_setup.py` — dev tuning + one-shot holdout runner.
- `run_extra_sensitivity.py`, `check_watchlist_extra.py` — supplementary checks.
- `make_summary.py` — builds `results/summary.csv` / `summary.json`.
- `signals.py` — standalone DataFrame→bool signal functions + `PASSING_SETUPS` registry.
- `test_signals.py` — 12 tests: parity with research implementations, no-lookahead
  truncation check, output-shape checks, PASS-registry state. **12 passed.**
- `results/` — per-setup JSON, selection JSON, summary CSV/JSON, logs.
- `data/`, `data_watchlist_extra/` — daily bars (gitignored; regenerate with the scripts).

## 7. Caveats and risks

1. **Survivorship**: only partially mitigated (276 of 307 STRAT-023 delisted additions
   had ≥220 in-window bars and were included; 31 were too short to warm up). The universe
   is still anchored on currently-known names → results are upward-biased; the observed
   negative edges are therefore conservative.
2. **Close-checked stops**: ATR stops trigger on close and fill at that close; gaps
   through the stop are priced at the worse close (conservative trigger timing, no
   intraday data).
3. **`adjustment=raw`**: unadjusted prices; split artifacts possible ($5 floor + close
   rules mitigate).
4. **Multiple comparisons**: 10 setup variants × up to 4 configs were screened on the
   same dev window. No Bonferroni/multiplicity correction was applied (pre-declared,
   consistent with STRAT-022); the untouched-holdout + ≥10 bps-edge guard is the
   protection, and the holdout was never used.
5. **Baseline asymmetry**: the random baseline can hold more concurrent positions than
   the (often sparser) strategy, so baseline means are not perfectly matched in risk
   terms — inherited from the STRAT-022 engine convention.
6. **RS decile methodology**: the top decile is computed cross-sectionally over the
   *eligible* subset each day; names without SPY-matched history are excluded. Decile
   membership is a contemporaneous rank (no lookahead), but the baseline for RS is very
   strong, which compresses the measured edge.
7. **VCP sample size**: only 368 dev trades (≥100 bar satisfied) — the exact daily-bar
   contraction definition is restrictive; a looser definition was NOT explored
   (pre-registration forbids post-hoc variants).
8. **No holdout evidence**: with zero dev passers there is no holdout result at all.
   Any future attempt to revive these setups must be a NEW pre-registered task on a
   fresh holdout, not a re-read of the 2025–2026 window.
9. **Scope**: `experiments/strat_025/engine/**` was not modified (owned by the Part 1
   agent); no `src/`, config, DB, secret, or OpenClaw-config files were touched; the
   trading-bot workspace was read-only.

## 8. Decision

**REJECT all nine swing setups for now.** No paper-trading sleeve, no production
wiring, no threshold changes. The only positive signal in the screen is the
RSI(2)-entry/no-SMA5-exit variant's +10.8 bps edge with t=0.80 — inside baseline noise
and far below the t ≥ 2.0 bar. Josh's approval is required for any follow-up
(e.g. a new pre-registered task that tests a *different* exit/risk structure on a
*different* holdout window).
