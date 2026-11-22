# SECFILE-001 — pre-registration (written and committed BEFORE any result is computed)

Task (Josh-approved 2026-11-21): test whether skipping long-leg names whose company has
recently filed a dilutive SEC registration statement (V1) or registration + prospectus
under an offering (V2) improves MOM 12-1 **on DEV only**. RESEARCH ONLY, paper only.
No orders, no main-bot change, no PR, no merge, no brokerage credentials.

## 0. Scope, window, data firewall
- DEV window: months 2021-01..2024-12 with exit_date <= 2024-12-31 (panel-month set used by
  HOLDOUT-MOM-002 `dev`, 47 months).
- **NO data dated after 2024-12-31 is read, fetched or used for anything.** The
  2025-01..2026-08 holdout is BURNED. (WOLF's June-2026 S-1 is motivating context only.)
- Base: frozen MOM 12-1 rules/costs/borrow/seed/panel exactly as EVENTNEWS-001 /
  HOLDOUT-MOM-002 (panel_holdout.parquet sha256 4387206b..c071, asserted with sha256sum
  before use; panel never modified).
- Unfiltered MOM DEV reference: net 1.71%/mo, t 2.29, maxDD -27.74%.

## 1. Filter mechanics (frozen)
At each monthly rebalance (decision date = last trading day of M-1): select legs with the
frozen MOM `select_legs` FIRST, then apply the filter to the chosen names (no deeper
re-ranking). **Only the LONG leg is filtered**; the SHORT leg is unchanged. Removed weight
is redistributed equally across the remaining long names (re-simulated at 1/n_remaining).
If the long leg would be empty the month is left unfiltered (counted and reported).

## 2. Exactly k=2 variants
- **V1 REGISTRATION** — remove a long name if its company filed, with a filing date in the
  30 calendar days BEFORE the rebalance date (decision_date - 30 days <= date <
  decision_date), an **S-1, S-3, F-1 or F-3** (including S-3ASR / F-3ASR). Exclude /A
  amendments (S-1/A etc.) and POS forms (POS AM, 424B*, etc.).
- **V2 REGISTRATION+OFFERING** — V1 plus any **424B1, 424B2, 424B3, 424B4, 424B5, 424B7**
  prospectus filed in the same 30-day window.
- No lookahead: only filings dated strictly before the decision date are used; the filing
  date compared is the EDGAR `Date Filed` of the form row.

## 3. Data (free SEC EDGAR only)
- Quarterly full-index files `https://www.sec.gov/Archives/edgar/full-index/YYYY/QTRn/form.idx`
  for **2020Q4..2024Q4 (17 files)**, cached raw to /root/research/secfile-001/data/.
  (2020Q4 is needed only so that the 2021-01 window can be evaluated; it is before, not
  after, the DEV window.)
- Ticker->CIK: `https://www.sec.gov/files/company_tickers.json` plus any free historical
  ticker/CIK mapping published by SEC (e.g. company_tickers_exchange.json); cache raw.
- Coverage: report % of DEV long-leg name-months whose ticker maps to a CIK. **Unmapped
  names (often delisted) STAY in the long leg (unfiltered).**
- Fair access: User-Agent `SmartBot research` (no personal email), <= 5 requests/sec,
  every raw file cached before parsing.

## 4. Random-draw seed scheme (frozen)
Reuse the EVENTNEWS-001 / HOLDOUT-MOM-002 scheme verbatim: for bar (a) the strict per-month
random MOM draws with SEED 20261112, 1000 draws (identical benchmark for filtered and
unfiltered). For bar (c): per draw d (0..999) and month r, seed = SEED2 + 1000*d + r with
SEED2 = 20261121, `np.random.default_rng(seed)`; each month remove the SAME NUMBER of
long-leg names as the variant removed that month, chosen at random, re-weighted equally,
simulated with the engine's cost/borrow/delisting rules. Percentile = 100 * mean(draw_mean <
filtered_mean) (strict, same as `percentile_vs_random`).

## 5. Pass bars per variant (ALL must hold), k=2
(a) filtered strategy passes DEV bars: >=24 months, mean net > 0, excess > 0,
    percentile >= 97.5, t on excess >= 2.24 (identical to `dev_bars`);
(b) improvement = filtered minus unfiltered MOM monthly net: mean > 0 AND t >= 2.24
    (one-sample t, n-1 df);
(c) beats random: percentile >= 98.75 (97.5 Bonferroni k=2) vs 1000 same-count random
    removals (seed scheme above).
t-stat / percentile / excess definitions are the frozen MOM 12-1 / EVENTNEWS-001 evaluation
code (`engine_fixed` + `run_study.window_metrics` / `dev_bars`); not redefined.

**Descriptive only (not a bar):** average next-month return of removed long names vs kept
long names.

## 6. Discipline
- No change to forms, window, rules or bars after results are seen. Plumbing-only fixes are
  allowed and must be stated in the report.
- A pass is a DEV candidate only, never a strategy to trade; confirmation needs a separate
  forward paper test Josh approves (2025+ is burned).
- Artifacts: /root/research/secfile-001/REPORT.md (<40 lines) and the external audit archive;
  repo's rolling REPORT.md / reports/ / ITERATION_PROGRESS_LOG.md are NOT touched.
