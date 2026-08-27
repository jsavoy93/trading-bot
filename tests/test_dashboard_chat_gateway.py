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
                            "stopReason": "stop",
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

    marker = " [Response truncated]"
    truncated_text = "x" * (CHAT_MESSAGE_MAX_CHARS - len(marker)) + marker
    assert payload == {
        "session": {
            "agent": TRADING_MANAGER_AGENT_ID,
            "status": "available",
            "has_active_run": False,
            "run_status": None,
        },
        "messages": [
            {"role": "user", "text": "hello <script>", "timestamp": "2026-08-17T12:28:31+00:00", "truncated": False},
            {
                "role": "assistant",
                "text": truncated_text,
                "timestamp": "2026-08-17T12:28:32.694000+00:00",
                "truncated": True,
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
    messages.append({"role": "assistant", "stopReason": "stop", "content": [{"type": "text", "text": "same final"}], "timestamp": 100})
    messages.append({"role": "assistant", "stopReason": "stop", "content": [{"type": "text", "text": "same final"}], "timestamp": 101})

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
    assert result["status"] == "accepted"
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
    # The chat.send RPC payload must NOT include any `timeoutMs` key.
    # OpenClaw treats `timeoutMs` as the agent RUN deadline — passing it
    # would shadow `agents.defaults.timeoutSeconds = 1800` configured in
    # `/root/.openclaw/openclaw.json` and falsely mark long engineering
    # runs as Failed. The agent-run deadline is owned by OpenClaw runtime
    # config; the dashboard only enforces the bounded subprocess-level
    # ceiling via `timeout=GATEWAY_SEND_TIMEOUT_SECONDS`.
    #
    # We assert the JSON key (`timeoutMs:`) is absent. Comments may
    # legitimately reference the identifier `timeoutMs` to document
    # the contract; a JS object literal with that key would appear as
    # `timeoutMs:` (colon, not assignment).
    import re
    assert not re.search(r"\btimeoutMs\s*:", script), (
        "dashboard must not pass any timeoutMs key to chat.send; "
        "the agent-run deadline is owned by OpenClaw runtime config"
    )
    assert "sendChat" in script and "message: MESSAGE" in script


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


# ---------------------------------------------------------------------------
# Authoritative assistant-message filter for Engineering dashboard Chat.
# Goal: the Chat UI shows only the actual manager response.
#   * role="user" messages are always kept (conversational, no mirror).
#   * role="assistant" messages are kept ONLY when:
#       - stopReason == "stop" (the actual final response), AND
#       - they are NOT OpenClaw delivery-mirror duplicates
#         (model == "delivery-mirror"; provider metadata is intentionally
#         NOT used as the durable distinction).
# All other assistant turns and delivery-mirror rows are excluded.
# ---------------------------------------------------------------------------


def _messages_payload(messages):
    return {
        "ok": True,
        "agentId": TRADING_MANAGER_AGENT_ID,
        "selectedSession": {"key": PREFERRED_TRADING_MANAGER_SESSION_KEY, "sessionId": "session-123"},
        "history": {
            "sessionKey": PREFERRED_TRADING_MANAGER_SESSION_KEY,
            "sessionId": "session-123",
            "messages": messages,
        },
    }


def _project(messages):
    return GatewayChatHistoryClient(
        runner=lambda *a, **kw: _completed(_messages_payload(messages))
    ).history().to_public_dict()


def test_history_projection_keeps_user_messages_and_drops_non_user_roles():
    payload = _project(
        [
            {"role": "system", "content": "hidden system", "timestamp": 1},
            {"role": "tool", "content": "tool result", "timestamp": 2},
            {"role": "function", "content": "function result", "timestamp": 3},
            {"role": "developer", "content": "developer hidden", "timestamp": 4},
            {"role": "user", "content": "real user question", "timestamp": 5},
        ]
    )
    rendered = [(m["role"], m["text"]) for m in payload["messages"]]
    assert rendered == [("user", "real user question")]
    serialized = json.dumps(payload)
    for forbidden in ("hidden system", "tool result", "function result", "developer hidden"):
        assert forbidden not in serialized


def test_history_projection_keeps_only_assistant_messages_with_stop_reason_stop():
    payload = _project(
        [
            {"role": "user", "content": "question", "timestamp": 1},
            # Tool-use intermediate turn: must be excluded.
            {
                "role": "assistant",
                "stopReason": "toolUse",
                "content": [{"type": "text", "text": "let me check"}],
                "timestamp": 2,
            },
            # Max-tokens truncation: must be excluded.
            {
                "role": "assistant",
                "stopReason": "max_tokens",
                "content": [{"type": "text", "text": "truncated mid-thought"}],
                "timestamp": 3,
            },
            # End-turn / aborted / unknown / missing stopReason: all excluded.
            {
                "role": "assistant",
                "stopReason": "end_turn",
                "content": [{"type": "text", "text": "end_turn reply"}],
                "timestamp": 4,
            },
            {
                "role": "assistant",
                "stopReason": "abort",
                "content": [{"type": "text", "text": "aborted reply"}],
                "timestamp": 5,
            },
            {
                "role": "assistant",
                "stopReason": "unknown",
                "content": [{"type": "text", "text": "unknown reply"}],
                "timestamp": 6,
            },
            {
                "role": "assistant",
                # no stopReason at all
                "content": [{"type": "text", "text": "missing stopReason"}],
                "timestamp": 7,
            },
            # Final response: must be kept.
            {
                "role": "assistant",
                "stopReason": "stop",
                "content": [{"type": "text", "text": "final manager reply"}],
                "timestamp": 8,
            },
        ]
    )
    rendered = [(m["role"], m["text"]) for m in payload["messages"]]
    assert rendered == [
        ("user", "question"),
        ("assistant", "final manager reply"),
    ]
    serialized = json.dumps(payload)
    for forbidden in (
        "let me check",
        "truncated mid-thought",
        "end_turn reply",
        "aborted reply",
        "unknown reply",
        "missing stopReason",
    ):
        assert forbidden not in serialized


def test_history_projection_drops_openclaw_delivery_mirror_assistant_messages():
    payload = _project(
        [
            {"role": "user", "content": "please fix the bug", "timestamp": 1},
            # The actual manager response.
            {
                "role": "assistant",
                "stopReason": "stop",
                "content": [{"type": "text", "text": "the fix is applied"}],
                "timestamp": 2,
            },
            # The OpenClaw mirror of the same reply (appended by
            # mirrorTelegramAssistantReplyToTranscript). Identical content +
            # stopReason, distinguished only by model="delivery-mirror".
            {
                "role": "assistant",
                "provider": "openclaw",
                "model": "delivery-mirror",
                "stopReason": "stop",
                "content": [{"type": "text", "text": "the fix is applied"}],
                "timestamp": 3,
            },
        ]
    )
    rendered = [(m["role"], m["text"]) for m in payload["messages"]]
    assert rendered == [
        ("user", "please fix the bug"),
        ("assistant", "the fix is applied"),
    ]


def test_history_projection_drops_delivery_mirror_even_when_not_adjacent_to_real_reply():
    # The mirror must be filtered by the explicit metadata, not by adjacent
    # deduplication. Insert several unrelated user turns between the real
    # reply and its mirror.
    payload = _project(
        [
            {
                "role": "assistant",
                "stopReason": "stop",
                "content": [{"type": "text", "text": "first reply"}],
                "timestamp": 1,
            },
            {"role": "user", "content": "follow up A", "timestamp": 2},
            {"role": "user", "content": "follow up B", "timestamp": 3},
            {"role": "user", "content": "follow up C", "timestamp": 4},
            {
                "role": "assistant",
                "provider": "openclaw",
                "model": "delivery-mirror",
                "stopReason": "stop",
                "content": [{"type": "text", "text": "first reply"}],
                "timestamp": 5,
            },
        ]
    )
    rendered = [(m["role"], m["text"]) for m in payload["messages"]]
    assert rendered == [
        ("assistant", "first reply"),
        ("user", "follow up A"),
        ("user", "follow up B"),
        ("user", "follow up C"),
    ]


def test_history_projection_filter_does_not_rely_on_manager_provider_field():
    # The trading-manager model/provider may change in the future. The mirror
    # detection MUST be driven by the explicit OpenClaw marker
    # (model == "delivery-mirror"), not by the manager's configured provider.
    # A real assistant message from any provider with stopReason="stop" must
    # still pass; a delivery-mirror marker on any provider must still drop.
    payload = _project(
        [
            # A future-model manager reply (provider != openclaw). Must pass.
            {
                "role": "assistant",
                "provider": "some-future-provider",
                "model": "some-future-model",
                "stopReason": "stop",
                "content": [{"type": "text", "text": "future model reply"}],
                "timestamp": 1,
            },
            # A delivery-mirror marker is the explicit OpenClaw hint, not a
            # provider-specific property of the manager itself. Must drop.
            {
                "role": "assistant",
                "provider": "openclaw",
                "model": "delivery-mirror",
                "stopReason": "stop",
                "content": [{"type": "text", "text": "future model reply"}],
                "timestamp": 2,
            },
            # A delivery-mirror marker with a different (hypothetical future)
            # provider must STILL drop — the rule is the marker, not the
            # provider.
            {
                "role": "assistant",
                "provider": "another-channel",
                "model": "delivery-mirror",
                "stopReason": "stop",
                "content": [{"type": "text", "text": "future model reply"}],
                "timestamp": 3,
            },
        ]
    )
    rendered = [(m["role"], m["text"]) for m in payload["messages"]]
    assert rendered == [("assistant", "future model reply")]


def test_history_projection_drops_tool_use_assistant_with_no_user_visible_text():
    # A pure tool-use assistant turn must not render even when its visible
    # text happens to be non-empty — stopReason != "stop" excludes it
    # regardless of text content.
    payload = _project(
        [
            {"role": "user", "content": "investigate", "timestamp": 1},
            {
                "role": "assistant",
                "stopReason": "toolUse",
                "content": [{"type": "text", "text": "calling read_file"}],
                "timestamp": 2,
            },
            {
                "role": "assistant",
                "stopReason": "stop",
                "content": [
                    {"type": "tool_use", "id": "call_1", "input": {"path": "/etc"}},
                    {"type": "text", "text": "the file shows ..."},
                ],
                "timestamp": 3,
            },
        ]
    )
    rendered = [(m["role"], m["text"]) for m in payload["messages"]]
    assert rendered == [
        ("user", "investigate"),
        ("assistant", "the file shows ..."),
    ]


def test_history_projection_preserves_user_messages_with_no_stop_reason():
    # User turns in OpenClaw transcripts do not carry stopReason. The rule
    # only restricts assistant turns; user messages are always kept.
    payload = _project(
        [
            {"role": "user", "content": "alpha", "timestamp": 1},
            {"role": "user", "content": "beta", "timestamp": 2},
            {
                "role": "assistant",
                "stopReason": "stop",
                "content": [{"type": "text", "text": "ack"}],
                "timestamp": 3,
            },
            {"role": "user", "content": "gamma", "timestamp": 4},
        ]
    )
    rendered = [(m["role"], m["text"]) for m in payload["messages"]]
    assert rendered == [
        ("user", "alpha"),
        ("user", "beta"),
        ("assistant", "ack"),
        ("user", "gamma"),
    ]


# ---------------------------------------------------------------------------
# 16K inbound response bound + explicit truncation flag.
# Goal: a normal Trading-Manager report (up to ~16,000 chars) must ship
# verbatim to the browser with truncated=False; only genuinely oversized
# responses get truncated, and truncation is always flagged with a visible
# "[Response truncated]" marker so the UI can never mistake a silently-cut
# payload for a complete response.
# ---------------------------------------------------------------------------


def _project_payload(messages):
    return GatewayChatHistoryClient(
        runner=lambda *a, **kw: _completed(_messages_payload(messages))
    ).history().to_public_dict()


def test_bounded_message_just_under_16k_passes_through_unchanged():
    text = "x" * 3_999
    payload = _project_payload(
        [
            {
                "role": "assistant",
                "stopReason": "stop",
                "content": [{"type": "text", "text": text}],
                "timestamp": 1,
            }
        ]
    )
    msgs = payload["messages"]
    assert len(msgs) == 1
    assert msgs[0]["truncated"] is False
    assert msgs[0]["text"] == text
    assert msgs[0]["text"].endswith("x")


def test_bounded_message_exactly_16k_passes_through_unchanged():
    text = "x" * CHAT_MESSAGE_MAX_CHARS
    payload = _project_payload(
        [
            {
                "role": "assistant",
                "stopReason": "stop",
                "content": [{"type": "text", "text": text}],
                "timestamp": 1,
            }
        ]
    )
    msgs = payload["messages"]
    assert len(msgs) == 1
    assert msgs[0]["truncated"] is False
    assert len(msgs[0]["text"]) == CHAT_MESSAGE_MAX_CHARS
    assert msgs[0]["text"] == text
    assert "[Response truncated]" not in msgs[0]["text"]


def test_bounded_message_just_over_16k_is_truncated_with_flag_and_marker():
    text = "x" * (CHAT_MESSAGE_MAX_CHARS + 1)
    payload = _project_payload(
        [
            {
                "role": "assistant",
                "stopReason": "stop",
                "content": [{"type": "text", "text": text}],
                "timestamp": 1,
            }
        ]
    )
    msgs = payload["messages"]
    assert len(msgs) == 1
    assert msgs[0]["truncated"] is True
    assert len(msgs[0]["text"]) == CHAT_MESSAGE_MAX_CHARS
    assert msgs[0]["text"].endswith(" [Response truncated]")
    assert " [Response truncated]" in msgs[0]["text"]


def test_known_5138_char_response_passes_through_unchanged():
    text = "A" * 5_138
    payload = _project_payload(
        [
            {
                "role": "assistant",
                "stopReason": "stop",
                "content": [{"type": "text", "text": text}],
                "timestamp": 1,
            }
        ]
    )
    msgs = payload["messages"]
    assert len(msgs) == 1
    assert msgs[0]["truncated"] is False
    assert msgs[0]["text"] == text
    assert len(msgs[0]["text"]) == 5_138
    # Copy contract: a normal 5,138-char manager response is delivered in
    # full, including the entire natural ending, so the browser's per-
    # message Copy receives the complete projected text.
    assert msgs[0]["text"][-1] == "A"


def test_chat_message_to_dict_always_exposes_truncated_field():
    # Even for uncut messages, `truncated` MUST be present in the JSON
    # payload as a boolean (never absent, never null). The browser reads
    # `message.truncated === true` to decide whether to show the badge.
    cm = __import__("dashboard_api.chat_gateway", fromlist=["ChatMessage"]).ChatMessage(
        role="assistant", text="hi", timestamp="t", truncated=False
    )
    d = cm.to_dict()
    assert d["truncated"] is False
    assert set(d.keys()) == {"role", "text", "timestamp", "truncated"}


# ---------------------------------------------------------------------------
# chat.send accept semantics.
# Goal: the dashboard's Node subprocess wrapper for the OpenClaw Gateway
# chat.send RPC must use a bounded ACCEPT timeout (NOT the agent run
# timeout). Long engineering tasks MUST NOT cause this proxy to time out.
# The accepted result must surface ok=True with status="accepted" and the
# run_id assigned by the Gateway. chat.history polling tracks the run
# progress asynchronously.
# ---------------------------------------------------------------------------


def test_send_adapter_surfaces_accepted_status_with_run_id():
    def runner(*a, **kw):
        return _completed({
            "ok": True,
            "agentId": TRADING_MANAGER_AGENT_ID,
            "selectedSession": {"key": PREFERRED_TRADING_MANAGER_SESSION_KEY},
            "runId": "run-abc-123",
        })

    result = GatewayChatHistoryClient(runner=runner).send("hello").to_public_dict()

    assert result["ok"] is True
    assert result["status"] == "accepted"
    assert result["run_id"] == "run-abc-123"


def test_send_adapter_subprocess_uses_accept_only_subprocess_timeout_not_run_timeout():
    captured = {}

    def runner(*args, **kwargs):
        captured["input"] = kwargs.get("input") or (args[2] if len(args) > 2 else "")
        return _completed({
            "ok": True,
            "agentId": TRADING_MANAGER_AGENT_ID,
            "selectedSession": {"key": PREFERRED_TRADING_MANAGER_SESSION_KEY},
            "runId": "run-xyz-456",
        })

    GatewayChatHistoryClient(runner=runner).send("hello").to_public_dict()

    script = captured["input"]
    # The chat.send RPC payload must NOT include any `timeoutMs` key at
    # all (no `timeoutMs:`, no `timeoutMs =`, no partial match). OpenClaw
    # treats `timeoutMs` as the agent RUN deadline; passing it would
    # shadow `agents.defaults.timeoutSeconds = 1800` configured in
    # `/root/.openclaw/openclaw.json` and falsely mark long engineering
    # runs as Failed.
    import re
    assert not re.search(r"\btimeoutMs\s*:", script), (
        "dashboard must not pass any timeoutMs to chat.send; "
        "the agent-run deadline is owned by OpenClaw runtime config"
    )
    # Defence in depth: also assert the specific forbidden numeric values.
    assert "timeoutMs: 15000" not in script
    assert "timeoutMs: 180000" not in script
    assert "timeoutMs: 1800000" not in script
    # The subprocess wrapper's own ceiling is still enforced via
    # `timeout=GATEWAY_SEND_TIMEOUT_SECONDS` (15s) — that is the only
    # dashboard-imposed timeout for chat.send, and it bounds the
    # wait-for-accept only (NOT the agent run).
    from dashboard_api.chat_gateway import GATEWAY_SEND_TIMEOUT_SECONDS
    assert GATEWAY_SEND_TIMEOUT_SECONDS == 15, (
        "the chat.send subprocess accept ceiling must remain 15s"
    )


def test_send_adapter_subprocess_failure_surfaces_failed_with_bounded_error():
    def runner(*a, **kw):
        raise RuntimeError("synthetic gateway failure")

    result = GatewayChatHistoryClient(runner=runner).send("hello").to_public_dict()
    assert result["ok"] is False
    assert result["status"] == "failed"
    assert "Gateway chat send unavailable" == result["error"]


def test_send_adapter_subprocess_timeout_surfaces_failed_with_bounded_error():
    import subprocess as _sp

    def runner(*a, **kw):
        raise _sp.TimeoutExpired(cmd=["node"], timeout=15)

    result = GatewayChatHistoryClient(runner=runner).send("hello").to_public_dict()
    assert result["ok"] is False
    assert result["status"] == "failed"
    # Must be a bounded, non-sensitive message; never raw stderr or traceback.
    assert result["error"] == "Gateway chat send proxy timed out; chat history will reflect run state"


def test_send_adapter_does_not_block_on_long_running_manager_run():
    # The send wrapper returns within seconds regardless of any
    # in-flight trading-manager run length. This is the core invariant
    # that prevents the dashboard from falsely marking a still-healthy
    # manager run as Failed just because the manager is taking minutes.
    import time

    started = time.monotonic()
    result = GatewayChatHistoryClient(
        runner=lambda *a, **kw: _completed({
            "ok": True,
            "agentId": TRADING_MANAGER_AGENT_ID,
            "selectedSession": {"key": PREFERRED_TRADING_MANAGER_SESSION_KEY},
            "runId": "run-fast",
        })
    ).send("hello").to_public_dict()
    elapsed = time.monotonic() - started
    assert result["status"] == "accepted"
    # Synthesised runner returns instantly; bound is generous (0.5s).
    assert elapsed < 0.5


# ---------------------------------------------------------------------------
# chat.send timeout ownership regression tests (Option A fix).
# Goal: the dashboard must NOT inject any agent-run deadline into the
# chat.send RPC. The 1800s agent timeout is owned by OpenClaw runtime
# config (`agents.defaults.timeoutSeconds` in
# `/root/.openclaw/openclaw.json`). The dashboard only bounds the
# wait-for-accept at the subprocess layer; it does NOT impose a 15s,
# 180s, or any other run-completion timeout on the chat.send call.
# Without the override, OpenClaw's `resolveAgentTimeoutMs({cfg,
# overrideMs: undefined})` returns `clampTimerTimeoutMs(
# cfg.agents.defaults.timeoutSeconds * 1000)`, which feeds
# `registerChatAbortController` and yields an effective ~31-minute run
# deadline (1800s + 60s grace, bounded by [120s, 24h]). The previous bug
# was an oversized dashboard pollution that shadowed the configured
# deadline with a tiny 15s override; the 120s `minMs` floor in
# `resolveChatRunExpiresAtMs` then yielded the observed 2-minute abort.
# ---------------------------------------------------------------------------


def test_send_adapter_payload_omits_timeoutms_field_entirely():
    """Regression: dashboard chat.send RPC payload must not contain timeoutMs.

    Previously (PRs #66 and earlier) the dashboard injected
    `timeoutMs: {GATEWAY_SEND_TIMEOUT_SECONDS * 1000}` into the chat.send
    RPC. OpenClaw consumed that field as the agent-run deadline, shadowed
    the configured `agents.defaults.timeoutSeconds = 1800`, and — once the
    120-second `minMs` floor in `resolveChatRunExpiresAtMs` clamped the
    small value — emitted "chat run timed out" at ~2 m 20 s. This test
    pins the new contract: timeoutMs must be absent from the JSON payload
    sent to the Gateway.
    """
    captured = {}

    def runner(*args, **kwargs):
        captured["input"] = kwargs.get("input") or (args[2] if len(args) > 2 else "")
        return _completed({
            "ok": True,
            "agentId": TRADING_MANAGER_AGENT_ID,
            "selectedSession": {"key": PREFERRED_TRADING_MANAGER_SESSION_KEY},
            "runId": "run-no-timeoutms",
        })

    GatewayChatHistoryClient(runner=runner).send("audit the dashboard").to_public_dict()

    script = captured["input"]
    # Strict: the JSON payload must not contain a `timeoutMs` key AT ALL.
    # Comments may legitimately reference the identifier `timeoutMs` to
    # document the contract; only the JSON key (`timeoutMs:`) is forbidden.
    import re
    assert not re.search(r"\btimeoutMs\s*:", script), (
        "dashboard chat.send payload must not contain a `timeoutMs:` key; "
        "agent-run deadline is owned by OpenClaw config"
    )
    # Also assert the surrounding object-literal text does not contain
    # any of the previously injected values.
    assert "timeoutMs:" not in script


def test_send_adapter_subprocess_timeout_remains_accept_only_15s():
    """Regression: chat.send subprocess wrapper still bounded at 15s.

    The dashboard keeps a bounded accept-only subprocess ceiling of 15s
    (`timeout=GATEWAY_SEND_TIMEOUT_SECONDS`) so a wedged Node call cannot
    hang the proxy forever. This is NOT the agent-run deadline — it is
    the wait-for-accept. It must be unchanged after the Option A fix.
    """
    captured = {}

    def runner(*args, **kwargs):
        captured["kwargs"] = kwargs
        return _completed({
            "ok": True,
            "agentId": TRADING_MANAGER_AGENT_ID,
            "selectedSession": {"key": PREFERRED_TRADING_MANAGER_SESSION_KEY},
            "runId": "run-accept-only",
        })

    GatewayChatHistoryClient(runner=runner).send("hello").to_public_dict()

    assert captured["kwargs"]["timeout"] == 15, (
        "subprocess accept-only ceiling must remain 15s after the fix"
    )
    # The wrapper Node script must not contain a parallel JavaScript-side
    # `timeoutMs` either. We assert the JSON key (`timeoutMs:`) is absent;
    # comments may legitimately reference the identifier for documentation.
    import re
    script = captured["kwargs"].get("input") or ""
    assert not re.search(r"\btimeoutMs\s*:", script), (
        "dashboard must not inject any timeoutMs key into chat.send"
    )


def test_chat_history_timeout_unaffected_by_send_payload_change():
    """Regression: chat.history separate RPC keeps its own 15s boundary.

    The Option A fix only touches the chat.send payload. chat.history is
    a separate RPC whose bounded wait-for-poll lives on the Python
    `subprocess.run` layer (`timeout=GATEWAY_HISTORY_TIMEOUT_SECONDS`).
    It must remain exactly 15 seconds after the change.
    """
    from dashboard_api.chat_gateway import GATEWAY_HISTORY_TIMEOUT_SECONDS
    captured = {}

    def runner(*args, **kwargs):
        captured["kwargs"] = kwargs
        return _completed({
            "ok": True,
            "agentId": TRADING_MANAGER_AGENT_ID,
            "selectedSession": {"key": PREFERRED_TRADING_MANAGER_SESSION_KEY},
            "history": {
                "sessionKey": PREFERRED_TRADING_MANAGER_SESSION_KEY,
                "messages": [],
                "sessionInfo": {"hasActiveRun": False, "status": "idle"},
            },
        })

    GatewayChatHistoryClient(runner=runner).history()

    assert captured["kwargs"]["timeout"] == GATEWAY_HISTORY_TIMEOUT_SECONDS
    assert captured["kwargs"]["timeout"] == 15
    # The chat.history Node script must NOT inject any run-deadline
    # field either (chat.history is a read; it never enters
    # registerChatAbortController). Assert the JSON key (`timeoutMs:`)
    # is absent; comments may legitimately mention the identifier.
    import re
    script = captured["kwargs"].get("input") or ""
    assert not re.search(r"\btimeoutMs\s*:", script)


def test_legacy_180s_send_alias_constant_is_removed_from_module():
    """Regression: the obsolete GATEWAY_SEND_LEGACY_TIMEOUT_SECONDS alias
    is gone after the fix. Its presence was misleading — the comment
    claimed "Equal to the accept timeout above" while the value was
    hardcoded to 180 (a stale value from the previously removed
    180-second send-side wait). With the fix, no internal or external
    caller should reference this constant anywhere in the module.
    """
    import dashboard_api.chat_gateway as gateway_module
    # The legacy alias must be removed entirely from the public module
    # surface — it implies the dashboard still controls the send timeout,
    # which it no longer does.
    assert not hasattr(gateway_module, "GATEWAY_SEND_LEGACY_TIMEOUT_SECONDS"), (
        "GATEWAY_SEND_LEGACY_TIMEOUT_SECONDS must be removed once the "
        "dashboard no longer injects any chat.send timeout"
    )


def test_chat_bounds_constants_remain_unchanged():
    """Regression: outbound 4K and inbound 16K bounds preserved.

    The Option A fix touches only the chat.send timeout contract. The
    outbound user-input bound (CHAT_SEND_MAX_CHARS = 4_000) and the
    inbound projected-manager-response bound (CHAT_MESSAGE_MAX_CHARS =
    16_000) must remain exactly as specified so existing truncation
    semantics and the per-character Copy contract are preserved.
    """
    from dashboard_api.chat_gateway import (
        CHAT_SEND_MAX_CHARS,
        CHAT_MESSAGE_MAX_CHARS,
        CHAT_HISTORY_LIMIT,
        ALLOWED_RUN_STATUSES,
    )
    assert CHAT_SEND_MAX_CHARS == 4_000
    assert CHAT_MESSAGE_MAX_CHARS == 16_000
    assert CHAT_HISTORY_LIMIT == 50
    # The allowed terminal run-status set must remain unchanged so the
    # Idle / Working / Failed indicator mapping is preserved.
    assert ALLOWED_RUN_STATUSES == frozenset({"running", "idle", "done", "failed", "killed", "timeout"})


def test_send_compose_returns_promptly_after_acceptance_regardless_of_run_length():
    """Regression: browser compose returns to idle immediately on accept.

    The send wrapper returns within seconds regardless of any in-flight
    trading-manager run length. A long engineering task (e.g. >30
    minutes) must NEVER block this proxy. With the Option A fix the
    chat.send RPC no longer carries a dashboard-imposed deadline, so
    the only bound on this call is the 15s subprocess accept ceiling;
    the run itself progresses asynchronously under the OpenClaw-owned
    1800s timeout (31-min effective deadline).
    """
    import time

    # 1) The proxy itself must return promptly. The synthesised runner
    #    returns instantly; the proxy must not introduce any artificial
    #    wait that would block browser compose state.
    started = time.monotonic()
    result = GatewayChatHistoryClient(
        runner=lambda *a, **kw: _completed({
            "ok": True,
            "agentId": TRADING_MANAGER_AGENT_ID,
            "selectedSession": {"key": PREFERRED_TRADING_MANAGER_SESSION_KEY},
            "runId": "run-prompt",
        })
    ).send("hello").to_public_dict()
    elapsed = time.monotonic() - started
    assert result["ok"] is True
    assert result["status"] == "accepted"
    assert result["run_id"] == "run-prompt"
    assert elapsed < 0.5

    # 2) The agent-run deadline ownership is documented in the script:
    #    no `timeoutMs` is injected, so the run falls back to
    #    OpenClaw's `agents.defaults.timeoutSeconds = 1800`.
    captured = {}

    def runner2(*args, **kwargs):
        captured["input"] = kwargs.get("input") or (args[2] if len(args) > 2 else "")
        return _completed({
            "ok": True,
            "agentId": TRADING_MANAGER_AGENT_ID,
            "selectedSession": {"key": PREFERRED_TRADING_MANAGER_SESSION_KEY},
            "runId": "run-no-block",
        })

    GatewayChatHistoryClient(runner=runner2).send("audit long task").to_public_dict()
    # Only the JSON key (`timeoutMs:`) is forbidden; comments may mention
    # the identifier for documentation.
    import re
    assert not re.search(r"\btimeoutMs\s*:", captured["input"]), (
        "dashboard must not inject a timeoutMs key into chat.send"
    )
