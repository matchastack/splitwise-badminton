from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from alembic import command

from tests.conftest import alembic_config

SEP_1 = datetime(2026, 9, 1, tzinfo=UTC)
OCT_15 = datetime(2026, 10, 15, tzinfo=UTC)


def test_migrations_round_trip_and_match_models(migrated_database_url: str) -> None:
    config = alembic_config(migrated_database_url)

    command.downgrade(config, "base")
    command.upgrade(config, "head")
    command.check(config)  # fails if the models have drifted from the migrations


def test_batches_keep_their_dates_through_0004(migrated_database_url: str) -> None:
    """Upgrading backfills ``opened_at``; downgrading puts it back where the old ledger
    looks: a batch closes the previous one at its ``created_at``."""
    config = alembic_config(migrated_database_url)
    engine = sa.create_engine(migrated_database_url, poolclass=sa.pool.NullPool)

    def batch_dates(column: str) -> list[tuple[int, datetime]]:
        with engine.begin() as connection:
            rows = connection.execute(
                sa.text(f"SELECT id, {column} FROM shuttle_batches ORDER BY id")
            )
            return [tuple(row) for row in rows]

    try:
        command.downgrade(config, "0003")
        with engine.begin() as connection:
            connection.execute(sa.text("TRUNCATE members RESTART IDENTITY CASCADE"))
            connection.execute(sa.text("INSERT INTO members (display_name) VALUES ('alice')"))
            connection.execute(
                sa.text(
                    "INSERT INTO shuttle_batches (payer_member_id, amount_cents, starts_at, "
                    "created_at) VALUES (1, 8000, :start, :recorded)"
                ),
                {"start": SEP_1, "recorded": SEP_1 + timedelta(hours=10)},
            )

        command.upgrade(config, "head")
        assert batch_dates("opened_at") == [(1, SEP_1 + timedelta(hours=10))]

        with engine.begin() as connection:
            # Bought on 1 Oct, put in use on 15 Oct; another purchase is still waiting.
            connection.execute(
                sa.text(
                    "INSERT INTO shuttle_batches (payer_member_id, amount_cents, starts_at, "
                    "opened_at, created_at) VALUES (1, 7679, :start, :opened, :recorded)"
                ),
                {"start": OCT_15, "opened": OCT_15, "recorded": OCT_15 - timedelta(days=14)},
            )
            connection.execute(
                sa.text(
                    "INSERT INTO shuttle_batches (payer_member_id, amount_cents) VALUES (1, 50)"
                )
            )
        command.downgrade(config, "0003")

        assert batch_dates("created_at") == [(1, SEP_1 + timedelta(hours=10)), (2, OCT_15)]
    finally:
        command.upgrade(config, "head")
        with engine.begin() as connection:
            connection.execute(sa.text("TRUNCATE members RESTART IDENTITY CASCADE"))
        engine.dispose()
