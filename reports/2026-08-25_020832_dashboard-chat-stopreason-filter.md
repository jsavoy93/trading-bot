# Dashboard Chat — StopReason + Delivery-Mirror Filter

## Summary

- **Backlog item**: DASH-007 (slice 3) — authoritative assistant-message filter for
  the Engineering Dashboard Chat tab.
- **Branch**: `agent/dashboard-chat-stopreason-filter`
- **Base commit**: `6608e78` (PR #62, agent working indicator)
- **Final commit**: see `git log -1` after final commit
- **Status**: DONE — pending Josh PR review
- **Files changed**: `dashboard_api/chat_gateway.py`, `tests/test_dashboard_chat_gateway.py`,
  `MENTOR.md`, `ITERATION_PROGRESS_LOG.md`, `REPORT.md`, this archive.

## Authoritative Filtering Rule

The Chat UI shows only the actual manager conversation. Three categories survive:

1. **`role="user"` messages** — always kept (conversational, no mirror per OpenClaw).
2. **`role="assistant"` messages** — kept **only** when **both** of these hold:
   - `stopReason == "stop"` (the actual final response, not an intermediate turn)
   - NOT an OpenClaw delivery-mirror (`model != "delivery-mirror"`)
4. **All other rows** are dropped: `system`, `tool`, `function`, `developer`, `toolResult`,
   assistant `toolUse` turns, assistant `max_tokens` / `end_turn` / `abort` / unknown /
   missing stopReason rows, and OpenClaw delivery-mirror duplicates.

The filter is driven by:
- `role` (allow-list: user, assistant)
- `stopReason` (allow-list on assistant: exactly `"stop"`)
- explicit OpenClaw `model == "delivery-mirror"` marker

We deliberately do **not** key on the trading-manager's configured model/provider,
so the filter survives any future model change. The mirror marker is written by
OpenClaw's own `mirrorTelegramAssistantReplyToTranscript` (see
`/usr/lib/node_modules/openclaw/dist/bot-B-OxOCyH.js`) and matches the
`isOpenClawDeliveryMirrorAssistantMessage` predicate in
`/usr/lib/node_modules/openclaw/dist/transcript-hciCrqol.js`.

## Why Provider Filtering Was NOT Required

We verified that removing any provider-specific requirement does **not** allow
delivery mirrors through, because:

- Mirror detection uses `model == "delivery-mirror"`, an explicit OpenClaw-written
  marker that is independent of the manager's provider.
- The `provider: "openclaw"` field is present on mirrors as well, but we do **not**
  require it — if OpenClaw ever changes the mirror marker, the explicit `model`
  field still identifies the mirror correctly.
- A real manager response with any provider/model (current `minimax-portal/MiniMax-M3`
  or any future replacement) still passes, as long as `stopReason == "stop"` and
  `model != "delivery-mirror"`.

This is documented in code comments in `_is_delivery_mirror_message` and exercised
by the `test_history_projection_filter_does_not_rely_on_manager_provider_field`
test, which proves that a future-model manager reply is kept while both OpenClaw
and hypothetical-other-provider delivery mirrors are still dropped.

## Files Changed

| File | Change |
|---|---|
| `dashboard_api/chat_gateway.py` | Add `_is_assistant_final_stop_reason` and `_is_delivery_mirror_message` helpers; update `_project_message` to apply both rules before extracting text. |
| `tests/test_dashboard_chat_gateway.py` | Add `stopReason="stop"` to two pre-existing legitimate assistant fixtures; add 7 new focused tests covering: role allow-list, assistant stopReason filter, mirror filter, non-adjacent mirror filter, provider-independence, tool-use-no-text case, user messages with no stopReason. |
| `MENTOR.md` | Document the authoritative filter in the Chat tab section. |
| `ITERATION_PROGRESS_LOG.md` | Append the iteration entry. |

## Behavior Preserved

Everything from PRs #58–#62 remains green:
- Same fixed `trading-manager` agent routing and preferred Telegram session key
- Same `GatewayChatHistoryClient` adapter
- Same `CHAT_HISTORY_LIMIT = 50`, `CHAT_MESSAGE_MAX_CHARS = 4_000`,
  `GATEWAY_HISTORY_TIMEOUT_SECONDS = 15`
- Same `sessionInfo.hasActiveRun` + `sessionInfo.status` projection for the agent
  working indicator (Idle / Working… / Failed / Loading…)
- Same 15-second `setInterval(refreshChatHistory, CHAT_POLL_INTERVAL_MS)` cadence
- Same Chat UI state cache (`historyHtml`, `agentStatus`, `composeStatus`, scroll)
  survival across snapshot polling
- Same `setAgentStatus` / `setComposeState` separation (send failure does not set
  Working; only authoritative Gateway state projects Working)
- Same bounded error handling (`UnavailableChatHistoryClient` fallback,
  `reason: _bounded_text(...)`, no Gateway stderr / tracebacks exposed)
- Same HTML escaping (`esc(...)`) and content-type filtering (`type != "text"` dropped)
- No mutation of the underlying OpenClaw transcript; the adapter only reads through
  `chat.history`.

## Acceptance Criteria

| # | Criterion | Method | Result |
|---|---|---|---|
| 1 | Role allow-list: only `user` and `assistant` survive | `test_history_projection_keeps_user_messages_and_drops_non_user_roles` | PASS |
| 2 | Assistant `stopReason != "stop"` rows are excluded (toolUse, max_tokens, end_turn, abort, unknown, missing) | `test_history_projection_keeps_only_assistant_messages_with_stop_reason_stop` | PASS |
| 3 | OpenClaw delivery-mirror rows (`model == "delivery-mirror"`) are excluded even when adjacent to the real reply | `test_history_projection_drops_openclaw_delivery_mirror_assistant_messages` | PASS |
| 4 | Mirror rows are excluded even when NOT adjacent (filter must not rely on adjacent dedup) | `test_history_projection_drops_delivery_mirror_even_when_not_adjacent_to_real_reply` | PASS |
| 5 | Filter does not depend on the manager's provider/model (durable distinction) | `test_history_projection_filter_does_not_rely_on_manager_provider_field` | PASS |
| 6 | Pure toolUse assistant turns with text content are excluded | `test_history_projection_drops_tool_use_assistant_with_no_user_visible_text` | PASS |
| 7 | User messages are kept regardless of missing stopReason | `test_history_projection_preserves_user_messages_with_no_stop_reason` | PASS |
| 8 | Existing test for safe visible fields remains green after adding `stopReason="stop"` to the legitimate assistant fixture | `test_history_projection_returns_only_safe_visible_bounded_fields` | PASS |
| 9 | Existing dedup test remains green after adding `stopReason="stop"` to both adjacent duplicates | `test_history_projection_is_count_bounded_and_deduplicates_delivery_mirror` | PASS |
| 10 | Working indicator, send-state, history bounds, escaping, content-type filtering all preserved | Focused dashboard tests + manual `/api/engineering/chat/history` HTTP probe | PASS |

## Tests Run

- Focused chat gateway tests
  `.venv/bin/python -m pytest tests/test_dashboard_chat_gateway.py -q`
  → `21 passed, 1 warning` (14 existing + 7 new).
- Focused dashboard / chat / provider / read-model / timeline tests
  `.venv/bin/python -m pytest tests/test_dashboard_chat_gateway.py tests/test_dashboard_api_app.py tests/test_dashboard_api_provider.py tests/test_dashboard_engineering_read_model.py tests/test_dashboard_timeline.py -q`
  → `113 passed, 2 warnings`.
- Full safe suite `.venv/bin/python -m pytest -q`
  → `822 passed, 86 warnings` (baseline 815 → 822 = +7 new tests).
- `git diff --check` → PASS (no whitespace/tab issues).

## Manual Verification

Live OpenClaw Gateway call against the actual `trading-manager`
session `agent:trading-manager:telegram:direct:8455029949`
(session id `b9c6ece4-a1ef-4554-bab6-5ff1e5d560cc`,
configured model `minimax-portal/MiniMax-M3`, status `running`).

Raw tail (limit=200) summary:
- 108 messages, distribution `(role, stopReason)`:
  - `toolResult`: 60 (filtered out by `_safe_role`)
  - `assistant toolUse`: 47 (filtered out by `_is_assistant_final_stop_reason`)
  - `user`: 1 (kept)

Pre-fix projection would have leaked **13 intermediate `assistant toolUse` messages
with text content** (model narrating its plan between tool calls), presenting them
to the dashboard as if they were real manager replies:

```
text: I'm reading MENTOR.md for instructions, then proceeding.
text: Let me check the existing structure of test fixtures more carefully ...
text: I have a clear picture. Let me set up the plan and create a branch.
text: Now let me verify the updated file and look at how the existing tests ...
text: Now let me update the existing test fixture ...
... (13 total)
```

Post-fix projection correctly returns **1 message** — the actual user message:

```
role=user | text=Approved. Proceed with implementation on a dedicated branch. Use the authoritative metadata distincti...
```

HTTP probe via `TestClient(create_app()).get("/api/engineering/chat/history")`:
- Status: 200
- Session preserved: `{agent: "trading-manager", status: "available",
  has_active_run: false, run_status: "running"}` (working indicator unchanged)
- Messages: correctly filtered set per the new rule.

The user message is currently scrolled out of the 50-message tail (manager is
mid-run, status=`running`); once the run completes and a `stopReason="stop"`
assistant row lands, it will surface as the next assistant message after the
user turn. Verified by:
- `test_history_projection_keeps_only_assistant_messages_with_stop_reason_stop`
  (final-stop assistant kept; toolUse / max_tokens / end_turn / abort / unknown /
  missing-stopReason excluded).
- Live filter wiring unchanged for non-message fields (`sessionInfo.hasActiveRun`,
  `sessionInfo.status`); `setAgentStatus(projectAgentStatus(...))` still projects
  Idle / Working… / Failed correctly.

## Risks

- **Operational**: if a manager run in this session ever produces a non-`"stop"`
  final stopReason (e.g., `end_turn` from a future OpenClaw schema), the final
  reply would not surface. The current OpenClaw transcript code
  (`attempt-execution-CgTGShuY.js`, `mirrorTelegramAssistantReplyToTranscript`)
  consistently writes `stopReason: "stop"` for completed runs, so this is
  theoretical. The fix matches the durable field documented in
  `agent-YgzFw64q.js` and `agent-command-DU8vcwED.js`.
- **Discovery**: the filter assumes OpenClaw continues to write `model:
  "delivery-mirror"` for mirrors. The mirror-marker field name is part of
  OpenClaw's stable transcript schema and is asserted by OpenClaw's own
  `isOpenClawDeliveryMirrorAssistantMessage`; the risk of the field name changing
  without notice is low. If it ever does, the worst case is a duplicated assistant
  reply in the Chat UI, not a security regression.
- **No migration required**: this is a pure projection change; no DB or OpenClaw
  transcript is mutated. The full suite stayed green throughout.

## Decision Required

Josh: review the PR and approve merge. No follow-up implementation slice is
blocked on this — it is a strict tightening of the Chat projection.

## Next Action

- Commit on `agent/dashboard-chat-stopreason-filter`.
- Push branch and open PR.
- STOP for Josh review.