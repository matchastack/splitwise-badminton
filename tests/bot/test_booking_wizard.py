from sqlalchemy import func, select

from badminton.bot.handlers.booking import EXPIRED
from badminton.db.models import Draft, Member
from badminton.domain.timeutil import fmt_date
from tests.bot.booking_helpers import CAROL, BOB, Wizard, future_day
from tests.conftest import BotHarness
from tests.fake_telegram import GROUP_ID, callback_update, message, text_update


async def the_draft(bot: BotHarness) -> Draft:
    async with bot.sessionmaker() as db:
        return await db.scalar(select(Draft))


async def test_book_photo_opens_wizard_with_sender_as_payer(bot: BotHarness) -> None:
    wizard = await Wizard(bot, BOB).start()

    sent = bot.telegram.last("sendMessage").params
    assert sent["chat_id"] == GROUP_ID
    assert sent["reply_parameters"]["message_id"] < wizard.card["message_id"]
    assert wizard.text.startswith("🏸 <b>New court booking</b> by @bob_t")
    assert "Which day" in wizard.text
    assert "✖ Cancel" in wizard.buttons()
    draft = await the_draft(bot)
    async with bot.sessionmaker() as db:
        payer = await db.get(Member, draft.data["payer_member_id"])
    assert payer.telegram_user_id == BOB["id"]
    assert draft.data["photo_file_id"] == "photo-1280"
    assert draft.message_id == wizard.card["message_id"]


async def test_pick_date_time_and_duration(bot: BotHarness) -> None:
    day = future_day(40)  # usually next month, so navigation is exercised
    wizard = await Wizard(bot, BOB).start()

    await wizard.pick_date(day)
    assert f"📅 {fmt_date(day)}" in wizard.text
    assert "What time" in wizard.text

    await wizard.tap("11:00")
    assert f"📅 {fmt_date(day)}, 11:00" in wizard.text

    await wizard.tap("2h")
    assert f"📅 {fmt_date(day)}, 11:00–13:00 (2h)" in wizard.text
    assert (await the_draft(bot)).data.items() >= {
        "hour": 11,
        "minutes": 120,
        "step": "price",
    }.items()


async def test_back_buttons(bot: BotHarness) -> None:
    wizard = await Wizard(bot, BOB).start()
    await wizard.pick_date(future_day())
    await wizard.tap("09:00")

    await wizard.tap("‹ Change time")
    assert "What time" in wizard.text
    await wizard.tap("‹ Change date")
    assert "Which day" in wizard.text
    assert "hour" not in (await the_draft(bot)).data


async def test_other_duration_through_a_reply(bot: BotHarness) -> None:
    wizard = await Wizard(bot, BOB).start()
    await wizard.pick_date(future_day())
    await wizard.tap("18:00")

    await wizard.tap("Other…")
    prompt = bot.telegram.last("sendMessage")
    assert "how many hours" in prompt.params["text"]
    await bot.send(
        {"update_id": 1, "message": message(sender=BOB, text="7", reply_to=prompt.message)}
    )
    reprompt = bot.telegram.last("sendMessage")
    assert reprompt.params["text"].startswith("⚠️ Enter between 0.5 and 6 hours")
    await bot.send(
        {"update_id": 2, "message": message(sender=BOB, text="1.5", reply_to=reprompt.message)}
    )

    assert "18:00–19:30 (1.5h)" in wizard.text
    assert (await the_draft(bot)).data["minutes"] == 90


async def test_only_the_submitter_can_fill_it_in(bot: BotHarness) -> None:
    wizard = await Wizard(bot, BOB).start()
    bot.telegram.clear()

    await wizard.tap("›", sender=CAROL)

    assert wizard.last_alert() == "Only bob can fill this in."
    assert bot.telegram.calls_to("editMessageText") == []


async def test_past_dates_are_refused(bot: BotHarness) -> None:
    wizard = await Wizard(bot, BOB).start()
    draft = await the_draft(bot)

    await bot.send(
        callback_update(f"bk:{draft.id}:d:20200101", sender=BOB, on_message=wizard.card)
    )

    assert wizard.last_alert() == "That date has passed."
    assert "day" not in (await the_draft(bot)).data


async def test_expired_or_missing_draft(bot: BotHarness) -> None:
    wizard = await Wizard(bot, BOB).start()

    await bot.send(callback_update("bk:999999:d:20991231", sender=BOB, on_message=wizard.card))

    assert wizard.last_alert() == EXPIRED


async def test_cancel(bot: BotHarness) -> None:
    wizard = await Wizard(bot, BOB).start()

    await wizard.tap("✖ Cancel")

    assert wizard.text == "✖ Booking cancelled."
    async with bot.sessionmaker() as db:
        assert await db.scalar(select(func.count()).select_from(Draft)) == 0


async def test_book_without_a_photo_explains_how(bot: BotHarness) -> None:
    await bot.send(text_update("/book", sender=BOB))

    assert "caption /book" in bot.telegram.sent_texts(GROUP_ID)[0]
    assert bot.telegram.calls_to("deleteMessage") == []
