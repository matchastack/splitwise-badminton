import asyncio
from datetime import timedelta

from sqlalchemy import func, select, update

from badminton.bot.admin import NOT_ADMIN
from badminton.bot.handlers.member_admin import LINK_GONE, NOBODY_TO_LINK, _link_pair
from badminton.db.models import (
    Attendance,
    AuditLog,
    Booking,
    Draft,
    Member,
    OpeningBalance,
    Session,
)
from badminton.domain.timeutil import fmt_date, sgt_date, utc_now
from badminton.services.drafts import cleanup_expired_drafts
from badminton.services.members import active_members, link_guest, member_label
from badminton.services.tick import TickContext
from tests.conftest import TEST_SETTINGS, BotHarness
from tests.fake_telegram import ADMIN_ID, GROUP_ID, callback_update, message, text_update, user

ZY = user(ADMIN_ID, "zy", "zy_handle")
SHAWN = user(222, "shawn")


async def members(bot: BotHarness) -> dict[str, Member]:
    async with bot.sessionmaker() as db:
        return {m.display_name: m for m in await db.scalars(select(Member))}


async def open_member_list(bot: BotHarness) -> dict:
    await bot.send(text_update("/addmember danish", sender=ZY))
    await bot.send(text_update("/members", sender=ZY))
    return bot.telegram.last("sendMessage").message


def button(card: dict, text_prefix: str) -> str:
    for row in card["reply_markup"]["inline_keyboard"]:
        for btn in row:
            if btn["text"].startswith(text_prefix):
                return btn["callback_data"]
    raise AssertionError(f"no button starting with {text_prefix!r}")


async def test_admin_adds_a_guest_member(bot: BotHarness) -> None:
    await bot.send(text_update("/addmember  danish  ", sender=ZY))

    guest = (await members(bot))["danish"]
    assert guest.telegram_user_id is None
    assert guest.active
    assert bot.telegram.sent_texts(GROUP_ID) == ["➕ Admin added guest member <b>danish</b>."]
    async with bot.sessionmaker() as db:
        row = await db.scalar(select(AuditLog))
        assert (row.action, row.payload["after"]) == (
            "member.add",
            {"id": guest.id, "name": "danish"},
        )


async def test_addmember_needs_a_name_and_an_admin(bot: BotHarness) -> None:
    await bot.send(text_update("/addmember", sender=ZY))
    await bot.send(text_update("/addmember sneaky", sender=SHAWN))

    assert bot.telegram.sent_texts() == ["Usage: /addmember &lt;name&gt;", NOT_ADMIN]
    assert "sneaky" not in await members(bot)


async def test_member_list_shows_notes(bot: BotHarness) -> None:
    card = await open_member_list(bot)

    assert card["text"] == "👥 <b>Members</b>\n• zy (@zy_handle)\n• danish (guest)"
    assert button(card, "🚫").startswith("mem:act:")


async def test_deactivate_and_reactivate(bot: BotHarness) -> None:
    card = await open_member_list(bot)
    toggle = button(card, "✏️ danish").replace("ren", "act")

    await bot.send(callback_update(toggle, sender=ZY, on_message=card))

    assert not (await members(bot))["danish"].active
    assert "• danish (guest, inactive)" in bot.telegram.last("editMessageText").params["text"]
    assert bot.telegram.sent_texts(GROUP_ID)[-1] == "🚫 Admin deactivated <b>danish</b>."
    async with bot.sessionmaker() as db:
        assert [m.display_name for m in await active_members(db)] == ["zy"]

    await bot.send(callback_update(toggle, sender=ZY, on_message=card))

    assert (await members(bot))["danish"].active
    assert bot.telegram.sent_texts(GROUP_ID)[-1] == "↩️ Admin reactivated <b>danish</b>."


async def test_rename_through_a_prompt(bot: BotHarness) -> None:
    card = await open_member_list(bot)

    await bot.send(callback_update(button(card, "✏️ danish"), sender=ZY, on_message=card))
    prompt = bot.telegram.last("sendMessage")
    assert prompt.params["text"].endswith("what should <b>danish</b> be called?")

    await bot.send(
        {"update_id": 1, "message": message(sender=ZY, text="x" * 40, reply_to=prompt.message)}
    )
    reprompt = bot.telegram.last("sendMessage")
    assert reprompt.params["text"].startswith("⚠️ Names can be at most 32 characters.")

    await bot.send(
        {"update_id": 2, "message": message(sender=ZY, text="Danish K", reply_to=reprompt.message)}
    )

    assert "Danish K" in await members(bot)
    assert (
        bot.telegram.sent_texts(GROUP_ID)[-1] == "✏️ Admin renamed <b>danish</b> to <b>Danish K</b>."
    )
    assert "• Danish K (guest)" in bot.telegram.last("editMessageText").params["text"]
    async with bot.sessionmaker() as db:
        assert await db.scalar(select(func.count()).select_from(Draft)) == 0


async def test_an_unanswered_rename_expires_without_the_member_list(bot: BotHarness) -> None:
    card = await open_member_list(bot)
    await bot.send(callback_update(button(card, "✏️ danish"), sender=ZY, on_message=card))
    prompt = bot.telegram.last("sendMessage").message
    async with bot.sessionmaker() as db:
        await db.execute(update(Draft).values(expires_at=utc_now()))
        await db.commit()

    await cleanup_expired_drafts(
        TickContext(bot.application.bot, TEST_SETTINGS, bot.sessionmaker, utc_now())
    )

    deleted = [call.params["message_id"] for call in bot.telegram.calls_to("deleteMessage")]
    assert deleted == [prompt["message_id"]]


async def test_member_buttons_are_admin_only(bot: BotHarness) -> None:
    card = await open_member_list(bot)

    await bot.send(callback_update(button(card, "🚫"), sender=SHAWN, on_message=card))

    assert bot.telegram.last("answerCallbackQuery").params["text"] == NOT_ADMIN
    assert all(m.active for m in (await members(bot)).values())


DANISH = user(333, "Danish K", "danishk")


async def give_opening_balances(bot: BotHarness, cents: dict[str, int]) -> None:
    by_name = await members(bot)
    async with bot.sessionmaker() as db:
        db.add_all(
            OpeningBalance(member_id=by_name[name].id, amount_cents=amount)
            for name, amount in cents.items()
        )
        await db.commit()


def buttons(card: dict) -> list[str]:
    return [btn["text"] for row in card["reply_markup"]["inline_keyboard"] for btn in row]


async def tap(bot: BotHarness, card: dict, text_prefix: str, sender: dict = ZY) -> dict:
    await bot.send(callback_update(button(card, text_prefix), sender=sender, on_message=card))
    return bot.telegram.last("editMessageText").message


async def test_only_guests_have_a_link_button(bot: BotHarness) -> None:
    card = await open_member_list(bot)

    assert buttons(card) == [
        "✏️ zy",
        "🚫 Deactivate",
        "✏️ danish",
        "🔗 Link",
        "🚫 Deactivate",
    ]
    assert button(card, "🔗") == f"mem:lnk:{(await members(bot))['danish'].id}"


async def test_admin_links_a_guest_to_the_account_they_joined_with(bot: BotHarness) -> None:
    card = await open_member_list(bot)
    await give_opening_balances(bot, {"danish": -500, "zy": 500})
    await bot.send(text_update("/balance", sender=DANISH))  # danish joins: a second record
    assert set(await members(bot)) == {"zy", "danish", "Danish K"}

    picker = await tap(bot, card, "🔗")
    assert picker["text"] == (
        "🔗 Which Telegram account is <b>danish</b>?\n"
        "Pick the member the bot added when they first used it."
    )
    assert buttons(picker) == ["Danish K (@danishk)", "zy (@zy_handle)", "‹ Back"]

    confirm = await tap(bot, picker, "Danish K")
    assert confirm["text"] == (
        "🔗 Link guest <b>danish</b> to <b>Danish K (@danishk)</b>?\n"
        "Their records become one, called danish. Balances −$5.00 and +$0.00 make −$5.00."
    )

    listed = await tap(bot, confirm, "✅ Link")

    by_name = await members(bot)
    assert set(by_name) == {"zy", "danish"}
    assert (by_name["danish"].telegram_user_id, by_name["danish"].username) == (333, "danishk")
    assert listed["text"] == "👥 <b>Members</b>\n• zy (@zy_handle)\n• danish (@danishk)"
    assert "🔗 Link" not in buttons(listed)
    assert bot.telegram.sent_texts(GROUP_ID)[-1] == (
        "🔗 Admin linked guest <b>danish</b> to @danishk. "
        "Their balance and history are now one record."
    )
    async with bot.sessionmaker() as db:
        row = await db.scalar(select(AuditLog).where(AuditLog.action == "member.link"))
        assert row.actor_member_id == by_name["zy"].id
        assert row.payload["before"]["account"]["name"] == "Danish K"
        assert row.payload["after"] == {"id": by_name["danish"].id, "balance_cents": -500}

    await bot.send(text_update("/balance", sender=DANISH, chat_id=333))
    assert (
        bot.telegram.last("sendMessage")
        .params["text"]
        .startswith("💰 <b>danish</b>, you owe $5.00.")
    )


async def test_the_admin_can_link_a_guest_to_their_own_account(bot: BotHarness) -> None:
    async with bot.sessionmaker() as db:
        db.add(Member(display_name="zy (sheet)"))  # imported before the admin used the bot
        await db.commit()
    await bot.send(text_update("/members", sender=ZY))  # the admin isn't a member yet
    await bot.send(text_update("/balance", sender=ZY))
    card = bot.telegram.calls_to("sendMessage")[0].message

    confirm = await tap(bot, await tap(bot, card, "🔗"), "zy")
    await tap(bot, confirm, "✅ Link")

    by_name = await members(bot)
    assert set(by_name) == {"zy (sheet)"}
    assert by_name["zy (sheet)"].telegram_user_id == ADMIN_ID
    async with bot.sessionmaker() as db:
        row = await db.scalar(select(AuditLog).where(AuditLog.action == "member.link"))
        assert row.actor_member_id == by_name["zy (sheet)"].id


async def test_linking_needs_someone_on_telegram_and_an_up_to_date_card(bot: BotHarness) -> None:
    async with bot.sessionmaker() as db:
        db.add(Member(display_name="danish"))
        await db.commit()
    await bot.send(text_update("/members", sender=ZY))
    card = bot.telegram.last("sendMessage").message

    await bot.send(callback_update(button(card, "🔗"), sender=ZY, on_message=card))
    assert bot.telegram.last("answerCallbackQuery").params["text"] == NOBODY_TO_LINK

    await bot.send(text_update("/balance", sender=DANISH))
    confirm = await tap(bot, await tap(bot, card, "🔗"), "Danish K")
    await tap(bot, confirm, "✅ Link")
    await bot.send(callback_update(button(confirm, "✅ Link"), sender=ZY, on_message=confirm))

    assert bot.telegram.last("answerCallbackQuery").params["text"] == LINK_GONE


async def test_back_returns_to_the_member_list(bot: BotHarness) -> None:
    card = await open_member_list(bot)

    listed = await tap(bot, await tap(bot, card, "🔗"), "‹ Back")

    assert listed["text"] == card["text"]


async def test_link_buttons_are_admin_only(bot: BotHarness) -> None:
    card = await open_member_list(bot)
    await bot.send(text_update("/balance", sender=SHAWN))

    await bot.send(callback_update(button(card, "🔗"), sender=SHAWN, on_message=card))
    guest = (await members(bot))["danish"]
    shawn = (await members(bot))["shawn"]
    await bot.send(
        callback_update(f"mem:lnk:{guest.id}:{shawn.id}:ok", sender=SHAWN, on_message=card)
    )

    assert [call.params["text"] for call in bot.telegram.calls_to("answerCallbackQuery")] == [
        NOT_ADMIN,
        NOT_ADMIN,
    ]
    assert (await members(bot))["danish"].telegram_user_id is None


def test_member_label_notes_the_handle_and_status() -> None:
    assert member_label(
        Member(display_name="zy", username="zy_h", telegram_user_id=1, active=True)
    ) == ("zy (@zy_h)")
    assert member_label(Member(display_name="danish", active=False)) == "danish (guest, inactive)"
    assert member_label(Member(display_name="nic", telegram_user_id=2, active=True)) == "nic"


async def played_together(bot: BotHarness, *names: str, days_ago: int = 3) -> None:
    """A session that ended ``days_ago``, booked by zy, with ``names`` marked in."""
    by_name = await members(bot)
    start = utc_now() - timedelta(days=days_ago)
    async with bot.sessionmaker() as db:
        session = Session(starts_at=start, ends_at=start + timedelta(hours=2))
        db.add(session)
        await db.flush()
        db.add(
            Booking(
                session_id=session.id,
                payer_member_id=by_name["zy"].id,
                submitted_by_member_id=by_name["zy"].id,
                starts_at=start,
                minutes=120,
                amount_cents=1480,
            )
        )
        db.add_all(
            Attendance(session_id=session.id, member_id=by_name[name].id, status="in")
            for name in names
        )
        await db.commit()


async def test_the_confirm_step_refuses_a_session_both_records_played(bot: BotHarness) -> None:
    card = await open_member_list(bot)
    await bot.send(text_update("/balance", sender=DANISH))
    await played_together(bot, "zy", "danish", "Danish K")

    refused = await tap(bot, await tap(bot, card, "🔗"), "Danish K")

    day = fmt_date(sgt_date(utc_now() - timedelta(days=3)))
    assert refused["text"] == (
        f"⚠️ <b>danish</b> and <b>Danish K (@danishk)</b> are both marked in for {day}. "
        "Linked, they would count once there, changing everyone's share of those sessions. "
        "Use /edit to mark one of them out first."
    )
    assert buttons(refused) == ["‹ Back"]


async def test_linking_refreshes_the_latest_invoice_card(bot: BotHarness) -> None:
    card = await open_member_list(bot)
    await bot.send(text_update("/balance", sender=DANISH))
    await played_together(bot, "zy", "Danish K")
    await bot.send(text_update("/invoice", sender=ZY))
    invoice_card = bot.telegram.last("sendMessage").message
    assert buttons(invoice_card) == ["✅ Danish K → zy $7.40 paid"]

    confirm = await tap(bot, await tap(bot, card, "🔗"), "Danish K")
    await tap(bot, confirm, "✅ Link")

    refreshed = [
        call.message
        for call in bot.telegram.calls_to("editMessageText")
        if call.params["message_id"] == invoice_card["message_id"]
    ]
    assert buttons(refreshed[-1]) == ["✅ danish → zy $7.40 paid"]  # the line moved to danish
    assert "• @danishk → @zy_handle $7.40" in refreshed[-1]["text"]


async def test_a_second_tap_on_link_waits_then_finds_it_done(bot: BotHarness) -> None:
    await bot.send(text_update("/addmember danish", sender=ZY))
    await bot.send(text_update("/balance", sender=DANISH))
    by_name = await members(bot)
    ids = by_name["danish"].id, by_name["Danish K"].id

    async with bot.sessionmaker() as first, bot.sessionmaker() as second:
        guest, account = await _link_pair(first, *ids, lock=True)
        await link_guest(first, guest, account, now=utc_now())
        waiting = asyncio.create_task(_link_pair(second, *ids, lock=True))
        await asyncio.sleep(0.3)
        assert not waiting.done()  # held by the first tap's row locks
        await first.commit()

        assert await waiting is None  # so it answers LINK_GONE instead of linking again
