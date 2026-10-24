"""Members: registered automatically from Telegram users (FR-MEM-1) or added by the admin."""

from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, Select, and_, delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute
from telegram import User

from badminton.db.models import (
    Attendance,
    AuditLog,
    Booking,
    Draft,
    InvoiceBalance,
    InvoiceLine,
    Member,
    OpeningBalance,
    Session,
    ShuttleBatch,
    Transfer,
)
from badminton.domain.timeutil import fmt_date, sgt_date

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
# Where the wizards keep member IDs in a draft's state: /court's payer, a /members
# rename, /settle's two sides, /shuttle's payer, and /edit's attendance toggles.
_DRAFT_MEMBER_FIELDS = ("payer_member_id", "member_id", "from_id", "to_id", "payer_id")
_DRAFT_MEMBER_LISTS = ("ins", "shown_ins")


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
    """A member as listed to the admin: ``alice (@alice_handle)``, ``grace (guest, inactive)``.
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


class LinkRefused(ValueError):
    """The guest and the account can't be linked as they are. Nothing was changed."""


async def sessions_both_played(
    db: AsyncSession, guest: Member, account: Member, now: datetime
) -> list[Session]:
    """Ended sessions both records are marked in for. Linking would count the person
    once there, re-splitting a cost already in everyone's balance."""

    def played(member: Member) -> Select[tuple[int]]:
        return select(Attendance.session_id).where(
            Attendance.member_id == member.id, Attendance.status == "in"
        )

    return list(
        await db.scalars(
            select(Session)
            .where(
                Session.status == "scheduled",
                Session.ends_at <= now,
                Session.id.in_(played(guest)),
                Session.id.in_(played(account)),
            )
            .order_by(Session.starts_at)
        )
    )


async def link_guest(db: AsyncSession, guest: Member, account: Member, *, now: datetime) -> None:
    """Give a guest the Telegram account of ``account``: the member the bot created when
    that person first used it (FR-MEM-8).

    Everything ``account`` did moves to ``guest`` and ``account`` is deleted. Nobody
    else's figures change, so the linked member's balance is the two balances added
    together. It takes the account's name, as for anyone who joined normally: their
    Telegram first name, or what they set with /name. The caller commits.
    """
    if guest.telegram_user_id is not None:
        raise LinkRefused(f"{guest.display_name} is already linked to a Telegram account.")
    if account.telegram_user_id is None or account.id == guest.id:
        raise LinkRefused(f"{account.display_name} has no Telegram account to link.")
    if shared := await sessions_both_played(db, guest, account, now):
        days = ", ".join(fmt_date(sgt_date(session.starts_at)) for session in shared)
        raise LinkRefused(
            f"{guest.display_name} and {account.display_name} are both marked in for {days}. "
            "Use /edit to mark one of them out first."
        )
    keep, gone = guest.id, account.id

    # An open wizard holding the account's ID would point at nobody: let it expire.
    for draft in (await db.scalars(select(Draft))).all():
        if _mentions(draft.data, gone):
            await db.delete(draft)

    # A payment or settle-up line between the two would become one to themselves.
    await db.execute(
        delete(Transfer).where(_between(Transfer.from_member_id, Transfer.to_member_id, keep, gone))
    )
    await db.execute(
        delete(InvoiceLine).where(
            _between(InvoiceLine.from_member_id, InvoiceLine.to_member_id, keep, gone)
        )
    )

    # One answer per session: if either record was in, the person played. Both being
    # in is left only for sessions not yet ended or cancelled, which aren't in anyone's
    # balance, so nothing already charged is re-split.
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

    telegram_user_id, display_name, username, dm_enabled = (
        account.telegram_user_id,
        account.display_name,
        account.username,
        account.dm_enabled,
    )
    await db.delete(account)
    await db.flush()  # frees the Telegram user ID, which is unique
    guest.telegram_user_id = telegram_user_id
    guest.display_name = display_name
    guest.username = username
    guest.dm_enabled = dm_enabled
    guest.active = True
    await db.flush()


def _mentions(data: dict[str, Any], member_id: int) -> bool:
    """Whether a wizard's state holds ``member_id`` in one of its member fields."""
    return any(data.get(field) == member_id for field in _DRAFT_MEMBER_FIELDS) or any(
        member_id in (data.get(field) or ()) for field in _DRAFT_MEMBER_LISTS
    )


def _between(
    sender: InstrumentedAttribute[int], receiver: InstrumentedAttribute[int], one: int, other: int
) -> ColumnElement[bool]:
    return or_(
        and_(sender == one, receiver == other),
        and_(sender == other, receiver == one),
    )
