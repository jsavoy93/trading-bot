# RSI2RANDOM-001 — audit of the RSI2REGIME-001 random-portfolio baseline (DEV research)

Status: **DRAFT (numbers pending, run in progress).** RESEARCH ONLY — docs/scripts under
`research/rsi2random-001*`. DEV window 2021-01-01..2024-12-31 only; the 2025-01..2026-08
holdout is never loaded (`prepare()` truncates every series at 2024-12-31).

Harness: `research/rsi2random-001/run_rsi2random.py` (imports the frozen
`research/rsi2regime-001/run_rsi2regime.py` so the data loader, indicators, S1a exits and
costs are byte-identical to the audited study). Output: `research/rsi2random-001/results_rsi2random.json`.

## Q1: Is the random-portfolio baseline in RSI2REGIME-001 built fairly (same universe, filters, exits, costs, entry dates/exposure, no lookahead or survivorship) and computed correctly?

Answer: pending.

Evidence: pending.

Confidence: low — run in progress.

## Q2: What is the corrected t-stat of each of the six books vs random, and does it agree with the percentiles?

Answer: pending.

Evidence: pending.

Confidence: low — run in progress.

## Q3: Does the harness pass sanity checks, with a bad entry losing to random and a lookahead entry crushing random?

Answer: pending.

Evidence: pending.

Confidence: low — run in progress.

## Q4: Why do random entries perform about as well as RSI(2) on DEV 2021-2024, and how much of the return comes from the exit rules versus the entry signal?

Answer: pending.

Evidence: pending.

Confidence: low — run in progress.

## Next steps

Pending.
