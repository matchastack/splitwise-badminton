# splitwise-badminton

A Telegram bot that tracks court bookings, attendance and shuttlecock batches for a badminton group, and posts a monthly settle-up.

- Requirements: [`docs/requirements.md`](docs/requirements.md)
- Task plan: [`tasks.md`](tasks.md)

## Development

You need [uv](https://docs.astral.sh/uv/) and Python 3.13.

```sh
uv sync                    # install runtime + dev dependencies
uv run ruff check          # lint
uv run ruff format --check # formatting
uv run pytest              # tests
```

Copy `.env.example` to `.env` and fill in the values for local runs.

Database tests need `TEST_DATABASE_URL` pointing at a **throwaway** Postgres, because its schema is dropped and rebuilt on every run. Without it they are skipped. Apply migrations to a real database with `DATABASE_URL=... uv run alembic upgrade head`.
