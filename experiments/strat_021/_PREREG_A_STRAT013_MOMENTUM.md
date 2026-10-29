# STRAT-021 Pre-Registration A — STRAT-013-R2 Momentum Sub-Sleeve (STANDALONE)

> **Status: FROZEN before any portfolio-return computation for this study.**
> Frozen: 2026-10-29 01:44 UTC (see .sha256 sidecar). Owner directive
> 2026-10-29 01:39 UTC (PAPER ONLY; research/docs only). This document
> closes the documented STRAT-020 Phase 1 gap ("STRAT-013 standalone
> selection rule NOT documented") with a NEW explicit rule. It does not
> claim to reconstruct the original 2025-era STRAT-013 study selections.

## 1. Purpose

Define the frozen standalone sleeve methodology for the 12-1 momentum
factor as a candidate long-term sub-sleeve, evaluated under the STRAT-021
portfolio-level protocol (Pre-Registration C).

## 2. Factor

- **Momentum 12-1 (Jegadeesh-Titman 1993):**
  `mom = close[t−1] / close[t−12] − 1`, computed from
  `month_last_adjusted_close` in `monthly_research.csv.gz` (SHA 936e558e…).
  At formation year Y: t−1 = June(Y), t−12 = June(Y−1).
- **Direction:** HIGHER favorable.
- Pre-computed per-ticker values
  `experiments/strat_019/_strat_013_momentum_ticker_level.csv.gz`
  (`formation_year, ticker, momentum_12_1`) are the authoritative input;
  no recomputation of factor values.

## 3. Universe eligibility (at each formation, June Y)

Ticker must have:
1. A valid `momentum_12_1` for formation_year Y.
2. ≥ 12 months of price history in `monthly_research` through June(Y)
   (implied by factor validity; no additional seasoning filter).
3. Pass a **relative liquidity floor**: not in the bottom decile by
   `month_median_daily_dollar_volume` for June(Y) within the
   momentum-eligible ticker set. (Relative rank only — absolute dollar
   volume is NOT valid per the STRAT-020 Phase 4 corrected audit.)

## 4. Breakpoints, selection, weighting

- Quintile breakpoints computed over ALL eligible tickers (full universe;
  no NYSE-only restriction — exchange data is not available for the
  momentum universe, unlike STRAT-017's Compustat `primary_exchange`).
  **This deviation from STRAT-017's NYSE breakpoints is stated
  prominently.**
- **Selection: Q5 (highest momentum), equal weight.**
- No market-cap weighting; do NOT synthesize market cap.

## 5. Cadence and holding

- **Annual formation at late June; holding year July(Y) → June(Y+1).**
- No monthly re-ranking, no quarterly refresh.
- Formation years: 2017–2025 (complete holding years through 2026-06);
  a 2026 partial holding period, if reported, is labeled PARTIAL /
  INCOMPLETE and excluded from primary aggregates.

## 6. Prohibitions

No parameter sweeps; no re-optimization; no lookahead (factor uses data
through June(Y) only); no mixing with STRAT-015/017 (STRAT-019 composite
REJECT stands); no new data acquisition.
