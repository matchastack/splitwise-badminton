from alembic import command

from tests.conftest import alembic_config


def test_migrations_round_trip_and_match_models(migrated_database_url: str) -> None:
    config = alembic_config(migrated_database_url)

    command.downgrade(config, "base")
    command.upgrade(config, "head")
    command.check(config)  # fails if the models have drifted from the migrations
