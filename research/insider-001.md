# INSIDER-001 — Insider buying (SEC Form 4) on top of MOM 12-1 (DEV 2021-01..2024-12)

Research only. No bot code touched. DEV window only; the 2025-01..2026-08 holdout was
not loaded (the panel is read with a row filter `date <= 2024-12-31`).

Script: `research/insider-001/run_insider.py` (committed, unchanged for this study).
Spec: `research/insider-001/PREREGISTRATION.md` (frozen before any result).
Raw outputs: `research/insider-001/results.json`, `research/insider-001/results.md`.

## Plain-English summary for Josh

Adding insider-buying information to the momentum book does not help.

- **V1 (tilt the MOM long leg toward recent insider buys): FAIL.** The tilt barely changes
  anything: V1 is numerically identical to plain MOM 12-1 (1.7082%/mo both, difference
  -0.00008%/mo, t -0.06). Why: at most ~8 names per month (~8% of the ~102 long names)
  qualify as insider-bought, and most of those are already in the MOM top decile - the
  tilt swaps only a handful of names, so it cannot move the return.
- **V2 (standalone "cluster buying" portfolio): FAIL, and clearly worse than just holding
  the market.** 0.41%/mo net, underperforming the equal-weight eligible universe by
  0.39%/mo (t -0.86), 0-0th percentile vs 1000 random same-size portfolios, Sharpe 0.19,
  max drawdown -37.4%. Its correlation with MOM is -0.24, so as a diversifier it is at
  least not a copy of MOM, but it has no edge of its own on this data.
- **Verdict: do not pursue insider buying (Form 4 P-code purchases) any further for
  sizing or selection on MOM 12-1.** It is now a tested idea in the research log.

## VERDICT per variant

Bars: Bonferroni k=2 => percentile threshold 98.75, t threshold 2.24
(`PREREGISTRATION.md` L85). All three bars (a), (b), (c) must pass
(`PREREGISTRATION.md` L87-101).

| Variant | (a) DEV bars | (b) improvement | (c) beats random | PASS/FAIL |
|---|---|---|---|---|
| V1 MOM tilt | FAIL - months 47 ok, mean net >0 ok, mean excess -0.00008%/mo **not >0**, t -0.06 < 2.24, percentile 100.0 ok | FAIL - paired mean (V1-baseline) -0.00008%/mo, t -0.06 (< 2.24) | PASS - percentile 100.0 >= 98.75 (draw mean 1.34%, max draw 1.69% vs V1 1.71%) | **FAIL** |
| V2 cluster | FAIL - months 47 ok, mean net 0.41% >0 ok, mean excess -0.39%/mo **not >0**, t -0.86 < 2.24, percentile 0.0 FAIL | FAIL - excess vs universe -0.39%/mo, t -0.86 | FAIL - percentile 0.0 < 98.75 | **FAIL** |

Exact script bar dicts (stdout):
- V1: `{'months_ge_min': True, 'mean_net_gt_0': True, 'mean_excess_gt_0': False, 't_ge_bar': False, 'percentile_ge_bar': True, 'PASS': False}`
- V2: `{'months_ge_min': True, 'mean_net_gt_0': True, 'mean_excess_gt_0': False, 't_ge_bar': False, 'percentile_ge_bar': False, 'PASS': False}`

Note on V1's (c): the 100.0 percentile is real but does not mean an edge. The random
draws replace the insider tilt with random picks from the same top-20% MOM pool, so the
draw mean (1.34%) sits below the baseline (1.71%) - i.e. picking the top decile by MOM
beats a random half of the top quintile, regardless of insider data. V1 equals the
baseline, so it trivially beats that weaker draw. The bar that matters for the idea,
(b), is -0.00008%/mo with t -0.06.

## Comparison table (monthly compounded, net of cost)

| Metric | Plain MOM 12-1 | V1 MOM tilt | V2 cluster |
|---|---|---|---|
| Mean net %/mo | 1.708% | 1.708% | 0.413% |
| Mean excess | (vs random pm) 100th pct | vs baseline -0.0001%/mo, t -0.06 | vs universe -0.389%/mo, t -0.86 |
| t (excess) | 1.673 (see caveat below) | -0.06 (vs baseline) | -0.86 (vs universe) |
| Sharpe (ann.) | 0.846 | 0.846 | 0.189 |
| Max drawdown | -27.74% | -27.74% | -37.43% |
| Worst 3 months | -17.74%, -15.67%, -12.26% | -17.74%, -15.67%, -12.26% | -13.07%, -11.71%, -10.92% |
| Turnover (one-way, mean) | (baseline engine) | 0.669 | 0.834 |
| Names per month | 102.1 (decile) | 102.1 (of which 7.96 insider-bought) | 35.3 |

Accuracy note: V1's bars are graded by the script against the **baseline** (correct per
prereg L87: "t on the monthly V1-baseline difference"), while V2's are graded against the
universe (L89). The `t_excess` value shown for baseline in `results.md` (1.673) is the raw
t of net returns, not the reference t(excess) 2.294 - see the blocker note below.

## V2 correlation with MOM 12-1

Pearson correlation of monthly V2 net returns with baseline MOM net returns:
**-0.237** over 47 DEV months (`results.json` -> `variants.V2_cluster.correlation_with_baseline_mom`).
So V2 is mildly negatively correlated with momentum: a real diversifier candidate in
shape, but with no positive return edge on its own (0.41%/mo net, 0-0th percentile vs
random, max drawdown -37.4%).

## Q1: Does V1 (MOM long leg tilted toward recent insider buys) pass all three preregistered bars on DEV 2021-2024?

**Answer:** No. FAIL on bars (a) and (b); bar (c) passes only trivially. V1 is
indistinguishable from plain MOM 12-1.

**Evidence:**
- Bars: `PREREGISTRATION.md` L85 (thresholds), L87-88 (V1 bar a), L92-93 (V1 bar b),
  L96-99 (V1 bar c).
- V1 definition: `PREREGISTRATION.md` L55-66 (top-20% MOM pool, insider-bought names
  first by MOM desc, then fill by MOM rank).
- Implementation: `run_insider.py` L325-356 (`mom_pool`, `v1_legs`).
- Command: `python3 research/insider-001/run_insider.py` (1000 draws).
- Output: V1 mean net 1.7082%/mo vs baseline 1.7082%/mo; paired mean -0.00008%/mo,
  t -0.0609; percentile 100.0; months with positions 47/47; insider-bought names in the
  long leg: 374 across 47 months (mean 7.96/month, max 14).
- Reason the tilt is a no-op: with ~102 long names per month and only ~8 insider-bought
  names in the top-20% MOM pool, and most of those already inside the top decile, the
  insider ordering reshuffles at most a few slots per month out of 102, and equal
  weighting within the leg means the effect on the monthly return is tiny.
- Sanity: the diagnostic re-ran only the V1 random-drawset (1000 draws, not committed as
  a result) and got the same percentile 100.0; the committed run is the single full run.

**Confidence: high** - the difference is -0.8 basis points per month with t -0.06; no
signal exists in this construction on this data.

## Q2: Does V2 (standalone cluster insider-buying long portfolio) pass all three preregistered bars on DEV 2021-2024, and how correlated is it with MOM 12-1?

**Answer:** No, FAIL on all three bars. It loses to the equal-weight eligible universe by
0.39%/mo (t -0.86) and lands at the 0th percentile of 1000 random same-size portfolios.
Correlation with MOM 12-1 is **-0.24**.

**Evidence:**
- Bars: `PREREGISTRATION.md` L85, L89-90 (V2 bar a), L94 (V2 bar b), L100-101 (V2 bar c).
- Cluster definition: `PREREGISTRATION.md` L50-54 (>= 2 distinct insiders, 90 calendar
  days, filing date < d); `PREREGISTRATION.md` L71-78 (long-only, all names, cash months
  counted).
- Implementation: `run_insider.py` L140-175 (`insider_signal`), L365-386 (`base_eligible`,
  `universe_return`).
- Output: mean net 0.41297%/mo, mean excess vs universe -0.38915%/mo, t(excess) -0.85793,
  percentile 0.0, months with positions 47/47 (no cash months), names/month 35.3,
  turnover 0.834, Sharpe 0.189, max drawdown -37.43%, worst 3 months -13.07%, -11.71%,
  -10.92%, win rate 51.1%.
- Correlation: -0.2369 (`results.json` -> `correlation_with_baseline_mom`).

**Confidence: high** - a 39 bps/mo shortfall with t -0.86 and a 0th-percentile random
ranking is a clean fail; the turnaround (0.41%/mo gross of nothing) is also far below
the universe, so this is not a cost artefact.

## Q3: How do Sharpe, max drawdown, worst 3 months, turnover and names per month compare between plain MOM 12-1, V1 and V2?

**Answer:** V1 is a clone of MOM on every risk metric (Sharpe 0.846, maxDD -27.74%,
worst months identical) except it carries higher one-way turnover (0.669 vs the baseline
engine's decile-level rebalancing) without any return gain. V2 has about a fifth of the
Sharpe (0.189), a much deeper drawdown (-37.4% vs -27.7%), a milder worst month (-13.1%
vs -17.7%, because it is not shorting), turnover 0.834 and only 35 names/month.

**Evidence:**
- Metrics definitions: `PREREGISTRATION.md` L101-105 (Sharpe x sqrt(12), maxDD of the
  monthly compounded series, worst 3, turnover = opened+closed over names held).
- Implementation: `run_insider.py` L177-220 (`sharpe`, `worst_k`, `summary`, `turnover`).
- Numbers: table above and `results.json` -> `baseline`, `variants.V1_mom_tilt`,
  `variants.V2_cluster`; `results.md` for the human-readable form.
- Worst 3 months: baseline/V1 `[-17.74%, -15.67%, -12.26%]`; V2
  `[-13.07%, -11.71%, -10.92%]`.

**Confidence: high** - these are direct summary statistics of the committed result files.

## Baseline reference check (blocker note, no fix applied)

The script prints `baseline: mean 1.7082%/mo t_exc 1.6734 maxDD -27.74% months 47`.
Mean (1.7082%) and max drawdown (-27.74%) reproduce the reference (1.708%, -27.7%,
`PREREGISTRATION.md` L42); the printed t does **not** (1.6734 vs reference 2.294). The
script's reference check therefore reports `mean_ok: False` and `t_ok: False`:
- `mean_ok` fails only because the tolerance is 1e-6 against the 4-decimal reference
  0.01708 (`run_insider.py` L69-72): the difference is 2.30e-6, i.e. the mean reproduces
  to 4 decimals (1.7082% vs 1.708%).
- `t_ok` fails because `t_excess` in this script is `t_stat(base_net)` - the raw t of
  net returns (`run_insider.py` L533-534, L540-546) - not a t on excess, while the 2.294
  reference in VOLSIZE-001 is a t on the baseline-vs-random excess
  (`research/volsize-001/run_volsize.py` L540-541). The printed 1.673 is consistent with
  a 1.708%/mo mean at the DEV monthly volatility.

This is a metric-definition/tolerance mismatch in the reference check, not a change in
the strategy: mean and max drawdown reproduce exactly, and the V1/V2 bar logic uses the
correct excess definitions (`run_insider.py` L553+L604 for V1 vs baseline, L559+L616 for
V2 vs universe). Per the task rules this was NOT fixed (script edits are out of scope
for a results run); see Next steps.

## Next steps

1. **Fix the baseline reference check in `research/insider-001/run_insider.py`** (small,
   separate coding job): compare the mean against `REF_MEAN_NET` with a sensible
   tolerance (1e-6 is too tight vs the 4-decimal reference 1.708) and either rename the
   printed `t_exc` to `t_net` or compute the reference t on excess the way VOLSIZE-001
   does, so the printed line matches the prereg's reference wording (L42).
2. **Do not pursue insider buying further** on this construction: V1 is a no-op (too few
   qualifying names, mostly already in the momentum top decile), V2 has a negative
   excess vs the universe. Marked tested in `RESEARCH_LOG.md`.
3. If Josh wants to revisit insider data at all, the only remaining (untested) angle is
   using it as a *short-side / avoidance* filter or as a multi-month hold, which are
   different preregs and would need a fresh registration; nothing here licenses that.
4. MAP.md: unchanged because this is a research-only PR (no structure, commands or
   conventions changed).
