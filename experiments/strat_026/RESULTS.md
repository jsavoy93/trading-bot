# STRAT-026 — Pro-Swing Strategy Research (paper only)

Branch: `research/strat-026-pro-swing` (from `research/strat-023-recheck` @ 9b164c3)
Status: research complete. Dev (2021-01-01..2024-12-31) + one-shot holdout
(2025-01-01..2026-10-28). No production code/config/DB/broker changes.

## What was tested

All reuse the frozen STRAT-025 infrastructure (`experiments/strat_025/setup_research/`):
same 672-symbol daily CSV universe (curated liquid list + 307 delisted additions +
watchlist ETFs, survivorship partially mitigated), same loader/Wilder RSI/ATR
conventions, same 10 bps/side cost model, same random-entry baseline machinery
(1000 draws, per-symbol anti-overlap, identical exits). STRAT-025 entry-only tests
were NOT repeated.

- **S1a rsi2_regime** — RSI(2)<10 dip entry with market regime filter
  (SPY close > SPY SMA200 on signal day) and professional exits: initial stop
  2xATR (close-checked), chandelier trailing stop 3xATR (highest close since
  entry, close-checked), 50% partial profit at +2R (intraday high touch, filled
  at 2R), 15-day time exit.
- **S1b volbrk_regime** — 20-day close-high breakout on 1.5x average volume,
  same regime filter and same professional exits.
- **S2 pead_proxy** — PEAD via gap+volume proxy (no earnings-date data
  available in this repo): open gap > +4% vs prior close with volume > 1.5x
  trailing-20d mean, entry NEXT day open, chandelier 3xATR trail, 20-day time
  exit.
- **S3 momentum_{1,2,4}w** — weekly (last trading day of week) rebalance into
  top decile by 6-month return skipping the most recent month, only when
  regime ON (SPY>SMA200); equal weight; 1/2/4-week holds; 10 bps/side per
  turnover.

Gate (pre-declared): dev pass iff n>=100 trades AND
`t_vs_base = (strat_mean − baseline_pooled_mean)/sqrt(se_strat² + se_base²) >= 2`.
Holdout run once, only for dev passers.

## Results (net of 20 bps round-trip costs)

### Dev 2021-2024

| Strategy | n | avg net/trade | win rate | avg win / avg loss | strat t | baseline mean | t vs base | Verdict |
|---|---|---|---|---|---|---|---|---|
| rsi2_regime | 10,857 | +0.258% | 47.9% | +6.38% / −5.38% | 3.02 | +0.024% | **2.74** | **PASS** |
| volbrk_regime | 3,353 | +0.008% | 47.7% | +6.40% / −5.82% | 0.06 | +0.124% | −0.78 | fail |
| pead_proxy | 1,515 | +0.097% | 47.9% | +11.00% / −9.93% | 0.26 | −0.418% | 1.36 | fail |
| momentum_1w | 156 rebal | −0.561%/wk | 46.8% | +2.00% / −2.81% | −2.30 | −0.518% | −0.14 | fail |
| momentum_2w | 156 | −0.758% | 44.9% | +2.61% / −3.50% | −2.53 | −0.519% | −0.62 | fail |
| momentum_4w | 156 | −1.105% | 44.2% | +3.62% / −4.85% | −2.58 | −0.699% | −0.73 | fail |

Max drawdown note for the three entry strategies: the per-trade compounded
equity curve prints −100% because raw-adjustment split artifacts produce
spurious single-trade returns near −90% (e.g. MSTR 2024-08, AVGO 2024-06, NVDA
2021-07 — all split days, not real losses). This is the documented STRAT-022
data caveat; DD for entry strategies is therefore **not meaningful** and both
strategy and baseline share the same artifact. Momentum weekly portfolio DDs
(uncorrupted): −62.6% (1w), −75.1% (2w), −87.3% (4w).

### Holdout 2025-01-01..2026-10-28 (one-shot, dev passers only)

| Strategy | n | avg net/trade | win rate | avg win / avg loss | strat t | baseline mean | t vs base | Outcome |
|---|---|---|---|---|---|---|---|---|
| rsi2_regime | 6,258 | +0.579% | 50.7% | +6.97% / −5.99% | 5.19 | +0.373% | 1.84 | edge positive, below t=2 bar |

## Conclusions

1. **rsi2_regime** is the only candidate that cleared the dev gate (t_vs_base
   2.74). Its holdout edge is directionally positive (+0.579% vs baseline
   +0.373% per trade; +0.21pp edge) and its own t-stat is strong (5.19), but
   the edge-over-random t is 1.84 — **just under the pre-declared bar**.
   Honest verdict: promising, not confirmed. A large part of the holdout
   per-trade return is regime-window market drift (the baseline also earned
   +0.373%/trade in 2025-26).
2. **volbrk_regime** underperformed random entries on dev — the 20-day-high
   volume breakout adds nothing over the exit engine in this universe.
3. **pead_proxy** (gap+volume proxy, documented: no earnings-date data) beat
   its baseline by ~51 bps/trade on dev but t_vs_base 1.36 < 2 — did not
   qualify. Proxy quality (any >4% gap, not earnings-specific) likely dilutes
   the drift signal.
4. **Medium-term momentum** was NEGATIVE in absolute terms at all three hold
   horizons (2021-2024 dev) and no better than random deciles — skipping the
   most recent month did not rescue it in this small-cap-tilted universe.

## Risks / caveats

- Survivorship partially mitigated (307 delisted names added) but not a full
  point-in-time universe; regime filter uses the same SPY series for strategy
  and baseline (fair).
- Split artifacts (raw adjustment) corrupt per-trade DD and inject rare
  extreme fake losses for both strategy and baseline; means/t-stats are only
  mildly affected (rare, symmetric across arms).
- Partial-profit fills at 2R assume intraday high touch = fill at 2R (no
  intraday data; standard assumption, slightly optimistic).
- Holdout was a single window that happened to be almost entirely regime-ON;
  baseline drift was strongly positive, making the t_vs_base bar harder.
- PEAD is a proxy, not true earnings-announcement drift.

## Reproduce

```bash
cd experiments/strat_026
ln -s ../strat_025/setup_research/data data   # frozen 672-symbol CSVs
python3 run_research.py dev       # writes results/_results_dev.json + _dev_selection.json
python3 run_research.py holdout   # dev passers only, one shot
```

Files: `experiments/strat_026/run_research.py`, `experiments/strat_026/engine.py`
(copied frozen from STRAT-025), `experiments/strat_026/RESULTS.md` (this file),
`results/*.json` (committed; CSVs would be gitignored).
