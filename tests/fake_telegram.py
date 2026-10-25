"""A fake Telegram Bot API for bot-flow tests (NFR-9): nothing touches the network.

``FakeTelegram`` replaces python-telegram-bot's HTTP transport. It records every
API call and answers with plausible results. Files to download (e.g. a booking
screenshot) go in ``files``, by file ID. Everyone is a member of the group unless
``set_member_status`` says otherwise. The ``*_update`` factories build incoming
updates as plain dicts, the same JSON Telegram would POST to the webhook.
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
_OTHER_METHODS = {"getChatMember", "getFile"}


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

    ``fail(method, code, description, chat_id=...)`` makes the next call to ``method``
    fail, e.g. ``fail("sendMessage", 403, "Forbidden: bot was blocked by the user")``.
    """

    calls: list[ApiCall] = field(default_factory=list)
    files: dict[str, bytes] = field(default_factory=dict)
    _statuses: dict[int, tuple[str, bool]] = field(default_factory=dict)  # see set_member_status
    _photo_ids: set[int] = field(default_factory=set)  # the bot's photo messages
    _text_ids: set[int] = field(default_factory=set)  # and its text messages
    _failures: dict[str, list[tuple[int, str, int | None]]] = field(default_factory=dict)
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
        if "/file/bot" in url:  # a download, from the file_path getFile gave
            return 200, self.files.get(api_method, b"")

        # setMyCommands names its chat inside ``scope`` rather than as a top-level chat_id.
        target_chat = params.get("chat_id") or (params.get("scope") or {}).get("chat_id")
        if failure := self._take_failure(api_method, target_chat) or self._wrong_kind(
            api_method, params
        ):
            code, description = failure
            self.calls.append(ApiCall(api_method, params))
            return code, _encode({"ok": False, "error_code": code, "description": description})
        if api_method not in _TRUE_METHODS | _SEND_METHODS | _EDIT_METHODS | _OTHER_METHODS:
            description = f"FakeTelegram does not support {api_method}; add it to the fake"
            return 400, _encode({"ok": False, "error_code": 400, "description": description})

        result = self._result(api_method, params)
        self.calls.append(ApiCall(api_method, params, result))
        return 200, _encode({"ok": True, "result": result})

    def fail(self, method: str, code: int, description: str, *, chat_id: int | None = None) -> None:
        """Make the next call to ``method`` (to ``chat_id``, if given) fail."""
        self._failures.setdefault(method, []).append((code, description, chat_id))

    def set_member_status(self, user_id: int, status: str, *, is_member: bool = True) -> None:
        """Make getChatMember report ``user_id`` with ``status``, e.g. ``"left"`` for an
        outsider. ``is_member`` is for ``"restricted"``: whether they are still in the group."""
        self._statuses[user_id] = (status, is_member)

    def _take_failure(self, method: str, chat_id: object) -> tuple[int, str] | None:
        queued = self._failures.get(method, [])
        for index, (code, description, only_chat) in enumerate(queued):
            if only_chat is None or str(only_chat) == str(chat_id):
                del queued[index]
                return code, description
        return None

    def _wrong_kind(self, method: str, params: dict[str, Any]) -> tuple[int, str] | None:
        """Like Telegram, refuse to edit the text of a photo or the caption of a text."""
        message_id = int(params.get("message_id") or 0)
        if method == "editMessageText" and message_id in self._photo_ids:
            return 400, "Bad Request: there is no text in the message to edit"
        if method == "editMessageCaption" and message_id in self._text_ids:
            return 400, "Bad Request: there is no caption in the message to edit"
        return None

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
        if method == "getChatMember":
            user_id = int(params["user_id"])
            status, is_member = self._statuses.get(user_id, ("member", True))
            return _chat_member(user_id, status, is_member)
        if method == "getFile":
            file_id = params["file_id"]
            return {"file_id": file_id, "file_unique_id": f"u-{file_id}", "file_path": file_id}
        if method in _SEND_METHODS:
            message_id = next(self._message_ids)
            (self._photo_ids if method == "sendPhoto" else self._text_ids).add(message_id)
            return self._message(params, message_id)
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
        for key in ("text", "caption"):
            if key in params:
                message[key] = params[key]
        if isinstance(params.get("photo"), str):  # sent by file ID
            message["photo"] = [
                {"file_id": params["photo"], "file_unique_id": "u", "width": 590, "height": 1280}
            ]
        # Like Telegram, only inline keyboards come back on the message.
        markup = params.get("reply_markup")
        if isinstance(markup, dict) and "inline_keyboard" in markup:
            message["reply_markup"] = markup
        return message


def _encode(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload).encode()


def _chat_member(user_id: int, status: str, is_member: bool) -> dict[str, Any]:
    """A ChatMember as Telegram sends it, with the fields its status requires."""
    member: dict[str, Any] = {"user": user(user_id), "status": status}
    if status == "creator":
        member["is_anonymous"] = False
    elif status == "administrator":
        member |= dict.fromkeys(
            (
                "can_be_edited",
                "is_anonymous",
                "can_manage_chat",
                "can_delete_messages",
                "can_manage_video_chats",
                "can_restrict_members",
                "can_promote_members",
                "can_change_info",
                "can_invite_users",
                "can_post_stories",
                "can_edit_stories",
                "can_delete_stories",
            ),
            False,
        )
    elif status == "restricted":
        member |= dict.fromkeys(
            (
                "can_change_info",
                "can_invite_users",
                "can_pin_messages",
                "can_send_messages",
                "can_send_polls",
                "can_send_other_messages",
                "can_add_web_page_previews",
                "can_manage_topics",
                "can_send_audios",
                "can_send_documents",
                "can_send_photos",
                "can_send_videos",
                "can_send_video_notes",
                "can_send_voice_notes",
                "can_edit_tag",
                "can_react_to_messages",
            ),
            False,
        )
        member |= {"is_member": is_member, "until_date": 0}
    elif status == "kicked":
        member["until_date"] = 0
    return member


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
    chat_id: int | None = None,
    text: str | None = None,
    reply_to: dict[str, Any] | None = None,
    message_id: int | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """An incoming message, in the group unless ``chat_id`` says otherwise; a reply is in
    the chat of the message it replies to. A leading ``/command`` gets a bot_command
    entity, as from Telegram."""
    if chat_id is None:
        chat_id = reply_to["chat"]["id"] if reply_to is not None else GROUP_ID
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
    *,
    sender: dict[str, Any],
    caption: str | None = None,
    chat_id: int | None = None,
    file_id: str = "photo",
    reply_to: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A photo in three sizes, ``<file_id>-90`` to ``<file_id>-1280`` (the largest)."""
    sizes = [
        {
            "file_id": f"{file_id}-{size}",
            "file_unique_id": f"u-{size}",
            "width": size,
            "height": size,
        }
        for size in (90, 320, 1280)
    ]
    data = message(sender=sender, chat_id=chat_id, photo=sizes, reply_to=reply_to)
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
