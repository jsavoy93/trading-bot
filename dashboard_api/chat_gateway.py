from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable, Mapping, Sequence

TRADING_MANAGER_AGENT_ID = "trading-manager"
PREFERRED_TRADING_MANAGER_SESSION_KEY = "agent:trading-manager:telegram:direct:8455029949"
CHAT_HISTORY_LIMIT = 50
CHAT_MESSAGE_MAX_CHARS = 4_000
CHAT_SEND_MAX_CHARS = 4_000
GATEWAY_HISTORY_TIMEOUT_SECONDS = 15
GATEWAY_SEND_TIMEOUT_SECONDS = 180


@dataclass(frozen=True)
class ChatMessage:
    role: str
    text: str
    timestamp: str | None

    def to_dict(self) -> dict[str, object]:
        return {"role": self.role, "text": self.text, "timestamp": self.timestamp}


@dataclass(frozen=True)
class ChatHistory:
    agent: str
    status: str
    messages: tuple[ChatMessage, ...]
    unavailable_reason: str | None = None
    resolved_session_key: str | None = None
    resolved_session_id: str | None = None

    def to_public_dict(self) -> dict[str, object]:
        session: dict[str, object] = {"agent": self.agent, "status": self.status}
        if self.unavailable_reason:
            session["reason"] = _bounded_text(self.unavailable_reason, 160)
        return {"session": session, "messages": [message.to_dict() for message in self.messages]}


@dataclass(frozen=True)
class ChatSendResult:
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
            payload["error"] = _bounded_text(self.error, 160)
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
            return ChatSendResult(ok=True, status="sent", run_id=run_id, timestamp=_now_iso())
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
      message: MESSAGE,
      timeoutMs: {GATEWAY_SEND_TIMEOUT_SECONDS * 1000}
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
    return ChatHistory(
        agent=TRADING_MANAGER_AGENT_ID,
        status="available",
        messages=messages,
        resolved_session_key=session_key,
        resolved_session_id=session_id,
    )


def _project_message(value: Any) -> ChatMessage | None:
    if not isinstance(value, Mapping):
        return None
    role = _safe_role(value.get("role"))
    if role is None:
        return None
    text = _extract_visible_text(value.get("content"))
    if not text:
        return None
    return ChatMessage(role=role, text=_bounded_text(text, CHAT_MESSAGE_MAX_CHARS), timestamp=_timestamp(value.get("timestamp")))


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
        return _bounded_text(value, 80)
    return None


def _bounded_text(value: object, limit: int) -> str:
    text = str(value)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _string_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _normalize_send_message(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()
