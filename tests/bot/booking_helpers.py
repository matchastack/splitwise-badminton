"""Helpers for driving the booking wizard in tests."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from badminton.domain.timeutil import sgt_date, utc_now
from tests.conftest import BotHarness
from tests.fake_telegram import ADMIN_ID, callback_update, message_update, photo_message, user

ZY = user(ADMIN_ID, "zy", "zy_handle")
SHAWN = user(222, "shawn", "shawn_t")
NIC = user(333, "nic")


def future_day(days: int = 14) -> date:
    return sgt_date(utc_now()) + timedelta(days=days)


class Wizard:
    """Drives one booking card: ``await wizard.tap("2h")`` presses a button on it."""

    def __init__(self, bot: BotHarness, sender: dict[str, Any]) -> None:
        self.bot = bot
        self.sender = sender
        self.card: dict[str, Any] = {}

    async def start(self) -> Wizard:
        await self.bot.send(message_update(photo_message(sender=self.sender, caption="/book")))
        self.card = self.bot.telegram.last("sendMessage").message
        return self

    @property
    def current(self) -> dict[str, Any]:
        """The card as last sent or edited (remembered across ``telegram.clear()``)."""
        card_id = self.card["message_id"]
        for call in reversed(self.bot.telegram.calls):
            edits_card = call.method in ("sendMessage", "editMessageText")
            if (
                edits_card
                and isinstance(call.result, dict)
                and call.result["message_id"] == card_id
            ):
                self.card = call.result
                break
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
