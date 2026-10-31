# STRAT-022 — Pre-Registered Research Plan: Short-Term Dip-Buy (2–5 Day Holds)

> RESEARCH ONLY. Paper-trading data access. No production code/config/DB/broker changes.
> No BUY-threshold weakening. This document is frozen and committed BEFORE any backtest is run.
> Frozen: see git history — commit hash of this file's first commit is the prereg anchor.

## 1. Objective

Test whether a classic short-term dip-buy rule (uptrend filter + extreme short-term
oversold) has positive expectancy net of costs on a liquid US equity universe,
2021–2026, versus a random-entry baseline with identical exits and costs.

Prior-work dedupe (read before designing, not repeated here in detail):
- STRAT-008/009/010/011 studied the *production* signal (RSI<35 & SMA10>SMA30) on a
  60-day window (Jul–Sep 2026). STRAT-022 is a different rule (RSI(2), SMA200 filter,
  ATR/time stops) on a multi-year window. No parameter reuse.
- STRAT-012 established the untouched-holdout discipline followed here.
- STRAT-021 evaluated long-term monthly sub-sleeves (MOM/LVOL/GP) — unrelated cadence.

## 2. Data

- Daily bars (OHLCV) from Alpaca paper data API (`https://data.alpaca.markets/v2/stocks/bars`),
  using the repo's existing paper credentials from `.env` (precedent: STRAT-006R harness).
  NEVER live endpoints/credentials.
- Fetch window: 2020-06-01 → 2026-10-28 (warm-up for SMA200 + dev + holdout).
- Adjustment: `adjustment=raw` (consistent with prior experiments; splits NOT adjusted —
  see caveat §8; symbols with post-2020 split-driven price artifacts are a known noise source;
  mitigated by the $5 price floor at entry time).

## 3. Universe

- Candidate pool: the STRAT-006R curated ~250 well-known liquid US equities
  (`experiments/strat_006r_candidate_universe.py`, deduped). Justification: repo precedent,
  Alpaca-compatible, all liquid names.
- **Survivorship bias — explicit caveat**: the pool is built from *currently-known* tickers.
  Companies delisted/bankrupted before 2026 (e.g., if present in pool history) are missing,
  and names that were small in 2021 and later grew are over-represented. This biases
  backtest returns UPWARD (survivorship). STRAT-020 Phase 5 quantified similar coverage
  gaps for factor cohorts; for this rule-based event study we state the caveat and
  direction rather than claim quantification. Mitigations: dip-buy losses on names that
  went to zero are partially captured only if the ticker remained listed through 2026.
- Point-in-time liquidity filter (no lookahead): a symbol-day is eligible for entry only if:
  - trailing 20-day mean volume ≥ 1,000,000 shares (computed on bars up to and including
    the signal day), AND
  - close ≥ $5 on the signal day.
- The filter is evaluated identically for strategy and baseline.

## 4. Strategy rule (pre-declared; the ONLY configurations that will be run)

Entry signal (evaluated on daily CLOSE t):
- `close_t > SMA200_t` (SMA of close, 200 days), AND
- `RSI(2)_t < ENTRY_RSI` where RSI(2) = Wilder-smoothed RSI with period 2
  (avg gain/loss initialized with simple means of first 2 changes, then Wilder recursion).
- Liquidity filter of §3 must pass on day t.

Entry execution: NEXT trading day's OPEN (t+1). If t+1 open is missing → trade skipped.

Exits (whichever occurs first, all evaluated on CLOSE of each holding day s ≥ t+1):
1. **Time stop**: after `TIME_STOP` trading days held (entry day = day 1), exit at that
   day's close.
2. **Profit-target style exit**: `close_s > SMA5_s` → exit at that same day's close.
3. **ATR stop**: `close_s ≤ entry_open − ATR_MULT × ATR14_signal_day` → exit at that
   same day's close.

Honest stop assumption: stops are checked on CLOSE only (no intraday lows), and the
exit fills at that close. This OVERSTATES stop-fill quality (real intraday breach would
fill at or below the stop; using close ≤ level means we exit at a close at/below the
level, so the assumption is conservative in trigger timing but not in gap risk — a
close far below the stop exits at that worse close, which IS captured). Gaps through
the stop are therefore priced at the (worse) close. Stated plainly: no intraday data used.

Anti-overlap rule: ONE open position per symbol. While a position is open in symbol X,
new entry signals for X are ignored. No pyramiding.

Parameter grid (exactly 8 configurations — no others will be run):
| Config | ENTRY_RSI | ATR_MULT | TIME_STOP |
|---|---|---|---|
| C1 | 10 | 2.0 | 5 |
| C2 | 10 | 2.0 | 10 |
| C3 | 10 | 3.0 | 5 |
| C4 | 10 | 3.0 | 10 |
| C5 | 5 | 2.0 | 5 |
| C6 | 5 | 2.0 | 10 |
| C7 | 5 | 3.0 | 5 |
| C8 | 5 | 3.0 | 10 |

(C2/C4/C6/C8 are the pre-declared "10-day time stop variant" of the base rule.)

## 5. Costs

Round-trip cost applied per side at execution: 5 bps and 10 bps (both reported for
every configuration). Net trade return = gross return − 2 × cost (entry + exit side).

## 6. Random-entry baseline

- Same universe, same liquidity filter, same holding/exit engine (identical time stop,
  SMA5 exit, ATR stop computed with the SAME ATR_MULT of the config being compared),
  same costs.
- Baseline entries: for each symbol, sample uniformly at random from all eligible
  (liquidity-passing, SMA200-warm) days in the same period, excluding days that are
  strategy signal days for ANY config; number of baseline entries per symbol matched to
  that symbol's strategy entry count for the compared config (0 if none). Anti-overlap
  applies identically. ATR stop for baseline uses the same formula from its own entry.
- Seed: 20221110 (frozen). One baseline draw per config/period/cost (no baseline
  shopping).

## 7. Periods

- **Development: 2021-01-01 → 2024-12-31** (entry dates). All tuning/comparison happens here.
- **Holdout: 2025-01-01 → 2026-10-28** (entry dates). LOCKED until dev analysis is
  complete and committed. Run EXACTLY ONCE, for the single config selected by the rule
  in §9 (plus its matched baseline). Reported as-is, no re-runs.

## 8. Metrics (formulas fixed now)

Per configuration (dev, each cost level):
- N trades; win rate = (# trades with net return > 0) / N.
- avg win = mean(net return | net return > 0); avg loss = mean(net return | net return ≤ 0).
- per-trade Sharpe = mean(net) / std(net, ddof=1) (dimensionless, per-trade); also
  annualized daily-equity Sharpe below.
- t-stat = mean(net) / (std(net, ddof=1)/√N).
- Equity curve for drawdown/annualized Sharpe: all trades of the config, each with equal
  notional 1.0, aggregated into a DAILY portfolio return series = sum of each open
  trade's daily close-to-close return that day / concurrent-position-count-that-day
  (i.e., equal-weight average of open positions each day; flat days = 0 net of 0 cost),
  costs booked on entry/exit days. Max drawdown = max peak-to-trough of the cumulative
  log-equity curve of that daily series. Annualized Sharpe = mean(daily)/std(daily)×√252.
- Holdout: same metrics for the single selected config.

## 9. Selection rule (frozen, for the one holdout run)

Among the 8 configs with ≥ 300 dev trades at 10 bps cost, select the config with the
highest per-trade Sharpe net of 10 bps. If no config reaches 300 dev trades, select the
config with the most trades (ties broken by config ID C1<C2<...). No further criteria,
no discretion, no re-runs.

## 10. Pass/fail bar (frozen)

A config PASSES dev if BOTH:
1. mean net per-trade return (10 bps) > 0 with t-stat ≥ 2.0, AND
2. mean net per-trade return (10 bps) exceeds the matched baseline mean by ≥ 0.10%
   (10 bps) per trade.
Holdout PASSES if the selected config's holdout mean net return (10 bps) > baseline
holdout mean and t-stat > 0. A REJECT is a valid outcome; no post-hoc tuning.

## 11. Deliverables

- `experiments/strat_022/` — harness code, data, results JSON.
- `reports/<UTC-ts>_STRAT-022.md` + `REPORT.md` + `ITERATION_PROGRESS_LOG.md` entry.
- Branch `research/strat-022-dip-buy`, PR via `gh`, NOT merged.

## 12. Caveats (pre-declared)

1. Survivorship bias (§3) — upward bias on results.
2. Close-checked stops (§4) — stated assumption.
3. `adjustment=raw` — split artifacts possible; $5 floor + close-based rules limit impact.
4. Wilder RSI(2) with 2-period warm-up; early bars before warm-up excluded.
5. Baseline is a single seeded draw — baseline uncertainty understated; noted in report.
6. 2026 holdout is short (~22 months) and includes one regime only; low power.
