# BLEND-001 step 2 — MOM 12-1 + C4 blend on DEV (research findings)

Status: **DRAFT — full 1000-draw run in progress; numbers below are placeholders
until it finishes.** DEV window 2021-01..2024-11 (47 months); holdout
2025-01..2026-08 never loaded.

Bar definitions are frozen in `research/blend-001/PREREGISTRATION.md` §6(a)-(e) with
`research/blend-001/PREREGISTRATION_ERRATUM.md` applied (E1: C4 sleeve = 1 unit per
open position, cash elsewhere, **trailing expanding p95** denominator `D_i`; E2: bar
(e) stays 98.75; E3: research-only scope). A variant PASSES only if all of:
(a) n >= 24; (b) mean blend net > 0; (c) t(blend) >= 2.24; (d) mean(d_t) > 0 and
paired t(d_t) >= 2.24 where d_t = b_t/sigma_M - m_t; (e) percentile vs 1000 random
blends >= 98.75.

## Q1: Does blend B1 (MOM 50 / C4 50), as pre-registered, pass ALL pass bars (a)-(e)?

Answer: PENDING (full run in progress).

Evidence: pending.

Confidence: low — result not yet produced.

## Q2: Does blend B2 (MOM 70 / C4 30) pass ALL pass bars (a)-(e)?

Answer: PENDING (full run in progress).

Evidence: pending.

Confidence: low — result not yet produced.

## Q3: Per-variant mean net %/mo, t, Sharpe, max drawdown, worst 3 months, percentile vs random-entry blends, and paired improvement vs MOM alone (mean, t)?

Answer: PENDING.

Evidence: pending (will cite `results.json` fields and the script's printed lines).

Confidence: low — result not yet produced.

## Q4: Monthly correlation of the C4 series with MOM on DEV, and how many C4 trades/months does it contain?

Answer (partial, from the self-test path): C4 rebuild produced **7256 trades** over
**378 symbols**, aligned to the same **47 months** (2021-01..2024-11); the
monitor-path correlation printed by the same build was **+0.162** (full-run
correlation is the reported figure).

Evidence: `python3 research/blend-001/run_blend.py --self-test` output:
`swing: 378 symbols, 15060 signal days, 7256 trades, 47 months aligned -> PASS`.
Full-run line: `C4 rebuild: 7256 trades (monitor), mean 0.1067%/mo; corr with MOM (monitor) +0.162`.

Confidence: medium — trade count and months come from the passing self-test; the
correlation will be re-stated from the full-run artifacts.

## Self-test (prerequisite, passed)

`python3 research/blend-001/run_blend.py --self-test` (workdir = worktree):
- MOM mean +0.0170822994 (ref same, diff 0) PASS; maxDD -0.2774257907 PASS;
  Sharpe +0.8455531342 PASS; worst3 [-0.177358, -0.156747, -0.122600] PASS;
  47 months PASS.
- no-lookahead: 6 truncation re-runs identical; entry/exit and DEV asserts PASS.
- MOM t(excess) 2.2944 (ref 2.294) PASS.
- `== self-test PASS ==`

## Next steps

- (to be completed after the full run)
