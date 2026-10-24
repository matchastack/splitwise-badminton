"""Helpers for driving the booking wizard in tests."""

from __future__ import annotations

import itertools
from datetime import date, timedelta
from typing import Any

from badminton.domain.timeutil import sgt_date, utc_now
from tests.conftest import BotHarness
from tests.fake_telegram import (
    ADMIN_ID,
    callback_update,
    message_update,
    photo_message,
    text_update,
    user,
)

ALICE = user(ADMIN_ID, "alice", "alice_handle")
BOB = user(222, "bob", "bob_t")
CAROL = user(333, "carol")

_screenshots = itertools.count(1)


def future_day(days: int = 14) -> date:
    return sgt_date(utc_now()) + timedelta(days=days)


def activesg_text(
    day: date,
    hour: int = 11,
    hours: int = 2,
    courts: tuple[str, ...] = ("11",),
    venue: str = "Hougang Sport Hall",
) -> str:
    """An ActiveSG booking screenshot's text, as OCR reads it: one slot per court and hour."""
    lines = [
        "Booking confirmed!",
        "BOOKING DETAILS",
        "What Badminton",
        f"Where {venue}",
        "93 Hougang Avenue 4 Singapore",
        "538832",
    ]
    slots = [(start, court) for start in range(hour, hour + hours) for court in courts]
    for index, (start, court) in enumerate(slots):
        when = f"{day:%a}, {day.day} {day:%b}, {start % 12 or 12}:00 {'am' if start < 12 else 'pm'}"
        lines += [f"When {when}" if index == 0 else when, f"Court {court}"]
    return "\n".join([*lines, "Back to Home"])


class Card:
    """A bot message as it stands after every edit so far (remembered across
    ``telegram.clear()``): ``await card.tap("✅ Confirm")`` presses a button on it."""

    def __init__(
        self, bot: BotHarness, sender: dict[str, Any], message: dict[str, Any], seen: int
    ) -> None:
        self.bot = bot
        self.sender = sender
        self.card = message
        self._seen = seen  # how many recorded API calls have been applied to ``card``

    @property
    def current(self) -> dict[str, Any]:
        calls = self.bot.telegram.calls
        if self._seen > len(calls):
            self._seen = 0
        for call in calls[self._seen :]:
            if call.params.get("message_id") != self.card["message_id"] or call.result is None:
                continue
            if call.method in ("editMessageText", "editMessageCaption"):
                self.card = call.result
            elif call.method == "editMessageReplyMarkup":
                self.card = {**self.card, "reply_markup": call.params.get("reply_markup", {})}
        self._seen = len(calls)
        return self.card

    @property
    def text(self) -> str:
        """The message's text, or its caption if it's a photo."""
        return self.current.get("text") or self.current.get("caption", "")

    def buttons(self) -> dict[str, str]:
        rows = self.current.get("reply_markup", {}).get("inline_keyboard", [])
        return {button["text"]: button["callback_data"] for row in rows for button in row}

    async def tap(self, label: str, *, sender: dict[str, Any] | None = None) -> None:
        data = self.buttons()[label]
        await self.bot.send(
            callback_update(data, sender=sender or self.sender, on_message=self.card)
        )

    def last_alert(self) -> str:
        return self.bot.telegram.last("answerCallbackQuery").params.get("text", "")


class Wizard(Card):
    """Drives one booking card. ``screenshot`` is the text the bot's OCR reads off the
    photo; by default it reads nothing, so the wizard asks for the date and time."""

    def __init__(self, bot: BotHarness, sender: dict[str, Any], screenshot: str = "") -> None:
        super().__init__(bot, sender, {}, 0)
        self.screenshot = screenshot
        self.prompt: dict[str, Any] | None = None
        self.photo: dict[str, Any] = {}

    async def start(self, *, caption: bool = False) -> Wizard:
        """Send /book, then the screenshot in reply to the bot's prompt; or, with
        ``caption``, the screenshot captioned /book."""
        file_id = f"shot{next(_screenshots)}"
        self.bot.telegram.files[f"{file_id}-1280"] = self.screenshot.encode()
        if caption:
            self.photo = photo_message(sender=self.sender, caption="/book", file_id=file_id)
        else:
            await self.bot.send(text_update("/book", sender=self.sender))
            self.prompt = self.bot.telegram.last("sendMessage").message
            self.photo = photo_message(sender=self.sender, file_id=file_id, reply_to=self.prompt)
        await self.bot.send(message_update(self.photo))
        self.card = self.bot.telegram.last("sendMessage").message
        self._seen = len(self.bot.telegram.calls)
        return self

    async def pick_date(self, day: date) -> None:
        while str(day.day) not in self.buttons() or not self._shows_month(day):
            await self.tap("›")
        await self.tap(str(day.day))

    def _shows_month(self, day: date) -> bool:
        return day.strftime("%b %Y") in self.buttons()

    def record(self) -> Card:
        """The booking's record: the screenshot the bot posted on confirming."""
        calls = self.bot.telegram.calls
        index = max(i for i, call in enumerate(calls) if call.method == "sendPhoto")
        return Card(self.bot, self.sender, calls[index].message, index + 1)


async def book(
    bot: BotHarness, sender: dict[str, Any], day: date, hour: str = "11:00", hours: str = "2h"
) -> Wizard:
    """Run the wizard up to the booking to confirm, read off a screenshot of ``hours``
    from ``hour`` on ``day``."""
    screenshot = activesg_text(day, int(hour[:2]), int(hours.removesuffix("h")))
    return await Wizard(bot, sender, screenshot).start()


async def confirmed(
    bot: BotHarness, sender: dict[str, Any], day: date, hour: str = "11:00", hours: str = "2h"
) -> Card:
    """Book and confirm, as a separate session; returns the booking's record."""
    wizard = await book(bot, sender, day, hour=hour, hours=hours)
    if "Separate session" in wizard.buttons():
        await wizard.tap("Separate session")
    await wizard.tap("✅ Confirm")
    return wizard.record()
