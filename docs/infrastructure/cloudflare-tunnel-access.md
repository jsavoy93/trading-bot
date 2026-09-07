# Cloudflare Tunnel + Access for the Engineering Dashboard (PR2)

The Engineering Dashboard is exposed to Josh's iPhone over a single HTTPS
URL via a Cloudflare Tunnel protected by Cloudflare Access. The tunnel
is **egress-only** — no inbound firewall port, no `0.0.0.0` bind, no
public uvicorn port.

```
  iPhone → Cloudflare Edge (Access auth, HTTPS)
         → Cloudflare Tunnel (egress-only outbound connection)
         → http://127.0.0.1:8010  (dashboard_api.app on the host)
```

## Status of PR2 (this PR)

PR2 implements everything that can be automated without Josh's input:

- **cloudflared binary** — installed (`cloudflared version 2026.8.3`).
- **cloudflared systemd service** — `~/.config/systemd/user/cloudflared.service`
  + drop-in `10-env.conf`, mirrors the dashboard.service pattern.
  Loaded; **not enabled/started** because there is no tunnel yet.
- **Dashboard security wiring** —
  - `dashboard_api/security.py` (CloudflareAccessValidator, WriteOriginGuard,
    InProcessRateLimiter, SecuritySettings, request_is_from_loopback).
  - `dashboard_api/app.py` wires these in via FastAPI dependencies on
    every route, plus a new `/healthz` route that bypasses Access but
    requires a direct loopback client.
  - 53 focused security tests pass; 174 existing dashboard tests pass;
    883 full safe suite still green.
- **Config knob** — `DASHBOARD_CF_ACCESS_ENFORCEMENT` (`disabled` |
  `local-bypass` | `always`; default `local-bypass` for production).
  The dashboard service env will be updated when Josh completes the
  manual steps below; until then it stays at `disabled` (no Access
  required, since the only listener is loopback).

PR2 **does NOT** verify:

- The end-to-end HTTPS URL (Cloudflare account / domain not yet
  provisioned on this host — see "Manual Action Required" below).
- Real Access JWT validation against a production team (certs endpoint
  not reachable yet; tests use a local JWKS fixture).

Both verifications will run in the next iteration once Josh completes
the manual steps and the manager restarts the cloudflared service.

## Architecture (concrete)

| Component | Location | Bound to |
| --- | --- | --- |
| uvicorn dashboard | `~/.config/systemd/user/dashboard.service` | `127.0.0.1:8010` |
| cloudflared tunnel | `~/.config/systemd/user/cloudflared.service` | outbound TCP/443 only |
| Cloudflare Access | `https://<team>.cloudflareaccess.com` | HTTPS at edge |
| Tunnel credential file | `/root/.cloudflared/<TUNNEL_ID>.json` (chmod 0600) | filesystem only |
| Tunnel config | `/root/.cloudflared/config.yml` | filesystem only |
| Dashboard runtime env | `.dashboard.env` (chmod 0600, gitignored) | filesystem only |
| Tunnel runtime env | `.cloudflared.env` (chmod 0600, gitignored) | filesystem only |

No secrets are committed. The dashboard never sees broker credentials,
Telegram tokens, or Cloudflare API keys.

## App-Side Access Validation

`dashboard_api/security.py:CloudflareAccessValidator` validates the
`Cf-Access-Jwt-Assertion` header against Cloudflare's published JWKS:

1. Fetches `https://{team_domain}/cdn-cgi/access/certs` on first use;
   bounded TTL cache (1h).
2. Verifies signature with the key whose `kid` matches the JWT header.
3. Validates `iss == https://{team_domain}`.
4. Validates `aud == {configured audience}`.
5. Validates `exp` / `iat`.
6. Returns a bounded `JwtValidationResult(valid, status, email)` —
   the JWT itself and raw claims are never logged.

Algorithm allow-list is `("RS256",)` only — `HS256` and any other
algorithm is rejected.

## Write Protection

`WriteOriginGuard` rejects `POST / PUT / PATCH / DELETE` whose
`Origin` (or fallback `Referer`) host does not match the configured
`DASHBOARD_PUBLIC_HOSTNAMES` allow-list. Configurable per host (e.g.,
`dashboard.example.com` allows any subdomain). Same-origin requests
from the public hostname pass; requests without an Origin or Referer
are rejected by default (configurable via
`DASHBOARD_ALLOW_MISSING_ORIGIN=true` for curl-style direct hits).

`/api/engineering/chat/send` additionally enforces:

- **Existing 4,000-char bound** — preserved verbatim from PR #67.
- **Existing non-text rejection** — preserved verbatim.
- **Bounded in-process rate limit** —
  `DASHBOARD_CHAT_SEND_RATE_LIMIT=10`,
  `DASHBOARD_CHAT_SEND_RATE_WINDOW_SECONDS=60`. 429 + `Retry-After`.

## Loopback-Only Health Path

`/healthz` returns 200 only when `request.client.host in {127.0.0.1,
::1}` **and** the request has no `Cf-Connecting-Ip` header (the latter
distinguishes a true direct request from a tunneled one). External
requests get 403. The response body is bounded JSON and never includes
JWT, claims, or paths.

## Restart / Reboot Survival

`cloudflared.service` uses `Restart=always`, `RestartSec=5`,
`StartLimitBurst=5/60s`, `WantedBy=default.target`, and the root user
already has `Linger=yes`. The same empirical SIGKILL-then-restart
verification done for dashboard.service in PR1 will be repeated for
cloudflared.service once the tunnel is provisioned.

## Manual Action Required (Josh)

Until PR2's manual step is complete, the dashboard is reachable only
from `127.0.0.1:8010` on the host or via an existing SSH session —
no public port is exposed. Cloudflare Tunnel is the **only** intended
external path.

The following steps require Josh's account. After Josh completes them,
the manager will:

1. Place the tunnel credentials file under `/root/.cloudflared/` (chmod 0600).
2. Write `/root/.cloudflared/config.yml` from the values Josh supplies.
3. Enable + start `cloudflared.service`.
4. Re-run the dashboard service with the real
   `DASHBOARD_CF_ACCESS_TEAM_DOMAIN` and `DASHBOARD_CF_ACCESS_AUDIENCE`.
5. Verify end-to-end: external HTTPS URL → 401 (no JWT) → Access login →
   200 (valid JWT); bad JWT → 401; loopback `/healthz` → 200.

### Step 1 — Cloudflare account and domain

1. Sign in to <https://dash.cloudflare.com/> (create an account if you
   don't already have one).
2. If you don't yet have a domain on Cloudflare:
   - **If you already own a domain** through any registrar, click
     **Add a Site**, enter the domain (e.g. `yourdomain.com`), and
     follow the wizard to update the registrar's nameservers to the
     two Cloudflare-assigned nameservers. Wait for DNS to propagate
     (a few minutes to 24h).
   - **If you do not own a domain yet**, you'll need to register one
     before Access can be wired up. Cloudflare Registrar (the
     "Register Domains" tab) is the easiest path. The manager does
     **not** purchase domains — that requires your explicit action.
3. Once the domain shows "Active" in the Cloudflare dashboard, note
   the **Team domain** (also called the Cloudflare Zero Trust
   organization URL), e.g. `yourteam.cloudflareaccess.com`. This is
   the value of `DASHBOARD_CF_ACCESS_TEAM_DOMAIN`.

### Step 2 — Authorize cloudflared on the host

1. On the host, run:
   ```bash
   cloudflared tunnel login
   ```
   This prints a URL and opens a browser to the Cloudflare dashboard.
   Pick the domain you added in Step 1, click **Authorize**. A file
   `/root/.cloudflared/cert.pem` is written. Mode 0600.

### Step 3 — Create the tunnel

1. Run:
   ```bash
   cloudflared tunnel create dashboard
   ```
   This prints a **Tunnel UUID** and writes a credentials file at
   `/root/.cloudflared/<TUNNEL_ID>.json` (mode 0600). Note the UUID —
   you'll need it in Step 5.

### Step 4 — Create DNS record

1. Run:
   ```bash
   cloudflared tunnel route dns dashboard dashboard.yourdomain.com
   ```
   This creates the CNAME record in Cloudflare automatically.

### Step 5 — Configure the tunnel

1. Create `/root/.cloudflared/config.yml` (mode 0600) with:
   ```yaml
   tunnel: <TUNNEL_UUID_FROM_STEP_3>
   credentials-file: /root/.cloudflared/<TUNNEL_ID>.json

   ingress:
     - hostname: dashboard.yourdomain.com
       service: http://127.0.0.1:8010
     - service: http_status:404
   ```
2. Add `dashboard.yourdomain.com` to
   `DASHBOARD_PUBLIC_HOSTNAMES` in `/root/.openclaw/workspace/trading-bot/.dashboard.env`
   (chmod 0600).

### Step 6 — Create the Cloudflare Access Application

1. In the Cloudflare dashboard: **Zero Trust → Access → Applications**.
2. Click **Add an Application → Self-hosted**.
3. Name: `Engineering Dashboard`.
4. Domain: `dashboard.yourdomain.com`.
5. Session duration: e.g. 24 hours.
6. **Identity providers**: enable **One-time PIN** (email OTP). If you
   have a Google account you can also enable **Google** under
   *Authentication → Add new → Google* and select it here — Google is
   preferred for daily use.
7. **Policies**: create one policy called `Josh only`. Expression:
   `Email == "josh.savoy93@gmail.com"` (or your actual address).
   Action: **Allow**.
8. After saving, on the application overview page, copy the
   **Application Audience (AUD) Tag**. This is the value of
   `DASHBOARD_CF_ACCESS_AUDIENCE`. It looks like a long hex string.

### Step 7 — Hand back to the manager

Reply to this conversation with:

- `DASHBOARD_CF_ACCESS_TEAM_DOMAIN=yourteam.cloudflareaccess.com`
- `DASHBOARD_CF_ACCESS_AUDIENCE=<the AUD tag>`
- `DASHBOARD_PUBLIC_HOSTNAMES=dashboard.yourdomain.com`
- The path of the tunnel credentials file (default
  `/root/.cloudflared/<TUNNEL_ID>.json` is fine)
- The tunnel UUID (only for logging; it stays in the config file)

The manager will:

- Place the credentials file under `/root/.cloudflared/` (mode 0600).
- Write `/root/.cloudflared/config.yml` from the values above.
- Update `/root/.config/systemd/user/dashboard.service.d/10-env.conf`
  to source the real team domain and audience.
- Enable + start `cloudflared.service`.
- Re-run the dashboard service with `DASHBOARD_CF_ACCESS_ENFORCEMENT=local-bypass`.
- Verify end-to-end:
  - `ss -tlnp` still shows `127.0.0.1:8010` only.
  - External `curl https://dashboard.yourdomain.com/engineering` →
    `307` (Cloudflare Access redirect to login).
  - Authenticated `curl -H "Cf-Access-Jwt-Assertion: <jwt>" ...` → 200.
  - Bad/missing JWT → 401.
  - Write with wrong Origin → 403.
  - Rate limit on `/api/engineering/chat/send` → 429 with `Retry-After`.
  - `curl http://127.0.0.1:8010/healthz` (loopback) → 200.

After Josh's review the PR is merged.

## Out of Scope for PR2

Documented for the next iterations:

- PR3 — Durable SQLite chat store at `.agent-state/engineering-chat.sqlite3`.
- PR4 — Durable + Live Chat UI (history pagination + live reconcile).
- PR5 — Current session backfill (one-shot, idempotent).
- Unrelated CUPS `0.0.0.0:631` exposure (Josh's spec flagged as separate
  follow-up).
- Cloudflare-side rate limiting / WAF (account-plan dependent;
  documented separately if Josh enables Cloudflare WAF).
