# STRAT-023 — Paper-Only C4 Shadow Runner

Standalone daily shadow tracker for the STRAT-022 **C4** dip-buy rule
(the config selected for the PR 116 holdout). It records simulated signals
and fills to its own SQLite DB. It is **completely decoupled** from the main
bot: no `src/` imports, no brokerage client, no orders — not even paper
orders through the main account.

## The rule (frozen — matches `experiments/strat_022/PREREGISTRATION.md` C4)

- **Entry** (on each completed daily close): `close > SMA200` AND
  Wilder `RSI(2) < 10` AND trailing 20-day mean volume ≥ 1,000,000 AND
  close ≥ $5.
- **Entry fill**: **next trading day's OPEN** (mirrors the backtest exactly;
  detected on the next daily run). Chosen over a same-day close fill so the
  shadow record is directly comparable to the PR 116 backtest.
- **Exits** (whichever first, on close of each holding day, entry day = day 1):
  1. `close > SMA5` → exit at close (`sma5`)
  2. `close ≤ entry_open − 3 × ATR14(signal day)` → exit at close (`atr_stop`)
  3. 10 trading days held → exit at close (`time`)
- **Costs**: 10 bps per side (`net = gross − 20 bps` round-trip).
- **Anti-overlap**: one open position per symbol; signals while open are ignored.
- Parameters live in `shadow_runner.py` (`ENTRY_RSI=10, ATR_MULT=3.0,
  TIME_STOP=10, COST_PER_SIDE=0.0010`) and MUST NOT be relaxed.

## Hard paper-only guard

The runner refuses to start (exit code 2) unless `TRADING_BOT_PAPER_ONLY=1`
is set. This is intentional friction: it can never be wired into anything that
executes real orders by accident. Proven by
`test_guard_refuses_without_env_var`.

## Running manually

```bash
cd <repo>
TRADING_BOT_PAPER_ONLY=1 python experiments/strat_023/shadow_runner.py
```

- Universe: the STRAT-006R curated liquid list (same as
  `experiments/strat_022/download_data.py`), read from the main workspace.
- Data: Alpaca **paper** data API daily bars (`adjustment=raw`), last ~420
  calendar days per symbol, credentials read from the main workspace `.env`
  (read-only) or `ALPACA_API_KEY`/`ALPACA_API_SECRET` env vars.
- Dry-run without network: `TRADING_BOT_PAPER_ONLY=1 STRAT023_FIXTURE_DIR=<dir>`
  with CSVs in the strat_022 data format (`t,o,h,l,c,v`).
- DB override: `STRAT023_DB=/path/to/shadow.db`.
- Output DB: `experiments/strat_023/shadow.db` (**gitignored**), tables
  `signals` (pending → filled/cancelled) and `trades` (exit fields NULL while
  open).

## Daily automation (shipped DISABLED — opt-in only)

Unit files are installed at `~/.config/systemd/user/` (copies in
`experiments/strat_023/systemd/`). The timer fires **21:15 UTC Mon–Fri**,
which is after the 16:00 ET US equity close in both DST (20:00 UTC) and
winter (21:00 UTC) time, so the last daily bar is final.

Enable (Josh's explicit decision):

```bash
systemctl --user enable --now strat023-c4-shadow.timer
systemctl --user list-timers | grep strat023        # verify
journalctl --user -u strat023-c4-shadow.service -f  # tail logs
```

Disable again: `systemctl --user disable --now strat023-c4-shadow.timer`.

## Tests

Run in isolation from the repo suite (no repo conftest is loaded from this
path; `-p no:cacheprovider` keeps the tree clean):

```bash
.venv/bin/python -m pytest experiments/strat_023/test_shadow_runner.py \
    -p no:cacheprovider -v
```

Covers: the paper-only guard (refuses without env, runs end-to-end with env on
a synthetic fixture writing signals + fills to a temp DB), Wilder RSI parity
with `run_backtest.wilder_rsi`, entry-signal parity with
`run_backtest.signal_indices`, exit-logic parity with
`run_backtest.run_trades` across all three exit reasons, and a freeze check
that C4 parameters are not relaxed.

## Caveats

- Exit fills assume the triggering close (same honest-stop convention as the
  backtest; real intraday breaches would fill worse).
- Signals pending fill when a symbol's data stops are cancelled (`data_gap`).
- This is forward paper-shadow data collection only — it says nothing about
  expected live performance beyond what PR 116 already reported.

## STRAT-024 — live PAPER-order runner (`live_paper_runner.py`)

Same frozen C4 rule, but places **real orders on the Alpaca PAPER account
only**. Josh-approved 2026-10-31 (STRAT-024).

### Hard guards (exit code 2, no orders)

1. `TRADING_BOT_PAPER_ONLY=1` must be set.
2. Trading API base URL must be exactly `https://paper-api.alpaca.markets`
   (hardcoded; any `STRAT024_API_BASE_URL` override to a non-paper URL is
   rejected).
3. Kill switch: if `experiments/strat_023/STRAT024_KILL` exists
   (override: `STRAT024_KILL_FILE`), the runner refuses to place ANY order.
4. Fixture bars + live API requires `STRAT024_FIXTURE_LIVE=1` (prevents
   accidental orders from synthetic data).

### Sleeve sizing defaults (conservative)

| Default | Value |
|---|---|
| Sleeve | **30% of account equity** (`SLEEVE_EQUITY_FRAC=0.30`) |
| Max open runner positions | **3** (`MAX_POSITIONS`) |
| Max position size | **10% of account equity** (`MAX_POSITION_PCT`) |

Per-position notional = min(remaining sleeve cash, 10% × equity, buying
power), whole shares only; entries are skipped when one share is not
affordable. Sleeve cash is reduced by open runner positions AND still-open
runner entry orders.

### Collision safety with the main bot

- Every `client_order_id` starts with `sl_short-` and every order row in
  `live_paper.db` records `sleeve='short'`.
- Exit quantities are capped at the runner's own tracked qty — the runner
  NEVER sells shares it did not buy, even if the main bot holds the same
  symbol.
- Entries are skipped for symbols the account already holds beyond the
  runner's own position.

### Exits (placed as orders — C4 rules unchanged)

- **3xATR stop**: resting GTC sell `stop` order placed right after the
  entry fill, at `fill_price − 3 × ATR14(signal day)`.
- **close > SMA5** on the latest completed close → cancel the stop, submit
  a DAY market sell (fills at next open; the backtest exits at the
  triggering close — known honest divergence).
- **10 trading days held** → same cancel-stop + market-sell path.

### Idempotency

Deterministic `client_order_id`s (`sl_short-<SYM>-e/s/x…-<YYYYMMDD>`)
checked against the local `orders` table AND open Alpaca orders before
submission; Alpaca rejects duplicate client_order_ids as a final defense.
Reruns place no duplicate orders.

### Run / DB

```bash
TRADING_BOT_PAPER_ONLY=1 .venv/bin/python experiments/strat_023/live_paper_runner.py
```

DB: `experiments/strat_023/live_paper.db` (gitignored; override
`STRAT024_DB`). Tables: `orders` (PK = client_order_id, `sleeve='short'`)
and `positions` (`pending_fill → open → closed`).

### Tests

```bash
TRADING_BOT_PAPER_ONLY=1 TESTING=1 .venv/bin/python -m pytest \
    experiments/strat_023/test_live_paper_runner.py -p no:cacheprovider -v
```

Proves: all four guards, frozen C4/sleeve parameters, sizing caps
(30%/10%/whole-share/skip), sl_short tagging + sleeve=short record,
main-bot-hold entry skip, max 3 positions, 3xATR stop price formula,
SMA5 exit (market sell + stop cancelled first), time exit after 10 days,
never-sells-more-than-own-qty, rerun idempotency, external stop-fill
closes the tracked position.
