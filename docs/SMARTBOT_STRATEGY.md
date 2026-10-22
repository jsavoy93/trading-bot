# SmartBot Strategy — Authoritative Specification

> **Status:** AUTHORITATIVE as of 2026-10-22.
> **Source revision:** `d54752c` (origin/main, Merge PR #110 `fix/txse-universe-outage`).
> **Deployed revision:** `d54752c` — verified running (`smartbot-runner.service`, PID started
> 2026-10-22 03:04 UTC, checkout `/root/deploy/trading-bot-main`, paper-only).
> **Maintenance contract:** machine-verifiable parameters in this document are embedded as
> `<!-- param: key=value source=... -->` markers and enforced by
> `tests/test_strategy_doc_consistency.py`. A parameter change that is not reflected here
> FAILS CI. Prose sections beyond those markers are **human-reviewed explanations**, not
> machine proofs. This document supersedes MENTOR.md's "Critical Settings" table and any
> conflicting strategy prose (see §14).
>
> **Verification legend:** ✅ = machine-verified at this revision by code inspection pinned
> by tests; 👁 = verified by direct runtime observation (2026-10-21/22 audits, read-only);
> 📝 = human-reviewed explanation that cannot be fully machine-verified.

## 1. Strategy classification and holding horizon

**HYBRID with internally inconsistent horizons** — not purely intraday, swing, or position trading:

- **Entry:** mean-reversion gate (RSI oversold) required *simultaneously* with momentum gates
  (SMA-10 > SMA-30 uptrend AND MACD histogram > 0), on **daily** bars, with an **hourly**
  confirmation blend (30% weight). ✅
- **Entry execution:** hour-scale tranche scaling (40/35/25; tranche 3 at +4h). ✅
- **Exits:** multi-week percentage rules (−8% stop / +15% take-profit / +25% strong-profit)
  evaluated on daily bars; **no position-aging or time-based exit exists**. ✅
- **Operational loop:** every `loop_delay_seconds` (schema default 300 s; **persisted 10 s** 👁),
  re-evaluating daily-bar analysis.

Intended horizon is therefore undefined by design; positions are held until an exit rule
fires, with no upper time bound. 📝

## 2. Analysis pipeline (what actually runs)

Universe → queue → two divergent analysis paths → filters → ranking → execution:

1. **Universe:** all Alpaca assets filtered to `tradable ∧ status=ACTIVE ∧ class=US_EQUITY`
   (`get_all_us_symbols`). Since d54752c, strict alpaca-py asset validation failures
   (e.g. the new `TXSE` exchange code) fall back to per-record validation: ~9 of ~33.6k
   records are skipped instead of emptying the universe. ✅ (observed in production logs: 👁
   "Retrieved 13512 tradeable symbols").
2. **Queue:** `_get_rolling_ticker_list` — rolling sequential queue, excludes open positions
   and pending orders; if >50 candidates, top 300 are RS-ranked and the top 150 are placed
   ahead of the remainder. 30 symbols per cycle. ✅
3. **Analysis path selection (`run_analysis`):** with `enable_multi_timeframe=True`
   (persisted 👁), **Path 1 (`analyze_multi_timeframe`)** is the intended production path;
   **Path 2 (`analyze_symbol`)** is the fallback when hourly data is unavailable. ⚠ The two
   paths publish **different `total_score` values for the same symbol** (Path 1 does not
   blend hourly; Path 2 does) — yet `total_score` is the SCORE-002 ranking key. ✅
4. **Post-analysis BUY→HOLD filters** (in order): regime filter, earnings window
   (`earnings_days_skip=3`), SP-relative-strength filter, volume confirmation, trading
   window, news sentiment, short interest, insider bonus, MTF/volume-downgrade filters
   (⚠ structurally inert in Path 2 — hardcoded defaults are never recomputed), and the
   outer liquidity filter. Effective state of each toggle is in §12. ✅
5. **Execution:** SELL signals execute inline immediately; BUY candidates are ranked
   (SCORE-002) and the top `max_trades` (2 in the deployed invocation) are executed. ✅

## 3. BUY conditions ✅

Single source: `buy_eligible` in `src/core/smart_bot.py::analyze_symbol` (~line 3789):

| Gate | Rule | Effective value |
|---|---|---|
| RSI oversold | `rsi < rsi_buy_threshold` | 30 default, **35 persisted** 👁 |
| SMA uptrend | `SMA_10 > SMA_30` (`sma_fast=10`, `sma_slow=30`) | ✅ |
| MACD histogram | `MACD − MACD_signal > 0` (EMA 12/26, signal 9) | ✅ |
| Volume | `volume_ratio ≥ 1.0` vs 20-day volume SMA — **only if `enable_volume_confirmation`** | schema `True`, **persisted `False` → gate INERT** 👁 |
| Score | **NOT a gate** — `min_score_buy` is deprecated (SCORE-002) and never consulted | ✅ |

Path 1 (`analyze_multi_timeframe`) BUY = daily signal BUY **and** hourly signal BUY
(same rule on hourly bars); disagreement → HOLD ("CONFLICTED"); missing hourly data →
daily-only. ✅

Signal strength: `STRONG` if `total_score ≥ 65`, else `MEDIUM`. ✅

## 4. SELL conditions ✅

Two **different** exit rules depending on path:

- **Path 2 (fallback):** `blended_signed ≤ −50` where
  `blended_signed = daily_raw_signed × 0.70 + hourly_score × 0.30`. ⚠ Known defect
  (backlog SCORE-003): the strength branch makes **MEDIUM SELL unreachable**
  (`≤ −50` implies `≤ 20`).
- **Path 1 (production):** SELL when `SMA_10 < SMA_30 AND rsi > rsi_sell_threshold (70)`
  on daily, **confirmed by the same signal on hourly bars**. There is **no** numeric score
  threshold for SELL in Path 1.
- **`min_score_sell` DOES NOT EXIST** — not in `STRATEGY_SETTINGS_SCHEMA`, no code
  reference. Earlier MENTOR.md documentation of `min_score_sell = 65` was false (the 65
  is the STRONG-BUY threshold). ✅ (corrected misconception)

## 5. Indicators and scoring ✅

`calculate_indicators` (daily bars unless noted):

| Indicator | Period(s) | Formula notes |
|---|---|---|
| SMA fast/slow | 10 / 30 | rolling mean; `sma_slow` is also the min-bar gate |
| RSI | 14 | **Simple (Cutler) RSI** — rolling means of gains/losses, NOT Wilder smoothing |
| MACD | 12/26/9 | `ewm(span, adjust=False)`; only the **histogram** gates BUY |
| Bollinger | 20, 2σ | position used for score |
| ATR | 14 | simple mean of true range (not Wilder); `ATR_pct = ATR/close×100` |
| Volume SMA / ratio | 20 | ratio gates volume confirmation |
| VWAP | session-cumulative | tranche-2 trigger |

**SCORE-001 components** (each clamped ±25; fail-closed to skip on any non-finite input):

- RSI: `25 − rsi/2` · SMA: `±min(25, |separation%| × 5)` · MACD: `(histogram/ATR) × 25`
  (ATR-normalised, dimensionless) · Bollinger: `25 − bb_position×50` · Catalyst: additive,
  **unclamped** (no ±25 clamp — documented gap).
- Volatility tier: `ATR_pct < 2.0` → RSI component ×1.3; `ATR_pct > 5.0` → SMA ×1.3
  (applied after the clamp; transient ±32.5 possible).
- `total_score = clamp(50 + signed_sum, 0, 100)` — Path 2 blends hourly (30%);
  Path 1 does not (see §2.3 divergence). Insider bonus may add +10 (re-clamped).

**SCORE-002 ranking:** eligible BUY candidates sorted `total_score DESC, symbol ASC`;
top `max_trades` executed; remainder skipped (`max_trades_reached`). Score ranks; it never
gates. ✅

## 6. Universe eligibility and liquidity filters ✅

- Universe: §2.1. Queue excludes positions, pending orders, and symbols with ≥3 analysis
  failures (7-day cooldown unban, or 2 successes).
- **Liquidity filter** (`check_liquidity`, applied post-analysis via
  `_apply_outer_liquidity_filter`, only to BUY signals): 20-day average volume
  ≥ `min_daily_volume` (1,000,000) AND high-low spread proxy ≤ `max_spread_pct × 3`
  (0.3 × 3 = **0.9%** effective — `max_spread_pct` mirrors exist for observability only).
  Fail-open on insufficient data ("COULD_NOT_BE_EVALUATED"). `enable_liquidity_filter`
  is persisted **true** 👁.
- RS composite ranking (queue ordering only): 20-day excess return vs SPY (0–40) +
  52w-high proximity (0–30) + volume surge (0–30).

## 7. Position sizing ✅

`calculate_position_size`:

- **PRIMARY (live path, `enable_atr_sizing=True` default):**
  `qty = int(portfolio_value × 2% / (price × ATR%/100))` — each trade risks **2% of
  portfolio at one ATR of adverse move**. `signal_strength` is IGNORED on this path.
  (Corrected misconception: the STRONG 2% / MEDIUM 1.5% / WEAK 1% map is the
  **ATR-unavailable fallback only**, essentially never taken with defaults.)
- Post-sizing: `qty` clamped to ≥1 and ≤ `portfolio_value × max_position_pct (5%) / price`;
  VIX-regime multiplier and adaptive per-symbol multipliers may reduce.
- ⚠ Two conflicting per-ticker caps exist: 5% (`max_position_pct`, sizing) vs a hardcoded
  **10%** in `check_position_limits` (execution gate).
- **No "max positions" setting exists**; open-position count is unbounded by configuration.

## 8. Risk limits and safety gates ✅

Ordered execution checks (`execute_trade`): margin (`cash < 0` blocks all), pending-order
skip, BUY cooldown (240 min), SELL position-exists, per-ticker 10% cap, sector
concentration ≤ `max_sector_concentration` (25% default, **50% persisted** 👁),
correlation ≤ 0.7, portfolio beta ≤ 1.5. ⚠ These checks do **not** include pending
exposure from earlier candidates in the same cycle (documented OBS-001 gap).

Portfolio guards (each `run_analysis`): max drawdown 10% from peak → **pause all trading**
(peak is in-memory; a restart resets the guard); daily loss limit 5% → pause for the day.

**Paper-only enforcement (fail-closed):** `trading_bot_paper_only_guard()` requires
`TRADING_BOT_PAPER_ONLY=1`, paper allowlisted URL, live-URL denylist pass, and `PK`-prefix
API key — else `RuntimeError` before any client is built. `TradingClient(paper=True)` is
hardcoded. Verified live in deployment 👁.

## 9. Stops, take-profit, and trailing stops ✅

- **No protective stop, take-profit, or trailing stop order is placed at entry.**
  (Corrected misconception: earlier documentation implied entry protection.)
- `stop_loss_pct` (8%) and `take_profit_pct` (15%) are constructor constants used **only**
  as confidence weights in the per-loop exit ladder (§10), producing market orders — never
  resting orders.
- `_place_stop_loss_order` is called from **exactly one place**: a position already down
  **>10%** and |market value| > $100 gets a stop at `price − 2×ATR` (or ×0.92 fixed).
  Between entry and −10% there is **no broker-side protection**.
- **`trailing_stop_pct` (5.0) is DEAD CODE** — assigned, never read.
- **`atr_stop_multiplier` (2.0) is effectively dead** — appears only in a log label; the
  ATR stop recomputes from `df['ATR']` without using it.
- `calculate_exit_tranches` / `update_trailing_stops` (position_scaling.py) are **defined
  but never called** — exit tranches and trailing stops DO NOT run in production.
- **No position-aging, max-hold, or time-based exit exists** (verified: no code references).

## 10. Exit ladder (the real exit engine) ✅

`analyze_current_positions_for_selling`, per loop, per position (gated by a **30-minute
per-position cooldown**; positions with |market value| < $50 skipped):

| Trigger | Threshold | Confidence contribution |
|---|---|---|
| Technical SELL signal (§4) | path-dependent | +40 |
| Strong profit | plpc > +25% | +30 |
| Moderate profit | plpc > +10% | +15 |
| **Hard stop-loss** | plpc < −8% | **confidence = 100 (forced)** |
| **Take profit** | plpc > +15% | 90 |
| Defensive warning | plpc < −4.8% (−0.6×8) | +25 |
| Concentration | weight > 12% | +25 |
| Overbought | RSI > 75 | +20 |
| AI sell suggestion | — | +25 |

Act at confidence ≥ 30. Sell size: ≥70 or plpc < −8% → FULL; ≥50 → 50%; else 25%.

Other exits: rotation (worst position sold in full when a candidate scores ≥
`rotation_threshold` = 20 higher and worst position plpc > −5; max one per cycle);
concentration trim (>15% weight AND >50% gain → sell 25% via `_place_portfolio_order`).

**Complete exit-mechanism inventory** (20 mechanisms incl. the 3 non-existent ones) is in
audit archive `2026-10-21_125547_...-FINDINGS-APPENDIX.md` §7.1.

## 11. Entry tranches, orders, fills ✅

- **Entry tranches (wired):** if sized qty ≥ 3, split 40/35/25 — T1 immediate market
  order; T2 when `(price ≤ vwap×1.01 or ≤ entry×1.01) and rsi < 65`; T3 when
  `price > entry and ≥4h since entry and volume_ratio ≥ 1`. Pending plans are
  **in-memory only** — a restart abandons them.
- **Orders:** always `MarketOrderRequest(qty, side, TimeInForce.DAY)`; integer qty.
- **Fill handling: NONE.** No order-status polling, no partial-fill handling, no
  reconciliation. DB rows are written `status='SUBMITTED'`; tranche 1 is marked
  `filled` **on submission, not on actual fill**. SUBMITTED ≠ FILLED — consumers must
  filter accordingly.
- SELL sizing uses `min(trade_amount/price, position qty)`; `trade_amount` = $1000
  hardcoded (not schema-backed).

## 12. Effective configuration vs code defaults

Persisted overrides win over schema defaults (CONFIG-001). Deployed effective values
(👁 verified 2026-10-21/22 from the deployed process settings log and a read-only settings
table dump — CI cannot see the production DB, so this column is human-maintained):

<!-- param: rsi_buy_threshold=35 source=persisted -->
<!-- param: rsi_sell_threshold=70 source=persisted -->
<!-- param: sma_fast=10 source=persisted -->
<!-- param: sma_slow=30 source=persisted -->
<!-- param: rsi_period=14 source=persisted -->
<!-- param: hourly_weight=0.3 source=persisted -->
<!-- param: loop_delay_seconds=10 source=persisted -->
<!-- param: max_sector_concentration=0.5 source=persisted -->
<!-- param: max_correlation=0.7 source=persisted -->
<!-- param: max_portfolio_beta=1.5 source=persisted -->
<!-- param: max_drawdown_pct=10.0 source=persisted -->
<!-- param: daily_loss_limit_pct=5.0 source=persisted -->
<!-- param: atr_position_size_pct=2.0 source=persisted -->
<!-- param: rotation_threshold=20 source=persisted -->
<!-- param: min_score_buy=50 source=persisted -->
<!-- param: enable_multi_timeframe=True source=persisted -->
<!-- param: enable_liquidity_filter=True source=persisted -->
<!-- param: enable_volume_confirmation=False source=persisted -->
<!-- param: enable_regime_filter=False source=persisted -->
<!-- param: enable_sp_filter=False source=persisted -->
<!-- param: enable_sector_filter=False source=persisted -->
<!-- param: enable_ai_conflict_filter=False source=persisted -->

Runtime-disabled filters (schema default `True`, persisted `False` → **inert**): volume
confirmation, regime, SP-relative-strength, sector-rotation, AI-conflict. 👁

Constructor constants (not schema-backed, not persisted, not dashboard-adjustable —
changing them requires a code edit or CLI flag):
<!-- param: stop_loss_pct=8.0 source=constructor -->
<!-- param: take_profit_pct=15.0 source=constructor -->
<!-- param: trailing_stop_pct=5.0 source=constructor -->
<!-- param: atr_stop_multiplier=2.0 source=constructor -->
<!-- param: min_daily_volume=1000000 source=constructor -->
<!-- param: max_spread_pct=0.3 source=constructor -->
<!-- param: max_position_pct=0.05 source=constructor -->
<!-- param: trade_amount=1000 source=constructor -->
<!-- param: earnings_days_skip=3 source=constructor -->
<!-- param: max_position_percentage_exec_cap=10.0 source=hardcoded -->

## 13. Portfolio rebalancing: advisory only ✅

The portfolio optimiser (`run_optimization_pass`, 60-day lookback, every 6 loops)
**computes and prints rebalance recommendations but never executes them** — no orders are
placed from optimiser output. The only executable portfolio actions are the concentration
trim and the late stop-order placement (§9–10).

## 14. Corrected prior-documentation misconceptions

| Earlier claim (MENTOR.md et al.) | Corrected fact |
|---|---|
| `min_score_sell = 65` gates SELL | Key does not exist; SELL = §4 rules |
| Sizing = STRONG 2% / MEDIUM 1.5% / WEAK 1% | ATR 2%-risk sizing is primary; that map is fallback-only |
| Trailing stop 5% active | Dead code; never read |
| Stop-loss protects entries | No entry protection; stop only after −10% |
| Volume/regime/SP/sector filters active | Persisted `False` — inert at runtime |
| `enable_multi_timeframe` gives one score | Path 1 vs Path 2 publish different totals |

MENTOR.md is retained as project history; where it conflicts with this document on
strategy facts, **this document wins**.

## 15. Known operational limitations (machine-verified where noted)

1. Path 1/Path 2 score divergence on the ranking key (§2). ✅
2. MEDIUM SELL unreachable (SCORE-003). ✅
3. Path-2 MTF/volume-downgrade filters structurally inert. ✅
4. No fill reconciliation; SUBMITTED/filled recorded at submission. ✅
5. Dual per-ticker caps (5% sizing vs 10% execution). ✅
6. Trading-window check uses fixed UTC−5 (DST-ignoring). ✅
7. In-memory-only state lost on restart: drawdown peak, pending tranches, analysis queue,
   size multipliers. ✅
8. 10-second loop re-evaluates daily-bar analysis; only the 30-min sell cooldown brakes it. ✅
9. No `bot_version` identity attribute (STRAT-020); snapshots fall back "2.1.0". ✅
10. Catalyst score unclamped (±25 contract broken for that one component). ✅

## 16. Research-only factors — NOT deployed ✅

STRAT-013 / STRAT-015 / STRAT-017 (gross profitability etc.) live exclusively in
untracked `experiments/`; zero imports from `src/`, `main.py`, or `dashboard.py`.
STRAT-017's own verdict: "PASS AS CANDIDATE-SELECTION FACTOR — **NOT DEPLOYED**".
No research factor affects production trading.

## 17. Code references (revision d54752c)

- `src/core/smart_bot.py` — `trading_bot_paper_only_guard`, `get_all_us_symbols`,
  `rank_by_relative_strength`, `_get_rolling_ticker_list`, `calculate_indicators`,
  `_score_components_with_reason`, `analyze_symbol` (buy_eligible ~3789; SELL ~3809),
  `analyze_multi_timeframe`, `check_liquidity` (~2447), `_apply_outer_liquidity_filter`,
  `run_analysis`, `execute_trade`, `calculate_position_size` (~4529),
  `analyze_current_positions_for_selling`, `evaluate_rotation`,
  `execute_portfolio_actions`, `_place_stop_loss_order` (~8190),
  `_execute_pending_entry_tranches`, `run_continuous_loop`
- `src/core/settings_service.py` — `STRATEGY_SETTINGS_SCHEMA`,
  `load_effective_strategy_settings`, `dashboard_parameters`
- `src/analysis/position_scaling/position_scaling.py` — entry tranches (active);
  exit tranches / trailing stops (dead)
- `src/analysis/trading_windows/trading_windows.py` — `is_trading_allowed`
- `src/core/mtf_outcome.py` — `compute_mtf_outcome`
- Regression contracts: `tests/test_smart_bot_score_normalization.py`,
  `tests/test_score_002_eligibility_and_ranking.py`,
  `tests/test_signal_emission_buy_reachability.py`,
  `tests/test_smart_bot_decision_paths.py`, `tests/test_settings_service.py`,
  **`tests/test_strategy_doc_consistency.py` (this document's drift check)**

## 18. What the automated checks can and cannot prove 📝

- **Can prove:** every `<!-- param -->` marker matches the live extraction from
  `STRATEGY_SETTINGS_SCHEMA` and AST-parsed constructor defaults; pinned structural facts
  (SELL threshold expression, buy-eligibility gate terms, dead-code status of trailing
  stop / exit tranches, paper-guard presence) still hold in source.
- **Cannot prove:** that all prose matches every execution path; that the persisted
  production DB values shown in §12 remain current (re-verify after any settings change
  via the dashboard `/api/settings` or the deployed process's "Effective strategy
  settings" log line); behavioural equivalence of untested code paths. Human reviewer
  obligation: when changing `src/core/smart_bot.py` or `settings_service.py`, re-review
  the affected prose sections of this document in the same PR.
