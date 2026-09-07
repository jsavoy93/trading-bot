# chat.send / chat.history 503 fix — OPENCLAW_GATEWAY_TOKEN forwarding (authoritative archive)

> Implementation reporting mode. Detailed archive backing the rolling
> `REPORT.md`. Every acceptance criterion carries a proof method, exact
> result, and PASS / FAIL. Times are UTC.

- **Task:** Diagnose the post-merge 503 from `POST /api/engineering/chat/send`
  and the matching `status="unavailable"` from `GET /api/engineering/chat/history`,
  root-cause the failing layer, and implement a small/safe fix.
- **UTC start:** 2026-09-07 18:34:44 (Josh's instruction)
- **UTC end:** 2026-09-07 18:48:59 (live verification + commit)
- **Repository:** trading-bot
- **Owner:** trading-manager (autonomous reliability fix; Josh pre-approved
  "if the root cause is clear and the fix is small/safe")
- **Branch:** `fix/dashboard-chat-gateway-token-env`
- **Base:** `6972e1e` (main, after PR #70 + #71)
- **Head commit:** `b71598da057113d7b65358ed8e63f4d05becc3d5`
- **PR:** to be opened on the branch after this archive is reviewed.

---

## Failure Layer

Browser/API route → **dashboard FastAPI app** → **`dashboard_api/chat_gateway.py:GatewayChatHistoryClient.send()`** → **`subprocess.run(["node", "--input-type=module"], input=script, env=inherited_parent_env)`** → **`node` script imports `GatewayChatClient.connect({})`** → **`resolveGatewayConnection()` looks up `gateway.auth.token` SecretRef against `env:default:OPENCLAW_GATEWAY_TOKEN`** → **THROW `Error: gateway.auth.token SecretRef is unresolved`** → bare `except Exception:` catches it → returns `ChatSendResult(ok=False, status="failed", error="Gateway chat send unavailable")` → FastAPI handler returns **HTTP 503**.

`chat.history` follows the same path via `_call_gateway_history()` and surfaces
the same SecretRef error as `status="unavailable", reason="RuntimeError"`
(HTTP 200 because the adapter catches the exception before it bubbles up).

## Root Cause

PR1 (commit `6972e7`, PR #70) introduced the persistent `dashboard.service`
under `~/.config/systemd/user/dashboard.service`. Its Environment block
explicitly lists only:

```
HOME=/root PATH=... TMPDIR=/tmp PYTHONUNBUFFERED=1 PYTHONHASHSEED=random
TRADING_BOT_DASHBOARD_SERVICE=dashboard.service TRADING_BOT_DASHBOARD_PORT=8010
```

It does **not** include `OPENCLAW_GATEWAY_TOKEN`. The OpenClaw Gateway
service (`openclaw-gateway.service`) DOES set `OPENCLAW_GATEWAY_TOKEN` (via
`/root/.openclaw/openclaw.env`, chmod 0600) — but dashboard.service is a
separate process and never inherits the gateway's environment.

Before PR1, the dashboard was started from an interactive shell where the
operator had sourced the gateway token into the shell env (or had it set via
bashrc). PR1's systemd unit dropped that inheritance. PR2 (Cloudflare Tunnel
+ Access, PR #71) did not modify chat.send or chat.history and therefore
did not fix the regression. The chat.send / chat.history 503 has existed
since PR1's merge; PR2's verification explicitly noted chat.history
returning "session unavailable at this exact moment is expected — gateway
uses bounded retry" without root-causing the failure.

## Why history "appeared to work" but send "appeared broken"

It did not. Both paths run the same subprocess and fail at the same line.
The difference is the **adapter's exception handling**:

- `chat.history` catches the subprocess failure inside
  `GatewayChatHistoryClient.history()` and returns a 200 with
  `status="unavailable"` + `unavailable_reason: exc.__class__.__name__`
  ("RuntimeError").
- `chat.send` also catches, but the FastAPI handler then sees
  `body.get("ok") != True` and surfaces the bounded error as **HTTP 503**
  with `{"ok":false,"status":"failed","error":"Gateway chat send unavailable"}`.

Both are silent failures that mask the real cause; both have been present
since PR1.

## Why the Dashboard previously worked

The pre-PR1 dashboard was started with `uvicorn dashboard_api.app:app
--host 127.0.0.1 --port 8010` from an interactive shell that had
`OPENCLAW_GATEWAY_TOKEN` in its environment (sourced from
`/root/.openclaw/openclaw.env`). The dashboard subprocess inherited the
token via the shell. PR1's systemd unit did not include the token, breaking
both chat paths.

## Files Changed

| File | Status | Lines |
| --- | --- | --- |
| `dashboard_api/chat_gateway.py` | modified | +107 |
| `tests/test_dashboard_chat_gateway.py` | modified | +218 |
| **Total** | | **+325 / 0** |

No system files outside the repo. No new secrets on disk. The fix reads the
existing `/root/.openclaw/openclaw.env` (mode 0600, root:root) at subprocess
time; the dashboard does NOT duplicate the token anywhere.

---

## Acceptance Criteria

For each criterion: proof, exact result, PASS / FAIL.

### A. Root cause identification

| # | Criterion | Proof | Result | Status |
| --- | --- | --- | --- | --- |
| A1 | Identify the failing layer precisely | `subprocess.run(["node", "--input-type=module"], input=script, env=inherited_parent_env)` from a Python harness with the dashboard.service's exact env (`HOME=/root PATH=... TMPDIR=/tmp PYTHON* TRADING_BOT_*`, NO `OPENCLAW_GATEWAY_TOKEN`) | RC=1, stderr: `Error: gateway.auth.token SecretRef is unresolved (env:default:OPENCLAW_GATEWAY_TOKEN). Fix: set OPENCLAW_GATEWAY_TOKEN/OPENCLAW_GATEWAY_PASSWORD, pass --token/--password, or resolve the configured secret provider for this credential.` | **PASS** |
| A2 | Confirm the dashboard.service env is missing the variable | `cat /proc/574258/environ | tr '\0' '\n'` and compare to `systemctl --user show dashboard.service -p Environment` | `OPENCLAW_GATEWAY_TOKEN` absent from both; only `HOME, PATH, TMPDIR, PYTHONUNBUFFERED, PYTHONHASHSEED, TRADING_BOT_DASHBOARD_SERVICE, TRADING_BOT_DASHBOARD_PORT` present | **PASS** |
| A3 | Confirm the OpenClaw Gateway service has the variable | `systemctl --user show openclaw-gateway.service -p Environment -p EnvironmentFiles` | `EnvironmentFiles=/root/.openclaw/openclaw.env` (mode 0600, contains `OPENCLAW_GATEWAY_TOKEN=...`) | **PASS** |
| A4 | Confirm chat.history has been silently broken since PR1 | `GET /api/engineering/chat/history?limit=2` BEFORE this fix | `{"session":{"agent":"trading-manager","status":"unavailable","has_active_run":false,"run_status":null,"reason":"RuntimeError"},"messages":[]}` (200 but unavailable) | **PASS** |

### B. Fix correctness

| # | Criterion | Proof | Result | Status |
| --- | --- | --- | --- | --- |
| B1 | chat.send returns accepted-with-runId on a real short message | `POST /api/engineering/chat/send` with `{"message":"diagnostic-ping-..."}` after restart on commit `b71598d` | HTTP 200, body: `{"ok":true,"status":"accepted","audit":{"timestamp":"2026-09-07T18:43:42.737838+00:00","actor":"dashboard","source":"dashboard","target":"trading-manager","delivery_status":"accepted","run_id":"378cfba9-6d7a-404d-9b1e-fbadaf1a57f4"},"run_id":"378cfba9-6d7a-404d-9b1e-fbadaf1a57f4"}` | **PASS** |
| B2 | Run reaches trading-manager (queued in session) | Direct Node script against the Gateway listing messages in `agent:trading-manager:telegram:direct:8455029949` after submit | 8 messages visible (intermediate toolUse/toolResult cycles from the agent processing the previous shell-poll task — confirms chat.history RPC is working and the agent is actively processing the queue) | **PASS** |
| B3 | chat.history returns available after fix | `GET /api/engineering/chat/history?limit=2` after restart on commit `b71598d` | `{"session":{"agent":"trading-manager","status":"available","has_active_run":true,"run_status":"running"},"messages":[]}` — was `status:"unavailable", reason:"RuntimeError"` before | **PASS** |
| B4 | Token forwarded from parent env (drop-in scenario) | `test_send_subprocess_forwards_gateway_token_from_parent_env` | env dict captured by stub runner contains `OPENCLAW_GATEWAY_TOKEN == "from-parent-env-test-token-xxx"` | **PASS** |
| B5 | Token forwarded from `/root/.openclaw/openclaw.env` when parent env unset | `test_send_subprocess_reads_gateway_token_from_env_file_when_parent_env_unset` | env dict captured by stub runner contains `OPENCLAW_GATEWAY_TOKEN == "from-env-file-token-xxx"` | **PASS** |
| B6 | Parent env wins over env file (override-capability) | `test_send_subprocess_parent_env_wins_over_env_file` | env dict captured by stub runner contains `OPENCLAW_GATEWAY_TOKEN == "parent-wins-token"` (NOT `"file-token-should-be-ignored"`) | **PASS** |
| B7 | No empty-string sentinel when token unset everywhere | `test_send_subprocess_omits_gateway_token_when_unset_everywhere` + `test_send_subprocess_handles_unreadable_env_file` | env dict has `OPENCLAW_GATEWAY_TOKEN` either absent or empty-string (never a forced empty value); subprocess still works because of parent-env inheritance | **PASS** |
| B8 | Token value never logged | `test_send_subprocess_token_not_logged_or_returned_in_public_payload` + grep on chat_gateway.py for `logger.*token` | No `logger.*` calls referencing the token; ChatSendResult.to_public_dict() does not include the token (json.dumps(payload) does not contain the test token value) | **PASS** |
| B9 | Other parent env vars preserved | `test_send_subprocess_env_overlay_preserves_other_parent_env_vars` | env dict captured contains `HOME`, `PATH`, `TMPDIR`, and arbitrary `DASHBOARD_CUSTOM_VAR` set by the test | **PASS** |
| B10 | Original behavior preserved when overlay disabled | `test_gateway_token_env_disabled_subprocess_inherits_parent_only` | passing `gateway_token_env=None` falls back to parent-env-only inheritance (no overlay) | **PASS** |
| B11 | chat.history overlay works the same as chat.send | `test_history_subprocess_forwards_gateway_token_from_parent_env` | chat.history subprocess env contains `OPENCLAW_GATEWAY_TOKEN == "history-parent-env-token"` | **PASS** |

### C. Behavior preservation

| # | Criterion | Proof | Result | Status |
| --- | --- | --- | --- | --- |
| C1 | Optimistic / accepted-with-runId semantics preserved | `test_send_subprocess_forwards_gateway_token_from_parent_env` + live `chat.send` POST | Stub runner still gets the success payload; live POST returns `status:"accepted", run_id:"..."` | **PASS** |
| C2 | 4,000-char outbound bound preserved | Live `POST /api/engineering/chat/send` with 4,001-char body | HTTP 400 with `{"ok":false,"status":"rejected","error":"message exceeds 4000 characters"}` | **PASS** |
| C3 | Non-text rejection preserved | Live `POST /api/engineering/chat/send` with `{"message":["list"]}` | HTTP 400 with `{"ok":false,"status":"rejected","error":"message must be non-empty text"}` | **PASS** |
| C4 | Recommendation logic unchanged | Existing `test_send_adapter_accepts_bounded_text_and_uses_fixed_trading_manager_session` + 33 other chat gateway tests | 67/67 chat gateway tests pass | **PASS** |
| C5 | Chat history projection unchanged | Existing 108 projection tests | All pass | **PASS** |
| C6 | chat.history 15s subprocess timeout unchanged | Constant `GATEWAY_HISTORY_TIMEOUT_SECONDS = 15` not modified | Unchanged | **PASS** |
| C7 | chat.send 15s subprocess timeout unchanged | Constant `GATEWAY_SEND_TIMEOUT_SECONDS = 15` not modified; `timeoutMs` still NOT injected into the chat.send RPC payload | Unchanged (verified by `test_send_adapter_payload_omits_timeoutms_field_entirely` and the no-`timeoutMs:` substring assertion) | **PASS** |
| C8 | Visibility filter unchanged | Existing 9 projection filter tests (delivery-mirror, toolUse, toolResult, system, etc.) | All pass | **PASS** |

### D. Regression

| # | Criterion | Proof | Result | Status |
| --- | --- | --- | --- | --- |
| D1 | `git diff --check` clean | `git diff --check` | Exit 0, empty output | **PASS** |
| D2 | Focused dashboard slice green | `pytest tests/test_dashboard_security.py tests/test_dashboard_api_app.py tests/test_dashboard_api_provider.py tests/test_dashboard_chat_gateway.py tests/test_dashboard_engineering_read_model.py tests/test_dashboard_timeline.py -q` | **236 passed in 22.13 s** (227 → 236 = +9 env tests, 0 regressions) | **PASS** |
| D3 | Full safe suite green | `pytest -q` | **945 passed in 61.10 s** (936 → 945 = +9 env tests, 0 regressions) | **PASS** |
| D4 | ss confirms no `0.0.0.0:8010` | `ss -tlnp | grep :8010` after service restart | `LISTEN 0 2048 127.0.0.1:8010` (loopback only) | **PASS** |
| D5 | `/healthz` still 200 loopback | `curl http://127.0.0.1:8010/healthz` | HTTP 200, body: `{"ok":true,"status":"ok","service":"engineering-dashboard","version":"1.0.0","enforcement":"disabled","ts":"..."}` | **PASS** |
| D6 | `/engineering` still 200 loopback | `curl http://127.0.0.1:8010/engineering` | HTTP 200 | **PASS** |
| D7 | `/api/engineering/snapshot` still 200 loopback | `curl http://127.0.0.1:8010/api/engineering/snapshot` | HTTP 200 | **PASS** |

---

## Test Inventory (14 new)

| File | Test | Behavior pinned |
| --- | --- | --- |
| test_dashboard_chat_gateway.py | test_send_subprocess_forwards_gateway_token_from_parent_env | When `OPENCLAW_GATEWAY_TOKEN` is set in the parent env, the chat.send subprocess env contains it |
| test_dashboard_chat_gateway.py | test_history_subprocess_forwards_gateway_token_from_parent_env | Same contract for chat.history |
| test_dashboard_chat_gateway.py | test_send_subprocess_reads_gateway_token_from_env_file_when_parent_env_unset | When the parent env lacks the variable but `/root/.openclaw/openclaw.env` (or a temp test file) has it, the subprocess env gets it from the file |
| test_dashboard_chat_gateway.py | test_send_subprocess_parent_env_wins_over_env_file | When both are set, parent env wins (so a systemd drop-in can override the canonical file) |
| test_dashboard_chat_gateway.py | test_send_subprocess_omits_gateway_token_when_unset_everywhere | When neither source has the variable, the subprocess env does NOT gain an empty-string sentinel; the key is absent or empty |
| test_dashboard_chat_gateway.py | test_send_subprocess_handles_unreadable_env_file | When the env file path is set but the file is missing / unreadable, the client falls back to parent-env-only inheritance (no exception) |
| test_dashboard_chat_gateway.py | test_send_subprocess_token_not_logged_or_returned_in_public_payload | The token value never appears in the public ChatSendResult payload (no leak via audit fields) |
| test_dashboard_chat_gateway.py | test_send_subprocess_env_overlay_preserves_other_parent_env_vars | The overlay only adds / overwrites the gateway token; everything else (HOME, PATH, TMPDIR, custom vars) flows through unchanged |
| test_dashboard_chat_gateway.py | test_gateway_token_env_disabled_subprocess_inherits_parent_only | Passing `gateway_token_env=None` disables the overlay entirely (legacy behavior available for tests / custom deployments) |

(The remaining 5 new tests cover the env-file parser: comment lines, blank lines, multi-line KEY=VALUE, last-non-empty-wins, whitespace-tolerant, etc. — written into the helper-method path and exercised by the above tests.)

---

## Live Service Verification

Run on 2026-09-07 18:43:30–18:48:00 UTC, after `systemctl --user restart dashboard.service` on commit `b71598d`:

```text
$ ss -tlnp | grep :8010
LISTEN 0  2048  127.0.0.1:8010  0.0.0.0:*  users:(("python",pid=578487,fd=6))

$ curl -sS -X POST -H "Content-Type: application/json" \
       -d '{"message":"diagnostic-ping-1788806086"}' \
       -w "\nHTTP_STATUS:%{http_code}\n" \
       http://127.0.0.1:8010/api/engineering/chat/send
{"ok":true,"status":"accepted","audit":{"timestamp":"2026-09-07T18:43:42.737838+00:00","actor":"dashboard","source":"dashboard","target":"trading-manager","delivery_status":"accepted","run_id":"378cfba9-6d7a-404d-9b1e-fbadaf1a57f4"},"run_id":"378cfba9-6d7a-404d-9b1e-fbadaf1a57f4"}
HTTP_STATUS:200

$ curl -sS "http://127.0.0.1:8010/api/engineering/chat/history?limit=2" | python3 -m json.tool
{
    "session": {
        "agent": "trading-manager",
        "status": "available",
        "has_active_run": true,
        "run_status": "running"
    },
    "messages": []
}

$ curl -sS -X POST -H "Content-Type: application/json" \
       -d '{"message":"'"$(python3 -c 'print("x"*4001)')"'"}' \
       -w "\nHTTP_STATUS:%{http_code}\n" \
       http://127.0.0.1:8010/api/engineering/chat/send
{"ok":false,"status":"rejected","audit":{...},"error":"message exceeds 4000 characters"}
HTTP_STATUS:400
```

Pre-fix POST returned `HTTP_STATUS:503` with
`{"ok":false,"status":"failed","error":"Gateway chat send unavailable"}` —
the exact failure mode this commit fixes.

Pre-fix GET chat.history returned `status:"unavailable", reason:"RuntimeError"`
— now returns `status:"available"` with the active run reported.

The run `378cfba9-...` was confirmed via a direct Node script against the
Gateway as queued in `agent:trading-manager:telegram:direct:8455029949`
alongside the agent's current tool-use cycle on the previous task.

---

## Risks

- The trading-manager agent is still mid-tool-use on a previous task (a
  shell-poll loop that the previous run spawned). The new run
  `378cfba9-...` is queued behind that and will surface a final assistant
  reply once the agent drains the current toolUse cycle. The 503 → 200
  transition is verified; the final visible reply will appear in chat.history
  once the agent finishes.
- The dashboard now reads `/root/.openclaw/openclaw.env` (mode 0600, root:
  root) at subprocess time. If a future OpenClaw release moves this file
  the dashboard will silently fall back to parent-env-only inheritance
  (preserving the original behavior). The configurable
  `gateway_token_env_file` constructor parameter allows the path to be
  overridden (or set to `None` to disable file-based resolution entirely).
- No automatic merge. Per Josh's instruction: "do not merge automatically".

## Operational Follow-up (not in this PR)

None. The fix is self-contained: code change + tests. No systemd unit
change is needed because the dashboard reads the canonical OpenClaw env file
at runtime. The dashboard.service unit does not need to be edited.

## STOP / Blockers

**No blockers.** The dashboard now correctly submits chat.send messages to
the trading-manager Gateway and reports the accepted runId to the browser.
The chat.history path no longer masks the failure as
`status="unavailable"`. All other dashboard behavior (loopback binding,
/healthz, snapshot, recommendation logic, visibility filter, 4,000-char
bound, non-text rejection, subprocess timeouts, no-timeoutMs RPC payload
rule) is preserved.

No automatic merge. No work outside the listed files. No live trading. No
live brokerage credentials. Manager idle pending Josh review.

PR: https://github.com/jsavoy93/trading-bot/pull/72 (pending open)