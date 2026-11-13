from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app import models
from app.database import Base
from app.engine.app_mcp.contracts import ToolCallContext
from app.engine.app_mcp.local_manager import (
    LocalAppContext,
    LocalAppError,
    LocalAppManager,
    LocalAppState,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class SharedDesktopConnector:
    def __init__(self) -> None:
        self.closed = False
        self.actions: list[tuple[dict[str, Any], dict[str, Any]]] = []

    async def capabilities(self) -> dict[str, Any]:
        return {
            "success": True,
            "error": None,
            "data": {"actions": ["launch_app", "activate_window"]},
        }

    async def auth_status(self) -> dict[str, Any]:
        return {
            "success": True,
            "error": None,
            "data": {"connected": True, "online_agents": [3]},
        }

    async def observe(
        self,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "success": True,
            "error": None,
            "data": {
                "windows": [{"title": "Notepad"}],
                "elements": [],
                "context": dict(context or {}),
            },
        }

    async def act(
        self,
        action: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.actions.append((dict(action), dict(context or {})))
        return {
            "success": True,
            "error": None,
            "data": {
                "action": action.get("action"),
                "params": dict(action.get("params") or {}),
            },
        }

    async def verify(
        self,
        assertion: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {"success": True, "error": None, "data": assertion}

    async def aclose(self) -> None:
        self.closed = True


class FakeDiscoveryRegistry:
    def __init__(self) -> None:
        self.calls: list[int] = []

    async def test_server(
        self,
        server_id: int,
        *,
        user_id: int | None = None,
        workspace_id: int | None = None,
        session: Any = None,
    ) -> dict[str, Any]:
        self.calls.append(server_id)
        return {"success": True, "tool_count": 5}


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        CREDENTIAL_ENCRYPTION_KEY=(
            "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="
        ),
        WORKSPACE_ISOLATION=True,
    )


async def _session_factory(
    tmp_path: Path,
) -> tuple[
    async_sessionmaker[AsyncSession],
    Any,
]:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'in-process.db').as_posix()}",
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with factory() as session:
        session.add_all(
            [
                models.User(
                    id=7,
                    username="owner",
                    email="owner@example.com",
                    hashed_password="hash",
                ),
                models.User(
                    id=8,
                    username="other",
                    email="other@example.com",
                    hashed_password="hash",
                ),
                models.Workspace(
                    id=12,
                    owner_id=7,
                    name="Owner",
                    slug="default",
                    is_default=True,
                ),
                models.Workspace(
                    id=13,
                    owner_id=8,
                    name="Other",
                    slug="default",
                    is_default=True,
                ),
            ]
        )
        await session.commit()
    return factory, engine


def _manager(
    tmp_path: Path,
    *,
    session_factory: Any,
    registry: Any,
    connector: SharedDesktopConnector,
    process_factory: Any = None,
    platform_name: str = "win32",
    apps_root: Path | None = None,
) -> LocalAppManager:
    kwargs: dict[str, Any] = {}
    if process_factory is not None:
        kwargs["process_factory"] = process_factory
    return LocalAppManager(
        project_root=PROJECT_ROOT,
        apps_root=apps_root or PROJECT_ROOT / "mcp_apps",
        logs_root=tmp_path / "logs",
        session_factory=session_factory,
        registry=registry,
        connectors={
            "desktop": connector,
            "windows": connector,
        },
        settings=_settings(),
        platform_name=platform_name,
        **kwargs,
    )


def test_generic_desktop_starts_in_process_with_shared_connector(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        session_factory, engine = await _session_factory(tmp_path)
        connector = SharedDesktopConnector()
        registry = FakeDiscoveryRegistry()

        def unexpected_process(*_args: Any, **_kwargs: Any) -> None:
            raise AssertionError("in_process app must not spawn a subprocess")

        manager = _manager(
            tmp_path,
            session_factory=session_factory,
            registry=registry,
            connector=connector,
            process_factory=unexpected_process,
        )
        context = LocalAppContext(
            user_id=7,
            workspace_id=12,
            public_base_url="http://127.0.0.1:9123",
        )
        try:
            apps = {app.app_id: app for app in await manager.scan()}
            desktop = apps["generic_desktop"]
            assert desktop.state == LocalAppState.DISCOVERED
            assert desktop.host_mode == "in_process"
            assert desktop.transport_kind == "in_process"
            assert desktop.unsupported_reason is None

            first = await manager.start("generic_desktop", context)
            second = await manager.start("generic_desktop", context)
            assert first.state == LocalAppState.RUNNING
            assert first.pid is None
            assert first.port is None
            assert first.info.url == (
                "http://127.0.0.1:9123"
                "/api/local-apps/generic_desktop/mcp"
            )
            runtime = manager.in_process_runtime("generic_desktop")
            assert runtime is not None
            assert manager.in_process_runtime("generic_desktop") is runtime
            assert second.server_id == first.server_id
            assert runtime.adapter.backend.connector is connector

            probe = await runtime.execute_tool(
                "desktop_probe",
                {},
                ToolCallContext(
                    user_id=7,
                    workspace_id=12,
                    workflow_id=21,
                ),
            )
            action = await runtime.execute_tool(
                "desktop_activate",
                {"title": "Notepad"},
                ToolCallContext(
                    user_id=7,
                    workspace_id=12,
                    workflow_id=21,
                    execution_id=22,
                ),
            )
            missing = await runtime.execute_tool(
                "desktop_activate",
                {"title": "Notepad"},
                ToolCallContext(user_id=7, workspace_id=12),
            )
            assert probe.success is True
            assert action.success is True
            assert missing.success is False
            assert missing.error is not None
            assert missing.error.code == "missing_context"
            assert connector.actions[0][0]["action"] == "activate_window"
            assert connector.actions[0][1]["workflow_id"] == 21

            stopped = await manager.stop("generic_desktop", context)
            assert stopped is True
            assert manager.in_process_runtime("generic_desktop") is None
            assert connector.closed is False
        finally:
            await manager.close_all()
            await engine.dispose()

    asyncio.run(scenario())


def test_non_windows_platform_is_explicitly_unsupported(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        session_factory, engine = await _session_factory(tmp_path)
        connector = SharedDesktopConnector()
        manager = _manager(
            tmp_path,
            session_factory=session_factory,
            registry=FakeDiscoveryRegistry(),
            connector=connector,
            platform_name="linux",
        )
        try:
            apps = {app.app_id: app for app in await manager.scan()}
            assert apps["demo_app"].state == LocalAppState.UNSUPPORTED
            assert (
                apps["generic_desktop"].state
                == LocalAppState.UNSUPPORTED
            )
            assert "Windows" in (apps["demo_app"].last_error or "")
            with pytest.raises(LocalAppError) as captured:
                await manager.start(
                    "generic_desktop",
                    LocalAppContext(user_id=7, workspace_id=12),
                )
            assert captured.value.code == "unsupported_platform"
        finally:
            await manager.close_all()
            await engine.dispose()

    asyncio.run(scenario())


def test_scan_removal_closes_in_process_runtime_only(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        session_factory, engine = await _session_factory(tmp_path)
        apps_root = tmp_path / "mcp_apps"
        apps_root.mkdir()
        shutil.copytree(
            PROJECT_ROOT / "mcp_apps" / "generic_desktop",
            apps_root / "generic_desktop",
        )
        connector = SharedDesktopConnector()
        manager = _manager(
            tmp_path,
            session_factory=session_factory,
            registry=FakeDiscoveryRegistry(),
            connector=connector,
            apps_root=apps_root,
        )
        try:
            await manager.scan()
            await manager.start(
                "generic_desktop",
                LocalAppContext(
                    user_id=7,
                    workspace_id=12,
                    public_base_url="http://127.0.0.1:9123",
                ),
            )
            assert manager.in_process_runtime("generic_desktop") is not None
            shutil.rmtree(apps_root / "generic_desktop")
            await manager.scan()
            assert manager.in_process_runtime("generic_desktop") is None
            assert connector.closed is False
        finally:
            await manager.close_all()
            await engine.dispose()

    asyncio.run(scenario())


def test_close_all_releases_in_process_runtime(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        session_factory, engine = await _session_factory(tmp_path)
        connector = SharedDesktopConnector()
        manager = _manager(
            tmp_path,
            session_factory=session_factory,
            registry=FakeDiscoveryRegistry(),
            connector=connector,
        )
        try:
            await manager.scan()
            await manager.start(
                "generic_desktop",
                LocalAppContext(
                    user_id=7,
                    workspace_id=12,
                    public_base_url="http://127.0.0.1:9123",
                ),
            )
            assert manager.in_process_runtime("generic_desktop") is not None
            await manager.close_all()
            assert manager.in_process_runtime("generic_desktop") is None
            assert connector.closed is False
        finally:
            await manager.close_all()
            await engine.dispose()

    asyncio.run(scenario())
