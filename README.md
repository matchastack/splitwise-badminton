# splitwise-badminton

A Telegram bot that tracks court bookings, attendance and shuttlecock batches for a badminton group, and posts a monthly settle-up. It replaces a hand-kept spreadsheet.

- Requirements: [`docs/requirements.md`](docs/requirements.md)
- Task plan: [`tasks.md`](tasks.md)

## What it does

- **Bookings:** a member sends the court booking screenshot with the caption `/book`, then taps through date, time, hours and price. The sender is recorded as the payer.
- **Attendance:** 2 days before each session the bot posts a card with **[✅ I'm in] [❌ I'm out]**. People can change their answer until the session ends; no answer counts as out.
- **Locking:** when the session ends, attendance locks and the bot posts the split. Everyone who played shares the court cost and gets hours added to the current shuttle batch (1 hour = 1 share).
- **Shuttles:** the admin records each purchase with `/shuttle`. That closes the previous batch, which is then split by hours played.
- **Monthly settle-up:** on the 1st of each month the bot posts who pays whom, using the fewest transfers, with a **✅ paid** button on each line. Everyone who has opened a chat with the bot also gets a private statement.
- **Admin corrections:** every change the admin makes is announced in the group.
- **Commands-only group:** the bot deletes anything in the group that isn't a command.

## Setup guide

Everything runs on free tiers:

| Service | What it's for | Free tier |
|---|---|---|
| [Telegram](https://telegram.org) | The bot | Free |
| [Neon](https://neon.tech) | Postgres database | Free plan |
| [Vercel](https://vercel.com) | Hosts the webhook and the daily cron | Hobby plan |
| [GitHub](https://github.com) | Code, and CI with Actions | Free |
| [cron-job.org](https://cron-job.org) *(optional)* | Runs the scheduler more often than once a day | Free |

You also need [uv](https://docs.astral.sh/uv/getting-started/installation/) and Python 3.13 on your computer. They're used to run the database migrations and the Telegram setup script.

Allow about 30 minutes. Do the steps in order: some later steps need values from earlier ones.

### 1. Create the bot (Telegram → @BotFather)

1. In Telegram, open [@BotFather](https://t.me/BotFather) and send `/newbot`.
2. Choose a display name (e.g. *Badminton Bot*) and a username ending in `bot` (e.g. `our_badminton_bot`).
3. BotFather replies with a **token** like `123456789:AAH…`. This is `BOT_TOKEN`. Keep it secret: anyone with it controls the bot.
4. Optional: `/setdescription` and `/setuserpic` to make it look nice.
5. **Leave privacy mode on.** The bot will be a group admin, and admins receive every message anyway.

> Lost or leaked the token? Send `/revoke` to BotFather to get a new one, then update `BOT_TOKEN` in Vercel and redeploy (step 5).

### 2. Generate two secrets

`WEBHOOK_SECRET` proves to the bot that requests really come from Telegram. `CRON_SECRET` protects the scheduler endpoint. Generate each one with:

```sh
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Run it twice and keep both values. The webhook secret may only contain `A–Z a–z 0–9 _ -`, which this command guarantees.

### 3. Create the database (Neon)

1. Sign up at [neon.tech](https://neon.tech) and create a project:
   - **Region:** *AWS Asia Pacific (Singapore)*. `vercel.json` runs the bot in Vercel's Singapore region (`sin1`), so the database sits next to it.
   - **Postgres version:** the default.
2. On the project dashboard, click **Connect**.
   - Turn **Connection pooling** on.
   - Copy the connection string. It looks like `postgresql://user:password@ep-xxx-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require`. This is `DATABASE_URL`.
   - Use the **pooled** string, with `-pooler` in the host. Serverless functions open a new connection on every request, and the pooler handles that.
3. Create the tables from your computer:

   ```sh
   git clone https://github.com/matchastack/splitwise-badminton.git
   cd splitwise-badminton
   uv sync
   DATABASE_URL='postgresql://…-pooler…' uv run alembic upgrade head
   ```

   Run `alembic upgrade head` again whenever a change adds a migration (files in `migrations/versions/`). Do it before deploying that change.

### 4. Find your Telegram user ID

`ADMIN_TELEGRAM_IDS` lists the people allowed to run admin commands, by numeric Telegram ID. To find yours, message [@userinfobot](https://t.me/userinfobot); it replies with your ID, e.g. `123456789`.

Alternatively, once the bot is deployed, send `/chatid` to it in a private chat. In a private chat, the chat ID *is* your user ID. For more than one admin, separate the IDs with commas: `123456789,987654321`.

### 5. Deploy to Vercel

1. Sign up at [vercel.com](https://vercel.com) with GitHub. Then go to **Add New… → Project** and import `splitwise-badminton`.
2. Under **Configure Project**:
   - **Framework Preset:** `Other`.
   - **Root Directory:** leave as `./`. Build and output settings can stay empty: `vercel.json` routes `/api/*` to the Python function.
   - Vercel reads the Python version (3.13) and dependencies from `pyproject.toml` / `uv.lock`.
3. Open **Environment Variables** and add these, for the Production environment:

   | Variable | Value |
   |---|---|
   | `BOT_TOKEN` | from step 1 |
   | `WEBHOOK_SECRET` | first secret from step 2 |
   | `CRON_SECRET` | second secret from step 2 |
   | `DATABASE_URL` | pooled Neon string from step 3 |
   | `ADMIN_TELEGRAM_IDS` | from step 4 |
   | `GROUP_CHAT_ID` | leave it out for now (step 7) |

4. Click **Deploy**. When it finishes, note the **production domain** under **Settings → Domains**, e.g. `https://splitwise-badminton.vercel.app`.
5. **Settings → Cron Jobs** should list `/api/cron` daily at `0 2 * * *` (10:00 in Singapore). Vercel sends `CRON_SECRET` with each run automatically.
6. Check the deployment protection setting:
   - Under **Settings → Deployment Protection**, the default *Standard Protection* keeps the production domain public, which is what Telegram needs.
   - Always give Telegram the **production** domain, not a preview or deployment URL.

> Vercel applies environment variable changes only to new deployments. After changing a variable, go to **Deployments → ⋯ → Redeploy**.

### 6. Connect the bot to the deployment

Create a `.env` file in the repo, copying `.env.example`, with the same values you gave Vercel. Then run:

```sh
uv run --env-file .env scripts/setup_telegram.py --base-url https://splitwise-badminton.vercel.app
```

This does two things:
- Registers the webhook (`<base-url>/api/telegram`) with your `WEBHOOK_SECRET`.
- Publishes the command menu: member commands for everyone, plus admin commands for you.

It prints the webhook URL when done.

### 7. Set up the Telegram group

1. Create the group, or use an existing one, and add the bot as a member (search for its username).
2. Make the bot an **admin** with the **Delete messages** right. It needs this to keep the group commands-only. Other rights can be off. On mobile: group → *Edit → Administrators → Add Admin*.
3. Send `/chatid` in the group. The bot replies with a negative number like `-1001234567890`. This is `GROUP_CHAT_ID`.
4. Add `GROUP_CHAT_ID` in Vercel (**Settings → Environment Variables**) and **redeploy**. Add it to your `.env` too.
5. Run the setup script from step 6 again, so the admin command menu also appears for you in the group.
6. Optional: in BotFather, use `/setjoingroups` → **Disable**, so nobody can add your bot to other groups. The bot ignores other chats anyway.
7. Ask everyone to open a private chat with the bot and press **Start**. Telegram only lets a bot DM people who have done this, and the DM is how monthly statements arrive.

> If Telegram ever converts the group to a *supergroup* (it does this for some settings changes), the group ID changes. Run `/chatid` again and update `GROUP_CHAT_ID`.

### 8. Optional: run the scheduler more often (cron-job.org)

On Vercel's free plan the cron runs **once a day**, at some point between 10:00 and 10:59 Singapore time. That's enough for the basics:
- attendance cards go up 2 days ahead;
- the invoice is posted on the 1st;
- taps after a session ends are refused at the exact end time either way.

The only effect of a daily cron is that the "🔒 locked" summary can arrive up to a day late. To post it within about 15 minutes instead:

1. Create a free account at [cron-job.org](https://cron-job.org) and add a cron job:
   - **URL:** `https://splitwise-badminton.vercel.app/api/cron`
   - **Schedule:** every 15 minutes
   - **Advanced → Headers:** `Authorization` = `Bearer <your CRON_SECRET>`
2. Save. Every step of the scheduler is safe to run any number of times, so the Vercel cron and cron-job.org can both run.

### 9. Check that it works

1. In the group, send `/help`. The bot should reply. As the admin you also see admin commands.
2. Post a booking screenshot with the caption `/book` and go through the wizard. If the session is within 2 days, the attendance card appears straight away.
3. Tap **✅ I'm in** and check the card updates.
4. Send a normal text message from a non-admin account. The bot should delete it.
5. Trigger the scheduler by hand:

   ```sh
   curl -H "Authorization: Bearer <CRON_SECRET>" https://splitwise-badminton.vercel.app/api/cron
   ```

   It should return `{"ok":true,"steps":{…}}`.
6. Optional: as the admin, `/invoice` posts a settle-up for the current month right away.
   - The scheduled run on the 1st skips a month that already has an invoice.
   - In the first week of a month, before the scheduled run has posted the previous month's invoice, `/invoice` posts that one instead.
   - Anything that happens after a manual invoice is included in the next one, because balances carry over.

### Environment variables

| Variable | Required | Where to get it | What it does |
|---|---|---|---|
| `BOT_TOKEN` | yes | @BotFather → `/newbot` (step 1) | Authenticates the bot with Telegram |
| `WEBHOOK_SECRET` | yes | Generate (step 2) | Telegram sends it with each update; requests without it get 401 |
| `CRON_SECRET` | yes | Generate (step 2) | Required as `Authorization: Bearer …` on `/api/cron`; Vercel sends it automatically |
| `DATABASE_URL` | yes | Neon → Connect → pooled string (step 3) | Postgres connection. `postgresql://` and `postgres://` are accepted |
| `ADMIN_TELEGRAM_IDS` | yes | @userinfobot or `/chatid` in a DM (step 4) | Comma-separated user IDs allowed to run admin commands |
| `GROUP_CHAT_ID` | after step 7 | `/chatid` in the group | The one group the bot works in. Until it's set, only `/chatid` and private chats work |
| `REMINDER_DAYS_BEFORE` | no (default `2`) | — | How many days before a session the attendance card goes up |
| `DEFAULT_COURT_RATE_CENTS` | no (default `740`) | — | Hourly court rate used to suggest a price before any booking exists ($7.40/h) |
| `TEST_DATABASE_URL` | tests only | Local Postgres or a Neon branch | Throwaway database for `pytest` (its schema is dropped) |

### Troubleshooting

- **The bot doesn't reply.** Check the webhook:

  ```sh
  curl https://api.telegram.org/bot<BOT_TOKEN>/getWebhookInfo
  ```

  - `url` should be your production `/api/telegram`.
  - `last_error_message` explains failures:
    - `401` means `WEBHOOK_SECRET` in Vercel differs from the one the setup script used, or Deployment Protection is blocking the URL.
    - `500` means look at Vercel's **Logs** tab; often `DATABASE_URL` is wrong or migrations weren't run.
- **The bot replies in DMs but ignores the group.** `GROUP_CHAT_ID` is missing or wrong, or you didn't redeploy after setting it. Run `/chatid` in the group again.
- **Messages aren't deleted.** The bot isn't an admin, or lacks *Delete messages*.
- **Someone doesn't get statements.** They haven't pressed **Start** in a private chat with the bot, or they blocked it. Pressing Start again turns statements back on.
- **I ran the local polling script and now the deployed bot is silent.** Polling removes the webhook. Run `scripts/setup_telegram.py` again (step 6).

## Commands

| Command | Who | Where | What |
|---|---|---|---|
| `/book` (as a photo caption) | anyone | group | Record a court booking |
| `/balance` | anyone | anywhere | Your balance, shuttle hours so far and unpaid settle-ups |
| `/upcoming` | anyone | anywhere | Booked sessions |
| `/name <name>` | anyone | anywhere | Change the name the bot shows for you |
| `/start` | anyone | DM | Turn on monthly statements |
| `/help` | anyone | anywhere | What the bot can do |
| `/chatid` | anyone | anywhere | Show the chat ID (setup) |
| `/shuttle <price>` | admin | anywhere | Record a shuttlecock purchase |
| `/edit` | admin | anywhere | Fix a session's attendance or bookings, or cancel it |
| `/settle` | admin | anywhere | Record a payment made outside the bot |
| `/addmember <name>` | admin | anywhere | Add a guest who isn't on Telegram |
| `/members` | admin | anywhere | Rename or deactivate members |
| `/invoice` | admin | anywhere | Post this month's settle-up now |

## Development

You need [uv](https://docs.astral.sh/uv/) and Python 3.13.

```sh
uv sync                    # install runtime + dev dependencies
uv run ruff check          # lint
uv run ruff format --check # formatting
uv run pytest              # tests
```

Database tests need `TEST_DATABASE_URL` pointing at a **throwaway** Postgres, because its schema is dropped and rebuilt on every run. Without it they are skipped. Any Postgres 16+ works, for example:
- `docker run -e POSTGRES_PASSWORD=postgres -p 5432:5432 postgres:16`
- a separate Neon branch

**Running the bot locally:** use a separate test bot from BotFather and a Neon branch, so you don't disturb the real group:

```sh
uv run --env-file .env scripts/dev_polling.py   # long polling + the scheduler every minute
```

Polling removes the bot's webhook. Run `scripts/setup_telegram.py` again before going back to the deployed version.

**CI:** GitHub Actions runs lint, formatting and tests against Postgres 16 on every pull request (`.github/workflows/ci.yml`).

**Schema changes:** edit `badminton/db/models.py`, then generate a migration with `DATABASE_URL=… uv run alembic revision --autogenerate -m "…"`. Review it before committing. The tests fail if the models and migrations drift apart.
