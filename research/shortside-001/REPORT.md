# SHORTSIDE-001 — short-side mirror of the bot's BUY rule (research only)

## Status: COMPLETE — DEV FAILED 3 of 5 bars. HOLDOUT NOT OPENED.

We tested the exact mirror of the live bot's BUY (dip-buy) rule as a SHORT rule, on free
daily data from the local research panel. Nothing was traded, no bot file or account was touched.

## The rule tested (parameters taken from the deployed bot, not from memory)
- Deployed BUY: RSI(14) < 35, SMA(10) > SMA(30), MACD histogram > 0. Volume gate is DISABLED
  in the live settings. The 35 comes from a database override; the schema default is 30.
- Mirror SHORT: RSI(14) > 65 AND SMA(10) < SMA(30) at the close, entry next open.
- Exit at the first of: RSI back below 50, a hard stop at +8% against the entry, or 10 trading days.
- Names: everything the research panel holds that passes a "borrowable" proxy (20-day average
  volume >= 1m shares and price >= $5). One open short per name.
- Costs: 0.15% per side, plus a 5%/yr borrow fee on short notional. Random baseline: 1000 draws, seed 20261119.

## DEV results (2021-01 .. 2024-12), 2213 trades over all 48 months
| Bar | Required | Actual | Pass |
|---|---|---|---|
| Months traded | >= 24 | 48 | YES |
| Mean net return per trade | > 0 | +1.07% | YES |
| Mean excess over random baseline | > 0 | **-1.07%** | NO |
| Percentile vs 1000 random draws | >= 97.5 | **0.9** | NO |
| t-stat on excess | >= 2.24 | **-2.25** | NO |

Other numbers: random short entries made +2.14% per trade (sd 0.48%); win rate 47.2%;
max drawdown of the monthly equity curve -4.1%; worst single trade **-61.0%** (AMC, stopped out
after 2 days); next worst VANI -53%, GFAI -52%, IMPP -50%, HYMC -47% — all are stops on
short squeezes.

## What this means in plain language
Shorting overbought, downtrending names did make a small positive raw return (+1.07% per trade),
but simply shorting a random borrowable name on a random day did about twice as well (+2.14%).
So the mirrored signal has no edge over chance; the strategy's return looks like it came from the
market/sample, not from the rule. The failure is not marginal — the t-stat is negative.
The risk picture is also ugly: a 10-day holding rule with an 8% stop still allowed a 61% loss,
because short-squeeze gappers jump through the stop overnight.

## Conclusion
The mirror of the bot's BUY rule, as specified, is REJECTED. The holdout window was not opened,
as the preregistration requires, and it remains unspent for any future, genuinely different idea.

## Artifacts
- Preregistration (frozen before any return was computed): research/shortside-001/PREREGISTRATION.md
- Script: research/shortside-001/run.py — run with `python3 run.py dev`
- DEV results: research/shortside-001/results_dev.json
- Full detail (commands, outputs, commit SHAs): reports/2026-11-19_212313_shortside-001.md
