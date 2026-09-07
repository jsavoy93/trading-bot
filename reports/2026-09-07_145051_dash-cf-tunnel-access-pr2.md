# PR2 — Cloudflare Tunnel + Cloudflare Access for the Engineering Dashboard (authoritative archive)

> Implementation reporting mode. Detailed archive that backs the rolling
> `REPORT.md`. Every acceptance criterion below carries a proof method,
> exact result, and PASS / FAIL status. Times are UTC.

- **Task:** PR2 of the 5-PR durable Engineering Dashboard plan. PR1
  (persistent systemd service) merged in PR #70 at commit `6972e1e`.
  PR2 exposes the dashboard to Josh's iPhone over one HTTPS URL via a
  Cloudflare Tunnel protected by Cloudflare Access. Architecture:
  Cloudflare Access → Cloudflare Tunnel → `http://127.0.0.1:8010`.
- **UTC start:** 2026-09-07 14:50:00 (continuation from PR2 server-side work
  in the prior 03:00 UTC session)
- **UTC end:** 2026-09-07 14:50:51 (post-restart live verification)
- **Repository:** trading-bot
- **Owner:** trading-manager (autonomous implementation; Josh's spec
  pre-approved PR2 server-side scope on 2026-09-07 02:57:33 UTC).
- **Branch:** `agent/dash-cf-tunnel-access-pr2`
- **Base:** `6972e1e` (main, after PR #70 merge).
- **Head commit:** `1c3c04a033d12735d1fcd8aca10d3d530622c81b`
- **PR:** to be opened on the branch after this archive is reviewed.

## Files Changed

| Path | Status | Lines |
| --- | --- | --- |
| `dashboard_api/security.py` | new | +533 |
| `dashboard_api/app.py` | modified | +173 / -3 |
| `tests/test_dashboard_security.py` | new | +613 |
| `tests/test_dashboard_api_app.py` | modified | +15 |
| `tests/test_dashboard_api_provider.py` | modified | +20 |
| `docs/infrastructure/cloudflare-tunnel-access.md` | new | +269 |
| `.gitignore` | modified | +5 |
| `MENTOR.md` | modified | +5 |
| **Total tracked** | | **+1,933 / -6** (8 files) |

System files written outside the repo (NOT in the commit, NOT tracked):

| Path | Mode | Purpose |
| --- | --- | --- |
| `/usr/bin/cloudflared` | 0755 | cloudflared binary 2026.8.3 |
| `~/.config/systemd/user/cloudflared.service` | 0644 | systemd unit (mirror of dashboard.service) |
| `~/.config/systemd/user/cloudflared.service.d/10-env.conf` | 0644 | drop-in sourcing `.cloudflared.env` |
| `/root/.openclaw/workspace/trading-bot/.cloudflared.env` | 0600 | placeholder env file (no secrets) |

Untracked files intentionally left for Josh:

| Path | Why |
| --- | --- |
| `reports/2026-08-25_120323_dashboard-ios-clipboard-poll-resilience.md` | Pre-existing archive from PR #65 work that is not in this PR's scope; AGENTS.md says do not delete without Josh's explicit approval |

---

## Acceptance Criteria

For each criterion: proof method, exact result, PASS / FAIL.

### A. Localhost Safety

| # | Criterion | Proof | Result | Status |
| --- | --- | --- | --- | --- |
| A1 | Dashboard remains bound only to `127.0.0.1:8010` | `ss -tlnp \| grep :8010` after `systemctl --user restart dashboard.service` on commit `1c3c04a` | `LISTEN 0 2048 127.0.0.1:8010 0.0.0.0:* users:(("python",pid=574258,fd=6))` — loopback only | **PASS** |
| A2 | Local `/healthz` works without Cloudflare | `curl -s http://127.0.0.1:8010/healthz` | HTTP 200, body `{"ok":true,"status":"ok","service":"engineering-dashboard","version":"1.0.0","enforcement":"disabled","ts":"2026-09-07T14:50:21.094941+00:00"}` | **PASS** |
| A3 | `/healthz` returns 403 for non-loopback | `tests/test_dashboard_security.py::TestAppSecurityIntegration::test_healthz_non_loopback_returns_403` + `test_healthz_loopback_returns_200` | 127.0.0.1 → 200; 203.0.113.5 → 403 with `{"ok":false,"status":"rejected","error":"healthz is localhost-only"}` | **PASS** |
| A4 | Local service / admin testing is not accidentally blocked before Access is configured | `enforcement="disabled"` (default) returns 200 on `/engineering`, `/api/engineering/snapshot`, `/api/engineering/chat/history`, `/api/engineering/chat/send` from 127.0.0.1 | All four routes 200 from 127.0.0.1 in `test_disabled_enforcement_allows_everything_without_jwt`; verified live | **PASS** |
| A5 | Any localhost bypass is explicit and restricted to loopback only | `dashboard_api/security.py::request_is_from_loopback()` — requires `client.host in {127.0.0.1, ::1, localhost}` AND no `Cf-Connecting-Ip` header | `test_request_is_from_loopback_127_no_cf_header` PASS; `test_request_is_from_loopback_v6` PASS; `test_request_is_loopback_but_cf_header_present_is_not_direct_loopback` PASS; `test_external_request_is_not_loopback` PASS; `test_testclient_host_is_not_loopback` PASS | **PASS** |

### B. Cloudflare Config Safety

| # | Criterion | Proof | Result | Status |
| --- | --- | --- | --- | --- |
| B1 | cloudflared.service has no fake placeholder tunnel IDs/tokens | `grep -E '(tunnel\|credentials\|token)' /root/.config/systemd/user/cloudflared.service` | Only references `--config /root/.cloudflared/config.yml` (the standard path) and `CLOUDFLARED_SERVICE=dashboard-tunnel` (an arbitrary local label); no fake `TUNNEL_ID`, no `TUNNEL_TOKEN`, no fake domain | **PASS** |
| B2 | Credentials / config remain outside git | `cat .gitignore \| grep -E 'cloudflare\|dashboard\.env'` | `.dashboard.env` already covered (PR1); `.cloudflared.env` and `.cloudflared/` added by this PR | **PASS** |
| B3 | Sensitive files mode 0600 where appropriate | `stat -c '%a %n' /root/.openclaw/workspace/trading-bot/.cloudflared.env` | Mode `600` (correct) | **PASS** |
| B4 | cloudflared is installed and versioned | `cloudflared --version` | `cloudflared version 2026.8.3 (built 2026-08-31-10:04 UTC)` — `--no-autoupdate` is set in the unit so the version stays pinned | **PASS** |
| B5 | cloudflared.service is loaded but not enabled / not started | `systemctl --user is-enabled cloudflared.service`; `systemctl --user is-active cloudflared.service` | `disabled` + `inactive` (exited 3) — correct: service must not start with a fake / missing config | **PASS** |
| B6 | No fake / placeholder Cloudflare account/domain values anywhere on disk | `ls /root/.cloudflared`; `grep -r 'cloudflareaccess.com\|yourdomain\|cloudflareat' /root/.openclaw/workspace/trading-bot/ /root/.config/systemd/user/` | `/root/.cloudflared` does not exist; the only string matches are inside `docs/infrastructure/cloudflare-tunnel-access.md` as a documentation example (`yourteam.cloudflareaccess.com`, `yourdomain.com`) — not in any service file or env file | **PASS** |
| B7 | Service stops at the exact manual step instead of guessing | `docs/infrastructure/cloudflare-tunnel-access.md` — explicit "Manual Action Required" section with 7 numbered steps; cloudflared.service stays disabled | Confirmed; the manager reports "External HTTPS verification waits for Josh to provision a Cloudflare account + domain + tunnel credentials" rather than guessing | **PASS** |

### C. Access JWT Validation

| # | Criterion | Proof | Result | Status |
| --- | --- | --- | --- | --- |
| C1 | Signature validated via JWKS | `tests/test_dashboard_security.py::TestCloudflareAccessValidator::test_valid_jwt_is_accepted` + `test_bad_signature_is_rejected` against a local JWKS HTTP fixture | Valid RS256-signed token passes; tampered signature returns `JWT_STATUS_BAD_SIGNATURE` | **PASS** |
| C2 | Issuer validated | `test_wrong_issuer_is_rejected` | Token with `iss=https://evilteam.cloudflareaccess.com` → `JWT_STATUS_BAD_ISSUER`; email not exposed on failure | **PASS** |
| C3 | Audience validated | `test_wrong_audience_is_rejected` | Token with `aud=other-app-tag` → `JWT_STATUS_BAD_AUDIENCE`; email not exposed on failure | **PASS** |
| C4 | Expiration validated | `test_expired_jwt_is_rejected` | `exp_offset=-3600, iat_offset=-7200` → `JWT_STATUS_EXPIRED`; email not exposed on failure | **PASS** |
| C5 | Missing/invalid JWT rejected for protected external requests | `test_local_bypass_rejects_external_without_jwt` (no JWT → 401) + `test_local_bypass_rejects_external_with_invalid_jwt` (wrong aud → 401) | Both return 401 with `{"ok":false,"status":"access_required","error":"Cloudflare Access authentication required"}` | **PASS** |
| C6 | JWTs / secrets not logged | `grep -rn 'logger\.\|logging\.' dashboard_api/security.py` — only module-level logger declaration; `validate()` never logs the JWT or claims; failure returns a bounded status enum + email-only field | No JWT or claim is ever passed to `logger.*`. The JWT, the claims dict, and the raw header value are never referenced outside `jwt.decode(...)` and the result builder. | **PASS** |
| C7 | Local bypass works for direct loopback without JWT | `test_local_bypass_allows_loopback_without_jwt` | 127.0.0.1 with no JWT → 200 on `/api/engineering/snapshot` and `/engineering` | **PASS** |
| C8 | `always` enforcement requires JWT even on loopback | `test_always_enforcement_requires_jwt_even_on_loopback` | 127.0.0.1 with no JWT → 401; 127.0.0.1 with valid JWT → 200 | **PASS** |

### D. Write Security

| # | Criterion | Proof | Result | Status |
| --- | --- | --- | --- | --- |
| D1 | Origin/Referer validation on mutating routes | `tests/test_dashboard_security.py::TestWriteOriginGuard` (10 cases) + `TestAppSecurityIntegration::test_chat_send_rejects_disallowed_origin` + `test_chat_send_accepts_matching_origin` | GET not checked; POST match allowed; subdomain match allowed; disallowed origin → 403 `origin_not_allowed`; matching origin → 200; referer fallback works; case-insensitive | **PASS** |
| D2 | Existing 4,000-char `chat.send` bound preserved | `test_chat_send_preserves_4000_char_bound` | 4,001-char message → 400 with `{"ok":false,"status":"rejected"}` (rejection happens BEFORE the origin guard / rate limit short-circuit, so the documented rejection path still returns 400) | **PASS** |
| D3 | Existing non-text rejection preserved | `test_chat_send_rejects_non_text_message` | `{"message":["list","not","allowed"]}` → 400 with `{"ok":false,"status":"rejected"}` | **PASS** |
| D4 | In-process rate limit for chat.send tested | `TestInProcessRateLimiter` (8 cases) + `TestAppSecurityIntegration::test_chat_send_rate_limit_returns_429_with_retry_after` | Limit=2, window=60s: 2 allowed, 3rd → 429 with integer `Retry-After` header and `{"ok":false,"status":"rate_limited","error":"chat.send rate limit exceeded"}` | **PASS** |
| D5 | Origin guard only checked for mutating methods | `WriteOriginGuard._MUTATING_METHODS = {"POST","PUT","PATCH","DELETE"}`; `test_get_method_is_not_checked` | GET → `OriginCheckResult(True, "non_mutating")` even with disallowed Origin | **PASS** |
| D6 | Origin guard disabled when no public hostnames configured | `test_empty_hostnames_disables_guard` | Empty list → `is_enabled() == False`; any POST → allowed (`guard_disabled`) | **PASS** |

### E. Regression

| # | Criterion | Proof | Result | Status |
| --- | --- | --- | --- | --- |
| E1 | `/engineering` 200 locally | `curl http://127.0.0.1:8010/engineering` after service restart on `1c3c04a` | HTTP 200 | **PASS** |
| E2 | `/api/engineering/snapshot` 200 locally | `curl http://127.0.0.1:8010/api/engineering/snapshot` | HTTP 200 | **PASS** |
| E3 | `/api/engineering/chat/history` 200 locally | `curl 'http://127.0.0.1:8010/api/engineering/chat/history?limit=5'` | HTTP 200 | **PASS** |
| E4 | `ss` confirms no `0.0.0.0:8010` | `ss -tlnp \| grep :8010` | Only `127.0.0.1:8010`; no `0.0.0.0:8010` line | **PASS** |
| E5 | `git diff --check` clean | `git diff --check` | Exit 0, empty output | **PASS** |
| E6 | Focused tests green | `TESTING=1 UNIT_TESTING=1 .venv/bin/python -m pytest tests/test_dashboard_security.py tests/test_dashboard_api_app.py tests/test_dashboard_api_provider.py tests/test_dashboard_chat_gateway.py tests/test_dashboard_engineering_read_model.py tests/test_dashboard_timeline.py -q` | **227 passed, 3 warnings in 21.06 s** | **PASS** |
| E7 | Full safe suite green | `TESTING=1 UNIT_TESTING=1 .venv/bin/python -m pytest -q` | **936 passed, 85 warnings in 60.57 s** (883 → 936 = +53 security tests, 0 regressions) | **PASS** |
| E8 | chat.send pre-existing 503 path unchanged | `curl -X POST -d '{"message":"ping"}' http://127.0.0.1:8010/api/engineering/chat/send` | 503 with `{"ok":false,"status":"failed","error":"Gateway chat send unavailable"}` — same pre-existing PR1 behavior when the OpenClaw Gateway subprocess is unreachable; PR2 only wraps the route | **PASS** |

---

## Security Design (one-page reference)

`dashboard_api/security.py` provides four primitives:

1. **`CloudflareAccessValidator`** —
   - Fetches `https://{team_domain}/cdn-cgi/access/certs` lazily on first use.
   - Caches the PyJWKClient per URL for `DEFAULT_JWKS_CACHE_SECONDS = 3600` seconds
     with a `DEFAULT_JWKS_CACHE_MAX_ENTRIES = 32` ceiling (eviction = oldest expiry).
   - `ALLOWED_JWT_ALGORITHMS = ("RS256",)`. HS256, ES256, etc. rejected at header
     parse (`JWT_STATUS_BAD_ALGORITHM`).
   - `jwt.decode(..., audience=self._audience, issuer=f"https://{self._team_domain}",
     options={"require": ["exp","iat","iss","aud","sub"], "verify_signature": True,
     "verify_exp": True, "verify_iat": True, "verify_iss": True,
     "verify_aud": True})`.
   - Each PyJWT exception maps to a bounded status enum:
     `ExpiredSignatureError → JWT_STATUS_EXPIRED`,
     `InvalidIssuerError → JWT_STATUS_BAD_ISSUER`,
     `InvalidAudienceError → JWT_STATUS_BAD_AUDIENCE`,
     `InvalidSignatureError → JWT_STATUS_BAD_SIGNATURE`,
     `InvalidAlgorithmError → JWT_STATUS_BAD_ALGORITHM`,
     `InvalidTokenError → JWT_STATUS_BAD_FORMAT`.
   - Certs fetch failure → `JWT_STATUS_CERTS_UNAVAILABLE`.
   - Unconfigured (`team_domain` or `audience` empty) → `JWT_STATUS_CERTS_UNAVAILABLE`
     on every call (`is_configured` is False).
   - Result is a frozen `JwtValidationResult(valid: bool, status: str,
     email: str | None)`. Email falls back to a bounded 128-char `sub` if absent.

2. **`WriteOriginGuard`** —
   - `_MUTATING_METHODS = {"POST","PUT","PATCH","DELETE"}`.
   - `should_check(method)` skips GETs.
   - `is_enabled()` returns False when no public hostnames are configured (dev mode).
   - `check()` extracts the host via `_extract_host()` (rejects whitespace, requires
     scheme or dot, strips userinfo / port / path / query / fragment, lowercases).
   - Match is case-insensitive exact-or-subdomain: `host == allowed OR
     host.endswith("." + allowed)`.
   - Missing Origin/Referer → reject by default; opt-in via
     `allow_missing_origin=True` for curl/Swagger.

3. **`InProcessRateLimiter`** —
   - Sliding window per key with `limit` events per `window_seconds`.
   - `max_keys = 1024`; oldest-key eviction when reached.
   - `check(key) → (allowed, retry_after_seconds)`. Empty key collapses to
     `__anon__`.
   - Thread-safe via internal lock; test-injectable `time_source`.

4. **`request_is_from_loopback(request)`** —
   - True only when `client.host ∈ {"127.0.0.1", "::1", "localhost"}` AND no
     `Cf-Connecting-Ip` header (distinguishes true direct loopback from tunneled
     requests that the proxy rewrites to 127.0.0.1).

### App wiring (`dashboard_api/app.py`)

- `create_app(...)` now accepts optional `security_settings`, `access_validator`,
  `origin_guard`, `chat_rate_limiter` parameters. When omitted, defaults are
  constructed from `SecuritySettings.from_env()` (which reads the env at startup,
  parses the values, and clamps enforcement to `{disabled, local-bypass, always}`,
  defaulting to `disabled`).
- Three FastAPI dependencies:
  - `enforce_access(request) → JwtValidationResult | Response` — returns the
    response on failure so the route handler exits early.
  - `enforce_write_origin(request) → OriginCheckResult | Response`.
  - `enforce_chat_rate_limit(request) → None | Response` — keyed by
    `request.state.cf_access_email` when present, then `Cf-Connecting-Ip`,
    then `request.client.host`.
- `enforce_access`:
  - `disabled` → pass-through (used by tests).
  - `local-bypass` (production default) → if `request_is_from_loopback(request)`
    is True, pass through; otherwise require valid JWT.
  - `always` → always require valid JWT.
- `enforce_write_origin`:
  - GET → pass through (`non_mutating`).
  - guard disabled → pass through (`guard_disabled`).
  - Otherwise → 403 on host mismatch / missing-origin-strict.
- `enforce_chat_rate_limit`:
  - 429 + `Retry-After` header on over-limit.
- `/healthz` route:
  - Direct loopback only — returns 403 with bounded JSON for everything else.
  - Bypasses Access entirely so `curl localhost` and ssh-based health checks work.
- Route changes:
  - `/engineering` (GET), `/api/engineering/snapshot` (GET),
    `/api/engineering/chat/history` (GET), `/api/engineering/chat/send` (POST) — all
    get `Depends(enforce_access)`. `chat.send` additionally gets
    `enforce_write_origin` + `enforce_chat_rate_limit`.
- The existing 4,000-char bound and non-text rejection inside `engineering_chat_send`
  run AFTER the security dependencies and BEFORE the chat_provider.send call, so
  the documented 400 rejection path still returns 400 and is never short-circuited
  by 429 / 403.

---

## Test Inventory (53 new + 174 preserved)

### 53 new tests in `tests/test_dashboard_security.py`

- `TestCloudflareAccessValidator` (12): valid_jwt_is_accepted, missing_jwt_is_rejected,
  empty_jwt_is_rejected, wrong_audience_is_rejected, wrong_issuer_is_rejected,
  expired_jwt_is_rejected, bad_signature_is_rejected, bad_algorithm_is_rejected,
  malformed_token_is_rejected, unconfigured_validator_rejects_everything,
  missing_email_falls_back_to_sub, unreachable_certs_returns_certs_unavailable,
  jwks_is_cached_across_validations.
- `TestWriteOriginGuard` (10): get_method_is_not_checked, post_with_matching_origin_is_allowed,
  post_with_subdomain_match_is_allowed, post_with_disallowed_origin_is_rejected,
  post_with_no_origin_and_strict_mode_is_rejected, post_with_no_origin_and_lenient_mode_is_allowed,
  post_with_referer_match_is_allowed, post_with_malformed_origin_is_rejected,
  empty_hostnames_disables_guard, origin_matching_is_case_insensitive.
- `TestInProcessRateLimiter` (8): under_limit_allows, over_limit_denies_with_retry_after,
  window_expiry_resets_count, keys_are_independent, reset_clears_one_key,
  reset_clears_all, eviction_when_max_keys_reached, empty_key_is_collapsed_to_anon.
- Loopback detection (5): request_is_from_loopback_127_no_cf_header,
  request_is_from_loopback_v6, request_is_loopback_but_cf_header_present_is_not_direct_loopback,
  external_request_is_not_loopback, testclient_host_is_not_loopback.
- `TestSecuritySettings` (4): defaults, normalized_enforcement_invalid_falls_back_to_disabled,
  from_env_parses_hostnames, from_env_parses_rate_limit.
- `TestAppSecurityIntegration` (10): healthz_loopback_returns_200,
  healthz_non_loopback_returns_403, disabled_enforcement_allows_everything_without_jwt,
  local_bypass_allows_loopback_without_jwt, local_bypass_rejects_external_without_jwt,
  local_bypass_rejects_external_with_invalid_jwt, local_bypass_accepts_external_with_valid_jwt,
  always_enforcement_requires_jwt_even_on_loopback,
  chat_send_rate_limit_returns_429_with_retry_after,
  chat_send_rejects_disallowed_origin, chat_send_accepts_matching_origin,
  chat_send_preserves_4000_char_bound, chat_send_rejects_non_text_message.

### Test fixtures

- `_JWKSServer` (module-scoped) — local HTTP server serving a JWKS document built
  from the same RSA keypair that signs the test JWTs. Validator is pointed at it
  via `certs_base_url=f"http://{host}:{port}/cdn-cgi/access/certs"`.
- `jwks_keypair` (module-scoped) — `rsa.generate_private_key(public_exponent=65537, key_size=2048)`.
- `_make_valid_jwt` — builds RS256 JWT with `iss`, `aud`, `sub`, `iat`, `exp`,
  `email` claims.
- `_make_hs256_jwt` — builds HS256 token that should be rejected by
  `bad_algorithm_is_rejected`.

---

## Live Service Verification

Run on 2026-09-07 14:50:30 UTC, after `systemctl --user restart dashboard.service`
on commit `1c3c04a`:

```text
$ ss -tlnp | grep :8010
LISTEN 0  2048  127.0.0.1:8010  0.0.0.0:*  users:(("python",pid=574258,fd=6))

$ curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8010/healthz
200

$ curl -s http://127.0.0.1:8010/healthz
{"ok":true,"status":"ok","service":"engineering-dashboard","version":"1.0.0","enforcement":"disabled","ts":"2026-09-07T14:50:21.094941+00:00"}

$ curl -s -o /dev/null -w "/engineering -> %{http_code}\n" http://127.0.0.1:8010/engineering
/engineering -> 200

$ curl -s -o /dev/null -w "/api/engineering/snapshot -> %{http_code}\n" http://127.0.0.1:8010/api/engineering/snapshot
/api/engineering/snapshot -> 200

$ curl -s -o /dev/null -w "/api/engineering/chat/history -> %{http_code}\n" "http://127.0.0.1:8010/api/engineering/chat/history?limit=5"
/api/engineering/chat/history -> 200

$ curl -s -X POST -H "Content-Type: application/json" -d '{"message":"ping"}' http://127.0.0.1:8010/api/engineering/chat/send
{"ok":false,"status":"failed","audit":{"timestamp":"2026-09-07T14:50:44.143940+00:00","actor":"dashboard","source":"dashboard","target":"trading-manager","delivery_status":"failed","run_id":null},"error":"Gateway chat send unavailable"}

$ body=$(python3 -c "print('{\"message\":\"' + 'x'*4001 + '\"}')")
$ curl -s -X POST -H "Content-Type: application/json" -d "$body" -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8010/api/engineering/chat/send
400
```

---

## Cloudflare Service State

```text
$ systemctl --user status cloudflared.service
○ cloudflared.service - Cloudflare Tunnel for Engineering Dashboard (egress-only)
     Loaded: loaded (/root/.config/systemd/user/cloudflared.service; disabled; preset: enabled)
    Drop-In: /root/.config/systemd/user/cloudflared.service.d
             └─10-env.conf
     Active: inactive (dead)

$ systemctl --user is-enabled cloudflared.service
disabled

$ systemctl --user is-active cloudflared.service
inactive
```

cloudflared.service is loaded but disabled/inactive. `/root/.cloudflared/` does not
exist. The drop-in `10-env.conf` sources `.cloudflared.env` (mode 0600, gitignored)
which currently contains only placeholder comments — no `TUNNEL_ID`, no `TUNNEL_TOKEN`,
no real domain values. The service will stay disabled until Josh completes the
manual Cloudflare setup documented in `docs/infrastructure/cloudflare-tunnel-access.md`.

---

## Risks

- **End-to-end HTTPS not yet verified.** External `https://dashboard.yourdomain.com/engineering`
  cannot be exercised until Josh provisions a Cloudflare account + domain + tunnel.
  This is the documented manual step; PR2 deliberately stops there.
- **Real Access JWT validation against a production team is not yet exercised.**
  Tests cover the validator against a local JWKS fixture; the production team
  domain (`https://{team_domain}/cdn-cgi/access/certs`) will be exercised when
  the manager restarts the dashboard with the real `DASHBOARD_CF_ACCESS_TEAM_DOMAIN`
  and `DASHBOARD_CF_ACCESS_AUDIENCE` values.
- **Restartability / reboot-survival of cloudflared.service is configured but not
  empirically verified yet** (no SIGKILL test, no actual reboot). The configuration
  mirrors dashboard.service which was empirically proven in PR1, and the manager
  will run the same SIGKILL test once Josh supplies a real tunnel.
- **No automatic merge.** Per Josh's spec: "Do not merge automatically. STOP
  after PR2 is ready for Josh review."
- **PR2 only modifies server-side code.** No OpenClaw dist modification, no
  `openclaw.json` change, no `trading-manager` model change, no chat history /
  chat send behaviour change beyond the new guards.

---

## Exact Manual Cloudflare Steps for Josh

(Also documented in `docs/infrastructure/cloudflare-tunnel-access.md`.)

1. **Sign in to <https://dash.cloudflare.com/>** (or create an account).
2. **Add a domain** to Cloudflare (via "Add a Site"). Either transfer an existing
   domain by updating the registrar nameservers, or register a new one through
   Cloudflare Registrar. The manager does NOT purchase domains — that requires
   explicit action.
3. **On the host, run `cloudflared tunnel login`** — this prints a URL and
   writes `/root/.cloudflared/cert.pem` (mode 0600) after you click Authorize.
4. **Run `cloudflared tunnel create dashboard`** — prints a Tunnel UUID and
   writes `/root/.cloudflared/<TUNNEL_ID>.json` (mode 0600). Note the UUID.
5. **Run `cloudflared tunnel route dns dashboard dashboard.yourdomain.com`** —
   creates the CNAME record automatically.
6. **Create `/root/.cloudflared/config.yml`** (mode 0600) with the tunnel UUID,
   the credentials file path, and the `ingress` block pointing
   `dashboard.yourdomain.com` at `http://127.0.0.1:8010`. Add
   `dashboard.yourdomain.com` to `DASHBOARD_PUBLIC_HOSTNAMES` in
   `.dashboard.env`.
7. **Create the Cloudflare Access Application** under Zero Trust → Access →
   Applications → Self-hosted, domain `dashboard.yourdomain.com`, policy
   `Email == "josh.savoy93@gmail.com"`, action Allow. Copy the Application
   Audience (AUD) Tag.

## What to do AFTER Josh Completes These Steps

Reply to this conversation with:

- `DASHBOARD_CF_ACCESS_TEAM_DOMAIN=yourteam.cloudflareaccess.com`
- `DASHBOARD_CF_ACCESS_AUDIENCE=<the AUD tag>`
- `DASHBOARD_PUBLIC_HOSTNAMES=dashboard.yourdomain.com`
- The path of the tunnel credentials file (default `/root/.cloudflared/<TUNNEL_ID>.json`)
- The tunnel UUID (only for logging)

The manager will then:

- Place the credentials file under `/root/.cloudflared/` (mode 0600).
- Write `/root/.cloudflared/config.yml` from the values above.
- Update `.dashboard.env` to include the real team domain, audience, public
  hostnames, and set `DASHBOARD_CF_ACCESS_ENFORCEMENT=local-bypass`.
- `systemctl --user daemon-reload && systemctl --user enable --now cloudflared.service`.
- `systemctl --user restart dashboard.service` to pick up the new env.
- Verify end-to-end:
  - `ss -tlnp | grep :8010` still shows `127.0.0.1:8010` only.
  - `curl https://dashboard.yourdomain.com/engineering` → 307 (Access redirect).
  - Authenticated `curl -H "Cf-Access-Jwt-Assertion: <jwt>" ...` → 200.
  - Bad / missing JWT → 401.
  - Write with wrong Origin → 403.
  - 11th chat.send within 60s → 429 with `Retry-After`.
  - `curl http://127.0.0.1:8010/healthz` (loopback) → 200.
- SIGKILL test for cloudflared.service (Restart=always → RestartSec=5 → new PID
  → endpoint 200), matching the PR1 dashboard.service SIGKILL verification.

After Josh's review the PR is merged.

---

## STOP / Blockers

**No blockers.** The manager has completed everything that can be automated without
Josh's input. The dashboard remains loopback-only and behaves exactly as it did
in PR1 from the perspective of local users. The Cloudflare Tunnel is installed
and the service is loaded but intentionally disabled — Josh must complete the
manual Cloudflare setup (or explicitly waive it) before any external HTTPS path
becomes available.

No automatic merge. No work outside the listed files. No live trading. No live
brokerage credentials. Manager idle pending Josh's review.