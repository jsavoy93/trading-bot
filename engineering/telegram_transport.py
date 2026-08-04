from __future__ import annotations

from dataclasses import dataclass
import json
import os
from typing import Mapping, Protocol
from urllib import error, parse, request


TELEGRAM_API_ORIGIN = "https://api.telegram.org"
MAX_TELEGRAM_MESSAGE_CHARS = 3_500
MAX_UPDATE_BATCH = 100
MIN_LONG_POLL_SECONDS = 1
MAX_LONG_POLL_SECONDS = 50
MIN_HTTP_TIMEOUT_SECONDS = 2.0
MAX_HTTP_TIMEOUT_SECONDS = 60.0


@dataclass(frozen=True)
class TelegramUpdate:
    update_id: int
    chat_id: int
    sender_id: int
    chat_type: str
    text: str
    forwarded: bool = False


class TelegramTransportError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        transient: bool,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message[:200])
        self.transient = transient
        self.retry_after = retry_after


class TelegramTransport(Protocol):
    def get_updates(
        self, *, offset: int, timeout_seconds: int, limit: int
    ) -> tuple[TelegramUpdate, ...]: ...

    def send_message(self, *, chat_id: int, text: str) -> str: ...


def telegram_credentials_from_env(
    environment: Mapping[str, str] | None = None,
) -> tuple[str, int]:
    values = os.environ if environment is None else environment
    token = values.get("ENGINEERING_TELEGRAM_BOT_TOKEN", "")
    raw_chat_id = values.get("ENGINEERING_TELEGRAM_JOSH_CHAT_ID", "")
    if not token or len(token) > 256 or any(char.isspace() for char in token):
        raise ValueError("Engineering Telegram credentials are missing or invalid")
    try:
        chat_id = int(raw_chat_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("Engineering Telegram credentials are missing or invalid") from exc
    if chat_id <= 0:
        raise ValueError("Engineering Telegram credentials are missing or invalid")
    return token, chat_id


class TelegramHTTPTransport:
    """Small fixed-origin Bot API transport; automated tests inject a fake."""

    def __init__(
        self,
        token: str,
        *,
        http_timeout_seconds: float = 55.0,
    ) -> None:
        if not token or len(token) > 256 or any(char.isspace() for char in token):
            raise ValueError("Telegram bot token is missing or invalid")
        if not MIN_HTTP_TIMEOUT_SECONDS <= http_timeout_seconds <= MAX_HTTP_TIMEOUT_SECONDS:
            raise ValueError("Telegram HTTP timeout is outside the finite bounds")
        self.__token = token
        self.http_timeout_seconds = float(http_timeout_seconds)

    def _call(self, method: str, fields: Mapping[str, object]) -> object:
        endpoint = f"{TELEGRAM_API_ORIGIN}/bot{self.__token}/{method}"
        encoded = parse.urlencode(
            {
                key: json.dumps(value) if isinstance(value, (list, tuple)) else str(value)
                for key, value in fields.items()
            }
        ).encode("utf-8")
        req = request.Request(endpoint, data=encoded, method="POST")
        try:
            with request.urlopen(req, timeout=self.http_timeout_seconds) as response:
                raw = response.read(1_000_001)
        except error.HTTPError as exc:
            retry_after = None
            try:
                body = json.loads(exc.read(16_384).decode("utf-8"))
                retry_after = float(body.get("parameters", {}).get("retry_after"))
            except (ValueError, TypeError, json.JSONDecodeError):
                pass
            raise TelegramTransportError(
                f"Telegram HTTP error {exc.code}",
                transient=exc.code == 429 or 500 <= exc.code < 600,
                retry_after=retry_after,
            ) from None
        except (error.URLError, TimeoutError, OSError) as exc:
            raise TelegramTransportError(
                f"Telegram transport failure: {type(exc).__name__}", transient=True
            ) from None
        if len(raw) > 1_000_000:
            raise TelegramTransportError("Telegram response exceeded limit", transient=False)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TelegramTransportError("Telegram returned invalid JSON", transient=False) from exc
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise TelegramTransportError("Telegram rejected the request", transient=False)
        return payload.get("result")

    def get_updates(
        self, *, offset: int, timeout_seconds: int, limit: int
    ) -> tuple[TelegramUpdate, ...]:
        if offset < 0 or not MIN_LONG_POLL_SECONDS <= timeout_seconds <= MAX_LONG_POLL_SECONDS:
            raise ValueError("Telegram long-poll arguments are invalid")
        if not 1 <= limit <= MAX_UPDATE_BATCH:
            raise ValueError("Telegram update limit is invalid")
        result = self._call(
            "getUpdates",
            {
                "offset": offset,
                "timeout": timeout_seconds,
                "limit": limit,
                "allowed_updates": ["message"],
            },
        )
        if not isinstance(result, list) or len(result) > limit:
            raise TelegramTransportError("Telegram returned an invalid update batch", transient=False)
        updates = []
        for item in result:
            update = self._parse_update(item)
            if update is not None:
                updates.append(update)
        return tuple(updates)

    @staticmethod
    def _parse_update(item: object) -> TelegramUpdate | None:
        if not isinstance(item, dict) or not isinstance(item.get("update_id"), int):
            return None
        message = item.get("message")
        if not isinstance(message, dict):
            return None
        chat = message.get("chat")
        sender = message.get("from")
        if not isinstance(chat, dict) or not isinstance(sender, dict):
            return None
        chat_id = chat.get("id")
        sender_id = sender.get("id")
        chat_type = chat.get("type")
        text = message.get("text")
        if not isinstance(chat_id, int) or not isinstance(sender_id, int):
            return None
        if not isinstance(chat_type, str) or not isinstance(text, str):
            return None
        forwarded = any(
            key in message
            for key in ("forward_origin", "forward_from", "forward_from_chat", "forward_date")
        )
        return TelegramUpdate(
            update_id=item["update_id"],
            chat_id=chat_id,
            sender_id=sender_id,
            chat_type=chat_type[:20],
            text=text,
            forwarded=forwarded,
        )

    def send_message(self, *, chat_id: int, text: str) -> str:
        if chat_id <= 0 or not text or len(text) > MAX_TELEGRAM_MESSAGE_CHARS:
            raise ValueError("Telegram message arguments are invalid")
        result = self._call(
            "sendMessage",
            {"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
        )
        if not isinstance(result, dict) or not isinstance(result.get("message_id"), int):
            raise TelegramTransportError("Telegram send receipt is invalid", transient=False)
        return str(result["message_id"])
