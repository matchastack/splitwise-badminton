"""Handler registration. Order matters: within a group the first matching handler wins."""

from __future__ import annotations

from telegram.ext import Application

from badminton.bot import prompts
from badminton.bot.handlers import (
    attendance,
    booking,
    booking_cancel,
    gate,
    guard,
    info,
    member_admin,
    members,
    shuttle,
)


def register_handlers(application: Application) -> None:
    gate.register(application)
    members.register(application)
    info.register(application)
    member_admin.register(application)
    booking.register(application)
    booking_cancel.register(application)
    attendance.register(application)
    shuttle.register(application)
    prompts.register(application)  # after commands: typed answers to wizard prompts
    guard.register(application)  # last: removes whatever nothing else handled
