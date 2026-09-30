# STRAT-005 Liquidity-Aware Universe Shadow Experiment

Window: 2026-09-30T14:10:34Z → 2026-10-01T00:00:00Z
Queue size: 12966
Alpaca universe (cached snapshot): 13508 symbols
Cycles per scenario: 100 × 30 slots = 3000 analyses

## Design Notes

Replay-based shadow. Frozen bars do not exist in `trading_bot.db`,
and the directive forbids new Alpaca REST calls for bars. This harness
replays the frozen analyzer output from `decision_snapshot.signal_pipeline`
(avg_volume, high_low_pct, total_score, analyzer_signal). See module docstring.

RS rank is approximated by `total_score DESC` (the production RS rank
requires SPY comparison + 252d window; not reconstructable without bars).

Pass-through for symbols with no known avg_volume (production fails open).
All universe symbols have a `decision_snapshot` by construction, so
MKT-CACHE-001 ineligible count is 0 in every scenario.

## Headline Result

**Tradeable SHADOW BUYs (passing downstream 1M-share + 0.9% high-low rule): 0 in every scenario.**

Even with the most permissive floor (F_10000, 10k shares/day), the highest-
volume analyzer BUY is YQ @ 211,788 avg_volume, which fails the 0.9% high-low
rule (5.11%). The next-highest, DSACW @ 80,490, fails the 1M volume rule by
**12.4×**. No combination of pre-analysis floor in {10k, 25k, 50k, 100k, 250k}
produces a single tradeable shadow BUY.

## Scenario Results

| Scenario | Floor | Floor Skips | Slot Replacements | Analyzer BUY Dec | Unique BUY Symbols | Tradeable SHADOW BUYs |
|---|---|---|---|---|---|---|
| CONTROL | None (CONTROL) | 0 | 0 | 43 | 43 | 0 (0 unique) |
| F_10000 | 10000 | 27 | 27 | 16 | 16 | 0 (0 unique) |
| F_25000 | 25000 | 37 | 37 | 6 | 6 | 0 (0 unique) |
| F_50000 | 50000 | 41 | 41 | 2 | 2 | 0 (0 unique) |
| F_100000 | 100000 | 42 | 42 | 1 | 1 | 0 (0 unique) |
| F_250000 | 250000 | 43 | 43 | 0 | 0 | 0 (0 unique) |

## Avg Volume Distribution (Analyzer BUYs per scenario)

### CONTROL
  n=43 min=11.6 p10=60.1 p25=424.4 p50=2838.3 p75=15994.9 p90=31446.7 max=211788.4 mean=14704.1

### F_10000
  n=16 min=10978.2 p10=13542.6 p25=15687.5 p50=20540.0 p75=31446.7 p90=80490.9 max=211788.4 mean=36732.6

### F_25000
  n=6 min=27745.8 p10=27745.8 p25=31446.7 p50=34766.4 p75=80490.9 p90=80490.9 max=211788.4 mean=70866.1

### F_50000
  n=2 min=80490.9 p10=80490.9 p25=80490.9 p50=80490.9 p75=211788.4 p90=211788.4 max=211788.4 mean=146139.6

### F_100000
  n=1 min=211788.4 p10=211788.4 p25=211788.4 p50=211788.4 p75=211788.4 p90=211788.4 max=211788.4 mean=211788.4

### F_250000
  (no BUY in scenario)

## Position-Size Ratio (% of ADV) Distribution

Assumptions: portfolio=$10000, position_pct=1.5%.
### CONTROL
  n=43 min=0.01% p25=0.09% p50=0.51% p75=10.99% p90=49.08% max=96.41% mean=11.80%

### F_10000
  n=16 min=0.01% p25=0.04% p50=0.07% p75=0.30% p90=11.30% max=32.08% mean=3.00%

### F_25000
  n=6 min=0.01% p25=0.02% p50=0.04% p75=0.05% p90=0.05% max=0.30% mean=0.08%

### F_50000
  n=2 min=0.02% p25=0.02% p50=0.02% p75=0.30% p90=0.30% max=0.30% mean=0.16%

### F_100000
  n=1 min=0.02% p25=0.02% p50=0.02% p75=0.02% p90=0.02% max=0.02% mean=0.02%

### F_250000
  (no BUY in scenario)

## Highest-volume BUY in each scenario (would it pass downstream 1M+0.9%?)

| Scenario | Symbol | avg_volume | high_low_pct | Passes downstream? | Position-size ratio |
|---|---|---|---|---|---|
| CONTROL | YQ | 211788 | 5.114 | NO | 0.02% |
| F_10000 | YQ | 211788 | 5.114 | NO | 0.02% |
| F_25000 | YQ | 211788 | 5.114 | NO | 0.02% |
| F_50000 | YQ | 211788 | 5.114 | NO | 0.02% |
| F_100000 | YQ | 211788 | 5.114 | NO | 0.02% |

## Per-Cycle Floor Skips

- CONTROL: total floor skips = 0, replacement slots filled = 0
- F_10000: total floor skips = 27, replacement slots filled = 27
- F_25000: total floor skips = 37, replacement slots filled = 37
- F_50000: total floor skips = 41, replacement slots filled = 41
- F_100000: total floor skips = 42, replacement slots filled = 42
- F_250000: total floor skips = 43, replacement slots filled = 43

## Resource Guard

- SmartBot PID 1260190 still running: True
- DB mtime before/after: 1790811815.9935021 / 1790811815.9935021
- No new Alpaca REST calls beyond asset-list snapshot and top-25 metadata lookups

## Asset Metadata Summary (top 25 BUY symbols)

- Total top BUY symbols: 25
- SPAC-like (acquisition / warrant / unit / right): 16
- ETF-like (fund / trust / etf): 4
- Other (real small/mid-caps): 5

### Top 10 by appearance (full names)

1. **YQ** — 17 Education & Technology Group Inc. American Depositary Shares (NASDAQ, fractionable=False)
2. **DSACW** — Daedalus Special Acquisition Corp. Warrant (NASDAQ, fractionable=False)
3. **GIBO** — GIBO Holdings Limited Class A Ordinary Shares (NASDAQ, fractionable=False)
4. **HCMA** — HCM III Acquisition Corp. Class A Ordinary Share (NASDAQ, fractionable=False)
5. **KRAQ** — KRAKacquisition Corp Class A Ordinary Shares (NASDAQ, fractionable=True)
6. **PAAC** — Proem Acquisition Corp I Ordinary Shares (NASDAQ, fractionable=False)
7. **OCAC.WS** — Ocean Capital Acquisition Corporation Warrants, each exercisable for one ordinary share (NYSE, fractionable=False)
8. **BIAFW** — bioAffinity Technologies, Inc. Warrant (NASDAQ, fractionable=False)
9. **MFUT** — Cambria Chesapeake Pure Trend ETF (BATS, fractionable=True)
10. **OXBR** — Oxbridge Re Holdings Limited Ordinary Shares (NASDAQ, fractionable=False)

## Result Classification

**B. PREFILTERING IMPROVES SCAN QUALITY BUT DOES NOT REVEAL BUYs THAT PASS CURRENT LIQUIDITY REQUIREMENTS**

Pre-filtering dramatically improves scan quality:
- At floor=10000, the analyzer BUY universe collapses from 43 to 16 unique symbols;
  the eliminated 27 are all sub-10k shares/day (genuinely illiquid).
- At floor=25000, only 6 unique survive; at 50k, only 2; at 100k, only 1 (YQ).
- At floor=250000, ZERO analyzer BUYs survive in 100 cycles × 30 slots.

But pre-filtering does NOT reveal any tradeable shadow BUY (passing 1M+0.9%).
The maximum avg_volume observed in any analyzer BUY is YQ @ 211,788 (12.4× below 1M),
which fails the 0.9% high-low rule on its own (5.11%). The next-highest, DSACW @ 80,490,
would only survive a 50k-100k floor but fails the 1M downstream rule by 12.4×.

**Implication:** The buy contract itself (RSI<35 ∧ SMA ∧ MACD) preferentially selects
illiquid micro-cap/SPAC securities. Lowering or raising the floor between 0 and 250k
does not produce a single position that would pass the current 1M downstream rule.
Pre-filtering is **necessary but not sufficient** — to get tradeable BUYs, the BUY
contract must be revisited (separate STRAT-006 candidate).

## Recommended Next Step (DESCRIPTIVE — DO NOT IMPLEMENT)

STRAT-006 (descriptive only): bounded pre-strategy universe restriction to a curated
set of large/mid-cap securities (e.g., S&P 500 + Russell 1000 universe). Even at
floor=10k, the analyzer emitted 43 BUY signals, but 0 are tradable. The BUY contract
appears to be hard-coded to favor illiquid small-caps by selecting volatile micro-caps
that exhibit RSI oversold + SMA crossover more frequently than large-caps. A universe
curated to S&P 500 + Russell 1000 (with avg_volume ≥ 1M by construction) would test
whether the BUY contract can fire AT ALL on tradable large-caps.
