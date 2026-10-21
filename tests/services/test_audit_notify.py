import dataclasses
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from badminton.db.models import AuditLog, Member
from badminton.services import audit
from badminton.services.notify import announce, dm, dm_admins
from tests.conftest import TEST_SETTINGS, BotHarness
from tests.fake_telegram import ADMIN_ID, GROUP_ID


async def test_audit_record_is_saved_with_json_payload(db: AsyncSession) -> None:
    zy = Member(display_name="zy")
    db.add(zy)
    await db.flush()

    audit.record(
        db,
        actor_member_id=zy.id,
        action="attendance.edit",
        before={"in": {3, 1}},
        after={"in": [1, 2]},
        session_id=5,
        at=datetime(2026, 10, 4, 3, tzinfo=UTC),
    )
    await db.commit()

    row = await db.scalar(select(AuditLog))
    assert row.actor_member_id == zy.id
    assert row.action == "attendance.edit"
    assert row.payload == {
        "before": {"in": [1, 3]},
        "after": {"in": [1, 2]},
        "session_id": 5,
        "at": "2026-10-04T03:00:00+00:00",
    }
    assert row.created_at is not None


async def test_announce_posts_to_the_group(bot: BotHarness) -> None:
    sent = await announce(bot.application.bot, TEST_SETTINGS, "hello group")

    assert sent is not None
    assert bot.telegram.last("sendMessage").params["chat_id"] == GROUP_ID


async def test_announce_without_group_is_skipped(bot: BotHarness) -> None:
    settings = dataclasses.replace(TEST_SETTINGS, group_chat_id=None)

    assert await announce(bot.application.bot, settings, "hello") is None
    assert bot.telegram.calls == []


async def test_dm_sends_only_to_members_with_dms_on(bot: BotHarness) -> None:
    on = Member(display_name="on", telegram_user_id=301, dm_enabled=True)
    off = Member(display_name="off", telegram_user_id=302, dm_enabled=False)
    guest = Member(display_name="guest", dm_enabled=True)

    results = [await dm(bot.application.bot, member, "hi") for member in (on, off, guest)]

    assert results == [True, False, False]
    assert [call.params["chat_id"] for call in bot.telegram.calls_to("sendMessage")] == [301]


async def test_dm_turns_dms_off_when_blocked(bot: BotHarness) -> None:
    member = Member(display_name="blocker", telegram_user_id=303, dm_enabled=True)
    bot.telegram.fail("sendMessage", 403, "Forbidden: bot was blocked by the user")

    assert await dm(bot.application.bot, member, "hi") is False
    assert member.dm_enabled is False


async def test_dm_admins_skips_admins_who_cannot_be_reached(bot: BotHarness) -> None:
    settings = dataclasses.replace(TEST_SETTINGS, admin_ids=frozenset({ADMIN_ID, 112}))
    bot.telegram.fail("sendMessage", 403, "Forbidden: bot can't initiate conversation with a user")

    await dm_admins(bot.application.bot, settings, "heads up")

    attempted = [call.params["chat_id"] for call in bot.telegram.calls_to("sendMessage")]
    assert attempted == [ADMIN_ID, 112]
    assert bot.telegram.calls_to("sendMessage")[1].result is not None
