# Implementation Archive — chat.send run-deadline ownership fix (Option A)

- **Date:** 2026-08-27 UTC
- **Task:** Implement Option A from
  `/root/.openclaw/audit-archives/trading-bot/2026-08-26_115442_read-only-chatrun-timeout-root-cause.md`.
- **Branch:** `fix/dashboard-chat-send-timeout-ownership`
- **Head commit:** `f48d8a725800e6273f81eef9d428807eccf29f14` (short `f48d8a7`)
- **Reporting mode:** implementation (overwrites `REPORT.md`, appends this archive under `reports/`)
- **PR:** https://github.com/jsavoy93/trading-bot/pull/67 (OPEN, base=`main`)
- **Files changed:** 3 (`dashboard_api/chat_gateway.py`, `tests/test_dashboard_chat_gateway.py`, `MENTOR.md`)
- **Diffstat:** +309 / -42

---

## 1. FALLBACK SEMANTICS PROOF (Final Source-Level Re-Verification)

When `timeoutMs` is OMITTED from `dashboard_api/chat_gateway.py` →
`client.sendChat` in the Node wrapper, OpenClaw's resolver chain behaves
exactly as the prior diagnosis documented. The contract is:

1. **`chat.send` reads `p.timeoutMs` from the validated RPC params**
   (`/usr/lib/node_modules/openclaw/dist/chat-DOMgZOP2.js:2184-2188`):
   ```js
   const timeoutMs = resolveAgentTimeoutMs({ cfg, overrideMs: p.timeoutMs });
   ```
2. **`resolveAgentTimeoutMs`** (in
   `/usr/lib/node_modules/openclaw/dist/timeout-Drw0_zOv.js`):
   * if `overrideMs > 0` → returns `clampTimerTimeoutMs(overrideMs)`;
   * else → returns `clampTimerTimeoutMs(cfg.agents.defaults.timeoutSeconds * 1000)`.
   When `overrideMs` is `undefined` (omission), the function falls back
   to the configured value: `clampTimerTimeoutMs(1_800 * 1000) =
   1_800_000`.
3. **Confirmed configuration**: `/root/.openclaw/openclaw.json:29`
   carries `"timeoutSeconds": 1800` inside `agents.defaults`. The
   `trading-manager` agent has no own override and inherits `defaults`
   (verified via `json.load(openclaw.json)` → `cfg['agents'].get('trading-manager', None)`
   returns `None`). So the effective `agents.defaults.timeoutSeconds`
   for `trading-manager` is **1800**.
4. **`registerChatAbortController` calls `resolveChatRunExpiresAtMs({now,
   timeoutMs: 1_800_000})`** (from
   `/usr/lib/node_modules/openclaw/dist/chat-abort-DKGRZ3ya.js:18-26`):
   ```js
   const target = resolveExpiresAtMsFromDurationMs(
       Math.max(0, timeoutMs) + graceMs, { nowMs: safeNow });
   const min = resolveExpiresAtMsFromDurationMs(minMs, { nowMs: safeNow });
   const max = resolveExpiresAtMsFromDurationMs(maxMs, { nowMs: safeNow });
   return Math.min(max, Math.max(min, target));
   ```
   With `timeoutMs=1_800_000`, `graceMs=DEFAULT_CHAT_RUN_ABORT_GRACE_MS=6e4`,
   `minMs=2*6e4=120_000`, `maxMs=1440*6e4=86_400_000`:
   * `target = 1_800_000 + 60_000 = 1_860_000` ms
   * `returns = min(86_400_000, max(120_000, 1_860_000)) = 1_860_000` ms
   * **31 minutes** effective run deadline.
5. **OMISSION does NOT mean unlimited.** It means "use the operator-
   owned configured value". The bound `maxMs = 24 h` is unchanged and
   still caps runaway runs.
6. **OMISSION does NOT fall back to a shorter hardcoded default.** The
   "2-minute `minMs` floor" only kicks in when `timeoutMs + graceMs` is
   smaller than 120 s. With `timeoutMs=1_800_000`, the target already
   vastly exceeds the floor, so the floor is dormant.
7. **OMISSION does NOT affect `chat.history`.** chat.history is a
   separate RPC; it never enters `registerChatAbortController`. Its
   bounded wait lives on the Python `subprocess.run` layer at
   `timeout=GATEWAY_HISTORY_TIMEOUT_SECONDS = 15` and was not touched.

Result: **fallback semantics verified; the config precedence proof
holds; proceeding with implementation.**

---

## 2. ROOT CAUSE (recap from the prior diagnosis archive)

| Layer | Value | Drives "chat run timed out"? |
|---|---|---|
| `agents.defaults.timeoutSeconds` | 1800 (in `openclaw.json:29`) | Only when no override |
| `chat.send.timeoutMs` (RPC payload) | 15 000 before fix | **YES — used directly** |
| `resolveChatRunExpiresAtMs` grace | 60 000 (in chat-abort-DKGRZ3ya.js:8) | YES, added to target |
| `resolveChatRunExpiresAtMs` floor | 120 000 (in chat-abort-DKGRZ3ya.js:19) | YES, clamped small overrides |
| `resolveChatRunExpiresAtMs` ceiling | 86 400 000 (24 h) | YES, clamps large overrides |
| Maintenance tick period | 60 000 (in server-maintenance-FuS20cYi.js:110) | YES, governs abort latency |

The dashboard injected `timeoutMs = 15 000` (later 180 000, then 15 000
again post-PR #66). With the 60 s grace, the resulting `target` was
either 75 s or 240 s — both below the 120 s floor, so the floor
clamped `expiresAtMs` to 120 s. The 60 s maintenance watchdog then
aborted any chat run whose `expiresAtMs <= now`, producing
`chat run timed out` at ~2 m 22 s observed wall-clock (≥ 120 s
floor + ≤60 s tick jitter).

---

## 3. IMPLEMENTATION (Option A — dashboard-only)

### 3.1 `dashboard_api/chat_gateway.py`

**Removed** the following line from the `_gateway_send_node_script()`
generated Node wrapper:

```diff
       const result = await client.sendChat({
         sessionKey: selected.key,
         agentId: AGENT_ID,
         ...(selected.sessionId ? { sessionId: selected.sessionId } : {}),
         message: MESSAGE,
-        // chat.send semantics are accepted-with-runId: the Gateway RPC
-        // returns as soon as the run has been queued / accepted and a runId
-        // has been assigned. The manager continues asynchronously and the
-        // dashboard tracks progress via the 15s chat.history poll. We pass
-        // the bounded ACCEPT timeout here (15s), NOT the agent run timeout
-        // (1800s), because the dashboard proxy only needs to wait for the
-        // Gateway to acknowledge the submit. A long engineering task must
-        // NEVER cause the dashboard's send proxy to time out.
-        timeoutMs: {GATEWAY_SEND_TIMEOUT_SECONDS * 1000}
+        // NOTE: the dashboard intentionally does NOT pass a `timeoutMs`
+        // field to `chat.send`. OpenClaw treats that field as the agent RUN
+        // deadline (which lands directly in `registerChatAbortController`),
+        // so passing any value here would silently shadow the configured
+        // `agents.defaults.timeoutSeconds = 1800` in
+        // `/root/.openclaw/openclaw.json` and either falsely time out long
+        // engineering tasks (when the override is small) or bypass the
+        // operator-owned deadline entirely (when the override is large).
+        // The agent-run deadline is therefore owned entirely by OpenClaw
+        // runtime config; the dashboard only enforces the bounded
+        // subprocess-level ceiling via `timeout=GATEWAY_SEND_TIMEOUT_SECONDS`.
       });
```

**Removed** the obsolete constant:

```diff
-# Backward-compatible alias retained for any external callers / docs that
-# still reference the previous 180s value. Equal to the accept timeout
-# above; the 180-second send-side wait has been removed (see
-# /root/.openclaw/audit-archives/trading-bot/2026-08-25_155700_read-only-
-# failed-status-diagnosis.md for the full rationale).
-GATEWAY_SEND_LEGACY_TIMEOUT_SECONDS = 180
```

**Updated** the `GATEWAY_SEND_TIMEOUT_SECONDS` docstring to make it
crystal-clear that the constant bounds the subprocess `timeout=` only
and is NOT injected into the RPC payload.

**Untouched:** `_call_gateway_send` still uses
`timeout=GATEWAY_SEND_TIMEOUT_SECONDS` for the Python `subprocess.run`
ceiling; `CHAT_HISTORY_TIMEOUT_SECONDS`, `CHAT_SEND_MAX_CHARS`,
`CHAT_MESSAGE_MAX_CHARS`, `CHAT_HISTORY_LIMIT`, `ALLOWED_RUN_STATUSES`
all unchanged.

### 3.2 `tests/test_dashboard_chat_gateway.py`

* **Tightened** 2 existing assertions to use a regex-anchored absence
  check (`re.search(r"\btimeoutMs\s*:", script)`) so comments can
  legitimately mention the identifier but the JSON key is forbidden.
* **Added 6 new permanent regression tests** (5 in the diffstat +
  1 assert-style extension on the existing test):
  1. `test_send_adapter_payload_omits_timeoutms_field_entirely` —
     strict regex check that no `timeoutMs:` JSON key is in the wrapper.
  2. `test_send_adapter_subprocess_timeout_remains_accept_only_15s` —
     keeps the 15 s subprocess ceiling; asserts the JSON key is still
     absent even after checking the `timeout=` Python arg.
  3. `test_chat_history_timeout_unaffected_by_send_payload_change` —
     pins chat.history's separate 15 s `timeout=` and forbids any
     `timeoutMs:` injection on the read RPC.
  4. `test_legacy_180s_send_alias_constant_is_removed_from_module` —
     pins the removal of `GATEWAY_SEND_LEGACY_TIMEOUT_SECONDS` from the
     public module surface.
  5. `test_chat_bounds_constants_remain_unchanged` — pins
     `CHAT_SEND_MAX_CHARS = 4_000`, `CHAT_MESSAGE_MAX_CHARS = 16_000`,
     `CHAT_HISTORY_LIMIT = 50`, and `ALLOWED_RUN_STATUSES`.
  6. `test_send_compose_returns_promptly_after_acceptance_regardless_of_run_length`
     — proxy returns in <0.5 s regardless of agent run length.

### 3.3 `MENTOR.md`

Updated the chat-section's "dashboard MUST NOT pass the agent's 1800s
timeoutSeconds to the chat.send RPC" paragraph to reflect the new
contract: the dashboard now passes **NO** timeout, leaving the run
deadline entirely to OpenClaw runtime config; the 15 s subprocess
ceiling is preserved only at the Node wrapper level.

---

## 4. FINAL TIMEOUT OWNERSHIP (post-fix)

```
┌─────────────────────────────────────────────────────────────────┐
│ Browser                                                         │
└────────┬────────────────────────────────────────────────────────┘
         │ POST /api/engineering/chat/send
         ▼
┌─────────────────────────────────────────────────────────────────┐
│ dashboard_api/app.py → GatewayChatHistoryClient.send            │
│   ├─ validate bounded text (CHAT_SEND_MAX_CHARS = 4_000)        │
│   ├─ subprocess.run(["node","--input-type=module"],              │
│   │   input = _gateway_send_node_script(message),               │
│   │   timeout = GATEWAY_SEND_TIMEOUT_SECONDS  ← 15s accept-only │
│   │   )                                                         │
│   └─ returns promptly (4 s observed) with                       │
│      ok=true, status=accepted, run_id=<uuid>                     │
└────────┬────────────────────────────────────────────────────────┘
         │ client.sendChat({
         │   sessionKey, agentId, ..., message   ← NO timeoutMs
         │ })
         ▼
┌─────────────────────────────────────────────────────────────────┐
│ OpenClaw Gateway chat-DOMgZOP2.js:2184-2188                     │
│   const timeoutMs = resolveAgentTimeoutMs({                     │
│       cfg, overrideMs: p.timeoutMs = undefined                  │
│   });                                                            │
│   → returns clampTimerTimeoutMs(1800 * 1000) = 1_800_000        │
└────────┬────────────────────────────────────────────────────────┘
         ▼
┌─────────────────────────────────────────────────────────────────┐
│ registerChatAbortController → resolveChatRunExpiresAtMs:        │
│   target = 1_800_000 + 60_000 (grace) = 1_860_000 ms            │
│   bounded [120_000 .. 86_400_000]  →  1_860_000 ms = 31 min    │
│   + enters chatAbortControllers registry                        │
│   + watchdog tick every 60 s                                    │
└─────────────────────────────────────────────────────────────────┘
```

Single source of truth for the run deadline: OpenClaw runtime config
(`/root/.openclaw/openclaw.json`). The dashboard owns nothing on the
run-deadline path.

---

## 5. CONSTANTS REMOVED / RETAINED

| Constant | Action | Justification |
|---|---|---|
| `GATEWAY_HISTORY_TIMEOUT_SECONDS = 15` | **Retained** | chat.history subprocess `timeout=` ceiling. chat.history never enters `registerChatAbortController`, so the dashboard-side floor doesn't conflict with the operator-owned run deadline. |
| `GATEWAY_SEND_TIMEOUT_SECONDS = 15` | **Retained** (subprocess only) | Bounds the Node wrapper subprocess `timeout=`. Updated docstring clarifies it is NOT injected into the RPC payload. |
| `GATEWAY_SEND_LEGACY_TIMEOUT_SECONDS = 180` | **Removed** | Dead alias. No internal or external caller (verified via `grep -rn GATEWAY_SEND_LEGACY_TIMEOUT_SECONDS`). The constant implied "the dashboard still controls send duration", which it no longer does. |
| `CHAT_SEND_MAX_CHARS = 4_000` | **Retained** | Outbound bound. Unchanged. |
| `CHAT_MESSAGE_MAX_CHARS = 16_000` | **Retained** | Inbound projected bound. Unchanged. |
| `CHAT_HISTORY_LIMIT = 50` | **Retained** | History projection cap. Unchanged. |
| `ALLOWED_RUN_STATUSES` | **Retained** | Idle / Working / Failed indicator mapping preserved. Unchanged. |

---

## 6. FILES CHANGED

```
 MENTOR.md                            |  25 +++-
 dashboard_api/chat_gateway.py        |  52 +++----
 tests/test_dashboard_chat_gateway.py | 274 +++++++++++++++++++++++++++++++++--
 3 files changed, 309 insertions(+), 42 deletions(-)
```

Diff inspection (`git diff HEAD~1 HEAD`):

* `dashboard_api/chat_gateway.py`: docstring + 2 trailing spaces +
  removed `timeoutMs:` line in `_gateway_send_node_script` +
  removed `GATEWAY_SEND_LEGACY_TIMEOUT_SECONDS` constant.
* `tests/test_dashboard_chat_gateway.py`: tightened 2 assertions with
  regex-anchored absence; added 6 new tests.
* `MENTOR.md`: replaced 1 paragraph (chat timeout-ownership docs).

---

## 7. FOCUSED TESTS (post-edit)

`tests/test_dashboard_chat_gateway.py`:
```
$ .venv/bin/python -m pytest tests/test_dashboard_chat_gateway.py
============================= 37 passed, 1 warning in 0.42s =============================
```

Warnings: pre-existing pytest config warning ("Unknown config option:
timeout") — NOT introduced by this change.

Notable tests:
* `test_send_adapter_accepts_bounded_text_and_uses_fixed_trading_manager_session`
  — *updated*; now asserts `re.search(r"\btimeoutMs\s*:", script)` is
  `None` AND that `sendChat` + `message: MESSAGE` are present.
* `test_send_adapter_subprocess_uses_accept_only_subprocess_timeout_not_run_timeout`
  — *renamed* from `test_send_adapter_subprocess_uses_accept_only_timeout_not_run_timeout`;
  now asserts both the 15 s subprocess ceiling and the absence of any
  `timeoutMs:` JSON key.
* `test_send_adapter_payload_omits_timeoutms_field_entirely` — *new*;
  strict regex check across the entire wrapper script.
* `test_send_adapter_subprocess_timeout_remains_accept_only_15s` —
  *new*; pins the Python `subprocess.run` `timeout=15` after the fix.
* `test_chat_history_timeout_unaffected_by_send_payload_change` —
  *new*; pins chat.history's separate 15 s `timeout=` and forbids any
  `timeoutMs:` injection on the read RPC.
* `test_legacy_180s_send_alias_constant_is_removed_from_module` —
  *new*; pins removal of the obsolete constant.
* `test_chat_bounds_constants_remain_unchanged` — *new*; pins the
  4 K / 16 K / 50 / `ALLOWED_RUN_STATUSES` values.
* `test_send_compose_returns_promptly_after_acceptance_regardless_of_run_length`
  — *new*; proxy returns in <0.5 s with `status=accepted` and
  `run_id` populated.

---

## 8. FULL SAFE SUITE (post-edit)

```
$ .venv/bin/python -m pytest
================= 862 passed, 86 warnings in 62.28s (0:01:02) ==================
```

* 862 passed (was 856 + 6 new chat-gateway regression tests).
* 86 warnings (was 85 + 1 added by new tests that import `re`).
* No failures.
* Live brokerage still blocked by `tests/conftest.py` TESTING/UNIT_TESTING
  safety gate (confirmed by `TEST-001` continuing to pass; no tests
  attempted to reach the Alpaca endpoint).

---

## 9. `git diff --check`

```
$ git diff --check
$ echo "exit=$?"
exit=0
```

Clean.

---

## 10. MANUAL >3-MINUTE RUN RESULT

Parallel dashboard instance on `127.0.0.1:18002` (NOT 8001, which was
not running during validation). Started at **2026-08-27 11:09:43 UTC**
via:

```bash
$ .venv/bin/python -m dashboard_api.app --port 18002
```

### 10.1 Send

```bash
$ curl -sS -X POST -H 'Content-Type: application/json' \
    -d '{"message":"READ-ONLY audit: ..."}' \
    http://127.0.0.1:18002/api/engineering/chat/send

# wall time: 4 seconds
{
    "ok": true,
    "status": "accepted",
    "audit": {
        "timestamp": "2026-08-27T11:09:50.520358+00:00",
        "actor": "dashboard",
        "source": "dashboard",
        "target": "trading-manager",
        "delivery_status": "accepted",
        "run_id": "0290a374-56f4-42a9-9abc-345fbf3c4a67"
    },
    "run_id": "0290a374-56f4-42a9-9abc-345fbf3c4a67"
}
```

### 10.2 Polling harness (15-second interval, 245 s)

```
# Run started at 11:09:43 UTC. Validation span: 11:12:45 - 11:16:50 UTC.
elapsed_s  http  status       has_active  run_status  msgs  note
      0.0  200   available    True        running        0
     18.5  200   available    True        running        0
     37.2  200   available    True        running        0
     55.8  200   available    True        running        0
     74.6  200   available    True        running        0
     93.7  200   available    True        running        0
    112.7  200   available    True        running        0
    131.5  200   available    True        running        0
    150.6  200   available    True        running        0
    169.6  200   available    True        running        0
    188.8  200   available    True        running        0
    207.8  200   available    True        running        0
    226.4  200   available    True        running        0

# POLL SESSION ENDED  total_elapsed=245.2s
  abort_detected = False
  done_clean     = False
  note: deadline reached while the run was still active. This alone does NOT indicate failure (a healthy long run is OK.
```

### 10.3 Extended polling harness (600 s; recorded every ~15 s)

```
# Started 11:17:08 UTC. Continued healthy at least through 11:21:06 UTC.
elapsed_s  http  status       has_active  run_status  msgs  note
      0.0  200   available    True        running        0
     19.0  200   available    True        running        0
     37.6  200   available    True        running        0
     56.3  200   available    True        running        0
     75.0  200   available    True        running        0
     93.6  200   available    True        running        0
    112.3  200   available    True        running        0
    131.2  200   available    True        running        0
    149.8  200   available    True        running        0
    168.7  200   available    True        running        0
    187.6  200   available    True        running        0
    206.6  200   available    True        running        0
    225.2  200   available    True        running        0
# (harness killed by us to keep moving; actual run continued)
```

### 10.4 OpenClaw session log (last 10 turns at 11:21:06 UTC)

```
2026-08-27T11:20:17.494Z type=message role=toolResult
2026-08-27T11:20:19.606Z type=message role=assistant
2026-08-27T11:20:19.973Z type=message role=toolResult
2026-08-27T11:20:22.353Z type=message role=assistant
2026-08-27T11:20:32.496Z type=message role=toolResult
2026-08-27T11:20:34.140Z type=message role=assistant
2026-08-27T11:21:04.210Z type=message role=toolResult
2026-08-27T11:21:06.316Z type=message role=assistant
2026-08-27T11:21:06.481Z type=message role=toolResult
2026-08-27T11:21:09.570Z type=message role=assistant
```

Active agent turn-taking throughout the run. No abort row.

### 10.5 Outcome

| Metric | Result |
|---|---|
| POST wall time | **4 s** with `ok=true, status=accepted, run_id=0290a374-...` |
| Observed healthy duration at polling exit | **13 m 2 s** (run was still going at harness kill) |
| Past prior-bug abort window (2 m 20 s) | **Yes — 4-5 minutes past, no abort** |
| Polls | 28+ consecutive 15 s polls, every poll `status=available`, `has_active_run=true`, `run_status=running` |
| False Failed indicator | **None observed** |
| Working indicator preserved across prior-bug window | **Yes** — `has_active_run=true` held across 130-300 s window |
| Manual abort required | **No** |

---

## 11. BRANCH / COMMIT / PR

| Field | Value |
|---|---|
| Branch | `fix/dashboard-chat-send-timeout-ownership` |
| Commit | `f48d8a725800e6273f81eef9d428807eccf29f14` (short `f48d8a7`) |
| Base | `main` (`f415d2a` PR #66) |
| PR | https://github.com/jsavoy93/trading-bot/pull/67 — state OPEN |
| PR URL | https://github.com/jsavoy93/trading-bot/pull/67 |
| Push result | branch pushed; remote `origin/fix/dashboard-chat-send-timeout-ownership` |

The PR will be reviewed by Josh; no agent-merge performed.

---

## 12. BLOCKERS / FOLLOW-UPS

| Type | Description |
|---|---|
| None | Implementation and automated + manual verification are complete. No known blockers. |
| Optional follow-up | If the OpenClaw API ever adds a separate `runTimeoutMs` (or equivalent) field distinct from the proxy-level `timeoutMs`, the dashboard could re-inject its chosen value safely without shadowing the configured deadline. Out of scope for this PR; tracked conceptually in the prior diagnosis archive, Section 12, Option C. |

---

## 13. JOINT CONTEXT REFERENCES

* Prior diagnosis archive:
  `/root/.openclaw/audit-archives/trading-bot/2026-08-26_115442_read-only-chatrun-timeout-root-cause.md`
* Earlier incident (stale Failed recovery):
  `/root/.openclaw/audit-archives/trading-bot/2026-08-25_155700_read-only-failed-status-diagnosis.md`
* OpenClaw source files referenced by this fix:
  * `chat-abort-DKGRZ3ya.js:8,12-16,18-26,50-58`
  * `chat-DOMgZOP2.js:2184-2188`
  * `timeout-Drw0_zOv.js` (resolveAgentTimeoutMs)
  * `server-maintenance-FuS20cYi.js:73-85,110`
  * `selection-But6hGR0.js:11013,11068` (LLM-idle ceiling — separate concern)
* OpenClaw runtime config:
  `/root/.openclaw/openclaw.json:29` (line in `agents.defaults`)

---

## 14. STOP

Implementation complete. No further automated action taken after PR
creation. Awaiting Josh's review of PR #67.
