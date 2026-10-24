from datetime import timedelta

from sqlalchemy import select

from badminton.db.models import Member, ShuttleBatch, Transfer
from badminton.domain.timeutil import fmt_session, utc_now
from badminton.services.attendance import set_attendance
from tests.bot.attendance_helpers import make_session
from tests.conftest import BotHarness
from tests.fake_telegram import ADMIN_ID, text_update, user

ALICE = user(ADMIN_ID, "alice", "alice_handle")
BOB = user(222, "bob")


async def member_id(bot: BotHarness, telegram_id: int, name: str) -> int:
    async with bot.sessionmaker() as db:
        member = await db.scalar(select(Member).where(Member.telegram_user_id == telegram_id))
        if member is None:
            member = Member(display_name=name, telegram_user_id=telegram_id)
            db.add(member)
            await db.commit()
        return member.id


async def played_session(bot: BotHarness, *player_ids: int) -> int:
    """A finished $14.80 session booked by alice (Telegram ID 111)."""
    session_id = await make_session(bot, starts_at=utc_now() - timedelta(hours=3))
    async with bot.sessionmaker() as db:
        for player in player_ids:
            await set_attendance(db, session_id, player, "in")
        await db.commit()
    return session_id


async def reply(bot: BotHarness, command: str, sender: dict) -> str:
    await bot.send(text_update(command, sender=sender))
    return bot.telegram.last("sendMessage").params["text"]


async def test_balance_for_creditor_debtor_and_newcomer(bot: BotHarness) -> None:
    alice = await member_id(bot, ADMIN_ID, "alice")
    bob = await member_id(bot, BOB["id"], "bob")
    await played_session(bot, alice, bob)  # alice paid $14.80, both played

    assert await reply(bot, "/balance", ALICE) == "💰 <b>alice</b>, you're owed $7.40."
    assert await reply(bot, "/balance", BOB) == "💰 <b>bob</b>, you owe $7.40."
    assert await reply(bot, "/balance", user(444, "new")) == "💰 <b>new</b>, you're all square."


async def test_balance_counts_transfers(bot: BotHarness) -> None:
    await played_session(bot)
    alice = await member_id(bot, ADMIN_ID, "alice")
    bob = await member_id(bot, BOB["id"], "bob")
    async with bot.sessionmaker() as db:
        db.add(Transfer(from_member_id=bob, to_member_id=alice, amount_cents=500))
        await db.commit()

    assert await reply(bot, "/balance", BOB) == "💰 <b>bob</b>, you're owed $5.00."


async def test_balance_shows_pending_shuttle_hours(bot: BotHarness) -> None:
    await played_session(bot)
    bob = await member_id(bot, BOB["id"], "bob")
    async with bot.sessionmaker() as db:
        payer = await db.scalar(select(Member.id).where(Member.telegram_user_id == ADMIN_ID))
        batch = ShuttleBatch(
            payer_member_id=payer, amount_cents=7679, starts_at=utc_now() - timedelta(days=1)
        )
        db.add(batch)
        await db.commit()
    await played_session(bot, bob)

    text = await reply(bot, "/balance", BOB)

    assert text.splitlines()[1] == (
        f"🏸 Current shuttle batch #{batch.id}: 2h played so far. "
        "It's billed once the batch is finished."
    )


async def test_upcoming_lists_scheduled_sessions(bot: BotHarness) -> None:
    assert await reply(bot, "/upcoming", BOB) == (
        "No sessions booked yet. Send /book in the group to record one."
    )
    start = utc_now().replace(microsecond=0) + timedelta(days=3)
    await make_session(bot, starts_at=start)
    await played_session(bot)  # already over: not listed

    text = await reply(bot, "/upcoming", BOB)

    assert text == (
        "📅 <b>Upcoming sessions</b>\n"
        f"• {fmt_session(start, start + timedelta(hours=2))} (2h) — booked by alice"
    )
