"""Members: registered automatically from Telegram users (FR-MEM-1) or added by the admin."""

from __future__ import annotations

import html
import re

from sqlalchemy import ColumnElement, and_, delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute
from telegram import User

from badminton.db.models import (
    Attendance,
    AuditLog,
    Booking,
    InvoiceBalance,
    InvoiceLine,
    Member,
    OpeningBalance,
    ShuttleBatch,
    Transfer,
)

MAX_NAME_LENGTH = 32

# Columns pointing at a member that link_guest simply repoints. Attendance and the
# stored balances are keyed by member, so it merges those row by row.
_MEMBER_COLUMNS = (
    Booking.payer_member_id,
    Booking.submitted_by_member_id,
    ShuttleBatch.payer_member_id,
    Transfer.from_member_id,
    Transfer.to_member_id,
    Transfer.recorded_by_member_id,
    InvoiceLine.from_member_id,
    InvoiceLine.to_member_id,
    AuditLog.actor_member_id,
)


async def upsert_from_telegram(db: AsyncSession, user: User) -> Member:
    """The member for a Telegram user, created on first contact. The @username is
    refreshed every time; the display name is only set on creation, so /name sticks."""
    member = await db.scalar(select(Member).where(Member.telegram_user_id == user.id))
    username = user.username[:MAX_NAME_LENGTH] if user.username else None
    if member is None:
        member = Member(
            telegram_user_id=user.id,
            display_name=clean_name(user.first_name or user.username or "Member"),
            username=username,
        )
        db.add(member)
        await db.flush()
    elif member.username != username:
        member.username = username
    return member


def clean_name(raw: str) -> str:
    """Collapse whitespace and trim to the maximum length."""
    name = re.sub(r"\s+", " ", raw).strip()
    return name[:MAX_NAME_LENGTH] or "Member"


def validate_name(raw: str) -> str:
    name = re.sub(r"\s+", " ", raw).strip()
    if not name:
        raise ValueError("Enter a name.")
    if len(name) > MAX_NAME_LENGTH:
        raise ValueError(f"Names can be at most {MAX_NAME_LENGTH} characters.")
    return name


def mention_html(member: Member) -> str:
    """How to show someone a payment is owed to (FR-MEM-6): @username, else a tappable
    name, else (guests) the plain name. Safe to embed in HTML messages."""
    if member.username:
        return f"@{html.escape(member.username)}"
    name = html.escape(member.display_name)
    if member.telegram_user_id is not None:
        return f'<a href="tg://user?id={member.telegram_user_id}">{name}</a>'
    return name


def member_label(member: Member) -> str:
    """A member as listed to the admin: ``zy (@zy_handle)``, ``danish (guest, inactive)``.
    Plain text: escape it for HTML messages."""
    notes = [f"@{member.username}"] if member.username else []
    if member.telegram_user_id is None:
        notes.append("guest")
    if not member.active:
        notes.append("inactive")
    return member.display_name + (f" ({', '.join(notes)})" if notes else "")


async def active_members(db: AsyncSession) -> list[Member]:
    """Members to offer in pickers; deactivated ones keep their history but are hidden."""
    return list(await db.scalars(select(Member).where(Member.active).order_by(Member.id)))


async def all_members(db: AsyncSession) -> list[Member]:
    return list(await db.scalars(select(Member).order_by(Member.id)))


async def link_guest(db: AsyncSession, guest: Member, account: Member) -> None:
    """Give a guest the Telegram account of ``account``: the member the bot created when
    that person first used it (FR-MEM-8).

    Everything ``account`` did moves to ``guest`` and ``account`` is deleted, so the
    linked member's balance is the two balances added together. The guest keeps its
    name. The caller commits.
    """
    if guest.telegram_user_id is not None:
        raise ValueError(f"{guest.display_name} is already linked to a Telegram account.")
    if account.telegram_user_id is None or account.id == guest.id:
        raise ValueError(f"{account.display_name} has no Telegram account to link.")
    keep, gone = guest.id, account.id

    # A payment or settle-up line between the two would become one to themselves.
    await db.execute(
        delete(Transfer).where(_between(Transfer.from_member_id, Transfer.to_member_id, keep, gone))
    )
    await db.execute(
        delete(InvoiceLine).where(
            _between(InvoiceLine.from_member_id, InvoiceLine.to_member_id, keep, gone)
        )
    )

    # One answer per session: if either record was in, the person played.
    gone_in = select(Attendance.session_id).where(
        Attendance.member_id == gone, Attendance.status == "in"
    )
    await db.execute(
        update(Attendance)
        .where(Attendance.member_id == keep, Attendance.session_id.in_(gone_in))
        .values(status="in")
    )
    kept_sessions = select(Attendance.session_id).where(Attendance.member_id == keep)
    await db.execute(
        delete(Attendance).where(
            Attendance.member_id == gone, Attendance.session_id.in_(kept_sessions)
        )
    )
    await db.execute(update(Attendance).where(Attendance.member_id == gone).values(member_id=keep))

    for column in _MEMBER_COLUMNS:
        await db.execute(update(column.class_).where(column == gone).values({column.key: keep}))

    # Stored balances: what each invoice reported, and the spreadsheet's carry-over.
    reported = await db.scalars(select(InvoiceBalance).where(InvoiceBalance.member_id == gone))
    for row in reported.all():
        mine = await db.get(InvoiceBalance, (row.invoice_id, keep))
        if mine is None:
            db.add(
                InvoiceBalance(
                    invoice_id=row.invoice_id, member_id=keep, balance_cents=row.balance_cents
                )
            )
        else:
            mine.balance_cents += row.balance_cents
        await db.delete(row)
    if (carried := await db.get(OpeningBalance, gone)) is not None:
        mine = await db.get(OpeningBalance, keep)
        total = carried.amount_cents + (mine.amount_cents if mine else 0)
        await db.delete(carried)
        if mine is None:
            db.add(
                OpeningBalance(member_id=keep, amount_cents=total, created_at=carried.created_at)
            )
        elif total:
            mine.amount_cents = total
        else:
            await db.delete(mine)
    await db.flush()  # before deleting the account those rows pointed at

    telegram_user_id, username, dm_enabled = (
        account.telegram_user_id,
        account.username,
        account.dm_enabled,
    )
    await db.delete(account)
    await db.flush()  # frees the Telegram user ID, which is unique
    guest.telegram_user_id = telegram_user_id
    guest.username = username
    guest.dm_enabled = dm_enabled
    guest.active = True
    await db.flush()


def _between(
    sender: InstrumentedAttribute[int], receiver: InstrumentedAttribute[int], one: int, other: int
) -> ColumnElement[bool]:
    return or_(
        and_(sender == one, receiver == other),
        and_(sender == other, receiver == one),
    )
