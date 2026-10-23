from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from badminton.bot.admin import NOT_ADMIN, admin_only
from tests.conftest import BotHarness
from tests.fake_telegram import ADMIN_ID, callback_update, message, text_update, user

ALICE = user(ADMIN_ID, "alice")
BOB = user(222, "bob")


@admin_only
async def secret(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text("done")


@admin_only
async def secret_button(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer("done")


async def test_admin_command_runs_for_admin_only(bot: BotHarness) -> None:
    bot.application.add_handler(CommandHandler("secret", secret), group=-1)

    await bot.send(text_update("/secret", sender=ALICE))
    await bot.send(text_update("/secret", sender=BOB))

    assert bot.telegram.sent_texts() == ["done", NOT_ADMIN]


async def test_admin_button_alerts_non_admin(bot: BotHarness) -> None:
    bot.application.add_handler(CallbackQueryHandler(secret_button, pattern="^secret$"), group=-1)
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("x", callback_data="secret")]])
    card = message(sender=ALICE, text="card", reply_markup=keyboard.to_dict())

    await bot.send(callback_update("secret", sender=BOB, on_message=card))
    await bot.send(callback_update("secret", sender=ALICE, on_message=card))

    refused, allowed = (call.params for call in bot.telegram.calls_to("answerCallbackQuery"))
    assert refused == {
        "callback_query_id": refused["callback_query_id"],
        "text": NOT_ADMIN,
        "show_alert": True,
    }
    assert allowed["text"] == "done"
