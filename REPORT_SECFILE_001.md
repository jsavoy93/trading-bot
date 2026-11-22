# SECFILE-001 — SEC share-sale filing filter on MOM 12-1, DEV 2021-01..2024-12

VERDICT: **V1 REGISTRATION — FAIL. V2 REGISTRATION+OFFERING — FAIL.** Neither clears bar (b)
(real improvement) nor bar (c) (beats same-count random removals): the "gain" sits inside the
random band. Research/paper only.

| metric (47 months) | unfiltered MOM | V1 (S-1/3, F-1/3) | V2 (+424B1-7) |
|---|---|---|---|
| mean net /mo | 1.708% | 1.740% | 1.841% |
| t on excess | 2.294 | 2.324 | 2.418 |
| percentile vs random MOM | 100.0 | 100.0 | 100.0 |
| maxDD | -27.74% | -28.60% | -26.78% |
| ann. net / win rate | 22.5% / 68.1% | 23.0% / 70.2% | 24.5% / 72.3% |
| (a) filtered passes DEV bars | ref | PASS | PASS |
| (b) improvement mean / t | — | +0.032%/mo, t 0.47 FAIL | +0.133%/mo, t 1.20 FAIL |
| (c) percentile vs same-count random removals | — | 75.7 (< 98.75) FAIL | 94.9 (< 98.75) FAIL |

Coverage: ticker->CIK from free SEC maps (current + exchange file) covers 96.9% of 4800 DEV
long-leg name-months (77.0% have any of the 12 target forms 2020Q4-2024Q4). Unmapped names stay
in the long leg. 17 form.idx files cached (738 MB); 544,940 relevant filings 2020-10-01..2024-12-31
(424B2 dominates: 455,705; S-1 5,258; S-3 4,552; S-3ASR 8,376; F-1 1,106; F-3 787; F-3ASR 370).
Only the long leg is filtered; short leg unchanged; filings strictly before the decision date.

Removed from long leg: V1 avg 3.91/mo (184 total, 3.8% of ~102 names, 45/47 months); V2 9.43/mo
(443 total, 9.2%, all 47 months).

Removed vs kept next-month return (descriptive): V1 removed +0.22%/mo (n=184) vs kept +1.76%;
V2 removed -0.11%/mo (n=443) vs kept +1.89%. Removed names do underperform, but too thinly to
survive bar (b)/(c).

3 worst DEV months (before vs after):
- V1: 2023-01 -17.74% -> -17.53%; 2021-02 -15.67% -> -16.11%; 2022-06 -12.26% -> -11.84%
- V2: 2023-01 -17.74% -> -18.03%; 2021-02 -15.67% -> -15.21%; 2022-06 -12.26% -> -11.79%

Plumbing: results-index path fix, plus a filtered-leg bug that silently kept the full long leg
(fixed, run repeated). No rule/form/window/bar change.

For Josh: SEC form filtering does not rescue MOM 12-1 on DEV (2025+ burned). Artifacts: this file,
results_index.json, monthly_nets.csv, summary_metrics.json, scripts_secfile/ (prereg 72e9333).
