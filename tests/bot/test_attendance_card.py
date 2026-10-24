from datetime import timedelta

from sqlalchemy import select, update

from badminton.bot.handlers.attendance import LOCKED
from badminton.db.models import Attendance, Member, Session
from badminton.domain.timeutil import at_sgt, fmt_session, utc_now
from tests.bot.attendance_helpers import make_session, move_session, post
from tests.bot.booking_helpers import ALICE, book, future_day
from tests.conftest import BotHarness
from tests.fake_telegram import GROUP_ID, callback_update, user

BOB = user(222, "bob", "bob_t")
CAROL = user(333, "Carol <3")


def tap(card: dict, sender: dict, answer: str) -> dict:
    data = next(
        button["callback_data"]
        for row in card["reply_markup"]["inline_keyboard"]
        for button in row
        if button["callback_data"].startswith(f"att:{answer}:")
    )
    return callback_update(data, sender=sender, on_message=card)


def edited_text(bot: BotHarness) -> str:
    return bot.telegram.last("editMessageText").params["text"]


async def statuses(bot: BotHarness) -> dict[int, str]:
    async with bot.sessionmaker() as db:
        rows = await db.scalars(select(Attendance))
        members = {m.id: m.telegram_user_id for m in await db.scalars(select(Member))}
        return {members[a.member_id]: a.status for a in rows}


async def test_card_shows_session_payer_and_answers_without_mentions(bot: BotHarness) -> None:
    start = utc_now().replace(microsecond=0) + timedelta(days=2)
    session_id = await make_session(bot, starts_at=start)

    card = await post(bot, session_id)

    assert card["text"] == (
        f"🏸 <b>{fmt_session(start, start + timedelta(hours=2))}</b> (2h)\n"
        "💵 Court $14.80 — paid by alice\n"
        "✅ In (0): —\n"
        "❌ Out (0): —\n"
        "No answer = not playing."
    )
    assert "@" not in card["text"]
    assert [b["text"] for b in card["reply_markup"]["inline_keyboard"][0]] == [
        "✅ I'm in",
        "❌ I'm out",
    ]
    async with bot.sessionmaker() as db:
        session = await db.get(Session, session_id)
        assert (session.card_chat_id, session.card_message_id) == (GROUP_ID, card["message_id"])
        assert session.card_sent_at is not None


async def test_answers_can_change_until_the_end(bot: BotHarness) -> None:
    card = await post(bot, await make_session(bot))

    await bot.send(tap(card, BOB, "in"))
    assert "✅ In (1): bob" in edited_text(bot)
    assert bot.telegram.last("answerCallbackQuery").params["text"] == "You're in ✅"

    await bot.send(tap(card, CAROL, "in"))
    await bot.send(tap(card, BOB, "out"))
    assert "✅ In (1): Carol &lt;3\n❌ Out (1): bob" in edited_text(bot)
    assert await statuses(bot) == {BOB["id"]: "out", CAROL["id"]: "in"}


async def test_first_tap_registers_the_member(bot: BotHarness) -> None:
    card = await post(bot, await make_session(bot))

    await bot.send(tap(card, CAROL, "in"))

    async with bot.sessionmaker() as db:
        member = await db.scalar(select(Member).where(Member.telegram_user_id == CAROL["id"]))
        assert member.display_name == "Carol <3"


async def test_repeated_tap_is_harmless(bot: BotHarness) -> None:
    card = await post(bot, await make_session(bot))
    await bot.send(tap(card, BOB, "in"))
    bot.telegram.fail("editMessageText", 400, "Bad Request: message is not modified")

    await bot.send(tap(card, BOB, "in"))

    assert await statuses(bot) == {BOB["id"]: "in"}


async def test_taps_after_the_end_are_refused(bot: BotHarness) -> None:
    session_id = await make_session(bot)
    card = await post(bot, session_id)
    await move_session(bot, session_id, utc_now() - timedelta(hours=2))

    await bot.send(tap(card, BOB, "in"))

    assert bot.telegram.last("answerCallbackQuery").params == {
        "callback_query_id": bot.telegram.last("answerCallbackQuery").params["callback_query_id"],
        "text": LOCKED,
        "show_alert": True,
    }
    assert await statuses(bot) == {}


async def test_cancelled_session(bot: BotHarness) -> None:
    session_id = await make_session(bot)
    card = await post(bot, session_id)
    async with bot.sessionmaker() as db:
        await db.execute(update(Session).values(status="cancelled"))
        await db.commit()

    await bot.send(tap(card, BOB, "in"))

    assert bot.telegram.last("answerCallbackQuery").params["text"] == "This session was cancelled."


async def test_card_lists_each_payer_when_several_booked(bot: BotHarness) -> None:
    day = future_day(1)
    first = await book(bot, ALICE, day, hour="20:00", hours="1h")
    await first.tap("✅ Confirm")  # the session's card goes up in T26; post it by hand here
    async with bot.sessionmaker() as db:
        session_id = await db.scalar(select(Session.id))
    card = await post(bot, session_id)
    second = await book(bot, BOB, day, hour="21:00", hours="1h")
    await second.tap("➕ Add to 20:00–21:00")
    await second.tap("✅ Confirm")

    refreshed = [
        c
        for c in bot.telegram.calls_to("editMessageText")
        if c.params["message_id"] == card["message_id"]
    ]
    assert refreshed[-1].params["text"].splitlines()[:2] == [
        f"🏸 <b>{fmt_session(at_sgt(day, 20), at_sgt(day, 22))}</b> (2h)",
        "💵 Court $14.80 — alice $7.40, bob $7.40",
    ]
