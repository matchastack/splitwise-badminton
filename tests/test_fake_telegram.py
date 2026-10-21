"""Self-tests for the bot-flow harness, using throwaway handlers."""

import pytest
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest, Forbidden
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from tests.conftest import BotHarness
from tests.fake_telegram import (
    ADMIN_ID,
    GROUP_ID,
    callback_update,
    message,
    message_update,
    photo_message,
    text_update,
    user,
)

ZY = user(ADMIN_ID, "zy", "zy_handle")
SHAWN = user(222, "shawn")


async def reply_with_button(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Tap", callback_data="tap:1")]])
    await update.effective_message.reply_text("pong", reply_markup=keyboard)


async def on_tap(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer("tapped")
    await update.callback_query.edit_message_text(f"tapped by {update.effective_user.first_name}")


async def test_records_sent_messages_and_button_taps(bot: BotHarness) -> None:
    bot.application.add_handler(CommandHandler("ping", reply_with_button))
    bot.application.add_handler(CallbackQueryHandler(on_tap, pattern=r"^tap:"))

    await bot.send(text_update("/ping", sender=ZY))
    sent = bot.telegram.last("sendMessage")
    assert sent.params["chat_id"] == GROUP_ID
    assert bot.telegram.sent_texts(GROUP_ID) == ["pong"]
    assert sent.message["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "tap:1"

    await bot.send(callback_update("tap:1", sender=SHAWN, on_message=sent.message))
    assert bot.telegram.last("answerCallbackQuery").params["text"] == "tapped"
    edit = bot.telegram.last("editMessageText")
    assert edit.params["message_id"] == sent.message["message_id"]
    assert edit.params["text"] == "tapped by shawn"


async def test_photo_caption_command_and_replies(bot: BotHarness) -> None:
    seen: list[Update] = []

    async def record(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        seen.append(update)

    bot.application.add_handler(MessageHandler(filters.PHOTO | filters.REPLY, record))

    await bot.send(message_update(photo_message(sender=ZY, caption="/book")))
    prompt = message(sender=ZY, text="How many hours?")
    await bot.send(text_update("3", sender=SHAWN, reply_to=prompt))

    photo, reply = (update.effective_message for update in seen)
    assert photo.photo[-1].file_id == "photo-1280"
    assert photo.caption_entities[0].type == "bot_command"
    assert reply.reply_to_message.text == "How many hours?"


async def test_injected_failure_reaches_the_handler(bot: BotHarness) -> None:
    async def dm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await context.bot.send_message(chat_id=update.effective_user.id, text="hi")

    bot.application.add_handler(CommandHandler("dm", dm))
    bot.telegram.fail("sendMessage", 403, "Forbidden: bot was blocked by the user")

    with pytest.raises(Forbidden):
        await bot.send(text_update("/dm", sender=SHAWN))
    assert bot.telegram.calls_to("sendMessage")[0].result is None


async def test_unsupported_method_fails_loudly(bot: BotHarness) -> None:
    async def get_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await context.bot.get_chat(update.effective_chat.id)

    bot.application.add_handler(CommandHandler("chat", get_chat))

    with pytest.raises(BadRequest, match="does not support getChat"):
        await bot.send(text_update("/chat", sender=ZY))
