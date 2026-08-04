import io
import json
from urllib import error

import pytest

from engineering.telegram_transport import (
    TELEGRAM_API_ORIGIN,
    TelegramHTTPTransport,
    TelegramTransportError,
    telegram_credentials_from_env,
)


class Response:
    def __init__(self, payload: object) -> None:
        self.payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size: int) -> bytes:
        return self.payload[:size]


def test_credentials_are_injected_from_environment_and_fail_closed() -> None:
    assert telegram_credentials_from_env(
        {
            "ENGINEERING_TELEGRAM_BOT_TOKEN": "123:test-token",
            "ENGINEERING_TELEGRAM_JOSH_CHAT_ID": "12345",
        }
    ) == ("123:test-token", 12345)
    for values in ({}, {"ENGINEERING_TELEGRAM_BOT_TOKEN": "secret"}, {
        "ENGINEERING_TELEGRAM_BOT_TOKEN": "secret",
        "ENGINEERING_TELEGRAM_JOSH_CHAT_ID": "group",
    }):
        with pytest.raises(ValueError, match="credentials"):
            telegram_credentials_from_env(values)


def test_long_poll_uses_fixed_origin_bounds_and_parses_private_message(monkeypatch) -> None:
    captured = {}

    def fake_urlopen(req, timeout):
        captured.update(url=req.full_url, body=req.data.decode(), timeout=timeout)
        return Response(
            {
                "ok": True,
                "result": [
                    {
                        "update_id": 7,
                        "message": {
                            "chat": {"id": 42, "type": "private"},
                            "from": {"id": 42},
                            "text": "/status",
                        },
                    }
                ],
            }
        )

    monkeypatch.setattr("engineering.telegram_transport.request.urlopen", fake_urlopen)
    transport = TelegramHTTPTransport("123:test-token", http_timeout_seconds=12)
    updates = transport.get_updates(offset=7, timeout_seconds=10, limit=5)

    assert captured["url"] == f"{TELEGRAM_API_ORIGIN}/bot123:test-token/getUpdates"
    assert captured["timeout"] == 12
    assert "offset=7" in captured["body"] and "timeout=10" in captured["body"]
    assert updates[0].text == "/status" and updates[0].chat_type == "private"


def test_update_parser_marks_forwarded_and_discards_unsupported_updates(monkeypatch) -> None:
    monkeypatch.setattr(
        "engineering.telegram_transport.request.urlopen",
        lambda *args, **kwargs: Response(
            {
                "ok": True,
                "result": [
                    {"update_id": 1, "channel_post": {"text": "/status"}},
                    {
                        "update_id": 2,
                        "message": {
                            "chat": {"id": 9, "type": "private"},
                            "from": {"id": 9},
                            "text": "/report",
                            "forward_origin": {"type": "user"},
                        },
                    },
                ],
            }
        ),
    )
    updates = TelegramHTTPTransport("token").get_updates(
        offset=0, timeout_seconds=2, limit=5
    )
    assert len(updates) == 1 and updates[0].forwarded is True


def test_send_is_bounded_and_returns_receipt(monkeypatch) -> None:
    monkeypatch.setattr(
        "engineering.telegram_transport.request.urlopen",
        lambda *args, **kwargs: Response({"ok": True, "result": {"message_id": 99}}),
    )
    transport = TelegramHTTPTransport("token")
    assert transport.send_message(chat_id=42, text="bounded") == "99"
    with pytest.raises(ValueError, match="arguments"):
        transport.send_message(chat_id=42, text="x" * 3_501)


def test_rate_limit_is_bounded_and_token_never_appears_in_error(monkeypatch) -> None:
    body = io.BytesIO(json.dumps({"parameters": {"retry_after": 4}}).encode())
    http_error = error.HTTPError("url", 429, "limited", {}, body)
    monkeypatch.setattr(
        "engineering.telegram_transport.request.urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(http_error),
    )
    with pytest.raises(TelegramTransportError) as caught:
        TelegramHTTPTransport("canary-secret").get_updates(
            offset=0, timeout_seconds=2, limit=1
        )
    assert caught.value.transient and caught.value.retry_after == 4
    assert "canary-secret" not in str(caught.value)


@pytest.mark.parametrize(
    "kwargs",
    (
        {"offset": -1, "timeout_seconds": 2, "limit": 1},
        {"offset": 0, "timeout_seconds": 0, "limit": 1},
        {"offset": 0, "timeout_seconds": 2, "limit": 101},
    ),
)
def test_long_poll_bounds_reject_invalid_arguments(kwargs) -> None:
    with pytest.raises(ValueError):
        TelegramHTTPTransport("token").get_updates(**kwargs)
