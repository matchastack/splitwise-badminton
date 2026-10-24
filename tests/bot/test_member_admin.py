import asyncio
from datetime import timedelta

from sqlalchemy import func, select, update

from badminton.bot.admin import NOT_ADMIN
from badminton.bot.handlers.member_admin import (
    LINK_GONE,
    LIST_CLOSED,
    NOBODY_TO_LINK,
    _link_pair,
)
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

ALICE = user(ADMIN_ID, "alice", "alice_handle")
BOB = user(222, "bob")


async def members(bot: BotHarness) -> dict[str, Member]:
    async with bot.sessionmaker() as db:
        return {m.display_name: m for m in await db.scalars(select(Member))}


async def open_member_list(bot: BotHarness) -> dict:
    await bot.send(text_update("/addmember grace", sender=ALICE))
    await bot.send(text_update("/members", sender=ALICE))
    return bot.telegram.last("sendMessage").message


def button(card: dict, text_prefix: str) -> str:
    for row in card["reply_markup"]["inline_keyboard"]:
        for btn in row:
            if btn["text"].startswith(text_prefix):
                return btn["callback_data"]
    raise AssertionError(f"no button starting with {text_prefix!r}")


async def test_admin_adds_a_guest_member(bot: BotHarness) -> None:
    await bot.send(text_update("/addmember  grace  ", sender=ALICE))

    guest = (await members(bot))["grace"]
    assert guest.telegram_user_id is None
    assert guest.active
    assert bot.telegram.sent_texts(GROUP_ID) == ["➕ Admin added guest member <b>grace</b>."]
    async with bot.sessionmaker() as db:
        row = await db.scalar(select(AuditLog))
        assert (row.action, row.payload["after"]) == (
            "member.add",
            {"id": guest.id, "name": "grace"},
        )


async def test_addmember_needs_a_name_and_an_admin(bot: BotHarness) -> None:
    await bot.send(text_update("/addmember", sender=ALICE))
    await bot.send(text_update("/addmember sneaky", sender=BOB))

    assert bot.telegram.sent_texts() == ["Usage: /addmember &lt;name&gt;", NOT_ADMIN]
    assert "sneaky" not in await members(bot)


async def test_member_list_shows_notes(bot: BotHarness) -> None:
    card = await open_member_list(bot)

    assert card["text"] == "👥 <b>Members</b>\n• alice (@alice_handle)\n• grace (guest)"
    assert button(card, "🚫").startswith("mem:act:")


async def test_deactivate_and_reactivate(bot: BotHarness) -> None:
    card = await open_member_list(bot)
    toggle = button(card, "✏️ grace").replace("ren", "act")

    await bot.send(callback_update(toggle, sender=ALICE, on_message=card))

    assert not (await members(bot))["grace"].active
    closed = bot.telegram.last("editMessageText").message
    assert closed["text"] == (
        f"👥 <b>Members</b>\n• alice (@alice_handle)\n• grace (guest, inactive)\n\n{LIST_CLOSED}"
    )
    assert "reply_markup" not in closed  # closed, so a stray tap later changes nothing
    assert bot.telegram.sent_texts(GROUP_ID)[-1] == "🚫 Admin deactivated <b>grace</b>."
    async with bot.sessionmaker() as db:
        assert [m.display_name for m in await active_members(db)] == ["alice"]

    await bot.send(text_update("/members", sender=ALICE))  # a fresh list for the next change
    card = bot.telegram.last("sendMessage").message
    assert buttons(card)[-1] == "↩️ Reactivate"
    await bot.send(callback_update(button(card, "↩️"), sender=ALICE, on_message=card))

    assert (await members(bot))["grace"].active
    assert bot.telegram.sent_texts(GROUP_ID)[-1] == "↩️ Admin reactivated <b>grace</b>."


async def test_rename_through_a_prompt(bot: BotHarness) -> None:
    card = await open_member_list(bot)

    await bot.send(callback_update(button(card, "✏️ grace"), sender=ALICE, on_message=card))
    prompt = bot.telegram.last("sendMessage")
    assert prompt.params["text"].endswith("what should <b>grace</b> be called?")

    await bot.send(
        {"update_id": 1, "message": message(sender=ALICE, text="x" * 40, reply_to=prompt.message)}
    )
    reprompt = bot.telegram.last("sendMessage")
    assert reprompt.params["text"].startswith("⚠️ Names can be at most 32 characters.")

    await bot.send(
        {
            "update_id": 2,
            "message": message(sender=ALICE, text="Grace K", reply_to=reprompt.message),
        }
    )

    assert "Grace K" in await members(bot)
    assert (
        bot.telegram.sent_texts(GROUP_ID)[-1] == "✏️ Admin renamed <b>grace</b> to <b>Grace K</b>."
    )
    closed = bot.telegram.last("editMessageText").message
    assert closed["text"].endswith(f"• Grace K (guest)\n\n{LIST_CLOSED}")
    assert "reply_markup" not in closed
    async with bot.sessionmaker() as db:
        assert await db.scalar(select(func.count()).select_from(Draft)) == 0


async def test_an_unanswered_rename_expires_without_the_member_list(bot: BotHarness) -> None:
    card = await open_member_list(bot)
    await bot.send(callback_update(button(card, "✏️ grace"), sender=ALICE, on_message=card))
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

    await bot.send(callback_update(button(card, "🚫"), sender=BOB, on_message=card))

    assert bot.telegram.last("answerCallbackQuery").params["text"] == NOT_ADMIN
    assert all(m.active for m in (await members(bot)).values())


DANISH = user(333, "Grace K", "danishk")


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


async def tap(bot: BotHarness, card: dict, text_prefix: str, sender: dict = ALICE) -> dict:
    await bot.send(callback_update(button(card, text_prefix), sender=sender, on_message=card))
    return bot.telegram.last("editMessageText").message


async def test_only_guests_have_a_link_button(bot: BotHarness) -> None:
    card = await open_member_list(bot)

    assert buttons(card) == [
        "✏️ alice",
        "🚫 Deactivate",
        "✏️ grace",
        "🔗 Link",
        "🚫 Deactivate",
    ]
    assert button(card, "🔗") == f"mem:lnk:{(await members(bot))['grace'].id}"


async def test_admin_links_a_guest_to_the_account_they_joined_with(bot: BotHarness) -> None:
    card = await open_member_list(bot)
    await give_opening_balances(bot, {"grace": -500, "alice": 500})
    await bot.send(text_update("/balance", sender=DANISH))  # grace joins: a second record
    assert set(await members(bot)) == {"alice", "grace", "Grace K"}

    picker = await tap(bot, card, "🔗")
    assert picker["text"] == (
        "🔗 Which Telegram account is <b>grace</b>?\n"
        "Pick the member the bot added when they first used it."
    )
    assert buttons(picker) == ["Grace K (@danishk)", "alice (@alice_handle)", "‹ Back"]

    confirm = await tap(bot, picker, "Grace K")
    assert confirm["text"] == (
        "🔗 Link guest <b>grace</b> to <b>Grace K (@danishk)</b>?\n"
        "Their records become one, called Grace K. Balances −$5.00 and +$0.00 make −$5.00."
    )
    guest_id = (await members(bot))["grace"].id

    listed = await tap(bot, confirm, "✅ Link")

    # The guest record stays, under the name their Telegram account has, like anyone's.
    by_name = await members(bot)
    assert set(by_name) == {"alice", "Grace K"}
    linked = by_name["Grace K"]
    assert (linked.id, linked.telegram_user_id, linked.username) == (guest_id, 333, "danishk")
    assert listed["text"] == (
        f"👥 <b>Members</b>\n• alice (@alice_handle)\n• Grace K (@danishk)\n\n{LIST_CLOSED}"
    )
    assert "reply_markup" not in listed  # closed, so a stray tap can't link anyone else
    assert bot.telegram.sent_texts(GROUP_ID)[-1] == (
        "🔗 Admin linked guest <b>grace</b> to @danishk. "
        "Their balance and history are now one record."
    )
    async with bot.sessionmaker() as db:
        row = await db.scalar(select(AuditLog).where(AuditLog.action == "member.link"))
        assert row.actor_member_id == by_name["alice"].id
        assert row.payload["before"]["account"]["name"] == "Grace K"
        assert row.payload["before"]["guest"]["name"] == "grace"
        assert row.payload["after"] == {"id": guest_id, "name": "Grace K", "balance_cents": -500}

    await bot.send(text_update("/balance", sender=DANISH, chat_id=333))
    assert (
        bot.telegram.last("sendMessage")
        .params["text"]
        .startswith("💰 <b>Grace K</b>, you owe $5.00.")
    )


async def test_the_admin_can_link_a_guest_to_their_own_account(bot: BotHarness) -> None:
    async with bot.sessionmaker() as db:
        db.add(Member(display_name="alice (sheet)"))  # imported before the admin used the bot
        await db.commit()
    await bot.send(text_update("/members", sender=ALICE))  # the admin isn't a member yet
    await bot.send(text_update("/balance", sender=ALICE))
    card = bot.telegram.calls_to("sendMessage")[0].message

    confirm = await tap(bot, await tap(bot, card, "🔗"), "alice")
    await tap(bot, confirm, "✅ Link")

    by_name = await members(bot)
    assert set(by_name) == {"alice"}  # the admin's Telegram name
    assert by_name["alice"].telegram_user_id == ADMIN_ID
    async with bot.sessionmaker() as db:
        row = await db.scalar(select(AuditLog).where(AuditLog.action == "member.link"))
        assert row.actor_member_id == by_name["alice"].id


async def test_linking_needs_someone_on_telegram_and_an_up_to_date_card(bot: BotHarness) -> None:
    async with bot.sessionmaker() as db:
        db.add(Member(display_name="grace"))
        await db.commit()
    await bot.send(text_update("/members", sender=ALICE))
    card = bot.telegram.last("sendMessage").message

    await bot.send(callback_update(button(card, "🔗"), sender=ALICE, on_message=card))
    assert bot.telegram.last("answerCallbackQuery").params["text"] == NOBODY_TO_LINK

    await bot.send(text_update("/balance", sender=DANISH))
    confirm = await tap(bot, await tap(bot, card, "🔗"), "Grace K")
    await tap(bot, confirm, "✅ Link")
    await bot.send(callback_update(button(confirm, "✅ Link"), sender=ALICE, on_message=confirm))

    assert bot.telegram.last("answerCallbackQuery").params["text"] == LINK_GONE


async def test_back_returns_to_the_member_list(bot: BotHarness) -> None:
    card = await open_member_list(bot)

    listed = await tap(bot, await tap(bot, card, "🔗"), "‹ Back")

    assert listed["text"] == card["text"]


async def test_link_buttons_are_admin_only(bot: BotHarness) -> None:
    card = await open_member_list(bot)
    await bot.send(text_update("/balance", sender=BOB))

    await bot.send(callback_update(button(card, "🔗"), sender=BOB, on_message=card))
    guest = (await members(bot))["grace"]
    bob = (await members(bot))["bob"]
    await bot.send(callback_update(f"mem:lnk:{guest.id}:{bob.id}:ok", sender=BOB, on_message=card))

    assert [call.params["text"] for call in bot.telegram.calls_to("answerCallbackQuery")] == [
        NOT_ADMIN,
        NOT_ADMIN,
    ]
    assert (await members(bot))["grace"].telegram_user_id is None


def test_member_label_notes_the_handle_and_status() -> None:
    assert member_label(
        Member(display_name="alice", username="alice_h", telegram_user_id=1, active=True)
    ) == ("alice (@alice_h)")
    assert member_label(Member(display_name="grace", active=False)) == "grace (guest, inactive)"
    assert member_label(Member(display_name="carol", telegram_user_id=2, active=True)) == "carol"


async def played_together(bot: BotHarness, *names: str, days_ago: int = 3) -> None:
    """A session that ended ``days_ago``, booked by alice, with ``names`` marked in."""
    by_name = await members(bot)
    start = utc_now() - timedelta(days=days_ago)
    async with bot.sessionmaker() as db:
        session = Session(starts_at=start, ends_at=start + timedelta(hours=2))
        db.add(session)
        await db.flush()
        db.add(
            Booking(
                session_id=session.id,
                payer_member_id=by_name["alice"].id,
                submitted_by_member_id=by_name["alice"].id,
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
    await played_together(bot, "alice", "grace", "Grace K")

    refused = await tap(bot, await tap(bot, card, "🔗"), "Grace K")

    day = fmt_date(sgt_date(utc_now() - timedelta(days=3)))
    assert refused["text"] == (
        f"⚠️ <b>grace</b> and <b>Grace K (@danishk)</b> are both marked in for {day}. "
        "Linked, they would count once there, changing everyone's share of those sessions. "
        "Use /edit to mark one of them out first."
    )
    assert buttons(refused) == ["‹ Back"]


async def test_linking_refreshes_the_latest_invoice_card(bot: BotHarness) -> None:
    card = await open_member_list(bot)
    await bot.send(text_update("/balance", sender=DANISH))
    await played_together(bot, "alice", "Grace K")
    await bot.send(text_update("/invoice", sender=ALICE))
    invoice_card = bot.telegram.last("sendMessage").message
    assert buttons(invoice_card) == ["✅ Grace K → alice $7.40 paid"]

    confirm = await tap(bot, await tap(bot, card, "🔗"), "Grace K")
    await tap(bot, confirm, "✅ Link")

    refreshed = [
        call.message
        for call in bot.telegram.calls_to("editMessageText")
        if call.params["message_id"] == invoice_card["message_id"]
    ]
    assert len(refreshed) == 1  # edited once, by the link
    assert buttons(refreshed[0]) == ["✅ Grace K → alice $7.40 paid"]
    assert "• @danishk → @alice_handle $7.40" in refreshed[-1]["text"]


async def test_a_second_tap_on_link_waits_then_finds_it_done(bot: BotHarness) -> None:
    await bot.send(text_update("/addmember grace", sender=ALICE))
    await bot.send(text_update("/balance", sender=DANISH))
    by_name = await members(bot)
    ids = by_name["grace"].id, by_name["Grace K"].id

    async with bot.sessionmaker() as first, bot.sessionmaker() as second:
        guest, account = await _link_pair(first, *ids, lock=True)
        await link_guest(first, guest, account, now=utc_now())
        waiting = asyncio.create_task(_link_pair(second, *ids, lock=True))
        await asyncio.sleep(0.3)
        assert not waiting.done()  # held by the first tap's row locks
        await first.commit()

        assert await waiting is None  # so it answers LINK_GONE instead of linking again
