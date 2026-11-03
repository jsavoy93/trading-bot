# STRAT-SWING-PAPER — per-strategy paper order tagging (C4 + RSI(2))

PAPER-ONLY single-shot executor for the two paper swing strategies, with
per-strategy `client_order_id` labels so fills, positions, and P&L can be
attributed per strategy from the brokerage itself.

- **C4** = STRAT-022 winning dip-buy (RSI(2)<10, close>SMA200, vol20≥1M,
  close≥$5; stop = entry − 3.0×ATR14; 10-trading-day time stop).
- **RSI(2)** = STRAT-026 S1a `rsi2_regime` (RSI(2)<10, vol20≥1M, close≥$5,
  SPY>SMA200 regime; initial stop 2.0×ATR14, chandelier 3.0×ATR14,
  50% partial at +2R, 15-day time exit).

Frozen research parameters are reused EXACTLY. No BUY/entry threshold is
relaxed. `experiments/strat_026_shadow/` (STRAT026_LOG_ONLY) is untouched.

## Labels

```
<strategy>-<SYMBOL>-<MMDD>-<n>          entry   (c4-AMD-1102-1, rsi2-NVDA-1102-1)
<strategy>-<SYMBOL>-<MMDD>-<n>-stop     stop    (c4-AMD-1102-1-stop)
...-stop-p                              RSI(2) +2R partial limit sell
```

`n` is a per-symbol-per-day sequence derived from existing labels
(open + closed orders) so retries never collide. Labels stay well inside
Alpaca's 48-char `client_order_id` limit for equity symbols (≤5 chars).

## Rules

1. **C4 first.** RSI(2) skips any symbol C4 holds (open position) or
   ordered that day (open-order label prefix `c4-<SYM>-<MMDD>-`).
2. **RSI(2) daily cap**: max 4 new buys/day, ranked by lowest RSI(2),
   ties by symbol ASC.
3. **Budget slices**: short-term bucket = 30% of portfolio value
   (configurable); C4 and RSI(2) each get a configurable share of it
   (default 50/50). Each strategy sizes only from its own slice; its
   open + pending notional counts against only its slice.

## Usage

```bash
# dry-run (default — nothing is sent)
ALPACA_API_KEY=... ALPACA_API_SECRET=... python runner.py --symbols AMD NVDA TSLA

# actually place PAPER orders (single shot, no timers)
ALPACA_API_KEY=... ALPACA_API_SECRET=... python runner.py --symbols AMD NVDA --execute

# per-strategy P&L report
ALPACA_API_KEY=... ALPACA_API_SECRET=... python report_pnl.py [--json]
```

Credentials come ONLY from `ALPACA_API_KEY` / `ALPACA_API_SECRET`
environment variables (paper keys). The trading URL is hard-locked to
`https://paper-api.alpaca.markets`; there is no live-trading code path.

Options: `--bucket-pct` (default 0.30), `--c4-share` (default 0.50),
`--rsi2-cap` (default 4). A `KILL_SWITCH` file next to this README halts
the runner immediately (exit 3).

State is kept in `logs/state.json` (gitignored); events append to
`logs/swing_events.jsonl`.

## Safety

- Dry-run by default; `--execute` is required to send anything.
- Paper endpoint only; refuses to start without env credentials.
- Single-shot process; never installs timers/loops. Live trading is
  prohibited by repo policy (AGENTS.md) and not implemented.

## Tests

`tests/test_swing_paper_rules.py` covers label format/sequences/stop
suffix/parse validation, the overlap rule, the daily cap ranking, and
budget slice sizing + cross-strategy isolation (pure logic, no network).
