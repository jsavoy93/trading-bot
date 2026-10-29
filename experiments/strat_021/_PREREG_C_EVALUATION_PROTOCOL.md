# STRAT-021 Pre-Registration C — Portfolio-Level Sub-Sleeve Evaluation Protocol (FROZEN)

> **Status: FROZEN before any portfolio-return computation.**
> Owner directive 2026-10-29 01:39 UTC. Pass/fail bar adapted from
> docs/TWO_SLEEVE_PORTFOLIO_DESIGN.md §11 (PR #113). IC ≠ profitability:
> this protocol evaluates PORTFOLIO-LEVEL net return/risk, not factor IC.

## 1. Sleeves evaluated (STANDALONE, equal-weight)

- **MOM** = STRAT-013-R2 Q5 momentum (Pre-Registration A).
- **LVOL** = STRAT-015-R2 Q1 low volatility (Pre-Registration B).
- **GP** = STRAT-017 frozen Q5 gross profitability (frozen §15/16
  pre-registration, SHA 2e587ed5…; cohort files ARE the selection —
  methodology unchanged, re-evaluated here under this protocol for
  like-for-like comparison).
- **Benchmark:** SPY monthly returns from the frozen daily bars
  (same window), reported alongside.

## 2. Return computation

- Monthly holding-year returns July(Y)→June(Y+1) for formations
  Y = 2017…2025 (9 complete holding years), equal weight within each
  sleeve, from `monthly_research.csv.gz` adjusted closes.
- Monthly sleeve return = mean of member monthly returns; members with a
  missing month are carried at that member's last available observation
  rule: **exclude missing months from that member's contribution and
  re-normalize weights among available members that month** (no forward
  filling of prices across terminal gaps, consistent with STRAT-017 §16.5).
- 2026 partial (July 2025 formation year already complete; no 2026
  formation is evaluated).

## 3. Cost model (pre-registered)

- **20 bps per side** applied to **traded value at each annual
  formation** (entries + exits), charged to the sleeve's July(Y) return:
  `cost_drag = 0.002 × one_way_turnover_fraction`, where
  `one_way_turnover_fraction = 0.5 × Σ|w_new − w_old|` (buys or sells
  side, counted once per side), first formation treated as full turnover
  (Σw = 1 one-way).
- No intra-year costs (no intra-year trading).
- Absolute dollar-volume feasibility is NOT modeled (documented data
  limitation); the relative liquidity floor in the pre-registrations is
  the only liquidity treatment.

## 4. Reported metrics (per sleeve, net of costs)

1. Cumulative and annualized net return; monthly net return series.
2. Annualized volatility; net Sharpe (rf = 0; stated).
3. Maximum drawdown (monthly closes).
4. Mean monthly net return with bootstrap 95% CI (10,000 resamples,
   stationary block bootstrap, block length 6 months) for: sleeve return,
   sleeve − SPY spread.
5. Turnover: per-formation one-way turnover fraction and mean.
6. Holding-year member counts.
7. Pairwise Pearson correlations of monthly net returns among MOM, LVOL,
   GP, SPY.

## 5. Pass/fail bar (frozen)

A sub-sleeve PASSES as a standalone long-term sub-sleeve candidate if,
net of costs over the 9 complete holding years:

1. **vs SPY:** mean monthly net excess return (sleeve − SPY) bootstrap
   95% CI lower bound > −0.10% (i.e., not materially worse than SPY), AND
2. **Positive spread:** point estimate of mean monthly net excess return
   > 0, AND
3. **Drawdown discipline:** sleeve net max drawdown ≤ SPY max drawdown
   × 1.25.

Diversification criterion (reporting, not pass/fail): pairwise sleeve
return correlation < 0.5 recommended for separate sub-sleeves; reported
with the caveat that overlapping Q5 memberships inflate correlation.

## 6. Caveats carried in every output

- **Survivorship:** coverage gaps skew toward delisted/acquired tickers;
  owner qualification 2026-10-12 (selection bias NOT ruled out) stands.
- Equal-weight, quintile, annual-rebalance results are research
  constructs; execution feasibility at paper-scale sizes is not proven
  (no bid-ask/impact data).
- 2017–2026 window only; no regime claim beyond it.
- Momentum/vol universes use full-universe breakpoints (deviation from
  STRAT-017's NYSE breakpoints).

## 7. Prohibitions

No re-optimization; no parameter sweeps; no additional factors; no
composite signals; no new data acquisition; no modification of frozen
STRAT-013/015/017/019/020 artifacts.
