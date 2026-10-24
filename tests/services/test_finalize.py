from datetime import date, timedelta

import pytest
from sqlalchemy import select, update
from telegram.error import BadRequest, NetworkError

from badminton.db.models import Booking, Member, Session, ShuttleBatch
from badminton.domain.timeutil import at_sgt, fmt_session, utc_now
from badminton.services.attendance import set_attendance
from badminton.services.finalize import finalize_ended_sessions
from badminton.services.tick import TickContext
from tests.bot.attendance_helpers import make_session, move_session, post
from tests.conftest import TEST_SETTINGS, BotHarness
from tests.fake_telegram import ADMIN_ID, GROUP_ID, callback_update, user

DAY = date(2026, 11, 8)
START = at_sgt(DAY, 11)
AFTER = START + timedelta(hours=2)
WHEN = fmt_session(START, AFTER)


def tick(bot: BotHarness, when=AFTER) -> TickContext:
    return TickContext(bot.application.bot, TEST_SETTINGS, bot.sessionmaker, when)


async def mark_in(bot: BotHarness, session_id: int, *names: str) -> None:
    async with bot.sessionmaker() as db:
        for name in names:
            member = Member(display_name=name)
            db.add(member)
            await db.flush()
            await set_attendance(db, session_id, member.id, "in")
        await db.commit()


def summaries(bot: BotHarness) -> list[str]:
    return [t for t in bot.telegram.sent_texts(GROUP_ID) if "is locked." in t]


async def test_ended_session_is_locked_with_a_summary(bot: BotHarness) -> None:
    session_id = await make_session(bot, starts_at=START)
    card = await post(bot, session_id)
    await mark_in(bot, session_id, "bob", "dave", "erin")

    await finalize_ended_sessions(tick(bot))

    locked_card = bot.telegram.last("editMessageText").params
    assert locked_card["message_id"] == card["message_id"]
    assert locked_card["text"].endswith("🔒 Attendance is locked.")
    assert "reply_markup" not in locked_card
    assert summaries(bot) == [
        f"🔒 <b>{WHEN}</b> is locked.\n👥 3 played: bob, dave, erin\n💵 Court $4.93–$4.94 each"
    ]
    async with bot.sessionmaker() as db:
        assert (await db.get(Session, session_id)).locked_at == AFTER


async def test_summary_names_the_shuttle_batch(bot: BotHarness) -> None:
    session_id = await make_session(bot, starts_at=START)
    await mark_in(bot, session_id, "bob", "dave")
    async with bot.sessionmaker() as db:
        payer = await db.scalar(select(Member.id).where(Member.display_name == "alice"))
        batch = ShuttleBatch(
            payer_member_id=payer,
            amount_cents=8000,
            starts_at=START - timedelta(days=30),
            opened_at=START - timedelta(days=30),
        )
        db.add(batch)
        await db.commit()

    await finalize_ended_sessions(tick(bot))

    assert summaries(bot)[0].endswith(
        f"💵 Court $7.40 each\n🏸 +2h each on shuttle batch #{batch.id}"
    )


async def test_only_once_and_only_after_the_end(bot: BotHarness) -> None:
    session_id = await make_session(bot, starts_at=START)
    await mark_in(bot, session_id, "bob")

    await finalize_ended_sessions(tick(bot, AFTER - timedelta(minutes=1)))
    assert summaries(bot) == []

    await finalize_ended_sessions(tick(bot))
    await finalize_ended_sessions(tick(bot, AFTER + timedelta(days=1)))
    assert len(summaries(bot)) == 1


async def test_cancelled_sessions_are_not_finalised(bot: BotHarness) -> None:
    await make_session(bot, starts_at=START)
    async with bot.sessionmaker() as db:
        await db.execute(update(Session).values(status="cancelled"))
        await db.commit()

    await finalize_ended_sessions(tick(bot))

    assert summaries(bot) == []


async def test_nobody_in_alerts_the_admin(bot: BotHarness) -> None:
    await make_session(bot, starts_at=START)

    await finalize_ended_sessions(tick(bot))

    assert summaries(bot) == [
        f"🔒 <b>{WHEN}</b> is locked.\n"
        "Nobody was marked in, so the admin has been asked to check it."
    ]
    admin_dm = [c for c in bot.telegram.calls_to("sendMessage") if c.params["chat_id"] == ADMIN_ID]
    assert admin_dm[0].params["text"].startswith(f"⚠️ Nobody was marked in for <b>{WHEN}</b>")


async def test_a_failed_admin_dm_does_not_unlock_the_session(bot: BotHarness) -> None:
    session_id = await make_session(bot, starts_at=START)
    bot.telegram.fail("sendMessage", 502, "Bad Gateway", chat_id=ADMIN_ID)

    with pytest.raises(NetworkError):
        await finalize_ended_sessions(tick(bot))
    await finalize_ended_sessions(tick(bot, AFTER + timedelta(hours=1)))

    assert len(summaries(bot)) == 1
    async with bot.sessionmaker() as db:
        assert (await db.get(Session, session_id)).locked_at == AFTER


async def test_a_failed_summary_is_retried(bot: BotHarness) -> None:
    session_id = await make_session(bot, starts_at=START)
    await mark_in(bot, session_id, "bob")
    bot.telegram.fail("sendMessage", 502, "Bad Gateway", chat_id=GROUP_ID)

    with pytest.raises(NetworkError):
        await finalize_ended_sessions(tick(bot))
    bot.telegram.clear()  # forget the failed attempt
    later = AFTER + timedelta(hours=1)
    await finalize_ended_sessions(tick(bot, later))

    assert len(summaries(bot)) == 1
    async with bot.sessionmaker() as db:
        assert (await db.get(Session, session_id)).locked_at == later


async def test_one_failing_session_does_not_hold_up_the_rest(bot: BotHarness) -> None:
    first = await make_session(bot, starts_at=START)
    second = await make_session(bot, starts_at=START + timedelta(hours=3))
    await mark_in(bot, first, "bob")
    await mark_in(bot, second, "dave")
    bot.telegram.fail("sendMessage", 400, "Bad Request: message is too long", chat_id=GROUP_ID)

    with pytest.raises(BadRequest):
        await finalize_ended_sessions(tick(bot, AFTER + timedelta(hours=3)))

    async with bot.sessionmaker() as db:
        assert (await db.get(Session, first)).locked_at is None  # retried next time
        assert (await db.get(Session, second)).locked_at is not None


async def test_a_late_tap_finalises_straight_away(bot: BotHarness) -> None:
    session_id = await make_session(bot)
    card = await post(bot, session_id)
    await mark_in(bot, session_id, "dave")
    await move_session(bot, session_id, utc_now() - timedelta(hours=3))  # ended an hour ago
    button = card["reply_markup"]["inline_keyboard"][0][0]["callback_data"]

    await bot.send(callback_update(button, sender=user(222, "bob"), on_message=card))

    assert len(summaries(bot)) == 1
    assert "1 played: dave" in summaries(bot)[0]


async def test_locking_unpins_the_booking_records(bot: BotHarness) -> None:
    session_id = await make_session(bot, starts_at=START)
    async with bot.sessionmaker() as db:
        await db.execute(update(Booking).values(record_chat_id=GROUP_ID, record_message_id=4321))
        await db.commit()
    await mark_in(bot, session_id, "bob")

    await finalize_ended_sessions(tick(bot))
    await finalize_ended_sessions(tick(bot))

    unpinned = [call.params for call in bot.telegram.calls_to("unpinChatMessage")]
    assert unpinned == [{"chat_id": GROUP_ID, "message_id": 4321}]


async def test_an_unpin_error_still_unpins_the_other_records(bot: BotHarness) -> None:
    session_id = await make_session(bot, starts_at=START)
    async with bot.sessionmaker() as db:
        await db.execute(update(Booking).values(record_chat_id=GROUP_ID, record_message_id=4321))
        first = await db.scalar(select(Booking))
        second = Booking(
            session_id=session_id,
            payer_member_id=first.payer_member_id,
            submitted_by_member_id=first.payer_member_id,
            starts_at=START,
            minutes=60,
            amount_cents=740,
            record_chat_id=GROUP_ID,
            record_message_id=4322,
        )
        db.add(second)
        await db.commit()
    await mark_in(bot, session_id, "bob")
    bot.telegram.fail("unpinChatMessage", 502, "Bad Gateway")

    await finalize_ended_sessions(tick(bot))

    unpinned = [call.params["message_id"] for call in bot.telegram.calls_to("unpinChatMessage")]
    assert sorted(unpinned) == [4321, 4322]  # the first failed, and was tried
