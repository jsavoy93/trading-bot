# REPORT — DASH-SLEEVE live sleeve panel

## Executive summary
Added a read-only live sleeve panel to the trading dashboard (paper account):
new GET `/api/sleeve-live` endpoint + auto-refreshing UI card. Broker (Alpaca
paper) remains authoritative for quantities/values/cash; the per-sleeve split
is derived from `trades.sleeve` ledger attribution via `SleevePositionBook`.
11 new tests pass; full safe suite shows zero new failures vs baseline (37
pre-existing, environment/live-DB dependent, identical before and after).
Branch `dashboard/sleeve-live-panel`, PR opened, NOT merged.

## Task / purpose
Josh-approved (2026-10-30 00:23 UTC): update the trading dashboard, PAPER
ONLY. Fresh worktree off deployed main 3e2cee5; port useful dashboard-only
changes from the dirty checkout; add live sleeve panel (positions & P&L per
sleeve, cash, recent trades/signals, bot health incl. ledger-vs-broker
reconciliation); tests; PR with deploy/rollback steps.

## Ported vs skipped from dirty checkout
- Ported: **nothing** — `dashboard.py` and `templates/dashboard.html` in the
  dirty checkout are byte-identical to 3e2cee5 and have no local
  modifications (`git status` clean for those paths; `git diff HEAD 3e2cee5`
  empty for them).
- Skipped: all dirty-checkout dirt is STRAT-020 research noise
  (`experiments/`, `ITERATION_PROGRESS_LOG.md`, `MENTOR.md` local edits) —
  out of scope per task instructions.

## Branch / Commit
- Branch: `dashboard/sleeve-live-panel` (worktree `/root/.openclaw/worktrees/dash-sleeve`)
- Commit: `546ad93` (base `3e2cee5`, deployed main)

## Files changed
- `dashboard.py` — new read-model section `DASH-SLEEVE` (~305 lines):
  `get_sleeve_live_snapshot()`, `GET /api/sleeve-live`, helpers
  (`_sleeve_db_path`, `_sleeve_systemd_state`, `_sleeve_recent_signals`,
  `_sleeve_holds_last_hour`, `_sleeve_ledger_position_qty`).
- `templates/dashboard.html` — new "🫛 Sleeves — Live" card (id
  `slee-live-card`) + `loadSleevePanel()` JS, auto-refresh 45 s.
- `tests/test_dashboard_sleeve_live.py` — 11 tests (new file).

## Tests run
- `pytest tests/test_dashboard_sleeve_live.py` → **11 passed**.
- Full safe suite `pytest tests` (worktree, `.venv` copy of project venv):
  - Before change (baseline, stash): **37 failed / 2069 passed / 1 skipped**
  - After change: **37 failed / 2080 passed / 1 skipped**
  - Failed-test lists are **byte-identical** (diff = empty). All 37 are
    pre-existing environment/live-`trading_bot.db`-state failures
    (phase-c / p0-buy-blockers / buy-funnel live-shape tests,
    test_bot002_sigterm, test_settings_service API-route tests) — none
    related to this change. No new failures introduced.
- Broker safety gate: PAPER-ONLY guard (BOT-002) observed active in logs;
  no real Alpaca calls in tests (trading_client mocked per conftest).

## Panel description
The card (dashboard tab, between Positions and Latest Cycle) shows:
- Cash + portfolio value (paper account).
- Per-sleeve table: S (short-term technical), L (long-term factor),
  legacy/unattributed — position count, market value, unrealized P&L
  (pro-rata derived from broker numbers × ledger sleeve-qty share).
- Recent trades (with sleeve tag) and latest analyzed_stocks signals.
- Health line: `systemctl --user is-active smartbot-runner.service` state,
  last session start (last loop), last-1h data errors vs total HOLDs
  (failed_analyses; only "No market data" counted as errors per MENTOR.md),
  and ledger-vs-broker quantity reconciliation (mismatch list when deltas
  exist, green check when quantities agree).
- Footer note stating broker-authoritative / derived-attribution contract.

## Deploy steps (NOT executed — for Josh/operator)
Current setup: `trading-dashboard.service` (user systemd) runs uvicorn
dashboard:app on 127.0.0.1:8000 from a deployed checkout
(`/root/deploy/trading-bot-main` @ 3e2cee5).
1. `cd /root/deploy/trading-bot-main && git fetch origin`
2. `git checkout dashboard/sleeve-live-panel` (or merge PR into main first
   and `git checkout main && git pull --ff-only`)
3. `systemctl --user restart trading-dashboard.service`
4. Verify: `curl -s http://127.0.0.1:8000/api/sleeve-live | head` and open
   the dashboard — "Sleeves — Live" card renders.
   (Cloudflare tunnel/Access untouched; still loopback 127.0.0.1:8000.)

## Rollback steps (NOT executed)
1. `cd /root/deploy/trading-bot-main && git checkout 3e2cee5`
2. `systemctl --user restart trading-dashboard.service`
3. Verify `/api/sleeve-live` returns 404 and dashboard renders normally.

## Known risks
- Per-sleeve value/P&L split is pro-rata derived; if ledger attribution
  drifts from broker, numbers are only as good as sleeve tags (mismatch
  banner surfaces this).
- Legacy bucket can show negative qty if tagged ledger qty exceeds broker
  qty — surfaced as reconciliation mismatch, intended.
- `systemctl --user` probe returns "unavailable" when systemd user session
  is not queryable (read-only; never mutates).
- Pre-existing 37 test failures remain (documented, unrelated).

## Manager decision / Next
Awaiting Josh's review of PR. No merge performed (hard rule). Next
recommended action: Josh reviews/approves PR and authorizes deploy steps
above.

Archive: `reports/2026-10-30_004300_dash-sleeve-panel.md`
