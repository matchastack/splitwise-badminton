"""Handler registration. Order matters: within a group the first matching handler wins."""

from __future__ import annotations

from telegram.ext import Application

from badminton.bot.handlers import gate, members


def register_handlers(application: Application) -> None:
    gate.register(application)
    members.register(application)
