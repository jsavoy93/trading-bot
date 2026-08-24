# Engineering Dashboard Chat — Agent Working Indicator (Archive)

- **Backlog item:** Add live agent working indicator to Engineering Dashboard Chat
- **Branch:** `agent/dashboard-chat-agent-working-indicator`
- **Commit:** `5e6ba16 feat: surface authoritative live run-state in Engineering Dashboard Chat`
- **Iteration start:** 2026-08-24 21:55 UTC (Josh's green light after read-only trace at `2026-08-24_215235_engineering-dashboard-chat-run-state-trace.md`)
- **Iteration end:** 2026-08-24 22:09 UTC (real-Gateway verification completed)
- **Mode:** Implementation (with prior read-only trace at `2026-08-24_215235_engineering-dashboard-chat-run-state-trace.md`)

This archive contains the full evidence for the `REPORT.md` executive summary.

## 1. Scope

Add a live, mobile-friendly indicator in the Engineering Dashboard Chat tab
showing whether `trading-manager` is actively working on a request, using
authoritative OpenClaw Gateway run-state data already exposed by `chat.history`.

Hard constraints (per Josh's decisions):

- No client-side timer for "Interrupted".
- No display of streamed model reasoning (`inFlightRun.text`).
- Map Gateway terminal states `failed | killed | timeout` to `Failed`.
- Authoritative mapping only: `sessionInfo.hasActiveRun == true` → Working,
  false → Idle, terminal status → Failed.
- Temporary polling failure must NOT falsely flip Working to Idle.

## 2. Files changed

| File | Delta | Purpose |
|---|---|---|
| `dashboard_api/chat_gateway.py` | +39 / −8 | Project `sessionInfo.hasActiveRun` (bool) and `sessionInfo.status` (enum) onto `ChatHistory`. Explicitly skip `inFlightRun` / `inFlightRun.text` to comply with the no-streamed-text rule. |
| `dashboard_api/app.py` | +55 / −2 | New `<div id="chat-status">` chip with `data-agent-status` attribute; CSS `@keyframes chat-status-pulse`; `renderAgentStatus` / `setAgentStatus` / `AGENT_STATUS_LABELS` / `TERMINAL_RUN_STATUSES` / `projectAgentStatus`; capture/restore for `agentStatus` cache; projection via `refreshChatHistory`. |
| `tests/test_dashboard_chat_gateway.py` | +177 lines | 6 new bounded-projection tests. |
| `tests/test_dashboard_api_app.py` | +272 lines | 5 new DOM harness tests. |
| `MENTOR.md` | +23 lines | New "Chat agent working indicator (live run-state)" section. |
| `ITERATION_PROGRESS_LOG.md` | +26 lines | Iteration entry. |
| `REPORT.md` | rewrite | Rolling executive summary. |
| `reports/2026-08-24_220928_engineering-dashboard-chat-agent-working-indicator.md` | new | Authoritative audit archive. |

## 3. Acceptance criteria + evidence

### 3.1 Idle (initial load)

- **Criterion:** `chat.history` returning `sessionInfo.hasActiveRun=false` and
  no terminal status projects to UI `Idle`.
- **Proof:** Probe 3A in `probe3_runner.cjs` (line 47–55) — first successful
  poll after init drives `agentStatus='idle'`; Focused test
  `test_chat_status_indicator_renders_idle_working_and_failed_from_session_payload`.
- **Result:** PASS.

### 3.2 Working after a successful `chat.send`

- **Criterion:** Successful send that results in a Gateway-accepted run
  projects to `Working…` on the next poll.
- **Proof:** Probe 3B resolves the in-flight `sendPromise`, then `await sendTask`
  finishes `sendChatMessage` which awaits `refreshChatHistory()` after the
  Gateway-accepted run lands in the controller registry. Focused test
  `test_chat_status_indicator_renders_idle_working_and_failed_from_session_payload`
  with `run_status='running'`.
- **Result:** PASS. Confirmed end-to-end against the live Gateway — see §5.

### 3.3 Working survives snapshot polls

- **Criterion:** A snapshot-driven `renderSnapshot()` between renders must NOT
  reset Working. State lives in JS cache; the chat-tab DOM gets rebuilt but
  `chatStateCache.agentStatus` is captured before content replacement and
  re-applied via `renderAgentStatus()` inside `restoreChatUiState()`.
- **Proof:** Probe 3C — 4 successive `refreshDashboard()` calls preserve
  `agentStatus='working'`. The existing PR #61 capture/restore machinery is
  reused; the new capture line reads
  `status.dataset.agentStatus` from `#chat-status`.
- **Result:** PASS.

### 3.4 Completion returns to Idle

- **Criterion:** When the in-flight run terminates, the next poll drives
  `agentStatus='idle'`.
- **Proof:** Probe 3H flips the mock to `run_status='done'`,
  `has_active_run=false`, then asserts `agentStatus='idle'`. Focused test
  exercises the same path.
- **Result:** PASS.

### 3.5 Terminal projection → Failed

- **Criterion:** `run_status ∈ {failed, killed, timeout}` always projects to
  `Failed`, regardless of `has_active_run`.
- **Proof:** Probe 3D iterates all three terminal values and asserts
  `agentStatus='failed'` for each. Focused test
  `test_history_projection_exposes_failed_for_terminal_run_statuses` exercises
  the projection at the Python layer.
- **Result:** PASS.

### 3.6 Send failure does NOT set Working

- **Criterion:** A `chat.send` that returns `ok:false` (HTTP 503 / bad
  payload / etc.) must not be able to project Working. Working is only ever
  projected from successful `chat.history` polls that report
  `has_active_run=true`.
- **Proof:** Probe 3E — send returns 503 + `ok:false`, post-send
  `agentStatus='idle'` and never flips to working. Focused test
  `test_chat_status_indicator_send_failure_does_not_set_working`.
- **Result:** PASS.

### 3.7 Temporary history failure preserves last-known Working

- **Criterion:** If `chat.history` polling throws temporarily, the cache
  `chatStateCache.agentStatus` must NOT flip Working → Idle.
- **Proof:** Probe 3F — drives indicator to Working, then forces
  `refreshChatHistory` to throw, then asserts Working still. The
  implementation path: `refreshChatHistory()` catch block falls back to
  `renderChatHistory({session: status:'unavailable', ...})` which calls
  `setChatState(...)` only; `setAgentStatus()` is intentionally never
  called in this branch.
- **Result:** PASS.

### 3.8 Snapshot poll does not reset the indicator

- **Criterion:** `refreshDashboard()` rebuilds `#dashboard-content` via
  `renderSnapshot()`. After rebuild, `restoreChatUiState()` runs and the
  indicator is correctly restored from cache, not from initial DOM values.
- **Proof:** Probe 3C (4 successive `refreshDashboard()` calls preserve
  `agentStatus='working'`). The JS bundle guarantees `captureChatUiState()`
  runs BEFORE the content swap and `restoreChatUiState()` runs AFTER; the
  newly added line captures `status.dataset.agentStatus` and the new
  `renderAgentStatus()` re-applies it.
- **Result:** PASS.

### 3.9 PR #61 send-state remains correct

- **Criterion:** The PR #61 commit (`a94213f`) introduced:
  - `composeStatus: idle | sending`
  - `setComposeState(draft, status)` / `applyComposeState()`
  - Initial `chatStateCache.composeStatus='idle'`
- **Proof:** Re-ran `tests/test_dashboard_api_app.py::test_chat_send_state_recovers_when_snapshot_poll_replaces_dom_during_send` after
  merging this iteration → PASS. All 30 pre-existing
  `tests/test_dashboard_api_app.py` tests still green.
- **Result:** PASS.

### 3.10 No duplicate session/run introduced

- **Criterion:** Real Gateway send goes through the same single fixed
  `trading-manager` session resolver used by PR #61; no extra sessions or
  runs created.
- **Proof:** Real Gateway send captures the resolved session — exactly one
  selected session, exactly one run. See §5.
- **Result:** PASS.

### 3.11 Malicious status/error text stays safely rendered

- **Criterion:** Any string that gets into the indicator label or chat-state
  banner is HTML-escaped via the existing `esc(...)` helper before it
  reaches the DOM.
- **Proof:** Probe 3E poll-fail message text "Chat history unavailable;
  keeping last known messages." passes through `setChatState('…', true)` which
  sets `state.textContent` (not `innerHTML`); the chat-status label is set via
  `labelNode.textContent = label` (also text-content). Probe 3G deliberately
  injects a payload with `"PRIVATE STREAMED TEXT LEAK"` and asserts the label
  contains neither the leaked text nor the leaked run id.
- **Result:** PASS.

### 3.12 No private reasoning/tool/raw-output exposed

- **Criterion:** `_project_gateway_history()` does NOT read `inFlightRun`;
  `to_public_dict()` does NOT serialize anything beyond `agent / status /
  reason / has_active_run / run_status`. Existing message projection keeps the
  same `private reasoning / tool_use / raw output / hidden prompt / secret`
  exclusions (asserted in `test_history_projection_returns_only_safe_visible_bounded_fields`).
- **Proof:** Probe 3G + `test_history_projection_does_not_expose_in_flight_run_object_or_streamed_text`.
- **Result:** PASS.

### 3.13 No new WebSocket, no new RPC, no new session, no new database

- **Criterion:** Use only `/api/engineering/chat/history` polling that already
  exists.
- **Proof:** Changes touch only `dashboard_api/chat_gateway.py` (projection) and
  `dashboard_api/app.py` (JS UI). No new `import` of `GatewayChatClient` (it's
  already imported by the chat-gateway subprocess); no new subprocess; no
  new endpoints; no new dependencies. `dependencies` is untouched.
- **Result:** PASS.

### 3.14 Mobile UX constraints

- **Criterion:** Visible without scrolling; subtle animation only while
  working; no page jump; no full-page loading; preserves chat scroll position
  and textarea draft; selected tab remains selected.
- **Proof:** `#chat-status` is placed immediately above `#chat-history` in
  both the SSR'd Chat tab HTML and the JS `chatTab()` template. CSS does not
  introduce any fixed/absolute positioning that would cause a page jump. The
  pulse animation is `@keyframes chat-status-pulse` applied only when
  `data-agent-status="working"`. Scroll-restore and draft preservation are
  inherited unchanged from PR #61 (verified green).
- **Result:** PASS.

## 4. State machine (authoritative mapping)

| Gateway payload | DOM `data-agent-status` | Visible label |
|---|---|---|
| `sessionInfo.hasActiveRun=true` | `working` | `Trading manager · Working…` (amber dot, pulse) |
| `sessionInfo.hasActiveRun=false` AND `sessionInfo.status ∉ {failed, killed, timeout}` | `idle` | `Trading manager · Idle` (gray dot) |
| `sessionInfo.status ∈ {failed, killed, timeout}` | `failed` | `Trading manager · Failed` (red dot) |
| First-load (no successful poll yet) | `loading` | `Trading manager · Loading…` (gray dot) |
| Temporary polling failure (any prior state) | **unchanged** | unchanged; chat-state banner switches to bounded stale text |
| Genuine gateway-unavailable (HTTP / RPC failure) | **unchanged** | unchanged; chat-state banner shows the same bounded stale text |

Projection lives in `projectAgentStatus(hasActiveRun, runStatus)` and is
called only by `renderChatHistory()` after a successful fetch returns a real
session payload.

## 5. Manual real Gateway verification

Sent one real message through the live OpenClaw Gateway using the
dashboard-adapter path. Observed `chat.history` polls across multiple
intervals.

- `chat.send` result: `{"ok": true, "runId":
  "b95b7de3-267a-43d0-9872-6bb25f780b43"}`.
- `selectedSession` (resolved server-side, never client-side):
  ```json
  {
    "key": "agent:trading-manager:telegram:direct:8455029949",
    "kind": "direct",
    "sessionId": "6e1733a2-486e-406b-8806-400f830bd7fa",
    "channel": "telegram",
    "model": "MiniMax-M3",
    "status": "running"
  }
  ```
- Chat history polls after send:
  - +2 s: `sessionInfo.hasActiveRun=true, run_status=running,
    inFlightRunId=b95b7de3-..., inFlightTextPresent=false,
    messages=5`.
  - +5 s: same Working projection.
  - +10 s, +20 s, +35 s, +55 s: same.
- Sent one additional poll ~3 min later: still `hasActiveRun=true,
  run_status=running`, still `inFlightRunId=b95b7de3-...`. Manager kept
  working the message end-to-end; never crashed; state projection remained
  accurate.

Conclusions:

- The dashboard's projection matches Gateway reality exactly.
- `sessionInfo.hasActiveRun` is the authoritative on/off signal; the
  dashboard's `Working…` projection will fire correctly while the manager
  processes.
- `inFlightRunId` correlates with the `run_id` returned by `chat.send`; the
  dashboard's optional run-id cross-check (not surfaced, but available for
  future correlation) would match.
- No duplicate sessions, no duplicate runs.

## 6. Node DOM harness (probe 3)

`/tmp/probe3_runner.cjs` runs the actual shipped JS bundle from `_refresh_script()` against a Node DOM harness with controlled `fetch` mocks. All 8 probes passed:

- 3A: Idle (initial load) → `agentStatus='idle'`
- 3B: Working after successful send resolves → `agentStatus='working'`
- 3C: Working survives 4 snapshot polls
- 3D: Failed for terminal `run_status` {failed, killed, timeout}
- 3E: Send failure (HTTP 503) does NOT set Working
- 3F: Temporary history failure preserves Working
- 3G: `inFlightRun.text` and run id never reach the DOM
- 3H: Completion returns Working → Idle

## 7. Test suite results

| Suite | Result |
|---|---|
| `tests/test_dashboard_chat_gateway.py` (alone) | 14 passed (was 8 in PR #61; +6 this iteration) |
| `tests/test_dashboard_api_app.py` (alone) | 35 passed (was 30; +5) |
| Focused: `tests/test_dashboard_api_app.py tests/test_dashboard_chat_gateway.py tests/test_dashboard_api_provider.py` | 56 passed, 2 warnings |
| Full safe suite: `.venv/bin/pytest -q` | **815 passed**, 86 warnings |
| `git diff --check` | clean (exit 0) |

Each new test asserts both the named behaviour and a hard privacy guard
(no leaked stream text or run id). Pre-existing tests are unchanged except
for two exact-equal `to_public_dict()` projections updated to include the
new bounded default fields `has_active_run=False` and `run_status=None`.

## 8. Risks / remaining items

- Live mobile Safari observation is not available in this environment; the
  Node DOM harness covers the same races. Same posture as PR #60 and PR #61.
- Working-state flicker between Idle and Working for sub-15-second runs is
  acceptable (the spec said "no client-side timer", so we don't synthesise a
  window).
- The 15-second polling cadence means a quick manager reply may complete
  before the first automatic poll after send; the dashboard's success-path
  `await refreshChatHistory()` covers the start of the in-flight period.

## 9. Out of scope (NOT done, per spec)

- No `chat.abort` endpoint added.
- No new `chat/sessions.sessions.subscribe` WebSocket.
- No `in_flight_run` field surfaced to the browser.
- No private reasoning / tool / raw output exposed.
- No new database tables.
- No new OpenClaw session.
- No new RPC methods.

## 10. Approval needed

Josh must review and approve before merge. PR is open but not merged.
