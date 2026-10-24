from pathlib import Path

from sqlalchemy import func, select

from badminton.bot.context import OCR_KEY
from badminton.bot.handlers.booking import EXPIRED, NOT_READ, UNREAD
from badminton.db.models import Draft, Member
from badminton.domain.timeutil import fmt_date
from badminton.services.ocr import read_text
from tests.bot.booking_helpers import BOB, CAROL, Wizard, activesg_text, future_day
from tests.conftest import BotHarness
from tests.fake_telegram import (
    GROUP_ID,
    callback_update,
    message,
    message_update,
    photo_message,
    text_update,
)

SCREENSHOT = Path(__file__).resolve().parents[1] / "fixtures" / "activesg_booking.jpg"


async def the_draft(bot: BotHarness) -> Draft:
    async with bot.sessionmaker() as db:
        return await db.scalar(select(Draft))


def deleted(bot: BotHarness) -> list[int]:
    return [call.params["message_id"] for call in bot.telegram.calls_to("deleteMessage")]


async def test_book_asks_for_the_screenshot(bot: BotHarness) -> None:
    await bot.send(text_update("/book", sender=BOB))

    prompt = bot.telegram.last("sendMessage")
    assert prompt.params["chat_id"] == GROUP_ID
    assert prompt.params["text"].endswith(
        ">bob</a>, send your booking screenshot as a reply to this message."
    )
    assert prompt.params["reply_markup"]["force_reply"] is True
    draft = await the_draft(bot)
    assert (draft.prompt_message_id, draft.message_id) == (prompt.message["message_id"], None)
    assert draft.data["awaiting"] == "photo"


async def test_screenshot_reply_shows_the_booking_to_confirm(bot: BotHarness) -> None:
    day = future_day(14)
    wizard = await Wizard(bot, BOB, activesg_text(day)).start()

    assert wizard.text == (
        f"📅 {fmt_date(day)}, 11:00–13:00 (2h)\n"
        "📍 Hougang Sport Hall · Court 11\n"
        "💵 $14.80, paid by @bob_t\n"
        "Is this right?"
    )
    assert set(wizard.buttons()) == {"✅ Confirm", "✏️ Edit", "✖ Cancel"}
    assert bot.telegram.last("getFile").params["file_id"] == wizard.photo["photo"][-1]["file_id"]
    card = bot.telegram.last("sendMessage").params
    assert card["reply_parameters"]["message_id"] == wizard.photo["message_id"]
    assert wizard.prompt["message_id"] in deleted(bot)  # answered
    assert wizard.photo["message_id"] not in deleted(bot)  # kept to check against
    draft = await the_draft(bot)
    assert draft.message_id == wizard.card["message_id"]
    assert draft.prompt_message_id is None
    assert (
        draft.data.items()
        >= {
            "photo_file_id": wizard.photo["photo"][-1]["file_id"],
            "photo_message_id": wizard.photo["message_id"],
            "venue": "Hougang Sport Hall",
            "courts": "11",
            "hour": 11,
            "minutes": 120,
            "suggested_cents": 1480,
            "step": "confirm",
        }.items()
    )
    async with bot.sessionmaker() as db:
        payer = await db.get(Member, draft.data["payer_member_id"])
    assert payer.telegram_user_id == BOB["id"]


async def test_screenshot_captioned_book_skips_the_prompt(bot: BotHarness) -> None:
    wizard = await Wizard(bot, BOB, activesg_text(future_day())).start(caption=True)

    assert "📍 Hougang Sport Hall · Court 11" in wizard.text
    assert [call.params["text"] for call in bot.telegram.calls_to("sendMessage")] == [wizard.text]


async def test_reads_a_real_screenshot(bot: BotHarness) -> None:
    bot.application.bot_data[OCR_KEY] = read_text
    bot.telegram.files["real-1280"] = SCREENSHOT.read_bytes()

    await bot.send(message_update(photo_message(sender=BOB, caption="/book", file_id="real")))

    card = bot.telegram.last("sendMessage").params["text"]
    assert ", 11:00–13:00 (2h)\n📍 Hougang Sport Hall · Court 11\n💵 $14.80" in card


async def test_typed_answer_to_the_screenshot_prompt_is_asked_again(bot: BotHarness) -> None:
    await bot.send(text_update("/book", sender=BOB))
    prompt = bot.telegram.last("sendMessage").message

    await bot.send(message_update(message(sender=BOB, text="Sunday 11am", reply_to=prompt)))

    assert (
        bot.telegram.last("sendMessage")
        .params["text"]
        .startswith("⚠️ Send your booking screenshot as a photo.\n")
    )
    assert (await the_draft(bot)).data["awaiting"] == "photo"


async def test_image_sent_as_a_file_is_asked_again(bot: BotHarness) -> None:
    await bot.send(text_update("/book", sender=BOB))
    prompt = bot.telegram.last("sendMessage").message
    document = {"file_id": "doc", "file_unique_id": "d", "mime_type": "image/jpeg"}
    sent = message(sender=BOB, reply_to=prompt, document=document)

    await bot.send(message_update(sent))

    assert (
        bot.telegram.last("sendMessage")
        .params["text"]
        .startswith("⚠️ Send it as a photo, not as a file.\n")
    )
    assert sent["message_id"] in deleted(bot)
    assert (await the_draft(bot)).data["awaiting"] == "photo"


async def test_someone_elses_screenshot_is_removed(bot: BotHarness) -> None:
    await bot.send(text_update("/book", sender=BOB))
    prompt = bot.telegram.last("sendMessage").message
    stray = photo_message(sender=CAROL, reply_to=prompt)

    await bot.send(message_update(stray))

    assert stray["message_id"] in deleted(bot)
    assert (await the_draft(bot)).data["awaiting"] == "photo"
    assert bot.telegram.calls_to("getFile") == []


async def test_unreadable_screenshot_asks_for_the_date_time_and_hours(bot: BotHarness) -> None:
    day = future_day(40)  # usually next month, so navigation is exercised
    wizard = await Wizard(bot, BOB).start()
    assert wizard.text == f"{UNREAD}\nWhich day is the court booked for?"

    await wizard.pick_date(day)
    assert f"📅 {fmt_date(day)}\nWhat time" in wizard.text

    await wizard.tap("11:00")
    assert f"📅 {fmt_date(day)}, 11:00\nHow long" in wizard.text

    await wizard.tap("2h")
    assert wizard.text == (
        f"📅 {fmt_date(day)}, 11:00–13:00 (2h)\n{NOT_READ}\n💵 $14.80, paid by @bob_t\n"
        "Is this right?"
    )
    assert (await the_draft(bot)).data.items() >= {
        "hour": 11,
        "minutes": 120,
        "step": "confirm",
    }.items()


async def test_venue_is_kept_when_only_the_time_is_unreadable(bot: BotHarness) -> None:
    wizard = await Wizard(bot, BOB, "Where Bishan Sport Hall\nWhen ???\nCourt 4").start()

    assert wizard.text == (f"{UNREAD}\n📍 Bishan Sport Hall\nWhich day is the court booked for?")


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
    await bot.send(message_update(message(sender=BOB, text="7", reply_to=prompt.message)))
    reprompt = bot.telegram.last("sendMessage")
    assert reprompt.params["text"].startswith("⚠️ Enter between 0.5 and 6 hours")
    await bot.send(message_update(message(sender=BOB, text="1.5", reply_to=reprompt.message)))

    assert "18:00–19:30 (1.5h)" in wizard.text
    assert "💵 $11.10" in wizard.text
    assert (await the_draft(bot)).data["minutes"] == 90


async def test_only_the_submitter_can_fill_it_in(bot: BotHarness) -> None:
    wizard = await Wizard(bot, BOB, activesg_text(future_day())).start()
    bot.telegram.clear()

    await wizard.tap("✏️ Edit", sender=CAROL)

    assert wizard.last_alert() == "Only bob can fill this in."
    assert bot.telegram.calls_to("editMessageText") == []


async def test_past_dates_are_refused(bot: BotHarness) -> None:
    wizard = await Wizard(bot, BOB).start()
    draft = await the_draft(bot)

    await bot.send(callback_update(f"bk:{draft.id}:d:20200101", sender=BOB, on_message=wizard.card))

    assert wizard.last_alert() == "That date has passed."
    assert "day" not in (await the_draft(bot)).data


async def test_expired_or_missing_draft(bot: BotHarness) -> None:
    wizard = await Wizard(bot, BOB).start()

    await bot.send(callback_update("bk:999999:d:20991231", sender=BOB, on_message=wizard.card))

    assert wizard.last_alert() == EXPIRED


async def test_cancel_removes_the_screenshot(bot: BotHarness) -> None:
    wizard = await Wizard(bot, BOB, activesg_text(future_day())).start()

    await wizard.tap("✖ Cancel")

    assert wizard.text == "✖ Booking cancelled."
    assert wizard.photo["message_id"] in deleted(bot)
    async with bot.sessionmaker() as db:
        assert await db.scalar(select(func.count()).select_from(Draft)) == 0
