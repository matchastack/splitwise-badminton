"""A fake Telegram Bot API for bot-flow tests (NFR-9): nothing touches the network.

``FakeTelegram`` replaces python-telegram-bot's HTTP transport. It records every
API call and answers with plausible results. The ``*_update`` factories build
incoming updates as plain dicts, the same JSON Telegram would POST to the webhook.
"""

from __future__ import annotations

import itertools
import json
import time
from dataclasses import dataclass, field
from typing import Any

from telegram.request import BaseRequest, RequestData

BOT_ID = 9000
BOT_USER = {"id": BOT_ID, "is_bot": True, "first_name": "Badminton", "username": "badminton_bot"}
GROUP_ID = -1001234567890
ADMIN_ID = 111

_TRUE_METHODS = {
    "answerCallbackQuery",
    "deleteMessage",
    "deleteMessages",
    "deleteWebhook",
    "pinChatMessage",
    "setMyCommands",
    "setWebhook",
    "unpinChatMessage",
}
_SEND_METHODS = {"sendMessage", "sendPhoto"}
_EDIT_METHODS = {"editMessageText", "editMessageReplyMarkup", "editMessageCaption"}


@dataclass(frozen=True, slots=True)
class ApiCall:
    method: str
    params: dict[str, Any]
    result: Any = None

    @property
    def message(self) -> dict[str, Any]:
        """The message this send/edit produced, e.g. to reply to it or tap its buttons."""
        assert isinstance(self.result, dict), f"{self.method} did not return a message"
        return self.result


@dataclass
class FakeTelegram(BaseRequest):
    """Records Bot API calls and synthesises successful responses.

    ``fail(method, code, description)`` makes the next call to ``method`` fail,
    e.g. ``fail("sendMessage", 403, "Forbidden: bot was blocked by the user")``.
    """

    calls: list[ApiCall] = field(default_factory=list)
    _failures: dict[str, list[tuple[int, str]]] = field(default_factory=dict)
    _message_ids: itertools.count[int] = field(default_factory=lambda: itertools.count(1000))

    async def initialize(self) -> None:
        pass

    async def shutdown(self) -> None:
        pass

    @property
    def read_timeout(self) -> float | None:
        return None

    async def do_request(
        self, url: str, method: str, request_data: RequestData | None = None, **_: object
    ) -> tuple[int, bytes]:
        api_method = url.rsplit("/", 1)[-1]
        params = dict(request_data.parameters) if request_data else {}
        if api_method == "getMe":
            return 200, _encode({"ok": True, "result": BOT_USER})

        if queued := self._failures.get(api_method):
            code, description = queued.pop(0)
            self.calls.append(ApiCall(api_method, params))
            return code, _encode({"ok": False, "error_code": code, "description": description})
        if api_method not in _TRUE_METHODS | _SEND_METHODS | _EDIT_METHODS:
            description = f"FakeTelegram does not support {api_method}; add it to the fake"
            return 400, _encode({"ok": False, "error_code": 400, "description": description})

        result = self._result(api_method, params)
        self.calls.append(ApiCall(api_method, params, result))
        return 200, _encode({"ok": True, "result": result})

    def fail(self, method: str, code: int, description: str) -> None:
        self._failures.setdefault(method, []).append((code, description))

    # Inspection helpers ---------------------------------------------------

    def calls_to(self, method: str) -> list[ApiCall]:
        return [call for call in self.calls if call.method == method]

    def last(self, method: str) -> ApiCall:
        calls = self.calls_to(method)
        assert calls, f"no {method} call; calls were {[c.method for c in self.calls]}"
        return calls[-1]

    def sent_texts(self, chat_id: int | None = None) -> list[str]:
        return [
            call.params.get("text") or call.params.get("caption", "")
            for call in self.calls
            if call.method in _SEND_METHODS
            and (chat_id is None or call.params.get("chat_id") == chat_id)
        ]

    def clear(self) -> None:
        self.calls.clear()

    # Responses ------------------------------------------------------------

    def _result(self, method: str, params: dict[str, Any]) -> Any:
        if method in _SEND_METHODS:
            return self._message(params, next(self._message_ids))
        if method in _EDIT_METHODS and "inline_message_id" not in params:
            return self._message(params, int(params["message_id"]))
        return True

    @staticmethod
    def _message(params: dict[str, Any], message_id: int) -> dict[str, Any]:
        chat_id = int(params["chat_id"])
        message: dict[str, Any] = {
            "message_id": message_id,
            "date": int(time.time()),
            "chat": _chat(chat_id),
            "from": BOT_USER,
        }
        for key in ("text", "caption", "reply_markup"):
            if key in params:
                message[key] = params[key]
        return message


def _encode(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload).encode()


def _chat(chat_id: int) -> dict[str, Any]:
    if chat_id < 0:
        return {"id": chat_id, "type": "supergroup", "title": "Badminton"}
    return {"id": chat_id, "type": "private", "first_name": "Member"}


# Update factories -------------------------------------------------------------

_update_ids = itertools.count(1)
_incoming_message_ids = itertools.count(1)


def user(user_id: int, first_name: str = "Member", username: str | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {"id": user_id, "is_bot": False, "first_name": first_name}
    if username:
        data["username"] = username
    return data


def message(
    *,
    sender: dict[str, Any],
    chat_id: int = GROUP_ID,
    text: str | None = None,
    reply_to: dict[str, Any] | None = None,
    message_id: int | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """An incoming message. A leading ``/command`` gets a bot_command entity, as from Telegram."""
    data: dict[str, Any] = {
        "message_id": message_id or next(_incoming_message_ids),
        "date": int(time.time()),
        "chat": _chat(chat_id),
        "from": sender,
        **extra,
    }
    if text is not None:
        data["text"] = text
        if entities := _command_entities(text):
            data["entities"] = entities
    if reply_to is not None:
        data["reply_to_message"] = reply_to
    return data


def photo_message(
    *, sender: dict[str, Any], caption: str | None = None, chat_id: int = GROUP_ID
) -> dict[str, Any]:
    sizes = [
        {"file_id": f"photo-{size}", "file_unique_id": f"u-{size}", "width": size, "height": size}
        for size in (90, 320, 1280)
    ]
    data = message(sender=sender, chat_id=chat_id, photo=sizes)
    if caption is not None:
        data["caption"] = caption
        if entities := _command_entities(caption):
            data["caption_entities"] = entities
    return data


def message_update(message_data: dict[str, Any]) -> dict[str, Any]:
    return {"update_id": next(_update_ids), "message": message_data}


def text_update(text: str, *, sender: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    return message_update(message(sender=sender, text=text, **kwargs))


def callback_update(
    data: str, *, sender: dict[str, Any], on_message: dict[str, Any]
) -> dict[str, Any]:
    """A button tap. ``on_message`` is the bot message the button belongs to."""
    return {
        "update_id": next(_update_ids),
        "callback_query": {
            "id": str(next(_update_ids)),
            "from": sender,
            "chat_instance": "test",
            "message": on_message,
            "data": data,
        },
    }


def _command_entities(text: str) -> list[dict[str, Any]]:
    if not text.startswith("/"):
        return []
    length = len(text.split(maxsplit=1)[0])
    return [{"type": "bot_command", "offset": 0, "length": length}]
