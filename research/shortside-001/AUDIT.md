# SHORTSIDE-AUDIT-002 — why random shorts showed +2.1%/trade
## VERDICT
Not a formula bug and not a real edge. The +2.14% random mean in run.py is a
**gate-conditioned, hold-keyed sample** plus an unadjusted-split data artifact.
Nobody can short "random gated name-days": that pool is 0.3% of eligible
name-days, a falling-day/momentum basket; the correct DEV short benchmark is ~flat.
## Root cause (file:line + mechanism)
1. **Biased sample (real cause), run.py L122-133.** The pool is only gate days
   (`rsi>65 & sma10<sma30`): 2,985 name-days = **0.3%** of eligible name-days —
   the exact "RSI hot + downtrend" names SHORTSIDE-001 targets. Inside it the mean
   short return rises with hold: h=0 −0.18%, h=5 +1.18%, h=10 **+3.13%** (46.7%
   of draws). Weighted by the strategy's hold distribution that is +2.14%; the
   strategy (L92-98) gates per day but trades one name, so the baseline mixes a
   10-day basket return with a daily sign flip.
2. **Minor off-by-one, run.py L137-138:** `xi = min(t + 1 + h, T - 1)`,
   `e_px = opn[t+1]`. Fixing it to `xi = e + h - 1` gives −0.46 pp (0.0214 →
   0.0168). The strategy path (L77-97) is shifted similarly but internally
   consistent, so the baseline is off the strategy's clock. Small effect.
3. **No forward-alignment leak.** Exit is `close[t+1+h]`; no `shift(+h)` lookahead,
   and no trade exited before entry (bar the h=0 case); T-1 clipping: 0.000 of trades.
4. **Costs/borrow sign: correct.** `0.0015×2 + 0.05·h/252` is subtracted; the stop
   (L75, `c >= e_p*1.08`) clips only losses (551 stops, mean −11.2% net).
## Benchmarks (DEV 2021-01-01..2024-12-31)
- (a) Short equal-weight panel, every 10-day window: **−0.52%** (median −0.64%,
  n=1,004). Short SPY, same formula: **−0.97%**.
- (b) Short **every eligible name** (ADV20≥1e6, px≥5), every 10-day window,
  correct 10 bars: **+0.50% mean, −0.53% median** (n=1,070,733). The gap is tail
  driven: excluding >40%-gain windows gives **−0.54%**; 1% two-sided trim
  **−0.18%**, 5% trim −0.49%.
- So ~0.3 pp of the +2.1% is tail/distortion; ~1.6 pp is the gate pool + hold mix.
## Top-winner artifacts
**DEV results:** in DEV only 24 names show a single-day move >90% — reverse-split
artifacts (BHVN, 2 on 2022-01-31, etc.); ten of the top 12 shorts are BHVN
2022-09-19..09-30, whose panel jumps close 151.80 (2022-10-03) → 8.30 (2022-10-04)
overnight, an unadjusted 1:20 reverse split, not a return.
**Whole-panel artifact-scan context (outside DEV):** ORGO/OCGN 2019 and the
Mar-2020 COVID cluster (600 names on 2020-03-03) show the same failure mode;
10,311 of 992,148 windows >40% short gain across 1,129 symbols, 62% on dates that
already hold ≥20 such names. Crash-day clusters are real price action; splits are
the flagged artifacts.
## Impact on SHORTSIDE-001 REJECT
Unchanged. The real short rule's DEV result (2,213 trades, mean **+1.07%**, win
rate 47.2%, drawdown −4.1%, worst −61%) still fails 3/5 bars. The +2.14% random
mean was the "no-edge" comparator and is overstated by ~1.6 pp and by splits.
Use (b) instead: mean −0.5% / median −0.5%. The rule's +1.07% then beats random
gate-less shorts, but it still fails, so **REJECT stands** (now slightly
conservative rather than inflated).
## Contamination risk: LONGSHORT-001 / CRASHFILTER-001
Same panel (`panel.parquet`, split-only adjustment; `panel_meta.json` says only
"adjusted" — verified on BHVN). Any long-short difference or crash filter using
close-to-close ratios inherits these artifacts. Check both for BHVN (2022-10),
MARA, CVNA, NVAX, OCGN-style jumps. No evidence either way here: **medium risk,
needs a winsorisation/split-screen pass on both**.
## Reproduce
One vectorized pass on `panel.parquet` (`opn[t+1] / close[t+h] − 1 − 0.0032`) over
i0=505..1509 (2021-01-02..2024-12-31) plus one exact-key replay of run.py's rng
stream (reproduces 0.02144). All benchmark windows end within 2021-2024 and were
clipped at 2024-12-31, so no price on/after 2025-01-01 was read. Scripts are
ephemeral (/tmp/ss2_*.py, not committed).
