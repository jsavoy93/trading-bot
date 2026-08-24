# Engineering Dashboard Chat Send-State Reset Fix — Detailed Archive

## Executive summary
Fixed the dashboard Chat compose-state bug where successful sends could leave the textarea populated with the sent message and keep the Send button disabled as `Sending…`. Root cause was stale DOM-bound compose state after PR #60's persistent Chat state: if snapshot polling rebuilt `#dashboard-content` while `POST /api/engineering/chat/send` was in flight, the send completion path updated detached textarea/button objects and the remounted Chat controls preserved the cached sending state.

## Task
Use `--project-id trading-bot`. Fix Engineering Dashboard Chat send-state reset. Do not add abort/interrupt, do not expose Gateway credentials, do not allow arbitrary routing, create dedicated branch and PR, do not merge.

## Branch and commit
- Branch: `agent/dashboard-chat-send-state-fix`
- Commit: Pending at archive creation.
- PR: Pending at archive creation.

## Trace findings
- `sending=true` equivalent was set in `sendChatMessage()` by directly disabling `#chat-send` and changing text to `Sending…`.
- The textarea value was read from `#chat-message` at submit time.
- `POST /api/engineering/chat/send` resolved inside `sendChatMessage()` before `await refreshChatHistory()`.
- History refresh ran after a successful POST, but the UI compose reset path was coupled to DOM references captured before the POST.
- `finally` restored only the original `button` reference; if snapshot polling had replaced `#dashboard-content`, that reference was detached.
- PR #60 changed behavior by intentionally preserving Chat UI state across snapshot renders. That preserved stale `draft`, `buttonDisabled`, and `buttonText` values when the snapshot poll happened during an in-flight send.
- The stale state that remained set was the Chat cache's sent-message draft plus `buttonDisabled=true` and `buttonText='Sending…'` (now replaced by `composeStatus`).

## Implementation fix
- Replaced cached `buttonDisabled`/`buttonText` with explicit `composeStatus: 'idle' | 'sending'`.
- Added `applyComposeState()` to apply cached compose state to the current DOM elements (`#chat-message`, `#chat-send`) after DOM replacement.
- Added `setComposeState(draft, status)` as the only send-flow mutator for textarea contents and button/input disabled state.
- On send start: preserve original draft and set `composeStatus='sending'`.
- On successful POST acceptance: immediately clear draft and set `composeStatus='idle'`, before/during history refresh and without waiting for manager reply.
- On POST failure: restore original draft and set `composeStatus='idle'`.
- History failures after successful send remain separate: compose stays cleared/idle while history shows bounded unavailable state and keeps last-known messages.

## Files changed
- `dashboard_api/app.py`
- `tests/test_dashboard_api_app.py`
- `MENTOR.md`
- `ITERATION_PROGRESS_LOG.md`
- `REPORT.md`
- `reports/2026-08-24_201051_engineering-dashboard-chat-send-state-fix.md`

## Acceptance criteria evidence
| Criterion | Proof method | Result |
|---|---|---|
| Trace current send flow before changing code | Inspected `sendChatMessage()`, `refreshChatHistory()`, `captureChatUiState()`, `restoreChatUiState()`, `refreshDashboard()` | PASS |
| Textarea clears on successful send | Existing and new Node DOM tests assert empty value after mocked success | PASS |
| Button returns to `Send` and enabled | Existing and new Node DOM tests assert label and disabled state after success | PASS |
| Textarea editable after success | New DOM harness asserts `disabled=false` | PASS |
| Send state independent of manager response/history | Test covers successful send followed by failed history refresh; compose remains cleared/idle | PASS |
| Failed POST preserves unsent text | Existing send-state test keeps original draft after mocked POST failure | PASS |
| Failed POST restores controls and bounded failure | Existing send-state test asserts button idle and failure message | PASS |
| Snapshot polling does not restore old textarea text | New DOM harness replaces DOM via `refreshDashboard()` during in-flight send, then success clears remounted textarea | PASS |
| Repeated sends work/no duplicate sends | New DOM harness performs second send and asserts exactly two send calls | PASS |
| PR #60 history persistence still works | Existing snapshot/history persistence test still passes | PASS |
| Malicious message rendering remains safe | Existing chat history rendering test still passes | PASS |
| Existing send behavior still works | Focused tests pass | PASS |
| Existing dashboard tabs/polling still work | Focused tests pass | PASS |
| No abort/generic control added | No route changes; existing route tests pass | PASS |
| `git diff --check` | Command run | PASS |

## Validation commands and exact results
- `.venv/bin/python -m pytest tests/test_dashboard_api_app.py::test_chat_send_state_recovers_when_snapshot_poll_replaces_dom_during_send -q` → `1 passed, 1 warning`
- `.venv/bin/python -m pytest tests/test_dashboard_api_app.py tests/test_dashboard_api_provider.py tests/test_dashboard_chat_gateway.py -q` → `45 passed, 2 warnings`
- `.venv/bin/python -m pytest -q` → `804 passed, 82 warnings`
- `git diff --check && echo 'git diff --check: PASS'` → `git diff --check: PASS`

## Browser/DOM harness result
Permanent Node DOM harness test added: `test_chat_send_state_recovers_when_snapshot_poll_replaces_dom_during_send`. It simulates selecting Chat, entering `how are you`, clicking Send, verifying `Sending…`, replacing `#dashboard-content` through normal snapshot polling while the POST is unresolved, resolving the POST, confirming remounted controls are idle and textarea cleared, rendering manager history normally, and sending a second message successfully.

## Manual real Gateway double-send result
- Before manual send: one `trading-manager` session, key `agent:trading-manager:telegram:direct:8455029949`, session id `fc12bb64-f6f9-458b-84a7-d7414e0a7196`, model `MiniMax-M3`.
- Sent via dashboard FastAPI endpoint/TestClient:
  - `dashboard send-state fix manual probe 1` → HTTP 200, run id `c080cbea-f11c-4202-81f7-f28228c4d515`.
  - `dashboard send-state fix manual probe 2` → HTTP 200, run id `095c4a1f-2436-46e9-b5d0-d33cfeee66a7`.
- After manual send: one `trading-manager` session remained, key `agent:trading-manager:telegram:direct:8455029949`, session id `6e1733a2-486e-406b-8806-400f830bd7fa`, model `MiniMax-M3`.
- Bounded history later showed probe 2 visible. Probe 1 had been visible on the immediate check, then fell out of the bounded/sanitized window as the shared manager produced responses.
- No duplicate session was created.

## Risks and notes
- Live mobile Safari 60-second observation was not available in this environment; the exact race is covered by the deterministic Node DOM harness.
- Sending manual probe messages to the shared manager session caused the manager to respond in that session; no repository changes outside this branch's expected files were observed afterward.
- Existing unrelated warnings remain: pytest unknown `timeout`, deprecated `websockets.legacy`, datetime UTC deprecations, and dataclass collection warnings.
- Existing unrelated Git auto-GC warning remains environmental housekeeping.

## Overall acceptance
PASS — implementation complete, automated verification passed, manual Gateway double-send accepted both messages and preserved a single shared manager session.

## Next action
Commit, push `agent/dashboard-chat-send-state-fix`, open PR, and wait for Josh review/approval before merge.
