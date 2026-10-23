"""Members: registered automatically from Telegram users (FR-MEM-1) or added by the admin."""

from __future__ import annotations

import html
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import User

from badminton.db.models import Member

MAX_NAME_LENGTH = 32


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
