"""Helpers for driving the booking wizard in tests."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from badminton.domain.timeutil import sgt_date, utc_now
from tests.conftest import BotHarness
from tests.fake_telegram import ADMIN_ID, callback_update, message_update, photo_message, user

ALICE = user(ADMIN_ID, "alice", "alice_handle")
BOB = user(222, "bob", "bob_t")
CAROL = user(333, "carol")


def future_day(days: int = 14) -> date:
    return sgt_date(utc_now()) + timedelta(days=days)


class Wizard:
    """Drives one booking card: ``await wizard.tap("2h")`` presses a button on it."""

    def __init__(self, bot: BotHarness, sender: dict[str, Any]) -> None:
        self.bot = bot
        self.sender = sender
        self.card: dict[str, Any] = {}
        self._seen = 0  # how many recorded API calls have been applied to ``card``

    async def start(self) -> Wizard:
        await self.bot.send(message_update(photo_message(sender=self.sender, caption="/book")))
        self.card = self.bot.telegram.last("sendMessage").message
        self._seen = len(self.bot.telegram.calls)
        return self

    @property
    def current(self) -> dict[str, Any]:
        """The card after every edit so far (remembered across ``telegram.clear()``)."""
        calls = self.bot.telegram.calls
        if self._seen > len(calls):
            self._seen = 0
        for call in calls[self._seen :]:
            if call.params.get("message_id") != self.card["message_id"]:
                continue
            if call.method == "editMessageText":
                self.card = call.result
            elif call.method == "editMessageReplyMarkup":
                self.card = {**self.card, "reply_markup": call.params.get("reply_markup", {})}
        self._seen = len(calls)
        return self.card

    @property
    def text(self) -> str:
        return self.current["text"]

    def buttons(self) -> dict[str, str]:
        rows = self.current.get("reply_markup", {}).get("inline_keyboard", [])
        return {button["text"]: button["callback_data"] for row in rows for button in row}

    async def tap(self, label: str, *, sender: dict[str, Any] | None = None) -> None:
        data = self.buttons()[label]
        await self.bot.send(
            callback_update(data, sender=sender or self.sender, on_message=self.card)
        )

    async def pick_date(self, day: date) -> None:
        while str(day.day) not in self.buttons() or not self._shows_month(day):
            await self.tap("›")
        await self.tap(str(day.day))

    def _shows_month(self, day: date) -> bool:
        return day.strftime("%b %Y") in self.buttons()

    def last_alert(self) -> str:
        return self.bot.telegram.last("answerCallbackQuery").params.get("text", "")


async def book(
    bot: BotHarness, sender: dict[str, Any], day: date, hour: str = "11:00", hours: str = "2h"
) -> Wizard:
    """Run the wizard up to the price step."""
    wizard = await Wizard(bot, sender).start()
    await wizard.pick_date(day)
    await wizard.tap(hour)
    await wizard.tap(hours)
    return wizard
