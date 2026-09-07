# Persistent Engineering Dashboard Service (PR1)

The Engineering Dashboard (`dashboard_api/app.py`, FastAPI/uvicorn) is
managed by a user-level systemd service that mirrors the existing
`openclaw-gateway.service` layout.

## Service Layout

| Path | Purpose |
| --- | --- |
| `~/.config/systemd/user/dashboard.service` | Main unit file |
| `~/.config/systemd/user/dashboard.service.d/10-env.conf` | Drop-in for optional runtime env |
| `/root/.openclaw/workspace/trading-bot/.dashboard.env` | Project-local env file (mode 0600, gitignored) |

## Service Unit

The main unit binds to loopback only (`127.0.0.1:8010`), runs from the repo
working directory, and inherits the same restart / start-limit / kill-mode
discipline as the OpenClaw gateway service:

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

## Drop-In

```ini
[Service]
# Optional project-local runtime env. The leading '-' means systemd does NOT
# fail if the file is missing. NEVER add TESTING=1 or UNIT_TESTING=1 here.
EnvironmentFile=-/root/.openclaw/workspace/trading-bot/.dashboard.env
```

## Operating Commands

```bash
# Reload after editing unit/drop-in
systemctl --user daemon-reload

# Enable (auto-start on session default.target, surviving reboot via Linger)
systemctl --user enable dashboard.service

# Start / stop / restart
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

## Reboot Survival

The root user has `Linger=yes` (`loginctl show-user root | grep Linger`).
With `WantedBy=default.target` and user-level `Linger`, the service starts
automatically when `user@0.service` is started at boot, no interactive login
required.

## Crash Restart

`Restart=always` plus `RestartSec=5`. Verified empirically on 2026-09-07:
SIGKILL of the uvicorn main PID produced a clean restart in 5 s with
`NRestarts` incrementing and the `/engineering` endpoint still returning
HTTP 200 immediately after.

## Loopback-Only Verification

`ss -tlnp | grep :8010` shows `LISTEN ... 127.0.0.1:8010 ... users:(("python",...))`.
The dashboard never binds `0.0.0.0:8010`. PR2 (Cloudflare Tunnel) is the
only intended external path; until then the dashboard is reachable only
from the local host or via the existing SSH session.

## Production Discipline

- `TESTING=1` / `UNIT_TESTING=1` must NEVER appear in the service
  environment; those flags are reserved for the bounded pytest gate.
- The persistent service never holds live brokerage credentials or
  Telegram/Gateway tokens. It reaches the Gateway over loopback and the
  Gateway itself owns the broker credentials.
- No inbound firewall port for 8010. No public uvicorn port.
