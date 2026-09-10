# BOT-002 — Paper-only fail-closed guard + persistent SmartBot runner

**Branch:** `main` (no PR opened yet — awaiting Josh's review of smoke
results before BOT-002 enablement)
**Commits:** `c1cbe63` (this commit), `e47efe5` (SCORE-003 backlog entry
housekeeping), `126d04f` (SCORE-001 merge), `4d7de83` (BOT-001 merge)
**Status:** Smoke-tested, NOT enabled for continuous operation. Awaiting
Josh's explicit approval to `systemctl --user enable smartbot-runner.service`.

## PREFLIGHT findings

### `scripts/run_continuous.py` hard-codes 5 operational values

```python
MAX_SYMBOLS = 30        # Symbols to analyze per loop
MAX_TRADES = 2          # Max trades per loop
LOOP_DELAY = 60         # Seconds between loops
SUMMARY_INTERVAL = 50   # Show summary every N loops
USE_AI = True           # Smart mode
```

Of these, only `LOOP_DELAY = 60` is a real concern because it overrides
the schema-backed `loop_delay_seconds` value (default 300). The other
four are explicit per-loop operational budgets, not strategy semantics.
None of them change buy/sell thresholds, position sizing, or risk
parameters. **None constitute "material safety override"** per the BOT-002
spec.

### Single-instance gap

`scripts/run_continuous.py` does NOT acquire the `/tmp/trading_bot.lock`
single-instance guard that `main.py` has. A manual invocation of the
script could collide with a systemd-managed instance.

### Decision: target `main.py --continuous` for the systemd unit

The systemd unit `ExecStart` targets `main.py --continuous` instead of
`scripts/run_continuous.py`. `main.py` provides:
- `/tmp/trading_bot.lock` single-instance guard so manual and systemd
  invocations cannot collide.
- `loop_delay=None` default which routes through
  `SmartTradingBot._resolve_loop_delay(None)` and uses the schema-backed
  `loop_delay_seconds` value.
- The same CLI surface for both manual and systemd runs.

`scripts/run_continuous.py` is preserved for manual smoke testing and
now also acquires the lock; its `loop_delay` defaults to `None`.

## Paper-only fail-closed guard

New `trading_bot_paper_only_guard()` function in
`src/core/smart_bot.py` runs BEFORE `TradingClient` construction.
Hard-fails (RuntimeError) if ANY of:

1. `TRADING_BOT_PAPER_ONLY != "1"` (only the literal `"1"` is accepted;
   `"0"`, `"true"`, `"yes"`, etc. are all rejected)
2. `ALPACA_BASE_URL` is unset or empty
3. `ALPACA_BASE_URL` points to a live endpoint (denylisted:
   `api.alpaca.markets`, `api.alpaca.markets/v2`)
4. `ALPACA_BASE_URL` is unrecognised (typos, localhost, unknown hosts)
5. `ALPACA_API_KEY` is missing
6. `ALPACA_API_KEY` does not start with the paper-key prefix `"PK"`

Error messages contain only the failing condition name; they never
leak the secret `ALPACA_API_KEY` or `ALPACA_API_SECRET` values.

### 17 focused tests (all passing)

`tests/test_bot002_paper_only_guard.py`:

- Happy path (paper endpoint + guard = allowed)
- Paper endpoint variants (`/v2`, trailing slash)
- Live endpoint rejected (with and without `/v2`)
- Missing `TRADING_BOT_PAPER_ONLY` rejected
- `"0"`, `"true"`, `"yes"` variants rejected (only literal `"1"` accepted)
- Missing/empty `ALPACA_BASE_URL` rejected
- Malformed/unrecognised URL rejected (typos like
  `paper-api.alpca.markets`, localhost, etc.)
- Live key prefix (`AK`) rejected even with paper URL
- Missing API key rejected
- Error messages never leak secret values
- Guard runs BEFORE `TradingClient` construction (end-to-end)

## Graceful shutdown (SIGTERM handling)

### Issue surfaced during smoke test 1

`systemd stop` sends SIGTERM. Python does not raise `KeyboardInterrupt`
on SIGTERM, so the bot died mid-loop with the in-progress session row
left ACTIVE in `trading_sessions` with no `session_end` timestamp. The
BOT-001 reap-at-startup logic catches it on the next boot, but the
audit trail loses the exact shutdown moment.

### Fix

1. `main.py` installs SIGTERM and SIGINT handlers that translate the
   signal into `sys.exit(0)`. A second signal during shutdown exits
   hard (`os._exit(1)`).
2. `run_continuous_loop` adds a `SystemExit` exception handler that
   calls `_finalize_active_session_on_shutdown(reason)`.
3. New `_finalize_active_session_on_shutdown(reason)` helper calls
   `end_session()` (with `mark_session_failed()` fallback if the
   `end_session()` write fails).

### 2 new focused tests

- `_finalize_active_session_on_shutdown` is a no-op when no
  `session_id` is set.
- `main.py` installs non-default SIGTERM and SIGINT handlers
  (verified by subprocess import + `signal.getsignal()`).

## systemd unit

### Installed

```bash
cp systemd/smartbot-runner.service.template \
   ~/.config/systemd/user/smartbot-runner.service
systemctl --user daemon-reload
```

The unit is **NOT enabled** — `systemctl --user is-enabled
smartbot-runner.service` returns `disabled` (exit=1).

### Unit configuration

```ini
[Unit]
Description=SmartBot Continuous Paper-Only Trading Runner (BOT-002)
Documentation=file:%h/.local/share/trading-bot/smartbot-runner.README.md
After=network-online.target
Wants=network-online.target
StartLimitBurst=5
StartLimitIntervalSec=300

[Service]
WorkingDirectory=/root/.openclaw/workspace/trading-bot
RestartPreventExitStatus=78
Environment=ALPACA_BASE_URL=https://paper-api.alpaca.markets/v2
Environment=TRADING_BOT_PAPER_ONLY=1
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONHASHSEED=random
Environment=HOME=/root
Environment=TMPDIR=/tmp
Environment=PATH=/usr/bin:/root/.local/bin:/root/bin:/usr/local/bin:/bin
ExecStart=/root/.openclaw/workspace/trading-bot/.venv/bin/python \
    -u \
    /root/.openclaw/workspace/trading-bot/main.py --continuous
Restart=always
RestartSec=30
SuccessExitStatus=0 143
StandardOutput=journal
StandardError=journal
SyslogIdentifier=smartbot-runner
TimeoutStartSec=90
TimeoutStopSec=30
KillMode=control-group

[Install]
WantedBy=default.target
```

### Safety properties

1. **Loop-independent persistent process** — `ExecStart` runs
   `main.py --continuous` which calls `SmartTradingBot.run_continuous_loop()`.
2. **Restart=always + RestartSec=30** — recovers from transient failures;
   30s prevents thrash on tight crash loops.
3. **WantedBy=default.target + Linger=yes** — root user has `Linger=yes`
   so the runner survives logout.
4. **Journald logging** — `StandardOutput=journal / StandardError=journal`
   make every log line queryable via `journalctl --user -u smartbot-runner`.
5. **Single-instance protection** — `main.py` acquires
   `/tmp/trading_bot.lock` so a second invocation fails fast.
6. **Explicit paper-only guard** — `Environment=ALPACA_BASE_URL` and
   `Environment=TRADING_BOT_PAPER_ONLY` are hard-coded in the unit.
7. **Fail closed** — `RestartPreventExitStatus=78` + `StartLimitBurst=5`
   over `StartLimitIntervalSec=300` bounds crash-loop amplification.
8. **No live brokerage client** — `TradingClient(paper=True)` is
   hardcoded in `src/core/smart_bot.py:101`; the BOT-002 guard
   enforces paper-only at construction time.
9. **No live endpoint configuration** — only `ALPACA_BASE_URL` is
   consulted; the BOT-002 guard rejects any URL not matching the
   approved paper endpoint.
10. **No credentials embedded** — the unit only references the
    `ALPACA_API_KEY` and `ALPACA_API_SECRET` env vars which systemd
    inherits from the user's environment (no `EnvironmentFile` is
    needed because dotenv-style loading is not used; the bot reads
    `os.getenv(...)` directly).
11. **Graceful shutdown** — SIGTERM/SIGINT handlers in `main.py`
    translate the signal into a graceful `sys.exit(0)` that finalizes
    the active session row.

## Smoke test results

### Smoke test 1 (60 seconds, manual start, manual stop)

| Check | Result |
|---|---|
| Process stays alive | PASS (3 loops in 2 minutes) |
| No crash loop | PASS |
| No live endpoint | PASS (paper-only guard verified) |
| Symbols analyzed | PASS (30 symbols in loop 1) |
| SCORE-001 scoring executes | PASS (sessions 71840-71842) |
| Invalid-data fail-closed | PASS (no crash; expected ERROR logs for delisted symbols) |
| Session counters sane | PASS (71840: 30 symbols, 0 trades) |
| Journald contains useful logs | PASS (portfolio recommendations, regime, etc.) |
| No duplicate SmartBot process | PASS (1 process) |
| No unexpected orders | PASS (0 trades; no BUY signal naturally) |
| Loop delay uses schema-backed value | PASS (10s, not 300s default or 60s hard-coded) |

### Smoke test 2 (post-graceful-shutdown fix)

| Check | Result |
|---|---|
| SIGTERM received | PASS |
| Graceful shutdown intent logged | PASS (`🛑 Received SIGTERM; initiating graceful shutdown...`) |
| Session finalized | PASS (session 71845 status=ENDED) |
| Lock file cleaned | PASS (`/tmp/trading_bot.lock` removed) |
| Service fully stopped | PASS (`active=inactive`) |
| Interrupted session from smoke 1 reaped on next startup | PASS (session 71844 ACTIVE→FAILED with reason `startup-reap`) |

## Full safe suite

`TESTING=1 UNIT_TESTING=1 ./.venv/bin/python -m pytest tests/ -q` →
**1144 passed, 26 warnings in 79.29s** (was 1125 pre-BOT-002, +19 new:
17 paper-only guard + 2 graceful-shutdown tests).

`git diff --check HEAD` clean. `systemd-analyze verify
/root/.config/systemd/user/smartbot-runner.service` exits 0 (unit
is valid).

## Demo-script collection fix

`tests/conftest.py` adds `collect_ignore` for two demo scripts in
`tests/` that construct `SmartTradingBot()` at module-import time
(incompatible with the BOT-002 paper-only guard):

- `tests/test_no_trade_reasons.py` — demo script showing no-trade reasons
- `tests/test_ticker_criteria.py` — demo script showing per-ticker criteria

These are not pytest tests — they are runnable smoke-test scripts that
happen to live under `tests/` for historical reasons. They are excluded
from pytest collection but can still be run manually if needed.

## Decisions / Risks

### Decisions

- **ExecStart targets `main.py --continuous`** (not `scripts/run_continuous.py`)
  for the single-instance lock + schema-backed `loop_delay`.
- **`TradingClient(paper=True)` retained** as defense-in-depth even though
  the BOT-002 guard is the authoritative paper-only boundary.
- **Only the literal `"1"` is accepted** for `TRADING_BOT_PAPER_ONLY` —
  this is a fail-closed guard and accepts only the exact runner-protocol
  value.
- **Loop delay = 10s (schema-backed)** — pre-existing setting in the DB;
  the runner correctly uses it. The bot is running 6 loops/min which is
  operationally fine but generates high API call rate. No change to the
  schema-backed value.

### Risks

- **Pre-existing 2099-01-01 stale session row (id=71804) is not reaped**
  because the reap SQL uses string comparison (`session_start < cutoff`)
  and `"2099-01-01..."` is greater than `"2026-..."`. This is a pre-existing
  limitation, not a BOT-002 bug. The row is BOT-001 test fixture data and
  does not affect the runner.
- **Loop delay of 10 seconds is aggressive** — 6 loops/min generates
  ~8,640 Alpaca API calls/day. Within Alpaca paper rate limits but worth
  monitoring. Josh can raise the value via `settings_service.set(
  "loop_delay_seconds", ...)` if desired.

## Manager review decision

**ACCEPT**: All preflight concerns addressed, all smoke-test checks
pass, all 1144 tests pass, graceful shutdown works, journald captures
useful logs, no live endpoint, no crash loop. The unit is installed
but NOT enabled — continuous operation requires explicit Josh
approval to `systemctl --user enable smartbot-runner.service`.

## Next action

**STOP**. Awaiting Josh's review. Do NOT enable the unit for
continuous operation. Do NOT start SCORE-002. Do NOT begin any
other work without explicit Josh approval.
