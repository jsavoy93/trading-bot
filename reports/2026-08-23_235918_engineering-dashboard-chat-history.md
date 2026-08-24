# Engineering Dashboard Chat — Slice 1 Read-only History

## Summary

Implemented a read-only Chat tab for `/engineering` that displays bounded visible history from the existing OpenClaw `trading-manager` conversation. The dashboard user cannot send messages, abort/interrupt work, choose an arbitrary agent/session, or access Gateway credentials.

## Scope

- Project: `trading-bot`
- Branch: `agent/dashboard-chat-history`
- Dashboard route: `/engineering`
- Read endpoint: `GET /api/engineering/chat/history`
- Gateway method used: `chat.history`
- Fixed agent: `trading-manager`
- Preferred existing session: `agent:trading-manager:telegram:direct:8455029949`

## Resolved session

Manual verification resolved:

- agent: `trading-manager`
- session key: `agent:trading-manager:telegram:direct:8455029949`
- session id: `e31c5f61-2d71-4078-bc59-eab62ef92067`
- model: `MiniMax-M2.7`

Only one `trading-manager` session was present before/after read verification. Reading history did not create a new OpenClaw session.

## Implementation

- Added `dashboard_api/chat_gateway.py` as a small isolated server-side adapter.
- Adapter uses OpenClaw Gateway chat machinery through `GatewayChatClient` and calls `chat.history` only.
- Session discovery is scoped server-side to `trading-manager`; browser input cannot supply agent id, session key, Gateway URL/token, or RPC method.
- Added `GET /api/engineering/chat/history` returning only bounded safe public fields.
- Added seventh mobile-first dashboard tab: `Chat`.
- Preserved existing tabs: Overview, Activity, Backlog, Timeline, Reports, Health.
- Preserved 15s snapshot polling, selected-tab persistence, stale warning behavior, and no full-page refresh.
- Added 15s bounded chat history polling; no WebSocket, send box, send endpoint, or abort endpoint.

## Safe history projection

Public response shape:

```json
{
  "session": {"agent": "trading-manager", "status": "available"},
  "messages": [
    {"role": "user", "text": "...", "timestamp": "..."},
    {"role": "assistant", "text": "...", "timestamp": "..."}
  ]
}
```

Bounds and filters:

- maximum messages: 50
- maximum message text length: 4,000 characters
- roles allowed: `user`, `assistant`
- assistant content projected only from text blocks
- thinking/private reasoning blocks excluded
- hidden prompts excluded
- tool calls/tool arguments/tool results excluded
- raw stdout/stderr excluded
- secrets/Gateway credentials excluded
- duplicate delivery-mirror assistant rows deduplicated
- Gateway failures return bounded unavailable state without raw stderr/traceback

## Validation

- Focused chat/dashboard/provider tests:
  - `.venv/bin/pytest tests/test_dashboard_chat_gateway.py tests/test_dashboard_api_app.py tests/test_dashboard_api_provider.py -q`
  - Result: `39 passed, 2 warnings`
- Full safe suite:
  - `.venv/bin/pytest -q`
  - Result: `798 passed, 82 warnings`
- `git diff --check`: PASS
- Manual route/Gateway verification:
  - `/engineering` contains Chat tab and no send box.
  - `GET /api/engineering/chat/history?agentId=bad&sessionKey=bad&gatewayUrl=https://evil.example&method=chat.send` returned 200 with `{'agent': 'trading-manager', 'status': 'available'}` and did not echo client-supplied routing.
  - `POST /api/engineering/chat/send` returned 404.
  - `POST /api/engineering/chat/abort` returned 404.
  - History returned 2 safe visible messages from the existing Telegram manager session.
- Telegram compatibility verification:
  - `openclaw channels status --probe --channel telegram --account manager` showed Telegram manager connected/running/works after the history read.
  - Session updatedAt stayed `1786969718201`; no new `trading-manager` session was created.

## Risks / Blockers

- No known implementation blockers.
- The adapter currently shells out to the local OpenClaw Node Gateway chat client because the dashboard is Python and no narrow Python Gateway chat client exists in this repo.
- Gateway auth remains server-side only, but the dashboard process must run where OpenClaw Gateway auth/config is available.
- Actual mobile Safari/Termius verification was not available in this environment; local ASGI/JS harnesses and route checks passed.

## Slice 2 recommendation

If Josh approves Slice 2, add a separate bounded text-only `POST /api/engineering/chat/send` endpoint that calls Gateway `chat.send` server-side for the same fixed `trading-manager` target and existing resolved session. Keep browser input limited to message text, impose size/rate bounds, audit delivery status, and continue to defer `chat.abort`/interrupt/control actions.
