"""The monthly run (FR-INV-1): generate the invoice, post it, carry over the previous
one's unpaid lines and DM everyone their statement. ``preview_invoice`` shows what a run
would do without doing it (FR-INV-8)."""

from __future__ import annotations

import html
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Bot

from badminton.config import Settings
from badminton.db.models import Invoice
from badminton.domain.timeutil import (
    fmt_period,
    is_daily_post_time,
    period_of,
    previous_period,
    sgt_date,
)
from badminton.services.invoice import (
    card_text,
    discard_invoice,
    generate_invoice,
    plan_invoice,
    previous_invoice,
    publish_invoice,
    refresh_invoice_card,
    unpaid_lines,
)
from badminton.services.statements import send_statements, statements
from badminton.services.tick import TickContext

PREVIEW_HEADER = (
    "👀 <b>Preview</b>: /invoice would post this in the group now. Nothing has been saved or sent."
)

# How many days into a month a missed invoice run is still caught up. Beyond that (e.g. a
# first deployment mid-month) the previous month is left alone.
CATCH_UP_DAYS = 7


async def run_invoice(
    bot: Bot, db: AsyncSession, settings: Settings, *, period: str, now: datetime
) -> Invoice | None:
    """Returns None if ``period`` was already invoiced. If the card can't be posted, the
    invoice is discarded so the next run tries again."""
    previous = await previous_invoice(db)
    previous_id = previous.id if previous is not None else None
    invoice = await generate_invoice(db, period=period, now=now)
    if invoice is None:
        return None
    invoice_id = invoice.id
    await db.commit()
    try:
        await publish_invoice(bot, db, settings, invoice)
    except Exception:
        await db.rollback()
        await discard_invoice(db, invoice_id, previous_id)
        await db.commit()
        raise
    await db.commit()
    if previous is not None:
        await refresh_invoice_card(bot, db, previous, carried_into=fmt_period(period))
    await send_statements(bot, db, invoice)
    await db.commit()
    return invoice


async def preview_invoice(db: AsyncSession, *, period: str, now: datetime) -> str | None:
    """What ``run_invoice`` would do for ``period`` at ``now``, with nothing saved or sent
    (FR-INV-8): the card it would post, how many of the previous invoice's unpaid lines it
    would carry over, and who would get a statement. None if ``period`` was already
    invoiced, so a run would do nothing."""
    if await db.scalar(select(Invoice.id).where(Invoice.period == period)) is not None:
        return None
    plan = await plan_invoice(db, period=period, now=now)
    views = plan.line_views()
    # Every line on a new invoice is open, so each would get a ✅ button.
    card = card_text(plan.summary, views, has_buttons=True)
    out = [PREVIEW_HEADER, "", f"<blockquote>{card}</blockquote>", ""]
    if plan.previous is not None and (carried := len(await unpaid_lines(db, plan.previous))):
        out.append(
            f"↪️ {carried} unpaid line{'s' if carried != 1 else ''} on the "
            f"{fmt_period(plan.previous.period)} invoice would be carried into this one."
        )
    recipients = await statements(db, period, plan.ledger, plan.previous, views)
    if recipients:
        names = ", ".join(html.escape(member.display_name) for member, _ in recipients)
        out.append(f"📬 Private statements would go to: {names}")
    else:
        out.append("📬 No one would get a private statement.")
    return "\n".join(out)


def scheduled_period(now: datetime) -> str | None:
    """The period the monthly run invoices at ``now``: the previous month, on the 1st (SGT)
    or a few days after if runs were missed. None later in the month."""
    if sgt_date(now).day > CATCH_UP_DAYS:
        return None
    return previous_period(now)


async def manual_period(db: AsyncSession, now: datetime) -> str:
    """The period /invoice fills: the previous month while the monthly run would still
    invoice it, so the two can't both post one; otherwise the current month."""
    scheduled = scheduled_period(now)
    if scheduled is not None:
        existing = await db.scalar(select(Invoice.id).where(Invoice.period == scheduled))
        if existing is None:
            return scheduled
    return period_of(now)


async def monthly_invoice(context: TickContext) -> None:
    """Tick step: invoice the scheduled period unless it already has an invoice (FR-INV-1).
    It waits for 10:00 SGT, so the invoice and statements don't go out at midnight."""
    period = scheduled_period(context.now)
    if period is None or not is_daily_post_time(context.now):
        return
    async with context.sessionmaker() as db:
        await run_invoice(context.bot, db, context.settings, period=period, now=context.now)
