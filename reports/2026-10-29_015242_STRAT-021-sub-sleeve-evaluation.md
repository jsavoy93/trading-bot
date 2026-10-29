# STRAT-021 — Long-Term Sub-Sleeve Standalone Evaluation (MOM / LVOL / GP)

Owner directive 2026-10-29 01:39 UTC. PAPER ONLY; research + docs only; no
production code; STRAT-020 checkout untouched; no BUY-threshold changes;
STRAT-016/019 combinations NOT resurrected.

## 1. Freeze discipline (BEFORE any return computation)

Pre-registrations written, SHA-frozen, committed and pushed (b81f4ae)
before the evaluation ran:
- A: STRAT-013-R2 momentum standalone rule (closes "selection rule NOT
  documented" gap). Q5 top momentum, equal weight, annual June formation,
  relative liquidity floor, full-universe breakpoints.
- B: STRAT-015-R2 low-vol standalone rule (closes same gap for 015). Q1
  lowest 12-mo avg realized vol, n_valid>=9, same cadence/floor.
- C: Evaluation protocol — 20 bps/side cost on formation turnover,
  bootstrap CIs (10k block resamples, block 6, seed 21029), frozen
  pass/fail bar from docs/TWO_SLEEVE_PORTFOLIO_DESIGN.md §11.

Both STRAT-013 and STRAT-015 previously had NO frozen standalone
methodology (STRAT-020 Phase 1: NOT_READY, CLASS 5 blocker) — this study
creates new frozen rules; it does not claim to reconstruct original
selections.

## 2. Results (108 months, 2017-07 → 2026-06; net of costs; SPY gross)

| Sleeve | Ann net | Ann vol | Sharpe | MaxDD | Mo. spread vs SPY (95% CI) | Turnover | Members/yr |
|---|---|---|---|---|---|---|---|
| MOM Q5 | 9.07% | 19.86% | 0.46 | -32.9% | -0.39% (-0.86,+0.07) | 0.787 | 826-1587 |
| LVOL Q1 | 5.65% | 9.58% | 0.59 | -17.7% | -0.79% (-1.09,-0.48) | 0.330 | 852-1642 |
| GP Q5 | 16.16% | 22.52% | 0.72 | -30.7% | +0.18% (-0.62,+1.15) | 0.271 | 104-135 |
| SPY | 15.07% | 16.08% | 0.94 | -23.9% | — | — | — |

Cumulative net: MOM +118.4%, LVOL +64.0%, GP +285.2%, SPY +253.8%.

Pairwise monthly correlations: MOM-LVOL .91, MOM-GP .82, LVOL-GP .85
(all > 0.5 diversification recommendation; overlapping Q5 memberships).

## 3. Verdicts (frozen bar, PREREG C §5)

- MOM: FAIL (all 3 criteria; worst drawdown, highest turnover, negative spread).
- LVOL: FAIL (2 of 3; passes only drawdown discipline).
- GP: FAIL (fails CI-not-worse-than-SPY by margin -0.62% < -0.10%; maxDD
  30.7% vs 29.9% cap by 0.8pp). Positive point spread (+0.18%/mo) but CI
  includes materially-worse outcomes. GP remains the strongest candidate;
  the FAIL is statistical-confidence, not sign, unlike MOM/LVOL.

## 4. Implementation notes / anomalies

1. GP_A in cohort files is continuous GP/Assets, not a quintile label;
   quintiles recomputed XNYS-only valid-GP per frozen STRAT-017 rule.
2. SPY from raw `close` (daily parquet lacks adjusted close) — dividends
   not reinvested; understates SPY, biasing spreads PRO-sleeve. Despite
   that handicap-removal, MOM and LVOL still failed on point estimates.
3. Liquidity floor: strict > June(Y) 10th-pctile of median daily dollar
   volume within factor-eligible set; deterministic qcut ties.
4. keep_default_na=False (literal "NA" ticker protection) per repo CSV contract.

## 5. Recommendation to Josh

- Do NOT add MOM or LVOL as sub-sleeves: negative point spreads, failed
  the pre-registered bar, and 0.82-0.91 correlations add no
  diversification.
- GP: keep as the SINGLE long-term sleeve (Sleeve L) exactly as in
  docs/TWO_SLEEVE_PORTFOLIO_DESIGN.md. Its net point spread vs SPY is
  positive (+0.18%/mo, cumulative +285% vs +254%) but fails the
  confidence bar; carrying three overlapping sleeves would multiply
  costs and correlation without evidence.
- The 0.5 diversification threshold was not met by ANY pair — decisive
  against the "separate sub-sleeves" structure; confirms single-L design.
- No production code changed. Nothing merged or deployed.

## 6. Artifacts

- experiments/strat_021/_PREREG_{A,B,C}*.md(.sha256) — frozen pre-regs
- experiments/strat_021/_strat_021_evaluation.py — seeded script
- experiments/strat_021/_strat_021_results.json — full metrics + member lists
- experiments/strat_021/_strat_021_monthly_returns.csv — 108 months

Survivorship caveat stands in every output (owner qualification
2026-10-12: selection bias NOT ruled out). Equal-weight annual-rebalance
research constructs; execution feasibility at paper sizes not proven.
