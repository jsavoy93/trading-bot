# GAPFADE-MIDCAP-PAPER — paper-only gap-fade experiment (S&P 400)

PAPER-ONLY experiment runner. Separate from C4 (`sl_short-*`) and RSI(2)
(`rsi2-*`): it never exits or counts another bot's positions, uses its own
state file, and its orders carry the client_order_id prefix `gf_mid-`.

Status: **wired, NOT enabled** — frozen decision logic, guards, dry-run CLI,
state, shadow logging, the real-vs-shadow PnL report (`report_pnl.py`), the
early-stop **flag** (`early_stop.py`; config/file only, trips nothing) and
**disabled** systemd user-unit templates. The live path fetches its own data
(S&P 400 membership from Wikipedia, yfinance earnings, Alpaca PAPER
market data). No unit is
installed, enabled, started or restarted; nothing here is deployed. Paper
only.

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

A **dry run never needs** `TRADING_BOT_PAPER_ONLY` (it places zero orders).
With no `--inputs` it reads live data from the sources below — S&P 400
membership (Wikipedia/Slickcharts), Yahoo Finance earnings via `yfinance` and
Alpaca PAPER market data — using the PAPER credential pair, and prints the
plan. Only `--execute` (order placement) requires the env, the locked paper
URL and an absent kill switch.

## Run

```bash
# live dry run (default): fetches S&P 400 membership, yfinance earnings and
# Alpaca PAPER data; places zero orders; no env var needed
python experiments/gapfade_midcap_paper/runner.py --pass morning

# offline dry run from a JSON fixture (no network)
python experiments/gapfade_midcap_paper/runner.py \
    --inputs inputs.json --pass morning --seed 1

# guarded execute (places OPG orders on the PAPER account)
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
| Universe (S&P 400) | live: `membership.py` — PRIMARY Wikipedia "List of S&P 400 companies", FALLBACK Slickcharts `/sp400` (both parsed from the Symbol column, stdlib only); must yield 380-420 unique symbols or the source is skipped; cached `sp400_members.txt`, >10 days stale ⇒ refuse; offline: inputs JSON `universe` + `eligible_clean` | membership is FETCHED each run when live (cached), never hardcoded. The old SSGA MDY CSV is dead (HTTP 404) and iShares IJH is 403 from this box |
| Prior close, expected open (~09:20 ET) | live: Alpaca PAPER snapshot (`marketdata.py`); offline: inputs JSON `prior_close`, `expected_open`, `all_expected_opens` | a pre-market print is only a reference, never a fill |
| Official bars, ATR14 | Alpaca PAPER daily bars (`official_bar`, `true_ranges`) | ATR14 as of t−1; exits judged on official bars only |
| Earnings (t−1, t, t+1) | live: Yahoo Finance via `yfinance` (`earnings.py`, cached, refreshed ~daily); offline: inputs JSON `earnings` | no coverage ⇒ skip (fail closed) |
| Volatility quintiles | live: Alpaca PAPER closes; offline: inputs JSON `vols` (60-day close-to-close) | vol-matched shadow: same quintile, nearest rank when empty |
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
nothing is written). It needs no `TRADING_BOT_PAPER_ONLY`:

```bash
# live data: S&P 400 membership + yfinance earnings + Alpaca PAPER, zero orders
python experiments/gapfade_midcap_paper/runner.py --pass morning
python experiments/gapfade_midcap_paper/runner.py --pass after_close
# offline fixture instead of the network
python experiments/gapfade_midcap_paper/runner.py --inputs inputs.json \
    --pass morning --seed 1
```

The live path fetches its own data. `--inputs JSON` (`day`, `trading_days`,
`universe`, `prior_close`, `expected_open`, `all_expected_opens`,
`earnings`, `eligible_clean`, `vols`, `equity`, `buying_power`; after-close:
`day`, `trading_days`, `bars`) stays as the offline seam for tests and
no-network dry runs; it is never hardcoded and `--execute` refuses it.

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

## Systemd USER units (templates only — enable commands NOT EXECUTED)

`systemd/gapfade-midcap-{morning,after-close}.{service,timer}` are **user-unit
templates** committed to the repo but **not installed, not enabled, not
started**. The `[Install] WantedBy=timers.target` section is on the two
timers only (services have none); `TRADING_BOT_PAPER_ONLY=1` is set on both
services and the paper URL stays hard-locked in `guards.py`. There is no
`enable.sh` and there will not be one.

Schedules: `Mon..Fri 09:20 America/New_York` (before the 09:28 ET deadline)
and `Mon..Fri 16:10 America/New_York`; timezone is explicit in the timer.

`WorkingDirectory=/root/strat-gapfade` is the stable checkout, created
**after this merges** (nothing in this repo creates it). Both services run
from that checkout's own venv: `ExecStart=/root/strat-gapfade/.venv/bin/python
-m experiments.gapfade_midcap_paper.runner ...`.

```bash
# NOT EXECUTED — once, after merge, on the host that runs the timer:
git clone --branch main https://github.com/jsavoy93/trading-bot.git /root/strat-gapfade
```

Everything below is documentation for a human operator. **None of these
commands has been run by any agent**, and running them is a separate,
explicit decision made after a green paper dry run.

### 1. First dry run (no orders)

The checkout has its own venv at `/root/strat-gapfade/.venv` (the units'
`ExecStart` points at `/root/strat-gapfade/.venv/bin/python`):

```bash
cd /root/strat-gapfade
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
# NOT EXECUTED — offline dry run from the checkout, places ZERO orders:
.venv/bin/python experiments/gapfade_midcap_paper/runner.py \
    --inputs inputs.json --pass morning --seed 1
# and the live-data dry run (reads Alpaca PAPER data, still zero orders,
# no TRADING_BOT_PAPER_ONLY needed):
.venv/bin/python experiments/gapfade_midcap_paper/runner.py --pass morning
```

### 2. Install the user units

```bash
# NOT EXECUTED — user units live in ~/.config/systemd/user/:
mkdir -p ~/.config/systemd/user
cp /root/strat-gapfade/experiments/gapfade_midcap_paper/systemd/gapfade-midcap-*.service \
   /root/strat-gapfade/experiments/gapfade_midcap_paper/systemd/gapfade-midcap-*.timer \
   ~/.config/systemd/user/
systemctl --user daemon-reload
```

### 3. Enable and start the timers

```bash
# NOT EXECUTED — enable + start both timers:
systemctl --user enable --now gapfade-midcap-morning.timer
systemctl --user enable --now gapfade-midcap-after-close.timer
# check (also NOT EXECUTED):
systemctl --user list-timers 'gapfade-midcap-*'
```

### 4. Kill switch

```bash
# NOT EXECUTED — create the file to stop all future orders:
touch /root/strat-gapfade/experiments/gapfade_midcap_paper/KILL_GAPFADE_MIDCAP_PAPER
```

The kill-switch file (`guards.py`; override `GAPFADE_MIDCAP_KILL_FILE`)
makes every `--execute` run exit **2 before any order** is placed; dry runs
are unaffected. Delete the file to resume. The timers keep firing, but each
run refuses at the guard.

### 5. Disable and stop the timers

```bash
# NOT EXECUTED — stop future triggers and remove the enable symlinks:
systemctl --user disable --now gapfade-midcap-morning.timer
systemctl --user disable --now gapfade-midcap-after-close.timer
systemctl --user daemon-reload
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

- [x] S&P 400 membership fetched from Wikipedia (primary) / Slickcharts
  (fallback) each live run; 380-420 range gate, cached, fail-closed
- [x] Earnings calendar files, "no coverage ⇒ skip" enforced in `rules`
- [x] Expected open (~09:20 ET), prior close, official bars, ATR14
- [x] PAPER broker behind `broker.Broker`, shadow logging
- [x] PnL report (`report_pnl.py`) and early-stop flag (`early_stop.py`)
- [ ] Cost notes validated against real fills (uses 27 bps/side; 10 via flag)
- [ ] Systemd user units — templates committed; enable commands NOT EXECUTED

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

## S&P 400 membership sources (`membership.py`)

Resolution order in `load_members`: same-day cache → PRIMARY Wikipedia
`List_of_S%26P_400_companies` → FALLBACK `slickcharts.com/sp400` → cached
copy younger than `MAX_AGE_DAYS` (10) with a LOUD `WARNING: STALE ...`
line → `MembershipError` (fail closed, no universe, no entries). A source's
list is accepted only when it has 380-420 unique symbols; an out-of-range
list counts as a failed source and never passes. A success is written to
`<cache dir>/sp400_members.txt` plus a `sp400_members.downloaded` date
stamp. `fetch(source_name, url) -> str` is injectable; the tests stub it and
never touch the network (`tests/test_gapfade_membership.py`, fixtures shaped
like the real Wikipedia and Slickcharts pages). Stdlib only (`urllib`,
`html.parser`), no new dependencies.
