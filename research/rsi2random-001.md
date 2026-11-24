# RSI2RANDOM-001 — does RSI(2) S1a beat a truly random, exposure-matched portfolio? (DEV)

Status: **DRAFT — full run in progress.** RESEARCH ONLY. All work under `research/`.
DEV window 2021-01-01..2024-12-31 only; the 2025-01..2026-08 holdout is never loaded
(`prepare()` truncates every series at 2024-12-31). Data: cached split-adjusted parquet bars
in `/tmp/rsi2random_tmp` (no re-download). Harness:
`research/rsi2random-001/run_rsi2random.py`; output `research/rsi2random-001/results_rsi2random.json`.

## Q1: Does the RSI(2) S1a swing strategy beat a truly random, exposure-matched portfolio on DEV 2021-2024, with filter on and off, at max_open 4 and 12?

Answer: pending (full run in progress).

Evidence: pending.

Confidence: low — run in progress.

## Q2: Does the harness pass the sanity checks, with RSI(2)>90 entries not beating random and a lookahead-cheat entry crushing random?

Answer: pending.

Evidence: pending.

Confidence: low — run in progress.

## Q3: How much of the strategy's return comes from the exit rules alone versus the RSI(2) entry signal?

Answer: pending.

Evidence: pending.

Confidence: low — run in progress.

## Next steps

- Finish the run and fill in Q1-Q3 with the numbers from `results_rsi2random.json`.
