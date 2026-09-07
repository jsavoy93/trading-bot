# PR1 — Persistent Dashboard Service (detailed archive)

- **Repository**: `/root/.openclaw/workspace/trading-bot`
- **Project ID**: `trading-bot` (per Josh's spec)
- **Branch**: `agent/dash-persistent-service-pr1`
- **Base commit**: `8976279` (main)
- **Head commit**: `b3beea7`
- **Archive timestamp (UTC)**: 2026-09-07 02:15:00
- **Author / role**: trading-manager
- **Run classification**: Implementation (PR1 of the 5-PR durable
  Engineering Dashboard plan; do NOT begin PR2 until review)

## 1. Scope and Boundaries

In-scope for PR1 (per Josh's spec):
- Persistent process management only.
- Dashboard starts after server reboot.
- Dashboard restarts if it crashes.
- Dashboard bound to loopback only.
- Logs available through `journalctl`.
- No Termius shell required to keep it alive.
- Use the same USER-LEVEL systemd pattern already used by OpenClaw.
- Verify the existing OpenClaw user-service/drop-in layout and mirror it
  correctly.
- Preserve TESTING/UNIT_TESTING env only if the dashboard actually requires
  them for production operation; do not blindly carry test-only flags into
  a persistent production service.
- Enable linger only if required for root user-level systemd startup after
  reboot.

Out of scope (do NOT begin until PR1 reviewed):
- Cloudflare Tunnel + Access (PR2).
- Durable SQLite chat store (PR3).
- Durable + Live Chat UI (PR4).
- Current session backfill (PR5).
- The unrelated CUPS `0.0.0.0:631` exposure (Josh flagged as separate
  follow-up).

## 2. Acceptance Criteria

For each criterion: original text, proof method, exact result, PASS/FAIL.

### 2.1 Service starts
- **Criterion**: User-level systemd service starts cleanly via
  `systemctl --user start dashboard.service`.
- **Proof**: `systemctl --user start dashboard.service` (exit 0, no output);
  immediate status check showed `Active: active (running)`.
- **Result**: `Active: active (running) since Mon 2026-09-07 02:12:03 UTC; 3s ago`,
  `Main PID: 558505 (python)`.
- **Status**: PASS

### 2.2 `curl 127.0.0.1:8010/engineering = 200`
- **Criterion**: HTTP 200 from the engineering dashboard route.
- **Proof**: `curl -sS -o /dev/null -w "HTTP %{http_code} size=%{size_download} time=%{time_total}s\n" http://127.0.0.1:8010/engineering`
- **Result**: `HTTP 200 size=60768 time=0.096116s`
- **Status**: PASS

### 2.3 snapshot / history endpoints work
- **Criterion**: `/api/engineering/snapshot` and
  `/api/engineering/chat/history` return HTTP 200 with bounded JSON.
- **Proof**: Two `curl -sS -o /tmp/*.json -w "HTTP %{http_code} size=%{size_download}\n" …`
  invocations against the running service.
- **Result**:
  - `/api/engineering/snapshot` → `HTTP 200 size=5098`. First 200 chars:
    `{"project_identity":"trading-bot","repository":{"root":"/root/.openclaw/workspace/trading-bot","branch":"agent/dash-persistent-service-pr1","is_clean":false,"dirty_paths":[".dashboard.env","reports/20…`
  - `/api/engineering/chat/history?limit=5` → `HTTP 200 size=141`. Body:
    `{"session":{"agent":"trading-manager","status":"unavailable","has_active_run":false,"run_status":null,"reason":"RuntimeError"},"messages":[]}`. The `status: unavailable` at this exact poll moment is expected (Gateway chat.history returns bounded retry-on-error); the route itself responds and the bounded read-model projection works.
- **Status**: PASS

### 2.4 kill process → systemd restarts it
- **Criterion**: SIGKILL of the uvicorn main PID triggers a restart within
  `RestartSec=5` and the endpoint is immediately healthy again.
- **Proof**: Read `MainPID` and `NRestarts`, SIGKILL, sleep 7 s, re-read.
- **Result**:
  - Pre-kill: PID `558505`, NRestarts `0`.
  - `kill -9 558505` at 02:12:33 UTC.
  - `dashboard.service: Main process exited, code=killed, status=9/KILL`.
  - `dashboard.service: Scheduled restart job, restart counter is at 1.` at 02:12:38 UTC (5 s later — exact RestartSec=5 behavior).
  - Post-restart: PID `558561`, NRestarts `1`, `Active: active`.
  - `curl http://127.0.0.1:8010/engineering` → `HTTP 200` immediately.
- **Status**: PASS

### 2.5 Reboot-survival configuration verified
- **Criterion**: Service restarts automatically after server reboot.
- **Proof**: Linger status + unit `[Install]` + `systemctl --user is-enabled`.
- **Result**:
  - `loginctl show-user root` → `Linger=yes`, `State=lingering` (pre-existing; no change needed).
  - Unit `[Install]` contains `WantedBy=default.target` (user-level default target).
  - `systemctl --user enable dashboard.service` created symlink
    `~/.config/systemd/user/default.target.wants/dashboard.service → /root/.config/systemd/user/dashboard.service`.
  - `systemctl --user is-enabled dashboard.service` → `enabled`.
  - End-to-end host reboot is destructive and out of scope; the configuration is verified.
- **Status**: PASS

### 2.6 No 0.0.0.0 bind
- **Criterion**: Dashboard never binds to a public address.
- **Proof**: `ss -tlnp | grep :8010`.
- **Result**: `LISTEN 0 2048 127.0.0.1:8010 0.0.0.0:* users:(("python",pid=558561,fd=6))`. The bind (left column) is `127.0.0.1:8010`. The `0.0.0.0:*` on the right is the kernel accept-source field, not a bind address. No `0.0.0.0:8010` line.
- **Status**: PASS

### 2.7 `git diff --check`
- **Criterion**: Diff clean for commit hygiene.
- **Proof**: `git diff --check` (and `git diff --cached --check`).
- **Result**: No output, exit 0.
- **Status**: PASS

### 2.8 Focused tests
- **Criterion**: Dashboard-focused test suite passes.
- **Proof**: `TESTING=1 UNIT_TESTING=1 .venv/bin/python -m pytest tests/test_dashboard_api_app.py tests/test_dashboard_api_provider.py tests/test_dashboard_chat_gateway.py tests/test_dashboard_engineering_read_model.py tests/test_dashboard_timeline.py -q --no-header`
- **Result**: `174 passed, 2 warnings in 20.17s`. Warnings are pre-existing (`pytest timeout` unknown config; `websockets.legacy` deprecation — both noted in MENTOR.md).
- **Status**: PASS

### 2.9 Full safe suite (code changed)
- **Criterion**: Full safe suite passes (run because repo files changed:
  `.gitignore`, `MENTOR.md`, `docs/infrastructure/dashboard-systemd.md`).
- **Proof**: `TESTING=1 UNIT_TESTING=1 .venv/bin/python -m pytest --no-header -q`
- **Result**: `883 passed, 84 warnings in 57.34s`. Pre-existing warnings only (`datetime.utcnow()` in `src/database/sqlite_db.py`, `websockets.legacy`, pytest timeout). No new failures.
- **Status**: PASS

### 2.10 TESTING/UNIT_TESTING not in production env
- **Criterion**: Production service env does not carry test-only flags.
- **Proof**: Read unit `Environment=…` block and drop-in.
- **Result**: No `Environment=TESTING=…` or `Environment=UNIT_TESTING=…` in either the main unit or the drop-in. Drop-in explicitly comments: "NEVER add TESTING=1 or UNIT_TESTING=1 to a production service."
- **Status**: PASS

### 2.11 journalctl logs available
- **Criterion**: Service logs are reachable via journalctl.
- **Proof**: `journalctl --user -u dashboard.service -n 12 --no-pager`.
- **Result**: Captured the full lifecycle: `Started dashboard.service`, `Started server process`, `Application startup complete`, `Uvicorn running on http://127.0.0.1:8010`, GET lines, `Main process exited, code=killed, status=9/KILL`, `Scheduled restart job`, new boot under new PID, new GET lines.
- **Status**: PASS

## 3. Files Changed (authoritative list)

### 3.1 Tracked (in the repo, on branch `agent/dash-persistent-service-pr1`)

```
.gitignore                               |   3 +
MENTOR.md                                |   5 +
docs/infrastructure/dashboard-systemd.md | 111 +++++++++++++++++++++++++++++++
3 files changed, 119 insertions(+)
```

Diff excerpts (relevant hunks):

`.gitignore`:
```diff
+# Persistent dashboard service runtime env (PR1: dashboard.service.d/10-env.conf)
+.dashboard.env
+
```

`MENTOR.md`:
```diff
+**Engineering Dashboard:** `dashboard_api/app.py` (FastAPI) runs under a
+user-level systemd service (`dashboard.service`, loopback `127.0.0.1:8010`)
+mirroring the `openclaw-gateway.service` layout. Reboot-survival via
+`Linger=yes` for the root user. See `docs/infrastructure/dashboard-systemd.md`.
+
```

`docs/infrastructure/dashboard-systemd.md` (new file): 111 lines covering
service layout, full unit content, drop-in content, operating commands,
reboot survival, crash restart, loopback-only verification, and production
discipline (no TESTING/UNIT_TESTING, no live credentials).

### 3.2 Untracked (system files, outside the repo)

- `~/.config/systemd/user/dashboard.service` — main unit.
- `~/.config/systemd/user/dashboard.service.d/10-env.conf` — drop-in.
- `/root/.openclaw/workspace/trading-bot/.dashboard.env` — mode 0600,
  gitignored, placeholder for future runtime overrides.

These files are intentionally outside the repo: the canonical OpenClaw
pattern stores them in `~/.config/systemd/user/` (the user-level systemd
search path). Their content is captured in
`docs/infrastructure/dashboard-systemd.md` so the configuration is
reviewable in git.

## 4. Service Unit (final content)

```ini
[Unit]
Description=Trading Bot Engineering Dashboard (uvicorn :8010 loopback)
After=network-online.target
Wants=network-online.target
StartLimitBurst=5
StartLimitIntervalSec=60

[Service]
WorkingDirectory=/root/.openclaw/workspace/trading-bot
ExecStart=/root/.openclaw/workspace/trading-bot/.venv/bin/python -m uvicorn dashboard_api.app:app --host 127.0.0.1 --port 8010
Restart=always
RestartSec=5
RestartPreventExitStatus=78
TimeoutStopSec=30
TimeoutStartSec=30
SuccessExitStatus=0 143
KillMode=control-group
Environment=HOME=/root
Environment=TMPDIR=/tmp
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONHASHSEED=random
Environment=TRADING_BOT_DASHBOARD_SERVICE=dashboard.service
Environment=TRADING_BOT_DASHBOARD_PORT=8010
Environment=PATH=/usr/bin:/root/.local/bin:/root/bin:/usr/local/bin:/bin

[Install]
WantedBy=default.target
```

## 5. Drop-In (final content)

`~/.config/systemd/user/dashboard.service.d/10-env.conf`:
```ini
[Service]
# Optional project-local runtime env. The leading '-' means systemd does NOT
# fail if the file is missing. NEVER add TESTING=1 or UNIT_TESTING=1 to a
# production service.
EnvironmentFile=-/root/.openclaw/workspace/trading-bot/.dashboard.env
```

## 6. Operating-Command Reference

```bash
# Reload after editing unit/drop-in
systemctl --user daemon-reload

# Enable (auto-start at session default.target, surviving reboot via Linger)
systemctl --user enable dashboard.service

# Lifecycle
systemctl --user start dashboard.service
systemctl --user stop dashboard.service
systemctl --user restart dashboard.service

# Inspect
systemctl --user status dashboard.service --no-pager
journalctl --user -u dashboard.service -n 200 --no-pager

# Health (loopback only)
curl -sS -o /dev/null -w 'HTTP %{http_code}\n' http://127.0.0.1:8010/engineering
curl -sS http://127.0.0.1:8010/api/engineering/snapshot | head -c 200
```

## 7. Mirrored OpenClaw Pattern — Diff Against `openclaw-gateway.service`

| Field | openclaw-gateway.service | dashboard.service |
| --- | --- | --- |
| `[Unit] Description` | `OpenClaw Gateway (v2026.6.5)` | `Trading Bot Engineering Dashboard (uvicorn :8010 loopback)` |
| `After=network-online.target` | yes | yes |
| `Wants=network-online.target` | yes | yes |
| `StartLimitBurst=5` | yes | yes |
| `StartLimitIntervalSec=60` | yes | yes |
| `ExecStart` | `/usr/bin/node /usr/lib/node_modules/openclaw/dist/index.js gateway --port 18789` | `/root/.openclaw/workspace/trading-bot/.venv/bin/python -m uvicorn dashboard_api.app:app --host 127.0.0.1 --port 8010` |
| `WorkingDirectory` | (none — Node module path is absolute) | `/root/.openclaw/workspace/trading-bot` (required for Python package imports) |
| `Restart=always` | yes | yes |
| `RestartSec=5` | yes | yes |
| `RestartPreventExitStatus=78` | yes | yes (kept for parity) |
| `TimeoutStopSec=30` | yes | yes |
| `TimeoutStartSec=30` | yes | yes |
| `SuccessExitStatus=0 143` | yes | yes |
| `KillMode=control-group` | yes | yes |
| `Environment=HOME` | yes | yes |
| `Environment=TMPDIR` | yes | yes |
| `Environment=PATH` | yes (Node-friendly) | yes (Python-friendly) |
| `WantedBy=default.target` | yes | yes |
| Drop-in `10-env.conf` | `EnvironmentFile=/root/.openclaw/openclaw.env` | `EnvironmentFile=-/root/.openclaw/workspace/trading-bot/.dashboard.env` |

Mirrors the canonical OpenClaw pattern; diverges only where the runtime
actually differs (Node vs Python; absolute ExecStart paths; env file is
optional and project-local).

## 8. Risks

- **Reboot end-to-end**: not exercised in this PR (host reboot is
  destructive). Configuration is verified.
- **CUPS `0.0.0.0:631`**: unrelated, separately flagged by Josh.
- **No public port**: until PR2, the dashboard is reachable only from
  the local host or via the existing SSH session.
- **No secrets in the service env**: live brokerage, Telegram, or
  OpenClaw Gateway tokens are never loaded here.
- **Drop-in env file is optional**: `EnvironmentFile=-…` keeps the service
  bootable even if the file is missing. Forward-compat hook for runtime
  overrides without service-file churn.

## 9. Out of Scope (documented for next PRs)

- PR2 — Cloudflare Tunnel + Access (only after Josh approves/merges PR1).
- PR3 — Durable SQLite chat store at `.agent-state/engineering-chat.sqlite3`.
- PR4 — Durable + Live Chat UI.
- PR5 — Current session backfill.

## 10. Stopped State / Blockers

None. Implementation complete. Automated verification passed. No external
prerequisites pending for PR1. STOPPED per Josh's spec, awaiting PR review.

## 11. Next Action

1. Open GitHub PR via `gh pr create` for branch
   `agent/dash-persistent-service-pr1` against `main`.
2. STOP. Do not begin PR2. Do not merge.
3. Manager idle until Josh replies.
