from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from badminton.db.models import Draft, Member, ShuttleBatch
from badminton.services import shuttle
from badminton.services.shuttle import (
    AlreadyWaiting,
    Removal,
    ReopenRefused,
    add_waiting,
    remove_waiting,
    reopen,
)
from tests.bot.shuttle_helpers import miss_once

NOW = datetime(2026, 10, 24, 2, 0, tzinfo=UTC)


async def payer(db: AsyncSession) -> Member:
    member = Member(display_name="zy")
    db.add(member)
    await db.commit()
    return member


async def in_use(db: AsyncSession, member: Member, days_ago: int) -> ShuttleBatch:
    start = NOW - timedelta(days=days_ago)
    batch = ShuttleBatch(
        payer_member_id=member.id, amount_cents=8000, starts_at=start, opened_at=start
    )
    db.add(batch)
    await db.commit()
    return batch


async def test_a_second_waiting_purchase_is_refused_and_the_session_stays_usable(
    db: AsyncSession,
) -> None:
    member = await payer(db)
    first = await add_waiting(db, payer_id=member.id, amount_cents=7679)
    await db.commit()
    draft = Draft(kind="shuttle", chat_id=1, user_id=1, expires_at=NOW)
    db.add(draft)
    await db.commit()

    with pytest.raises(AlreadyWaiting) as refused:
        await add_waiting(db, payer_id=member.id, amount_cents=2000)

    assert refused.value.waiting.id == first.id
    # Only the savepoint was rolled back: objects loaded before are still readable,
    # and the session can carry on.
    assert draft.id is not None
    await db.delete(draft)
    await db.commit()


async def test_reopen_refuses_a_purchase_recorded_after_its_check(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    member = await payer(db)
    await in_use(db, member, days_ago=30)
    current = await in_use(db, member, days_ago=3)
    waiting = await add_waiting(db, payer_id=member.id, amount_cents=5000)
    await db.commit()
    miss_once(monkeypatch, shuttle)

    with pytest.raises(ReopenRefused, match=f"Batch #{waiting.id} is waiting"):
        await reopen(db, current)

    assert current.opened_at is not None  # still in use, and readable
    await db.commit()


@pytest.mark.parametrize("meanwhile", ["nothing", "opened", "removed"])
async def test_remove_waiting_says_what_happened(db: AsyncSession, meanwhile: str) -> None:
    member = await payer(db)
    batch = await add_waiting(db, payer_id=member.id, amount_cents=7679)
    await db.commit()
    if meanwhile == "opened":
        await db.execute(
            update(ShuttleBatch)
            .where(ShuttleBatch.id == batch.id)
            .values(starts_at=NOW, opened_at=NOW)
            .execution_options(synchronize_session=False)
        )
    elif meanwhile == "removed":
        await db.execute(
            delete(ShuttleBatch)
            .where(ShuttleBatch.id == batch.id)
            .execution_options(synchronize_session=False)
        )

    removal = await remove_waiting(db, batch)

    expected = {"nothing": Removal.REMOVED, "opened": Removal.IN_USE, "removed": Removal.GONE}
    assert removal is expected[meanwhile]
    assert batch.amount_cents == 7679  # still readable for the announcement
