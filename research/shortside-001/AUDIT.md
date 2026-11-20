# SHORTSIDE-AUDIT-002 — why random shorts showed +2.1%/trade

## VERDICT
Not a formula bug and not a real edge. The +2.14% random mean in run.py is the
**gate-conditioned + hold-keyed sample**, plus an unadjusted-split data artifact.
Nobody can short "random gated name-days"; that pool is 0.3% of eligible name-days
and is a falling-day/momentum basket. The correct short benchmark in DEV is roughly flat.

## Root cause (file:line + mechanism)
1. **Biased sample (real cause), run.py L122-133.** pool_days = only gate days
   (`rsi>65 & sma10<sma30`), i.e. 2,985 name-days = **0.3%** of eligible name-days.
   Those are the exact "RSI hot + downtrend" names SHORTSIDE-001 targets, and the
   mean short return rises monotonically with hold inside that pool:
   h=0 −0.18%, h=5 +1.18%, h=10 **+3.13%** (46.7% of all draws). Weighted by the
   strategy's hold distribution, that is +2.14%. Trading rule (L92-98) uses the same
   gates per day, but the baseline picks one name and an unrelated random hold —
   mixing a 10-day basket return with a daily sign flip.
2. **Minor off-by-one, run.py L137-138:** `xi = min(t + 1 + h, T - 1)` and
   `e_px = opn[t+1]`. For h=0 the exit is t+1+h and float(live) key ok, but note that
   exit bar index counts **entry bar as bar 0** (`xi = e + (h-1)` → h=0 collapses to
   entry open). Rewriting with `xi = e + h - 1` = −0.46 pp (0.0214 → 0.0168).
   The strategy path (L77-97) is itself shifted by one (`entry_i = t+1`, `held = t−e_i`,
   exit at close[t] / open[t+1]) and is internally consistent — so the baseline is NOT
   wired to the strategy's clock. Small effect, but it makes the comparison in-run.py
   inconsistent by half the daily return.
3. **No forward-alignment bugs found.** Order is `e_px = opn[t+1]`, `x_px = close[t+1+h]`;
   no `shift(+h)` leak. Exit never precedes entry (0 of 221,300 baseline trades had
   xi ≤ tt+1 except h=0 vs entry t+1) and clipping at T-1 is 0.000 of trades.
4. **Costs/borrow sign: correct** — `0.0015×2 + 0.05·h/252`, subtracted; stop path
   (L75) uses `c >= e_p*1.08` → clips only losses; 551 stops × mean −11.2% net ≈ −8% + gap.

## Benchmarks, run.py formula, DEV 2021-01-01..2024-12-31
- (a) short equal-weight panel every 10-day window: **−0.52%** (median −0.64%, n=1004).
  Short SPY, same formula: **−0.97%**. Market rose; a flat/slightly wrong benchmark.
- (b) short **every eligible name** (ADV20≥1e6, px≥5) every 10-day window, correct 10 bars:
  **+0.50% mean, −0.53% median** (n=1,070,733). Note mean ≫ median: mean is driven by
  the tail. Mean excludes >40%-gain windows: **−0.54%**; 1% two-sided trim: **−0.18%**
  (5% trim: −0.49%).
- Conclusion: (b) is ~+0.5%, i.e. ~0.3 pp of the +2.1% is tail/distortion, and the
  rest (~1.6 pp) is the gate-conditioned pool + hold-key mix.

## Top-10 winners (correct 10-bar windows, DEV)
Ten of the top 12 are the same name, BHVN, 2022-09-19..09-30 → 08-10: panel shows
close **151.80 (2022-10-03) → 8.30 (2022-10-04)** overnight, i.e. an **unadjusted
1:20 reverse split**, not a return. Also ORGO 2019 (pre-DEV) and OCGN 2019 are
the same failure mode. 10,311 of 992,148 windows >40% short gain across 1,129 symbols;
**62% of them fall on dates that already hold ≥20 such names**, e.g. the Mar-2020
COVID crash window (600 names 2020-03-03) — i.e. genuine crash days, distinguished
by being clustered and by well-known names. In DEV itself, only 24 names show a
single-day move >90% (reverse-split artifacts: BHVN, 2 on 2022-01-31, etc.).
**The splits are the flagged artifacts; the crash-day clusters are real price action.**

## Impact on SHORTSIDE-001 REJECT
Unchanged. The real short rule's DEV result (2,213 trades, mean **+1.07%**, win rate
47.2%, drawdown −4.1%, worst −61%) still fails on 3/5 bars; the +2.14% random mean
was being used as the "no-edge / random" comparator and is overstated by ~1.6 pp
and by split artifacts. Replace the baseline with (b): −0.5% (mean) / −0.5% median —
the short rule's +1.07% is then *better* than random gate-less shorts, but the
strategy still fails, so **REJECT stands** (and is now slightly conservative, not
inflated).

## Contamination risk: LONGSHORT-001 / CRASHFILTER-001
Same panel (panel.parquet with split-only adjustment, panel_meta.json only says
"adjusted", no split-adjusted OHLC — verified BHVN). Any long-short difference or
crash filter using close-to-close ratios on this panel inherits the same artifacts.
Check their top winners for BHVN (2022-10), MARA, CVNA, NVAX, OCGN-style jumps.
No evidence found either way in this audit — flag as **medium risk, needs a
winsorisation/split-screen pass on both**.

## Reproduce
`/root/research/shortside-001/` scripts are ephemeral; the numbers above come from
one vectorized pass on panel.parquet (`opn[t+1] / close[t+h] − 1 − 0.0032`) over
i0=505..1509 (2021-01-02..2024-12-31) and one exact-key replay of run.py's rng
stream (reproduces 0.02144). Outputs folder: /tmp/ss2_*.py (not committed).
