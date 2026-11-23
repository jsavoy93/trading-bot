# VOLSIZE-001 — Volatility-scaled position sizing on MOM 12-1 (DEV 2021-01..2024-12)

RESEARCH MODE (approved by Josh Nov 22, 2026). DEV window only; the
2025-01..2026-08 holdout is BURNED and was never read.

**VERDICT — V1 (inverse-vol weights): FAIL on all three bars.**
- bar (a) DEV bar (mean net>0, mean excess>0, pct>=98.75, t(excess)>=2.24): **FAIL** — pct 33.8, t(excess) -0.573.
- bar (b) improvement over plain MOM (paired mean>0, t>=2.24): **FAIL** — mean -0.261%/mo, t -1.044.
- bar (c) beats same-name random weightings (pct>=98.75): **FAIL** — pct 33.8.

**VERDICT — V2 (15% vol target): FAIL on all three bars.**
- bar (a): **FAIL** — mean net +1.918%/mo but pct 74.1 and t(excess) 0.622 (both below pct 98.75 / t 2.24).
- bar (b): **FAIL** — mean +0.210%/mo, t 0.572.
- bar (c): **FAIL** — pct 74.1 vs shuffled-exposure paths (threshold 98.75).

Both variants are dead as registered. Nothing was tuned, re-run with other settings, or fixed here.

## Q1: Does V1 (inverse-vol weights) pass all three preregistered bars on DEV 2021-2024?
**Answer:** No. V1 fails (a), (b) and (c). It is *worse* than plain MOM on every return measure, and
no better than putting random weights on the same names.

**Evidence:**
- Run: `python3 research/volsize-001/run_volsize.py` (1000 draws, one run, 74.1s), outputs
  `research/volsize-001/results.json` / `results.md`.
  - mean net **1.448%/mo** (vs baseline 1.708%), Sharpe **0.750** (vs 0.846), maxDD **-25.77%**
    (vs -27.74%), worst 3 months -18.98 / -12.27 / -10.15%, win rate 68.1% (unchanged).
  - t(excess) **-0.573**; percentile vs the 1000 random *weightings of the same names* **33.8**
    (random weightings average 1.591%/mo, i.e. slightly better than V1).
  - paired difference V1 - baseline: mean **-0.261%/mo**, t **-1.044**.
- Prereg bars: `research/volsize-001/PREREGISTRATION.md:79-100`;
  thresholds in `research/volsize-001/run_volsize.py:40-41` (BONFERRONI_P 98.75, BAR_T 2.24).
- Read: inverse-vol weighting tilts toward quiet names, and those names contributed less than the
  average name in this book; the tilt mostly traded return for a small drawdown reduction
  (-27.7% -> -25.8%), which the bars do not reward.

**Confidence:** high — direction and magnitude agree across all five independent statistics and
the same-name random-weighting control, on the frozen preregistered design.

## Q2: Does V2 (15% vol target) pass all three preregistered bars on DEV 2021-2024?
**Answer:** No. V2 raises mean net and Sharpe slightly but fails (a), (b) and (c).

**Evidence:**
- mean net **1.918%/mo** (ann +25.60%), Sharpe **0.890**, maxDD **-29.60%**, worst 3 months
  -21.48 / -13.61 / -10.57%, win rate 68.1%.
- t(excess) **0.622**; percentile vs shuffled exposure paths **74.1** (random paths average
  1.687%/mo, close to V2's 1.918%).
- paired difference V2 - baseline: mean **+0.210%/mo**, t **0.572**.
- Scale stats: min 0.497, max 1.500, mean 1.029 — the target mostly ran near 1.0x, with the band
  capped at 1.5x during low-vol months; that is where the extra return and extra drawdown come from
  (cost-on-scale-change rule: `PREREGISTRATION.md:70-76`).
- Percentile 74.1 means the real timing of the exposure changes carries no information: shuffling
  the same scale values across months does as well or better 26% of the time.

**Confidence:** high — the shuffled-path control isolates timing, and it shows none.

## Q3: How do Sharpe, max drawdown and the worst 3 months compare — plain MOM 12-1 vs each variant?

**Answer:** V1 is uniformly weaker (lower mean, lower Sharpe, mildly smaller drawdown). V2 is
slightly stronger on mean/Sharpe but with a deeper drawdown and a worse worst month. Neither
improvement is statistically distinguishable from noise.

**Evidence:**

| portfolio | mean net /mo | ann | t (excess) | Sharpe | maxDD | worst 3 months | win rate | pct vs random |
|---|---|---|---|---|---|---|---|---|
| baseline MOM 12-1 | +1.708% | +22.54% | 2.294 | 0.846 | -27.74% | -17.74, -15.67, -12.26 | 68.1% | 100.0 |
| V1 inv-vol weights | +1.448% | +18.82% | -0.573 | 0.750 | -25.77% | -18.98, -12.27, -10.15 | 68.1% | 33.8 |
| V2 15% vol target | +1.918% | +25.60% | 0.622 | 0.890 | -29.60% | -21.48, -13.61, -10.57 | 68.1% | 74.1 |

(-t values are relative to each variant's own random benchmark, per prereg section 6.)

**Confidence:** high — all values come from a single frozen run of the committed script
(`research/volsize-001/results.json`).

## Baseline sanity check (prereg section 3)
- Reference: mean net 1.7082%/mo, t(excess) 2.294, maxDD -27.7%.
- Reproduced: mean net **1.70823%/mo** (abs diff 2.99e-7 vs the frozen constant 0.017082 — a
  rounding of the reference constant, not a data difference), t(excess) **2.2944**, maxDD
  **-27.74%**. 47 DEV months. Within the prereg's t tolerance (5e-3); the mean check tripped the
  script's 1e-12 warning only because the reference is stored to 6 decimals
  (`run_volsize.py:53`). Not material.
- Determinism sha256 of the DEV monthly-net vectors: `092a83a80e3e65ce3859cad7659bf5fbdce210a9e9ef42513a0232d3e8e5c35a`.
- Panel actually used: `/tmp/panel_dev.parquet`, sha256
  `cf70f94c31fcec21350ab1aa60a8a599f6bf88f8f344aa283ebc764b23b861f1`, date range
  2019-01-02 .. 2024-12-31 (7 row groups, 6,743,965 rows), produced by
  `DATASET_DIR=/tmp DATASET_SUFFIX=_yearly PARQUET_PATH=/tmp/panel.parquet scripts/build_panel.py`
  on this DEV/yearly dataset (the same build that produced /tmp/panel_check.parquet with the same
  coverage, 9,157,044 rows, no holdout dates). The old pin in `PREREGISTRATION.md` section 2
  (`panel_holdout.parquet`, sha256 4387206b...) points at the full panel that runs to 2026-09-29;
  the script does filter that file to <= 2024-12-31 correctly, but the pin was corrected to the
  DEV-only artifact actually used so the docs describe what ran.

## Plain-English summary for Josh
Two ways of sizing MOM 12-1 positions by volatility were tested against the frozen rules:
give calmer stocks bigger weights (V1), or dial the whole book up/down to target 15% volatility (V2).
Neither passes. V1 is worse than plain momentum — it gives up about 0.26%/month of return and the
vol-tilt delivers no better than random weights on the same stocks. V2 adds about 0.21%/month and
a bit of Sharpe, but takes a deeper max drawdown (-29.6% vs -27.7%), a worse single month (-21.5%),
and a shuffled version of the same scaling does just as well, so the timing adds nothing. The
base MOM 12-1 numbers reproduce exactly. Recommend closing VOLSIZE-001 as FAILED and not pursuing
volatility-based sizing further.

## Next steps
- Close VOLSIZE-001 as FAIL (all bars failed for both variants); no follow-up variant in this PR.
- Script nit (no action taken here — research PR, no code): `run_volsize.py:53` stores
  `REF_MEAN_NET = 0.017082` while the prereg/REGIMESECTOR reference is 1.7082%/mo, so the 1e-12
  tolerance in `run_volsize.py:545-549` always warns. If the script is ever reused, either store the
  reference to more decimals or use the prereg's stated tolerance and treat the warning as noise.
- Docs nit: `README.md` and `PREREGISTRATION.md` section 2 should name the DEV-only panel artifact
  + SHA256 (done in PREREGISTRATION via a correction note in this PR's docs update; README already
  describes the DEV filter). The build recipe for `/tmp/panel_dev.parquet` is recorded above.
- Window note: the run covers 2021-01..2024-11 decision months (47 months; the last month whose
  exit open is <= 2024-12-31). This matches the baseline reference month count (47), so it is the
  preregistered window, not a shortfall.
- The prereg's stated DEV baseline tolerance (1e-12 on the mean) is unusable against a 4-decimal
  reference; tighten the prereg wording for future tasks.
