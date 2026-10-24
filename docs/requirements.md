# Splitwise Badminton — Software Requirements

| | |
|---|---|
| Status | Draft v1 |
| Owner | Admin (group organiser) |
| Last updated | 2026-10-24 |
| Task breakdown | [`tasks.md`](../tasks.md) |

## 1. Introduction

### 1.1 Purpose
This document defines the requirements for a Telegram bot that tracks badminton costs for a friends' group. It replaces the hand-maintained `Badminton.xlsx`.

### 1.2 Background
Today the admin logs every court booking and shuttlecock purchase in a spreadsheet:

- The `Items` sheet has one row per expense: date, item, price, payer, and a share count per player.
  - **Court** rows split equally among the people who played that session.
  - **Shuttle** rows are batch purchases. Their shares are the playing time each person put in while the batch was in use. A batch is used until the next one is bought.
  - **Transfer** rows record settle-up payments. The payer is the sender and the share holder is the receiver.
- The `Total` sheet computes, per person: Paid, Spend = Σ shares × price per share, and Surplus = Paid − Spend.

Pain points:

- The admin must remember to key in attendance after every session.
- Player columns are fixed, so adding a new player means editing the sheet.
- Nothing reminds people to say whether they're coming, or to pay.

### 1.3 Scope
In scope: one Telegram group, where the bot does the following.

1. Records court bookings.
2. Collects attendance.
3. Locks attendance when a session ends.
4. Tracks shuttlecock batches.
5. Keeps running balances.
6. Posts a monthly settle-up.
7. Lets the admin correct any record, transparently.
8. Carries each player's balance over from the spreadsheet.

Out of scope for v1 is listed in [§8](#8-out-of-scope-v1).

### 1.4 Definitions
| Term | Meaning |
|---|---|
| **Member** | A person tracked by the bot. Usually linked to a Telegram account. A *guest member* has no Telegram account and is managed by the admin. |
| **Admin** | The organiser, identified by Telegram user ID in configuration. Can correct any record. |
| **Booking** | One court payment: who paid, start time, length in hours, price, and a screenshot. |
| **Session** | One or more bookings on the same day that overlap or run back-to-back, played as one block. Start = earliest booking start; end = latest booking end. |
| **Attendance** | A member's answer for a session: *in* or *out*. No answer counts as *out*. |
| **Share** | 1 hour played = 1 share. Used to split shuttle batches. |
| **Shuttle batch** | One shuttlecock purchase. Once in use, it covers every session that starts on or after the batch's start point, until the next batch starts. |
| **Waiting / open / closed batch** | A new purchase *waits* until the admin finishes the batch in use; at most one batch waits. The batch in use is *open*. It *closes* when the admin finishes it, and the waiting batch becomes the open one. |
| **Balance** | Credits − debits for a member over all time. Positive = the group owes them; negative = they owe the group. |
| **Settle-up** | A list of transfers that brings every balance back to (about) zero. |
| **SGT** | Singapore Time, UTC+8. Every user-facing date and time is in SGT. |

## 2. Users and stakeholders
| Role | Needs |
|---|---|
| **Member** | Report bookings and shuttle purchases quickly, answer attendance with one tap, know what they owe and to whom. |
| **Admin** | Stop manual bookkeeping, say when a shuttle batch is finished, fix mistakes, keep the group's trust through transparency. |
| **Guest** (non-Telegram) | Be charged correctly when the admin adds them to a session. |

## 3. Operating environment and constraints

### 3.1 Environment
- **Runtime:** Python 3.13 on Vercel Functions (Hobby, free tier).
  - The web app is FastAPI.
  - The bot uses python-telegram-bot in webhook mode.
- **Database:** Neon Postgres (free tier), using its pooled connection string. Access is through SQLAlchemy 2 (async) with psycopg 3. Migrations use Alembic.
- **Scheduler:** a Vercel cron runs once a day. An external free scheduler (e.g. cron-job.org) can optionally call the same endpoint more often.
- **Chat:** a single Telegram group. The bot is a group admin with the *Delete messages* permission.

### 3.2 Platform constraints
| ID | Constraint | Consequence for the design |
|---|---|---|
| C-1 | Vercel Hobby crons run at most once a day, at any time within the scheduled hour. | Locking can't depend on cron timing. It is enforced against the clock on every interaction (FR-ATT-5). All scheduled work is idempotent. |
| C-2 | A bot can't message a user who hasn't opened a chat with it (`/start`). | Settle-ups are always posted in the group. Personal DM statements are a bonus for members who have run `/start`. |
| C-3 | A bot can't list the members of a group. | Members are registered when they interact with the bot (FR-MEM-1). |
| C-4 | Inline-button `callback_data` is limited to 64 bytes. | Callbacks carry short IDs only. |
| C-5 | A bot can delete other users' messages, or pin messages, only if it is an admin with those rights. | The setup guide makes the bot an admin with both (FR-OPS-4). |
| C-6 | Serverless functions keep no memory between requests. | Wizard state is stored in the database (FR-BK-9). |

### 3.3 Assumptions
- Courts are bookable in whole-hour slots, as on ActiveSG. The hours entered are still free-form for flexibility.
- Everyone marked *in* plays for the whole session. There is no partial attendance.
- Members trust each other: whoever pays a settle-up line, or receives it, may mark it paid.
- Courts are booked on ActiveSG. Its confirmation screenshot lists the venue, and each one-hour slot with its date, start time and court (FR-BK-12).
- Phone numbers are already shared between friends privately. The bot shows Telegram handles only.

## 4. Functional requirements
Each requirement has an ID that the user stories (§7) and tasks (`tasks.md`) refer to. "Must" is mandatory for v1.

### 4.1 Members (MEM)
- **FR-MEM-1:**
  - The bot must create a member automatically the first time a Telegram user interacts with it: taps an attendance card, runs `/start`, or submits a booking.
  - The display name defaults to the user's Telegram first name.
  - The stored @username must be refreshed on every interaction.
- **FR-MEM-2:** `/start` in a private chat must link the user to their member record and turn on DM statements (`dm_enabled`).
- **FR-MEM-3:**
  - If a DM fails because the user blocked the bot, `dm_enabled` must be set to false.
  - Delivery to everyone else must carry on.
- **FR-MEM-4:** `/name <name>` must set the caller's display name, up to 32 characters.
- **FR-MEM-5:** The admin must be able to:
  - add a guest member with `/addmember <name>`;
  - list members with `/members`;
  - rename or deactivate any member.
  - Inactive members are hidden from pickers, but their history is kept.
  - Once a change is made (rename, deactivate, reactivate or link, FR-MEM-8), the list closes: it shows the updated members without buttons, so scrolling past it later can't change anyone by accident. `/members` opens a fresh list.
- **FR-MEM-6:**
  - Wherever the bot names someone a payment is owed to, it must show their @username.
  - Members without a username are shown as a text mention, i.e. a tappable name.
  - Guest members are shown by display name.
- **FR-MEM-7:** `/help` must list the commands available to the caller. Admin commands are shown to the admin only.
- **FR-MEM-8:** In `/members`, the admin can link a guest to a Telegram account. This covers someone who was a guest (for example, imported from the spreadsheet, FR-MIG-4) and has since joined, so the bot made them a second member record (FR-MEM-1).
  - The admin taps 🔗 on the guest, picks the member the bot made for their account (newest first), and confirms. The confirmation shows both balances and their sum.
  - The two records become one, with the account's name and Telegram identity, as for a member who joined normally (FR-MEM-1, FR-MEM-4). Everything the account did (bookings, attendance, shuttle purchases, payments, settle-up lines, stored balances) moves to the guest. Nobody else's figures change, so the linked balance is the two balances added together.
  - If both records are marked *in* for a session that has ended, the link is refused, naming the sessions. Counting the person once there would re-split costs already in everyone's balance, so the admin first marks one of them out with `/edit`, which announces that correction (FR-ADM-3).
  - For other sessions, the person counts once: *in* if either answer was *in*. Payments and settle-up lines between the two records are dropped, because they would be to themselves.
  - Open wizards that hold the account's member ID expire. The latest invoice card is refreshed.
  - The link is announced in the group and audited, and the member list closes (FR-MEM-5).

### 4.2 Court bookings (BK)
- **FR-BK-1:**
  - A member starts a booking by sending `/book` in the group. The bot asks for the booking screenshot with a reply prompt, and the member replies with it.
  - Sending the screenshot with the caption `/book` does both at once.
  - A new booking replaces the member's earlier one still waiting for its screenshot, and that prompt is removed. A screenshot answering a prompt whose form has expired starts a new booking.
  - The sender is the payer, whether or not they play.
  - The photo's Telegram `file_id` is stored as proof.
- **FR-BK-2:**
  - The bot must reply to the screenshot with a booking wizard: one message that is edited at each step.
  - Only the submitter can operate it. Anyone else who taps gets an alert.
- **FR-BK-3:** The bot reads the date, start time and hours off the screenshot (FR-BK-12). If it can't, it asks for them, starting with the **date**: a calendar keyboard.
  - It shows one month, with ‹ › buttons to move between months.
  - Past days are disabled.
- **FR-BK-4:** Then the **start time**: an hour grid from 07:00 to 22:00.
- **FR-BK-5:** Then the **duration**: buttons **[1h] [2h] [Other]**.
  - *Other* asks for the hours with a reply prompt.
  - It accepts a positive number in 0.5-hour steps, up to 6.
- **FR-BK-6:** **Price**: the screenshot shows none, so the bot suggests one.
  - The suggestion is the hourly rate × the court time: one hour per slot on the screenshot, e.g. 4h for two courts for 2 hours. Hours the member picks are taken as one court at a time.
  - The hourly rate is the latest booking of a single court's price per hour, since courts booked at once cost more per hour. The default rate is $7.40/h.
  - The member can type another amount with ✏️ Edit (FR-BK-8). It accepts $0.01–$500.00 with up to 2 decimal places, and stays if the hours change.
- **FR-BK-7:**
  - If the new booking is on the same date as an existing scheduled session, and the times overlap or run back-to-back, the wizard must offer **[Add to <existing session>]** or **[Separate session]**.
  - Adding the booking re-derives the session's start and end.
  - Changing the date, time or hours asks again. So does confirming, if someone booked a session to join in the meantime.
- **FR-BK-8:**
  - **Confirm**: the booking (date, start–end, hours, venue and courts, price and payer) with **[✅ Confirm] [✏️ Edit] [✖ Cancel]**.
  - **✏️ Edit** changes the date, time or hours (as in FR-BK-3 to FR-BK-5), or the venue, courts or price (reply prompts).
  - On confirm, the bot posts the screenshot captioned with the booking: its permanent record, which also shows when the attendance card will be posted.
  - The bot pins the record without notifying anyone (FR-ATT-4), and deletes the wizard and the member's screenshot.
- **FR-BK-9:**
  - Wizard state is stored in the database.
  - A wizard that isn't finished within 24 hours expires. Its message, and the member's booking screenshot, are deleted.
- **FR-BK-10:** The submitter or the admin can cancel a booking before its session starts.
  - Its record is unpinned.
  - A session left with no bookings is cancelled.
  - The cancellation is announced in the group.
- **FR-BK-11:** If a session starts within the reminder window (FR-ATT-1), confirming a booking for it posts the attendance card straight away.
- **FR-BK-12:** The bot reads the screenshot with OCR: Tesseract, run by the bot itself. No LLM or external service is involved.
  - It reads the venue (*Where*) and each one-hour slot of the *When* list: date, start time, and the court on the same or the next line. Other dates on the page are ignored, and so is the name: the sender is the payer (FR-BK-1).
  - The screenshot shows no year. The date is its next occurrence within the six months bookings are made for, and must fall on the weekday shown. Otherwise the day or weekday was misread, or the booking has passed, and the bot asks for the date.
  - The date, start time and hours are filled in only when the slots run back to back on one day. Otherwise the bot asks for them (FR-BK-3).
  - Anything else it can't read can be added with ✏️ Edit.

### 4.3 Attendance and locking (ATT)
- **FR-ATT-1:**
  - The bot must post an attendance card in the group `REMINDER_DAYS_BEFORE` days before a session (default 2).
  - The timing is by SGT calendar date, on the first scheduled run of that day.
- **FR-ATT-2:** The card must show the following, and must not @mention anyone:
  - the session's date, start–end time and hours;
  - each booking's price and payer;
  - **✅ In (n)** and **❌ Out (n)** with names;
  - the note "no answer = not playing".
- **FR-ATT-3:**
  - **[✅ I'm in] [❌ I'm out]** record or change the tapper's answer, and the card is updated in place.
  - Answers can be changed any number of times before the session ends.
- **FR-ATT-4:** No reminder messages or pings are sent beyond the card itself.
- **FR-ATT-5:** Attendance locks at the session's end time.
  - Any tap at or after that time is refused with an alert. That tap also triggers finalisation (FR-ATT-6) if it hasn't happened yet.
  - Members who never answered count as *out*.
- **FR-ATT-6:** Finalisation does three things, exactly once per session:
  - It removes the card's buttons and marks it 🔒.
  - It posts a summary: number of players, court cost each, and hours added to the current shuttle batch each.
  - It unpins the session's booking records (FR-BK-8).
- **FR-ATT-7:**
  - A session that ends with no one *in* is left out of all calculations.
  - The admin is notified by DM so they can fix the attendance or cancel the session.

### 4.4 Shuttlecock batches (SH)
- **FR-SH-1:** Any member records a shuttlecock purchase by sending `/shuttle <price>` in the group, as for a booking (FR-BK-1).
  - The sender is the payer. In a private chat, a member is asked to send it in the group instead, so someone outside the group can't record a purchase.
  - The admin can use it anywhere, and picks the payer from a list of members, to record a purchase for someone else.
  - The card shows the price and payer with **[✅ Record] [✖ Cancel]**. Only the sender can operate it.
  - The purchase becomes the *waiting* batch, for when the batch in use runs out. Nothing closes and nothing is billed. It is announced in the group.
- **FR-SH-2:** `/shuttle_finished` (admin) finishes the open batch: it closes, and the waiting batch becomes the open one. The admin picks the waiting batch's start point: **[Next session]** (default) or **[Last session]**, the latter for shuttles already used in the most recent session.
  - With no waiting batch, `/shuttle_finished` is refused: the next purchase must be recorded first.
  - With no open batch yet (the very first purchase), it puts the waiting batch in use.
- **FR-SH-3:** The group announcement when a batch is finished must include:
  - the batch that just closed: total hours, price per hour, and each player's hours and amount;
  - the batch now in use: price, payer and start point.
- **FR-SH-4:** A session belongs to the batch with the latest start point at or before the session's start.
  - Sessions before the first batch have no shuttle cost.
- **FR-SH-5:** At most one batch waits.
  - `/shuttle <price>` while a batch is waiting is refused with a warning that names it.
  - The database enforces this too, so two purchases recorded at the same moment can't both wait.
- **FR-SH-6:** While a purchase is waiting, its payer or the admin can cancel it from its card. A second tap confirms.
  - Anyone else gets an alert.
  - Once the batch is in use it can't be cancelled; the admin corrects it instead (FR-SH-7).
  - The cancellation is announced in the group and audited.
- **FR-SH-7:** `/shuttle_edit` (admin) lists the waiting batch, the open batch and the most recent closed ones. For a chosen batch the admin can:
  - change its price or payer; for a closed batch, the announcement shows the recalculated split;
  - remove it, while it is waiting;
  - undo `/shuttle_finished`, on the open batch: it goes back to waiting and the batch before it is open again, so that batch's split is withdrawn. This is refused while another batch is waiting (FR-SH-5).
  - Every change is announced and audited (FR-ADM-3, FR-ADM-5).

### 4.5 Cost rules and balances (LED)
- **FR-LED-1:**
  - All amounts are integer cents.
  - Every split uses the largest-remainder method, so the parts add up exactly to the total. Leftover cents go to members in ascending member-ID order.
- **FR-LED-2:** **Court**:
  - For a finalised session, each booking's payer is credited the booking price.
  - The session's total court price is debited equally across the members marked *in*.
- **FR-LED-3:** **Shuttle** (closed batches only):
  - The payer is credited the batch price.
  - The price is debited across players in proportion to their hours in the batch's sessions, where 1 hour = 1 share.
  - A closed batch with no hours is left to its payer.
- **FR-LED-4:**
  - An **open** batch is not billed: the payer is not credited and no one is debited.
  - Members can see their hours in it so far as "pending".
  - A **waiting** batch is not billed either, and covers no session yet.
- **FR-LED-5:** A **transfer** credits the sender and debits the receiver.
- **FR-LED-6:** Balance = credits − debits over all time. The sum of all members' balances must always be zero.
- **FR-LED-7:**
  - Costs and shares are derived from stored facts (bookings, attendance, batches, transfers, and the opening balances carried over from the spreadsheet, FR-MIG-2). They are never stored.
  - Any correction therefore flows through to every figure automatically.
- **FR-LED-8:** `/balance` shows the caller's balance, their pending hours in the open batch, the waiting batch (if any), and any unpaid settle-up lines involving them.
- **FR-LED-9:** `/upcoming` lists scheduled sessions with their date, time, hours and payer(s).

Worked examples:

- **Court:** 2h for $14.80, 4 players in → $3.70 each. With 3 players in, 1480 cents splits 494 / 493 / 493.
- **Shuttle:** an $80.00 batch that closes after 50 hours in total costs $1.60/h.
  - Someone with 12h pays $19.20 and someone with 2h pays $3.20.
  - This is the sheet's batch from 16 Aug 2026: alice 12h, bob 10h, carol 2h, dave 12h, erin 12h, frank 2h.

### 4.6 Invoicing and settlement (INV)
- **FR-INV-1:**
  - On the 1st of each month (SGT), the bot must generate an invoice for the previous month, once only.
  - The admin can also generate one with `/invoice`.
- **FR-INV-2:**
  - The settle-up is computed from all-time balances by **fewest transfers**: greedily match the biggest debtor with the biggest creditor.
  - Amounts under $1.00 are skipped and carry forward.
  - Example: balances alice +$45, frank +$10, bob −$30, carol −$20, dave −$5 give 4 transfers: bob → alice $30, carol → alice $15, carol → frank $5, dave → frank $5.
- **FR-INV-3:** The group card must show:
  - the month's totals: sessions, court spend, and shuttle batches closed;
  - one line per transfer, `@payer → @payee $X.XX`, each with a **[✅ Paid]** button.
- **FR-INV-4:**
  - **[✅ Paid]** can be tapped by that line's payer, its payee, or the admin. Anyone else gets an alert.
  - A tap records a transfer and marks the line paid. Tapping again undoes it.
- **FR-INV-5:**
  - Members with DM enabled must receive a personal statement covering:
    - opening balance;
    - each session played: date, hours, court share;
    - each closed batch they were charged for: hours and amount;
    - payments sent and received;
    - the balance carried over from the spreadsheet, in the first statement after the import (FR-MIG-2);
    - adjustments;
    - closing balance;
    - who to pay and how much, or "nothing to pay".
  - Members with nothing to pay or receive, and no activity, get no DM.
- **FR-INV-6:**
  - When a new invoice is generated, the previous invoice's unpaid lines must be disabled and marked "carried into <month>".
  - Paid lines stay as they are.
- **FR-INV-7:**
  - The invoice stores each member's closing balance.
  - If later corrections change a figure already invoiced, the next statement shows the difference as an **Adjustments** line.

### 4.7 Admin corrections and transparency (ADM)
- **FR-ADM-1:** Only Telegram user IDs in `ADMIN_TELEGRAM_IDS` can use admin commands. Anyone else is refused and nothing changes.
- **FR-ADM-2:** `/edit` lists recent sessions, newest first, including locked ones. For a chosen session the admin can:
  - toggle each active member *in* / *out*, then **[Save]**;
  - change a booking's price, payer or hours;
  - cancel the session.
- **FR-ADM-3:**
  - Every admin change must be announced in the group, with a before/after diff and the recalculated split.
  - Example: "✏️ Admin changed Sun 4 Oct: +carol −dave · court now $3.70 each".
- **FR-ADM-4:**
  - `/settle` (admin) records a manual transfer: from, to and amount.
  - It is announced in the group.
- **FR-ADM-5:** Every admin change, every shuttle purchase, and every booking or purchase cancellation is written to an audit log: who, what, before and after, and when.
- **FR-ADM-6:** Admin commands work in the group or in a private chat with the bot. Announcements always go to the group.

### 4.8 Group moderation (MOD)
- **FR-MOD-1:**
  - In the group, the bot must delete every message from a non-admin member unless it is one of these:
    - a command the bot handles;
    - a `/book` photo;
    - a reply to an active bot prompt.
  - This covers plain text, stickers, media, unknown commands and forwards.
- **FR-MOD-2:**
  - A reply to a bot prompt (FR-BK-1, FR-BK-5, FR-BK-8 and the admin wizards) is processed.
  - After processing, both the reply and the prompt are deleted, so the group only holds bot cards.
  - The exception is the booking screenshot: it stays until the booking is confirmed or cancelled, so it can be checked against (FR-BK-8).
- **FR-MOD-3:** Messages from the admin and service messages (joins, leaves, pins, title changes) are never deleted.
- **FR-MOD-4:** The bot ignores updates from any chat other than the configured group and private chats.

### 4.9 Operations (OPS)
- **FR-OPS-1:** `POST /api/telegram` accepts Telegram webhook updates.
  - It rejects requests without the correct `X-Telegram-Bot-Api-Secret-Token` header (401).
  - It returns 200 for every authenticated update, even if a handler fails. Errors are logged, so Telegram doesn't keep retrying.
- **FR-OPS-2:** `GET /api/cron` runs the scheduled *tick*, and requires `Authorization: Bearer <CRON_SECRET>`. Each step of the tick must be safe to run any number of times:
  1. post due attendance cards;
  2. finalise ended sessions;
  3. generate the monthly invoice (on the 1st);
  4. clean up expired wizards.
- **FR-OPS-3:** A setup script must:
  - register the webhook, with its secret and the allowed update types;
  - publish the command list to Telegram.
- **FR-OPS-4:** The README must explain the full setup:
  - BotFather;
  - adding the bot to the group as an admin with *Delete messages* and *Pin messages*;
  - finding the group ID with `/chatid`;
  - Neon and migrations;
  - Vercel environment variables;
  - the optional external cron;
  - local long-polling for development.
- **FR-OPS-5:** `/chatid` replies with the current chat's ID, even before `GROUP_CHAT_ID` is configured.

### 4.10 Migration from the spreadsheet (MIG)
- **FR-MIG-1:** `scripts/import_spreadsheet.py <file>` reads the `Items` sheet of `Badminton.xlsx`.
  - By default it is a dry run. It prints the bot's members, which member each player column becomes, and a reconciliation table: Paid, Spend and Surplus as on the `Total` sheet, and the opening balance the bot will carry over.
  - `--apply` saves the import, writes it to the audit log, and announces the balances in the group (unless `--no-announce`).
  - Rows the bot can't reproduce are refused with their row number: an unknown item or payer, a price that isn't whole cents, a transfer without exactly one receiver, or a row without shares other than the latest shuttle purchase.
  - The dry run is refused in the same cases as `--apply` (FR-MIG-4), so it never invites an import that will fail.
- **FR-MIG-2:**
  - Each player's balance is carried over as one opening balance: Paid − Spend, computed exactly, then rounded to cents by largest remainder so the balances still sum to zero.
  - History isn't copied. The sheet stays the record of it.
  - Before saving, the import checks with the ledger that every member's balance moved by exactly their opening balance and that all balances sum to zero. Otherwise nothing is saved.
- **FR-MIG-3:** The latest shuttle row with no shares (a tube nobody has used yet) becomes the open batch.
  - It has the row's payer and price, and starts at 00:00 SGT the day after the sheet's last row.
  - Its price is left out of its buyer's opening balance; they are credited when the batch closes (FR-LED-3, FR-LED-4).
  - The tube needs a date. A court row dated after it is refused: that session played with the tube, but the batch only bills sessions after the sheet's last day. Its shares must be filled in first.
- **FR-MIG-4:**
  - A player column matches the member with the same display name or @username, ignoring case, or the member chosen with `--member NAME=ID`. Anyone else becomes a guest member.
  - The import refuses, changing nothing, anything that would count twice:
    - balances already carried over;
    - a session before the batch start;
    - a shuttle batch starting, or a payment recorded, before the batch start;
    - any shuttle batch, when the sheet has an unused tube.
  - Batches in use and payments can't be removed in the bot, so the refusal says to import into a new, empty database.

## 5. Non-functional requirements
| ID | Category | Requirement |
|---|---|---|
| NFR-1 | Cost | Runs entirely on free tiers: Vercel Hobby, Neon Free, and GitHub Actions free minutes for CI. |
| NFR-2 | Correctness | Money is integer cents. Splits add up exactly. All balances sum to zero, which is tested. |
| NFR-3 | Reliability | Scheduled steps and card/invoice posting are idempotent: they claim their work atomically in the database before sending. If a scheduled run is missed, the next one catches up. |
| NFR-4 | Security | The webhook secret, cron bearer token and admin ID list are configured as environment variables. Secrets are never logged. Updates from other chats are ignored. |
| NFR-5 | Privacy | Only Telegram user ID, first name, @username, booking photo `file_id`s, and the venue and courts read from them are stored. Screenshots are read through a temporary file that is deleted at once; they aren't kept. No phone numbers or payment details. |
| NFR-6 | Transparency | Every change that affects someone's money is visible in the group and recorded in the audit log. |
| NFR-7 | Usability | Each wizard is one message edited in place. Buttons are preferred over typing. Messages are short. All times are in SGT, e.g. "Sun 4 Oct, 11:00–13:00". |
| NFR-8 | Performance | A typical webhook update is handled within 3 seconds, allowing for a Neon cold start. |
| NFR-9 | Maintainability | Typed Python, linted with `ruff`. The domain (cost) logic is pure and fully unit-tested. Bot flows are tested against a fake Telegram API, with no network calls. CI runs on every PR. |

## 6. External interfaces

### 6.1 Telegram commands
| Command | Who | Where | Purpose |
|---|---|---|---|
| `/start` | anyone | DM | Link account, enable DM statements |
| `/help` | anyone | any | List available commands |
| `/book` | member | group | Start the booking wizard; then send the screenshot (or send it captioned `/book`) |
| `/shuttle <price>` | member | group (admin: any) | Record a shuttle purchase; it waits until the current batch is finished |
| `/balance` | member | any | Balance, pending shuttle hours, unpaid lines |
| `/upcoming` | member | any | Scheduled sessions |
| `/name <name>` | member | any | Set display name |
| `/chatid` | anyone | any | Show chat ID (setup) |
| `/edit` | admin | any | Correct a session's attendance, bookings, or cancel it |
| `/shuttle_finished` | admin | any | Finish the open shuttle batch; the waiting one takes over |
| `/shuttle_edit` | admin | any | Correct a shuttle batch's price or payer, remove a waiting one, or undo a finish |
| `/settle` | admin | any | Record a manual transfer |
| `/addmember <name>` | admin | any | Add a guest member |
| `/members` | admin | any | List, rename or deactivate members; link a guest to a Telegram account |
| `/invoice` | admin | any | Generate the invoice now |

### 6.2 HTTP endpoints
| Method & path | Auth | Purpose |
|---|---|---|
| `POST /api/telegram` | `X-Telegram-Bot-Api-Secret-Token` | Telegram webhook |
| `GET /api/cron` | `Authorization: Bearer` | Scheduled tick |

### 6.3 Configuration
`BOT_TOKEN`, `WEBHOOK_SECRET`, `DATABASE_URL`, `GROUP_CHAT_ID`, `ADMIN_TELEGRAM_IDS` (comma-separated), `CRON_SECRET`, `REMINDER_DAYS_BEFORE` (default 2), `DEFAULT_COURT_RATE_CENTS` (default 740).

### 6.4 Data model (summary)
| Entity | Key fields |
|---|---|
| Member | telegram_user_id (nullable for guests), display_name, username, dm_enabled, active |
| Session | starts_at, ends_at, status (scheduled / cancelled), card message ref, card_sent_at, locked_at |
| Booking | session, payer, starts_at, minutes, amount_cents, photo_file_id, venue, courts, record message ref, submitted_by |
| Attendance | session, member, status (in / out) |
| ShuttleBatch | payer, amount_cents, starts_at and opened_at (both empty while waiting; at most one waits) |
| Transfer | from, to, amount_cents, invoice line (optional), recorded_by |
| OpeningBalance | member (one each), amount_cents (signed), created_at |
| Invoice / InvoiceLine / InvoiceBalance | period (unique), lines, per-member closing balance |
| Draft | kind, chat, user, message refs, JSON state, expires_at |
| AuditLog | actor, action, payload (before/after), created_at |

## 7. Epics and user stories
Acceptance criteria are brief; the referenced FRs hold the detail.

### E1 — Foundation and deployment
- **US-1.1:** As the admin, I want the bot hosted on free infrastructure, so that running it costs nothing. *(NFR-1, FR-OPS-1)*
  - The bot answers webhooks on Vercel and stores data in Neon.
  - No paid plan is needed.
- **US-1.2:** As a developer, I want automated checks on every PR, so that changes don't break money logic. *(NFR-9)*
  - CI runs `ruff` and `pytest` against Postgres, and must pass before merge.
- **US-1.3:** As the admin, I want step-by-step setup instructions, so that I can deploy and reconfigure the bot myself. *(FR-OPS-3, FR-OPS-4, FR-OPS-5)*
  - Following the README from scratch gives a working bot in the group.
- **US-1.4:** As the admin, I want scheduled work to run reliably on a once-a-day cron, so that nothing is missed or sent twice. *(C-1, FR-OPS-2, NFR-3)*
  - Calling the cron endpoint repeatedly has no side effects after the first call.
  - A missed day is caught up on the next run.

### E2 — Members
- **US-2.1:** As a member, I want to be registered automatically when I first use the bot, so that there's no sign-up step. *(FR-MEM-1)*
  - My first tap on a card creates my member record with my Telegram name.
- **US-2.2:** As a member, I want to receive my statement privately, so that I can see my own breakdown. *(FR-MEM-2, FR-MEM-3)*
  - After `/start` in DM, I receive monthly statements.
  - If I block the bot, delivery to me stops without errors.
- **US-2.3:** As a member, I want to choose the name the bot shows for me. *(FR-MEM-4)*
- **US-2.4:** As the admin, I want to add and manage members who aren't on Telegram, so that guests can be charged. *(FR-MEM-5, FR-MEM-8)*
  - Guests appear in attendance editing and settle-ups.
  - Deactivated members disappear from pickers but keep their history.
  - When a guest joins, I link them to their Telegram account, and their balance and history follow them.
- **US-2.5:** As a member, I want to check my balance and upcoming sessions any time. *(FR-LED-8, FR-LED-9)*

### E3 — Court booking
- **US-3.1:** As a member who booked a court, I want to submit the booking screenshot with `/book`, so that the group knows I paid. *(FR-BK-1, FR-BK-2)*
  - I'm recorded as payer even if I don't play.
  - Only I can operate my wizard.
- **US-3.2:** As a member, I want the bot to read my booking off the screenshot, so that submitting is quick and error-free. *(FR-BK-3 to FR-BK-6, FR-BK-8, FR-BK-12)*
  - It shows the date, time, venue, court and a suggested price. I confirm, or fix anything with ✏️ Edit.
  - If it can't read the date and time, I pick them with taps: calendar, hour grid, [1h][2h][Other].
- **US-3.3:** As a member, I want consecutive or overlapping bookings on the same day to count as one session, so that we don't get two polls for one game. *(FR-BK-7)*
- **US-3.4:** As a member, I want unfinished wizards to clean themselves up, so that the group stays tidy. *(FR-BK-9)*
- **US-3.5:** As the submitter or the admin, I want to cancel a booking before it happens, so that refunded courts aren't charged. *(FR-BK-10)*
- **US-3.6:** As a member, I want a session booked at short notice to get its attendance card straight away. *(FR-BK-11)*
- **US-3.7:** As a member, I want upcoming bookings pinned in the group, with their screenshot, so that I can see when and where we play. *(FR-BK-8, FR-BK-10, FR-ATT-6)*

### E4 — Attendance and locking
- **US-4.1:** As a member, I want an attendance card 2 days before each session, so that I can say whether I'm playing with one tap. *(FR-ATT-1, FR-ATT-2)*
- **US-4.2:** As a member, I want to change my answer until the session ends, so that late changes of plan are handled. *(FR-ATT-3)*
- **US-4.3:** As a member, I don't want reminder pings; not answering means I'm not playing. *(FR-ATT-4, FR-ATT-5)*
- **US-4.4:** As the group, we want attendance locked when the session ends, with a summary of the split. *(FR-ATT-5, FR-ATT-6)*
  - Taps after the end time are refused.
  - The summary is posted once.
- **US-4.5:** As the admin, I want to be told when a session ends with nobody marked in, so that I can fix it. *(FR-ATT-7)*

### E5 — Shuttlecock batches
- **US-5.1:** As a member who bought shuttlecocks, I want to record the purchase myself, so that its cost is shared by usage. *(FR-SH-1, FR-SH-4)*
  - I can record it before the current batch runs out; it waits until then.
- **US-5.2:** As a member, when a batch is finished I want to see how it was split, so that the shuttle cost is transparent. *(FR-SH-2, FR-SH-3, FR-LED-3)*
- **US-5.3:** As a member, I want to see my hours so far in the current batch, and whether a new one is waiting. *(FR-LED-4, FR-LED-8)*
- **US-5.4:** As the admin, I want to say when the current batch is finished, so that later sessions count towards the next one. *(FR-SH-2, FR-SH-5)*
  - Only one purchase can wait, so there is never doubt about which batch comes next.
- **US-5.5:** As a member, I want to cancel a purchase I recorded by mistake, before it is used. *(FR-SH-6)*

### E6 — Cost engine
- **US-6.1:** As a member, I want court costs split equally among the people who played, and the booker credited. *(FR-LED-2)*
- **US-6.2:** As a member, I want each shuttle batch split by hours played (1h = 1 share), billed only once it's used up. *(FR-LED-3, FR-LED-4)*
- **US-6.3:** As the admin, I want exact cent-level splits whose balances always net to zero, so that the books always reconcile. *(FR-LED-1, FR-LED-6, NFR-2)*
- **US-6.4:** As the admin, I want corrections to recalculate every affected figure automatically. *(FR-LED-5, FR-LED-7)*

### E7 — Monthly invoicing and settlement
- **US-7.1:** As a member, on the 1st of each month I want a settle-up in the group listing who pays whom, with the fewest transfers. *(FR-INV-1 to FR-INV-3, FR-MEM-6)*
- **US-7.2:** As a member, I want to mark a transfer as paid, so that balances clear. *(FR-INV-4)*
  - The payer, payee or admin can toggle it.
  - Anyone else is refused.
- **US-7.3:** As a member, I want a private statement explaining my amount. *(FR-INV-5)*
- **US-7.4:** As the admin, I want to generate an invoice on demand, and have old unpaid lines carried into the new one. *(FR-INV-1, FR-INV-6)*
- **US-7.5:** As a member, I want corrections to past months shown as adjustments, so that the numbers still add up. *(FR-INV-7)*

### E8 — Admin corrections and transparency
- **US-8.1:** As the admin, I want to correct a session's attendance, with the change announced to the group. *(FR-ADM-2, FR-ADM-3)*
- **US-8.2:** As the admin, I want to correct a booking's price, payer or hours, or cancel a session. *(FR-ADM-2, FR-ADM-3)*
- **US-8.3:** As the admin, I want to record a payment made outside the bot. *(FR-ADM-4)*
- **US-8.4:** As the group, we want every admin change logged, and non-admins unable to make admin changes. *(FR-ADM-1, FR-ADM-5, FR-ADM-6)*
- **US-8.5:** As the admin, I want to correct a shuttle batch's price or payer, remove a mistaken purchase, or undo a mistaken finish. *(FR-SH-7, FR-ADM-3)*

### E9 — Group moderation
- **US-9.1:** As the admin, I want the group to contain only bot activity, so that cards and settle-ups are easy to find. *(FR-MOD-1, FR-MOD-3)*
- **US-9.2:** As a member, I want my replies to bot prompts to be accepted and then cleaned up. *(FR-MOD-2)*
- **US-9.3:** As the admin, I want the bot to ignore other chats. *(FR-MOD-4)*

### E10 — Migration from the spreadsheet
- **US-10.1:** As the admin, I want to carry everyone's balance over from `Badminton.xlsx`, so that nothing owed is lost and the bot's figures match the sheet to the cent. *(FR-MIG-1, FR-MIG-2, FR-MIG-4)*
  - A dry run shows the reconciliation before anything is saved.
  - The carried-over balances sum to zero.
- **US-10.2:** As a member, I want a tube bought but not yet used before the switch to be split by hours played, like any other batch. *(FR-MIG-3)*

## 8. Out of scope (v1)
- More than one group.
- A "+1 guest" button on the attendance card. Guests are added by the admin instead.
- Partial attendance within a session.
- PayNow numbers or QR codes.
- Importing the history in `Badminton.xlsx`, row by row. Only each player's balance and the unused tube are carried over (§4.10).
- A web dashboard.
- Recurring bookings.
- Currencies other than SGD.
