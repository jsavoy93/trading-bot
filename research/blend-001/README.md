# BLEND-001 — MOM 12-1 + C4 swing blend (research script)

Step 1: prereg + script + smoke test only. This directory commits **no results**.
The design is frozen in `PREREGISTRATION.md` (committed before any swing/blend
number was computed).

## What it does
- MOM leg: the frozen engine `research/volsize-001/engine_fixed.py` (imported,
  unmodified), DEV months 2021-01 .. 2024-11.
- Swing leg: STRAT-022 **C4** rebuilt verbatim (RSI(2)<10, close>SMA200, next-open
  entry, SMA5 / 3xATR14 / 10-day exits, 10 bps per side) from the surviving
  STRAT-022 OHLCV copy at `/tmp/rebal_red/experiments/strat_022/data/`.
  The RSI(2) shadow runner (STRAT-023) has no 2021-2024 history and is excluded.
- Variants: B1 50/50, B2 70/30 (MOM share). Paired metric
  `d_t = blend_t / sigma_M - mom_t`, paired t; percentile vs 1000 matched
  random-entry swing draws.
- DEV only: every load drops bars after 2024-12-31 and asserts it.

## Run

    python3 research/blend-001/run_blend.py --self-test     # MOM 1.708%/mo + no-lookahead
    python3 research/blend-001/run_blend.py --smoke --n-random 5   # pipeline check, not results
    python3 research/blend-001/run_blend.py --full          # step 2 only (1000 draws)

Flags: `--panel PATH` (default `/tmp/panel_dev.parquet`), `--swing-data DIR`
(default `/tmp/rebal_red/experiments/strat_022/data`), `--n-random N`,
`--out-dir DIR`. `--full` writes `results.json` / `results.md` to `--out-dir`
(default: next to the script; pass `--out-dir /tmp/blend-001` in step 2 to
keep results out of the repo). C4 aggregation follows
`PREREGISTRATION_ERRATUM.md` E1 (1 unit per open position, cash elsewhere,
**trailing** expanding-p95 denominator).

Runtime: self-test ~90 s, smoke ~45 s, full run mostly memory-bound on the
panel load (~1 GB RSS).
