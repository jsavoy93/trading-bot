# STRAT-026 RSI(2) Dip-Buy Shadow (LOG-ONLY)

Paper-only **shadow** service for the STRAT-026 `rsi2_regime` configuration,
modeled on `strat023-c4-shadow`. **LOG-ONLY**: it never places orders — not
even paper orders. The only network traffic is READ-ONLY `GET` daily bars from
the Alpaca paper data API. There is no order-submission code path.

## Strategy (frozen from `experiments/strat_026/run_research.py`, S1a)

- Entry on completed daily close `t`: Wilder RSI(2) < 10, vol20 ≥ 1,000,000,
  close ≥ $5, AND regime filter ON (SPY close > SPY SMA200).
- Entry execution: next trading day's OPEN.
- Pro exits: initial stop 2×ATR14 (close-checked), chandelier trail 3×ATR14
  (highest close since entry, close-checked, no lookahead), 50% partial at
  +2R on intraday high touch, 15-day time exit.
- Anti-overlap: one open hypothetical position per symbol.
- Costs: 10 bps/side for the logged `net_ret`.

## Safety

- **Own kill switch**: create `experiments/strat_026_shadow/KILL_SWITCH`
  (separate from strat023-c4-shadow's guard) and the run halts with exit 3.
- **Log-only guard**: refuses to start without `STRAT026_LOG_ONLY=1`.

## Logs

- `logs/shadow_events.jsonl` — JSONL events: `signal`, `entry`, `partial`,
  `exit`, `signal_cancelled`, `kill`.
- `logs/state.json` — pending signals + open hypothetical positions.

## systemd (NOT installed, NOT enabled)

`systemd/strat026-rsi2-shadow.{service,timer}` are included in the PR for
review only. Josh approves and installs:

```bash
cp systemd/strat026-rsi2-shadow.* ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now strat026-rsi2-shadow.timer
```

Timer: Mon–Fri 21:30 UTC (after US close; 15 min after strat023's 21:15).

## Run

```bash
STRAT026_LOG_ONLY=1 python shadow_runner.py              # live reads, log-only
STRAT026_LOG_ONLY=1 STRAT026_FIXTURE_DIR=tests/fixtures/strat026 \
    python shadow_runner.py                              # offline fixture mode
```

## Tests

`tests/test_strat026_shadow.py` covers RSI(2) signal logic, regime filter,
pro exits, kill-switch halt, log-only safety (GET-only, no order calls), and
unit-file presence/validity.
