# STRAT-021 Pre-Registration B — STRAT-015-R2 Low Realized Volatility Sub-Sleeve (STANDALONE)

> **Status: FROZEN before any portfolio-return computation for this study.**
> Frozen: 2026-10-29 01:44 UTC (see .sha256 sidecar). Owner directive
> 2026-10-29 01:39 UTC. Closes the documented gap ("STRAT-015 standalone
> study rule NOT preserved") with a NEW explicit rule; does not claim to
> reconstruct the original study's selections.

## 1. Purpose

Frozen standalone sleeve methodology for the low-realized-volatility
factor as a candidate long-term sub-sleeve, evaluated under STRAT-021
Pre-Registration C.

## 2. Factor

- **12-month average realized volatility:** mean of
  `monthly_total_volatility` over July(Y−1) → June(Y), from
  `monthly_realized_volatility.csv.gz` (SHA 8b61e5d6…).
- **Direction:** LOWER favorable.
- Pre-computed authoritative input:
  `experiments/strat_019/_strat_015_volatility_ticker_level.csv.gz`
  (`formation_year, ticker, avg_vol_12mo, n_valid_months`).
- Validity: require `n_valid_months ≥ 9` of 12.

## 3. Universe eligibility (at each formation, June Y)

Ticker must have:
1. Valid `avg_vol_12mo` with `n_valid_months ≥ 9`.
2. Relative liquidity floor: not in the bottom decile by
   `month_median_daily_dollar_volume` for June(Y) within the
   volatility-eligible set (relative rank only; absolute dollar volume
   invalid per STRAT-020 Phase 4 corrected audit).

## 4. Breakpoints, selection, weighting

- Quintile breakpoints over ALL eligible tickers (no NYSE-only
  restriction; exchange data unavailable — deviation from STRAT-017
  stated prominently).
- **Selection: Q1 (LOWEST volatility), equal weight.**
- No synthesized market cap.

## 5. Cadence and holding

- Annual late-June formation; holding July(Y) → June(Y+1); no
  intra-year re-ranking.
- Formation years 2017–2025; 2026 partial (if reported) labeled
  PARTIAL / INCOMPLETE and excluded from primary aggregates.

## 6. Prohibitions

Same as Pre-Registration A §6.
