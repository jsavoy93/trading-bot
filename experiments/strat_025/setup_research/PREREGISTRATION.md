# STRAT-025 Part 2 — Pre-Registered Research Plan: Swing Setup Screen (9 Setups)

> RESEARCH ONLY. Paper data. No production code/config/DB/broker changes. No BUY-threshold
> weakening. No merges/pushes/commits from this subtask. This document is frozen BEFORE any
> backtest is run. Anchor: sha256 recorded in `PREREGISTRATION.sha256` (written immediately
> after this file).

## 1. Objective

Test NINE classic swing setups, each SEPARATELY, on daily bars 2020–2026, versus a
random-entry baseline with identical exits/costs and ≥1000 draws. Identify setups with
positive net expectancy that clear the STRAT-022 evidence bar on dev and survive a single
untouched holdout run.

This is NEW research. It does not redo STRAT-022 (RSI(2) dip-buy grid) or STRAT-023
(C4 shadow / survivorship recheck, PR 116/118). Setup (g) deliberately reuses the C4
ENTRY but a DIFFERENT, pre-declared exit (documented in §4g).

## 2. Data

- Daily OHLCV bars, 2020-06-01 → 2026-10-28 (warm-up + dev + holdout).
- Sources:
  - Alpaca paper data API (`adjustment=raw`), same precedent/harness as STRAT-022
    `download_data.py`, for all currently-listed names;
  - read-only copy of per-symbol series extracted from the DATA-001 parquet shards
    (`/root/.openclaw/workspace/trading-bot/experiments/data_001/bars_shards`, untracked)
    for the 307 delisted additions from the STRAT-023 recheck (survivorship mitigation).
- Caveats identical to STRAT-022 §12: raw adjustment (split artifacts; $5 floor mitigates),
  close-checked stops, survivorship partially mitigated (delisted names added; still
  not a full point-in-time universe).

## 3. Universe

Union of:
1. the STRAT-022/C4 universe: STRAT-006R curated ~379 liquid names
   (`experiments/strat_006r_candidate_universe.py`, deduped) PLUS the 307 delisted
   additions selected in the STRAT-023 recheck (`experiments/strat_022/recheck/_additions_selected.csv`), and
2. the main bot watchlist (quality symbols list in `src/core/smart_bot.py`
   `quality_symbols`, incl. ETFs SPY/QQQ/IWM/VTI/VOO/VEA/VWO/BND/AGG/GLD).

Point-in-time eligibility (no lookahead), identical for strategy and baseline:
trailing 20-day mean volume ≥ 1,000,000 shares AND close ≥ $5 on signal day t, AND all
indicators used by the setup are warm (non-NaN) at t.

## 4. The nine setups (exact, frozen rules; signal evaluated on CLOSE of day t, entry at NEXT trading day OPEN t+1; if t+1 open missing → skip)

Common exit engine (applies to all setups unless a setup adds a profit exit):
- **ATR stop**: close_s ≤ entry_open − ATR_MULT × ATR14(signal day t) → exit that close.
- **Time stop**: after TIME_STOP trading days held (entry day = day 1) → exit that close.
- Close-checked stops only (STRAT-022 honest-stop convention). Anti-overlap: one open
  position per symbol; signals while open are ignored. No pyramiding.

Grids (exactly these; ATR_MULT × TIME_STOP):

### (a) ema20_pullback — Pullback to 20-EMA in an uptrend
- Uptrend: close_t > SMA200_t AND SMA50_t > SMA200_t.
- Pullback: low_t ≤ EMA20_t AND close_t ≥ 0.995 × EMA20_t (tagged the EMA intraday,
  held above it into the close).
- Grid: ATR {2.5, 3.0} × TIME {10, 20}.

### (b) high20_breakout_vol — 20-day-high breakout on volume
- close_t > max(close over the prior 20 trading days t−20..t−1), AND
- volume_t > 1.5 × (mean volume over prior 20 days t−20..t−1)  [pre-registered factor: 1.5×].
- Grid: ATR {2.5, 3.0} × TIME {10, 20}.

### (c) vcp_minervini — Volatility-contraction pattern (exact daily-bar definition)
Let H63 = max close over t−63..t−1. Let W1,W2,W3 = the three consecutive 5-trading-day
windows inside t−15..t−1 (W1 oldest). range(W) = max(high in W) − min(low in W).
Signal on day t requires ALL:
1. close_t > SMA200_t (uptrend),
2. close_t ≥ 0.85 × H63 (near highs — base in upper 15% of 3-month range),
3. contraction: range(W2) ≤ 0.70 × range(W1) AND range(W3) ≤ 0.70 × range(W2)
   (≥2 successive contractions of daily-range width),
4. pivot breakout: close_t > max(high over t−15..t−1).
- Grid: ATR {2.5, 3.0} × TIME {10, 20}.

### (d1) gap_and_go — Gap up that holds (daily approximation)
- gap_t = open_t / close_{t−1} − 1 > +2%, AND close_t > open_t (gapped up ≥2% vs prior
  close and held/extended into the close).
- Grid: ATR {2.5, 3.0} × TIME {5, 10}.

### (d2) gap_fill_fade — Gap down that recovers (daily approximation)
- gap_t = open_t / close_{t−1} − 1 < −2%, AND close_t > open_t (reclaim day).
- ADDITIONAL profit exit: close_s ≥ close_{t−1} (gap filled at the pre-gap close) →
  exit that close; ranked BEFORE ATR/time stops on each day s.
- Grid: ATR {2.5, 3.0} × TIME {5, 10}.

### (e) rs_leaders — Relative-strength leaders vs SPY (top decile)
- RS_line(symbol) = close_t / close_{t−RSW} ÷ (SPY_close_t / SPY_close_{t−RSW}).
- Cross-sectional decile: each trading day, rank all ELIGIBLE symbols by RS_line;
  signal fires for symbol X when X is in the top decile today, was NOT in it yesterday,
  AND close_t > SMA200_t.
- Grid: RSW {63 (3m), 126 (6m)} × ATR {3.0} × TIME {20}.

### (f1) inside_day — Inside day
- high_t < high_{t−1} AND low_t > low_{t−1} AND close_t > SMA200_t.
- Grid: ATR {2.5, 3.0} × TIME {5, 10}.

### (f2) nr7 — Narrowest range in 7 days
- (high_t − low_t) < (high_i − low_i) for all i in t−6..t−1, AND close_t > SMA200_t.
- Grid: ATR {2.5, 3.0} × TIME {5, 10}.

### (g) rsi2_uptrend_v2 — C4 entry, distinct exit
- Entry: IDENTICAL to STRAT-022 C4: close_t > SMA200_t AND Wilder RSI(2)_t < 10 AND
  liquidity filter.
- Exit — DIFFERENT from C4 (documented difference): C4 exits are
  (close>SMA5 → exit) / (3.0×ATR stop) / (10-day time). This variant REMOVES the
  SMA5 profit exit entirely; exits are ONLY: ATR stop (ATR_MULT) and TIME_STOP days.
- Grid: ATR {2.5, 3.0} × TIME {5, 10}.

## 5. Costs

10 bps per side (net trade return = gross − 2 × 0.0010). Single cost level for all
decisions (STRAT-022 bar is defined at 10 bps).

## 6. Random-entry baseline (≥1000 draws)

- Same universe, same eligibility, same exit engine (same ATR_MULT/TIME_STOP as the
  compared config; setup-specific profit exits, where declared, apply identically).
- Per draw: for each symbol, sample uniformly (without replacement) as many eligible,
  indicator-warm days as the STRATEGY generated signal days for that symbol/config,
  excluding days that are strategy signal days for that config; anti-overlap applied
  identically.
- 1000 draws, seed 20261031 (frozen). Report: mean of per-draw mean net returns,
  5th/95th percentile of draw means, and pooled per-trade mean (all draws pooled).
- Baseline comparison metric for the pass bar = mean of per-draw means.

## 7. Periods

- **Development: 2021-01-01 → 2024-12-31** (entry dates). ALL tuning/selection here.
  (SMA200 warm-up from 2020-06 data delays earliest eligible entries to ~2021-04 —
  same accepted caveat as STRAT-022.)
- **Holdout: 2025-01-01 → 2026-10-28** (entry dates). LOCKED until dev analysis
  complete. Run EXACTLY ONCE per setup, only for setups whose SELECTED dev config
  PASSED the dev bar. No re-runs.

## 8. Metrics (formulas frozen; same as STRAT-022 §8)

Per setup-config: N trades; win rate; avg win/loss; per-trade Sharpe =
mean(net)/std(net, ddof=1); t-stat = mean(net)/(std(net, ddof=1)/√N); net at 10 bps/side.

## 9. Selection rule per setup (frozen)

Among the setup's grid configs with ≥ 100 dev trades at 10 bps, select the config with
the highest per-trade Sharpe net of 10 bps. If no config reaches 100 dev trades, the
setup FAILS (insufficient sample). No discretion, no re-runs.

## 10. Pass/fail bar (frozen; identical evidence bar to STRAT-022 §10)

A setup's selected config PASSES dev if ALL:
1. mean net per-trade return (10 bps) > 0 with t-stat ≥ 2.0, AND
2. mean net per-trade return exceeds the matched 1000-draw baseline mean by ≥ 0.10%
   (10 bps) per trade.
Holdout PASSES if the selected config's holdout mean net return (10 bps) > baseline
holdout mean AND holdout t-stat > 0. A REJECT is a valid outcome; no post-hoc tuning.

## 11. Deliverables

`experiments/strat_025/setup_research/`: PREREGISTRATION.md (+sha256), prepare_data.py,
universe.py, engine.py, setups.py, run_setup.py, per-setup results JSON/CSV in results/,
RESULTS.md (comparison table), signals.py (standalone daily-bars-DataFrame→bool signal
functions for PASSING setups only).

## 12. Pre-declared caveats

1. Survivorship: partially mitigated (307 delisted names added); still upward-biased —
   universe is anchored on currently-known names.
2. Close-checked stops; gap risk priced at worse closes (conservative trigger timing).
3. `adjustment=raw` split artifacts; $5 floor + close-based rules mitigate.
4. Nine setups × grids tested on the same dev window → multiple-comparison risk; the
   untouched holdout and the ≥10 bps-over-baseline margin are the guard. No Bonferroni
   applied (pre-declared, consistent with STRAT-022).
5. Baseline anti-overlap asymmetry (baseline N may exceed strategy N), inherited from
   STRAT-022 engine convention, documented.
6. Holdout ~22 months, one regime; low power.
