# STRAT-SWING-PAPER — deployment launcher

`run_daily.sh` is the launcher for the **daily RSI(2) PAPER run**. It is the
`ExecStart` target of the `swing-rsi2-paper.service` **user** unit
(`Type=oneshot`), which fires once per day at 21:30 UTC.

Until 2026-11-04 this script existed only on the server at
`/root/deploy/rsi2-swing-run/run_daily.sh` and was **not tracked in this
repository**. That is how a missing `--symbols` flag broke the 2026-11-04
run: the unit exited with code 2 (`INVALIDARGUMENT`) and the runner's
argparse reported

```
error: the following arguments are required: --symbols
```

No orders were placed. The fix (one token, `--symbols`, on the final
`exec` line) was applied on the server; this directory brings the
corrected script **under version control** so the deployed wrapper and the
reviewed one cannot drift apart silently.

## What this script does

1. **Kill switch first.** If
   `$REPO/experiments/swing_paper/KILL_SWITCH` exists it exits `3` before
   any API call. Create it with:
   `touch /root/deploy/rsi2-swing/experiments/swing_paper/KILL_SWITCH`
2. **Paper-only guard.** Exports `TRADING_BOT_PAPER_ONLY=1`, which the
   runner requires before it will honour `--execute`.
3. **Read-only credentials.** Sources `ALPACA_API_KEY` / `ALPACA_API_SECRET`
   from the existing workspace paper `.env`. The values are **never** stored
   in this file, in this repository, or in any file it writes.
4. **Symbol universe.** Extracts `POPULAR_LIQUID_US` from the STRAT-006R
   curated universe file by AST parsing (no import, no side effects).
5. **Launch.** `exec`s the runner with `--execute` and the four current
   limit flags.

## The launch contract

The final line is the contract this directory now pins with tests:

```bash
exec "$PY" "$RUNNER" --execute --sleeve-pct 0.15 --rsi2-cap 4 \
    --max-open 4 --per-position-cap 0.25 --symbols $SYMBOLS
```

| Flag | Value | Meaning |
|---|---|---|
| `--execute` | — | places **PAPER** orders (default is dry-run) |
| `--sleeve-pct` | `0.15` | RSI(2) sleeve as a fixed share of portfolio equity |
| `--rsi2-cap` | `4` | max RSI(2) new buys per day |
| `--max-open` | `4` | max open RSI(2) positions |
| `--per-position-cap` | `0.25` | per-position cap as a share of the sleeve |
| `--symbols` | *(universe)* | candidate symbols — **required by the runner** |

Changing any of these thresholds changes trading behavior and requires
Josh's explicit approval. The test suite in
`tests/test_swing_paper_launcher.py` asserts these exact values, so a
silent change fails CI instead of changing live paper exposure.

## Tests

```bash
/root/.openclaw/workspace/trading-bot/.venv/bin/python -m pytest \
    tests/test_swing_paper_launcher.py -q
```

The test parses the real launch command out of this script and feeds it to
the runner's **real** argparse, so a renamed flag, a missing `--symbols`,
or a changed threshold fails the suite rather than the next 21:30 UTC run.

## Safety notes

- **Paper only.** The runner hard-locks its trading URL to
  `https://paper-api.alpaca.markets` and refuses `--execute` unless
  `TRADING_BOT_PAPER_ONLY=1` is set. The launcher sets it.
- **No secrets in this repository.** Only the *names* of the credential
  variables appear here; the values live in the external `.env`.
- **The deployed copy is not changed by this directory.** It is a
  reviewable source of truth for the wrapper. Syncing the server copy is a
  separate, explicit deployment step.
