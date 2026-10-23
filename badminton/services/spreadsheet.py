"""Carry the spreadsheet's balances into the bot (FR-MIG-1 to FR-MIG-4).

Each player's balance comes over as one opening balance, so the bot's figures match
the sheet to the cent. The unused tube becomes the open shuttle batch. Players who
aren't members yet are added as guests, to be linked to their Telegram account when
they join.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from badminton.db.models import Member, OpeningBalance, Session, ShuttleBatch
from badminton.domain.sheet import SheetSummary
from badminton.domain.timeutil import at_sgt, fmt_date, sgt_date
from badminton.services import audit
from badminton.services.members import clean_name
from badminton.services.repo import get_balances


class ImportRefused(RuntimeError):
    """The import would clash with what the bot already holds; nothing was changed."""


class TallyMismatch(RuntimeError):
    """After importing, the bot's balances didn't match the sheet. The caller rolls back."""


@dataclass(frozen=True)
class Match:
    name: str  # the sheet's player column
    member: Member | None  # None: a guest will be added
    how: str


@dataclass(frozen=True)
class ImportResult:
    member_ids: dict[str, int]
    guests: tuple[str, ...]  # players added as guest members
    opening_cents: dict[str, int]
    batch_id: int | None
    balances: dict[int, int]  # every member's balance after the import


def cutoff(summary: SheetSummary) -> datetime:
    """Where the bot takes over: the start of the day after the sheet's last row (SGT).
    The unused tube is the batch from here on."""
    return at_sgt(summary.last_day + timedelta(days=1), 0)


async def match_members(
    db: AsyncSession, players: tuple[str, ...], chosen: Mapping[str, int]
) -> list[Match]:
    """Pair each player with a member: ``chosen`` (sheet name -> member ID) first, then
    a member whose name or @username is the player's name, else a new guest."""
    unknown = sorted(set(chosen) - set(players))
    if unknown:
        raise ImportRefused(f"No player column called {', '.join(map(repr, unknown))}.")
    matches = []
    for name in players:
        if name in chosen:
            member = await db.get(Member, chosen[name])
            if member is None:
                raise ImportRefused(f"There's no member #{chosen[name]} (for {name!r}).")
            matches.append(Match(name, member, "chosen with --member"))
            continue
        lowered = name.casefold()
        candidates = list(
            await db.scalars(
                select(Member)
                .where(
                    or_(
                        func.lower(Member.display_name) == lowered,
                        func.lower(Member.username) == lowered,
                    )
                )
                .order_by(Member.id)
            )
        )
        if len(candidates) > 1:
            ids = ", ".join(f"#{member.id}" for member in candidates)
            raise ImportRefused(f"{name!r} could be member {ids}: pick one with --member.")
        if candidates:
            matches.append(Match(name, candidates[0], "same name"))
        else:
            matches.append(Match(name, None, "new guest"))

    taken: dict[int, str] = {}
    for match in matches:
        if match.member is None:
            continue
        if match.member.id in taken:
            raise ImportRefused(
                f"{taken[match.member.id]!r} and {match.name!r} are both member #{match.member.id}."
            )
        taken[match.member.id] = match.name
    return matches


async def check_can_import(db: AsyncSession, summary: SheetSummary) -> None:
    """Refuse an import that would count something twice."""
    if await db.scalar(select(OpeningBalance.member_id).limit(1)) is not None:
        raise ImportRefused("Balances have already been carried over from the spreadsheet.")
    if summary.pending is not None and await db.scalar(select(ShuttleBatch.id).limit(1)):
        raise ImportRefused(
            "A shuttle batch is already recorded, but the sheet's unused tube must become "
            "the first batch."
        )
    earlier = await db.scalar(
        select(Session.starts_at)
        .where(Session.status == "scheduled", Session.starts_at < cutoff(summary))
        .order_by(Session.starts_at)
        .limit(1)
    )
    if earlier is not None:
        raise ImportRefused(
            f"The bot already has a session on {fmt_date(sgt_date(earlier))}, which the "
            "sheet covers. Cancel it with /edit first."
        )


async def import_sheet(
    db: AsyncSession, summary: SheetSummary, matches: list[Match], *, now: datetime
) -> ImportResult:
    """Add the guests, opening balances and open batch, then check that every balance
    moved by exactly the sheet's amount. The caller commits, or rolls back on error."""
    await check_can_import(db, summary)
    before = await get_balances(db, now)

    member_ids: dict[str, int] = {}
    guests = []
    for match in matches:
        member = match.member
        if member is None:
            member = Member(display_name=clean_name(match.name))
            db.add(member)
            await db.flush()
            guests.append(match.name)
        member_ids[match.name] = member.id

    opening = summary.opening_cents()
    db.add_all(
        OpeningBalance(member_id=member_ids[name], amount_cents=cents, created_at=now)
        for name, cents in opening.items()
        if cents
    )
    batch = None
    if summary.pending is not None:
        batch = ShuttleBatch(
            payer_member_id=member_ids[summary.pending.payer],
            amount_cents=summary.pending.amount_cents,
            starts_at=cutoff(summary),
            created_at=now,
        )
        db.add(batch)
    await db.flush()
    audit.record(
        db,
        actor_member_id=None,
        action="sheet.import",
        after={
            name: {"member_id": member_ids[name], "opening_cents": opening[name]}
            for name in summary.players
        },
        guests=guests,
        rows=summary.row_count,
        last_day=summary.last_day,
        batch_id=batch.id if batch is not None else None,
    )
    await db.flush()

    after = await get_balances(db, now)
    wrong = [
        name
        for name, member_id in member_ids.items()
        if after.get(member_id, 0) - before.get(member_id, 0) != opening[name]
    ]
    if wrong or sum(after.values()) != 0:
        raise TallyMismatch(
            f"Balances don't tally for {', '.join(wrong) or 'the group'}; nothing was saved."
        )
    return ImportResult(
        member_ids=member_ids,
        guests=tuple(guests),
        opening_cents=opening,
        batch_id=batch.id if batch is not None else None,
        balances=after,
    )
