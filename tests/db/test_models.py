from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from badminton.db.models import (
    Attendance,
    AuditLog,
    Booking,
    Draft,
    Invoice,
    InvoiceBalance,
    InvoiceLine,
    Member,
    Session,
    ShuttleBatch,
    Transfer,
)

START = datetime(2026, 10, 4, 3, 0, tzinfo=UTC)  # Sun 4 Oct, 11:00 SGT


async def add_members(db: AsyncSession) -> tuple[Member, Member]:
    zy = Member(telegram_user_id=111, display_name="zy", username="zy_handle")
    guest = Member(display_name="danish")
    db.add_all([zy, guest])
    await db.flush()
    return zy, guest


async def test_round_trip_every_model(db: AsyncSession) -> None:
    zy, guest = await add_members(db)
    session = Session(starts_at=START, ends_at=START + timedelta(hours=2))
    db.add(session)
    await db.flush()
    db.add_all(
        [
            Booking(
                session_id=session.id,
                payer_member_id=zy.id,
                submitted_by_member_id=zy.id,
                starts_at=START,
                minutes=120,
                amount_cents=1480,
                photo_file_id="photo-1",
            ),
            Attendance(session_id=session.id, member_id=zy.id, status="in"),
            Attendance(session_id=session.id, member_id=guest.id, status="out"),
            ShuttleBatch(payer_member_id=zy.id, amount_cents=7679, starts_at=START),
            Draft(
                kind="booking", chat_id=-100, user_id=111, data={"step": "date"}, expires_at=START
            ),
            AuditLog(actor_member_id=zy.id, action="test", payload={"before": 1, "after": 2}),
        ]
    )
    invoice = Invoice(period="2026-10")
    db.add(invoice)
    await db.flush()
    line = InvoiceLine(
        invoice_id=invoice.id, from_member_id=guest.id, to_member_id=zy.id, amount_cents=740
    )
    db.add_all([line, InvoiceBalance(invoice_id=invoice.id, member_id=zy.id, balance_cents=740)])
    await db.flush()
    db.add(
        Transfer(
            from_member_id=guest.id, to_member_id=zy.id, amount_cents=740, invoice_line_id=line.id
        )
    )
    await db.commit()
    db.expunge_all()

    loaded = await db.scalar(
        select(Session).options(selectinload(Session.bookings), selectinload(Session.attendance))
    )
    assert loaded is not None
    assert loaded.status == "scheduled"
    assert loaded.starts_at == START
    assert [b.amount_cents for b in loaded.bookings] == [1480]
    assert {a.member_id: a.status for a in loaded.attendance} == {zy.id: "in", guest.id: "out"}

    member = await db.scalar(select(Member).where(Member.telegram_user_id == 111))
    assert member is not None
    assert member.dm_enabled is False
    assert member.active is True
    assert member.created_at is not None

    draft = await db.scalar(select(Draft))
    assert draft is not None
    assert draft.data == {"step": "date"}

    loaded_invoice = await db.scalar(
        select(Invoice).options(selectinload(Invoice.lines), selectinload(Invoice.balances))
    )
    assert loaded_invoice is not None
    assert [x.amount_cents for x in loaded_invoice.lines] == [740]
    assert [x.balance_cents for x in loaded_invoice.balances] == [740]
    assert (await db.scalar(select(Transfer.invoice_line_id))) == line.id


@pytest.mark.parametrize(
    "bad_row",
    [
        lambda zy, guest: Transfer(from_member_id=zy.id, to_member_id=zy.id, amount_cents=100),
        lambda zy, guest: Transfer(from_member_id=zy.id, to_member_id=guest.id, amount_cents=0),
        lambda zy, guest: Session(starts_at=START, ends_at=START),
        lambda zy, guest: Session(
            starts_at=START, ends_at=START + timedelta(hours=1), status="done"
        ),
    ],
    ids=["transfer-to-self", "zero-transfer", "empty-session", "unknown-status"],
)
async def test_check_constraints(db: AsyncSession, bad_row) -> None:
    zy, guest = await add_members(db)
    db.add(bad_row(zy, guest))

    with pytest.raises(IntegrityError):
        await db.flush()


async def test_one_batch_per_start_time(db: AsyncSession) -> None:
    zy, _ = await add_members(db)
    db.add_all(
        [
            ShuttleBatch(payer_member_id=zy.id, amount_cents=8000, starts_at=START),
            ShuttleBatch(payer_member_id=zy.id, amount_cents=7679, starts_at=START),
        ]
    )

    with pytest.raises(IntegrityError):
        await db.flush()
