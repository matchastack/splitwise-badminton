import dataclasses
from datetime import timedelta

import pytest
from sqlalchemy import select, update

from badminton.db.models import Member, Session
from badminton.domain.timeutil import at_sgt, fmt_session
from badminton.services.attendance import set_attendance
from badminton.services.reminders import send_due_reminders
from badminton.services.schedule import default_steps
from badminton.services.tick import TickContext
from tests.bot.booking_helpers import BOB, confirmed, future_day
from tests.conftest import TEST_SETTINGS, BotHarness
from tests.fake_telegram import GROUP_ID, ApiCall

DAY = future_day(10)
REMINDER_DAY = DAY - timedelta(days=2)  # REMINDER_DAYS_BEFORE is 2
TEN_SGT = at_sgt(REMINDER_DAY, 10)


async def remind(bot: BotHarness, when=TEN_SGT, settings=TEST_SETTINGS) -> None:
    await send_due_reminders(TickContext(bot.application.bot, settings, bot.sessionmaker, when))


def reminders(bot: BotHarness) -> list[ApiCall]:
    return [
        call
        for call in bot.telegram.calls_to("sendMessage")
        if call.result is not None and call.params["text"].startswith("🔔")
    ]


async def booked(bot: BotHarness, day=DAY, hour: str = "11:00") -> Session:
    """Bob books a session, which posts its card; returns the session."""
    await confirmed(bot, BOB, day, hour)
    async with bot.sessionmaker() as db:
        return await db.scalar(select(Session).order_by(Session.id.desc()))


async def add_members(bot: BotHarness, **members: tuple[int | None, str | None, bool]) -> dict:
    """Members by name: (Telegram ID, @username, active). Returns their IDs."""
    async with bot.sessionmaker() as db:
        added = {
            name: Member(display_name=name, telegram_user_id=tid, username=handle, active=active)
            for name, (tid, handle, active) in members.items()
        }
        db.add_all(added.values())
        await db.commit()
        return {name: member.id for name, member in added.items()}


async def answer(bot: BotHarness, session_id: int, member_id: int, status: str) -> None:
    async with bot.sessionmaker() as db:
        await set_attendance(db, session_id, member_id, status)
        await db.commit()


async def sent_at(bot: BotHarness, session_id: int):
    async with bot.sessionmaker() as db:
        return await db.scalar(select(Session.reminder_sent_at).where(Session.id == session_id))


async def test_it_replies_to_the_card_tagging_who_hasnt_answered(bot: BotHarness) -> None:
    session = await booked(bot)  # Bob, @bob_t, hasn't answered
    ids = await add_members(
        bot,
        carol=(333, None, True),  # no @username: a tappable name
        dave=(444, "dave_d", True),
        erin=(555, "erin_e", True),
        guest=(None, None, True),  # no Telegram account to tag
        frank=(666, "frank_f", False),  # deactivated
    )
    await answer(bot, session.id, ids["dave"], "in")
    await answer(bot, session.id, ids["erin"], "out")

    await remind(bot)

    (reminder,) = reminders(bot)
    assert reminder.params["chat_id"] == GROUP_ID
    assert reminder.params["text"] == (
        f"🔔 <b>{fmt_session(at_sgt(DAY, 11), at_sgt(DAY, 13))}</b>: are you playing? "
        "Tap ✅ I'm in or ❌ I'm out on the card.\n"
        '@bob_t, <a href="tg://user?id=333">carol</a>'
    )
    assert reminder.params["reply_parameters"]["message_id"] == session.card_message_id
    assert reminder.params["reply_parameters"]["allow_sending_without_reply"]
    assert await sent_at(bot, session.id) == TEN_SGT


async def test_it_waits_for_ten_sgt_on_the_reminder_day(bot: BotHarness) -> None:
    await booked(bot)

    await remind(bot, at_sgt(REMINDER_DAY - timedelta(days=1), 10))
    await remind(bot, at_sgt(REMINDER_DAY, 0))
    await remind(bot, at_sgt(REMINDER_DAY, 9, 45))
    assert reminders(bot) == []

    await remind(bot, at_sgt(REMINDER_DAY, 10))
    assert len(reminders(bot)) == 1


async def test_it_is_sent_once(bot: BotHarness) -> None:
    await booked(bot)

    await remind(bot)
    await remind(bot, TEN_SGT + timedelta(minutes=15))
    await remind(bot, TEN_SGT + timedelta(days=1))

    assert len(reminders(bot)) == 1


async def test_a_missed_day_is_caught_up_until_the_session_starts(bot: BotHarness) -> None:
    await booked(bot)

    await remind(bot, at_sgt(DAY, 10))

    assert len(reminders(bot)) == 1


async def test_none_once_the_session_has_started(bot: BotHarness) -> None:
    await booked(bot, hour="11:00")

    await remind(bot, at_sgt(DAY, 11))

    assert reminders(bot) == []


async def test_no_one_is_tagged_when_everyone_answered(bot: BotHarness) -> None:
    session = await booked(bot)
    async with bot.sessionmaker() as db:
        bob = await db.scalar(select(Member.id).where(Member.telegram_user_id == BOB["id"]))
    await answer(bot, session.id, bob, "out")

    await remind(bot)

    assert reminders(bot) == []
    assert await sent_at(bot, session.id) == TEN_SGT  # done: a later answer changes nothing


@pytest.mark.parametrize("days_ahead", [1, 2])
async def test_a_session_booked_on_or_after_its_reminder_day_gets_none(
    bot: BotHarness, days_ahead: int
) -> None:
    await booked(bot, future_day(days_ahead), "20:00")  # its card is fresh

    await remind(bot, at_sgt(future_day(1), 10))

    assert reminders(bot) == []


async def test_a_cancelled_session_gets_none(bot: BotHarness) -> None:
    await booked(bot)
    async with bot.sessionmaker() as db:
        await db.execute(update(Session).values(status="cancelled"))
        await db.commit()

    await remind(bot)

    assert reminders(bot) == []


async def test_a_card_left_in_another_group_gets_none(bot: BotHarness) -> None:
    """After a move (README), taps on the old group's cards are ignored."""
    await booked(bot)
    async with bot.sessionmaker() as db:
        await db.execute(update(Session).values(card_chat_id=-100999))
        await db.commit()

    await remind(bot)

    assert reminders(bot) == []


async def test_nothing_is_sent_before_setup(bot: BotHarness) -> None:
    await booked(bot)

    await remind(bot, settings=dataclasses.replace(TEST_SETTINGS, group_chat_id=None))

    assert reminders(bot) == []


async def test_a_failed_send_is_retried_next_tick(bot: BotHarness) -> None:
    session = await booked(bot)
    bot.telegram.fail("sendMessage", 502, "Bad Gateway")

    with pytest.raises(Exception, match="Bad Gateway"):
        await remind(bot)
    assert await sent_at(bot, session.id) is None

    await remind(bot, TEN_SGT + timedelta(minutes=15))
    assert len(reminders(bot)) == 1


def test_the_tick_sends_reminders_after_posting_missing_cards() -> None:
    names = [name for name, _ in default_steps()]
    assert names.index("attendance_cards") < names.index("attendance_reminders")
