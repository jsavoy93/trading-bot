# SMOOTHMOM-001 — smooth vs spiky momentum (DEV results)

Step 2 of SMOOTHMOM-001. Research only: no bot code change, no merge, no deploy, no
paper/live trading. Design frozen in `research/smoothmom-001/PREREGISTRATION.md`
(as read by `PREREGISTRATION_ERRATUM.md`); this file only records the result.

**Window:** DEV 2021-01 .. 2024-11 (47 rebalance months). Entry open >= 2021-01-01 and
exit open <= 2024-12-31, so the 2024-12 rebalance is excluded (its exit open is in
2025-01, i.e. inside the BURNED holdout). 47 >= 24 months, so the month-count bar is
met. Holdout rows were never loaded (pyarrow row filter + assert at load).

## VERDICT

Both variants **FAIL**. Bonferroni k=2 percentile bar = 98.75, t bar = 2.24.

| | (a) DEV bars | (b) improvement over plain MOM | (c) beats random | Overall |
|---|---|---|---|---|
| **V1 double sort** | **FAIL** — t(excess) 0.149 < 2.24, pct 63.0 < 98.75 (mean net > 0 and excess > 0 do pass) | **FAIL** — −0.932%/mo, t −1.543 | **FAIL** — pct 63.0 | **FAIL** |
| **V2 combined score** | **FAIL** — t(excess) 0.592 < 2.24, pct 92.4 < 98.75 (mean net > 0 and excess > 0 do pass) | **FAIL** — −0.659%/mo, t −1.008 | **FAIL** — pct 92.4 | **FAIL** |

Plain MOM 12-1 remains the better portfolio on every return metric. Smoothness does
not add value on this data; it subtracts.

## Headline table (DEV 2021-01..2024-11, 47 months, 1000 random draws)

| portfolio | mean net /mo | annualised | t(excess) | pct vs random | Sharpe | max drawdown | worst 3 months | turnover |
|---|---|---|---|---|---|---|---|---|
| Baseline MOM 12-1 | **+1.708%** | +22.54% | **+2.294** | 100.0 | **0.846** | **−27.74%** | −17.74% / −15.67% / −12.26% | 0.186 |
| V1 double sort | +0.776% | +9.72% | +0.149 | 63.0 | 0.419 | −28.00% | −18.95% / −18.63% / −9.61% | 0.197 |
| V2 combined score | +1.049% | +13.34% | +0.592 | 92.4 | 0.538 | −27.85% | −19.43% / −19.37% / −10.94% | 0.189 |

Paired monthly difference vs baseline: V1 **−0.932%/mo (t −1.543)**,
V2 **−0.659%/mo (t −1.008)**.

Turnover is the script's mean one-way name turnover per month over the whole book.
Sharpe is annualised from monthly net returns (mean/sd × sqrt(12)).

## In plain English for Josh

The "frog in the pan" idea was that stocks whose momentum came from many small steady
days keep working better than stocks that jumped in a few big moves. Tested the
pre-registered way on 2021-2024, it does not hold up. Both the double-sort version and
the blended-score version earn roughly half to two-thirds of plain momentum
(0.78% and 1.05% a month vs 1.71%), and neither beats plain momentum, while their
excess-over-random is statistically indistinguishable from zero (t 0.15 and 0.59).
V2 is not terrible — it is a bit less volatile-over-time and drew down no worse — but
it is beaten by the simple thing we already have, so there is nothing to adopt.
Recommendation: close the idea, keep plain MOM 12-1 as the momentum reference, and
stop retrying smoothness refinements on this data.

## Q1: Does V1 (double sort: smoothest half of top/bottom MOM quintiles) pass all three preregistered bars on DEV 2021-2024?

**Answer:** No. V1 fails all three bars.

**Evidence:**
- Bar (a): returns 0.776%/mo net with positive excess, 47 months — but
  t(excess) = 0.149 (needs >= 2.24) and percentile vs random = 63.0 (needs >= 98.75).
  `research/smoothmom-001/results.json` → `v1.t_excess = 0.14946185273770263`,
  `v1.percentile_vs_random = 63.0`; `bars.v1_a = false`.
- Bar (b): paired difference vs baseline −0.932%/mo, t −1.543 →
  `v1_vs_baseline = {mean: -0.009321931242172673, t: -1.5429187810590963}`;
  `bars.v1_b = false`.
- Bar (c): 63.0 percentile vs 1000 random draws that pick a random half of the top and
  bottom MOM quintiles (`v1.percentile_vs_random = 63.0`, `v1.random_mean =
  0.007043978365746973`); `bars.v1_c = false`.
- Command: `python3 research/smoothmom-001/run_smoothmom.py` (from the worktree root),
  runtime 146.7s, output `baseline mean_net check: |0.017082 - 0.017082| = 2.99e-07`.
- Determinism sha256 `be398966bb07292ceb73a14ca9acb08bdcfbca2790b1ccc27eb5bace19ed7187`
  (`results.md` last line).

**Confidence:** high — the study is pre-registered, single-run, and the result is not
near any bar (t 0.15 vs 2.24; pct 63 vs 98.75), so noise or rounding cannot flip it.

## Q2: Does V2 (combined MOM + smoothness score) pass all three preregistered bars on DEV 2021-2024?

**Answer:** No. V2 fails all three bars, though it is closer than V1.

**Evidence:**
- Bar (a): 1.049%/mo net, positive excess, 47 months — but t(excess) = 0.592 (needs
  >= 2.24) and percentile vs random = 92.4 (needs >= 98.75).
  `results.json` → `v2.t_excess = 0.5918700010730898`,
  `v2.percentile_vs_random = 92.4`; `bars.v2_a = false`.
- Bar (b): paired difference vs baseline −0.659%/mo, t −1.008 →
  `v2_vs_baseline = {mean: -0.006589734297040833, t: -1.0083244300370462}`;
  `bars.v2_b = false`.
- Bar (c): 92.4 percentile vs 1000 random same-size legs drawn from the top/bottom MOM
  quintiles (`v2.percentile_vs_random = 92.4`, `v2.random_mean =
  0.007112861183324506`); `bars.v2_c = false`.
- V2's score component is direction-aligned `pct_rank(sign(MOM) x (-ID))` as the
  erratum requires (`run_smoothmom.py:252-255`), so this is not the erratum's bug.

**Confidence:** high — same reasoning as Q1; the point estimate is also below the
baseline, so it fails (b) in the direction of *worse*, not merely insignificantly.

## Q3: How do Sharpe, max drawdown, worst 3 months and turnover compare between plain MOM 12-1 and each variant?

**Answer:** Plain MOM 12-1 wins on return and Sharpe at essentially the same drawdown
and turnover. Smoothing did not buy risk reduction.

**Evidence** (`research/smoothmom-001/results.json`, echoed in `results.md`):

| metric | baseline | V1 | V2 | reading |
|---|---|---|---|---|
| Sharpe (ann.) | 0.846 | 0.419 | 0.538 | both variants roughly half the baseline risk-adjusted return |
| max drawdown (monthly) | −27.74% | −28.00% | −27.85% | no meaningful difference; smoothing did **not** cut the drawdown |
| worst 3 months | −17.74 / −15.67 / −12.26 % | −18.95 / −18.63 / −9.61 % | −19.43 / −19.37 / −10.94 % | both variants have a *worse* worst month than baseline |
| turnover (one-way/mo) | 0.186 | 0.197 | 0.189 | V1 turns over ~6% more; V2 ~2% more — nothing to gain |
| win rate | 68.1% | 63.8% | 70.2% | V2 wins slightly more months, but smaller when it wins |

So the risk story is the decisive one: if smoothing had cut the −27.7% drawdown
materially it might have earned a place, but it did not (−28.0% / −27.9%), while
annualised return fell by roughly half to two-thirds.

**Confidence:** high — same single deterministic run; metrics are direct cell reads.

## Correctness check (synthetic, before the real run)

Run as `/tmp/sm_check.py` (scratch, not committed; imports the committed
`run_smoothmom.py` and `engine_fixed.py` unchanged). Four synthetic 300-day series
with a decision date at bar 280, so the ID window is bars 29..259 (231 days):

| series | construction | ID |
|---|---|---|
| smooth winner | +0.10%/day, tiny noise | **−1.000** |
| smooth loser (steady decline) | −0.10%/day, tiny noise | **−1.000** |
| spiky winner | −0.09%/day dull + four +14% days | **+0.965** |
| spiky loser | +0.09%/day dull + four −14% days | **+0.965** |

Confirms the required behaviour: **low (negative) ID for a steady winner AND for a
steady loser (many small DOWN days); high ID for a winner driven by a few big jumps.**
The ID ladder and "lower ID = smoother" both hold.

Note on the erratum: §2's prose in `PREREGISTRATION_ERRATUM.md` says smooth losers have
*positive* ID. That prose is wrong — a steady decline has mostly down days, so
(%neg − %pos) is positive and the negative sign factor makes ID negative. The formula
and the rule "lower ID = smoother" are right, and the **code implements the formula**,
not the prose. This matches the task's own note and did not require any change.

Selection with 40 synthetic names (4 behaviours × 10 copies, MOM set so each behaviour
sits in a known quintile):
- V1: long = `[0,1,4,5]` (all from the top MOM quintile, all negative-ID = smooth
  winners); short = `[24,25,28,29]` (all from the bottom MOM quintile, negative-ID =
  smooth losers). So **V1 shorts the lowest-ID losers** — correct.
- V2: long `[0,1,4,5]`, short `[24,25,28,29]`; smooth losers excluded from the long
  leg. So **`sign(MOM) x (−ID)` ranks smooth winners high and smooth losers low** —
  correct, matching erratum §1.

The code agrees with the frozen formula and both variant definitions; no STOP condition
was triggered.

## Sanity check (baseline reproduction, prereg §6)

```
baseline mean_net check: |0.017082 - 0.017082| = 2.99e-07
```

Full baseline from `results.json`: mean_net 0.01708229936906453 (1.708%/mo),
t_excess 2.2943640317237652, max_drawdown −0.2774257906660196 (−27.74%). Matches
1.708% / 2.294 / −27.7% within the erratum §3 tolerances (5e-5 / 5e-3 / 5e-3). The
script asserts this and exits non-zero otherwise; it did not trip.

## Notes / caveats

- Only DEV was run; the 2025-01..2026-08 holdout is burned and was never loaded.
- One run only, pre-registered settings, no tuning, no extra variants.
- Two benign `RuntimeWarning`s were printed by `engine_fixed.py:160`
  (`Mean of empty slice` for advance/volume windows of names with no history in the
  early DEV months). They do not change any result — the baseline reproduces the
  frozen reference to 3e-7 — but they are the one cosmetic thing a future engine
  cleanup could silence. Not fixed here (research-only job).
- Window is 47 months, not 48: the 2024-12 rebalance exits in 2025-01, outside DEV.

## Next steps

1. **Close SMOOTHMOM-001.** Both variants fail every bar; do not re-run or re-tune
   smoothness on MOM 12-1. Recorded in `/root/research/RESEARCH_LOG.md`.
2. **Keep plain MOM 12-1** as the momentum reference (DEV t 2.294). Nothing here
   changes the standing "MOM 12-1 failed holdout" conclusion.
3. Possible separate coding job (not done here, research-only): silence the
   `Mean of empty slice` warning at `engine_fixed.py:160` — cosmetic only.
4. Possible future (not this job): if smoothness is ever revisited, it should be tested
   as an *exit/sizing* signal rather than a selection tilt, since the selection tilt
   only loses return here.
