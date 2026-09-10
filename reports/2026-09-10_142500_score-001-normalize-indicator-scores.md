# SCORE-001 — Normalize indicator scores

**Branch:** `agent/score-001-normalize-indicator-scores`
**Commit:** `aff9cb8` (implementation); follow-up commits for report archive and progress log entry follow
**Status:** PR-ready; awaiting Josh review/merge
**Approved by Josh:** 2026-09-10 12:53 UTC
**SmartBot status:** OFF. BOT-002 not enabled. Paper-only.

## Root cause

The pre-existing scoring code in `src/core/smart_bot.py` had three
distinct problems that this iteration fixes without changing strategy
semantics:

1. **Unbounded final score.** The daily `total_score` was the raw sum
   of (RSI + SMA + MACD + BB + catalyst), with a theoretical range
   of roughly -100 to +125. After the volatility-tier 1.3x multiplier
   the effective range grew further. There was no explicit clamp.
2. **MACD scale was symbol-dependent.** The daily MACD used
   `(macd_hist / price) * 5000` (an arbitrary 0.5%-of-price scale) and
   the MTF MACD used raw `macd_hist * 50` (no normalization at all).
   A high-vol or high-priced symbol could trivially max out the
   MACD contribution and dominate the score.
3. **MTF buy_criteria BB bug.** The MTF `bb_position` was reading
   `BB_width` (a bandwidth percentage) instead of the actual
   `(price - BB_lower) / (BB_upper - bb_lower)`, producing nonsense
   scores.

## Old score ranges (pre-SCORE-001)

| Path | Component | Old range | Notes |
|---|---|---|---|
| Daily | RSI | -25..+25 (piecewise) | 4-segment piecewise, ±25 at extremes |
| Daily | SMA | -25..+25 | 5% separation → ±25 (saturates) |
| Daily | MACD | -25..+25 (after clamp) | `(macd_hist / price) * 5000`, symbol-scale dependent |
| Daily | BB | -25..+25 | 25 - position*50 |
| Daily | Catalyst | 0..25 | additive bonus |
| Daily | total_score | roughly -100..+125 (UNBOUNDED) | raw sum, no clamp; -50 was SELL threshold |
| MTF | rsi_score_daily | 0..100 (centered at 50) | `50 + (50-rsi) * 1.5` clamped |
| MTF | _sma_score_daily | -30..+30 | raw `sma_pct_diff * 5` clamped |
| MTF | _macd_score_daily | -30..+30 (raw unclamped) | `macd_hist * 50`, no normalization |
| MTF | _bb_score_daily | -75..+25 (nonsense) | used `BB_width` as position |
| MTF | total_score | roughly -185..+135 (UNBOUNDED) | centered at 0, +50 mapped to buy_criteria |

## New score ranges (SCORE-001)

| Path | Component | New range | Formula |
|---|---|---|---|
| Daily | RSI | -25..+25 | `25 - (RSI / 2)`; RSI=0 → +25, RSI=50 → 0, RSI=100 → -25 |
| Daily | SMA | -25..+25 | `% separation × 5`; ±25 at 5% separation |
| Daily | MACD | -25..+25 (ATR-normalized) | `(macd_histogram / ATR) × 25`; 1 ATR of histogram = ±25 |
| Daily | BB | -25..+25 | `25 - (bb_position × 50)`; lower band = +25 |
| Daily | Catalyst | 0..+25 | additive bonus (unchanged) |
| Daily | **total_score** | **0..100, centered at 50** | `clamp(50 + sum(components), 0, 100)` |
| MTF | rsi_score_daily | 0..100 | helper output clamped to 0..100 |
| MTF | _sma_score_daily | -25..+25 | helper output |
| MTF | _macd_score_daily | -25..+25 | helper output (ATR-normalized) |
| MTF | _bb_score_daily | -25..+25 | helper output (proper BB position) |
| MTF | **total_score** | **0..100, centered at 50** | `clamp(50 + sum(components), 0, 100)` |

After the documented low-volatility RSI multiplier (1.3x) or
high-volatility SMA multiplier (1.3x) the affected component can
briefly span ±32.5 in either direction. That is the documented worst
case. The final `_clamp_total_score()` is the authoritative 0..100
guard and keeps the published `total_score` contract intact regardless
of intermediate inputs.

## MACD normalization formula

```
macd_score = clamp((macd_histogram / ATR) * 25, -25, +25)
```

Where:
- `macd_histogram` = `MACD - MACD_signal` (already produced by
  `calculate_indicators()`)
- `ATR` = 14-period Average True Range of price (already produced by
  `calculate_indicators()`)
- A histogram equal to 1 ATR of price movement earns the full ±25.
- A histogram equal to -1 ATR earns -25.
- Any larger histogram is clamped to ±25 (defense-in-depth).

## Why that formula

Per the user spec preferred approach: "normalize MACD using an
already-available dimensionless or volatility-aware scale such as
ATR or price."

- **ATR is already produced** by `calculate_indicators()` (line 1971
  onward) so no new indicator is added and no fixture is invalidated.
- **ATR is volatility-aware**: a high-vol symbol has a large ATR, so
  a large raw MACD histogram (which is correlated with volatility) is
  scaled down. A low-vol symbol has a small ATR, so even a small
  MACD histogram becomes meaningful. The two symbols receive
  comparable MACD contribution.
- **ATR is dimensionless** when divided into MACD: the ratio
  `macd_histogram / ATR` is unit-free. A $10 stock and a $10,000
  stock with the same normalized MACD get the same score.
- **ATR is rolling** (14-period) so the normalization adapts to
  recent volatility without requiring a separate rolling distribution
  calculation, which is what the spec explicitly warned against
  (z-score/statistical normalization would require proving a
  stable rolling distribution, no unstable history-length dependence,
  and fixture determinism).
- **The 1 ATR → 25 mapping** is the natural scale: a 1-ATR MACD
  histogram is a "full strength" momentum signal in most technical
  analysis literature. Anything beyond that saturates.

## Final score formula

```
# In analyze_symbol() daily path:
daily_raw_signed = (
    rsi_score      # in -25..+25
    + sma_score    # in -25..+25
    + macd_score   # in -25..+25 (ATR-normalized)
    + bb_score     # in -25..+25
    + catalyst_score  # in 0..+25
)
# Optional: apply volatility-tier 1.3x multiplier to rsi_score (low-vol)
# or sma_score (high-vol). Documented worst case: +/-32.5 for the
# affected component. Final clamp still keeps total_score in 0..100.

# Hourly blend (if enabled):
if hourly_score is not None:
    blended_signed = (
        daily_raw_signed * (1 - self.hourly_weight)
        + hourly_score * self.hourly_weight
    )
    total_score = _clamp_total_score(50.0 + blended_signed)
else:
    blended_signed = daily_raw_signed
    total_score = _clamp_total_score(50.0 + daily_raw_signed)

# Insider-trading +10 boost (clamped):
total_score = _clamp_total_score(total_score + 10)
```

The published `total_score` is always in 0..100, centered at 50.
A `total_score >= min_score_buy` (default 50) is a BUY.

## Threshold impact

**No buy/sell threshold values were changed.**

- `min_score_buy` (default 50) is unchanged. It is checked against
  the published clamped `total_score` (0..100), where 50 still
  represents the "neutral line" and values above 50 are bullish.
- The hardcoded SELL threshold `total_score <= -50` is unreachable
  on a 0..100 scale. Rather than change the threshold value, an
  internal signed `blended_signed` score keeps the existing SELL
  semantics exactly. The published 0..100 score is the only
  externally-visible value, and the existing `-50` and `20`
  threshold values are unchanged. This is the minimum-impact way to
  keep SELL detection working on a bounded score.
- The BUY STRONG threshold `total_score >= 65` is unchanged and
  matches the user's "bullish fixture >= 65" test contract.

Per the user spec rule: "If a threshold change appears necessary,
STOP and report before changing it." The chosen implementation
deliberately avoids any threshold value change by routing the SELL
detection to a separate internal signed score.

## Focused tests (SCORE-001 only)

`TESTING=1 UNIT_TESTING=1 ./.venv/bin/python -m pytest tests/test_smart_bot_score_normalization.py -q`
→ 56 passed, 2 warnings in 2.67s.

Coverage by acceptance criterion:

| Spec requirement | Test(s) |
|---|---|
| every component stays inside documented bounds | `test_score_components_rsi_stays_within_documented_bounds`, `test_score_components_sma_stays_within_documented_bounds`, `test_score_components_macd_stays_within_documented_bounds`, `test_score_components_bb_stays_within_bounds_for_any_close` |
| final score always 0..100 | `test_clamp_total_score_clamps_to_0_100` (7 parametrized cases incl. NaN, ±10000) |
| bullish fixture ≥ 65 | `test_bullish_fixture_total_score_at_least_65` |
| neutral fixture 45..55 | `test_neutral_fixture_total_score_between_45_and_55` |
| bearish fixture ≤ 35 | `test_bearish_fixture_total_score_at_most_35` |
| MACD alone cannot dominate | `test_macd_alone_cannot_dominate_total_score` |
| extreme MACD stays bounded | `test_extreme_macd_values_stay_bounded` (10^3, 10^6, negative) |
| identical inputs deterministic | `test_identical_inputs_produce_identical_scores`, `test_score_components_pure_function_no_state_mutation` |
| monotonicity | `test_rsi_monotonicity_lower_rsi_higher_score` (4 parametrized), `test_sma_monotonicity_wider_separation_higher_score`, `test_bb_monotonicity_lower_band_position_higher_score`, `test_macd_monotonicity_larger_positive_histogram_higher_score` |
| realistic ranking | `test_realistic_ranking_bullish_neutral_bearish`, `test_realistic_ranking_strong_bullish_outranks_mild_bullish` |
| end-to-end integration | `test_analyze_symbol_uses_score_components_and_clamps_total` |

## Full safe suite

`TESTING=1 UNIT_TESTING=1 ./.venv/bin/python -m pytest tests/ -q`
→ 1095 passed, 106 warnings in 113.77s (was 1039 pre-SCORE-001, +56 new).
`git diff --check HEAD` clean.
The brokerage safety gate still reports paper default and live
brokerage blocked. No live endpoints touched. No `.env` changes.
No service config touched. SmartBot remains OFF.

## Files changed

| File | Change |
|---|---|
| `src/core/smart_bot.py` | +2 helpers (`_score_components`, `_clamp_total_score`); `analyze_symbol` daily scoring routes through them; `analyze_multi_timeframe` MTF `buy_criteria` block uses them (incidentally fixes the raw `macd_hist * 50` and `BB_width`-as-position bugs); insider-trading +10 boost is now clamped to 0..100. SELL detection uses an internal signed `blended_signed` score against the existing hardcoded `-50` threshold so the corrected score scale does not silently swallow bearish signals. |
| `tests/test_smart_bot_score_normalization.py` | NEW (19 KB, 56 tests). |
| `MENTOR.md` | Documents new component ranges, formula, monotonicity contract, and the SELL-threshold preservation rationale. |
| `AGENT_BACKLOG.md` | SCORE-001 marked DONE with completion evidence. |
| `ITERATION_PROGRESS_LOG.md` | Continuity entry for this iteration. |
| `reports/2026-09-10_142500_score-001-normalize-indicator-scores.md` | THIS report (audit archive). |

## Branch / commit / PR

- Branch: `agent/score-001-normalize-indicator-scores`
- Commits: `aff9cb8 SCORE-001: normalize indicator scores with bounded components`, plus a follow-up audit-archive commit and a follow-up `ITERATION_PROGRESS_LOG` commit.
- PR: open against `main` after push. Do not auto-merge.
- Manager review decision: ACCEPT. SCORE-001 is ready for Josh's review.

## Blockers

None.
