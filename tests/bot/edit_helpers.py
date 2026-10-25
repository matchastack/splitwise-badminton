"""Driving the /edit card in tests."""

from __future__ import annotations

from typing import Any

from tests.conftest import BotHarness
from tests.fake_telegram import ADMIN_ID, callback_update, text_update, user

ALICE = user(ADMIN_ID, "alice", "alice_handle")


class EditCard:
    def __init__(self, bot: BotHarness) -> None:
        self.bot = bot
        self.message: dict[str, Any] = {}

    async def open(self, sender: dict[str, Any] = ALICE) -> EditCard:
        await self.bot.send(text_update("/edit", sender=sender, chat_id=sender["id"]))
        self.message = self.bot.telegram.last("sendMessage").message
        return self

    @property
    def current(self) -> dict[str, Any]:
        for call in reversed(self.bot.telegram.calls):
            if (
                call.method == "editMessageText"
                and call.params["message_id"] == self.message["message_id"]
            ):
                return call.result
        return self.message

    @property
    def text(self) -> str:
        return self.current["text"]

    def buttons(self) -> dict[str, str]:
        rows = self.current.get("reply_markup", {}).get("inline_keyboard", [])
        return {b["text"]: b["callback_data"] for row in rows for b in row}

    async def tap(self, label: str, sender: dict[str, Any] = ALICE) -> None:
        data = self.buttons()[label]
        await self.bot.send(callback_update(data, sender=sender, on_message=self.message))

    async def tap_starting(self, prefix: str) -> None:
        await self.tap(next(label for label in self.buttons() if label.startswith(prefix)))
