import importlib

import pytest


@pytest.mark.parametrize(
    "module",
    ["badminton", "alembic", "fastapi", "psycopg", "sqlalchemy.ext.asyncio", "telegram"],
)
def test_runtime_dependencies_import(module: str) -> None:
    importlib.import_module(module)
