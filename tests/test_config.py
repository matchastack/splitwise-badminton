import pytest

from badminton.config import ConfigError, Settings

BASE_ENV = {
    "BOT_TOKEN": "123:abc",
    "WEBHOOK_SECRET": "hook_secret-1",
    "DATABASE_URL": "postgresql://u:p@host-pooler/db?sslmode=require",
    "CRON_SECRET": "cron-secret",
    "ADMIN_TELEGRAM_IDS": "111, 222",
}


def env(**overrides: str) -> dict[str, str]:
    return {**BASE_ENV, **overrides}


def test_parses_required_settings_and_defaults() -> None:
    settings = Settings.from_env(env())

    assert settings.bot_token == "123:abc"
    assert settings.webhook_secret == "hook_secret-1"
    assert settings.cron_secret == "cron-secret"
    assert settings.admin_ids == frozenset({111, 222})
    assert settings.group_chat_id is None
    assert settings.reminder_days_before == 2
    assert settings.default_court_rate_cents == 740


def test_parses_optional_settings() -> None:
    settings = Settings.from_env(
        env(
            GROUP_CHAT_ID="-1001234567890", REMINDER_DAYS_BEFORE="3", DEFAULT_COURT_RATE_CENTS="800"
        )
    )

    assert settings.group_chat_id == -1001234567890
    assert settings.reminder_days_before == 3
    assert settings.default_court_rate_cents == 800


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("postgresql://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
        ("postgres://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
        ("postgresql+psycopg://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
    ],
)
def test_database_url_uses_psycopg_driver(raw: str, expected: str) -> None:
    assert Settings.from_env(env(DATABASE_URL=raw)).database_url == expected


def test_reports_every_missing_required_setting() -> None:
    with pytest.raises(ConfigError, match="BOT_TOKEN, CRON_SECRET"):
        Settings.from_env(env(BOT_TOKEN="", CRON_SECRET="  "))


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"WEBHOOK_SECRET": "has space"}, "WEBHOOK_SECRET"),
        ({"ADMIN_TELEGRAM_IDS": "111,abc"}, "ADMIN_TELEGRAM_IDS must be an integer"),
        ({"ADMIN_TELEGRAM_IDS": " , "}, "at least one Telegram user ID"),
        ({"GROUP_CHAT_ID": "group"}, "GROUP_CHAT_ID must be an integer"),
        ({"REMINDER_DAYS_BEFORE": "-1"}, "REMINDER_DAYS_BEFORE must be at least 0"),
        ({"DEFAULT_COURT_RATE_CENTS": "0"}, "DEFAULT_COURT_RATE_CENTS must be at least 1"),
    ],
)
def test_rejects_malformed_settings(overrides: dict[str, str], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        Settings.from_env(env(**overrides))


def test_is_admin() -> None:
    settings = Settings.from_env(env())

    assert settings.is_admin(111)
    assert not settings.is_admin(333)
    assert not settings.is_admin(None)


def test_repr_hides_secrets() -> None:
    text = repr(Settings.from_env(env()))

    for secret in ("123:abc", "hook_secret-1", "cron-secret", "u:p@host"):
        assert secret not in text
