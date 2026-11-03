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
rsi2-AMD-20261103-1            entry market buy
rsi2-AMD-20261103-1-stop       protective stop (sell, remainder)
rsi2-AMD-20261103-1-stop-p     partial-exit limit leg (sell, +2R, OCO)
rsi2-AMD-20261103-1-stop-oco   partial-exit stop leg (sell, same OCO)
rsi2-AMD-20261103-1-timeexit   15-day time exit (sell, market)
```

Labels are deterministic per symbol per day (`next_sequence` over existing
labels), so a same-day rerun after a submitted order computes the same next
id and Alpaca rejects true duplicates; symbols with pending orders are also
skipped outright.

## Run order (idempotent; safe to re-run same day)

1. **Exits** for open RSI(2) trades: 15-trading-day time exit (cancels
   stop/OCO — the market sell is sent only after every cancel is
   CONFIRMED; a failed cancel means no sell and no double order),
   chandelier trail (`max(close since entry) - 3*ATR14`) raises the stop,
   never lowers it (a replace returns a NEW order id — state tracks it).
   A position that is gone (e.g. the stop filled) is closed in
   `state.json` so the slot and slice budget free up.
2. **Stop-after-fill**: protection is placed only AFTER a confirmed entry
   fill, and the WHOLE position is always protected: the partial-exit
   half goes out as an OCO bracket (limit target + stop for that half)
   and the remainder gets a plain stop. Total open sell qty for a symbol
   (existing sells + OCO half + stop) NEVER exceeds held qty. The
   initial stop / +2R target are recomputed from the ACTUAL fill price
   (frozen formulas, signal-day ATR14), and the 15-day clock starts on
   the FILL date.
3. **New entries** (RSI(2) only): skip symbols held in the account, symbols
   with ANY open/pending order (any label, including `sl_short-*` C4 shadow
   orders), and symbols C4 signaled the same day. The C4 shadow DB
   (`/root/strat-swing/experiments/strat_023/live_paper.db`, real columns
   `orders.created_ts` / `orders.ref_date`; override with `--c4-db` or
   `STRAT_SWING_C4_DB`) is read READ-ONLY and FAILS LOUDLY — a missing or
   unreadable DB aborts the run instead of guessing "no C4 signal".
   Refuses to trade an incomplete daily bar (last bar must be a prior
   day, or today after ~21:05 UTC). `TRADING_BOT_PAPER_ONLY=1` is
   REQUIRED for `--execute` (same guard as the C4 shadow runner).

## Sizing (per position, not first-come-takes-all)

- slice = portfolio_value × `--bucket-pct` (default 30%) MINUS the value
  of currently-open C4 positions (read from the C4 shadow DB, same
  fail-loud read) — RSI(2) and C4 share the 30% bucket, so combined
  exposure respects it
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
