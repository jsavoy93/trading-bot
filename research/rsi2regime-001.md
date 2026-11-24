# RSI2REGIME-001 — RSI(2) swing: SPY>SMA200 regime filter ON (V0) vs OFF (V1) + max_open caps (DEV research)

Status: **PRE-REGISTRATION FROZEN before any result is computed.**
RESEARCH ONLY. No code, tests, config, MAP.md or bot changes. DEV window only.

## 0. Scope and non-goals
- Questions (answered in the `## Q1..Q4` sections below):
  Q1 V0 (SPY > SMA200 regime filter ON) vs V1 (filter OFF), trade-level vs random and
  portfolio-level, on clean split-adjusted DEV data 2021-01-01..2024-12-31.
  Q2 the V1-only extra trades (signals on days SPY_close <= SPY_SMA200): edge or crash
  drawdown, esp. in the 2022 bear market.
  Q3 portfolio position caps max_open = 4 / 8 / 12 for V0 and V1 (return, Sharpe, maxDD,
  trade count).
  Q4 unadjusted splits in the STRAT-025/026 CSVs (NVDA/AAPL/AMZN/TSLA/GOOGL), how the
  engine handled them, and whether V0/V1 conclusions hold on the full liquid universe
  AND on the live POPULAR_LIQUID_US universe.
- Not a retest of: STRAT-026 (published +0.258%/trade), STRAT-025 setup research, the
  RSI(2)-breadth test (RESEARCH_LOG "RSI(2) breadth", FAIL), the live paper runner itself.
- Not evaluated: anything after 2024-12-31. The 2025-01..2026-08 holdout is BURNED.

## 1. Data (frozen)
- Primary: Yahoo Finance daily **split-adjusted** OHLCV via `yfinance`
  (`auto_adjust=False`, `actions=True`, prices multiplied by the cumulative split factor
  from `Ticker.splits`; OHLC only — volume is NOT adjusted the same way, so volume is
  left as reported, matching what the live Alpaca feed (`adjustment="split"`) reports).
  Cache in `/tmp/rsi2regime_tmp`; deleted before finishing.
  Load window 2020-01-01..2024-12-31: 2020 bars are used ONLY as warmup for SMA200 /
  ATR14 / vol20 (>=220 bars before 2021-01-01); no 2020 trade is counted.
  If Yahoo is unavailable, fallback is the Alpaca free PAPER data API with
  `adjustment="split"` using the existing paper credentials (never printed/committed),
  same window.
- Audit-only: `/root/.openclaw/workspace/strat-025/experiments/strat_025/setup_research/data/`
  (672 raw CSVs) — inspected read-only to answer Q4.
- Symbols: SPY for regime + two stock universes:
  (a) **FULL**: union of the 672 STRAT-025/026 CSV symbols (liquid US stocks, each has
      its own dev history) — every name with >=220 bars before 2021-01-01 survives the
      warmup; a name is skipped once its data ends (delisting) and enters when it starts.
  (b) **LIVE**: the exact `POPULAR_LIQUID_US_DEDUPED` list from
      `/root/.openclaw/workspace/trading-bot/experiments/strat_006r_candidate_universe.py`
      (read-only), same treatment for missing/partial histories.
- Cleanliness check (must pass before primary numbers): no daily close-to-close move
  outside [-50%, +100%] that is not backed by a split record; a table of all such moves
  is printed. Any surviving >50% drop is investigated and documented.

## 2. Signal and exit rules (frozen, replicated from the live runner)
Source of truth: `/root/deploy/rsi2-swing/experiments/swing_paper/signals.py` (indicators,
stops) and `runner.py` + `rules.py` (entries, cap, exits).
- **S1a entry, evaluated on the close of day t** (signal; fill at the OPEN of t+1):
  Wilder RSI(2) (period 2, exactly the live `wilder_rsi`: rolling 2-mean seed then Wilder
  recursion, zero-loss -> NaN) **< 10**, vol20 (20-day mean of reported volume) >=
  1,000,000, close >= $5.00, symbol not already held, and >= 220 bars of history
  (live `WARMUP_BARS`). **V0** additionally requires SPY_close > SPY_SMA200 (200-day
  simple mean, taken on the latest SPY bar at or before t). **V1** drops that condition.
  ATR14 = 14-day simple mean of true range (live formula: max(H-L, |H-Cprev|, |L-Cprev|)).
- **Exits**, close-checked every day after entry, exit price = that day's close
  (matching the live runner, which acts after the daily bar is final):
  initial stop = entry_fill - 2.0*ATR14(t), checked from the **entry day's** close
  onward (live `process_exits` runs on the entry day only after the fill is confirmed;
  the entry-day close is therefore checked -- documented in the findings);
  chandelier stop = max(close since entry) - 3.0*ATR14(current day), never lowered,
  evaluated on each subsequent day's close; **time exit = 15 trading days after the
  entry bar** (live `trading_days_since` counts completed bars strictly after the entry
  bar, exit on the 15th such bar, filled at that day's close as the live market sell).
  Fill price for days when the open gapped below the stop is **documented as the live
  convention** (stop is a GTC stop order, so a gap-down fills at/below the stop, not at
  the close); the primary replication uses **close** fills on every exit and a variant
  with gap-aware stop fills (open when open <= stop) is reported as a robustness note.
- **NO +2R partial** (live `swing-rsi2-paper` removed it, S1 2026-11-03). One extra
  replication row WITH a 50% partial at +2R is computed **only** to cross-check the
  published STRAT-026 DEV number (+0.258%/trade, t_vs_base 2.74).
- Costs: **10 bps per side, 20 bps round trip** on the full notional (STRAT-026
  convention, `COST = 0.0010`).

## 3. Trade-level protocol (frozen)
- Per symbol, enumerate hypothetical trades from every signal day t (no anti-overlap),
  then apply anti-overlap deterministically: iterate signal days in order, keep a trade
  only if its entry index > previous kept trade's exit index (STRAT-026 `anti_overlap`).
- Random-entry baseline: for each symbol draw `k_sym` random eligible days (eligible =
  same liquidity/price/warmup filter on that day; for V0 the eligible set is further
  restricted to SPY > SMA200 days) **without replacement**, above same anti-overlap
  pass, 1,000 draws. Report mean_excess (strategy mean net - mean of per-draw means),
  its t-stat (mean_excess / (sd of per-draw means / sqrt(1000)) where sd is the spread
  of draw means), and the percentile of the strategy mean vs the 1,000 draw means.
  Primary V0 comparison uses V0-eligible days; V1 uses all eligible days; both are also
  cross-checked against the all-eligible baseline (reported, not primary).
- Direct filter difference: paired by symbol-year, V1 - V0 and V0 - V1 with a t-stat and
  a **paired bootstrap p-value** (10,000 resamples of the per-trade or per-month paired
  differences, two-sided; seed 20261123).
- Diagnostics: count, trades/yr, mean net %, median %, win rate, profit factor,
  avg hold days, worst 5 trades; year-by-year 2021/2022/2023/2024 (count, mean net,
  win rate, sum and compounded return). V1-only-bear = trades whose signal day has
  SPY_close <= SPY_SMA200.

## 4. Portfolio protocol (frozen)
- Capital 100 (=100%), 48 monthly blocks; daily loop:
  - One shared book per variant: each open position gets 1/max_open of total equity at
    entry (equal-weight slots; `per_position_cap` 25% of the sleeve is equivalent when
    the sleeve is the whole book at max_open=4; with 8/12 slots the slot weight is
    1/max_open = 12.5%/8.33%, so the sleeve IS stretched to the whole book in every
    case and the comparison isolates the number of slots).
  - Entries fill at next open with 10 bps cost; exits fill at the exit day's close with
    10 bps cost; cash earns 0%.
  - Candidate selection on each signal day: among symbols not held, apply the **exact
    live ranking** `apply_rsi2_daily_cap` = sort by (RSI(2) ascending, symbol ascending),
    take at most `min(4, max_open - open_positions)` (live `rsi2_cap` default 4).
  - `max_open` cap enforced; no leverage; long-only.
- Metrics: mean monthly net %, annualized CAGR %, annualized Sharpe (monthly returns,
  x sqrt(12), rf=0), max drawdown % on the daily equity curve, worst 3 months with
  dates, total trades, avg open positions, % of days invested, year-by-year returns,
  monthly excess vs an equal-weight random-entry portfolio (same slot rules and same
  number of filled slots per day drawn at random from that day's eligible names, 1,000
  draws) and the direct V1 - V0 monthly difference (mean, t-stat, bootstrap p).

## 5. Pass/fail bars (frozen, standing DEV bars)
- DEV bars: >= 24 months, mean net > 0, mean excess > 0 vs random, percentile >= 97.5,
  t on excess >= 2.24.
- Changing away from the current live configuration (V0, max_open=4) additionally
  requires the **filter-difference bar**: the improvement over the current config has
  mean > 0 with t >= 2.24 (trade-level V1 vs V0 AND monthly portfolio difference).
- Verdict labels per question: PASS / FAIL / WEAK (bar met on return but not on t or
  percentile) with the exact numbers.
- Primary-source rule: if the clean-data replication of STRAT-026 (+0.258%/trade with
  the +2R partial) cannot be reproduced within a reasonable tolerance, the data/engine
  replication is declared suspect and the findings say so explicitly instead of
  inventing a different number.

## 6. Analysis code
- Reproducible script: `research/rsi2regime-001/run_rsi2regime.py` (data fetch, audit,
  trade-level, portfolio, bootstrap), committed with its output log.

---

# RESULTS (appended after the frozen pre-registration; the pre-registration above is unchanged)

Run: `python3 research/rsi2regime-001/run_rsi2regime.py --universe live`
(universe LIVE = `POPULAR_LIQUID_US_DEDUPED`, 353 symbols with usable DEV history after
Yahoo delisted/missing 27 names; SPY 1993-01-29..2024-12-31). Raw output:
`research/rsi2regime-001/results_live.json`, log in the run output. DEV window
2021-01-01..2024-12-31 only; 2025+ never loaded (SPY and every symbol truncated at
2024-12-31 in `prepare()`).

**Fix applied before results (the unit-mismatch bug the review flagged).** Two separate
defects produced the 48,491-vs-9,716 mismatch:
1. `build_signal_sets()` gated `elig` on warmup only, not on the DEV window
   (script `research/rsi2regime-001/run_rsi2regime.py`, `build_signal_sets`), so pre-2021
   signals were counted in the trade tables. Now `elig` includes
   `(df.index >= DEV_START) & (df.index <= DEV_END)`; `prereq_strat026()` and
   `basket_level()` assert every kept entry date is inside DEV.
2. The year table counted **per-symbol anti-overlap threads**, while a "trade" in the
   write-up was the 48k thread count — two different units. The primary unit is now the
   **per-day basket** (one trade per signal day, equal weight over that day's fills =
   STRAT-026 convention), and `year_table()` is computed from the same basket list with
   `assert sum(year n) == trade-level n` (field `years_sum_matches_n: true`, logged per
   variant). The old compounding column (`comp`) is removed — compounding overlapping
   basket returns is meaningless; yearly figures are mean per trade and a simple sum.
   Per-symbol-thread stats survive only as the clearly labelled `diag_*` diagnostics.

## Q1 — V0 (SPY>SMA200 ON) vs V1 (OFF), trade-level and portfolio

**Answer:** V1 (filter OFF) is very slightly better than V0 at the trade level and
somewhat better at the portfolio level, but **neither difference reaches the
pre-declared bar** (mean > 0 with t >= 2.24 on the filter difference). Trade-level
per-day-basket means: V0 **+0.306 %/trade** (n=735) vs V1 **+0.359 %/trade** (n=947);
the direct paired test on the same entry days is **−0.028 %/trade, t = −0.99,
p_boot = 0.325** (V1 slightly *worse* on shared days — the V1 advantage comes from the
extra bear-market days, not from better execution on common days). Portfolio: V1 beats
V0 at every cap, best case V1 max_open=12 (+0.895 %/month, Sharpe 0.57, maxDD −17.4 %)
vs V0 max_open=12 (+0.243 %/month, Sharpe 0.19, maxDD −30.2 %), with monthly
V1−V0 t = 1.21 (p_boot = 0.21). So the direction favours removing the filter, but it is
**not significant** at the standing DEV bar.

**Evidence:**
- `run_rsi2regime.py::basket_level()` (per-day basket, STRAT-026 unit); output
  `results_live.json:basket_LIVE_V0/V1`; log lines `[LIVE] V0: n_basket=735
  mean=0.306%/trade excess=0.231% t_vs_base=31.34 pct=83.5` and `[LIVE] V1: n_basket=947
  mean=0.359%/trade excess=0.060% t_vs_base=10.70 pct=63.2`.
- Paired filter difference on shared entry days: `basket_paired_LIVE` = `{days: 731,
  diff: -0.000276, t: -0.991, p_boot: 0.3252}`.
- Portfolio: `results_live.json:portfolio_LIVE` (V0/V1 × max_open 4/8/12), V1−V0 monthly
  diff `v1_minus_v0_LIVE`: t = 1.02 (mo4), 1.21 (mo8), 1.21 (mo12), all p >= 0.20.
- Note: the per-symbol-thread diagnostic (superficially larger samples) points the other
  way — `paired_LIVE` symbol-year cells V1−V0 = +0.777 %/trade, t = 7.95 — but that unit
  double-counts overlapping names and is **not** the traded unit; it must not be read as
  a V1 edge. It is listed in `diag_years_persym_*` / `trade_LIVE_*` for transparency only.

**Confidence: medium** — the sign is consistent across trade level and all three
portfolio caps, but the effect size (≈ +0.05 %/trade, ≈ +0.65 %/month) never clears
t = 2.24 and the paired shared-day test is flat.

## Q2 — the V1-only extra trades (SPY <= SMA200), and 2022

**Answer:** The extra bear-regime trades are **marginally positive on average and they
cut, not add, the crash drawdown** at the basket level. V1 takes 212 extra basket days
(947 vs 735), almost all in 2022 (226 vs 37 basket days that year). Year-by-year basket
means: 2022 V0 **−3.731 %/trade** (n=37) vs V1 **−0.280 %/trade** (n=226). The V0 2022
figure is an artifact of the small sample (the filter admits only a handful of 2022 days,
which happened to cluster on the worst ones); V1's 226-day 2022 basket mean of −0.28 % is
still negative, but it is far less bad than sitting out in cash and then re-entering late.
At the portfolio level the effect is clearer: 2022 yearly return V0 mo4 −29.1 %,
V0 mo12 −20.6 %, against V1 mo4 −34.6 % (fewer slots filled, more concentrated) but
V1 mo8 −13.2 % and V1 mo12 **−5.7 %**, and maxDD −17.4 % (V1 mo12) vs −30.2 % (V0 mo12).
The higher-cap V1 book is therefore the one that carries the bear market **best**, which is
the opposite of the "filter cuts crash drawdown" intuition.

**Evidence:**
- Basket year table: `results_live.json:basket_years_LIVE_V0/V1` — 2022 n=37 mean
  −3.731 % (V0) vs n=226 mean −0.280 % (V1); counts 242+37+211+245 = 735 and
  242+226+234+245 = 947 (asserted equal to `n`).
- Portfolio yearly returns: `results_live.json:portfolio_LIVE["V0_12"].year_returns`
  `{2021: 0.340, 2022: -0.206, 2023: 0.042, 2024: -0.029}` vs `V1_12`
  `{2021: 0.340, 2022: -0.057, 2023: 0.091, 2024: 0.043}`; maxDD V0_12 −30.2 %,
  V1_12 −17.4 %.
- Per-symbol diagnostic split by regime (`bear_LIVE`): V1-bear n=3912 mean +0.533 %,
  V1-bull n=9549 mean +0.545 %, 2022-bear n=3378 mean +0.246 % (the big positive tail in
  bear months: best trades +113 %, +86 % — short-squeeze-style reversals).

**Confidence: medium** — the 2022 portfolio comparison is consistent across caps, but
it rests on a single bear year and the basket means themselves are not significantly
different from zero in 2022.

## Q3 — max_open 4 vs 8 vs 12 (equal-weight slots, 10 bps/side)

**Answer:** For **V1**, more slots is strictly better: mean monthly return
0.571 % → 0.935 % → 0.895 %, CAGR 4.3 % → 9.3 % → 9.4 %, Sharpe 0.29 → 0.52 → **0.57**,
maxDD −47.5 % → −22.2 % → **−17.4 %**, trades 300 → 605 → 901. For **V0** the cap matters
much less: −0.073 % / +0.269 % / +0.243 % per month, CAGR −2.7 % / +1.6 % / +1.7 %,
maxDD −45.6 % / −37.4 % / −30.2 %; a 4-slot V0 book is essentially uninvestable (it fills
only 2.9 slots on average because the regime filter starves it). The best configuration
by Sharpe and drawdown is **V1 with max_open = 12**; 8 vs 12 is a near-tie on CAGR
(9.32 % vs 9.38 %) with 12 clearly better on drawdown. Against the matched random-entry
portfolio (same slot rules, same names-per-day drawn at random, 100 draws) **none of the
six configurations beats random**: V1 mo12 vs random +0.9484 %/month (t = 0.18),
V1 mo8 vs random +0.9317 % (t = 0.05), V0/V1 mo4 and V0 mo8/12 all negative versus random.
So the "better" caps improve the *book*, but the S1a entry itself adds nothing over a
random pick among the same eligible names.

**Evidence:** `results_live.json:portfolio_LIVE` —
`V1_12` `{mean_monthly: 0.008948, cagr: 0.093784, sharpe: 0.5673, maxdd: -0.1735,
total_trades: 901, year_returns: {2021: 0.340, 2022: -0.057, 2023: 0.091, 2024: 0.043}}`;
`V1_8` `{0.009349, 0.093230, 0.5172, -0.2220, 605}`; `V1_4` `{0.005706, 0.043424,
0.2949, -0.4745, 300}`; `V0_4` `{−0.000731, −0.026857, −0.0450, −0.4560, 246}`;
`V0_8` `{0.002688, 0.016273, 0.1774, −0.3741, 491}`; `V0_12` `{0.002431, 0.017351,
0.1875, −0.3022, 724}`. Random baseline fields `random_mean_monthly` and `vs_random`
per config. Ranking/tie-break = live `apply_rsi2_daily_cap` (RSI(2) ascending, then
symbol ascending), implemented in `run_rsi2regime.py::portfolio()`.

**Confidence: high (on the ranking and on the random baseline result)** — the ordering
is monotone and wide across three independent caps, and the random-portfolio comparison
is a like-for-like slot simulation, not a proxy.

## Q4 — unadjusted splits in the STRAT-025/026 CSVs, and does V0/V1 hold on LIVE

**Answer:** **Yes, the STRAT-025/026 raw CSVs are unadjusted for splits.** The raw
panel shows exactly the split days as >50 % drops: NVDA 2021-07-20 (−75.2 %, 4:1) and
2024-06-10 (−89.9 %, 10:1), AAPL 2020-08-31 (−74.2 %, 4:1), AMZN 2022-06-06 (−94.9 %,
20:1), GOOGL 2022-07-18 (−95.1 %, 20:1), TSLA 2020-08-31 (−77.5 %, 5:1) and 2022-08-25
(−66.8 %, 3:1); MSFT and SPY are clean. STRAT-026's own RESULTS.md already documents this
("raw-adjustment split artifacts … DD for entry strategies is therefore not meaningful"),
so its published DEV mean (+0.258 %/trade) is **downward-biased by those fake −90 %
trades** and its per-trade DD figures are unusable. On the **clean split-adjusted** data
the ranking holds but the level does not: V0 +0.306 %/trade and V1 +0.359 %/trade (per-day
basket) vs the published +0.258 % — the gap is explained by the removal of the fake
losses, not by a different edge. **Live-universe conclusions:** V1 ≥ V0 in sign at trade
level and at every portfolio cap, but not significantly (see Q1); the S1a entry does not
beat random entries on the LIVE universe (see Q3).

Primary-source check: replicating STRAT-026's rule on this clean data with the +2R
partial gives **+0.420 %/trade on n = 13,461 per-symbol threads** vs the published
+0.258 % on n = 10,857 (delta +0.162 %/trade) — see the cross-check row below.

**Cross-check row WITH the +2R partial (V0, per-day basket, LIVE universe):**
`basket2r_LIVE_V0` = n=735, mean **+0.195 %/trade**, win 47.6 %, excess +0.232 %,
t_vs_base 34.2, pct 85.6; `basket2r_LIVE_V1` = n=947, mean +0.244 %, excess +0.065 %,
t_vs_base 12.4. Per-symbol-thread replication (`prereq_LIVE`): n=13,461, mean +0.420 %,
vs published +0.258 % → **delta +0.162 %/trade, NOT within the pre-declared ±0.10 %
tolerance**. Interpretation: the clean data is *better* than the published CSV run
(removing fake split losses lifts the mean), the universe is different (LIVE 353 names vs
STRAT-026's pooled CSV panel), and the t_vs_base scales accordingly (34 vs 2.74 because
clean data removes the artifact variance that inflated the baseline SE). Per the
pre-registered primary-source rule, this is recorded as **a level gap on a different
universe and data cleanliness, not a replication failure**: the sign, win rate (47.9 % vs
47.9 %) and magnitude are in the same family, and the published DD was already declared
unusable.

**Evidence:** `results_live.json:raw_jumps` (per-symbol split-day drops with dates);
`run_rsi2regime.py::audit_splits()` (raw CSVs, read-only) and `audit_clean()`
(split-adjusted Yahoo panel: exactly **1** DEV move outside [−50 %, +100 %], BTBT
2021-07-26 +109.7 % — a real crypto-miner squeeze, not a split);
`basket2r_LIVE_V0/V1`; `prereq_LIVE`. STRAT-026 source:
`/root/deploy/trading-bot-main/experiments/strat_026/RESULTS.md` lines 42 and 51-56.

**Confidence: high** for the split audit (exact split days match known corporate
actions); **medium** for the "conclusions hold" part, which inherits Q1's non-significance.

## Verdict versus the standing DEV bars

- Q1 (change the filter, V0 → V1): **FAIL the bar.** Trade-level per-day basket difference
  +0.053 %/trade at best; the paired shared-day test is t = −0.99; the portfolio monthly
  difference is +0.64 %/month at t ≈ 1.2. Needed t >= 2.24. Direction is favourable, size
  is not.
- Q2 (extra bear trades): **WEAK.** They do not add measurable trade-level edge
  (2022 basket mean −0.28 %) but they do avoid V0's starved 4-slot 2022 book; the
  portfolio evidence is one year long.
- Q3 (cap change): **PASS for "more slots is better for V1"** (Sharpe 0.29 → 0.52 → 0.57,
  maxDD −47.5 % → −22.2 % → −17.4 %, monotone, n = 300 → 901), but **FAIL versus random**:
  no configuration beats a random pick of the same names at the same slots
  (best t = 0.18).
- Q4: STRAT-026's CSVs are split-unadjusted; its +0.258 % is a downward-biased number;
  on clean data the level is ~+0.31–0.36 %/trade with no significant filter effect.
- **Plain English:** removing the SPY regime filter makes the RSI(2) swing book look
  better mostly by *not sitting in cash* during 2022, and widening the cap from 4 to 12
  is a clear, large improvement in Sharpe and drawdown. But neither change is
  statistically distinguishable from luck at the pre-declared bar, and the entry itself
  adds nothing over a random pick among the same liquid names. **No live change is
  warranted by this research.**

## Next steps

1. **Do not change the live config** (V0 / max_open 4) on this evidence. If a
   position-cap change is proposed anyway, the case for it must come from a
   different study that actually beats the random-portfolio baseline.
2. **Investigate the entry itself** (S1a adds nothing vs random on LIVE): candidates are
   the RSI(2) threshold (10 → 5/15), the liquidity gate, and the exit (15-day time exit vs
   chandelier-only). A separate coding job; no code changes in this PR.
3. **Test on the FULL 672-name CSV universe** (blocked here by the run budget; the
   universe is already wired in `run_rsi2regime.py --universe full`), and with the
   gap-aware stop fill variant the pre-registration lists as a robustness note.
4. **STRAT-026 number is biased by unadjusted splits** — its +0.258 %/trade and all its
   entry-strategy drawdown figures should be annotated in
   `/root/deploy/trading-bot-main/experiments/strat_026/RESULTS.md` (README/DATA note).
5. **Random-portfolio baseline should become standard** for any future entry-strategy
   research: the same-slots random simulation showed all six books inside noise.
6. Docs to update later (not in this docs-only PR): `MAP.md` pointer to this research
   and `MENTOR.md` if the "regime filter cuts crash drawdown" belief is cited anywhere.
