from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

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
    LocalAppManager,
    LocalAppState,
)
from app.engine.installed_app_mcp import create_installed_app_package
from app.engine.installed_apps import InstalledApp, app_key_for_path
from app.engine.app_mcp.scoped_desktop import ScopedDesktopAdapter


class FakeDesktopConnector:
    def __init__(self) -> None:
        self.actions: list[tuple[str, dict]] = []

    async def auth_status(self) -> dict:
        return {
            "success": True,
            "data": {"connected": True, "online_agents": [1]},
        }

    async def observe(self, payload: dict) -> dict:
        return {
            "success": True,
            "data": {"windows": [{"title": "WPS Office"}]},
        }

    async def act(self, action: dict, context: dict) -> dict:
        self.actions.append((action["action"], action["params"]))
        return {"success": True, "data": {"state": "window_found"}}


class FakeDiscoveryRegistry:
    async def test_server(
        self,
        server_id: int,
        *,
        user_id: int | None = None,
        workspace_id: int | None = None,
        session: Any = None,
    ) -> dict:
        return {"success": True, "tool_count": 4}


def test_generated_package_starts_in_process_and_executes(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "WPS Office" / "ksolaunch.exe"
    executable.parent.mkdir()
    executable.write_bytes(b"MZ")
    installed = InstalledApp(
        key=app_key_for_path(executable),
        name="WPS Office",
        executable_path=str(executable),
        source="start_menu",
    )
    generated = create_installed_app_package(
        installed,
        root=tmp_path / "generated",
    )
    builtin_root = tmp_path / "builtin"
    builtin_root.mkdir()
    connector = FakeDesktopConnector()

    async def scenario() -> None:
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{(tmp_path / 'generated.db').as_posix()}"
        )
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        async with session_factory() as session:
            session.add(
                models.User(
                    id=7,
                    username="generated",
                    email="generated@example.com",
                    hashed_password="hash",
                )
            )
            await session.commit()
        manager = LocalAppManager(
            project_root=tmp_path,
            apps_root=builtin_root,
            generated_apps_root=tmp_path / "generated",
            logs_root=tmp_path / "logs",
            session_factory=session_factory,
            registry=FakeDiscoveryRegistry(),
            connectors={"desktop": connector},
            settings=SimpleNamespace(
                WORKSPACE_ISOLATION=False,
                CREDENTIAL_ENCRYPTION_KEY=(
                    "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="
                ),
            ),
            platform_name="win32",
        )
        context = LocalAppContext(
            user_id=7,
            public_base_url="http://127.0.0.1:8000",
        )
        try:
            apps = {app.app_id: app for app in await manager.scan()}
            assert generated.app_id in apps
            assert apps[generated.app_id].host_mode == "in_process"
            runtime = await manager.start(generated.app_id, context)
            assert runtime.state == LocalAppState.RUNNING
            assert runtime.info.pid is None
            app_runtime = manager.in_process_runtime(generated.app_id)
            assert isinstance(app_runtime.adapter, ScopedDesktopAdapter)

            observed = await app_runtime.execute_tool(
                "app_observe",
                {},
                ToolCallContext(
                    user_id=7,
                    workflow_id=21,
                    execution_id=22,
                ),
            )
            launched = await app_runtime.execute_tool(
                "app_launch",
                {"wait_for_window": True},
                ToolCallContext(
                    user_id=7,
                    workflow_id=21,
                    execution_id=22,
                ),
            )
            assert observed.success is True
            assert launched.success is True
            assert connector.actions == [
                (
                    "launch_app",
                    {
                        "path": str(executable),
                        "wait_for_window": True,
                    },
                )
            ]
        finally:
            await manager.close_all()
            await engine.dispose()

    asyncio.run(scenario())
