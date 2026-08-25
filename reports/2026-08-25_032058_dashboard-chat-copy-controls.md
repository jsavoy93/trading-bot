# Dashboard Chat Copy Controls — DASH-007

## Summary

- **Backlog item**: DASH-007 — Engineering Dashboard Chat copy controls
- **Branch**: `agent/dashboard-chat-copy-controls`
- **Base commit**: `145f5cf` (PR #63, stopReason + delivery-mirror filter)
- **Final commit**: see `git log -1`
- **Status**: DONE — pending Josh PR review
- **Files changed**: `dashboard_api/app.py`, `tests/test_dashboard_api_app.py`,
  `AGENT_BACKLOG.md`, `MENTOR.md`, `ITERATION_PROGRESS_LOG.md`, `REPORT.md`,
  this archive.

## Recovery Note

This task was started in a previous run that timed out before tests,
commit, and PR creation. The recovered uncommitted work included:

- Complete CSS additions: `.chat-message-actions`, `.chat-copy`,
  `.chat-status-row`, `.chat-since-copy`, plus idle/copied/failed
  state variants for both buttons.
- `_chat_tab()` HTML update: `.chat-status-row` wrapper containing the
  existing chat-status indicator and the new `#chat-copy-since` button
  (initially `hidden disabled`).
- JS template `chatTab()` update with the same structure.
- `chatStateCache` extended with `messages`, `sinceLastUserText`,
  `copyTimers`.
- `renderChatHistory` extended to: clear pending copy-reset timers on
  re-render, store the projected messages array, compute the since-last
  payload, render a per-message Copy button on each assistant card
  (with `data-copy-index`), and update the since-copy button visibility
  / disabled state.
- New JS helpers: `writeClipboardText` (with `navigator.clipboard`
  + bounded error path), `setCopyState`, `cancelCopyTimer`,
  `scheduleCopyReset`, `handlePerMessageCopy`, `handleSinceCopy`,
  `computeSinceLastUserText`, `updateSinceCopyButton`,
  `bindChatCopyControls`.
- New `bindChatCopyControls` is invoked from `switchTab`,
  `restoreChatUiState`, `refreshDashboard`, and once at script
  initialization, so handlers survive both `#chat-history` re-render
  and `#dashboard-content` snapshot-poll replacement.
- AGENT_BACKLOG entry DASH-007 added (IN_PROGRESS) with approved scope,
  allowed areas, explicit exclusions, acceptance criteria, and tests.

The recovery run then:

1. Hardened `bindChatCopyControls` against DOM mocks that lack
   `addEventListener` (defensive `typeof === 'function'` guard).
2. Added a test-injection hook `window.__chatClipboardWriteText` so the
   test harness can stub the clipboard under headless Node 24 (where
   `navigator` is a read-only global without `clipboard`).
3. Added 10 focused tests in `tests/test_dashboard_api_app.py`.
4. Re-verified `git diff --check` (clean) and ran focused dashboard,
   gateway, and full safe suite.

## Authoritative Design

The copy controls operate ONLY on the already-projected visible chat
messages (server projection from PR #63). Hidden progress / toolUse /
delivery-mirror / system rows can never leak into the clipboard.

### Per-message Copy button

- Rendered on every assistant (`role="assistant"`) card; never on
  user cards.
- Each button carries `data-copy-index="${i}"` keyed to the projected
  messages array stored in `chatStateCache.messages`.
- On click: `handlePerMessageCopy` reads
  `chatStateCache.messages[parseInt(button.dataset.copyIndex, 10)]`
  and writes the plain text to the clipboard via
  `writeClipboardText(text)`.
- After a successful copy the button label changes to "Copied" for
  ~1.8s and then returns to "Copy".
- On clipboard failure the button label becomes "Copy failed" for
  ~2.4s, a bounded failure banner is shown in the chat state area, and
  the chat history is not mutated. The button restores itself to
  "Copy" when the timer fires.
- No HTML extraction or DOM scraping is used: the source of truth is
  the projected plain-text message.

### "Copy since my last message" button

- Header-level button placed in a `.chat-status-row` next to the
  agent status indicator, easy to reach on mobile without dominating
  the UI.
- Initially `hidden disabled`. Once at least one assistant message
  exists after the most recent user message it becomes visible and
  enabled. If no user message exists at all, all assistant rows are
  joined (matches the spec's "after the most recent user message"
  semantics with the natural fallback).
- The payload is computed by `computeSinceLastUserText(messages)`,
  which finds the most recent `role="user"` index in the projected
  array and joins all `role="assistant"` rows after it with exactly
  two newline characters (`\n\n`). No timestamps, speaker names,
  separators, JSON, HTML, or UI metadata are added.
- When a new user turn arrives, the boundary resets automatically:
  `computeSinceLastUserText` is re-run on every history poll and the
  button label/state are refreshed.

## Behavior Preserved

All PRs #56–#63 behavior remains intact:

- Same fixed `trading-manager` agent routing and preferred Telegram
  session key.
- Same `GatewayChatHistoryClient` adapter, same `CHAT_HISTORY_LIMIT =
  50`, `CHAT_MESSAGE_MAX_CHARS = 4_000`, `GATEWAY_HISTORY_TIMEOUT_SECONDS
  = 15`.
- Same 15-second snapshot polling (`POLL_INTERVAL_MS = 15000`) and
  15-second chat-history polling (`CHAT_POLL_INTERVAL_MS = 15000`).
- Same snapshot-poll state cache survival (chat-history HTML, draft,
  scroll position, compose status, agent indicator) including the new
  `messages`, `sinceLastUserText`, and `copyTimers` fields.
- Same Idle / Working… / Failed indicator projection from
  `sessionInfo.hasActiveRun` + `sessionInfo.status`.
- Same send-state behavior (sending / failure / recovery) unchanged.
- Same bounded error handling, escape, content-type filtering.
- No mutation of the underlying OpenClaw transcript.

## Acceptance Criteria

| # | Criterion | Method | Result |
|---|---|---|---|
| 1 | Per-message Copy button exists on every assistant card and only on assistant cards | `test_chat_history_renders_per_message_copy_button_only_on_assistant_cards` | PASS |
| 2 | Per-message Copy writes exactly the message text (no metadata) and toggles "Copied" then restores "Copy" | `test_chat_copy_per_message_writes_plain_text_only_and_toggles_copied_state` | PASS |
| 3 | Clipboard-write failure path: bounded failure state surfaced, button restores, chat history untouched | `test_chat_copy_per_message_clipboard_failure_shows_bounded_state_and_restores_button` | PASS |
| 4 | "Copy since my last message" boundary correctness: assistant rows after the last user message are joined by `\n\n`; nothing before is included; the user message itself is excluded | `test_chat_copy_since_button_joins_after_last_user_message_with_double_newline` | PASS |
| 5 | "Copy since my last message" disabled when no assistant after last user | `test_chat_copy_since_button_disabled_when_no_assistant_after_last_user` | PASS |
| 6 | "Copy since my last message" enabled when only orphan assistant rows exist (no user) | `test_chat_copy_since_button_disabled_when_no_user_messages_at_all` | PASS |
| 7 | Copy controls survive snapshot poll that replaces the dashboard DOM | `test_chat_copy_controls_survive_snapshot_poll_replacing_dashboard_content` | PASS |
| 8 | Since-copy payload cannot include PR #63-filtered rows (toolUse / delivery-mirror / system) | `test_chat_copy_uses_projected_messages_not_raw_transcript_so_pr63_filter_holds` | PASS |
| 9 | HTML template contains since-copy button + status row wrapper | `test_chat_tab_html_renders_since_copy_button_and_status_row` | PASS |
| 10 | Inner JS wires copy helpers + since-button DOM | `test_chat_tab_inner_script_wires_copy_helpers_and_since_button_dom` | PASS |
| 11 | All prior dashboard / chat / gateway tests still pass | full safe suite | PASS |

## Tests Run

- Focused dashboard app: `45 passed, 2 warnings`
  (35 existing + 10 new for DASH-007).
- Focused chat gateway: `21 passed, 1 warning` (untouched).
- Combined dashboard / chat: `66 passed, 2 warnings`.
- Full safe suite: `832 passed, 85 warnings` (baseline 822 + 10 new).
- `git diff --check` → PASS.

## Manual Verification

Live `TestClient(create_app()).get('/api/engineering/chat/history')` →
HTTP 200, `session.agent=trading-manager`,
`session.status=available`, projection filter intact (no `toolResult` /
`toolUse` / `delivery-mirror` rows leak through), copy controls render
correctly in the served HTML (`id="chat-copy-since"` and
`class="chat-status-row"` are present). The currently active run has
the user message pushed past the 50-message tail so the live projection
returns 0 messages; the new since-copy button is rendered initially
hidden+disabled and only becomes visible once a final manager response
arrives.

## Risks

- The clipboard helper depends on the test injection hook
  `window.__chatClipboardWriteText` for headless-Node tests. In a real
  browser, the hook is undefined so `navigator.clipboard.writeText` is
  used. If a future browser denies clipboard access, the failure path
  surfaces a bounded banner and the chat history is unchanged.
- `bindChatCopyControls` is called from multiple re-bind points; the
  `dataset.copyBound` / `dataset.sinceBound` flags make the bind
  idempotent, so multiple invocations do not stack duplicate handlers.
- A stale restore timer pointing at a detached node is cleared at the
  start of every `renderChatHistory`, so it cannot mutate DOM that no
  longer exists after a poll.

## Decision Required

Josh: review the PR and approve merge. The new controls are pure
client-side additions that operate only on the already-projected chat
history, so there is no server-side or transcript-side change.

## Next Action

- Commit on `agent/dashboard-chat-copy-controls`.
- Push branch and open PR.
- STOP for Josh review.