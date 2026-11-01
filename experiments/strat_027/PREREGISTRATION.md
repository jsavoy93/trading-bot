# STRAT-027 — Pre-Registered Research Plan: Systematic Portfolio Construction

> **RESEARCH ONLY.** No production code, config, DB, broker, or order changes.
> No BUY-threshold changes. No changes to the main bot, the C4 sleeve, or the RSI(2) shadow.
> This document is frozen and committed BEFORE any backtest is run. The commit that
> first adds this file is the pre-registration anchor.

- **Backlog item:** STRAT-027 (Josh approved 2026-10-31 21:48 CDT)
- **Branch:** `research/strat-027-portfolio` (worktree `/root/.openclaw/worktrees/STRAT-027`)
- **Approval gate:** Josh approves every merge. Nothing is merged by this agent.

---

## 1. Objective

Test whether a **systematic, monthly-rebalanced, multi-stock portfolio** built from a
single measurable factor beats simple benchmarks **after risk and costs** — instead of
hunting for individual BUY signals.

Specifically: does a factor-ranked, diversified, capped portfolio beat

1. **buy-and-hold SPY**, and
2. **1,000 random portfolios** of the same size and rebalancing schedule,

on total return, volatility, max drawdown, Sharpe, and turnover?

A REJECT (no factor beats the benchmarks) is a valid and expected outcome. It will be
reported as such. No post-hoc parameter changes, no re-runs, no "second attempts".

### Explicitly NOT in scope
- No new BUY/SELL signals, no threshold changes, no production strategy edits.
- No live trading, no broker/order calls (paper data API only).
- No repeat of STRAT-020/021/022/023/024/025/026 research (see §11).

---

## 2. Data audit (done BEFORE this plan was frozen; result in `DATA_AUDIT.md`)

**Prices — available and used.**
Local read-only research dataset `research_data/alpaca_daily_2016_2026/` (DATA-001),
built from the Alpaca **paper** data API with `adjustment="all"` (split + dividend
adjusted), covering 2016-01-04 → 2026-09-29 for 13,489 US equity symbols
(13,126 active + 590 Alpaca-inactive/delisted names, which materially reduces
survivorship bias relative to a "currently known tickers" list).

Price panel actually used: **2019-01-02 → 2026-09-29**, 6,258 symbols with bars,
9,157,044 daily rows. Only **common stock / ADR** symbols are investable
(ETFs/funds, preferreds, warrants, rights, units, notes, SPACs are excluded —
they are not operating companies and their factor values are not comparable).
SPY is kept separately as the benchmark.

**Fundamentals — conditional.**
The audit result decides whether quality/value factors are tested at all:
- If point-in-time fundamentals (facts with a public release/filing date attached)
  are usable → test QUALITY and VALUE as additional individual factors.
- If not → test only the two price-based factors and **say so plainly** in the report.

This conditional was frozen BEFORE any backtest was run.

**Known data limits (carried into every conclusion):**
- The dataset has no listing/delisting timestamps → the universe is "broad historical"
  (Class B), not point-in-time index membership. Survivorship bias remains, but is
  *reduced* (inactive names included) rather than absent.
- Bars end **2026-09-29**; the last complete monthly rebalance open is 2026-09-01.
  The holdout therefore ends 2026-09-01, not "today" (2026-11-01). This is stated
  in the report rather than papered over.
- No intraday data, no order-book depth: costs are modelled as a flat per-side rate.

---

## 3. Universe and eligibility (point-in-time, no look-ahead)

At each monthly rebalance date `d`, a symbol is **eligible** only if ALL of:

| Screen | Value | Data used |
|---|---|---|
| Type | common stock or ADR | static asset metadata |
| Liquidity | trailing 20-session mean volume ≥ **1,000,000 shares** | bars strictly before `d` |
| Price | last close ≥ **$5** | bars strictly before `d` |
| History | ≥ **253** prior trading sessions of data | bars strictly before `d` |
| Tradable | a bar exists on `d` itself | bar on `d` |

Every screen uses bars **strictly before the rebalance date**. The liquidity/price
screens mirror the STRAT-022 precedent so the study is comparable to prior work.

---

## 4. Factors (each tested INDIVIDUALLY first)

Ranking inputs use bars **strictly before** the rebalance date. A factor value that is
not finite (missing history, zero price) makes the symbol ineligible.

| ID | Factor | Definition | Direction |
|---|---|---|---|
| `MOM12_1` | 12-1 month momentum | `P(t-1m) / P(t-12m) − 1` on adjusted closes, where `P(t-1m)` is the last close on or before one month before `d` | higher is better |
| `LVOL` | Low volatility | annualized realized volatility of the trailing **252** daily log returns ending strictly before `d` (`std × √252`) | **lower** is better |
| `QUALITY` | Quality | trailing-12-month return on equity = TTM `NetIncomeLoss` ÷ `StockholdersEquity`, using only filings **public on or before `d`** | higher is better |
| `VALUE` | Value | earnings yield = TTM `NetIncomeLoss` ÷ market value of equity at `d` | higher is better |

`QUALITY` and `VALUE` are tested **only if** the data audit clears them (§2).
`MOM12_1` deliberately skips the most recent month (standard 12-1 definition) to avoid
the short-term reversal effect.

**Combination rule (frozen).** Individual factors are evaluated first. A combination is
built **only if ≥2 individual factors pass the dev bar (§8)**. The combined score is the
mean of the cross-sectional z-scores of the passing factors (z-scores computed across
the eligible universe at each rebalance, winsorised at ±3), and names are ranked by the
combined score. The combination must independently pass the dev bar to be eligible for
the holdout.

---

## 5. Portfolio construction

- **Rebalance:** monthly, at the **open of the first trading day of each month**.
  Rankings and screens use data strictly before that open.
- **Holding period:** from the rebalance open to the next rebalance open.
- **Size:** **N = 30** names.
- **Weights:** **equal weight** (1/30 each), with a **single-name cap of 8%** enforced
  by proportional redistribution (the cap is a safety rule; under equal weighting at
  N = 30 it is not binding — it *is* binding in the declared N = 10 sensitivity check,
  which is reported to demonstrate the machinery works).
- **No cash** unless the cap redistribution cannot fill the portfolio.
- **Fractional shares** assumed (no lot-size or share-rounding effects). Stated plainly.
- **Delisting rule:** if a held name has no bar at the next rebalance open, it is
  liquidated at its **last available close** (same rule as the STRAT-022 engine).
  Bankruptcy recovery beyond the last close is not modelled.
- **Turnover:** at each rebalance, one-way turnover = `Σ |w_target − w_drifted|` after
  the previous holdings have drifted through their realised returns. Annualised
  turnover = mean per-period turnover × periods per year.

---

## 6. Costs

- **Primary:** **10 bps per side** of traded notional (commission + spread for liquid
  US equities). Cost at a rebalance = `turnover × 10 bps`.
- **Sensitivity:** 5 bps and 20 bps (reported, never selected on).
- Costs are charged on **both** the strategy and the random portfolios, using each
  portfolio's own honest turnover.

---

## 7. Periods

| Period | Rebalance dates (opens) | Use |
|---|---|---|
| **Development** | 2021-01-04 → 2024-12-02 (periods ending at the 2025-01-02 open) | all tuning, all comparisons, factor selection |
| **Holdout** | 2025-01-02 → 2026-08-03 (periods ending at the 2026-09-01 open) | ONE-SHOT |

The holdout is executed by a **single scripted run** after the dev analysis is complete
and committed. After the holdout is observed:
- no new factor may be introduced,
- no parameter may be changed,
- no re-run is permitted.

`QUALITY`/`VALUE` (if applicable) are **pre-declared in this document**, so reporting
every pre-declared factor on the holdout is a single pre-registered run, not holdout
shopping. The **headline claim** is still driven by the dev-selected factor; holdout
numbers for non-selected factors are context only. The multiple-comparison caveat is
stated in the report.

---

## 8. Benchmarks, metrics, and the frozen pass bar

### Benchmarks
1. **Buy-and-hold SPY** — SPY adjusted close, bought at the first rebalance open, held
   to the last rebalance open, no trading costs (a single round trip is negligible).
2. **1,000 random portfolios** — at each rebalance, draw **N = 30** names uniformly at
   random (without replacement) from the *same eligible universe at that date*,
   equal weight, same rebalance dates, same cost model, seed **20261101**.
   Random portfolios are resampled every month, so their honest turnover is much
   higher than a stable portfolio's. To keep the comparison fair, **both gross and net**
   random-portfolio statistics are reported, and the pass bar below uses the
   **gross-vs-gross** alpha test (costs are then shown to be non-determinative rather
   than assumed away).

### Metrics (reported for strategy, SPY, and the random cloud)
Annualised return, annualised volatility, Sharpe (risk-free = 0, stated), max drawdown,
mean/annualised one-way turnover, hit rate of positive periods, and
**t-statistics versus the random portfolio cloud**:
- `z_unpaired_mean` — strategy mean period return vs the distribution of the 1,000
  random portfolios' mean period returns (gross and net).
- `t_paired_periods` — paired t-test of the strategy's period returns against the
  random portfolios' mean period return path.
- percentile rank of the strategy's mean among the 1,000 random means.
- paired t-test of strategy vs SPY period returns.

### Frozen dev PASS bar (a factor must satisfy ALL of)
1. **Beats SPY:** net annualised return > SPY net annualised return **and** net Sharpe > SPY net Sharpe.
2. **Beats random on a like-for-like basis:** gross mean period return is **≥ 2.0 standard deviations** above the mean of the 1,000 random gross means (`z_unpaired_mean ≥ 2.0`).
3. **Survives costs:** net annualised return exceeds the **95th percentile** of the 1,000 random net annualised returns.

### Frozen holdout bar (applied to the dev-selected factor only)
1. net annualised return > SPY net annualised return on the holdout, **and**
2. `z_unpaired_mean` (gross) ≥ 2.0 versus the random cloud on the holdout, **and**
3. net annualised return above the 95th percentile of random net annualised returns.

### Declared sensitivity (reported, never used for selection)
- Portfolio size N ∈ {10, 30, 50}
- Cost per side ∈ {5, 10, 20} bps

---

## 9. Statistical honesty rules

- The random-portfolio null is the primary test of "is this factor doing anything?",
  because it automatically controls for market beta, universe composition, and the
  rebalancing/cost schedule.
- If the strategy's gross alpha is negative but net alpha is positive, that is reported
  as a **cost artifact**, not a win.
- Results are reported whether they pass or fail.

---

## 10. Deliverables

- `experiments/strat_027/` — harness code, `PREREGISTRATION.md`, `DATA_AUDIT.md`,
  `RESULTS.md` (numbers), `REPORT.md` (plain language), results JSON.
- `reports/<UTC-ts>_STRAT-027.md` + `REPORT.md` + `ITERATION_PROGRESS_LOG.md` entry.
- Branch `research/strat-027-portfolio`, **PR opened, NOT merged** (Josh approves merges).

---

## 11. Prior-work dedupe (read before designing)

| Prior item | What it did | Why STRAT-027 is not a repeat |
|---|---|---|
| STRAT-020 | factor-cohort coverage-gap quantification | data-coverage work, not portfolio construction |
| STRAT-021 | monthly sub-sleeve evaluation of MOM / LVOL / GP (gross-profitability) | **closest prior art**: it evaluated standalone long-only sleeves, not a cross-sectional, cap-constrained, cost-aware portfolio vs a random-portfolio null and vs SPY. Exact overlap and differences are stated in `DATA_AUDIT.md`/report. |
| STRAT-022/023 | short-term dip-buy event study, single-name entries | different cadence and object (event study, not portfolio) |
| STRAT-024 | live paper-order runner for the C4 sleeve | implementation, not research |
| STRAT-025 | nine swing setups, event study | different object |
| STRAT-026 | RSI(2) dip-buy LOG-ONLY shadow runner | implementation/shadow, not a factor portfolio study |

Any overlap that materialises is stated plainly in the report. If STRAT-027's portfolio
result is a re-run of STRAT-021's sleeve in a different wrapper, that is said out loud.

---

## 12. Pre-declared caveats

1. Survivorship bias is reduced but not eliminated (Class B universe; no listing/delisting dates).
2. No point-in-time index membership → the universe is "all liquid US common stocks/ADRs", not "S&P 500 members".
3. Broad universe includes small/mid caps; only volume and price screens separate them.
4. Adjusted prices include dividends; no separate dividend tax modelling.
5. Flat per-side cost; no market-impact model beyond the flat rate.
6. Fractional shares, no lot-size rounding, no borrow (long-only).
7. Monthly cadence only; no intra-month risk control (no stops, no drawdown triggers).
8. The holdout is ~20 monthly periods → low power for a small edge.
9. One market regime in the holdout window (2025-01 → 2026-09).
10. Delisted names are valued at their last available close; recovery beyond that is not modelled.
