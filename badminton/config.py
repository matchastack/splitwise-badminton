"""Runtime settings loaded from environment variables (requirements §6.3)."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import lru_cache

_WEBHOOK_SECRET_RE = re.compile(r"^[A-Za-z0-9_-]{1,256}$")
_DB_SCHEMES = ("postgres://", "postgresql://")
_REQUIRED = ("BOT_TOKEN", "WEBHOOK_SECRET", "DATABASE_URL", "CRON_SECRET", "ADMIN_TELEGRAM_IDS")


class ConfigError(RuntimeError):
    """A required setting is missing or malformed."""


@dataclass(frozen=True, slots=True)
class Settings:
    bot_token: str = field(repr=False)
    webhook_secret: str = field(repr=False)
    database_url: str = field(repr=False)
    cron_secret: str = field(repr=False)
    admin_ids: frozenset[int]
    group_chat_id: int | None = None
    reminder_days_before: int = 2
    default_court_rate_cents: int = 740

    def is_admin(self, user_id: int | None) -> bool:
        return user_id is not None and user_id in self.admin_ids

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        values = {name: env.get(name, "").strip() for name in _REQUIRED}
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise ConfigError(f"Missing required settings: {', '.join(missing)}")

        webhook_secret = values["WEBHOOK_SECRET"]
        if not _WEBHOOK_SECRET_RE.fullmatch(webhook_secret):
            raise ConfigError("WEBHOOK_SECRET must be 1-256 characters of A-Z, a-z, 0-9, _ or -")

        group_chat_id = env.get("GROUP_CHAT_ID", "").strip()
        return cls(
            bot_token=values["BOT_TOKEN"],
            webhook_secret=webhook_secret,
            database_url=normalise_database_url(values["DATABASE_URL"]),
            cron_secret=values["CRON_SECRET"],
            admin_ids=_parse_ids("ADMIN_TELEGRAM_IDS", values["ADMIN_TELEGRAM_IDS"]),
            group_chat_id=_parse_int("GROUP_CHAT_ID", group_chat_id) if group_chat_id else None,
            reminder_days_before=_parse_int(
                "REMINDER_DAYS_BEFORE", env.get("REMINDER_DAYS_BEFORE", "2"), minimum=0
            ),
            default_court_rate_cents=_parse_int(
                "DEFAULT_COURT_RATE_CENTS", env.get("DEFAULT_COURT_RATE_CENTS", "740"), minimum=1
            ),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Settings for the running process, read from the environment once."""
    return Settings.from_env()


def _parse_int(name: str, raw: str, *, minimum: int | None = None) -> int:
    try:
        value = int(raw.strip())
    except ValueError:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from None
    if minimum is not None and value < minimum:
        raise ConfigError(f"{name} must be at least {minimum}, got {value}")
    return value


def _parse_ids(name: str, raw: str) -> frozenset[int]:
    ids = frozenset(_parse_int(name, part) for part in raw.split(",") if part.strip())
    if not ids:
        raise ConfigError(f"{name} must list at least one Telegram user ID")
    return ids


def normalise_database_url(url: str) -> str:
    """Accept the plain URL Neon shows and select the psycopg 3 driver."""
    for scheme in _DB_SCHEMES:
        if url.startswith(scheme):
            return "postgresql+psycopg://" + url.removeprefix(scheme)
    return url
