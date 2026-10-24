from datetime import date, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from badminton.db.models import (
    Attendance,
    AuditLog,
    Base,
    Booking,
    Draft,
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
from badminton.services.members import _MEMBER_COLUMNS, LinkRefused, link_guest
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
    alice = Member(display_name="alice", telegram_user_id=1)
    guest = Member(display_name="frank", active=False)  # imported from the sheet
    account = Member(
        display_name="Frank Lee", telegram_user_id=2, username="frank_lee", dm_enabled=True
    )
    db.add_all([alice, guest, account])
    await db.flush()
    db.add(
        OpeningBalance(
            member_id=guest.id, amount_cents=-494, created_at=at_sgt(date(2026, 10, 23), 9)
        )
    )
    db.add(
        OpeningBalance(
            member_id=alice.id, amount_cents=494, created_at=at_sgt(date(2026, 10, 23), 9)
        )
    )
    return alice, guest, account


async def test_linking_moves_everything_onto_the_guest(db: AsyncSession) -> None:
    alice, guest, account = await people(db)
    marked_in = await add_session(db, 1, alice, {alice: "in", guest: "in", account: "out"})
    tapped_in = await add_session(db, 8, account, {alice: "in", guest: "out", account: "in"})
    only_account = await add_session(db, 15, alice, {alice: "in", account: "in"})
    batch = ShuttleBatch(
        payer_member_id=account.id, amount_cents=7679, starts_at=at_sgt(date(2026, 11, 2), 0)
    )
    invoice = Invoice(period="2026-11", created_at=at_sgt(date(2026, 11, 20), 9))
    db.add_all([batch, invoice])
    await db.flush()
    to_zy = InvoiceLine(
        invoice_id=invoice.id, from_member_id=account.id, to_member_id=alice.id, amount_cents=200
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
                to_member_id=alice.id,
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
            InvoiceBalance(invoice_id=invoice.id, member_id=alice.id, balance_cents=594),
            AuditLog(actor_member_id=account.id, action="booking.add"),
        ]
    )
    await db.commit()
    before = await get_balances(db, LATER)

    await link_guest(db, guest, account, now=LATER)
    await db.commit()

    after = await get_balances(db, LATER)
    assert after[guest.id] == before[guest.id] + before[account.id]
    assert after[alice.id] == before[alice.id]
    assert account.id not in after
    assert sum(after.values()) == 0

    assert await db.get(Member, account.id) is None
    linked = await db.get(Member, guest.id)
    # The guest's record, under the account's name, as for anyone who joined normally.
    assert (linked.display_name, linked.telegram_user_id, linked.username) == (
        "Frank Lee",
        2,
        "frank_lee",
    )
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
        (guest.id, alice.id, guest.id)
    ]
    lines = await db.scalars(select(InvoiceLine))
    assert [(line.id, line.from_member_id) for line in lines] == [(to_zy.id, guest.id)]
    reported = await db.scalars(select(InvoiceBalance).order_by(InvoiceBalance.member_id))
    assert [(row.member_id, row.balance_cents) for row in reported] == [
        (alice.id, 594),
        (guest.id, -594),
    ]
    assert await db.scalar(select(AuditLog.actor_member_id)) == guest.id
    assert (await db.get(OpeningBalance, guest.id)).amount_cents == -494


async def test_refuses_while_both_records_played_the_same_session(db: AsyncSession) -> None:
    alice, guest, account = await people(db)
    session_id = await add_session(db, 1, alice, {alice: "in", guest: "in", account: "in"})
    await db.commit()
    account_id = account.id
    before = await get_balances(db, LATER)

    with pytest.raises(
        LinkRefused, match=r"frank and Frank Lee are both marked in for Sun 1 Nov\."
    ):
        await link_guest(db, guest, account, now=LATER)
    await db.rollback()

    # Counting frank once would re-split that session for alice too, so nothing changed.
    assert await db.get(Member, account_id) is not None
    rows = await db.scalars(select(Attendance).where(Attendance.session_id == session_id))
    assert len(rows.all()) == 3
    assert await get_balances(db, LATER) == before


async def test_a_session_still_to_come_counts_them_once(db: AsyncSession) -> None:
    alice, guest, account = await people(db)
    session_id = await add_session(db, 1, alice, {alice: "in", guest: "in", account: "in"})
    await db.commit()
    before_it = at_sgt(date(2026, 11, 1), 9)
    before = await get_balances(db, before_it)

    await link_guest(db, guest, account, now=before_it)
    await db.commit()

    rows = await db.scalars(select(Attendance).where(Attendance.session_id == session_id))
    assert {(row.member_id, row.status) for row in rows} == {(alice.id, "in"), (guest.id, "in")}
    assert await get_balances(db, before_it) == before  # nothing charged yet changes


async def test_wizards_open_on_the_account_expire(db: AsyncSession) -> None:
    alice, guest, account = await people(db)
    expires = at_sgt(date(2026, 12, 31), 0)

    def draft(kind: str, **data: object) -> Draft:
        return Draft(kind=kind, chat_id=1, user_id=1, expires_at=expires, data=data)

    db.add_all(
        [
            draft("booking", payer_member_id=account.id),
            draft("settle", from_id=guest.id, to_id=account.id),
            draft(
                "edit",
                session_id=account.id + 100,
                ins=[alice.id, account.id],
                shown_ins=[alice.id],
            ),
            draft("member_rename", member_id=alice.id),
            draft("edit", session_id=account.id, ins=[alice.id]),  # a session ID, not a member
        ]
    )
    await db.commit()

    await link_guest(db, guest, account, now=LATER)
    await db.commit()

    left = await db.scalars(select(Draft).order_by(Draft.id))
    assert [(d.kind, d.data) for d in left] == [
        ("member_rename", {"member_id": alice.id}),
        ("edit", {"session_id": account.id, "ins": [alice.id]}),
    ]


def test_every_reference_to_a_member_is_moved_or_merged() -> None:
    references = {
        (fk.parent.table.name, fk.parent.name)
        for table in Base.metadata.tables.values()
        for fk in table.foreign_keys
        if fk.column.table.name == Member.__tablename__
    }
    repointed = {(c.property.columns[0].table.name, c.key) for c in _MEMBER_COLUMNS}
    merged_row_by_row = {
        ("attendance", "member_id"),
        ("invoice_balances", "member_id"),
        ("opening_balances", "member_id"),
    }

    # A new column pointing at members must be added to link_guest, or the account
    # can't be deleted after linking.
    assert references == repointed | merged_row_by_row


async def test_an_account_opening_balance_is_added_to_the_guests(db: AsyncSession) -> None:
    alice, guest, account = await people(db)
    alice_opening = await db.get(OpeningBalance, alice.id)
    alice_opening.amount_cents = 94  # so that the three still sum to zero
    db.add(
        OpeningBalance(member_id=account.id, amount_cents=400, created_at=alice_opening.created_at)
    )
    await db.commit()

    await link_guest(db, guest, account, now=LATER)
    await db.commit()

    assert (await db.get(OpeningBalance, guest.id)).amount_cents == -94
    assert await db.get(OpeningBalance, account.id) is None


async def test_only_a_guest_can_be_linked_to_a_telegram_account(db: AsyncSession) -> None:
    alice, guest, account = await people(db)
    await db.commit()

    with pytest.raises(ValueError, match="alice is already linked"):
        await link_guest(db, alice, account, now=LATER)
    with pytest.raises(ValueError, match="frank has no Telegram account"):
        await link_guest(db, guest, guest, now=LATER)
