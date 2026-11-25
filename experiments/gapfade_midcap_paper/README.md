# GAPFADE-MIDCAP-PAPER — paper-only gap-fade experiment (S&P 400)

PAPER-ONLY experiment runner. Separate from C4 (`sl_short-*`) and RSI(2)
(`rsi2-*`): it never exits or counts another bot's positions, uses its own
state file, and its orders carry the client_order_id prefix `gf_mid-`.

Status: **Part 1 of 2** — frozen decision logic, guards, dry-run CLI, state,
tests. Part 2 (same branch, same PR) wires live Alpaca PAPER data, real OPG
order submission, shadow-trade logging, the PnL report script and **disabled**
systemd units. Nothing here is enabled or deployed, and part 1 pulls no
credentials.

## Frozen rule (do not tune)

- **Universe**: current S&P 400 members only, injected (never hardcoded).
  Skip names without a reliable prior close and expected open.
- **Earnings filter**: skip names with earnings on t-1, t or t+1 of the
  entry day; skip names with no earnings-calendar coverage.
- **Entry** (pre-market 09:20–09:28 ET): `gap = expected_open / prior_close
  − 1`; signal only if `gap <= −4.0%`. Rank by most negative gap. Max 3 new
  entries/day, max 10 open positions, skip names already held by this runner.
  Sizing = fixed fraction of paper equity (default 1%); skip if the notional
  buys no whole share or buying power is insufficient.
- **Profit exit**: at the first later open strictly above the entry price,
  OPG sell that morning.
- **Stop**: close-based; level = `entry − 2 × ATR14[t−1]` (ATR14 as of the
  day *before* the entry day). After any daily close at or below the level
  (including the entry-day close) sell at the NEXT open (OPG). Documented
  honest divergence from the research, which exited at the triggering close
  (C4 documents its SMA5 next-open exits the same way).
- **Time cap**: 3 trading days, entry day = day 1, so day t+2 close → sell
  at the next open (t+3). Never hold past the cap.
- **Exit priority when several are due**: profit at the open → stop at the
  close → time cap (unit-tested).
- **Shadows**: for every real entry, two NOT-ordered shadow trades — one
  random earnings-clean S&P 400 name not already chosen, one random name
  from the same 60-day close-to-close volatility quintile; same entry day
  (official open) and identical exit rules. RNG is injectable (`--seed`).
- **Costs**: `rules.DEFAULT_COST_BPS_PER_SIDE = 27` bps per side
  (configurable). The research used **10 bps**; 27 is the more conservative
  assumption for mid-caps.

## Module layout

| File | Role |
|---|---|
| `rules.py` | Pure, I/O-free decision functions: gap filter, earnings skip, ranking/caps/sizing, exits + priority, ATR14 stop, shadow selection, costs, ET windows. |
| `guards.py` | Paper-only guard: `TRADING_BOT_PAPER_ONLY=1`, URL hard-locked to `https://paper-api.alpaca.markets`, kill-switch file, exit code 2. |
| `runner.py` | CLI. `--dry-run` is the DEFAULT (prints candidates, intended OPG buys/sells and shadows; zero orders). `--execute` is guarded. |
| `state.py` | Small JSON state store under `/root/trading-data/gapfade-midcap-paper/` (override `GAPFADE_MIDCAP_STATE_DIR` or `--state-dir`). Never the live DB. |
| `broker.py` | Part-2 boundary: `MarketData` / `Broker` protocols; part-1 stubs raise `NotWired`. |

## Guards (execute path only)

`--execute` exits **2** unless all hold:
1. env `TRADING_BOT_PAPER_ONLY=1`;
2. trading base URL is *exactly* `https://paper-api.alpaca.markets` (constant
   in `guards.py`, no override exists);
3. kill-switch file absent (`KILL_GAPFADE_MIDCAP_PAPER` next to `guards.py`,
   override `GAPFADE_MIDCAP_KILL_FILE`).

Part 1 has no broker wired, so `--execute` stops at the broker boundary with
exit code 3. Dry run needs no env and always places zero orders.

## Run

```bash
# dry run (default) — needs an inputs JSON until part 2 supplies data
python experiments/gapfade_midcap_paper/runner.py \
    --inputs inputs.json --pass morning --seed 1

# guarded execute (part 2 only; needs the broker)
TRADING_BOT_PAPER_ONLY=1 python experiments/gapfade_midcap_paper/runner.py --execute
```

`inputs.json` keys: `day`, `trading_days`, `universe`, `prior_close`,
`expected_open`, `all_expected_opens`, `earnings` (symbol → ISO dates),
`eligible_clean`, `vols`, `equity`, `buying_power`; after-close pass needs
`day`, `trading_days`, `bars`.

## Tests

```bash
python -m pytest tests/test_gapfade_rules.py tests/test_gapfade_guards.py \
    tests/test_gapfade_runner.py -q
```

Live in `tests/` because `pytest.ini` sets `testpaths = tests` (same as the
other experiment tests, which insert their experiment dir on `sys.path`).
Interpreter used: `/usr/bin/python3` (3.12.3) with pytest 9.2.0; tests use
temp dirs and never touch a live DB.

## Part 2 placeholders (to be filled in)

- [ ] S&P 400 membership source + refresh policy
- [ ] Earnings-calendar source (and "no coverage ⇒ skip" enforcement)
- [ ] Expected-open method (~09:20 ET) and prior-close source
- [ ] Alpaca PAPER broker behind `broker.Broker` (`submit_opg_buy/_sell`),
      `MarketData` implementation, shadow logging, PnL report
- [ ] Cost notes validated against real fills (27 vs research 10 bps)
- [ ] Josh's systemd units — **installed disabled only**, enable commands
      listed but NOT executed

## Wiring points for part 2

- `runner.plan_morning(state, inp, now, rng, size_fraction) -> dict`
- `runner.plan_after_close(state, inp) -> dict`
- `runner.execute_plan(state, plan, broker, data=None) -> int`
- `broker.make_data(args)` / `broker.make_broker(args)` — build the real
  PAPER client here; it must call `guards.check_paper_only()` before the
  first submit.
- `broker.MarketData`: `expected_open(day)`, `prior_close(day)`,
  `official_bar(symbol, day)`, `true_ranges(symbol, through_day, n)`,
  `eligible_clean(day)`, `vols(day, symbols)`.
