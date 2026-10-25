"""Correcting what the bot read off the screenshot, from ✏️ Edit."""

from sqlalchemy import select

from badminton.db.models import Booking, Draft
from badminton.domain.timeutil import fmt_date
from tests.bot.booking_helpers import BOB, Wizard, book, future_day
from tests.conftest import BotHarness
from tests.fake_telegram import message, message_update

DAY = future_day(14)


async def answer(bot: BotHarness, text: str) -> str:
    """Reply to the open prompt; returns the reply's text (or the prompt it asked again)."""
    prompt = bot.telegram.last("sendMessage").message
    await bot.send(message_update(message(sender=BOB, text=text, reply_to=prompt)))
    return bot.telegram.last("sendMessage").params["text"]


async def edit(wizard: Wizard, field: str) -> None:
    await wizard.tap("✏️ Edit")
    await wizard.tap(field)


async def test_edit_menu_and_back(bot: BotHarness) -> None:
    wizard = await book(bot, BOB, DAY)

    await wizard.tap("✏️ Edit")
    assert wizard.text.endswith("💵 $14.80, paid by @bob_t\nWhat do you want to change?")
    assert set(wizard.buttons()) == {
        "📅 Date",
        "🕐 Time",
        "⏱ Hours",
        "📍 Venue",
        "🔢 Courts",
        "💵 Price",
        "‹ Back",
        "✖ Cancel",
    }
    await wizard.tap("‹ Back")
    assert wizard.text.endswith("Is this right?")


async def test_change_the_date(bot: BotHarness) -> None:
    wizard = await book(bot, BOB, DAY)
    later = future_day(15)

    await edit(wizard, "📅 Date")
    assert "‹ Back" in wizard.buttons()
    await wizard.pick_date(later)

    assert wizard.text.startswith(f"📅 {fmt_date(later)}, 11:00–13:00 (2h)\n")
    assert wizard.text.endswith("Is this right?")


async def test_change_the_time(bot: BotHarness) -> None:
    wizard = await book(bot, BOB, DAY)

    await edit(wizard, "🕐 Time")
    assert "‹ Change date" not in wizard.buttons()  # that would start the date over
    await wizard.tap("‹ Back")
    assert wizard.text.endswith("What do you want to change?")
    await wizard.tap("🕐 Time")
    await wizard.tap("18:00")

    assert wizard.text.startswith(f"📅 {fmt_date(DAY)}, 18:00–20:00 (2h)\n")


async def test_change_the_hours_updates_the_suggested_price(bot: BotHarness) -> None:
    wizard = await book(bot, BOB, DAY)

    await edit(wizard, "⏱ Hours")
    assert "‹ Change time" not in wizard.buttons()
    await wizard.tap("1h")

    assert wizard.text.startswith(f"📅 {fmt_date(DAY)}, 11:00–12:00 (1h)\n")
    assert "💵 $7.40, paid by @bob_t" in wizard.text


async def test_a_typed_price_stays_when_the_hours_change(bot: BotHarness) -> None:
    wizard = await book(bot, BOB, DAY)

    await edit(wizard, "💵 Price")
    assert "How much did you pay" in bot.telegram.last("sendMessage").params["text"]
    assert (await answer(bot, "abc")).startswith("⚠️ Enter an amount like 14.80")
    await answer(bot, "15.5")
    assert "💵 $15.50, paid by @bob_t" in wizard.text
    await edit(wizard, "⏱ Hours")
    await wizard.tap("1h")

    assert "💵 $15.50, paid by @bob_t" in wizard.text
    await wizard.tap("✅ Confirm")
    async with bot.sessionmaker() as db:
        assert (await db.scalar(select(Booking))).amount_cents == 1550


async def test_change_the_venue_and_courts(bot: BotHarness) -> None:
    wizard = await book(bot, BOB, DAY)

    await edit(wizard, "📍 Venue")
    await answer(bot, "  Bishan   Sport Hall ")
    await edit(wizard, "🔢 Courts")
    assert (await answer(bot, "Court A1B2C")).startswith("⚠️ Enter the court number")
    await answer(bot, "5 and 6")

    assert "\n📍 Bishan Sport Hall · Courts 5, 6\n" in wizard.text
    await wizard.tap("✅ Confirm")
    async with bot.sessionmaker() as db:
        booking = await db.scalar(select(Booking))
    assert (booking.venue, booking.courts) == ("Bishan Sport Hall", "5, 6")
    assert "📍 Bishan Sport Hall · Courts 5, 6" in wizard.record().text


async def test_venue_is_escaped(bot: BotHarness) -> None:
    wizard = await book(bot, BOB, DAY)

    await edit(wizard, "📍 Venue")
    await answer(bot, "Hall <b>&</b>")

    assert "📍 Hall &lt;b&gt;&amp;&lt;/b&gt; · Court 11" in wizard.text


async def test_moving_on_withdraws_an_open_prompt(bot: BotHarness) -> None:
    wizard = await book(bot, BOB, DAY)
    await edit(wizard, "📍 Venue")
    prompt = bot.telegram.last("sendMessage").message

    await wizard.tap("‹ Back")
    await bot.send(message_update(message(sender=BOB, text="Bishan", reply_to=prompt)))

    deleted = [call.params["message_id"] for call in bot.telegram.calls_to("deleteMessage")]
    assert prompt["message_id"] in deleted
    assert "Hougang Sport Hall" in wizard.text
    async with bot.sessionmaker() as db:
        draft = await db.scalar(select(Draft))
    assert draft.prompt_message_id is None
    assert "awaiting" not in draft.data
