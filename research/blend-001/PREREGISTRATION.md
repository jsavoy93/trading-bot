# BLEND-001 — Blending MOM 12-1 with swing strategies (PREREGISTRATION)

Status: **FROZEN before any DEV result is computed.** Agent-drafted for Josh's
review; this file is committed on `agent/research-blend-001` *before* run_blend.py
computes any swing series or any blend result. Item numbers refer to the BLEND-001
step-1 brief.

RESEARCH ONLY, paper data, DEV window only.

## 1. Question

Does combining MOM 12-1 long-short with an uncorrelated short-horizon swing
strategy improve risk-adjusted DEV performance over MOM alone?

Base: the frozen MOM 12-1 engine `research/volsize-001/engine_fixed.py`
(imported, never modified). Swing series are pre-built monthly net return
series, so any variant here is a portfolio-level question on top of two fixed
components.

## 2. Data

### 2.1 MOM leg
- Panel `/tmp/panel_dev.parquet`, read with a pyarrow row filter
  `date >= 2018-01-01` and `date <= 2024-12-31`, and asserted after read
  (`max(date) <= 2024-12-31`, min `>= 2018-01-01`). Holdout rows are never read.
- DEV months: entry open >= 2021-01-01 and exit open <= 2024-12-31, i.e.
  **2021-01 .. 2024-11 (47 months)**. (2024-12 is dropped because its exit open
  is 2025-01-02 — the first non-DEV date.) Cost/borrow/screen exactly as the engine.

### 2.2 Swing leg(s)
- Strategy C4 from `experiments/strat_022/PREREGISTRATION.md` (RSI(2)<10 with
  `close > SMA200`, next-open entry, exits at close on `close > SMA5` /
  `close <= entry_open - 3*ATR14` / 10 trading days, whichever first; 10 bps per
  side). Rule re-implemented verbatim in `research/blend-001/run_blend.py`
  (`swing_rules.py` helpers) from the frozen STRAT-022 spec.
- Universe: the STRAT-006R curated list, 379 symbols (the ones that downloaded in
  STRAT-022), same as the STRAT-022 backtest.
- Source (frozen): the surviving byte copy of the STRAT-022 data at
  `/tmp/rebal_red/experiments/strat_022/data/` (format `t,o,h,l,c,v`), which is
  identical to what STRAT-022/PR 116 ran on (see 2.3). `.gitkeep` behaviour: if a
  symbol CSV is missing there, the run FAILS (`--refallback` opt-in re-downloads
  only those symbols with STRAT-022's exact request parameters from the Alpaca
  **paper** data API, free, paper credentials read read-only from
  `/root/.openclaw/workspace/trading-bot/.env`). No bulk data is copied into the
  repo or the worktree; no network call happens in a normal run.
- Only bars with date <= 2024-12-31 are ever read (the CSVs contain 2025-2026 rows;
  they are dropped at load and an assertion re-checks `max(date) <= 2024-12-31`),
  so holdout bars never enter the computation. No new data is stored on the server
  (the downloader's output, if used, goes to a `/tmp` cache dir and is not committed).
- Known caveats carried over unchanged from STRAT-022: survivorship (current-ticker
  universe), `adjustment=raw` prices (pre-declared in STRAT-022 §12), close-checked
  stops, one open position per symbol.
- Costs: 10 bps per side (STRAT-022's stricter level).

### 2.3 Rebuildability verdict (item 1; decided from code + spec, no results)
- **C4: REBUILDABLE.** The STRAT-022 harness (`run_backtest.py`) is a
  self-contained, deterministic rule engine over per-symbol OHLCV CSVs pulled from
  the free Alpaca paper data API; no DB, no broker, no state. The original CSVs are
  no longer in the strat-swing checkout (not tracked by git), but a surviving byte
  copy of the whole strat_022 directory from the same run exists on the box
  (`/tmp/rebal_red/experiments/strat_022/`, 379 symbols, same `fetched_at` and same
  `_download_manifest.json` as the checkout, identical `download_data.py` /
  `run_backtest.py` md5s, and an `_results_dev.json` numerically identical to the
  committed PR-116 result — verified by comparison before this prereg was committed).
  That copy is read-only input, never modified.
- **RSI(2) swing (STRAT-023 shadow / paper runner): NOT REBUILDABLE as a DEV
  series.** It is a live/shadow paper runner with no 2021-2024 trade history: its
  DB (`experiments/strat_023/live_paper.db`) only holds 2026 shadow/today-forward
  paper fills, and its logs/state contain unfilled dry-run entries the repo MAP
  explicitly says not to trust. No frozen DEV backtest of that runner exists. It
  is therefore **excluded** and the study tests C4 only. Nothing under
  `/root/strat-swing` is modified or committed.

## 3. Baseline (self-test only; the one allowed early number)
MOM 12-1 monthly net over 2021-01..2024-11. Published DEV reference
(VOLSIZE-001 / LONGSHORT-001): mean **1.7082%/mo**, t(raw) 1.673, t(excess vs the
engine's frozen random-decile benchmark) **2.294**, max drawdown **-27.7%**,
Sharpe **0.846**, worst 3 months **-17.7% / -15.7% / -12.3%** (2023-01, 2021-02,
2022-06). Self-test tolerance: mean within 1e-9 absolute; maxDD within 1e-4;
Sharpe within 1e-3; worst-3 within 1e-4. Outside tolerance = blocker.
The published reference itself was recomputed once mechanically (a single vector of
47 numbers) at prereg-writing time; no swing or blend number was computed.

## 4. Metric definitions (fixed now)

Monthly net series indexed on the MOM month grid. Let `m_t` be the MOM net and
`s_t` the C4 net for month t.

- `sigma_M` = sample stdev (ddof=1) of `m_t` over the full DEV window — a single
  **fixed** number; it is the same for every comparison, so the metric below is
  a fixed linear map applied to blends.
- **Blend** `b_t = w * m_t + (1 - w) * s_t` (fixed capacity split, see §5).
- **Risk-adjusted improvement (the paired metric):**
  `d_t = (b_t / sigma_M) - m_t`  — the blend's monthly net expressed on MOM's
  volatility scale, minus MOM's own monthly net, paired by month.
  Reported: mean(d_t) and paired t = mean/ (sd(d)/sqrt(n)) with the engine's
  `t_stat`. Units: monthly simple return.
- **Bars** use n = the number of paired months (must be >= 24).
- Sharpe for reporting (not a bar input) = mean/sd * sqrt(12).
- Percentile vs random (§6.4) = `engine.percentile_vs_random(mean(b), random draw
  means)` over the same months.

## 5. Variants (k = 2, LOCKED, chosen without looking at DEV)

Two fixed-weight capacity splits of MOM and C4 (any cash left idle earns 0%):
- **B1 — 50/50**: `w_MOM = 0.50`, `w_C4 = 0.50`.
- **B2 — MOM-weighted 70/30**: `w_MOM = 0.70`, `w_C4 = 0.30`.


## 6. Pass bars (verbatim; a variant PASSES only if ALL of (a)-(e) hold)

(a) **Sample**: paired months n >= 24.
(b) **Profitability**: mean monthly net of the blend > 0.
(c) **Significance vs zero**: t(blend net) >= 2.24.
(d) **Risk-adjusted improvement over MOM alone**: mean(d_t) > 0 AND paired
    t(d_t) >= 2.24 on `d_t = b_t/sigma_M - m_t` (§4).
(e) **Beats random**: percentile of mean(b) vs random-blend draws >= 98.75
    (Bonferroni, k=2: one-sided alpha 0.05/2 = 0.025 -> percentile bar 97.5%.
  The brief fixes the working bar at **98.75**; the stricter of the two is the
  brief's 98.75, which is what the code evaluates. k=3 would give 99.17; it is
  not used because the study registers only k=2 variants.)

Supplementary (reported, NOT a pass condition): blend Sharpe > MOM Sharpe
(0.846); blend max drawdown and worst 3 months reported without a numeric bar
(no DEV-informed threshold is defensible); correlation of `s_t` with `m_t`.

## 7. Random benchmark (bar e)

1000 draws per variant. Each draw replaces the C4 series with a **random-entry
swing strategy** built in the same pipeline:
- Per symbol, the number of entries equals that symbol's actual C4 entry count in
  the DEV window; the entry days are drawn uniformly without replacement from the
  symbol's eligible days (liquidity-passing, SMA200/ATR warm) **excluding** all
  C4 signal days.
- Identical hold/exit engine as C4: same SMA5 exit, same ATR stop multiplier
  (3.0) computed from the drawn entry, same 10-day time stop, same anti-overlap,
  same 10 bps per side.
- The drawn trades are aggregated to months with the same §5 formula.
- Seeds: `numpy.random.default_rng(20261201 + 10007*d)`, d = 0..999 (frozen; one
  stream per draw, consumed symbol-by-symbol in sorted symbol order).
- Bar: `percentile_vs_random(mean(b), draw_means) >= 98.75` where each draw's
  blend uses the same `w` as the tested variant.
- Random draws scale with the window, so a **smoke** run (few months) uses the
  same code path with a small draw count; its numbers are never reported as
  results.

## 8. Self-test requirements (must pass before a full run)
1. **No lookahead**: every C4 feature used on signal day t uses bars with index
   <= t (asserted by construction: rolling windows are `shift`-free only backward,
   entry at t+1 open); the entry open is read at t+1 and every exit uses a close
   at index >= t+1; every trade has `exit_date > entry_date` and both inside the
   loaded window. Month assignment uses the exit date; the MOM month grid uses
   entry/exit opens only. Assertions raise, not warn.
2. **MOM baseline self-test**: reproduces §3 within tolerance.
3. **Alignment**: C4 monthly series and MOM net share exactly the same 47 month
   keys, in order.

## 9. Reporting and stop rules
- Full run reports: for MOM, C4, B1, B2 — mean/mo, annualized, t, Sharpe, max DD,
  worst 3 months, and each bar of §6 with PASS/FAIL; correlation of each swing
  series with MOM; paired metric mean/t.
- No tuning after any result; a FAIL is the result.
- If C4 cannot be rebuilt at step 1, only the rebuildable leg(s) are tested and
  variants are reduced accordingly (here: both variants use C4, so a C4 failure
  would stop the study with a blocker report).
- Holdout 2025-01..2026-08 is BURNED: nothing after 2024-12-31 is loaded anywhere.
