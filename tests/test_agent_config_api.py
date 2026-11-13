from __future__ import annotations

import asyncio
from urllib.parse import quote

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.api.agent_config import router as agent_config_router
from app.api.dependencies import get_current_user
from app.database import Base, get_db
from app.models.agent import DesktopAgent
from app.models.user import User


async def _database() -> tuple[AsyncSession, object]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    return session_factory(), engine


async def _seed_user_and_agent(
    session: AsyncSession,
) -> tuple[User, DesktopAgent]:
    user = User(
        id=1,
        username="config-user",
        email="config-user@example.com",
        hashed_password="hash",
    )
    agent = DesktopAgent(
        id=1,
        user_id=user.id,
        name="DESKTOP-01",
        platform="windows",
        version="0.1.0",
        capabilities=["window"],
        status="online",
        token_hash="hash",
    )
    session.add_all([user, agent])
    await session.commit()
    await session.refresh(agent)
    return user, agent


class FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)


class FakeConnectionManager:
    def __init__(self) -> None:
        self.websocket = FakeWebSocket()
        self.online = True

    def is_online(self, agent_id: int) -> bool:
        return self.online and agent_id == 1

    def get(self, agent_id: int):
        if self.is_online(agent_id):
            return self.websocket
        return None


def _app(
    session: AsyncSession,
    user: User,
    manager: FakeConnectionManager,
) -> FastAPI:
    app = FastAPI()
    app.include_router(agent_config_router)
    app.state.connection_manager = manager

    async def override_db():
        yield session

    async def override_user() -> User:
        return user

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    return app


async def _request(
    app: FastAPI,
    method: str,
    path: str,
    **kwargs,
):
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        return await client.request(method, path, **kwargs)


def test_get_and_put_agent_config_hot_updates_online_agent() -> None:
    async def scenario() -> tuple[dict, dict, dict, DesktopAgent]:
        session, engine = await _database()
        try:
            user, agent = await _seed_user_and_agent(session)
            manager = FakeConnectionManager()
            app = _app(session, user, manager)

            initial = await _request(
                app,
                "GET",
                "/api/agents/1/config",
            )
            updated = await _request(
                app,
                "PUT",
                "/api/agents/1/config",
                json={
                    "allowed_apps": ["notepad.exe"],
                    "allowed_doc_dirs": [r"C:\Users\test\Documents"],
                },
            )
            await session.refresh(agent)
            return (
                initial.json(),
                updated.json(),
                manager.websocket.sent[-1],
                agent,
            )
        finally:
            await session.close()
            await engine.dispose()

    initial, updated, message, agent = asyncio.run(scenario())

    assert initial == {
        "allowed_apps": [],
        "allowed_doc_dirs": [],
    }
    assert updated == {
        "allowed_apps": ["notepad.exe"],
        "allowed_doc_dirs": [r"C:\Users\test\Documents"],
    }
    assert message["type"] == "config.update"
    assert message["config"] == updated
    assert agent.allowed_apps == ["notepad.exe"]
    assert agent.allowed_doc_dirs == [r"C:\Users\test\Documents"]


def test_add_and_remove_apps_and_dirs() -> None:
    async def scenario() -> tuple[dict, dict, dict, dict]:
        session, engine = await _database()
        try:
            user, _agent = await _seed_user_and_agent(session)
            manager = FakeConnectionManager()
            app = _app(session, user, manager)
            app_path = r"C:\Tools\notepad.exe"
            dir_path = r"C:\Users\test\Documents"

            added_app = await _request(
                app,
                "POST",
                "/api/agents/1/config/apps",
                json={"app": app_path},
            )
            removed_app = await _request(
                app,
                "DELETE",
                ("/api/agents/1/config/apps/" f"{quote(app_path, safe='')}"),
            )
            added_dir = await _request(
                app,
                "POST",
                "/api/agents/1/config/dirs",
                json={"dir": dir_path},
            )
            removed_dir = await _request(
                app,
                "DELETE",
                ("/api/agents/1/config/dirs/" f"{quote(dir_path, safe='')}"),
            )
            return (
                added_app.json(),
                removed_app.json(),
                added_dir.json(),
                removed_dir.json(),
            )
        finally:
            await session.close()
            await engine.dispose()

    added_app, removed_app, added_dir, removed_dir = asyncio.run(scenario())

    assert added_app["allowed_apps"] == [r"C:\Tools\notepad.exe"]
    assert removed_app["allowed_apps"] == []
    assert added_dir["allowed_doc_dirs"] == [r"C:\Users\test\Documents"]
    assert removed_dir["allowed_doc_dirs"] == []


def test_installed_apps_api_returns_icons_from_discovered_entries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    import app.api.agent_config as agent_config_module
    from app.engine.installed_apps import InstalledApp, app_key_for_path

    executable = tmp_path / "WPS Office" / "ksolaunch.exe"
    executable.parent.mkdir()
    executable.write_bytes(b"MZ")
    app_key = app_key_for_path(executable)
    discovered = (
        InstalledApp(
            key=app_key,
            name="WPS Office",
            executable_path=str(executable),
            source="start_menu",
        ),
    )
    monkeypatch.setattr(
        agent_config_module,
        "_cached_installed_apps",
        lambda refresh=False: discovered,
    )
    monkeypatch.setattr(
        agent_config_module,
        "_cached_app_icon",
        lambda app_key, executable_path: b"\x89PNG\r\n\x1a\nfake",
    )

    async def scenario() -> tuple[dict, object]:
        session, engine = await _database()
        try:
            user, _agent = await _seed_user_and_agent(session)
            app = _app(session, user, FakeConnectionManager())
            listed = await _request(
                app,
                "GET",
                "/api/agents/1/config/installed-apps",
            )
            icon = await _request(
                app,
                "GET",
                (
                    "/api/agents/1/config/installed-apps/"
                    f"{app_key}/icon"
                ),
            )
            return listed.json(), icon
        finally:
            await session.close()
            await engine.dispose()

    listed, icon = asyncio.run(scenario())

    assert listed["supported"] is True
    assert listed["apps"] == [
        {
            "key": app_key,
            "name": "WPS Office",
            "executable_path": str(executable),
            "source": "start_menu",
            "icon_url": (
                "/api/agents/1/config/installed-apps/"
                f"{app_key}/icon"
            ),
            "generated_app_id": None,
        }
    ]
    assert icon.status_code == 200
    assert icon.headers["content-type"] == "image/png"
    assert icon.content.startswith(b"\x89PNG")
