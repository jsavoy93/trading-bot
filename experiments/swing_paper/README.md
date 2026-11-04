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
rsi2-AMD-20261103-1-stop       protective stop (sell, FULL held qty)
rsi2-AMD-20261103-1-stop-r2    stop after its 1st chandelier raise
rsi2-AMD-20261103-1-stop-r3    stop after its 2nd chandelier raise
rsi2-AMD-20261103-1-timeexit   15-day time exit (sell, market)
rsi2-AMD-20261103-1-stopgap    market sell when price gapped at/below stop
rsi2-AMD-20261103-1-stopgap-r2 re-protection market sell after a dead
                               -stopgap sell (fresh id: Alpaca rejects
                               a reused client_order_id)
```

LEGACY labels (round ≤2) stay parseable for historical attribution only
and are NEVER created any more: `-stop-p` (partial half-sell) and
`-stop-oco` (OCO stop leg).

Labels are deterministic per symbol per day (`next_sequence` over existing
labels), so a same-day rerun after a submitted order computes the same next
id and Alpaca rejects true duplicates; symbols with pending orders are also
skipped outright.

## Exit / stop contract (Josh decisions S1, 2026-11-03)

- **ONE GTC stop per trade**, for the FULL held qty, placed only AFTER a
  confirmed entry fill. There is **no partial half-sell and no OCO bracket**
  (S1 removed both). The initial stop is re-anchored to the ACTUAL fill
  price (frozen formula, signal-day ATR14); the 15-day clock starts on the
  FILL date.
- **Chandelier trail** (`max(close since entry) - 3*ATR14`) raises the stop
  and NEVER lowers it. A raise is a `PATCH /v2/orders/{id}` that carries a
  NEW deterministic `client_order_id` (`...-stop-r<k>`); the response's NEW
  order id and client id are stored — a stale id is never reused (fix B).
- **Time exit (15 trading days)**: cancel the stop, then POLL until the
  status is `canceled`. `pending_cancel` means "wait and retry"; an order
  that is already canceled/expired/filled/replaced (404/422 on cancel) is
  treated as handled. If the cancellation cannot be CONFIRMED, **no market
  sell is sent** — the stop stays in place and the trade is retried on the
  next run (never a naked position, never a double sell) (fix C).
- **Position gone** (e.g. the stop filled): every leftover `rsi2-*` sell
  order for that symbol is canceled, and `stop_filled` is detected through
  the CURRENT stop client id (fix D).

## Live stop reconciliation (round-5)

Every run starts with a **live reconciliation** of the protective stops
(`runner.reconcile_stops`, before any exit/entry decision):

- each open trade's stop order is fetched from the broker
  (`GET /v2/orders:by_client_order_id`);
- a stop that is **canceled / expired / rejected** while shares are still
  held means the position is unprotected → a **fresh GTC stop** is placed
  immediately at the current stop price with a FRESH deterministic
  `client_order_id` (`...-stop-r<k>`); if the price is at/below the stop
  price the shares are sold at MARKET instead (`-stopgap`, then
  `-stopgap-r<k>` on a re-protection — a reused id would be rejected);
- a **missing stop order id** is reconciled the same way;
- a **pending `-stopgap` market sell** is watched: when it is CONFIRMED
  filled the trade is closed (reason `stop_gap_market_sell`); when it is
  canceled/expired/rejected the shares are protected again. The trade is
  **never closed on submit** — only on a confirmed fill (round-5);
- this closes the gap where a cancel request could not be confirmed
  inside the extended poll window and the broker COMPLETED the cancel
  afterwards: the next run's reconciliation sees the dead stop and
  re-protects the position.

## Sizing (S2 — fixed sleeve, independent of C4)

- sleeve = portfolio_value × `--sleeve-pct` (default **15%**, configurable)
- each position ≤ `--per-position-cap` (default **25% of the sleeve**)
- ≤ `--max-open` open RSI(2) positions (default **4**)
- ≤ `--rsi2-cap` new buys/day (default 4), ranked by lowest RSI(2)
- the sleeve is sized against **RSI(2)'s OWN open positions only** — the
  old "subtract open C4 notional" logic was REMOVED (S2)
- closed trades free the sleeve back

## Run order (idempotent; safe to re-run same day)

0. **Live stop reconciliation** (round-5): dead/missing stops are
   re-placed and pending `-stopgap` sells are confirmed or re-protected —
   BEFORE any exit or entry decision.
1. **Exits** for open RSI(2) trades (see the exit contract above).
2. **Stop-after-fill**: one GTC stop for the full held qty, placed only
   after a confirmed fill. Total open sell qty for a symbol NEVER exceeds
   held qty. A `-stopgap` market sell (price gapped at/below the stop)
   does **not** close the trade on submit — the trade stays open until the
   sell is confirmed filled (round-5).
3. **New entries** (RSI(2) only): skip symbols held in the account, symbols
   with ANY open/pending order (any label, including `sl_short-*` C4 shadow
   orders), and symbols C4 signaled on the **last completed trading day**.
   The C4 shadow DB (`/root/strat-swing/experiments/strat_023/live_paper.db`,
   real column `orders.ref_date`; override with `--c4-db` or
   `STRAT_SWING_C4_DB`) is read READ-ONLY and FAILS LOUDLY — a missing or
   unreadable DB aborts the run instead of guessing "no C4 signal".
   The skip compares against the C4 signal's `ref_date`, so it does not
   matter whether RSI(2) ran before or after C4 that day (fix K).
   Refuses to trade an incomplete daily bar (last bar must be a prior day,
   or today after ~21:05 UTC). `TRADING_BOT_PAPER_ONLY=1` is REQUIRED for
   `--execute` (same guard as the C4 shadow runner).

## Clock (fix J)

`run(args, clock=...)` takes ONE clock (a callable returning a UTC
datetime). That single snapshot drives every time-dependent decision:
`ymd_today`, `last_bar_is_final`, the C4 ref-date check and the
daily-bars request window. The logic helpers REQUIRE the injected clock —
a real wall-clock read exists only in `real_clock()` (the default clock)
and `_log_now()` (log timestamps only).

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

## P&L report facts (fix I)

- Alpaca v2 orders carry NO `trades` field; fills are quantified via
  `filled_qty` / `filled_avg_price` per order. The activities FILL feed is
  available as a cross-check via `--activities`.
- Closed orders are fetched with `nested=false` (no nested leg objects are
  needed — the RSI(2) orders are simple market/stop orders).
- Order pagination advances an `after` window by the FULL timestamp of the
  last order and de-duplicates by order id, so orders that share a boundary
  timestamp (`>=` semantics) are never counted twice.
- Activity pagination follows the Alpaca docs: `direction=asc` with
  `page_token` set to the id of the last activity of the previous page
  (`after` only bounds the first request); activities are de-duplicated by
  activity id.
- Partially-filled then canceled orders are included (`filled_qty > 0`).
- Open positions are attributed by LABELED fills only (net labeled
  buys−sells per strategy), never by "last buyer".

## Run-order contract with C4 (round-4 fix e / round-5)

The C4 same-day skip is `ref_date`-based (fix K), so it is independent of
WHICH runner executes first — but it can only see C4 rows that already
exist. The `strat023-c4-shadow` user timer fires at **21:15 UTC**, so the
RSI(2) runner may only `--execute` **after today's C4 run has actually
FINISHED** (round-5). A bare clock check is not enough: at 21:15 UTC the
C4 run may only just have started, or may have failed.

The gate (`runner.c4_run_finished_today`) looks for evidence that today's
C4 run completed:

1. **Primary — the C4 unit's user journal** (`strat023-c4-shadow.service`,
   today's post-close window from 21:00 UTC): the C4 runner's
   `live paper run complete:` line or systemd's `Finished ...` line. This
   also covers zero-signal days, where the C4 database stays silent.
2. **Fallback — the C4 shadow DB**: rows for today's `ref_date` written
   inside today's post-close window (`live_paper.db`).

If neither is present the `--execute` run is REFUSED (fail loudly, exit
code 2). Dry-run inspection runs remain usable at any time.

**Schedule the RSI(2) runner at 21:30 UTC or later** (the C4 timer fires
at 21:15 UTC; 21:30 leaves a 15-minute cushion for the C4 run to finish).
Covered by `test_execute_refused_until_c4_run_finished`,
`test_execute_allowed_after_c4_run_finished` and
`test_c4_run_finished_gate_uses_journal_and_db`.
