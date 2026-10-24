from datetime import date, timedelta

from sqlalchemy import select

from badminton.bot.admin import NOT_ADMIN
from badminton.db.models import Attendance, AuditLog, Member, Session
from badminton.domain.timeutil import at_sgt, fmt_date, fmt_session, fmt_time, sgt_date, utc_now
from badminton.services.attendance import set_attendance
from tests.bot.attendance_helpers import make_session, post
from tests.bot.edit_helpers import EditCard
from tests.conftest import BotHarness
from tests.fake_telegram import GROUP_ID, text_update, user

START = at_sgt(date(2026, 10, 4), 11)
WHEN = fmt_session(START, START + timedelta(hours=2))


async def setup(bot: BotHarness) -> tuple[int, dict[str, int]]:
    """A finished $14.80 session (paid by alice) with bob and dave in."""
    session_id = await make_session(bot, starts_at=START)
    async with bot.sessionmaker() as db:
        members = {name: Member(display_name=name) for name in ("bob", "dave", "carol")}
        db.add_all(members.values())
        await db.flush()
        for name in ("bob", "dave"):
            await set_attendance(db, session_id, members[name].id, "in")
        await db.commit()
        return session_id, {name: m.id for name, m in members.items()}


def edits_of(bot: BotHarness, message_id: int) -> list:
    return [
        c for c in bot.telegram.calls_to("editMessageText") if c.params["message_id"] == message_id
    ]


async def ins(bot: BotHarness, session_id: int) -> set[int]:
    async with bot.sessionmaker() as db:
        rows = await db.scalars(
            select(Attendance.member_id).where(
                Attendance.session_id == session_id, Attendance.status == "in"
            )
        )
        return set(rows)


async def test_edit_is_admin_only(bot: BotHarness) -> None:
    await bot.send(text_update("/edit", sender=user(222, "bob")))

    assert bot.telegram.sent_texts() == [NOT_ADMIN]


async def test_session_picker_lists_newest_first(bot: BotHarness) -> None:
    await setup(bot)
    later = utc_now() + timedelta(days=3)
    await make_session(bot, starts_at=later)

    card = await EditCard(bot).open()

    labels = [label for label in card.buttons() if label != "✖ Close"]
    assert labels == [
        f"{fmt_date(sgt_date(later))} {fmt_time(later)}",
        f"{fmt_date(date(2026, 10, 4))} 11:00",
    ]


async def test_session_picker_keeps_past_sessions_behind_many_upcoming_ones(
    bot: BotHarness,
) -> None:
    await setup(bot)
    soon = utc_now().replace(microsecond=0) + timedelta(days=1)
    upcoming = [soon + timedelta(weeks=week) for week in range(10)]
    for starts_at in upcoming:
        await make_session(bot, starts_at=starts_at)

    card = await EditCard(bot).open()

    labels = [label for label in card.buttons() if label != "✖ Close"]
    assert labels == [
        *(f"{fmt_date(sgt_date(start))} {fmt_time(start)}" for start in reversed(upcoming[:4])),
        f"{fmt_date(date(2026, 10, 4))} 11:00",
    ]


async def test_change_attendance_announces_and_audits(bot: BotHarness) -> None:
    session_id, ids = await setup(bot)
    card_message = await post(bot, session_id)
    card = await EditCard(bot).open()

    await card.tap_starting(fmt_date(date(2026, 10, 4)))
    assert "What do you want to change?" in card.text
    await card.tap("👥 Attendance")
    assert {"✅ bob", "✅ dave", "⬜ carol", "⬜ alice"} <= set(card.buttons())
    await card.tap("⬜ carol")
    await card.tap("✅ dave")
    assert {"✅ carol", "⬜ dave"} <= set(card.buttons())
    await card.tap("💾 Save")

    assert await ins(bot, session_id) == {ids["bob"], ids["carol"]}
    assert card.text == f"✅ Saved attendance for {WHEN}."
    assert bot.telegram.sent_texts(GROUP_ID)[-1] == (
        f"✏️ Admin changed attendance for <b>{WHEN}</b>: +carol −dave\n"
        "💵 Court now $7.40 each (2 played)"
    )
    refreshed = [
        c
        for c in bot.telegram.calls_to("editMessageText")
        if c.params["message_id"] == card_message["message_id"]
    ]
    assert "✅ In (2): bob, carol" in refreshed[-1].params["text"]
    async with bot.sessionmaker() as db:
        row = await db.scalar(select(AuditLog))
    assert (row.action, row.payload["before"], row.payload["after"]) == (
        "attendance.edit",
        ["bob", "dave"],
        ["bob", "carol"],
    )


async def test_saving_keeps_answers_given_while_editing(bot: BotHarness) -> None:
    session_id, ids = await setup(bot)
    card = await EditCard(bot).open()
    await card.tap_starting(fmt_date(date(2026, 10, 4)))
    await card.tap("👥 Attendance")
    async with bot.sessionmaker() as db:
        await set_attendance(db, session_id, ids["carol"], "in")  # carol taps ✅ on the card
        await db.commit()

    await card.tap("✅ dave")
    await card.tap("💾 Save")

    assert await ins(bot, session_id) == {ids["bob"], ids["carol"]}
    assert bot.telegram.sent_texts(GROUP_ID)[-1].startswith(
        f"✏️ Admin changed attendance for <b>{WHEN}</b>: −dave\n"
    )


async def test_saving_without_changes(bot: BotHarness) -> None:
    await setup(bot)
    card = await EditCard(bot).open()
    await card.tap_starting(fmt_date(date(2026, 10, 4)))
    await card.tap("👥 Attendance")

    await card.tap("💾 Save")

    assert card.text == f"✏️ {WHEN}: no changes."
    assert not [t for t in bot.telegram.sent_texts(GROUP_ID) if t.startswith("✏️ Admin")]


async def test_removing_everyone_says_session_is_left_out(bot: BotHarness) -> None:
    await setup(bot)
    card = await EditCard(bot).open()
    await card.tap_starting(fmt_date(date(2026, 10, 4)))
    await card.tap("👥 Attendance")

    await card.tap("✅ bob")
    await card.tap("✅ dave")
    await card.tap("💾 Save")

    assert bot.telegram.sent_texts(GROUP_ID)[-1].endswith(
        "Nobody is marked in now, so this session is left out of the costs."
    )


async def test_locked_card_stays_locked(bot: BotHarness) -> None:
    session_id, _ = await setup(bot)
    card_message = await post(bot, session_id)
    async with bot.sessionmaker() as db:
        session = await db.get(Session, session_id)
        session.locked_at = utc_now()
        await db.commit()
    card = await EditCard(bot).open()
    assert f"{fmt_date(date(2026, 10, 4))} 11:00 🔒" in card.buttons()
    await card.tap_starting(fmt_date(date(2026, 10, 4)))
    await card.tap("👥 Attendance")
    await card.tap("⬜ carol")
    await card.tap("💾 Save")

    refreshed = [
        c
        for c in bot.telegram.calls_to("editMessageText")
        if c.params["message_id"] == card_message["message_id"]
    ]
    assert refreshed[-1].params["text"].endswith("🔒 Attendance is locked.")
    assert "reply_markup" not in refreshed[-1].params
