# STRAT-025 Part 1 — Paper-Only Intraday Swing Engine

Standalone engine in `experiments/strat_025/engine/`. Mirrors the
`strat_023` pattern: **no `src/` imports**, Alpaca **PAPER only**, and a hard
startup guard. It **places real (paper) orders** on the Alpaca paper account —
unlike strat_023, which is shadow-only — but can never reach a live endpoint.

## Hard safety

- Refuses to start (exit 2) unless `TRADING_BOT_PAPER_ONLY=1` (see `guard.py`).
- Trading base URL is pinned to `https://paper-api.alpaca.markets`; any
  non-paper URL or `STRAT025_ALLOW_LIVE` value is an immediate refusal.
- Live credentials are never read; paper keys come from the main workspace
  `.env` (read-only) or `ALPACA_API_KEY`/`ALPACA_API_SECRET`.
- All order placement goes through `enter_with_stop()`, which **places a
  broker-side STOP immediately after every entry fill** (GTC, works all day).
  If the stop cannot be placed, the position is **flattened at market
  immediately** — a position is never left unstopped (unit-tested).
- Every order carries a `client_order_id` prefixed `sl_short-`.

## Kill switch

Active when **either**:
- env `STRAT025_KILL_SWITCH=1`, or
- the flag file exists (default `experiments/strat_025/KILL`, override
  `STRAT025_KILL_FILE`).

When active: **no new entries** (RiskManager gate returns `kill_switch`) and
the intraday loop returns. With `STRAT025_KILL_FLATTEN=1` the engine also
flattens every open position at market.

## Config flags (ALL DEFAULT OFF)

| Flag | Default | Meaning |
|---|---|---|
| `STRAT025_ENABLE_INTRADAY_SCAN` | 0 | intraday scanner loop places entries |
| `STRAT025_ENABLE_EOD_ENTRIES` | 0 | 15:45 ET daily-bar setup evaluation |
| `STRAT025_SETUP_<NAME>` | 0 | enable setup predicate `<NAME>` (e.g. `STRAT025_SETUP_PULLBACK_TREND=1`) |
| `STRAT025_SCAN_INTERVAL_SEC` | 300 | loop interval, clamped 60–300 s |
| `STRAT025_RISK_PER_TRADE_PCT` | 1.0 | % of sleeve equity risked per trade |
| `STRAT025_DAILY_LOSS_LIMIT_PCT` | 3.0 | day P&L halt threshold (latches for the day) |
| `STRAT025_MAX_POSITIONS_PER_SECTOR` | 3 | sector + correlation cap |
| `STRAT025_MAX_OPEN_POSITIONS` | 10 | portfolio cap |
| `STRAT025_SLEEVE_EQUITY_FLOOR_USD` | 100 | min notional or refuse the trade |
| `STRAT025_EARNINGS_BLACKOUT_DAYS` | 1 | ±calendar days around earnings |
| `STRAT025_KILL_SWITCH` / `STRAT025_KILL_FILE` / `STRAT025_KILL_FLATTEN` | off | kill switch (above) |
| `STRAT025_DB` | `experiments/strat_025/swing.db` | state DB (gitignored) |

## Universe

Union (read-only) of:
- the **C4 universe** — STRAT-006R `POPULAR_LIQUID_US` (same file as
  strat_023), from the main workspace;
- the **main bot's watchlist** — the symbols the main bot currently tracks,
  read read-only from the main workspace `analysis_status.json`
  (override with `strat_025.engine.universe.main_watchlist(path)`).
  Note: the main bot has no static watchlist table; `analysis_status.json`
  is its concrete tracked-symbol artifact.

## Setup registry (interface for Part 2)

`setups.py` maps setup name → daily-bar predicate
`(df: DataFrame) -> bool`, where `df` has columns
`open/high/low/close/volume/date` plus indicators (e.g. `sma200, rsi2, atr14,
vol20` — same formulas available from `strat_023.shadow_runner.add_indicators`).
Part 2 (`experiments/strat_025/setup_research/`) calls
`setups.register_setup(name, predicate)`. Placeholders
(`pullback_trend`, `breakout_range`, `gap_reclaim`) always return False and
only trade when `STRAT025_SETUP_<NAME>=1` **and** a loop flag is on.

## Earnings blackout

Alpaca has no earnings-calendar API, so dates are pluggable:
`EarningsBlackout(dates={symbol: [ISO dates]}, window_days=N)` or
`EarningsBlackout.from_json(path)` (`STRAT025_EARNINGS_JSON`). Entries are
blocked within ±`window_days` of an earnings date.

## Running

```bash
cd /root/.openclaw/workspace/strat-025
TRADING_BOT_PAPER_ONLY=1 \
STRAT025_ENABLE_INTRADAY_SCAN=1 STRAT025_SETUP_PULLBACK_TREND=1 \
/root/.openclaw/workspace/trading-bot/.venv/bin/python \
  -m experiments.strat_025.engine.engine
```

CLI refuses to run with no loop flag enabled (all flags default OFF).
The runtime loop itself (`SwingEngine.run_loop`) is bounded by
`max_iterations` for testing; the production loop runs while the market is
open (broker clock gate) and idles otherwise.

## Tests

```bash
cd /root/.openclaw/workspace/strat-025
/root/.openclaw/workspace/trading-bot/.venv/bin/python -m pytest \
  tests/test_strat_025_engine.py -p no:cacheprovider -v
```

37 passed: risk caps, sector caps, daily-loss latch, earnings blackout,
kill switch (env/file/flatten), stop-after-fill sequencing incl.
stop-failure→immediate-flatten and no-fill-no-stop, paper guard (4 cases),
registry default-off.

## Known limitations / risks

- Stop fills assume market stop semantics; gap-through-stop slippage is not
  modeled here (paper account reflects it realistically).
- Sector map must be supplied (config/JSON); unmapped symbols share the
  `UNKNOWN` sector bucket, which is capped together (conservative).
- `bars_provider` wiring for live intraday bars is deferred to Part 2 along
  with real setup predicates; Part 1 ships the loop, risk, order, and guard
  machinery fully tested with a mock broker.
