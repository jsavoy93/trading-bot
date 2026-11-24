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
