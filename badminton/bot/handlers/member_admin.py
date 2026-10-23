"""Admin member management: /addmember and /members (FR-MEM-5, FR-MEM-8).

Guests (people without Telegram) are added by name so they can be marked in and
charged. When a guest joins, the admin links them to the record the bot made for
their Telegram account. Every change is announced in the group and audited
(FR-ADM-3, FR-ADM-5).
"""

from __future__ import annotations

import html

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from badminton.bot import prompts
from badminton.bot.admin import admin_only
from badminton.bot.context import db_session, settings_of
from badminton.db.models import Draft, Member
from badminton.domain.money import fmt_signed
from badminton.domain.timeutil import fmt_date, sgt_date, utc_now
from badminton.services import audit
from badminton.services.drafts import create_draft, delete_draft
from badminton.services.invoice import previous_invoice, refresh_invoice_card
from badminton.services.members import (
    LinkRefused,
    all_members,
    link_guest,
    member_label,
    mention_html,
    sessions_both_played,
    upsert_from_telegram,
    validate_name,
)
from badminton.services.notify import announce
from badminton.services.repo import get_balances

RENAME_KIND = "member_rename"
LIST = "mem:list"
NOBODY_TO_LINK = "Nobody has used the bot yet. Ask them to send /start to the bot first."
LINK_GONE = "That's out of date: the guest is already linked or someone was removed."
ALERT_LIMIT = 200  # characters Telegram shows in an alert
LIST_CLOSED = "🔒 Linked. Send /members again to link someone else or make other changes."


def render_members(members: list[Member]) -> tuple[str, InlineKeyboardMarkup]:
    lines = ["👥 <b>Members</b>"]
    buttons = []
    for member in members:
        lines.append(f"• {html.escape(member_label(member))}")
        toggle = "↩️ Reactivate" if not member.active else "🚫 Deactivate"
        row = [
            InlineKeyboardButton(f"✏️ {member.display_name}", callback_data=f"mem:ren:{member.id}")
        ]
        if member.telegram_user_id is None:
            row.append(InlineKeyboardButton("🔗 Link", callback_data=f"mem:lnk:{member.id}"))
        row.append(InlineKeyboardButton(toggle, callback_data=f"mem:act:{member.id}"))
        buttons.append(row)
    return "\n".join(lines), InlineKeyboardMarkup(buttons)


@admin_only
async def add_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        name = validate_name(" ".join(context.args or ()))
    except ValueError:
        await update.effective_message.reply_text("Usage: /addmember &lt;name&gt;")
        return
    async with db_session(context) as db:
        admin = await upsert_from_telegram(db, update.effective_user)
        member = Member(display_name=name)
        db.add(member)
        await db.flush()
        audit.record(
            db, actor_member_id=admin.id, action="member.add", after={"id": member.id, "name": name}
        )
        await db.commit()
    await announce(
        context.bot,
        settings_of(context),
        f"➕ Admin added guest member <b>{html.escape(name)}</b>.",
    )


@admin_only
async def list_members(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with db_session(context) as db:
        text, markup = render_members(await all_members(db))
    await update.effective_message.reply_text(text, reply_markup=markup)


@admin_only
async def toggle_active(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    member_id = int(query.data.rsplit(":", 1)[1])
    async with db_session(context) as db:
        member = await db.get(Member, member_id)
        if member is None:
            await query.answer("That member no longer exists.", show_alert=True)
            return
        admin = await upsert_from_telegram(db, update.effective_user)
        member.active = not member.active
        audit.record(
            db,
            actor_member_id=admin.id,
            action="member.activate" if member.active else "member.deactivate",
            member_id=member.id,
        )
        name = html.escape(member.display_name)
        active = member.active
        await db.commit()
        text, markup = render_members(await all_members(db))
    await query.answer()
    await query.edit_message_text(text, reply_markup=markup)
    verb = "↩️ Admin reactivated" if active else "🚫 Admin deactivated"
    await announce(context.bot, settings_of(context), f"{verb} <b>{name}</b>.")


@admin_only
async def start_rename(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    member_id = int(query.data.rsplit(":", 1)[1])
    async with db_session(context) as db:
        member = await db.get(Member, member_id)
        if member is None:
            await query.answer("That member no longer exists.", show_alert=True)
            return
        draft = await create_draft(
            db,
            kind=RENAME_KIND,
            chat_id=query.message.chat_id,
            user_id=update.effective_user.id,
            now=utc_now(),
            # Not the draft's message_id: that one is deleted when the draft expires.
            data={"member_id": member.id, "list_message_id": query.message.message_id},
        )
        await prompts.ask(
            context.bot,
            draft,
            f"what should <b>{html.escape(member.display_name)}</b> be called?",
            owner=update.effective_user,
            field="name",
        )
        await db.commit()
    await query.answer()


@prompts.on_reply(RENAME_KIND)
async def finish_rename(
    update: Update, context: ContextTypes.DEFAULT_TYPE, db: AsyncSession, draft: Draft, text: str
) -> None:
    try:
        name = validate_name(text)
    except ValueError as error:
        raise prompts.PromptError(
            str(error).replace("Usage: /name <your name>", "Enter a name.")
        ) from None
    member = await db.get(Member, draft.data["member_id"])
    admin = await upsert_from_telegram(db, update.effective_user)
    old_name = member.display_name
    member.display_name = name
    audit.record(
        db,
        actor_member_id=admin.id,
        action="member.rename",
        before=old_name,
        after=name,
        member_id=member.id,
    )
    await db.flush()
    text_, markup = render_members(await all_members(db))
    await context.bot.edit_message_text(
        text_, chat_id=draft.chat_id, message_id=draft.data["list_message_id"], reply_markup=markup
    )
    await delete_draft(context.bot, db, draft)
    await announce(
        context.bot,
        settings_of(context),
        f"✏️ Admin renamed <b>{html.escape(old_name)}</b> to <b>{html.escape(name)}</b>.",
    )


@admin_only
async def back_to_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with db_session(context) as db:
        text, markup = render_members(await all_members(db))
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(text, reply_markup=markup)


@admin_only
async def pick_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Which Telegram member is this guest? Newest first: they've probably just joined."""
    query = update.callback_query
    guest_id = int(query.data.rsplit(":", 1)[1])
    async with db_session(context) as db:
        guest = await db.get(Member, guest_id)
        if guest is None or guest.telegram_user_id is not None:
            await query.answer(LINK_GONE, show_alert=True)
            return
        accounts = list(
            await db.scalars(
                select(Member)
                .where(Member.telegram_user_id.is_not(None))
                .order_by(Member.id.desc())
            )
        )
    if not accounts:
        await query.answer(NOBODY_TO_LINK, show_alert=True)
        return
    buttons = [
        [
            InlineKeyboardButton(
                member_label(account), callback_data=f"mem:lnk:{guest.id}:{account.id}"
            )
        ]
        for account in accounts
    ]
    buttons.append([InlineKeyboardButton("‹ Back", callback_data=LIST)])
    await query.answer()
    await query.edit_message_text(
        f"🔗 Which Telegram account is <b>{html.escape(guest.display_name)}</b>?\n"
        "Pick the member the bot added when they first used it.",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


@admin_only
async def confirm_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    guest_id, account_id = (int(part) for part in query.data.split(":")[2:4])
    async with db_session(context) as db:
        pair = await _link_pair(db, guest_id, account_id)
        if pair is None:
            await query.answer(LINK_GONE, show_alert=True)
            return
        guest, account = pair
        now = utc_now()
        shared = await sessions_both_played(db, guest, account, now)
        balances = await get_balances(db, now)
    name, account_name = html.escape(guest.display_name), html.escape(member_label(account))
    back = InlineKeyboardButton("‹ Back", callback_data=f"mem:lnk:{guest.id}")
    await query.answer()
    if shared:
        days = ", ".join(fmt_date(sgt_date(session.starts_at)) for session in shared)
        await query.edit_message_text(
            f"⚠️ <b>{name}</b> and <b>{account_name}</b> are both marked in for {days}. "
            "Linked, they would count once there, changing everyone's share of those "
            "sessions. Use /edit to mark one of them out first.",
            reply_markup=InlineKeyboardMarkup([[back]]),
        )
        return
    guest_cents, account_cents = balances.get(guest.id, 0), balances.get(account.id, 0)
    await query.edit_message_text(
        f"🔗 Link guest <b>{name}</b> to <b>{account_name}</b>?\n"
        f"Their records become one, called {html.escape(account.display_name)}. "
        f"Balances {fmt_signed(guest_cents)} and "
        f"{fmt_signed(account_cents)} make {fmt_signed(guest_cents + account_cents)}.",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✅ Link", callback_data=f"mem:lnk:{guest.id}:{account.id}:ok"
                    ),
                    back,
                ]
            ]
        ),
    )


@admin_only
async def finish_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    guest_id, account_id = (int(part) for part in query.data.split(":")[2:4])
    async with db_session(context) as db:
        pair = await _link_pair(db, guest_id, account_id, lock=True)
        if pair is None:
            await query.answer(LINK_GONE, show_alert=True)
            return
        guest, account = pair
        now = utc_now()
        before = await get_balances(db, now)
        guest_cents, account_cents = before.get(guest.id, 0), before.get(account.id, 0)
        guest_name = guest.display_name  # the link renames the guest
        linked_from = {
            "id": account.id,
            "name": account.display_name,
            "telegram_user_id": account.telegram_user_id,
            "balance_cents": account_cents,
        }
        try:
            await link_guest(db, guest, account, now=now)
        except LinkRefused as refusal:
            await query.answer(str(refusal)[:ALERT_LIMIT], show_alert=True)
            return
        # Only now: the admin may have just linked their own account.
        admin = await upsert_from_telegram(db, update.effective_user)
        audit.record(
            db,
            actor_member_id=admin.id,
            action="member.link",
            before={
                "guest": {"id": guest.id, "name": guest_name, "balance_cents": guest_cents},
                "account": linked_from,
            },
            after={
                "id": guest.id,
                "name": guest.display_name,
                "balance_cents": guest_cents + account_cents,
            },
        )
        await db.commit()
        text, _ = render_members(await all_members(db))
        name, mention = html.escape(guest_name), mention_html(guest)
        await query.answer()
        # Closed, without buttons, so a stray tap can't link or change anyone else.
        await query.edit_message_text(f"{text}\n\n{LIST_CLOSED}")
        await announce(
            context.bot,
            settings_of(context),
            f"🔗 Admin linked guest <b>{name}</b> to {mention}. "
            "Their balance and history are now one record.",
        )
        # Its lines may have moved to the guest, or gone if they were between the two.
        if (latest := await previous_invoice(db)) is not None:
            await refresh_invoice_card(context.bot, db, latest)


async def _link_pair(
    db: AsyncSession, guest_id: int, account_id: int, *, lock: bool = False
) -> tuple[Member, Member] | None:
    """The guest and the Telegram member to link, if that still makes sense. With
    ``lock``, both rows stay locked until the commit: a second tap on ✅ waits, then
    finds the account gone."""
    guest = await db.get(Member, guest_id, with_for_update=lock)
    account = await db.get(Member, account_id, with_for_update=lock)
    if guest is None or account is None or guest.telegram_user_id is not None:
        return None
    if account.telegram_user_id is None:
        return None
    return guest, account


def register(application: Application) -> None:
    application.add_handler(CommandHandler("addmember", add_member))
    application.add_handler(CommandHandler("members", list_members))
    application.add_handler(CallbackQueryHandler(toggle_active, pattern=r"^mem:act:\d+$"))
    application.add_handler(CallbackQueryHandler(start_rename, pattern=r"^mem:ren:\d+$"))
    application.add_handler(CallbackQueryHandler(back_to_list, pattern=rf"^{LIST}$"))
    application.add_handler(CallbackQueryHandler(pick_account, pattern=r"^mem:lnk:\d+$"))
    application.add_handler(CallbackQueryHandler(confirm_link, pattern=r"^mem:lnk:\d+:\d+$"))
    application.add_handler(CallbackQueryHandler(finish_link, pattern=r"^mem:lnk:\d+:\d+:ok$"))
