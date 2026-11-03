# STRAT-SWING-PAPER — paper swing executor (RSI(2) ONLY)

Single-shot PAPER executor for the STRAT-026 RSI(2) `rsi2_regime` swing
strategy, with per-strategy client_order_id labels so P&L can be
attributed per strategy from the paper account alone.

**SCOPE (Josh, 2026-11-03): this runner trades RSI(2) ONLY.** C4 order
placement was removed from this runner; C4 continues on the existing
`strat023-c4-shadow` timer (labels `sl_short-...`, state in
`live_paper.db`).

## Files

| File | Role |
|---|---|
| `rules.py` | Pure logic: labels, skip/overlap rule, caps, sizing |
| `signals.py` | Frozen research parameters + indicators (DO NOT RELAX) |
| `runner.py` | One-shot daily executor (exits → stops → entries) |
| `report_pnl.py` | Per-strategy P&L report from paper orders/fills |
| `logs/` | `state.json` + `swing_events.jsonl` (gitignored) |

## Order labels

```
rsi2-AMD-20261103-1          entry market buy
rsi2-AMD-20261103-1-stop     protective stop (sell)
rsi2-AMD-20261103-1-stop-p   partial-exit limit (sell, +2R)
rsi2-AMD-20261103-1-timeexit 15-day time exit (sell, market)
```

Labels are deterministic per symbol per day (`next_sequence` over existing
labels), so a same-day rerun after a submitted order computes the same next
id and Alpaca rejects true duplicates; symbols with pending orders are also
skipped outright.

## Run order (idempotent; safe to re-run same day)

1. **Exits** for open RSI(2) trades: 15-trading-day time exit (cancels
   stop/partial, market-sells held qty), chandelier trail
   (`max(close since entry) - 3*ATR14`) raises the stop, never lowers it.
   Closed trades are removed from `state.json` so the slice budget frees up.
2. **Stop-after-fill**: sell stop + partial limit are placed only AFTER a
   confirmed entry fill. Total open sell qty for a symbol (existing sells +
   new partial + new stop) NEVER exceeds held qty: the partial gets at most
   half the remaining room and the stop covers the rest.
3. **New entries** (RSI(2) only): skip symbols held in the account, symbols
   with ANY open/pending order (any label, including `sl_short-*` C4 shadow
   orders), and symbols C4 signaled the same day (best-effort read of
   `live_paper.db`; any error → empty set). Refuses to trade an incomplete
   daily bar (last bar must be a prior day, or today after ~21:05 UTC).
   `TRADING_BOT_PAPER_ONLY=1` is REQUIRED for `--execute` (same guard as
   the C4 shadow runner).

## Sizing (per position, not first-come-takes-all)

- slice = portfolio_value × `--bucket-pct` (default 30%)
- each position ≤ `--per-position-cap` (default 10%) of the slice
- ≤ `--rsi2-cap` new buys/day (default 4), ranked by lowest RSI(2)
- ≤ `--max-open` open RSI(2) positions (default 4)
- open notional counts against the slice; closed trades free it back

## Modes

- **Dry-run (default):** nothing is sent and NOTHING is written to
  `state.json` — only the `swing_events.jsonl` log.
- **`--execute`:** PAPER orders only (URL hard-locked to
  `paper-api.alpaca.markets`). State is saved after each order so one bad
  symbol or HTTP error cannot lose earlier orders; every symbol is wrapped
  in try/except (dotted symbols like BRK.B are supported).

## Usage

```bash
export ALPACA_API_KEY=... ALPACA_API_SECRET=...   # paper keys
export TRADING_BOT_PAPER_ONLY=1                   # required for --execute
python experiments/swing_paper/runner.py --symbols AMD NVDA [--execute]
python experiments/swing_paper/report_pnl.py [--json] [--since 2026-11-01]
```

`KILL_SWITCH` (a file next to this README) halts the runner immediately.

## P&L report facts

- Alpaca v2 orders carry NO `trades` field; fills are quantified via
  `filled_qty` / `filled_avg_price` per order. The activities FILL feed is
  available as a cross-check via `--activities`.
- Pagination uses `after`/`until` windows (no Page-Token).
- Partially-filled then canceled orders are included (`filled_qty > 0`).
- Open positions are attributed by LABELED fills only (net labeled
  buys−sells per strategy), never by "last buyer".
