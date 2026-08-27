from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable, Mapping, Sequence

TRADING_MANAGER_AGENT_ID = "trading-manager"
PREFERRED_TRADING_MANAGER_SESSION_KEY = "agent:trading-manager:telegram:direct:8455029949"
CHAT_HISTORY_LIMIT = 50
# Inbound / projected manager-response hard bound. The size of text we are
# willing to ship to the browser in a single projected assistant message.
# Deliberately kept a bounded safety limit (not unlimited) so a runaway
# model or malformed transcript cannot blow up the dashboard payload. 16,000
# chars comfortably covers any realistic structured Trading-Manager report
# (~3x headroom over the typical 5,138-char report we observed). When this
# bound IS hit, the projection sets `truncated=True` on the message so the
# UI can show an explicit "Response truncated" indicator and API consumers
# can never mistake a silently-cut payload for the complete response.
CHAT_MESSAGE_MAX_CHARS = 16_000
# Outbound user-input hard bound (Josh -> Trading Manager). Separate from
# the inbound response bound. Enforced as a rejection, never as a silent cut.
CHAT_SEND_MAX_CHARS = 4_000
GATEWAY_HISTORY_TIMEOUT_SECONDS = 15
# Send-accept timeout for the dashboard's Node subprocess wrapping the
# OpenClaw Gateway `chat.send` RPC. `chat.send` semantics are accepted-with-
# runId: the Gateway returns as soon as the run has been queued and a
# run_id has been assigned; the manager then continues asynchronously and
# the dashboard tracks progress via the chat.history poll. The dashboard
# MUST NOT block on the run and MUST NOT pass any run-deadline timeout to
# the `chat.send` RPC payload. Doing so would either (a) falsely mark any
# long-running engineering task as Failed when the underlying run is
# healthy, or (b) override the authoritative OpenClaw `agents.defaults.
# timeoutSeconds` (currently 1800s) configured in
# `/root/.openclaw/openclaw.json`. The agent-run deadline is therefore
# owned entirely by OpenClaw runtime config — the dashboard only enforces
# this bounded subprocess-level ceiling so a wedged Node call cannot hang
# the proxy forever. 15 seconds is generous: the Node subprocess +
# waitForReady + listSessions + sendChat round-trip has been observed at
# ~4 seconds in production.
GATEWAY_SEND_TIMEOUT_SECONDS = 15

# Terminal run-state values from OpenClaw Gateway sessionInfo.status.
# Anything outside this set is surfaced as None (the dashboard treats it as Idle).
ALLOWED_RUN_STATUSES = frozenset({"running", "idle", "done", "failed", "killed", "timeout"})


@dataclass(frozen=True)
class ChatMessage:
    role: str
    text: str
    timestamp: str | None
    # `truncated` is `True` only when the server-side projection cut the text
    # because it exceeded `CHAT_MESSAGE_MAX_CHARS`. The browser and any other
    # API consumer must treat this as a visible signal that the rendered text
    # is incomplete (the UI shows a "Response truncated" indicator; Copy may
    # copy only the bounded projected text but must not claim it is complete).
    truncated: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "role": self.role,
            "text": self.text,
            "timestamp": self.timestamp,
            "truncated": bool(self.truncated),
        }


@dataclass(frozen=True)
class ChatHistory:
    agent: str
    status: str
    messages: tuple[ChatMessage, ...]
    unavailable_reason: str | None = None
    resolved_session_key: str | None = None
    resolved_session_id: str | None = None
    # Authoritative run-state fields sourced from OpenClaw Gateway chat.history
    # (`sessionInfo.hasActiveRun` and `sessionInfo.status`). Surface only the
    # minimum bounded fields needed by the dashboard's working-indicator:
    # a boolean for in-flight, and the bounded enum for terminal state. The
    # raw streamed text under `inFlightRun.text` is intentionally NOT exposed.
    has_active_run: bool = False
    run_status: str | None = None

    def to_public_dict(self) -> dict[str, object]:
        session: dict[str, object] = {
            "agent": self.agent,
            "status": self.status,
            "has_active_run": bool(self.has_active_run),
            "run_status": self.run_status,
        }
        if self.unavailable_reason:
            session["reason"], _ = _bounded_text(self.unavailable_reason, 160)
        return {"session": session, "messages": [message.to_dict() for message in self.messages]}


@dataclass(frozen=True)
class ChatSendResult:
    # `status` semantics:
    #   "accepted" — Gateway has accepted the run and assigned a run_id;
    #                manager continues asynchronously. Browser compose MUST
    #                return to idle and the chat history poll MUST take over
    #                progress tracking. This is the success path for any run
    #                of any length, including runs longer than the dashboard
    #                proxy timeout.
    #   "sent"     — legacy alias retained for compatibility with consumers
    #                that branched on the prior one-word status. Same
    #                semantics as "accepted" (Gateway accepted the run).
    #   "rejected" — pre-RPC rejection (empty message, oversize message).
    #   "failed"   — RPC failed or returned ok!=true. The error field carries
    #                a bounded, non-sensitive reason.
    ok: bool
    status: str
    run_id: str | None = None
    error: str | None = None
    timestamp: str | None = None

    def to_public_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "ok": self.ok,
            "status": self.status,
            "audit": {
                "timestamp": self.timestamp,
                "actor": "dashboard",
                "source": "dashboard",
                "target": TRADING_MANAGER_AGENT_ID,
                "delivery_status": self.status,
                "run_id": self.run_id,
            },
        }
        if self.run_id:
            payload["run_id"] = self.run_id
        if self.error:
            payload["error"], _ = _bounded_text(self.error, 160)
        return payload


class GatewayChatHistoryClient:
    """Read a fixed OpenClaw trading-manager chat history through Gateway RPC.

    The browser never supplies agent/session/Gateway routing. This adapter scopes
    session discovery to the trading-manager agent, prefers Josh's existing
    Telegram direct session when it still exists, and only calls chat.history.
    """

    def __init__(
        self,
        *,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        timeout_seconds: int = GATEWAY_HISTORY_TIMEOUT_SECONDS,
    ) -> None:
        self._runner = runner
        self._timeout_seconds = timeout_seconds

    def history(self) -> ChatHistory:
        try:
            raw = self._call_gateway_history()
            payload = json.loads(raw)
            return _project_gateway_history(payload)
        except Exception as exc:  # noqa: BLE001 - public API must expose bounded unavailable state only.
            return ChatHistory(
                agent=TRADING_MANAGER_AGENT_ID,
                status="unavailable",
                messages=(),
                unavailable_reason=exc.__class__.__name__,
            )

    def send(self, message: object) -> ChatSendResult:
        normalized = _normalize_send_message(message)
        if normalized is None:
            return ChatSendResult(ok=False, status="rejected", error="message must be non-empty text", timestamp=_now_iso())
        if len(normalized) > CHAT_SEND_MAX_CHARS:
            return ChatSendResult(ok=False, status="rejected", error="message exceeds 4000 characters", timestamp=_now_iso())
        try:
            raw = self._call_gateway_send(normalized)
            payload = json.loads(raw)
            if payload.get("ok") is not True:
                return ChatSendResult(ok=False, status="failed", error="delivery failed", timestamp=_now_iso())
            run_id = _string_or_none(payload.get("runId"))
            # Gateway has accepted the run and assigned a run_id. The manager
            # continues asynchronously; the dashboard MUST NOT block here. The
            # browser composes to idle immediately and chat.history polling
            # tracks progress. We surface status="accepted" so the browser can
            # distinguish a real accept from a transport-level "sent" claim.
            return ChatSendResult(
                ok=True,
                status="accepted",
                run_id=run_id,
                timestamp=_now_iso(),
            )
        except subprocess.TimeoutExpired:
            # Defensive: the Node subprocess wrapper itself timed out before
            # the Gateway could return. The run is still very likely healthy
            # on the OpenClaw side — the dashboard simply lost its response.
            # Surface this as a transport-level failure so the browser compose
            # recovers, and trust chat.history polling to surface the real
            # terminal status when the run eventually completes.
            return ChatSendResult(
                ok=False,
                status="failed",
                error="Gateway chat send proxy timed out; chat history will reflect run state",
                timestamp=_now_iso(),
            )
        except Exception:  # noqa: BLE001 - never expose Gateway stderr/tracebacks to browser.
            return ChatSendResult(ok=False, status="failed", error="Gateway chat send unavailable", timestamp=_now_iso())

    def _call_gateway_history(self) -> str:
        script = _gateway_history_node_script()
        result = self._runner(
            ["node", "--input-type=module"],
            input=script,
            text=True,
            capture_output=True,
            check=False,
            timeout=self._timeout_seconds,
        )
        if result.returncode != 0:
            raise RuntimeError("OpenClaw Gateway chat history is unavailable")
        return result.stdout

    def _call_gateway_send(self, message: str) -> str:
        script = _gateway_send_node_script(message)
        result = self._runner(
            ["node", "--input-type=module"],
            input=script,
            text=True,
            capture_output=True,
            check=False,
            timeout=GATEWAY_SEND_TIMEOUT_SECONDS,
        )
        if result.returncode != 0:
            raise RuntimeError("OpenClaw Gateway chat send is unavailable")
        return result.stdout


class UnavailableChatHistoryClient:
    def history(self) -> ChatHistory:
        return ChatHistory(agent=TRADING_MANAGER_AGENT_ID, status="unavailable", messages=(), unavailable_reason="not_configured")

    def send(self, message: object) -> ChatSendResult:
        return ChatSendResult(ok=False, status="failed", error="not_configured", timestamp=_now_iso())


def _gateway_history_node_script() -> str:
    return f"""
import {{ GatewayChatClient }} from '/usr/lib/node_modules/openclaw/dist/gateway-chat-Bdx06tul.js';

const AGENT_ID = {json.dumps(TRADING_MANAGER_AGENT_ID)};
const PREFERRED_SESSION_KEY = {json.dumps(PREFERRED_TRADING_MANAGER_SESSION_KEY)};
const LIMIT = {CHAT_HISTORY_LIMIT};

function scoreSession(session) {{
  const key = typeof session?.key === 'string' ? session.key : '';
  const provider = session?.origin?.provider || session?.lastChannel || '';
  const updatedAt = typeof session?.updatedAt === 'number' ? session.updatedAt : 0;
  if (key === PREFERRED_SESSION_KEY) return Number.MAX_SAFE_INTEGER;
  if (provider === 'telegram' && key.includes(':telegram:direct:')) return Number.MAX_SAFE_INTEGER - 1 + Math.min(updatedAt, 999999999999);
  return updatedAt;
}}

const client = await GatewayChatClient.connect({{}});
client.start();
try {{
  await client.waitForReady();
  const sessionsResult = await client.listSessions({{ agentId: AGENT_ID, limit: 100 }});
  const sessions = Array.isArray(sessionsResult?.sessions) ? sessionsResult.sessions : [];
  const selected = sessions.slice().sort((a, b) => scoreSession(b) - scoreSession(a))[0];
  if (!selected?.key) {{
    console.log(JSON.stringify({{ ok: false, unavailableReason: 'trading_manager_session_not_found', agentId: AGENT_ID }}));
  }} else {{
    const history = await client.loadHistory({{ sessionKey: selected.key, agentId: AGENT_ID, limit: LIMIT }});
    console.log(JSON.stringify({{ ok: true, agentId: AGENT_ID, selectedSession: selected, history }}));
  }}
}} finally {{
  client.stop();
}}
"""


def _gateway_send_node_script(message: str) -> str:
    return f"""
import {{ GatewayChatClient }} from '/usr/lib/node_modules/openclaw/dist/gateway-chat-Bdx06tul.js';

const AGENT_ID = {json.dumps(TRADING_MANAGER_AGENT_ID)};
const PREFERRED_SESSION_KEY = {json.dumps(PREFERRED_TRADING_MANAGER_SESSION_KEY)};
const MESSAGE = {json.dumps(message)};

function scoreSession(session) {{
  const key = typeof session?.key === 'string' ? session.key : '';
  const provider = session?.origin?.provider || session?.lastChannel || '';
  const updatedAt = typeof session?.updatedAt === 'number' ? session.updatedAt : 0;
  if (key === PREFERRED_SESSION_KEY) return Number.MAX_SAFE_INTEGER;
  if (provider === 'telegram' && key.includes(':telegram:direct:')) return Number.MAX_SAFE_INTEGER - 1 + Math.min(updatedAt, 999999999999);
  return updatedAt;
}}

const client = await GatewayChatClient.connect({{}});
client.start();
try {{
  await client.waitForReady();
  const sessionsResult = await client.listSessions({{ agentId: AGENT_ID, limit: 100 }});
  const sessions = Array.isArray(sessionsResult?.sessions) ? sessionsResult.sessions : [];
  const selected = sessions.slice().sort((a, b) => scoreSession(b) - scoreSession(a))[0];
  if (!selected?.key) {{
    console.log(JSON.stringify({{ ok: false, unavailableReason: 'trading_manager_session_not_found', agentId: AGENT_ID }}));
  }} else {{
    const result = await client.sendChat({{
      sessionKey: selected.key,
      agentId: AGENT_ID,
      ...(selected.sessionId ? {{ sessionId: selected.sessionId }} : {{}}),
      message: MESSAGE
      // NOTE: the dashboard intentionally does NOT pass a `timeoutMs`
      // field to `chat.send`. OpenClaw treats that field as the agent RUN
      // deadline (which lands directly in `registerChatAbortController`),
      // so passing any value here would silently shadow the configured
      // `agents.defaults.timeoutSeconds = 1800` in
      // `/root/.openclaw/openclaw.json` and either falsely time out long
      // engineering tasks (when the override is small) or bypass the
      // operator-owned deadline entirely (when the override is large).
      // The agent-run deadline is therefore owned entirely by OpenClaw
      // runtime config; the dashboard only enforces the bounded
      // subprocess-level ceiling via `timeout={GATEWAY_SEND_TIMEOUT_SECONDS}`.
    }});
    console.log(JSON.stringify({{ ok: true, agentId: AGENT_ID, selectedSession: selected, runId: result.runId }}));
  }}
}} finally {{
  client.stop();
}}
"""


def _project_gateway_history(payload: Mapping[str, Any]) -> ChatHistory:
    if payload.get("ok") is False:
        return ChatHistory(
            agent=TRADING_MANAGER_AGENT_ID,
            status="unavailable",
            messages=(),
            unavailable_reason=str(payload.get("unavailableReason") or "not_found"),
        )

    if payload.get("agentId") != TRADING_MANAGER_AGENT_ID:
        raise RuntimeError("resolved session does not belong to trading-manager")

    selected = payload.get("selectedSession") if isinstance(payload.get("selectedSession"), Mapping) else {}
    history = payload.get("history") if isinstance(payload.get("history"), Mapping) else {}
    session_key = _string_or_none(history.get("sessionKey")) or _string_or_none(selected.get("key"))
    session_id = _string_or_none(history.get("sessionId")) or _string_or_none(selected.get("sessionId"))
    if not session_key:
        raise RuntimeError("missing resolved trading-manager session")

    raw_messages = history.get("messages") if isinstance(history.get("messages"), Sequence) else ()
    messages = tuple(_dedupe_messages(_project_message(message) for message in raw_messages))[-CHAT_HISTORY_LIMIT:]
    session_info = history.get("sessionInfo") if isinstance(history.get("sessionInfo"), Mapping) else {}
    # Authoritative in-flight indicator from the Gateway's chatAbortControllers
    # registry. `in_flight_run` and the streamed text under it are intentionally
    # NOT projected here; the indicator only needs the on/off state.
    has_active_run = _bounded_bool(session_info.get("hasActiveRun"))
    run_status = _allowed_run_status(session_info.get("status"))
    return ChatHistory(
        agent=TRADING_MANAGER_AGENT_ID,
        status="available",
        messages=messages,
        resolved_session_key=session_key,
        resolved_session_id=session_id,
        has_active_run=has_active_run,
        run_status=run_status,
    )


def _project_message(value: Any) -> ChatMessage | None:
    if not isinstance(value, Mapping):
        return None
    if _is_delivery_mirror_message(value):
        # OpenClaw delivery-mirror duplicates the actual outbound assistant reply
        # back into the transcript so other channels see it. The marker is the
        # explicit `model == "delivery-mirror"` field that OpenClaw writes from
        # `mirrorTelegramAssistantReplyToTranscript`; the message otherwise has
        # identical role/content/stopReason to the real response. We must drop
        # the mirror so the Chat UI does not double-render the same final reply.
        return None
    role = _safe_role(value.get("role"))
    if role is None:
        return None
    if role == "assistant" and not _is_assistant_final_stop_reason(value.get("stopReason")):
        # Assistant messages with stopReason != "stop" are intermediate model
        # turns (toolUse calls, max_tokens truncations, aborted runs, ...). Only
        # the actual final response (stopReason == "stop") is surfaced to the
        # Chat UI. User messages have no stopReason requirement.
        return None
    text = _extract_visible_text(value.get("content"))
    if not text:
        return None
    bounded_text, truncated = _bounded_text(text, CHAT_MESSAGE_MAX_CHARS)
    return ChatMessage(
        role=role,
        text=bounded_text,
        timestamp=_timestamp(value.get("timestamp")),
        truncated=truncated,
    )


def _dedupe_messages(messages: Sequence[ChatMessage | None]) -> list[ChatMessage]:
    projected: list[ChatMessage] = []
    previous: ChatMessage | None = None
    for message in messages:
        if message is None:
            continue
        if previous and previous.role == message.role and previous.text == message.text:
            continue
        projected.append(message)
        previous = message
    return projected


def _safe_role(value: object) -> str | None:
    if value == "user":
        return "user"
    if value == "assistant":
        return "assistant"
    return None


def _is_assistant_final_stop_reason(value: object) -> bool:
    # The Chat UI only shows the actual manager response, which OpenClaw marks
    # with stopReason == "stop". Tool-use intermediate turns, max_tokens
    # truncations, aborts, and messages missing stopReason metadata are
    # excluded by design. This avoids rendering model internals and keeps the
    # dashboard representation faithful to the public Telegram conversation.
    return value == "stop"


def _is_delivery_mirror_message(value: Mapping[str, Any]) -> bool:
    # OpenClaw's `mirrorTelegramAssistantReplyToTranscript` writes outbound
    # assistant replies back into the transcript with `model == "delivery-mirror"`
    # (and provider == "openclaw") so cross-channel replay stays consistent.
    # The mirror is byte-equivalent to the real response apart from those
    # marker fields, so the Chat UI would otherwise show the final reply twice.
    #
    # The durable distinction is the explicit `model == "delivery-mirror"`
    # marker written by OpenClaw itself. We deliberately do NOT key on the
    # trading-manager's configured model/provider, so this filter survives any
    # future model change for the trading-manager agent.
    return value.get("role") == "assistant" and value.get("model") == "delivery-mirror"


def _extract_visible_text(content: object) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, Sequence) or isinstance(content, (bytes, bytearray)):
        return ""
    parts: list[str] = []
    for item in content:
        if not isinstance(item, Mapping):
            continue
        if item.get("type") != "text":
            continue
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            parts.append(text.strip())
    return "\n\n".join(parts).strip()


def _timestamp(value: object) -> str | None:
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 10_000_000_000 else value
        return datetime.fromtimestamp(seconds, tz=UTC).isoformat()
    if isinstance(value, str):
        text, _ = _bounded_text(value, 80)
        return text
    return None


def _bounded_text(value: object, limit: int) -> tuple[str, bool]:
    """Bound a string to `limit` characters.

    Returns a tuple `(text, was_truncated)`. When truncation happens the text
    is cut to `limit - 2` characters and the explicit `[Response truncated]`
    marker is appended so the cut is never silent. The marker is intentionally
    bracketed and ASCII so it survives any downstream consumer (clipboard,
    email, JSON, ...) without rendering ambiguity.
    """
    text = str(value)
    if len(text) <= limit:
        return text, False
    # Reserve 25 chars for the marker so it is unambiguously visible inside
    # the bounded payload and the total length never exceeds `limit`.
    marker = " [Response truncated]"
    keep = max(0, limit - len(marker))
    return text[:keep] + marker, True


def _string_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _bounded_bool(value: object) -> bool:
    return value is True


def _allowed_run_status(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    return text if text in ALLOWED_RUN_STATUSES else None


def _normalize_send_message(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()
