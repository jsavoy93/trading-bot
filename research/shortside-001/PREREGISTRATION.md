# SHORTSIDE-001 pre-registration (frozen 2026-11-19 ~21:25 UTC, before any return was computed)
Idea: the live bot's BUY rule may simply be a mean-reversion dip-buyer. Test its exact MIRROR as a
short rule. RESEARCH ONLY; no orders, no bot changes.

The deployed BUY rule (evidence: /root/deploy/trading-bot-main/src/core/smart_bot.py:643,844,1263,1995-2030,4090-4095;
DB override read from a copy of the deployed trading_bot.db):
- RSI(14) < rsi_buy_threshold. Schema default is 30, but the deployed DB stores an override of 35, so 35 is used.
- SMA(10) > SMA(30) on daily closes.
- MACD histogram (12/26/9) > 0.
- Volume >= 20-day average ONLY when enabled; deployed enable_volume_confirmation=False, so not required.
- Strength: total_score >= 65 STRONG, else MEDIUM. total_score is a composite 0-100 rank score
  (SCORE-002 deprecates it as a gate); the mirrored system has no equivalent, so MEDIUM is the
  conservative (weaker) tier and is what this study tests: any mirrored entry needs no extra
  strength condition. Mapping: STRONG(long, urgent) -> no analog needed; MEDIUM(long, secondary)
  -> all mirrored shorts. Stated caveat: the mirror keeps the binding STRUCTURAL gates
  (mean-reversion + downtrend) and drops MACD/score, because a short mirror of "hist>0" would be
  "hist<0" and that is *not* required for a MEDIUM long either (the score, not MACD alone, decides).
Universe: the bot's (Alpaca active tradable US equities via get_all_us_symbols, smart_bot.py:1741),
which is far more than 15 names, intersected with the local research panel
(/root/.openclaw/worktrees/STRAT-027/experiments/strat_027/data/panel.parquet, 2019-01..2026-09, 6258 symbols).
Shortable: the panel carries NO shortable flag. Free substitute, fixed now: a name counts as shortable
for the study if it is in the panel AND has an ADV(20) >= 1,000,000 shares and price >= $5 at the
decision close (liquid, borrowable large-cap proxy). This is stated as a proxy, not an Alpaca flag.

Rule (frozen):
- Daily bars, decisions at the close of day t, entry at the OPEN of day t+1.
- SHORT entry when RSI(14) > 65 (100 - 35, and if the deployed level had differed we mirror that level)
  AND SMA(10) < SMA(30) at the close of day t (mirror of fast > slow).
- At most one open short per name; entry only if no open position in that name.
- Exit at the earliest of: (a) RSI(14) back below 50 at a close (exit next open),
  (b) hard stop: close >= entry * 1.08 (exit next open; gap-through allowed), (c) 10 trading days held
  (exit at close of day 10).
- Costs, fixed: 0.15% per side (0.30% round trip) plus 5.0%/yr short borrow on short notional,
  prorated over trading days held (252/yr).
- Return of a short trade = (entry price / exit price - 1) - round-trip cost - borrow.
Random baseline: 1000 draws, seed 20261119. For each draw, the same names, same trade count as the
strategy, entry dates sampled uniformly from the same decision-date set as the strategy, hold lengths
drawn from the strategy's own hold-length distribution, same costs. Portfolio monthly return =
average of the draw's trades; percentile of the strategy's monthly mean excess computed on the
1000-draw distribution of the same statistic.
Windows: DEV 2021-01-01..2024-12-31. HOLDOUT 2025-01-01..2026-08-31, run ONCE and only if DEV
passes EVERY bar.
Pass bars (not lowered, no re-runs):
 (1) trades span >= 24 distinct months;
 (2) mean net return per trade > 0;
 (3) mean excess over the random baseline > 0;
 (4) strategy percentile vs the 1000 random draws >= 97.5;
 (5) t-stat on monthly excess >= 2.24;
 also reported: max drawdown, worst single trade.
Caveat stated up front: this is a same-family second look (the BUY rule is already deployed on paper),
so DEV is not fully fresh; only the HOLDOUT would be new information.
