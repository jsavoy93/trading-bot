"""
Tests for the Engineering Dashboard chat send-limit increase (PR CHAT-LIMIT-2026-09-23).

OLD effective limit: 4,000 characters (hardcoded in 4 places).
NEW effective limit: 32,000 characters (CHAT_SEND_MAX_CHARS = 32_000).

Coverage:
  - Just-at-old-limit (4,000 chars) accepted (regression check)
  - 20,000-char message accepted (Josh's target)
  - 32,000-char message accepted (exact ceiling)
  - 32,001-char message rejected with length-aware error
  - 50,000-char message rejected (well over ceiling)
  - Multiline / code-block message preserved byte-for-byte end-to-end
  - Backend enforces CHAT_SEND_MAX_CHARS even if frontend is bypassed
  - Empty/whitespace/non-string inputs still rejected
  - Server-rendered HTML reflects new ceiling (maxlength=32000)
  - Server-rendered HTML includes character-counter element
  - JS template references CHAT_SEND_MAX_CHARS (no hardcoded 4000)
"""
from __future__ import annotations

import json
import re
import subprocess
import textwrap

import pytest


# ---------------------------------------------------------------------------
# Imports under test
# ---------------------------------------------------------------------------

from dashboard_api.chat_gateway import (
    CHAT_SEND_MAX_CHARS,
    GatewayChatHistoryClient,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ok_completed(payload: dict) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        ["node"], 0, stdout=json.dumps(payload), stderr=""
    )


def _accepted_completed(run_id: str = "run-1") -> subprocess.CompletedProcess:
    return _ok_completed({"ok": True, "runId": run_id})


def _gateway_send_max_chars() -> int:
    """Expose the constant for direct assertions."""
    assert CHAT_SEND_MAX_CHARS == 32_000, (
        f"CHAT_SEND_MAX_CHARS expected 32_000 but is {CHAT_SEND_MAX_CHARS}"
    )
    return CHAT_SEND_MAX_CHARS


# ---------------------------------------------------------------------------
# Helpers for byte-for-byte message extraction from the rendered Node script.
# The dashboard's `_gateway_send_node_script` embeds the message as
# `const MESSAGE = {json.dumps(message)};`. The helper extracts and JSON-
# decodes that literal so tests can assert the round-trip is exact.
# ---------------------------------------------------------------------------

def _extract_message_from_script(script: str) -> str | None:
    """Extract the `MESSAGE = <json-dumped-string>;` literal from the
    rendered Node script. Returns None if not found.
    """
    lines = script.splitlines()
    for line in lines:
        stripped = line.lstrip()
        if not stripped.startswith("const MESSAGE = "):
            continue
        rhs = stripped[len("const MESSAGE = "):].rstrip().rstrip(";").strip()
        try:
            return json.loads(rhs)
        except json.JSONDecodeError:
            return None
    return None


# ---------------------------------------------------------------------------
# 1. Constant — single source of truth
# ---------------------------------------------------------------------------

def test_chat_send_max_chars_is_32000() -> None:
    """The new ceiling is exactly 32,000 characters."""
    assert _gateway_send_max_chars() == 32_000


def test_chat_send_max_chars_above_josh_target() -> None:
    """The ceiling is comfortable headroom over Josh's 20,000-char target."""
    assert _gateway_send_max_chars() >= 20_000


def test_chat_send_max_chars_below_inbound_response_bound() -> None:
    """Outbound user-input ceiling stays below the inbound-response 64K bound."""
    from dashboard_api.chat_gateway import CHAT_MESSAGE_MAX_CHARS
    assert _gateway_send_max_chars() <= CHAT_MESSAGE_MAX_CHARS


# ---------------------------------------------------------------------------
# 2. Backend GatewayChatHistoryClient.send — byte-for-byte preservation + bounds
# ---------------------------------------------------------------------------

def test_send_accepts_just_at_old_limit_4000_chars() -> None:
    """Regression: messages at the old 4,000-char ceiling remain accepted."""
    captured = []

    def runner(*args, **kwargs):
        captured.append(kwargs.get("input", ""))
        return _accepted_completed()

    client = GatewayChatHistoryClient(runner=runner)
    message = "x" * 4_000  # exactly at the OLD ceiling
    result = client.send(message)
    assert result.ok is True
    assert result.status == "accepted"
    assert len(captured) == 1
    extracted = _extract_message_from_script(captured[0])
    assert extracted == message
    assert len(extracted) == 4_000


def test_send_accepts_josh_target_20000_chars() -> None:
    """Josh's 20,000-char target message is accepted and forwarded verbatim."""
    captured = []

    def runner(*args, **kwargs):
        captured.append(kwargs.get("input", ""))
        return _accepted_completed()

    client = GatewayChatHistoryClient(runner=runner)
    message = ("Y" * 19_999) + "Z"  # 20,000 chars; ensure last char distinct
    result = client.send(message)
    assert result.ok is True
    assert result.status == "accepted"
    assert len(captured) == 1
    extracted = _extract_message_from_script(captured[0])
    assert extracted == message
    assert len(extracted) == 20_000


def test_send_accepts_exact_ceiling_32000_chars() -> None:
    """Messages at exactly the new ceiling (32,000 chars) are accepted."""
    captured = []

    def runner(*args, **kwargs):
        captured.append(kwargs.get("input", ""))
        return _accepted_completed()

    client = GatewayChatHistoryClient(runner=runner)
    message = "a" * 32_000
    result = client.send(message)
    assert result.ok is True
    assert result.status == "accepted"
    assert len(captured) == 1
    extracted = _extract_message_from_script(captured[0])
    assert extracted == message
    assert len(extracted) == 32_000


def test_send_rejects_one_over_ceiling_32001_chars() -> None:
    """A message one character over the ceiling is rejected, not truncated."""
    captured = []

    def runner(*args, **kwargs):
        captured.append(kwargs.get("input", ""))
        return _accepted_completed()

    client = GatewayChatHistoryClient(runner=runner)
    message = "x" * 32_001
    result = client.send(message)
    assert result.ok is False
    assert result.status == "rejected"
    # The error message includes the raw constant value (32000) and the
    # formatted version (32,000) so the dashboard can surface either.
    assert "32000" in (result.error or ""), (
        f"rejection error must include ceiling value 32000; got: {result.error!r}"
    )
    assert len(captured) == 0, "rejected message must not have been forwarded"


def test_send_rejects_well_over_ceiling_50000_chars() -> None:
    """A 50K-char message is rejected without truncation or forwarding."""
    captured = []

    def runner(*args, **kwargs):
        captured.append(kwargs.get("input", ""))
        return _accepted_completed()

    client = GatewayChatHistoryClient(runner=runner)
    message = "z" * 50_000
    result = client.send(message)
    assert result.ok is False
    assert result.status == "rejected"
    assert len(captured) == 0


def test_send_preserves_multiline_and_code_block_verbatim() -> None:
    """Multiline prompts and code blocks survive the full round-trip
    character-for-character. This is the explicit byte-for-byte proof."""
    captured = []

    def runner(*args, **kwargs):
        captured.append(kwargs.get("input", ""))
        return _accepted_completed()

    client = GatewayChatHistoryClient(runner=runner)
    message = (
        "Hi manager,\n"
        "\n"
        "Please review this multi-line prompt.\n"
        "It includes:\n"
        "  1. A code block:\n"
        "```python\n"
        "def hello():\n"
        "    print('hello world')\n"
        "    return 42\n"
        "```\n"
        "\n"
        "  2. Tabs\tand\tspecial\x00chars.\n"
        "  3. Unicode: \u4e2d\u6587 \U0001f44d \u00e9\u00e0\u00fc.\n"
        "\n"
        "End of message."
    )
    result = client.send(message)
    assert result.ok is True
    assert len(captured) == 1
    received = _extract_message_from_script(captured[0])
    # Byte-for-byte equality
    assert received == message, (
        f"Multiline message mutated by send(). "
        f"original len={len(message)} received len={len(received)}"
    )
    # Explicit structural checks (defense-in-depth)
    assert "\n```python\n" in received
    assert "```\n" in received
    assert "\u4e2d\u6587" in received
    assert "\U0001f44d" in received
    assert "\t" in received  # tab character preserved


def test_send_preserves_unicode_20000_chars() -> None:
    """Unicode characters (e.g. CJK + emoji) count by code-point, not byte.
    A 20,000-codepoint message with CJK content is preserved verbatim."""
    captured = []

    def runner(*args, **kwargs):
        captured.append(kwargs.get("input", ""))
        return _accepted_completed()

    client = GatewayChatHistoryClient(runner=runner)
    # Use 4-byte emoji to also exercise UTF-8 byte expansion.
    message = "\U0001f44d" * 20_000  # 20,000 thumbs-up codepoints
    result = client.send(message)
    assert result.ok is True
    extracted = _extract_message_from_script(captured[0])
    assert extracted == message
    assert len(extracted) == 20_000


def test_send_empty_string_still_rejected() -> None:
    """Empty / whitespace-only messages continue to be rejected (unchanged)."""
    captured = []

    def runner(*args, **kwargs):
        captured.append(kwargs.get("input", ""))
        return _accepted_completed()

    client = GatewayChatHistoryClient(runner=runner)
    for empty in ("", "   ", "\n\n", "\t\t"):
        result = client.send(empty)
        assert result.ok is False
        assert result.status == "rejected"
        assert "non-empty" in (result.error or "").lower() or "empty" in (result.error or "").lower()
    assert len(captured) == 0


def test_send_non_string_still_rejected() -> None:
    """Non-string inputs continue to be rejected (unchanged)."""
    captured = []

    def runner(*args, **kwargs):
        captured.append(kwargs.get("input", ""))
        return _accepted_completed()

    client = GatewayChatHistoryClient(runner=runner)
    for non_text in (None, 12345, ["x"], {"message": "x"}, True, 3.14):
        result = client.send(non_text)
        assert result.ok is False
        assert result.status == "rejected"
    assert len(captured) == 0


def test_send_rejected_message_never_calls_gateway() -> None:
    """A rejected message MUST NOT be forwarded to the Gateway subprocess."""
    calls = []

    def runner(*args, **kwargs):
        calls.append((args, kwargs))
        return _accepted_completed()

    client = GatewayChatHistoryClient(runner=runner)
    over_limit = "x" * 32_001
    result = client.send(over_limit)
    assert result.ok is False
    assert result.status == "rejected"
    assert len(calls) == 0, (
        f"rejected message triggered {len(calls)} subprocess call(s); "
        f"the reject path MUST short-circuit before invoking the runner"
    )


# ---------------------------------------------------------------------------
# 3. Server-rendered HTML — maxlength, counter, limit indicator
# ---------------------------------------------------------------------------

def _dashboard_module():
    """Lazy import to avoid collection-time errors when fastapi missing."""
    import dashboard_api.app as app  # noqa: PLC0415
    return app


def test_server_html_textarea_maxlength_matches_ceiling() -> None:
    """Server-rendered textarea uses the new maxlength value."""
    app = _dashboard_module()
    html = app._chat_tab()
    expected = "'32000'"
    assert expected in html, (
        f"server-rendered textarea maxlength expected to contain {expected}; "
        f"the OLD '4000' must NOT be present"
    )
    assert "'4000'" not in html, (
        "server-rendered textarea still contains the OLD maxlength='4000'"
    )


def test_server_html_includes_counter_and_limit_elements() -> None:
    """Character counter and limit indicator must render near the textarea."""
    app = _dashboard_module()
    html = app._chat_tab()
    assert "id='chat-message-counter'" in html, (
        "character counter span missing from server-rendered HTML"
    )
    assert "id='chat-message-limit'" in html, (
        "limit indicator span missing from server-rendered HTML"
    )
    assert "class='chat-input-meta'" in html, (
        "container wrapper missing from server-rendered HTML"
    )


def test_server_html_limit_text_uses_formatted_ceiling() -> None:
    """The limit indicator must show 32,000 (with thousands separator)."""
    app = _dashboard_module()
    html = app._chat_tab()
    # Format with thousands separator
    assert "32,000" in html, (
        f"server-rendered limit text must include formatted ceiling '32,000'; "
        f"snippet: {html[html.find('chat-message-limit'):html.find('chat-message-limit')+200]}"
    )


# ---------------------------------------------------------------------------
# 4. JS template — chatTab template uses CHAT_SEND_MAX_CHARS (no hardcoded 4000)
# ---------------------------------------------------------------------------

def test_js_template_no_hardcoded_4000_for_maxlength() -> None:
    """The chatTab template must reference CHAT_SEND_MAX_CHARS, not 4000."""
    app = _dashboard_module()
    script = app._refresh_script()
    script = script.replace(
        "__SNAPSHOT_ROUTE__", app.SNAPSHOT_ROUTE
    ).replace(
        "__CHAT_HISTORY_ROUTE__", app.CHAT_HISTORY_ROUTE
    ).replace(
        "__CHAT_SEND_ROUTE__", app.CHAT_SEND_ROUTE
    ).replace(
        "__CHAT_HISTORY_DURABLE_ROUTE__", app.CHAT_HISTORY_DURABLE_ROUTE
    ).replace(
        "__CHAT_SEND_MAX_CHARS__", str(app.CHAT_SEND_MAX_CHARS)
    )
    match = re.search(r"const chatTab = \(\) => `(.*?)`;", script, re.DOTALL)
    assert match is not None, "could not extract chatTab template"
    template = match.group(1)
    # maxlength attribute uses the constant, not a hardcoded number
    assert "maxlength=\"${CHAT_SEND_MAX_CHARS}\"" in template, (
        f"chatTab template must interpolate CHAT_SEND_MAX_CHARS in maxlength; "
        f"got template head: {template[:300]}"
    )
    assert "maxlength=\"4000\"" not in template, (
        "chatTab template still hardcodes the OLD 4000"
    )
    assert "maxlength=\"32000\"" not in template, (
        "chatTab template should interpolate CHAT_SEND_MAX_CHARS, not hardcode 32000"
    )
    assert template.count("CHAT_SEND_MAX_CHARS") >= 2, (
        "expected at least 2 CHAT_SEND_MAX_CHARS references in chatTab template "
        "(maxlength + limit indicator)"
    )


def test_js_template_includes_counter_and_limit_indicator() -> None:
    """The chatTab template wires the counter and limit-indicator elements."""
    app = _dashboard_module()
    script = app._refresh_script()
    script = script.replace(
        "__SNAPSHOT_ROUTE__", app.SNAPSHOT_ROUTE
    ).replace(
        "__CHAT_HISTORY_ROUTE__", app.CHAT_HISTORY_ROUTE
    ).replace(
        "__CHAT_SEND_ROUTE__", app.CHAT_SEND_ROUTE
    ).replace(
        "__CHAT_HISTORY_DURABLE_ROUTE__", app.CHAT_HISTORY_DURABLE_ROUTE
    ).replace(
        "__CHAT_SEND_MAX_CHARS__", str(app.CHAT_SEND_MAX_CHARS)
    )
    match = re.search(r"const chatTab = \(\) => `(.*?)`;", script, re.DOTALL)
    assert match is not None
    template = match.group(1)
    assert "id=\"chat-message-counter\"" in template
    assert "id=\"chat-message-limit\"" in template


def test_js_sendchatmessage_validation_uses_constant() -> None:
    """The sendChatMessage() JS-side length check references the constant."""
    app = _dashboard_module()
    script = app._refresh_script()
    script = script.replace(
        "__SNAPSHOT_ROUTE__", app.SNAPSHOT_ROUTE
    ).replace(
        "__CHAT_HISTORY_ROUTE__", app.CHAT_HISTORY_ROUTE
    ).replace(
        "__CHAT_SEND_ROUTE__", app.CHAT_SEND_ROUTE
    ).replace(
        "__CHAT_HISTORY_DURABLE_ROUTE__", app.CHAT_HISTORY_DURABLE_ROUTE
    ).replace(
        "__CHAT_SEND_MAX_CHARS__", str(app.CHAT_SEND_MAX_CHARS)
    )
    # The const CHAT_SEND_MAX_CHARS declaration must exist at the script top
    assert re.search(
        r"const CHAT_SEND_MAX_CHARS = 32000;", script
    ) is not None, (
        "const CHAT_SEND_MAX_CHARS = 32000 must be declared at the script top"
    )
    # The sendChatMessage rejection path must reference it
    assert "trimmed.length > CHAT_SEND_MAX_CHARS" in script, (
        "sendChatMessage() must compare against CHAT_SEND_MAX_CHARS, not 4000"
    )
    # The literal 4000 must not appear anywhere in the rendered script
    assert "> 4000" not in script, (
        "sendChatMessage() still has a hardcoded '> 4000' check"
    )


# ---------------------------------------------------------------------------
# 5. End-to-end: backend GatewayChatHistoryClient.send preserves exact text
# ---------------------------------------------------------------------------

def test_send_preserves_byte_for_byte_through_runner() -> None:
    """Submitted text equals received text byte-for-byte (no truncation)."""
    received_scripts = []

    def runner(*args, **kwargs):
        received_scripts.append(kwargs.get("input", ""))
        return _accepted_completed()

    client = GatewayChatHistoryClient(runner=runner)
    # Include tricky chars: \n, \t, " , ' , unicode. Avoid leading/trailing
    # whitespace because the dashboard's `_normalize_send_message` strips
    # the message before forwarding — this is the existing contract.
    message = (
        "Manager,\n\nPlease:\n"
        "- line 1\n"
        "- line 2 with \"quotes\" and 'apostrophes'\n"
        "- tab\there\n"
        "- unicode \u00e9\u00e0\u4e2d\u6587\U0001f44d\n"
        "- end"
    )
    result = client.send(message)
    assert result.ok is True
    assert len(received_scripts) == 1
    extracted = _extract_message_from_script(received_scripts[0])
    assert extracted is not None, (
        f"could not find `const MESSAGE = ...;` literal in script; "
        f"first 500 chars: {received_scripts[0][:500]}"
    )
    # Submitted text is passed through `_normalize_send_message` which
    # strips leading/trailing whitespace; the trimmed form must equal
    # what reaches the Gateway script.
    assert extracted == message.strip(), (
        f"message mutated during round-trip. "
        f"original len={len(message)} stripped len={len(message.strip())} "
        f"received len={len(extracted)}"
    )
    # Defense-in-depth structural checks
    assert "\n" in extracted
    assert "\t" in extracted
    assert '"quotes"' in extracted
    assert "'apostrophes'" in extracted
    assert "\u00e9\u00e0" in extracted
    assert "\u4e2d\u6587" in extracted
    assert "\U0001f44d" in extracted
