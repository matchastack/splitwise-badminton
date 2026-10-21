"""Handler registration. Order matters: within a group the first matching handler wins."""

from __future__ import annotations

from telegram.ext import Application

from badminton.bot import prompts
from badminton.bot.handlers import booking, booking_cancel, gate, guard, member_admin, members


def register_handlers(application: Application) -> None:
    gate.register(application)
    members.register(application)
    member_admin.register(application)
    booking.register(application)
    booking_cancel.register(application)
    prompts.register(application)  # after commands: typed answers to wizard prompts
    guard.register(application)  # last: removes whatever nothing else handled
