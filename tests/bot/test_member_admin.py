from sqlalchemy import func, select, update

from badminton.bot.admin import NOT_ADMIN
from badminton.db.models import AuditLog, Draft, Member
from badminton.domain.timeutil import utc_now
from badminton.services.drafts import cleanup_expired_drafts
from badminton.services.members import active_members
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
