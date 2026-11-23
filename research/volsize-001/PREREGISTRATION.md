# VOLSIZE-001 — Volatility-scaled position sizing on MOM 12-1 (PREREGISTRATION)

Status: **FROZEN before any result is computed.** Approved by Josh Nov 22, 2026.
RESEARCH ONLY, DEV window only. No code, no tests, no bot changes.

## 1. Scope and non-goals
- Base strategy: MOM 12-1 long-short, monthly rebalance, decile legs, frozen
  construction/costs/panel of SECFILE-001 / REGIMESECTOR-001
  (`scripts_holdout/engine_fixed.py`, COST_PER_SIDE 0.15%, BORROW 1%/yr, MIN_PRICE 5,
  MIN_ADV_SHARES 1e6/20d, DECILE 0.10).
- **Only position SIZE changes.** The picks (leg membership) of MOM 12-1 are identical
  in the baseline and in V1/V2.
- Not a repeat of LONGSHORT-001 (low vol as a stock-PICKING factor). No earlier test of
  vol-scaled *sizing* exists in RESEARCH_LOG.md (checked 2026-11-22).
- Not retested here: CRASHFILTER-001, EVENTNEWS-001, SECFILE-001, REGIMESECTOR-001.

## 2. Data
- Panel: `/root/research/holdout-mom-001/scripts_holdout/panel_holdout.parquet`
  sha256 `4387206b0c4b1ad396596b6f84874b0ad44901237c5ae30adc530e1b8ae5c071` (code asserts prefix/suffix).
- Loaded with a parquet row filter `date <= 2024-12-31` and additionally restricted to
  `date >= 2018-01-01` (the earliest date any DEV momentum window reads). The 2025-01..
  2026-08 holdout rows are never read into memory.
- DEV window: months 2021-01 .. 2024-12 (decision dates, entry/exit dates <= 2024-12-31).
  Any exit date after 2024-12-31 is excluded.
- Free data only; no API keys.

## 3. Baseline (reference, must reproduce)
MOM 12-1 long-short. DEV reference: mean net **1.7082%/mo**, t(excess) **2.294**,
max drawdown **-27.7%**, worst month **-17.7% (Jan 2023)**.
Tolerance: mean net within 1e-12 of the reference constant (as REGIMESECTOR-001 did),
t within 5e-3. Anything else = blocker, stop and report.

## 4. Variants (k = 2, LOCKED, no tuning)
### V1 — inverse-volatility weights
- Same names as MOM 12-1 in each leg.
- Weight of name i within a leg: `w_i ∝ 1 / sigma_i`, where `sigma_i` is the standard
  deviation (ddof=1) of name i's daily returns over the **60 trading days ending at the
  month-end decision close** (same `md.dec` window as the frozen engine's `vol` field;
  data up to the decision date only).
- If any name's sigma is missing/zero, that name gets equal weight within the leg's
  vol-weighted portion (documented fallback); the leg gross stays 1.0.
- Normalize so each leg's gross weight = 1.0 (the baseline).
- Cap: any single name at **3x equal weight** (i.e. 3/n of the leg). Names over the cap
  are set to the cap and the remainder is renormalized over the uncapped names; this
  is applied until no name exceeds the cap (monotone, converges).
- Return of a leg month = sum_i w_i * r_i (r_i = engine entry->exit return, delisting
  fallback identical to the engine).
- Costs: same frozen 0.15% per side **charged on the actual one-way turnover of the
  month**: `cost = 0.15% * sum_i |w_i^cur - w_i^prev|`, where `w_i^prev` is the weight
  held from the previous month (0 for new names, and names that died are closed at
  their exit). `sum |dw|` equals the engine's open+close count times 1/n in the
  equal-weight case, so the baseline convention is a special case.
- Short leg: identical weighting, plus the frozen borrow convention scaled by the name
  weight (`1%/yr * held_days/252 * w_i`).

### V2 — volatility-targeted portfolio scaling
- Equal-weight MOM 12-1 net monthly series exactly as the baseline (each month's gross
  is the baseline net before exposure scaling).
- `realized` = annualized stdev of the **strategy's own daily returns** over the prior
  **63 trading days ending on the decision date** (annualization sqrt(252)).
- `scale_t = clamp(target / realized, 0.25, 1.5)`, target = **15% annualized**, fixed.
- First month (2021-01): if fewer than 63 daily observations exist, scale = 1.0.
- Month P&L = long_pnl * scale + short_pnl * scale (both legs scaled together);
  uninvested remainder earns 0%.
- Costs on actual turnover: long and short leg costs and borrow are multiplied by the
  scale (they are paid on the traded notional). Additionally the change in invested
  notional is charged at 0.15% per side of the change: `0.15% * |scale_t - scale_{t-1}|`
  on the long leg (scale_{t=0} = 0, i.e. initial entry is charged).
- No lookahead: the 63-day window ends at the decision date; nothing from the trading
  month enters the scale.

## 5. Time-series data for V2
- Strategy daily returns: must be risk-adjusted in a way that does not look ahead. The
  daily return of the strategy is proxied by the **equal-weight monthly-rebalance book
  marked to market daily**: each calendar day, the held names' equal-weight (1/(2n) for
  long, -1/(2n) for short) daily returns, using the panel's close-to-close returns and
  the same leg membership as the baseline for that month. Only closes up to the
  decision date are used to build the realized-vol series.
- (This keeps V2 a pure exposure-scaling test: same book, scaled.)

## 6. Bars (per variant)
- (a) DEV bars: n months >= 24, mean net > 0, mean excess > 0,
  percentile vs random >= 97.5, t(excess) >= 2.24.
  - Random benchmark: 1000 random long/short decile portfolios with identical rules
    (frozen `random_monthly_nets`, SEED 20261112) — same as the baseline's benchmark.
- (b) Improvement over plain MOM: paired monthly difference (variant - baseline),
  mean > 0 and t >= 2.24.
- (c) Beats random, variant-specific:
  - V1 vs **1000 random weightings of the same names**: within each leg, draw weights
    from the same weight-generating scheme (Dirichlet(1) then the same 3x-equal-weight
    cap and renormalization, applied on the same names), same gross each leg, same
    cost function (0.15% on turnover), at every month.
  - V2 vs **1000 random monthly exposure paths** with the same distribution of scale
    values: the multiset of real scales is shuffled across months (permutation), each
    shuffled path re-simulated with the same cost-on-scale-change rule.
  - Bonferroni k=2: percentile >= **98.75**.

## 7. Fixed random seeds
- Baseline random benchmark: engine SEED = 20261112 (1000 draws).
- V1 random weightings: `numpy.random.default_rng(20261122 + 10007*d)`, d = 0..999.
- V2 random exposure paths: `numpy.random.default_rng(20261123 + 10007*d)`, d = 0..999.
- Determinism: the run prints a sha256 of the DEV monthly-net vectors; re-running must
  reproduce it.

## 8. Metrics reported
Mean monthly net, annualized net, t(excess), percentile vs random, Sharpe (annualized
net / annualized monthly vol), max drawdown, worst 3 months, monthly win rate, for the
baseline and each variant. No holdout is computed (burned).

## 9. Stop rules
- Baseline reproduction outside tolerance -> stop, report blocker.
- Any lookahead assertion failure -> stop, report blocker.
- No tuning after results; if a variant fails, that is the result.
