"""Driving the shuttle cards in tests: /shuttle, /shuttle_finished and /shuttle_edit."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select

from badminton.db.models import Member, ShuttleBatch
from badminton.domain.timeutil import utc_now
from badminton.services.attendance import set_attendance
from badminton.services.repo import load_ledger
from tests.bot.attendance_helpers import make_session
from tests.conftest import BotHarness
from tests.fake_telegram import ADMIN_ID, callback_update, text_update, user

ZY = user(ADMIN_ID, "zy", "zy_handle")
SHAWN = user(222, "shawn")
NIC = user(333, "nic")


class Card:
    """One bot card, followed through its edits: ``await card.tap("✅ Record")``."""

    def __init__(self, bot: BotHarness, message: dict[str, Any], sender: dict[str, Any]) -> None:
        self.bot = bot
        self.message = message
        self.sender = sender

    @classmethod
    async def open(cls, bot: BotHarness, command: str, sender: dict[str, Any] = ZY) -> Card:
        await bot.send(text_update(command, sender=sender))
        return cls(bot, bot.telegram.last("sendMessage").message, sender)

    @property
    def current(self) -> dict[str, Any]:
        card = self.message
        for call in self.bot.telegram.calls:
            if call.params.get("message_id") != self.message["message_id"]:
                continue
            if call.method == "editMessageText":
                card = call.result
            elif call.method == "editMessageReplyMarkup":
                card = {**card, "reply_markup": call.params.get("reply_markup") or {}}
        return card

    @property
    def text(self) -> str:
        return self.current["text"]

    def buttons(self) -> dict[str, str]:
        rows = self.current.get("reply_markup", {}).get("inline_keyboard", [])
        return {b["text"]: b["callback_data"] for row in rows for b in row}

    async def tap(self, label: str, sender: dict[str, Any] | None = None) -> None:
        data = self.buttons()[label]
        await self.bot.send(
            callback_update(data, sender=sender or self.sender, on_message=self.message)
        )

    async def tap_starting(self, prefix: str, sender: dict[str, Any] | None = None) -> None:
        await self.tap(next(label for label in self.buttons() if label.startswith(prefix)), sender)

    def last_alert(self) -> str:
        return self.bot.telegram.last("answerCallbackQuery").params.get("text", "")


async def seed_members(bot: BotHarness, *names: str) -> dict[str, int]:
    async with bot.sessionmaker() as db:
        members = [Member(display_name=name) for name in names]
        db.add_all(members)
        await db.commit()
        return {m.display_name: m.id for m in members}


async def member_id(bot: BotHarness, telegram_user: dict[str, Any]) -> int:
    async with bot.sessionmaker() as db:
        return await db.scalar(
            select(Member.id).where(Member.telegram_user_id == telegram_user["id"])
        )


async def add_batch(
    bot: BotHarness, payer_id: int, cents: int, *, starts_at: datetime | None = None
) -> int:
    """A batch in use from ``starts_at``, or a waiting one without it; returns its ID."""
    async with bot.sessionmaker() as db:
        batch = ShuttleBatch(
            payer_member_id=payer_id, amount_cents=cents, starts_at=starts_at, opened_at=starts_at
        )
        db.add(batch)
        await db.commit()
        return batch.id


async def played(bot: BotHarness, hours_ago: int, who: list[int], minutes: int = 120) -> int:
    """A session that started ``hours_ago``, with ``who`` marked in; returns its ID."""
    session_id = await make_session(
        bot,
        starts_at=utc_now() - timedelta(hours=hours_ago),
        minutes=minutes,
        payer_name=f"payer{hours_ago}",
        payer_telegram_id=None,
    )
    async with bot.sessionmaker() as db:
        for member in who:
            await set_attendance(db, session_id, member, "in")
        await db.commit()
    return session_id


async def batches(bot: BotHarness) -> list[ShuttleBatch]:
    """Every batch, oldest purchase first."""
    async with bot.sessionmaker() as db:
        return list(await db.scalars(select(ShuttleBatch).order_by(ShuttleBatch.id)))


async def balances(bot: BotHarness) -> dict[int, int]:
    async with bot.sessionmaker() as db:
        return (await load_ledger(db, utc_now())).balances


async def purchase(bot: BotHarness, price: str, sender: dict[str, Any] = SHAWN) -> Card:
    """Record a purchase as a member (the sender pays); returns its card."""
    card = await Card.open(bot, f"/shuttle {price}", sender)
    await card.tap("✅ Record")
    return card
