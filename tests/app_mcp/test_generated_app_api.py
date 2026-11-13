from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401
from app.api.dependencies import get_current_user
from app.api.local_apps import (
    get_local_app_manager,
    router,
)
from app.api.workspace_deps import get_workspace_context
from app.database import Base, get_db
from app.engine.app_mcp.local_manager import (
    LocalAppContext,
    LocalAppRuntime,
    LocalAppState,
)
from app.engine.installed_apps import InstalledApp, app_key_for_path
from app.models.agent import DesktopAgent
from app.models.user import User
from app.security.workspaces import WorkspaceContext


class FakeManager:
    def __init__(self, generated_apps_root: Path) -> None:
        self.generated_apps_root = generated_apps_root
        self.calls: list[tuple[Any, ...]] = []
        self.last_scan = SimpleNamespace(apps=[], errors=[])

    @property
    def scan_errors(self):
        return self.last_scan.errors

    def list_apps(self):
        return self.last_scan.apps

    async def scan(self):
        self.calls.append(("scan",))
        return self.last_scan.apps

    async def start(
        self,
        app_id: str,
        context: LocalAppContext | None = None,
    ) -> LocalAppRuntime:
        self.calls.append(("start", app_id, context))
        from app.engine.app_mcp.local_manager import LocalAppInfo

        info = LocalAppInfo(
            app_id=app_id,
            name="WPS Office",
            description="Generated",
            version="0.1.0",
            platform="windows",
            tools_count=4,
            state=LocalAppState.RUNNING,
            url="http://127.0.0.1:8000/api/local-apps/wps/mcp",
            pid=None,
            port=None,
            host_mode="in_process",
        )
        return LocalAppRuntime(
            info=info,
            server_id=12,
            discovered_tool_count=4,
        )

    async def stop(
        self,
        app_id: str,
        context: LocalAppContext | None = None,
    ) -> bool:
        self.calls.append(("stop", app_id, context))
        return True

    async def remove_binding(
        self,
        app_id: str,
        context: LocalAppContext,
    ) -> dict:
        self.calls.append(("remove_binding", app_id, context))
        return {}


async def _database():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    session = factory()
    user = User(
        id=7,
        username="installed-app-user",
        email="installed@example.com",
        hashed_password="hash",
    )
    agent = DesktopAgent(
        id=3,
        user_id=7,
        name="DESKTOP-TEST",
        platform="windows",
        version="0.1.0",
        capabilities=["window"],
        status="online",
        token_hash="hash",
        allowed_apps=[],
        allowed_doc_dirs=[],
    )
    session.add_all([user, agent])
    await session.commit()
    return session, engine, user, agent


def _app(
    session: AsyncSession,
    user: User,
    manager: FakeManager,
) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.state.local_app_manager = manager
    workspace = SimpleNamespace(id=12)

    async def override_db():
        yield session

    async def override_user() -> User:
        return user

    async def override_workspace() -> WorkspaceContext:
        return WorkspaceContext(
            user=user,
            workspace=workspace,  # type: ignore[arg-type]
            isolation_enabled=False,
        )

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    app.dependency_overrides[get_workspace_context] = override_workspace
    app.dependency_overrides[get_local_app_manager] = lambda: manager
    return app


async def _request(app: FastAPI, method: str, path: str, **kwargs):
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        return await client.request(method, path, **kwargs)


def test_discovery_routes_return_generated_state_and_icon(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.api.local_apps as local_apps_module

    app_key = "a" * 64
    installed = InstalledApp(
        key=app_key,
        name="WPS Office",
        executable_path=r"D:\WPS\WPS Office\ksolaunch.exe",
        source="start_menu",
    )
    monkeypatch.setattr(
        local_apps_module,
        "cached_installed_apps",
        lambda refresh=False: (installed,),
    )
    monkeypatch.setattr(
        local_apps_module,
        "generated_integrations_by_key",
        lambda: {
            app_key: {
                "app_id": "wps_office_aaaaaaaa",
            }
        },
    )
    monkeypatch.setattr(
        local_apps_module,
        "cached_installed_app",
        lambda key: installed if key == app_key else None,
    )
    monkeypatch.setattr(
        local_apps_module,
        "cached_app_icon",
        lambda key, path: b"\x89PNG\r\n\x1a\nfake",
    )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: User(
        id=1,
        username="tester",
        email="tester@example.com",
        hashed_password="hash",
    )

    async def scenario():
        listed = await _request(
            app,
            "GET",
            "/api/local-apps/discoverable-apps",
        )
        icon = await _request(
            app,
            "GET",
            f"/api/local-apps/discoverable-apps/{app_key}/icon",
        )
        return listed, icon

    listed, icon = asyncio.run(scenario())

    assert listed.status_code == 200
    assert listed.json()["apps"][0]["generated_app_id"] == (
        "wps_office_aaaaaaaa"
    )
    assert icon.status_code == 200
    assert icon.headers["content-type"] == "image/png"


def test_create_and_delete_generated_app_syncs_allowlist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.api.local_apps as local_apps_module

    executable = tmp_path / "WPS Office" / "ksolaunch.exe"
    executable.parent.mkdir()
    executable.write_bytes(b"MZ")
    installed = InstalledApp(
        key=app_key_for_path(executable),
        name="WPS Office",
        executable_path=str(executable),
        source="start_menu",
    )
    monkeypatch.setattr(
        local_apps_module,
        "cached_installed_app",
        lambda key: installed if key == installed.key else None,
    )

    async def scenario() -> tuple[Any, Any, DesktopAgent, list[str]]:
        session, engine, user, agent = await _database()
        manager = FakeManager(tmp_path / "generated")
        app = _app(session, user, manager)
        try:
            created = await _request(
                app,
                "POST",
                (
                    "/api/local-apps/discoverable-apps/"
                    f"{installed.key}/create"
                ),
                json={"agent_id": 3},
            )
            await session.refresh(agent)
            allowed_after_create = list(agent.allowed_apps or [])
            app_id = created.json()["generated"]["app_id"]
            deleted = await _request(
                app,
                "DELETE",
                f"/api/local-apps/generated/{app_id}",
                params={
                    "agent_id": 3,
                    "remove_from_allowlist": True,
                },
            )
            await session.refresh(agent)
            return created, deleted, agent, allowed_after_create
        finally:
            await session.close()
            await engine.dispose()

    created, deleted, agent, allowed_after_create = asyncio.run(scenario())

    assert created.status_code == 200
    assert created.json()["app"]["host_mode"] == "in_process"
    assert created.json()["generated"]["template"] == "scoped_desktop_v1"
    assert allowed_after_create == [str(executable)]
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    assert agent.allowed_apps == []
