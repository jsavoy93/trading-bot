# SMOOTHMOM-001 — Smooth vs spiky momentum on MOM 12-1 (PREREGISTRATION)

Status: **FROZEN before any result is computed.** Approved by Josh Nov 22, 2026.
RESEARCH ONLY, DEV window only. Paper-trading research. No bot code changes outside
`research/smoothmom-001/`.

## 1. Idea ("frog in the pan")
Da, Gurun & Warachka (2014), "Frog in the Pan": stocks whose 12-1 gain came from many
small steady days keep going better than stocks whose gain came from a few big jumps
(investors under-react to gradual information, over-react to salient jumps). This study
tests smoothness as a refinement of the frozen MOM 12-1 long-short.

Not retested here: VOLSIZE-001, CRASHFILTER-001, EVENTNEWS-001, SECFILE-001,
REGIMESECTOR-001. No earlier smoothness test exists in RESEARCH_LOG.md (checked
2026-11-23; the idea was still listed under "Ideas not yet tested").

## 2. Scope and base strategy (frozen, unchanged)
- Base: MOM 12-1 long-short, monthly rebalance, decile legs, equal weight, frozen
  engine `engine_fixed.py` (vendored copy of research/volsize-001/).
- Costs 0.15%/side charged on open / full close (0.30% round trip), short borrow 1%/yr
  prorated by trading days held.
- Filters: close >= $5 at the decision date, mean daily volume >= 1,000,000 shares
  over the last 20 trading days, a finite positive entry open.
- MOM construction identical to the frozen engine: decision on the last trading day of
  month M-1, entry at the open of the first trading day of month M, exit at the open of
  the first trading day of month M+1; MOM = P(end of M-2) / P(end of M-13) - 1.
- Legs: equal weight, about decile size (10% of eligible names per leg).

## 3. Data and window
- Panel: DEV-only file `/tmp/panel_dev.parquet` (columns symbol, date, open, close,
  volume), loaded the same way as research/volsize-001/run_volsize.py: a pyarrow row
  filter at read time plus a loud assert on the loaded dates.
- **Window: DEV 2021-01 .. 2024-12 ONLY.** Every loaded row must satisfy
  `date <= 2024-12-31`; the script asserts this at load time and fails loudly otherwise.
  The 2025-01..2026-08 holdout is BURNED and is never loaded.
- DEV months: entry open >= 2021-01-01 and exit open <= 2024-12-31.
- Free data only; no API keys.

## 4. Smoothness score (ID)
For every stock at every rebalance, over the same 12-1 window used for momentum —
**trading days t-252 .. t-21 inclusive, where t is the rebalance (decision) date** —
using data up to the rebalance date only (no lookahead):

    ID = sign(cumulative 12-1 return) x (% negative-return days - % positive-return days)

- Cumulative 12-1 return = the stock's MOM 12-1 value over that window (P(t-21) /
  P(t-252) - 1, i.e. first to last close of the window, skipping the last month).
- Daily returns inside the window are close-to-close: r_d = close_d / close_{d-1} - 1.
- % negative-return days = (# days with r_d < 0) / (# days with a finite r_d),
  % positive-return days = (# days with r_d > 0) / (# days with a finite r_d).
  Zero-return days count in neither numerator.
- **Lower ID = smoother.** ID is positive for smooth winners (few big up days, many
  small down days) and negative for spiky winners; the sign flip makes smooth losers
  (steady decline) have positive ID too.
- Names with fewer than 200 finite daily returns in the window, or a missing MOM, get
  ID = NaN and are excluded from ID-based selection but still count in the MOM pools.

## 5. Variants (k = 2, LOCKED, no tuning)
Both variants keep the frozen costs, filters, rebalance calendar and equal weights.

### V1 — double sort
- Among the **top 20%** by MOM 12-1, the long leg = the half with the **LOWEST ID**.
- Among the **bottom 20%** by MOM 12-1, the short leg = the half with the **LOWEST ID**
  (smooth losers).
- Equal weight inside each leg. Leg size about the same as the baseline deciles
  (each V1 leg = 10% of eligible names, matching the baseline decile count as closely
  as integer rounding allows).

### V2 — combined score
- score = average of (MOM 12-1 percentile rank) and ((-ID) percentile rank), both
  percentiles in [0, 1] over the month's eligible names; ID is sign-adjusted so smooth
  winners rank high and smooth losers rank low.
- Long leg = top decile by score; short leg = bottom decile by score. Equal weight.

## 6. Baseline reproduction (checked by the script)
Plain MOM 12-1 on DEV must reproduce: mean net **1.708%/mo**, t(excess) **2.294**,
max drawdown **-27.7%**. Tight tolerances, loud failure (assert, non-zero exit):
mean net within 1e-12 of 0.017082, t within 5e-3, max drawdown within 5e-3.
A mismatch is a blocker: stop and report, do not interpret variant results.

## 7. Pass bars (both variants must clear a, b and c)
- (a) DEV bars: >= 24 months, mean net > 0, mean excess > 0, t(excess) >= 2.24,
  percentile vs random >= 98.75 (Bonferroni with k = 2).
- (b) Improvement over plain MOM: paired monthly difference (variant - baseline)
  mean > 0 AND t >= 2.24.
- (c) Beats random: **1000 random draws** picking the SAME number of names at random
  from the same candidate pool:
  - V1: random half of the top MOM quintile for the long leg, random half of the
    bottom MOM quintile for the short leg.
  - V2: random same-size legs drawn from the top / bottom MOM quintile.
  - Percentile of the variant's mean net vs the draw means >= 98.75.

## 8. Reporting (for step 2 only)
Report per portfolio (baseline, V1, V2): mean net, t(excess), percentile vs random,
Sharpe, max drawdown, worst 3 months, turnover, and the paired improvement vs baseline.

## 9. No tuning after this file is committed
No parameter, variant, or bar changes after this commit. The script implements exactly
this file; extra variants or knobs are not allowed.
