# Tasks

This is the implementation plan for [`docs/requirements.md`](docs/requirements.md). Each task is atomic: it does one thing, can be reviewed on its own, and leaves `main` green and deployable. Each task is delivered as **one branch → one PR**.

## Conventions

**Workflow**
1. Branch from the latest `main` once every task in *Depends on* has merged.
2. Branch name: `<type>/t<NN>-<slug>`, where `<type>` is one of `feat`, `chore`, `ci`, `test`, `docs` or `fix`.
3. Keep the PR to the task's scope. If you find other work, add it as a new task here instead.
4. Merge once CI is green. In the same PR, set the task's row below to ✅ and add the PR link.

**PR title:** `T<NN>: <task title>`, for example `T05: Web endpoints and Vercel config`.

**PR description:** about 200 words, using this template:

```markdown
## Summary
What this PR does and why, in 1–3 sentences.

## Changes
- 3–6 bullets of the notable changes

## Testing
- `uv run ruff check && uv run pytest` (plus anything manual)

Task: T<NN> · Stories: US-x.y · Depends on: #<PR>
```

**Definition of done (every task)**
- `uv run ruff check` and `uv run ruff format --check` are clean.
- `uv run pytest` passes, and new behaviour is covered by tests.
- No secrets are committed.
- New environment variables are added to `.env.example`.

## Summary

| ID | Task | Branch | Epic | Depends on | Status | PR |
|---|---|---|---|---|---|---|
| T01 | Project scaffold | `chore/t01-project-scaffold` | E1 | — | ✅ | [#2](https://github.com/matchastack/splitwise-badminton/pull/2) |
| T02 | CI workflow | `ci/t02-github-actions` | E1 | T01 | ✅ | [#3](https://github.com/matchastack/splitwise-badminton/pull/3) |
| T03 | Settings from environment | `feat/t03-settings` | E1 | T01 | ✅ | [#4](https://github.com/matchastack/splitwise-badminton/pull/4) |
| T04 | Database schema and migrations | `feat/t04-db-schema` | E1 | T03 | ✅ | [#5](https://github.com/matchastack/splitwise-badminton/pull/5) |
| T05 | Web endpoints and Vercel config | `feat/t05-web-endpoints` | E1 | T03 | ✅ | [#6](https://github.com/matchastack/splitwise-badminton/pull/6) |
| T06 | Fake Telegram test harness | `test/t06-fake-telegram-harness` | E1 | T04, T05 | ✅ | [#7](https://github.com/matchastack/splitwise-badminton/pull/7) |
| T07 | Telegram setup and dev scripts | `chore/t07-telegram-scripts` | E1 | T05 | ✅ | [#8](https://github.com/matchastack/splitwise-badminton/pull/8) |
| T08 | Money and allocation | `feat/t08-money-allocation` | E6 | T01 | ✅ | [#9](https://github.com/matchastack/splitwise-badminton/pull/9) |
| T09 | SGT time utilities | `feat/t09-time-utils` | E6 | T01 | ✅ | [#10](https://github.com/matchastack/splitwise-badminton/pull/10) |
| T10 | Ledger: court and transfers | `feat/t10-ledger-court-transfers` | E6 | T08 | ✅ | [#11](https://github.com/matchastack/splitwise-badminton/pull/11) |
| T11 | Ledger: shuttle batches | `feat/t11-ledger-shuttle-batches` | E6 | T10 | ✅ | [#12](https://github.com/matchastack/splitwise-badminton/pull/12) |
| T12 | Debt simplification | `feat/t12-debt-simplification` | E6 | T08 | ✅ | [#13](https://github.com/matchastack/splitwise-badminton/pull/13) |
| T13 | Monthly statement builder | `feat/t13-monthly-statement` | E6 | T09, T11 | ✅ | [#14](https://github.com/matchastack/splitwise-badminton/pull/14) |
| T14 | Ledger repository | `feat/t14-ledger-repository` | E6 | T04, T11 | ✅ | [#15](https://github.com/matchastack/splitwise-badminton/pull/15) |
| T15 | Member registry and basic commands | `feat/t15-member-registry` | E2 | T06 | ✅ | [#16](https://github.com/matchastack/splitwise-badminton/pull/16) |
| T16 | Admin guard, audit log, announcements | `feat/t16-admin-guard-audit` | E8 | T15 | ✅ | [#17](https://github.com/matchastack/splitwise-badminton/pull/17) |
| T17 | Wizard drafts and reply prompts | `feat/t17-drafts-and-prompts` | E3 | T15 | ✅ | [#18](https://github.com/matchastack/splitwise-badminton/pull/18) |
| T18 | Group message guard | `feat/t18-group-message-guard` | E9 | T17 | ✅ | [#19](https://github.com/matchastack/splitwise-badminton/pull/19) |
| T19 | Member admin commands | `feat/t19-member-admin-commands` | E2 | T16, T17 | ✅ | [#20](https://github.com/matchastack/splitwise-badminton/pull/20) |
| T20 | Calendar and hour keyboards | `feat/t20-calendar-hour-keyboards` | E3 | T09 | ✅ | [#21](https://github.com/matchastack/splitwise-badminton/pull/21) |
| T21 | Booking wizard: date, time, duration | `feat/t21-booking-wizard-schedule` | E3 | T17, T20 | ✅ | [#22](https://github.com/matchastack/splitwise-badminton/pull/22) |
| T22 | Booking wizard: price and confirm | `feat/t22-booking-wizard-confirm` | E3 | T21 | ✅ | [#23](https://github.com/matchastack/splitwise-badminton/pull/23) |
| T23 | Same-day session merge | `feat/t23-booking-session-merge` | E3 | T22 | ✅ | [#24](https://github.com/matchastack/splitwise-badminton/pull/24) |
| T24 | Booking cancellation | `feat/t24-booking-cancel` | E3 | T16, T22 | ✅ | [#25](https://github.com/matchastack/splitwise-badminton/pull/25) |
| T25 | Attendance card | `feat/t25-attendance-card` | E4 | T22 | ✅ | [#26](https://github.com/matchastack/splitwise-badminton/pull/26) |
| T26 | Attendance card scheduling | `feat/t26-attendance-scheduling` | E4 | T25 | ✅ | [#27](https://github.com/matchastack/splitwise-badminton/pull/27) |
| T27 | Session finalisation | `feat/t27-session-finalize` | E4 | T14, T16, T25 | ✅ | [#28](https://github.com/matchastack/splitwise-badminton/pull/28) |
| T28 | Shuttle purchase | `feat/t28-shuttle-purchase` | E5 | T14, T16, T17 | ✅ | [#29](https://github.com/matchastack/splitwise-badminton/pull/29) |
| T29 | `/balance` and `/upcoming` | `feat/t29-balance-upcoming` | E2 | T14, T15 | ✅ | [#30](https://github.com/matchastack/splitwise-badminton/pull/30) |
| T30 | Invoice generation and group card | `feat/t30-invoice-generation` | E7 | T12, T13, T14, T16 | ✅ | [#32](https://github.com/matchastack/splitwise-badminton/pull/32) |
| T31 | Invoice Paid toggle | `feat/t31-invoice-paid-toggle` | E7 | T29, T30 | ✅ | [#33](https://github.com/matchastack/splitwise-badminton/pull/33) |
| T32 | DM statements | `feat/t32-invoice-dm-statements` | E7 | T30 | ✅ | [#34](https://github.com/matchastack/splitwise-badminton/pull/34) |
| T33 | Monthly schedule and `/invoice` | `feat/t33-invoice-schedule` | E7 | T32 | ✅ | [#36](https://github.com/matchastack/splitwise-badminton/pull/36) |
| T34 | Admin: edit attendance | `feat/t34-admin-edit-attendance` | E8 | T14, T16, T17, T25 | ✅ | [#38](https://github.com/matchastack/splitwise-badminton/pull/38) |
| T35 | Admin: edit bookings, cancel session | `feat/t35-admin-edit-booking` | E8 | T24, T34 | ✅ | [#39](https://github.com/matchastack/splitwise-badminton/pull/39) |
| T36 | Admin: manual transfer | `feat/t36-admin-manual-transfer` | E8 | T14, T16, T17 | ✅ | [#40](https://github.com/matchastack/splitwise-badminton/pull/40) |
| T37 | Setup and deployment guide | `docs/t37-setup-guide` | E1 | T07, T32 | ✅ | [#35](https://github.com/matchastack/splitwise-badminton/pull/35) |
| T38 | Opening balances and spreadsheet import | `feat/t38-spreadsheet-import` | E10 | T14, T16, T37 | ✅ | [#61](https://github.com/matchastack/splitwise-badminton/pull/61) |
| T39 | Link a guest to a Telegram account | `feat/t39-link-guests` | E2 | T19, T38 | ✅ | [#62](https://github.com/matchastack/splitwise-badminton/pull/62) |
| T40 | Linked guest's name; member list closes after a change | `feat/t40-linked-guest-name` | E2 | T39 | ✅ | [#64](https://github.com/matchastack/splitwise-badminton/pull/64) |
| T41 | Waiting shuttle batches and member purchases | `claude/dreamy-cerf-pz28gc` | E5 | T28, T38 | ✅ | [#67](https://github.com/matchastack/splitwise-badminton/pull/67) |
| T42 | Share the wizard card refresh | `claude/dazzling-noether-dpbhbz` | E3 | T17, T41 | ✅ | [#69](https://github.com/matchastack/splitwise-badminton/pull/69) |
| T43 | Read booking screenshots (OCR); pin bookings | `claude/inspiring-euler-fdophp` | E3 | T23, T24, T27, T42 | ✅ | [#72](https://github.com/matchastack/splitwise-badminton/pull/72) |
| T44 | Rename `/book` to `/court` | `feat/t44-court-command` | E3 | T43 | ✅ | [#73](https://github.com/matchastack/splitwise-badminton/pull/73) |
| T45 | Only group members use the bot in private chat | `feat/t45-group-member-check` | E9 | T44 | ✅ | — |
| T46 | Member commands in private chat | `feat/t46-member-commands-private` | E9 | T45 | ⬜ | — |
| T47 | Admin commands in private chat | `feat/t47-admin-commands-private` | E9 | T46 | ⬜ | — |
| T48 | Let the group chat freely | `feat/t48-group-chat` | E9 | T47 | ⬜ | — |

The domain tasks (T08–T13) are pure code with no I/O, so they can be built in parallel with T02–T07.

---

## E1 — Foundation and deployment

### T01 · Project scaffold
`chore/t01-project-scaffold` · Stories US-1.2 · Depends on —
- **Scope:**
  - `pyproject.toml` set up for uv with `requires-python = "~=3.13.0"`.
  - Runtime dependencies: fastapi, python-telegram-bot, sqlalchemy[asyncio], psycopg[binary], alembic.
  - Dev group: pytest, pytest-asyncio, httpx, ruff.
  - Generate `uv.lock`.
  - Create an empty `badminton/` package.
  - Add ruff and pytest configuration.
  - Add `.env`, `.venv` and `.vercel` to `.gitignore`.
  - Add `.env.example`.
- **Done when:** `uv sync`, `uv run ruff check` and a placeholder `uv run pytest` all succeed.

### T02 · CI workflow
`ci/t02-github-actions` · Stories US-1.2 · Depends on T01
- **Scope:** `.github/workflows/ci.yml`, triggered by PRs and pushes to `main`, running:
  - `uv sync --frozen`
  - `ruff check`
  - `ruff format --check`
  - `pytest`, against a `postgres:16` service container via `TEST_DATABASE_URL`
- **Done when:** the workflow runs green on this PR.

### T03 · Settings from environment
`feat/t03-settings` · Stories US-1.1 · FR-ADM-1 · Depends on T01
- **Scope:** `badminton/config.py` — a frozen `Settings` dataclass loaded from the environment variables in requirements §6.3.
  - Defaults for `REMINDER_DAYS_BEFORE` and `DEFAULT_COURT_RATE_CENTS`.
  - `ADMIN_TELEGRAM_IDS` is parsed into a set, with `is_admin(user_id)`.
  - `GROUP_CHAT_ID` is optional, so that `/chatid` works before setup.
- **Done when:** tests cover parsing, defaults, and a clear error when a required variable is missing.

### T04 · Database schema and migrations
`feat/t04-db-schema` · Stories US-1.1, US-6.4 · Depends on T03
- **Scope:**
  - `badminton/db/models.py`: SQLAlchemy 2 models for every entity in requirements §6.4.
  - `badminton/db/engine.py`: async engine with `NullPool` and psycopg `prepare_threshold=None` (needed for Neon's PgBouncer), plus a sessionmaker factory.
  - Alembic environment and the initial migration.
  - `tests/conftest.py`: migrates `TEST_DATABASE_URL` once per run and truncates tables between tests.
- **Done when:** `alembic upgrade head` and `downgrade base` run cleanly, and a round-trip test inserts and reads each model.

### T05 · Web endpoints and Vercel config
`feat/t05-web-endpoints` · Stories US-1.1, US-1.4 · FR-OPS-1, FR-OPS-2 · Depends on T03
- **Scope:**
  - `badminton/web.py`: the FastAPI app.
    - `POST /api/telegram` checks the secret header, builds the python-telegram-bot application (`badminton/bot/app.py`, no handlers yet), runs `process_update`, and always returns 200 once authenticated.
    - `GET /api/cron` checks the Bearer token and runs `services/tick.py`, an orchestrator over an ordered list of steps that starts empty.
  - `api/index.py` entry point. Vercel detects the FastAPI app there and serves every path to it, so there is no rewrite: a rewrite to `/api/index` makes the app see that path and 404 every route.
  - `vercel.json`: the daily cron at `0 2 * * *` (10:00 SGT) and `includeFiles` for the function.
- **Done when:** tests check 401 for a missing or wrong secret or token, and 200 for a valid request.

### T06 · Fake Telegram test harness
`test/t06-fake-telegram-harness` · Stories US-1.2 · NFR-9 · Depends on T04, T05
- **Scope:**
  - `tests/fake_telegram.py`: a `BaseRequest` subclass that records every Bot API call and returns made-up responses for `getMe`, `sendMessage`, `editMessageText`, `editMessageReplyMarkup`, `deleteMessage` and `answerCallbackQuery`.
  - Factories for group/private text messages, `/book` photos, callback queries and replies.
  - A pytest fixture that wires the application to the fake and to the test database.
- **Done when:** a self-test sends an update through a throwaway handler and checks the recorded calls.

### T07 · Telegram setup and dev scripts
`chore/t07-telegram-scripts` · Stories US-1.3 · FR-OPS-3 · Depends on T05
- **Scope:**
  - `scripts/setup_telegram.py`:
    - `setWebhook`, with the secret, `allowed_updates=[message, callback_query]` and `drop_pending_updates`;
    - `setMyCommands` for members (default scope) and for the admin (chat scope).
  - `scripts/dev_polling.py`: deletes the webhook, then runs long polling plus the tick every minute.
- **Done when:** both scripts run against a test bot. `--help` documents their arguments.

## E6 — Cost engine

### T08 · Money and allocation
`feat/t08-money-allocation` · Stories US-6.3 · FR-LED-1 · Depends on T01
- **Scope:** `badminton/domain/money.py`
  - `allocate(total_cents, weights)` uses the largest-remainder method. Ties break by ascending key.
  - `fmt_sgd(cents)`.
  - `parse_amount(text)` accepts `14.8`, `$14.80` and `14`, and rejects values outside $0.01–$500.
- **Done when:**
  - Tests prove the parts always add up to the total, e.g. 1480 cents over 3 people gives 494/493/493.
  - Tests cover zero and empty weights, determinism, and parsing edge cases.

### T09 · SGT time utilities
`feat/t09-time-utils` · Stories US-4.1 · NFR-7 · Depends on T01
- **Scope:** `badminton/domain/timeutil.py`
  - The fixed UTC+8 timezone, and an injectable `now()`.
  - SGT date of a timestamp, and month start/end.
  - The date a session's card is due.
  - `fmt_session(start, end)` → "Sun 4 Oct, 11:00–13:00".
  - `parse_hours(text)`: 0.5-hour steps, at most 6.
- **Done when:** unit tests cover month boundaries, the due-date calculation and the formatting.

### T10 · Ledger: court and transfers
`feat/t10-ledger-court-transfers` · Stories US-6.1, US-6.3, US-6.4 · FR-LED-2, 5, 6, 7 · Depends on T08
- **Scope:** `badminton/domain/ledger.py`
  - Input dataclasses for the ledger.
  - `compute_ledger(..., as_of)` produces dated entries and balances.
  - Court payer credits and equal attendee debits, only for sessions that have ended and aren't cancelled.
  - Sessions with no attendees are excluded and flagged.
  - Transfer entries.
- **Done when:** tests cover the split, cancelled and future sessions, the no-attendee case, and that balances sum to zero.

### T11 · Ledger: shuttle batches
`feat/t11-ledger-shuttle-batches` · Stories US-6.2, US-5.3 · FR-LED-3, FR-LED-4, FR-SH-4 · Depends on T10
- **Scope:**
  - Assign each session to a batch by `starts_at`.
  - A closed batch credits its payer and debits players by minutes played, dated when it closed.
  - A closed batch with no hours stays with its payer.
  - The open batch is excluded, and reports pending hours per member.
- **Done when:**
  - A regression test recreates the sheet's 16 Aug 2026 batch ($80 over 50h, i.e. alice $19.20 … frank $3.20).
  - Tests cover the batch boundary and the "Last session" start point.

### T12 · Debt simplification
`feat/t12-debt-simplification` · Stories US-7.1 · FR-INV-2 · Depends on T08
- **Scope:** `badminton/domain/settle.py`
  - `simplify_debts(balances, min_cents=100)` greedily matches the biggest debtor with the biggest creditor.
  - The result is deterministic.
- **Done when:**
  - Tests show everyone ends at zero, give or take the threshold.
  - The FR-INV-2 example in requirements §4.6 produces exactly its 4 transfers.
  - Amounts below the threshold carry forward.

### T13 · Monthly statement builder
`feat/t13-monthly-statement` · Stories US-7.3, US-7.5 · FR-INV-5, FR-INV-7 · Depends on T09, T11
- **Scope:** `badminton/domain/statement.py`
  - Per-member statement: opening balance, sessions, closed batches, payments, adjustments (vs the previous snapshot), and closing balance.
  - Group totals for the month.
- **Done when:** tests cover a month with activity, a month with none, and an edit to an already-invoiced month showing up as an adjustment.

### T14 · Ledger repository
`feat/t14-ledger-repository` · Stories US-6.4 · FR-LED-7 · Depends on T04, T11
- **Scope:** `badminton/services/repo.py` loads members, sessions with bookings and attendance, batches, and transfers into the domain inputs. Helpers:
  - `get_balances(as_of)`
  - `pending_hours(member)`
  - `last_hourly_rate()`
- **Done when:** integration tests on Postgres give the same results as the pure ledger for a seeded scenario.

## E2 — Members

### T15 · Member registry and basic commands
`feat/t15-member-registry` · Stories US-2.1, US-2.2, US-2.3, US-9.3 · FR-MEM-1, 2, 4, 6, 7, FR-MOD-4, FR-OPS-5 · Depends on T06
- **Scope:** `badminton/services/members.py`
  - Upsert a member from a Telegram user, refreshing their username.
  - Mention/handle formatting.
  - A chat filter that only accepts the configured group or a private chat.
  - Commands: `/start` (links the account, enables DMs), `/name`, `/help` (shows admin commands only to the admin), and `/chatid`.
- **Done when:** bot-flow tests cover each command, plus ignoring a foreign chat.

### T19 · Member admin commands
`feat/t19-member-admin-commands` · Stories US-2.4 · FR-MEM-5 · Depends on T16, T17
- **Scope:**
  - `/addmember <name>` creates a guest member.
  - `/members` lists members with buttons for [Rename] (via a reply prompt) and [Deactivate]/[Reactivate].
  - Each change is announced in the group and audited.
- **Done when:** tests cover adding, renaming and deactivating, deactivated members being hidden from pickers, and refusal for non-admins.

### T29 · `/balance` and `/upcoming`
`feat/t29-balance-upcoming` · Stories US-2.5, US-5.3 · FR-LED-8, FR-LED-9 · Depends on T14, T15
- **Scope:**
  - `/balance` shows the caller's balance and pending shuttle hours. T31 adds unpaid lines.
  - `/upcoming` lists scheduled sessions.
- **Done when:** tests cover a member with credit, one with debt, and one with no history.

## E3 — Court booking

### T17 · Wizard drafts and reply prompts
`feat/t17-drafts-and-prompts` · Stories US-3.4, US-9.2 · FR-BK-9, FR-MOD-2 · Depends on T15
- **Scope:** `badminton/services/drafts.py`
  - Create, read, update and delete drafts, each with an owner and an expiry.
  - Send a ForceReply prompt that is tied to a draft.
  - Route text replies to the draft's handler, then delete the reply and the prompt.
  - A tick step deletes expired drafts and their messages.
- **Done when:** tests cover routing a reply, ignoring replies from a non-owner, and cleaning up expired drafts.

### T20 · Calendar and hour keyboards
`feat/t20-calendar-hour-keyboards` · Stories US-3.2 · FR-BK-3, FR-BK-4 · Depends on T09
- **Scope:** `badminton/bot/keyboards.py`
  - Month calendar with ‹ › navigation and past days disabled.
  - Hour grid from 07:00 to 22:00.
  - Compact callback encode/decode, kept under 64 bytes.
- **Done when:** unit tests cover the layout, month rollover, disabled days and callback size.

### T21 · Booking wizard: date, time, duration
`feat/t21-booking-wizard-schedule` · Stories US-3.1, US-3.2 · FR-BK-1 to FR-BK-5 · Depends on T17, T20
- **Scope:**
  - A `/book` photo caption starts a draft with the sender as payer and the photo's `file_id` stored.
  - The wizard message steps through calendar → hour → [1h][2h][Other], where Other uses a reply prompt.
  - Taps from anyone other than the submitter get an alert.
- **Done when:** a bot-flow test walks through to the price step, including the Other hours path and a stranger's tap being refused.

### T22 · Booking wizard: price and confirm
`feat/t22-booking-wizard-confirm` · Stories US-3.2 · FR-BK-6, FR-BK-8 · Depends on T21
- **Scope:**
  - Suggested price = last hourly rate × hours. [Other] asks for an amount via a reply prompt.
  - A confirm/cancel summary.
  - On confirm, create the session and booking, and edit the message into the permanent record, including when the card will be posted.
- **Done when:** a bot-flow test runs from `/book` to a stored booking. Cancelling stores nothing.

### T23 · Same-day session merge
`feat/t23-booking-session-merge` · Stories US-3.3 · FR-BK-7 · Depends on T22
- **Scope:**
  - Detect a scheduled session on the same date that overlaps or runs back-to-back.
  - Offer [Add to …] / [Separate session].
  - Recalculate the session's start and end from its bookings.
- **Done when:** tests cover back-to-back, overlapping, a gap (no prompt), and both choices.

### T24 · Booking cancellation
`feat/t24-booking-cancel` · Stories US-3.5 · FR-BK-10 · Depends on T16, T22
- **Scope:**
  - A [Cancel booking] button on the booking record, usable by the submitter or the admin before the session starts.
  - A session left with no bookings is cancelled. If its attendance card was already posted, the card is closed.
  - Announced in the group and audited.
- **Done when:** tests cover the allowed roles, refusal for others, refusal after the start time, and session cancellation.

### T42 · Share the wizard card refresh
`claude/dazzling-noether-dpbhbz` · Stories US-3.4 · FR-BK-9 · Depends on T17, T41
- **Scope:** `badminton/services/drafts.py`
  - `show_draft` edits a wizard's card in place. `/book`, `/edit`, `/settle` and `/shuttle_edit` use it instead of their own copies.
  - An edit that changes nothing, such as a double tap, makes Telegram answer "message is not modified". That is ignored; any other error is still raised.
- **Done when:** tests cover the edit, an unchanged card being harmless (directly and from a double tap in `/settle`), and other errors still being raised.

### T43 · Read booking screenshots (OCR); pin bookings
`claude/inspiring-euler-fdophp` · Stories US-3.1, US-3.2, US-3.7 · FR-BK-1 to FR-BK-8, FR-BK-10, FR-BK-12, FR-ATT-6 · Depends on T23, T24, T27, T42
- **Scope:**
  - `/book` asks for the screenshot with a reply prompt. A photo captioned `/book` still works.
  - `badminton/domain/screenshot.py` reads the venue, slots and courts off the OCR text. `badminton/services/ocr.py` runs Tesseract (the tesserocr wheel, with the English model in `badminton/tessdata`).
  - The wizard shows the booking with a suggested price to confirm: the court rate (`DEFAULT_COURT_RATE_CENTS`) × the court hours. ✏️ Edit changes the date, time, hours, venue, courts or price. It asks for the date and time only if they couldn't be read.
  - Bookings store their venue and courts (migration 0005).
  - On confirm, the bot posts the screenshot captioned with the booking and pins it silently. Cancelling the booking, or locking its session, unpins it.
- **Done when:** tests cover the parser (12-hour times, the year from the weekday, two courts, gaps), Tesseract on a real screenshot, the prompt and caption paths, editing, the posted and pinned record, and unpinning.

### T44 · Rename `/book` to `/court`
`feat/t44-court-command` · Stories US-3.1 · FR-BK-1, FR-MOD-1 · Depends on T43
- **Scope:** a court booking starts with `/court`, or a screenshot captioned `/court`, instead of `/book`.
  - The command menu, `/help`, the `/start` and `/upcoming` texts, the guard's caption filter, the README and the requirements say `/court`.
  - `/book`, as text or a caption, is answered with a pointer to `/court`, then removed from the group. `/court` in a private chat says to send it in the group.
  - A captioned command is matched like a text command: in any case, and only with this bot's @handle or none.
  - One constant names the command for the menu, the handlers, the caption check and the messages; a test checks every menu command has a handler.
- **Done when:** the booking tests run through `/court`, and tests cover the `/book` pointer (text and caption), captions for other bots or in other cases, and `/court` in a private chat.

## E4 — Attendance and locking

### T25 · Attendance card
`feat/t25-attendance-card` · Stories US-4.1, US-4.2, US-4.3 · FR-ATT-2 to FR-ATT-5 · Depends on T22
- **Scope:**
  - Card text: session, bookings and payers, In and Out lists, and "no answer = not playing". No mentions.
  - [✅ I'm in] [❌ I'm out] upsert the tapper's answer (creating the member if needed) and edit the card.
  - Taps at or after `ends_at` are refused.
- **Done when:** tests cover toggling, a new member being created on first tap, and refusal after the end.

### T26 · Attendance card scheduling
`feat/t26-attendance-scheduling` · Stories US-4.1, US-3.6, US-1.4 · FR-ATT-1, FR-BK-11, NFR-3 · Depends on T25
- **Scope:**
  - A tick step posts cards that are due by SGT date. It claims each one with `UPDATE … WHERE card_sent_at IS NULL RETURNING` before sending.
  - Confirming a booking inside the reminder window posts the card straight away.
- **Done when:** tests show running the tick twice posts once, a missed day is caught up, and an inside-window booking posts immediately.

### T27 · Session finalisation
`feat/t27-session-finalize` · Stories US-4.4, US-4.5 · FR-ATT-6, FR-ATT-7 · Depends on T14, T16, T25
- **Scope:**
  - Finalisation is triggered by the tick step or by a tap after the end. It claims `locked_at`, removes the buttons and adds 🔒.
  - It posts the summary: number of players, court cost each, and shuttle hours each with the batch number.
  - If no one attended, the admin gets a DM.
- **Done when:** tests show it happens once per session, the summary figures match the ledger, and the no-attendee DM is sent.

## E5 — Shuttlecock batches

### T28 · Shuttle purchase
`feat/t28-shuttle-purchase` · Stories US-5.1, US-5.2 · FR-SH-1 to FR-SH-3 · Depends on T14, T16, T17
- **Scope:**
  - `/shuttle <price>` (admin) → payer picker → [Next session]/[Last session] → confirm.
  - Store the batch.
  - Announce the new batch together with the closed batch's split: total hours, $/h, and each player's hours and amount.
  - Audited.
- **Done when:** tests cover the announcement figures, both start-point options, and refusal for non-admins.

### T41 · Waiting shuttle batches and member purchases
`claude/dreamy-cerf-pz28gc` · Stories US-5.1 to US-5.5, US-8.5 · FR-SH-1 to FR-SH-7, FR-LED-4, FR-LED-8 · Depends on T28, T38
- **Scope:**
  - `shuttle_batches.opened_at`, and a nullable `starts_at`: both are empty while a purchase waits. The database allows at most one waiting batch. The ledger opens and closes batches by `opened_at`; existing batches are backfilled from `created_at`, so no figure changes.
  - `/shuttle <price>` for every member, in the group: the sender pays (the admin, anywhere, picks the payer), the purchase waits, and nothing closes. It's refused while a batch is waiting. The buyer or the admin can cancel it from its card until it's in use.
  - `/shuttle_finished` (admin): [Next session]/[Last session] → the open batch closes with its split announced, and the waiting batch takes over.
  - `/shuttle_edit` (admin): change any batch's price or payer (with the recalculated split for a closed batch), remove a waiting batch, or undo `/shuttle_finished`.
  - `/balance` shows the waiting batch.
- **Done when:** tests cover a member's purchase in the group (refused by DM) and the admin's anywhere, the one-waiting refusal, cancelling by the payer or the admin and refusal for anyone else, races on Record, cancel, remove and undo, finishing from either start point, the first batch, every correction, and the migration both ways with rows.

## E7 — Monthly invoicing and settlement

### T30 · Invoice generation and group card
`feat/t30-invoice-generation` · Stories US-7.1, US-7.4 · FR-INV-1 to FR-INV-3, FR-INV-6, FR-INV-7, FR-MEM-6 · Depends on T12, T13, T14, T16
- **Scope:** `badminton/services/invoice.py`
  - Generate an invoice for a given period, once only, enforced by a unique period.
  - Settle-up lines come from `simplify_debts`.
  - Store each member's closing balance.
  - Post a group card with the month's totals and `@payer → @payee $X` lines, without buttons until T31.
  - Mark the previous invoice's unpaid lines as carried into this one.
- **Done when:** tests cover running twice (one invoice), the line amounts, handle formatting, and carry-over.

### T31 · Invoice Paid toggle
`feat/t31-invoice-paid-toggle` · Stories US-7.2 · FR-INV-4, FR-LED-8 · Depends on T29, T30
- **Scope:**
  - A [✅ Paid] button per line. The payer, payee or admin can toggle it; it creates or deletes the transfer and edits the card.
  - `/balance` lists unpaid lines.
- **Done when:** tests cover toggling twice, refusal for others, balances updating, and a carried-over line being disabled.

### T32 · DM statements
`feat/t32-invoice-dm-statements` · Stories US-7.3, US-7.5, US-2.2 · FR-INV-5, FR-MEM-3 · Depends on T30
- **Scope:**
  - When an invoice is generated, DM each member with `dm_enabled` and some activity or balance their statement, using the T13 builder.
  - If a member has blocked the bot, set `dm_enabled=false` and carry on with the rest.
- **Done when:** tests cover statement content, skipping members with no activity, and the blocked case.

### T33 · Monthly schedule and `/invoice`
`feat/t33-invoice-schedule` · Stories US-7.1, US-7.4, US-1.4 · FR-INV-1 · Depends on T32
- **Scope:**
  - A tick step that, on or after the 1st (SGT), generates last month's invoice if it doesn't exist yet.
  - `/invoice` (admin) generates it on demand.
- **Done when:** tests cover the 1st, a missed 1st caught up on the 2nd, the command, and refusal for non-admins.

## E8 — Admin corrections and transparency

### T16 · Admin guard, audit log, announcements
`feat/t16-admin-guard-audit` · Stories US-8.4, US-2.2 · FR-ADM-1, FR-ADM-5, FR-ADM-6, FR-MEM-3 · Depends on T15
- **Scope:**
  - An `admin_only` handler decorator.
  - `services/audit.py` with `record(actor, action, before, after)`.
  - `services/notify.py` with `announce(text)` to the group and `dm(member, text)`, which handles a blocked bot.
- **Done when:** tests cover refusal for non-admins, audit rows being written, and the blocked-DM case.

### T34 · Admin: edit attendance
`feat/t34-admin-edit-attendance` · Stories US-8.1 · FR-ADM-2, FR-ADM-3 · Depends on T14, T16, T17, T25
- **Scope:**
  - `/edit` → recent-session picker → an ✅/⬜ toggle for each active member → [Save].
  - Announce the diff with the recalculated court split, and audit it.
  - Works on locked sessions too.
- **Done when:** tests cover the diff text, recalculated figures, a locked session, and refusal for non-admins.

### T35 · Admin: edit bookings, cancel session
`feat/t35-admin-edit-booking` · Stories US-8.2 · FR-ADM-2, FR-ADM-3 · Depends on T24, T34
- **Scope:**
  - From `/edit` → [Bookings]: change price (reply prompt), payer (picker) or hours ([1h][2h][Other]).
  - [Cancel session].
  - Every change is announced and audited.
- **Done when:** tests cover each kind of edit, the session span being recalculated, and cancellation removing the session from the ledger.

### T36 · Admin: manual transfer
`feat/t36-admin-manual-transfer` · Stories US-8.3 · FR-ADM-4 · Depends on T14, T16, T17
- **Scope:**
  - `/settle` → from picker → to picker → amount (reply prompt) → confirm.
  - Store the transfer, announce it, and audit it.
- **Done when:** tests cover balances updating, refusing the same person on both sides, and refusal for non-admins.

## E9 — Group moderation

### T18 · Group message guard
`feat/t18-group-message-guard` · Stories US-9.1 · FR-MOD-1, FR-MOD-3 · Depends on T17
- **Scope:**
  - A last-priority handler that deletes group messages from non-admins that aren't handled commands, `/book` photos, or active prompt replies.
  - It never deletes admin or service messages.
  - If it lacks delete rights, it logs a warning.
- **Done when:** tests cover plain text, a sticker and an unknown command (all deleted), and an admin message, a service message and a prompt reply (all kept).

### T45 · Only group members use the bot in private chat
`feat/t45-group-member-check` · Stories US-9.3, US-9.4 · FR-MEM-9, NFR-4 · Depends on T44
- **Scope:** commands are moving to private chats (T46, T47), and anyone can message a public bot, so the bot checks who it's talking to first.
  - A `members_only` decorator, like `admin_only` (`badminton/bot/admin.py`), lets a private-chat command through only for the admin or a member of the group: someone `getChatMember` on `GROUP_CHAT_ID` reports as the owner, an administrator, a member, or restricted but still a member. Anyone else is told the bot is for the group's members, and no member record is made for them. With no `GROUP_CHAT_ID` set, only the admin gets through.
  - It wraps `/start`, `/name`, `/help`, `/balance` and `/upcoming`. In the group it does nothing, and `/chatid` stays open (FR-OPS-5).
  - A failed lookup (user not found, or the bot is no longer in the group) counts as not a member and is logged. Telegram only guarantees the lookup for a bot that is a group admin; this one stays an admin to pin bookings.
  - `tests/fake_telegram.py` answers `getChatMember`: a member by default, with a way to make someone an outsider.
  - Requirements: a new FR-MEM-9; a new US-9.4, "As the group, we want only our members to record bookings and purchases, so that outsiders can't change our balances"; NFR-4. README: a troubleshooting entry for a member the bot doesn't recognise, and *Moving the bot to another group* says members must be in the new group to use the bot privately.
- **Done when:** tests cover an outsider refused with no member record made, a member and a restricted member let through, a restricted ex-member and a failed lookup refused, the admin let through without a lookup, and no lookup for commands in the group.

### T46 · Member commands in private chat
`feat/t46-member-commands-private` · Stories US-2.3, US-2.5, US-3.1, US-5.1, US-5.5 · FR-BK-1, FR-BK-8, FR-BK-9, FR-SH-1, FR-SH-6, FR-MEM-7 · Depends on T45
- **Scope:** members use the bot in a private chat with it; the group gets the results.
  - `/court` (or a screenshot captioned `/court`), `/shuttle`, `/balance`, `/upcoming`, `/name` and `/help` work only in a private chat, behind `members_only`. In the group, each gets a short reply saying to send it to the bot privately, with a button that opens the chat (`https://t.me/<bot username>`).
  - Booking: the prompt, the screenshot and the wizard are in the private chat, and the screenshot handler only listens there. On confirm, the record (the screenshot captioned with the booking, with 🗑 Cancel booking) is posted in the group and pinned, as now. In the private chat, the wizard becomes a one-line confirmation instead of disappearing, and the member's screenshot stays.
  - Shuttle: the card is in the private chat. Once the purchase is recorded, the group announcement carries 🗑 Cancel purchase, so the payer or the admin can still cancel it while it waits.
  - Prompts in a private chat don't @mention the member. `/start`, `/help` and the `/book` pointer say to send commands to the bot privately.
  - Requirements: FR-BK-1, FR-BK-8, FR-BK-9, FR-SH-1, FR-SH-6, FR-MEM-7, §6.1 and C-2. README: the Commands table, and step 7's "press **Start**" becomes how members use the bot.
- **Done when:** the booking, shuttle, `/balance`, `/upcoming`, `/name` and `/help` tests run from private chats; tests cover the group reply for each command (as text and as a captioned photo), the record posted and pinned in the group, the private confirmation, and cancelling a purchase from the announcement as the payer, as the admin and as someone else.

### T47 · Admin commands in private chat
`feat/t47-admin-commands-private` · Stories US-2.4, US-7.4, US-8.1 to US-8.5, US-9.2 · FR-ADM-6, FR-MOD-2 · Depends on T46
- **Scope:**
  - `/edit`, `/shuttle_finished`, `/shuttle_edit`, `/settle`, `/addmember`, `/members` and `/invoice` work only in the admin's private chat. In the group they get the same reply as member commands. Announcements still go to the group (FR-ADM-3).
  - No prompt is asked in the group any more, so the reply-prompt handler (`prompts.handle_reply`) only listens in private chats, and replies in the group never query the database (NFR-1).
  - Requirements: FR-ADM-6, FR-MOD-2 and §6.1. README: the Commands table.
- **Done when:** the admin flow tests run from the admin's private chat, and tests cover admin commands in the group and a reply in the group that looks up no draft and makes no Telegram call.

### T48 · Let the group chat freely
`feat/t48-group-chat` · Stories US-9.1, US-1.3 · FR-MOD-1, FR-MOD-3, FR-OPS-3, FR-OPS-4 · Depends on T47
- **Scope:** the group becomes a normal chat. The bot posts its results there, handles taps on their buttons, and answers its commands with the reply from T46. It deletes no one's messages and ignores everything else.
  - Remove the guard (`badminton/bot/handlers/guard.py` and its registration in `badminton/bot/handlers/__init__.py`). `caption_command` and `is_booking_photo` move to `booking.py`.
  - The bot needs only *Pin messages*: README step 7, *Moving the bot to another group*, §3.1, C-5 and FR-OPS-4 say so, and the "Messages aren't deleted" troubleshooting entry goes.
  - `scripts/setup_telegram.py` publishes member commands for private chats only and admin commands for the admin's private chat only, and clears the group menus it published before, so the group offers none. The README drops the steps that rerun it for the group's menu.
  - `/help` drops "In the group, only commands are allowed; other messages are removed."
  - Requirements: FR-MOD-1 and FR-MOD-3 say the above (IDs kept); US-9.1 becomes "As the group, we want to chat freely, with the bot posting only its results".
- **Done when:** `tests/bot/test_guard.py` is replaced by tests showing that plain text, stickers, photos, replies, unknown commands and other bots' commands in the group get no Telegram call, from a member and from the admin; `tests/test_scripts.py` covers the new menus and the cleared group menus.

## E1 — Documentation

### T37 · Setup and deployment guide
`docs/t37-setup-guide` · Stories US-1.3 · FR-OPS-4 · Depends on T07, T32 (brought forward at the owner's request)
- **Scope:** README covering:
  - BotFather;
  - adding the bot to the group as an admin with *Delete messages*;
  - `/chatid`;
  - creating the Neon database and running `alembic upgrade head`;
  - Vercel project and environment variables;
  - `setup_telegram.py`;
  - the optional cron-job.org job (every 15 min);
  - local development with `dev_polling.py`;
  - a manual end-to-end checklist.
- **Done when:** someone else can follow it from scratch and get a working bot in a test group.

## E10 — Migration from the spreadsheet

### T38 · Opening balances and spreadsheet import
`feat/t38-spreadsheet-import` · Stories US-10.1, US-10.2 · FR-MIG-1 to FR-MIG-4 · Depends on T14, T16, T37
- **Scope:**
  - An `opening_balances` table (one signed amount per member), loaded into the ledger as an opening-balance entry and shown on statements.
  - `badminton/domain/sheet.py`: reproduce the `Total` sheet exactly from the `Items` rows, and round each balance to cents that sum to zero.
  - `scripts/import_spreadsheet.py`: dry run by default, `--member NAME=ID`, `--apply`, `--no-announce`. The unused tube becomes the open batch; unmatched players become guests.
  - The import checks the tally with the ledger before saving, and refuses a second import or one that would count something twice.
- **Done when:** tests cover the sheet maths and each refused row, rounding to a zero sum, the import's matching and refusals, a rollback when the tally is off, and the unused tube being billed by hours once the next batch is bought.

### T39 · Link a guest to a Telegram account
`feat/t39-link-guests` · Stories US-2.4 · FR-MEM-8 · Depends on T19, T38
- **Scope:**
  - `link_guest`: move everything the Telegram member did onto the guest (bookings, attendance, batches, transfers, invoice lines and balances, opening balance, audit actor), then delete it and give the guest its Telegram identity.
  - `/members`: a 🔗 button on guests → pick the Telegram member (newest first) → confirm with both balances → link, announce and audit.
- **Done when:** tests cover each moved fact, a session both records answered, payments between the two, the linked balance being the sum, the admin linking their own account, stale or empty pickers, and refusal for non-admins.

### T40 · Linked guest's name; member list closes after a change
`feat/t40-linked-guest-name` · Stories US-2.4 · FR-MEM-8 · Depends on T39
- **Scope:** when a guest is linked, the merged member takes the account's display name (their Telegram first name, or what they set with `/name`) instead of keeping the guest's spreadsheet name. The confirm step, audit row and docs say so.
  - After any change from `/members` (rename, deactivate, reactivate, link), the list closes without buttons, so scrolling past it later can't tap one by accident; `/members` opens a fresh one.
- **Done when:** tests cover the linked member's name, the confirm text, `/balance`, and the member list closing after a rename, a deactivation and a link.

## E11 — Future enhancements

Not planned yet. An idea here gets a task ID, a branch and a row in the Summary once it's needed.

### Move the bot to another group
Today this is manual (README, *Moving the bot to another group*), and what was posted in the old group stays behind with its buttons ignored. An admin command could finish the move:
- Repost what's still live in the new group and update the stored chat and message IDs: the booking records of upcoming sessions (pinned again), attendance cards already up, a waiting shuttle purchase, and the latest invoice with its unpaid lines.
- Let edits to a chat the bot has left fail quietly. `attendance.refresh_card`, `invoice.refresh_invoice_card` and `booking.refresh_record` only ignore `BadRequest`; the `Forbidden` Telegram sends instead stops `finalize_session` from locking a session and `run_invoice` from sending statements.
