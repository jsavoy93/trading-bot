# Engineering Dashboard Chat Flicker/Reset Fix

- Timestamp: 2026-08-24 00:50 UTC
- Project: `trading-bot`
- Branch: `agent/dashboard-chat-flicker-fix`
- Objective: Fix mobile Chat tab flicker where loaded history periodically resets to `Loading trading-manager history…` during normal Engineering Dashboard snapshot polling.

## Confirmed root cause

The bug is **A. snapshot polling replacing Chat DOM**.

Exact sequence traced before implementation:

1. Chat tab is selected.
2. `refreshChatHistory()` loads `/api/engineering/chat/history` and calls `renderChatHistory()`.
3. `renderChatHistory()` writes loaded messages into `#chat-history` and hides `#chat-state`.
4. Normal engineering dashboard snapshot polling runs every `POLL_INTERVAL_MS = 15000`.
5. `refreshDashboard()` fetches `/api/engineering/snapshot` and does `content.innerHTML = renderSnapshot(snapshot)` on `#dashboard-content`.
6. `renderSnapshot()` reconstructs all tab panels, including `panel('chat', chatTab(), false)`.
7. `chatTab()` returns initial Chat markup containing `Loading trading-manager history…`, an empty `#chat-history`, an empty textarea, and a fresh Send button.
8. `switchTab(activeTab)` restores selected Chat and calls `refreshChatHistory()`.
9. Until that separate history request completes, the user sees loaded → loading → loaded flicker.

Ruled out as primary cause: chat-history backend failure, overlapping history requests, selected-tab loss, full-page/navigation reload, and Gateway/session behavior.

## Implementation approach

Added client-side persistent Chat UI state around the snapshot render cycle:

- `chatStateCache` stores current history HTML, status text/display/status kind, textarea draft, send button disabled/text state, scroll position, and near-bottom state.
- `captureChatUiState()` runs before snapshot polling replaces `#dashboard-content`.
- `restoreChatUiState()` runs immediately after `content.innerHTML = renderSnapshot(snapshot)` when the active tab is Chat, and again after tab binding/switch restoration.
- `renderChatHistory()` updates the cache whenever history updates successfully.
- Temporary history failures still preserve last-known messages.
- Temporary snapshot failures never replace `#dashboard-content`, so Chat remains unchanged and the existing dashboard warning is shown.
- New messages stick to bottom only when the user was already near the bottom; otherwise prior scroll position is preserved where practical.

## Snapshot polling behavior after fix

- `/api/engineering/snapshot` continues polling every 15 seconds.
- Snapshot polling still updates dashboard-rendered engineering fields.
- When Chat is selected, snapshot polling no longer exposes the initial loading placeholder, clears loaded messages, clears textarea draft, or resets send button state.
- Selected Chat tab remains selected.

## Chat polling behavior after fix

- `/api/engineering/chat/history` continues polling every 15 seconds.
- Chat polling updates messages in place through `renderChatHistory()`.
- Successful history recovery updates messages without duplicating existing messages.
- History failure preserves last-known messages and shows a bounded stale/unavailable indicator.

## Regression test added

Added `test_chat_state_survives_snapshot_poll_without_loading_reset_or_draft_loss`.

It reproduces the exact bug by:

1. Rendering dashboard script.
2. Selecting Chat.
3. Loading chat history.
4. Verifying messages appear.
5. Setting textarea draft and simulated sending button state.
6. Triggering normal snapshot polling/render.
7. Verifying messages remain visible, loading text does not replace them, Chat remains selected, draft text survives, and send button state survives.
8. Triggering another snapshot poll and verifying no duplicate messages.
9. Simulating snapshot failure and verifying Chat state persists.
10. Triggering chat-history recovery and verifying new messages update normally without duplication.

Existing tests continue covering history failure preserving last-known messages, send failure preserving history, malicious message escaping, no full-page reload behavior, existing send behavior, and dashboard tabs/polling.

## Validation

- Focused dashboard/chat/provider tests: `.venv/bin/pytest tests/test_dashboard_api_app.py tests/test_dashboard_chat_gateway.py tests/test_dashboard_api_provider.py -q` → `44 passed, 2 warnings`
- Full safe suite: `.venv/bin/pytest -q` → `803 passed, 84 warnings`
- `git diff --check` → PASS
- DOM cycling harness: covered by focused regression test with repeated snapshot polls, snapshot failure, and chat-history recovery.

## Files changed

- `dashboard_api/app.py`
- `tests/test_dashboard_api_app.py`
- `MENTOR.md`
- `ITERATION_PROGRESS_LOG.md`
- `REPORT.md`
- `reports/2026-08-24_005000_engineering-dashboard-chat-flicker-fix.md`

## Risks / follow-ups

- Actual mobile Safari manual 60-second observation was not possible in this environment; the Node DOM harness simulates repeated snapshot/chat polling cycles.
- The Chat panel still starts from server-rendered loading markup on first page load, which is expected before first history response.
- Existing unrelated Git GC warning remains.
