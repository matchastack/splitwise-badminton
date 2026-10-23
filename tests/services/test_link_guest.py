from datetime import date, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from badminton.db.models import (
    Attendance,
    AuditLog,
    Booking,
    Invoice,
    InvoiceBalance,
    InvoiceLine,
    Member,
    OpeningBalance,
    Session,
    ShuttleBatch,
    Transfer,
)
from badminton.domain.timeutil import at_sgt
from badminton.services.members import link_guest
from badminton.services.repo import get_balances

LATER = at_sgt(date(2026, 12, 1), 9)


async def add_session(db: AsyncSession, day: int, payer: Member, answers: dict[Member, str]) -> int:
    start = at_sgt(date(2026, 11, day), 11)
    session = Session(starts_at=start, ends_at=start + timedelta(hours=2))
    db.add(session)
    await db.flush()
    db.add(
        Booking(
            session_id=session.id,
            payer_member_id=payer.id,
            submitted_by_member_id=payer.id,
            starts_at=start,
            minutes=120,
            amount_cents=1480,
        )
    )
    db.add_all(
        Attendance(session_id=session.id, member_id=member.id, status=status)
        for member, status in answers.items()
    )
    return session.id


async def people(db: AsyncSession) -> tuple[Member, Member, Member]:
    zy = Member(display_name="zy", telegram_user_id=1)
    guest = Member(display_name="yt", active=False)  # imported from the sheet
    account = Member(display_name="Yu Ting", telegram_user_id=2, username="yuting", dm_enabled=True)
    db.add_all([zy, guest, account])
    await db.flush()
    db.add(
        OpeningBalance(
            member_id=guest.id, amount_cents=-494, created_at=at_sgt(date(2026, 10, 23), 9)
        )
    )
    db.add(
        OpeningBalance(member_id=zy.id, amount_cents=494, created_at=at_sgt(date(2026, 10, 23), 9))
    )
    return zy, guest, account


async def test_linking_moves_everything_onto_the_guest(db: AsyncSession) -> None:
    zy, guest, account = await people(db)
    marked_in = await add_session(db, 1, zy, {zy: "in", guest: "in", account: "out"})
    tapped_in = await add_session(db, 8, account, {zy: "in", guest: "out", account: "in"})
    only_account = await add_session(db, 15, zy, {zy: "in", account: "in"})
    batch = ShuttleBatch(
        payer_member_id=account.id, amount_cents=7679, starts_at=at_sgt(date(2026, 11, 2), 0)
    )
    invoice = Invoice(period="2026-11", created_at=at_sgt(date(2026, 11, 20), 9))
    db.add_all([batch, invoice])
    await db.flush()
    to_zy = InvoiceLine(
        invoice_id=invoice.id, from_member_id=account.id, to_member_id=zy.id, amount_cents=200
    )
    between = InvoiceLine(
        invoice_id=invoice.id, from_member_id=guest.id, to_member_id=account.id, amount_cents=100
    )
    db.add_all([to_zy, between])
    await db.flush()
    db.add_all(
        [
            Transfer(
                from_member_id=account.id,
                to_member_id=zy.id,
                amount_cents=500,
                recorded_by_member_id=account.id,
            ),
            Transfer(
                from_member_id=guest.id,
                to_member_id=account.id,
                amount_cents=100,
                invoice_line_id=between.id,
            ),
            InvoiceBalance(invoice_id=invoice.id, member_id=guest.id, balance_cents=-494),
            InvoiceBalance(invoice_id=invoice.id, member_id=account.id, balance_cents=-100),
            InvoiceBalance(invoice_id=invoice.id, member_id=zy.id, balance_cents=594),
            AuditLog(actor_member_id=account.id, action="booking.add"),
        ]
    )
    await db.commit()
    before = await get_balances(db, LATER)

    await link_guest(db, guest, account)
    await db.commit()

    after = await get_balances(db, LATER)
    assert after[guest.id] == before[guest.id] + before[account.id]
    assert after[zy.id] == before[zy.id]
    assert account.id not in after
    assert sum(after.values()) == 0

    assert await db.get(Member, account.id) is None
    linked = await db.get(Member, guest.id)
    assert (linked.display_name, linked.telegram_user_id, linked.username) == ("yt", 2, "yuting")
    assert linked.dm_enabled and linked.active

    answers = await db.scalars(select(Attendance).where(Attendance.member_id == guest.id))
    assert {(a.session_id, a.status) for a in answers} == {
        (marked_in, "in"),
        (tapped_in, "in"),  # either record being in means they played
        (only_account, "in"),
    }
    booking = await db.scalar(select(Booking).where(Booking.session_id == tapped_in))
    assert (booking.payer_member_id, booking.submitted_by_member_id) == (guest.id, guest.id)
    assert (await db.get(ShuttleBatch, batch.id)).payer_member_id == guest.id
    transfers = await db.scalars(select(Transfer))
    assert [(t.from_member_id, t.to_member_id, t.recorded_by_member_id) for t in transfers] == [
        (guest.id, zy.id, guest.id)
    ]
    lines = await db.scalars(select(InvoiceLine))
    assert [(line.id, line.from_member_id) for line in lines] == [(to_zy.id, guest.id)]
    reported = await db.scalars(select(InvoiceBalance).order_by(InvoiceBalance.member_id))
    assert [(row.member_id, row.balance_cents) for row in reported] == [
        (zy.id, 594),
        (guest.id, -594),
    ]
    assert await db.scalar(select(AuditLog.actor_member_id)) == guest.id
    assert (await db.get(OpeningBalance, guest.id)).amount_cents == -494


async def test_a_session_both_records_played_counts_them_once(db: AsyncSession) -> None:
    zy, guest, account = await people(db)
    session_id = await add_session(db, 1, zy, {zy: "in", guest: "in", account: "in"})
    await db.commit()

    await link_guest(db, guest, account)
    await db.commit()

    rows = await db.scalars(select(Attendance).where(Attendance.session_id == session_id))
    assert {(row.member_id, row.status) for row in rows} == {(zy.id, "in"), (guest.id, "in")}
    balances = await get_balances(db, LATER)
    assert balances == {zy.id: 494 + 1480 - 740, guest.id: -494 - 740}


async def test_an_account_opening_balance_is_added_to_the_guests(db: AsyncSession) -> None:
    zy, guest, account = await people(db)
    zy_opening = await db.get(OpeningBalance, zy.id)
    zy_opening.amount_cents = 94  # so that the three still sum to zero
    db.add(OpeningBalance(member_id=account.id, amount_cents=400, created_at=zy_opening.created_at))
    await db.commit()

    await link_guest(db, guest, account)
    await db.commit()

    assert (await db.get(OpeningBalance, guest.id)).amount_cents == -94
    assert await db.get(OpeningBalance, account.id) is None


async def test_only_a_guest_can_be_linked_to_a_telegram_account(db: AsyncSession) -> None:
    zy, guest, account = await people(db)
    await db.commit()

    with pytest.raises(ValueError, match="zy is already linked"):
        await link_guest(db, zy, account)
    with pytest.raises(ValueError, match="yt has no Telegram account"):
        await link_guest(db, guest, guest)
