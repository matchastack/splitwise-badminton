import importlib
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest
from telegram import Update
from telegram.ext import Application, TypeHandler

from badminton.config import Settings
from badminton.db.engine import create_engine, create_sessionmaker
from badminton.services import schedule, tick
from badminton.web import Runtime, app, get_runtime
from tests.fake_telegram import FakeTelegram

SETTINGS = Settings.from_env(
    {
        "BOT_TOKEN": "123:abc",
        "WEBHOOK_SECRET": "hook-secret",
        "DATABASE_URL": "postgresql://u:p@localhost/unused",
        "CRON_SECRET": "cron-secret",
        "ADMIN_TELEGRAM_IDS": "111",
    }
)
ROOT = Path(__file__).resolve().parents[1]
UPDATE = {
    "update_id": 1,
    "message": {
        "message_id": 10,
        "date": 1_790_000_000,
        "chat": {"id": 111, "type": "private"},
        "from": {"id": 111, "is_bot": False, "first_name": "zy"},
        "text": "hello",
    },
}


@dataclass(frozen=True)
class RecordingRuntime(Runtime):
    """Adds a handler that records every update, optionally failing afterwards."""

    seen: list[Update] = field(default_factory=list)
    fail: bool = False

    def application(self) -> Application:
        application = super().application()

        async def record(update: Update, _context: object) -> None:
            self.seen.append(update)
            if self.fail:
                raise RuntimeError("handler failed")

        application.add_handler(TypeHandler(Update, record))
        return application


def make_runtime(*, fail: bool = False) -> RecordingRuntime:
    sessionmaker = create_sessionmaker(create_engine(SETTINGS.database_url))
    return RecordingRuntime(SETTINGS, sessionmaker, FakeTelegram(), fail=fail)


@pytest.fixture
def runtime() -> RecordingRuntime:
    return make_runtime()


@pytest.fixture
async def client(runtime: RecordingRuntime) -> AsyncIterator[httpx.AsyncClient]:
    app.dependency_overrides[get_runtime] = lambda: runtime
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


def webhook_headers(secret: str = "hook-secret") -> dict[str, str]:
    return {"X-Telegram-Bot-Api-Secret-Token": secret}


@pytest.mark.parametrize("headers", [{}, webhook_headers("wrong")], ids=["missing", "wrong"])
async def test_webhook_rejects_bad_secret(
    client: httpx.AsyncClient, runtime: RecordingRuntime, headers: dict[str, str]
) -> None:
    response = await client.post("/api/telegram", json=UPDATE, headers=headers)

    assert response.status_code == 401
    assert runtime.seen == []


async def test_webhook_processes_update(
    client: httpx.AsyncClient, runtime: RecordingRuntime
) -> None:
    response = await client.post("/api/telegram", json=UPDATE, headers=webhook_headers())

    assert response.status_code == 200
    assert [update.update_id for update in runtime.seen] == [1]
    assert runtime.seen[0].effective_message.text == "hello"


async def test_webhook_returns_200_when_handler_fails(client: httpx.AsyncClient) -> None:
    app.dependency_overrides[get_runtime] = lambda: make_runtime(fail=True)

    response = await client.post("/api/telegram", json=UPDATE, headers=webhook_headers())

    assert response.status_code == 200


@pytest.mark.parametrize("body", [b"not json", b"[1, 2]"], ids=["invalid", "not-object"])
async def test_webhook_rejects_malformed_body(client: httpx.AsyncClient, body: bytes) -> None:
    response = await client.post("/api/telegram", content=body, headers=webhook_headers())

    assert response.status_code == 400


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer wrong"}, {"Authorization": "cron-secret"}],
    ids=["missing", "wrong", "no-bearer-prefix"],
)
async def test_cron_rejects_bad_token(client: httpx.AsyncClient, headers: dict[str, str]) -> None:
    response = await client.get("/api/cron", headers=headers)

    assert response.status_code == 401


async def test_cron_runs_steps_and_isolates_failures(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran: list[str] = []

    async def ok_step(context: tick.TickContext) -> None:
        assert context.settings is SETTINGS
        ran.append("ok")

    async def failing_step(context: tick.TickContext) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(
        schedule, "default_steps", lambda: [("broken", failing_step), ("works", ok_step)]
    )

    response = await client.get("/api/cron", headers={"Authorization": "Bearer cron-secret"})

    assert response.status_code == 200
    assert response.json() == {"ok": False, "steps": {"broken": "error", "works": "ok"}}
    assert ran == ["ok"]


async def test_cron_with_no_steps(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(schedule, "default_steps", list)

    response = await client.post("/api/cron", headers={"Authorization": "Bearer cron-secret"})

    assert response.status_code == 200
    assert response.json() == {"ok": True, "steps": {}}


@pytest.mark.parametrize("name", ["telegram", "cron"])
def test_vercel_entry_point_exposes_app(name: str) -> None:
    entry_app = importlib.import_module(f"api.{name}").app

    assert entry_app is app


def test_every_route_is_served_by_its_own_vercel_function() -> None:
    # Vercel serves api/<name>.py at /api/<name>. A rewrite to one shared function would hand
    # the app the rewritten path (/api/index), so no route would match and every request 404s.
    config = json.loads((ROOT / "vercel.json").read_text())
    paths = {route.path for route in app.routes}

    assert paths == {"/api/telegram", "/api/cron"}
    assert "rewrites" not in config
    assert set(config["functions"]) == {f"{path.removeprefix('/')}.py" for path in paths}
    for function in config["functions"]:
        assert (ROOT / function).is_file()
    assert {job["path"] for job in config["crons"]} <= paths
