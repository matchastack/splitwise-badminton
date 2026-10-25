"""Handler registration. Order matters: within a group the first matching handler wins."""

from __future__ import annotations

from telegram.ext import Application

from badminton.bot import prompts
from badminton.bot.handlers import (
    attendance,
    booking,
    booking_cancel,
    edit,
    gate,
    info,
    invoice,
    member_admin,
    members,
    settle,
    shuttle,
    shuttle_edit,
    shuttle_finish,
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
    shuttle_finish.register(application)
    shuttle_edit.register(application)
    invoice.register(application)
    edit.register(application)
    settle.register(application)
    prompts.register(application)  # after commands: typed answers to wizard prompts
