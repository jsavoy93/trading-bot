# BLEND-001 step 2 — MOM 12-1 + C4 blend on DEV (research findings)

**Verdict: neither variant passes.** B1 (50/50) and B2 (70/30) both fail pass bars
(c), (d) and (e) of the frozen preregistration. The blend is *directionally* good
(higher Sharpe than MOM alone, far smaller drawdown) but it is not statistically
distinguishable from MOM alone, and it is only at the ~95.7th percentile of
random-entry blends — below the frozen 98.75 bar.

- Window: DEV 2021-01..2024-11 (47 months). Holdout 2025-01..2026-08 never loaded
  (asserted in the loader and re-checked in the self-test).
- Bars are frozen in `research/blend-001/PREREGISTRATION.md` §6(a)-(e) with
  `PREREGISTRATION_ERRATUM.md` applied (E1: C4 sleeve = 1 unit per open position,
  cash elsewhere, **trailing expanding p95** denominator `D_i`; E2: bar (e) stays
  98.75; E3: research-only scope). No tuning was done after seeing results.
- Run: `python3 research/blend-001/run_blend.py --full` (1000 draws, 4171.4 s).
  Artifacts: `results.json` / `results.md` (kept outside the repo at
  `/tmp/blend001-out/`). Series hash `sha256(mom_net||c4_net) =
  ac201256589e4a3671e18b250643381b99ad598fa9fba7fbfc3e37a7b41a22a5`.

Bar definitions (a) n>=24; (b) mean blend net > 0; (c) t(blend) >= 2.24;
(d) mean(d_t) > 0 and paired t(d_t) >= 2.24, `d_t = b_t/sigma_M - m_t`,
`sigma_M = 6.9984%`; (e) percentile vs 1000 random blends >= 98.75.

## Q1: Does blend B1 (MOM 50 / C4 50), as pre-registered, pass ALL pass bars (a)-(e)?

**Answer: NO.** B1 fails (c), (d) and (e). It passes only (a) and (b).

Evidence — `run_blend.py --full` line and `results.json` → `variants.B1`:
| bar | value | threshold | result |
|---|---|---|---|
| (a) n | 47 | >= 24 | PASS |
| (b) mean net | +0.9075% /mo | > 0 | PASS |
| (c) t(blend) | **+1.686** | >= 2.24 | **FAIL** |
| (d) mean(d_t) | +11.259% ; **paired t +1.683** | mean>0 AND t>=2.24 | **FAIL** (t) |
| (e) percentile | **95.7** | >= 98.75 | **FAIL** |

Printed line: `B1 w=0.50: mean +0.9075%/mo t +1.686 paired_mean +11.2586% paired_t +1.683 pct 95.7 PASS=False`.
(The paired mean is large only because `d_t` puts the blend on MOM's 7.0% monthly
volatility scale; the paired t is the statistic that matters and it is < 2.24.)

Confidence: **high** — single deterministic full run, frozen bars, numbers read
directly from `results.json`.

## Q2: Does blend B2 (MOM 70 / C4 30) pass ALL pass bars (a)-(e)?

**Answer: NO.** Same three failures as B1.

Evidence — `variants.B2`:
| bar | value | threshold | result |
|---|---|---|---|
| (a) n | 47 | >= 24 | PASS |
| (b) mean net | +1.2278% /mo | > 0 | PASS |
| (c) t(blend) | **+1.687** | >= 2.24 | **FAIL** |
| (d) mean(d_t) | +15.835% ; **paired t +1.688** | mean>0 AND t>=2.24 | **FAIL** (t) |
| (e) percentile | **95.7** | >= 98.75 | **FAIL** |

Printed line: `B2 w=0.70: mean +1.2278%/mo t +1.687 paired_mean +15.8355% paired_t +1.688 pct 95.7 PASS=False`.

Confidence: **high** — as Q1.

## Q3: Per-variant mean net %/mo, t, Sharpe, max drawdown, worst 3 months, percentile vs random-entry blends, and paired improvement vs MOM alone (mean, t)?

**Answer:** table below (all values exactly as `results.json` prints them).

| series | w_MOM | mean/mo | ann. | t | Sharpe | maxDD | worst 3 months | pct vs random | paired mean(d_t) | paired t |
|---|---|---|---|---|---|---|---|---|---|---|
| **MOM alone** (baseline) | 1.00 | +1.7082% | +22.54% | — | 0.846 | -27.74% | -17.74%, -15.67%, -12.26% | — | — | — |
| **C4 alone** | 0.00 | +0.1067% | +1.29% | — | 0.252 | -7.65% | -5.22%, -2.96%, -1.97% | — | — | — |
| **B1** (50/50) | 0.50 | +0.9075% | +11.45% | +1.686 | 0.852 | **-14.94%** | -8.74%, -8.28%, -7.84% | 95.7 | +11.259% | +1.683 |
| **B2** (70/30) | 0.70 | +1.2278% | +15.77% | +1.687 | 0.853 | -20.22% | -12.06%, -10.97%, -10.15% | 95.7 | +15.835% | +1.688 |

Supplementary (reported, not a bar): both blends have a marginally **higher Sharpe
than MOM alone** (0.852 / 0.853 vs 0.846) and materially **lower max drawdown**
(B1 -14.9%, B2 -20.2% vs -27.7%) and smaller worst-3 months. The safety benefit
is real; the return/significance case is not.

Note on t and pct: B1 and B2 have almost identical t (≈1.686/1.687) and identical
percentile 95.7 — expected, since they are linear rescalings of the same two
component series and the paired statistic divides by MOM's fixed sigma.

Evidence: `/tmp/blend001-out/results.json` (`mom`, `c4`, `variants.B1`,
`variants.B2`), `/tmp/blend001-out/results.md`, and the two printed `B1`/`B2`
lines quoted in Q1/Q2.

Confidence: **high** — deterministic full run, `--self-test` passed first
(MOM mean/maxDD/Sharpe/worst3 reproduced to 0 tolerance; 6 no-lookahead
truncation re-runs identical).

## Q4: Monthly correlation of the C4 series with MOM on DEV, and how many C4 trades/months does it contain?

**Answer:** correlation of the C4 monthly net series with MOM over the 47 DEV
months is **+0.1616** (`+0.162`). The C4 rebuild contains **7256 trades** over
**378 symbols**, aligned to **47 months**; **45 of the 47 months have at least one
trade** (only 2021-01 and 2021-02 are empty — the SMA200/ATR14 warm-up plus the
first month of the exit-in-month booking rule). Trades are booked in the month of
their exit date; the latest exit is 2024-12-31 (a 2024-11 grid session).

The near-zero correlation (-0 vs MOM's large swings — actual +0.16, small) is the
main *reason for studying the blend*; it is small enough that the sleeve does add
some diversification, but not enough to clear the significance bars with only 47
months.

Evidence: `results.json` → `corr_c4_mom = 0.16156343562986886`; full-run print
`C4 rebuild: 7256 trades (monitor), mean 0.1067%/mo; corr with MOM (monitor) +0.162`;
self-test line `swing: 378 symbols, 15060 signal days, 7256 trades, 47 months aligned -> PASS`.
Month-with-trades count (45/47) computed with the same `build_swing` +
prereg §5/erratum booking rule; empty months `['2021-01', '2021-02']`.

Confidence: **high** for correlation/trades/months (straight from the run);
**medium** for the 45/47 split (computed in a small auxiliary script using the
same pipeline, not emitted by the frozen runner).

## Self-test (prerequisite, passed)

`python3 research/blend-001/run_blend.py --self-test` (workdir = worktree):
MOM mean +0.0170822994, maxDD -0.2774257907, Sharpe +0.8455531342, worst3
[-0.177358, -0.156747, -0.122600] — all diff 0; 47 months; 6 no-lookahead
truncation re-runs identical; MOM t(excess) 2.2944 (ref 2.294) → `== self-test PASS ==`.

## Next steps

1. **Do not rescue this study by re-tuning.** The 98.75 bar and t>=2.24 are frozen;
   B1/B2 FAIL on DEV is the answer.
2. If a stronger case is wanted, the honest path is, in priority order:
   (i) a **pre-registered** test of a *third* C4-class sleeve with low correlation
   AND positive standalone Sharpe > ~0.5 — C4 alone at Sharpe 0.25 and +0.107%/mo
   is too weak to move a t-stat with 47 months; (ii) consider 70/30 only as a
   **risk-control overlay** (drawdown -27.7% → -20.2% is a real, large improvement)
   rather than a return improvement, and pre-register that framing before testing;
   (iii) if the risk-control framing is adopted, the pass bars should be re-frozen
   against MOM-alone's own statistics — not the return t — as a *separate*
   preregistration job.
3. **Code/report fact:** `run_blend.py --full` does not emit a months-with-trades
   count or a per-series annualized return in `results.md`; both had to be
   derived. A small coding job could add `n_active_months` and `ann_net` to
   `summary()` — *not done here* (research-only PR, no code changes).
4. Runtime note: the 1000-draw loop takes ~70 min on this 1-vCPU box; future runs
   should be launched detached (systemd transient unit / nohup+setsid) with a
   single long wait, or the draw loop should be vectorized further in a coding job.
