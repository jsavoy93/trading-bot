from __future__ import annotations

import json
import subprocess

from dashboard_api.chat_gateway import (
    ALLOWED_RUN_STATUSES,
    CHAT_HISTORY_LIMIT,
    CHAT_MESSAGE_MAX_CHARS,
    PREFERRED_TRADING_MANAGER_SESSION_KEY,
    TRADING_MANAGER_AGENT_ID,
    GatewayChatHistoryClient,
)


def _completed(payload: dict[str, object]) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["node"], 0, stdout=json.dumps(payload), stderr="")


def test_history_adapter_uses_fixed_trading_manager_routing_and_chat_history():
    calls = []

    def runner(*args, **kwargs):
        calls.append((args, kwargs))
        return _completed(
            {
                "ok": True,
                "agentId": TRADING_MANAGER_AGENT_ID,
                "selectedSession": {"key": PREFERRED_TRADING_MANAGER_SESSION_KEY, "sessionId": "session-123"},
                "history": {"sessionKey": PREFERRED_TRADING_MANAGER_SESSION_KEY, "sessionId": "session-123", "messages": []},
            }
        )

    history = GatewayChatHistoryClient(runner=runner).history()

    assert history.status == "available"
    assert history.resolved_session_key == PREFERRED_TRADING_MANAGER_SESSION_KEY
    script = calls[0][1]["input"]
    assert "chat.history" in script or "loadHistory" in script
    assert "chat.send" not in script
    assert "chat.abort" not in script
    assert f"const AGENT_ID = \"{TRADING_MANAGER_AGENT_ID}\"" in script
    assert f"const PREFERRED_SESSION_KEY = \"{PREFERRED_TRADING_MANAGER_SESSION_KEY}\"" in script
    assert "agentId: AGENT_ID" in script
    assert "sessionKey: selected.key" in script


def test_history_projection_returns_only_safe_visible_bounded_fields():
    long_text = "x" * (CHAT_MESSAGE_MAX_CHARS + 25)
    history = GatewayChatHistoryClient(
        runner=lambda *args, **kwargs: _completed(
            {
                "ok": True,
                "agentId": TRADING_MANAGER_AGENT_ID,
                "selectedSession": {"key": PREFERRED_TRADING_MANAGER_SESSION_KEY, "sessionId": "session-123"},
                "history": {
                    "sessionKey": PREFERRED_TRADING_MANAGER_SESSION_KEY,
                    "sessionId": "session-123",
                    "messages": [
                        {"role": "system", "content": "hidden prompt", "timestamp": 1},
                        {"role": "user", "content": "hello <script>", "timestamp": 1786969711000, "tool_calls": [{"secret": "nope"}]},
                        {
                            "role": "assistant",
                            "content": [
                                {"type": "thinking", "thinking": "private reasoning"},
                                {"type": "tool_use", "input": {"raw": "args"}},
                                {"type": "text", "text": long_text},
                            ],
                            "prompt": "hidden prompt",
                            "stdout": "raw output",
                            "stderr": "raw error",
                            "timestamp": 1786969712694,
                        },
                    ],
                },
            }
        )
    ).history()

    payload = history.to_public_dict()

    assert payload == {
        "session": {
            "agent": TRADING_MANAGER_AGENT_ID,
            "status": "available",
            "has_active_run": False,
            "run_status": None,
        },
        "messages": [
            {"role": "user", "text": "hello <script>", "timestamp": "2026-08-17T12:28:31+00:00"},
            {
                "role": "assistant",
                "text": "x" * (CHAT_MESSAGE_MAX_CHARS - 1) + "…",
                "timestamp": "2026-08-17T12:28:32.694000+00:00",
            },
        ],
    }
    serialized = json.dumps(payload)
    for forbidden in ("private reasoning", "hidden prompt", "tool_use", "tool_calls", "raw output", "raw error", "secret", "in_flight_run", "inFlightRun", "textContent"):
        assert forbidden not in serialized


def test_history_projection_is_count_bounded_and_deduplicates_delivery_mirror():
    messages = []
    for index in range(CHAT_HISTORY_LIMIT + 10):
        messages.append({"role": "user", "content": f"message {index}", "timestamp": index})
    messages.append({"role": "assistant", "content": [{"type": "text", "text": "same final"}], "timestamp": 100})
    messages.append({"role": "assistant", "content": [{"type": "text", "text": "same final"}], "timestamp": 101})

    history = GatewayChatHistoryClient(
        runner=lambda *args, **kwargs: _completed(
            {
                "ok": True,
                "agentId": TRADING_MANAGER_AGENT_ID,
                "selectedSession": {"key": PREFERRED_TRADING_MANAGER_SESSION_KEY},
                "history": {"sessionKey": PREFERRED_TRADING_MANAGER_SESSION_KEY, "messages": messages},
            }
        )
    ).history()

    assert len(history.messages) == CHAT_HISTORY_LIMIT
    assert [message.text for message in history.messages].count("same final") == 1


def test_gateway_failure_returns_bounded_unavailable_state():
    def runner(*args, **kwargs):
        return subprocess.CompletedProcess(["node"], 1, stdout="", stderr="super secret traceback and token")

    payload = GatewayChatHistoryClient(runner=runner).history().to_public_dict()

    assert payload == {
        "session": {
            "agent": TRADING_MANAGER_AGENT_ID,
            "status": "unavailable",
            "has_active_run": False,
            "run_status": None,
            "reason": "RuntimeError",
        },
        "messages": [],
    }
    assert "super secret" not in json.dumps(payload)


def test_wrong_agent_resolution_is_rejected_as_unavailable():
    payload = GatewayChatHistoryClient(
        runner=lambda *args, **kwargs: _completed(
            {
                "ok": True,
                "agentId": "engineering-manager",
                "selectedSession": {"key": "agent:engineering-manager:telegram:direct:8455029949"},
                "history": {"sessionKey": "agent:engineering-manager:telegram:direct:8455029949", "messages": []},
            }
        )
    ).history().to_public_dict()

    assert payload["session"]["agent"] == TRADING_MANAGER_AGENT_ID
    assert payload["session"]["status"] == "unavailable"


def test_send_adapter_accepts_bounded_text_and_uses_fixed_trading_manager_session():
    calls = []

    def runner(*args, **kwargs):
        calls.append((args, kwargs))
        return _completed({"ok": True, "agentId": TRADING_MANAGER_AGENT_ID, "selectedSession": {"key": PREFERRED_TRADING_MANAGER_SESSION_KEY}, "runId": "run-123"})

    result = GatewayChatHistoryClient(runner=runner).send("  hello manager  ").to_public_dict()

    assert result["ok"] is True
    assert result["status"] == "sent"
    assert result["run_id"] == "run-123"
    assert result["audit"]["actor"] == "dashboard"
    assert result["audit"]["target"] == TRADING_MANAGER_AGENT_ID
    script = calls[0][1]["input"]
    assert "sendChat" in script
    assert "chat.abort" not in script
    assert f"const AGENT_ID = \"{TRADING_MANAGER_AGENT_ID}\"" in script
    assert f"const PREFERRED_SESSION_KEY = \"{PREFERRED_TRADING_MANAGER_SESSION_KEY}\"" in script
    assert "sessionKey: selected.key" in script
    assert "agentId: AGENT_ID" in script
    assert 'const MESSAGE = "hello manager"' in script


def test_send_adapter_rejects_empty_non_text_and_over_limit_without_gateway_call():
    calls = []
    client = GatewayChatHistoryClient(runner=lambda *args, **kwargs: calls.append((args, kwargs)) or _completed({"ok": True, "runId": "bad"}))

    for value in ("", "   ", {"message": "hello"}, ["hello"], None):
        payload = client.send(value).to_public_dict()
        assert payload["ok"] is False
        assert payload["status"] == "rejected"
    too_long = "x" * 4001
    payload = client.send(too_long).to_public_dict()
    assert payload["ok"] is False
    assert payload["status"] == "rejected"
    assert calls == []


def test_send_gateway_failure_returns_bounded_safe_error():
    def runner(*args, **kwargs):
        return subprocess.CompletedProcess(["node"], 1, stdout="", stderr="secret traceback token")

    payload = GatewayChatHistoryClient(runner=runner).send("hello").to_public_dict()

    assert payload["ok"] is False
    assert payload["status"] == "failed"
    assert payload["error"] == "Gateway chat send unavailable"
    assert "secret" not in json.dumps(payload)


# ---------------------------------------------------------------------------
# Agent working-indicator run-state projection (Engineering dashboard).
# Authoritative source: chat.history sessionInfo.hasActiveRun (bool) and
# sessionInfo.status (enum). inFlightRun and the streamed text under it are
# intentionally NOT projected.
# ---------------------------------------------------------------------------


def test_history_projection_exposes_idle_when_no_active_run_and_no_terminal_status():
    history = GatewayChatHistoryClient(
        runner=lambda *a, **kw: _completed(
            {
                "ok": True,
                "agentId": TRADING_MANAGER_AGENT_ID,
                "selectedSession": {"key": PREFERRED_TRADING_MANAGER_SESSION_KEY},
                "history": {
                    "sessionKey": PREFERRED_TRADING_MANAGER_SESSION_KEY,
                    "sessionId": "session-123",
                    "messages": [],
                    "sessionInfo": {"hasActiveRun": False, "status": "idle"},
                },
            }
        )
    ).history()

    payload = history.to_public_dict()

    assert payload["session"]["has_active_run"] is False
    assert payload["session"]["run_status"] == "idle"


def test_history_projection_exposes_working_when_has_active_run_true():
    history = GatewayChatHistoryClient(
        runner=lambda *a, **kw: _completed(
            {
                "ok": True,
                "agentId": TRADING_MANAGER_AGENT_ID,
                "selectedSession": {"key": PREFERRED_TRADING_MANAGER_SESSION_KEY},
                "history": {
                    "sessionKey": PREFERRED_TRADING_MANAGER_SESSION_KEY,
                    "sessionId": "session-123",
                    "messages": [],
                    "sessionInfo": {"hasActiveRun": True, "status": "running"},
                    "inFlightRun": {"runId": "abc-123", "text": "private streamed text fragment"},
                },
            }
        )
    ).history()

    payload = history.to_public_dict()

    assert payload["session"]["has_active_run"] is True
    assert payload["session"]["run_status"] == "running"
    serialized = json.dumps(payload)
    assert "in_flight_run" not in serialized
    assert "inFlightRun" not in serialized
    assert "private streamed text fragment" not in serialized
    assert "abc-123" not in serialized


def test_history_projection_exposes_failed_for_terminal_run_statuses():
    for terminal in ("failed", "killed", "timeout"):
        history = GatewayChatHistoryClient(
            runner=lambda *a, _terminal=terminal, **kw: _completed(
                {
                    "ok": True,
                    "agentId": TRADING_MANAGER_AGENT_ID,
                    "selectedSession": {"key": PREFERRED_TRADING_MANAGER_SESSION_KEY},
                    "history": {
                        "sessionKey": PREFERRED_TRADING_MANAGER_SESSION_KEY,
                        "sessionId": "session-123",
                        "messages": [],
                        "sessionInfo": {"hasActiveRun": False, "status": _terminal},
                    },
                }
            )
        ).history()
        payload = history.to_public_dict()
        assert payload["session"]["has_active_run"] is False
        assert payload["session"]["run_status"] == terminal, f"expected {terminal}"


def test_history_projection_clamps_unknown_run_status_to_none_and_treats_non_bool_as_false():
    history = GatewayChatHistoryClient(
        runner=lambda *a, **kw: _completed(
            {
                "ok": True,
                "agentId": TRADING_MANAGER_AGENT_ID,
                "selectedSession": {"key": PREFERRED_TRADING_MANAGER_SESSION_KEY},
                "history": {
                    "sessionKey": PREFERRED_TRADING_MANAGER_SESSION_KEY,
                    "sessionId": "session-123",
                    "messages": [],
                    "sessionInfo": {
                        "hasActiveRun": "true",  # not a real bool — defensive clamp
                        "status": "wibble",  # not in ALLOWED_RUN_STATUSES — bounded to None
                    },
                },
            }
        )
    ).history()

    payload = history.to_public_dict()

    assert payload["session"]["has_active_run"] is False
    assert payload["session"]["run_status"] is None


def test_history_projection_tolerates_missing_sessionInfo_gracefully():
    history = GatewayChatHistoryClient(
        runner=lambda *a, **kw: _completed(
            {
                "ok": True,
                "agentId": TRADING_MANAGER_AGENT_ID,
                "selectedSession": {"key": PREFERRED_TRADING_MANAGER_SESSION_KEY},
                "history": {
                    "sessionKey": PREFERRED_TRADING_MANAGER_SESSION_KEY,
                    "sessionId": "session-123",
                    "messages": [],
                    # sessionInfo intentionally absent
                },
            }
        )
    ).history()

    payload = history.to_public_dict()

    assert payload["session"]["status"] == "available"
    assert payload["session"]["has_active_run"] is False
    assert payload["session"]["run_status"] is None


def test_history_projection_does_not_expose_in_flight_run_object_or_streamed_text():
    history = GatewayChatHistoryClient(
        runner=lambda *a, **kw: _completed(
            {
                "ok": True,
                "agentId": TRADING_MANAGER_AGENT_ID,
                "selectedSession": {"key": PREFERRED_TRADING_MANAGER_SESSION_KEY},
                "history": {
                    "sessionKey": PREFERRED_TRADING_MANAGER_SESSION_KEY,
                    "sessionId": "session-123",
                    "messages": [],
                    "sessionInfo": {"hasActiveRun": True, "status": "running"},
                    "inFlightRun": {"runId": "uuid-9", "text": "leaked-model-reasoning-trace"},
                },
            }
        )
    ).history()

    serialized = json.dumps(history.to_public_dict())

    for forbidden in ("inFlightRun", "in_flight_run", "uuid-9", "leaked-model-reasoning-trace"):
        assert forbidden not in serialized
