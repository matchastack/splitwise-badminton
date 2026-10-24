"""The command menu Telegram shows (requirements §6.1). /start and /chatid are left out:
Telegram offers Start itself, and /chatid is only needed during setup."""

from __future__ import annotations

from telegram import BotCommand

MEMBER_COMMANDS: tuple[BotCommand, ...] = (
    BotCommand("book", "Record a court booking from your screenshot"),
    BotCommand("balance", "Your balance and pending shuttle hours"),
    BotCommand("upcoming", "Upcoming sessions"),
    BotCommand("name", "Set the name I show for you"),
    BotCommand("help", "What I can do"),
)

ADMIN_COMMANDS: tuple[BotCommand, ...] = (
    *MEMBER_COMMANDS,
    BotCommand("edit", "Correct a session's attendance or bookings"),
    BotCommand("shuttle", "Record a shuttlecock purchase"),
    BotCommand("settle", "Record a payment made outside the bot"),
    BotCommand("addmember", "Add a member who isn't on Telegram"),
    BotCommand("members", "List, rename or deactivate members"),
    BotCommand("invoice", "Generate the monthly invoice now"),
)

ALLOWED_UPDATES: tuple[str, ...] = ("message", "callback_query")
