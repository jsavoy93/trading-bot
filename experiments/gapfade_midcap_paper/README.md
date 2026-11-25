# GAPFADE-MIDCAP-PAPER — paper-only gap-fade experiment (S&P 400)

PAPER-ONLY experiment runner. Separate from C4 (`sl_short-*`) and RSI(2)
(`rsi2-*`): it never exits or counts another bot's positions, uses its own
state file, and its orders carry the client_order_id prefix `gf_mid-`.

Status: **wired, NOT enabled** — frozen decision logic, guards, dry-run CLI,
state, shadow logging, the real-vs-shadow PnL report (`report_pnl.py`), the
early-stop **flag** (`early_stop.py`; config/file only, trips nothing) and
**disabled** systemd units. No unit is installed, enabled, started or
restarted; nothing here is deployed. Paper only.

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

## Sources (what a run and the report read)

All of it is this experiment's own paper data; the live `trading_bot.db`
is never opened by this experiment.

| Fact | Source | Notes |
| --- | --- | --- |
| Universe (S&P 400) | `broker.MarketData.eligible_clean(day)` / the inputs JSON `universe` + `eligible_clean` | membership injected, never hardcoded |
| Prior close, expected open (~09:20 ET) | inputs JSON `prior_close`, `expected_open`, `all_expected_opens` (part 2: Alpaca PAPER snapshot) | a pre-market print is only a reference, never a fill |
| Official bars, ATR14 | Alpaca PAPER daily bars (`official_bar`, `true_ranges`) | ATR14 as of t−1; exits judged on official bars only |
| Earnings (t−1, t, t+1) | inputs JSON `earnings` (`rules.EarningsCalendar`) | no coverage ⇒ skip |
| Volatility quintiles | inputs JSON `vols` (60-day close-to-close) | vol-matched shadow: same quintile, nearest rank when empty |
| Trades, shadows, fills | this trial's `state.json` (`/root/trading-data/gapfade-midcap-paper/`, override `GAPFADE_MIDCAP_STATE_DIR` or `--state-dir`) | `closed[]`, `shadows[]`, `intents[]`, `early_stops[]` |

`report_pnl.py` reads **only** that `state.json` — `closed` (real) and
`shadows` (random_clean / vol_matched) — and prints trade count, win rate,
mean net/trade and cumulative net per group plus the edge of real vs each
shadow. Cost is per side: `--cost-bps` (default 27, the research used 10).

```bash
python experiments/gapfade_midcap_paper/report_pnl.py            # 27 bps/side
python experiments/gapfade_midcap_paper/report_pnl.py --cost-bps 10 --json
```

## Dry run and refresh

`--dry-run` is the default and places **zero** orders (state is still read,
nothing is written):

```bash
python experiments/gapfade_midcap_paper/runner.py --inputs inputs.json \
    --pass morning --seed 1
python experiments/gapfade_midcap_paper/runner.py --inputs inputs.json \
    --pass after_close --seed 1
```

Refresh the inputs JSON each pass: `day`, `trading_days`, S&P 400 membership
(`universe` + `eligible_clean`), `prior_close`, `expected_open`,
`all_expected_opens`, `vols`, `equity`, `buying_power` (after-close pass:
`bars`). The inputs JSON is the seam between the data source and the frozen
rules; the runner never fetches membership or hardcodes it.

## Divergences from the research (documented, not tuned away)

1. **Exits leave at the NEXT open**, not at the research's triggering
   close: a stop (close ≤ entry − 2×ATR14[t−1]) or the 3-day cap sells at the
   next session's official open (OPG), like the profit exit. The research
   exited at the close that triggered it. This is more feasible to execute
   from a daily-bar runner and is what C4 documents for its SMA5 exits;
   it also makes real and both shadows identical, so the edge comparison
   stays apples to apples.
2. **Sizing is 1% of paper equity per entry**, not the research's 10% slots.
   Mid-cap liquidity and up to 10 concurrent positions make 1% the
   conservative choice; returns are reported per trade, so the edge is
   scale-free.
3. **Costs are 27 bps per side**, not the research's 10 bps. 27 bps is the
   conservative mid-cap assumption (spread + impact + slippage); the report
   script can re-price the same records at 10 bps (`--cost-bps 10`).

## Early stop (flag only)

`early_stop.py` trips when, after **≥150 real fills** (and ≥100 shadow
fills per kind), the trial's cumulative net is below a shadow's — primarily
the vol-matched edge, `< 0` — and records that in this trial's own
`state.json` under `early_stops[]`.

```bash
python experiments/gapfade_midcap_paper/early_stop.py            # print only
python experiments/gapfade_midcap_paper/early_stop.py --record   # write flag
```

It is deliberately powerless: no order, no kill-switch, no signal, no
process or unit management, and it reads only this trial's records, so it
cannot affect any other bot. **A human reads the flag and decides**; the
units below stay disabled either way.

## Systemd units (disabled by design — enable commands NOT EXECUTED)

`systemd/gapfade-midcap-{morning,after-close}.{service,timer}` are committed
but **not installed, not enabled, not started**. The commands below are
documentation only — none of them has been run, and running them is a
separate, explicit operator decision. There is intentionally no `enable.sh`.

```bash
# NOT EXECUTED — operator decision, after a green paper dry run:
cp experiments/gapfade_midcap_paper/systemd/gapfade-midcap-*.{service,timer} \
    /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now gapfade-midcap-morning.timer
systemctl enable --now gapfade-midcap-after-close.timer
# check (also NOT EXECUTED):
systemctl list-timers 'gapfade-midcap-*'
```

## Tests

```bash
python -m pytest tests/test_gapfade_rules.py tests/test_gapfade_guards.py \
    tests/test_gapfade_runner.py -q
```

Live in `tests/` because `pytest.ini` sets `testpaths = tests` (same as the
other experiment tests, which insert their experiment dir on `sys.path`).
Interpreter used: `/usr/bin/python3` (3.12.3) with pytest 9.2.0; tests use
temp dirs and never touch a live DB.

## Status checklist

- [x] S&P 400 membership injected (refresh each pass — see Sources)
- [x] Earnings calendar files, "no coverage ⇒ skip" enforced in `rules`
- [x] Expected open (~09:20 ET), prior close, official bars, ATR14
- [x] PAPER broker behind `broker.Broker`, shadow logging
- [x] PnL report (`report_pnl.py`) and early-stop flag (`early_stop.py`)
- [ ] Cost notes validated against real fills (uses 27 bps/side; 10 via flag)
- [ ] Systemd units — committed disabled; enable commands NOT EXECUTED

## Wiring points

- `runner.plan_morning(state, inp, now, rng, size_fraction) -> dict`
- `runner.plan_after_close(state, inp) -> dict`
- `runner.execute_plan(state, plan, broker, data=None) -> int`
- `broker.make_data(args)` / `broker.make_broker(args)` — build the real
  PAPER client here; it must call `guards.check_paper_only()` before the
  first submit.
- `broker.MarketData`: `expected_open(day)`, `prior_close(day)`,
  `official_bar(symbol, day)`, `true_ranges(symbol, through_day, n)`,
  `eligible_clean(day)`, `vols(day, symbols)`.
