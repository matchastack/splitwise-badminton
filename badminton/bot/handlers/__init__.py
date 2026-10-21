"""Handler registration. Order matters: within a group the first matching handler wins."""

from __future__ import annotations

from telegram.ext import Application

from badminton.bot import prompts
from badminton.bot.handlers import gate, guard, members


def register_handlers(application: Application) -> None:
    gate.register(application)
    members.register(application)
    prompts.register(application)  # after commands: typed answers to wizard prompts
    guard.register(application)  # last: removes whatever nothing else handled
