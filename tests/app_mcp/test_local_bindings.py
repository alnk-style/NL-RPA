from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app import models
from app.database import Base
from app.engine.app_mcp.local_manager import (
    LocalAppContext,
    LocalAppError,
    LocalAppManager,
    LocalAppState,
)
from app.models.mcp import MCPServer

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class BindingConnector:
    async def capabilities(self) -> dict[str, Any]:
        return {"success": True, "error": None, "data": {"actions": []}}

    async def auth_status(self) -> dict[str, Any]:
        return {
            "success": True,
            "error": None,
            "data": {"connected": True},
        }

    async def observe(
        self,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {"success": True, "error": None, "data": {}}

    async def act(
        self,
        action: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {"success": True, "error": None, "data": {}}

    async def verify(
        self,
        assertion: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {"success": True, "error": None, "data": {}}

    async def aclose(self) -> None:
        return None


class BindingRegistry:
    async def test_server(
        self,
        server_id: int,
        *,
        user_id: int | None = None,
        workspace_id: int | None = None,
        session: Any = None,
    ) -> dict[str, Any]:
        return {"success": True, "tool_count": 5}


async def _database(
    tmp_path: Path,
) -> tuple[async_sessionmaker[AsyncSession], Any, int]:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'bindings.db').as_posix()}",
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
                    id=14,
                    owner_id=7,
                    name="Owner Other",
                    slug="other",
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
        server = MCPServer(
            user_id=7,
            workspace_id=12,
            name="Generic Desktop",
            transport="http",
            url="http://127.0.0.1:1/mcp",
            config={
                "managed_by": "local_app_manager",
                "local_app_id": "generic_desktop",
                "auto_start": True,
            },
        )
        session.add(server)
        await session.commit()
        await session.refresh(server)
        broken_id = server.id
    return factory, engine, broken_id


def _settings(*, isolation: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        CREDENTIAL_ENCRYPTION_KEY=(
            "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="
        ),
        WORKSPACE_ISOLATION=isolation,
    )


def _manager(
    tmp_path: Path,
    session_factory: Any,
    *,
    isolation: bool = True,
) -> LocalAppManager:
    connector = BindingConnector()
    return LocalAppManager(
        project_root=PROJECT_ROOT,
        apps_root=PROJECT_ROOT / "mcp_apps",
        logs_root=tmp_path / "logs",
        session_factory=session_factory,
        registry=BindingRegistry(),
        connectors={"desktop": connector, "windows": connector},
        settings=_settings(isolation=isolation),
        platform_name="win32",
        process_factory=lambda *_args, **_kwargs: (
            (_ for _ in ()).throw(
                AssertionError("in_process app spawned a process")
            )
        ),
    )


def test_first_authenticated_start_binds_owner_and_workspace(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        session_factory, engine, _ = await _database(tmp_path)
        manager = _manager(tmp_path, session_factory)
        context = LocalAppContext(
            user_id=7,
            workspace_id=12,
            public_base_url="http://127.0.0.1:8012",
        )
        try:
            await manager.scan()
            runtime = await manager.start("generic_desktop", context)
            assert runtime.server_id is not None
            async with session_factory() as session:
                server = await session.get(MCPServer, runtime.server_id)
                assert server is not None
                assert server.config["managed_by"] == "local_app_manager"
                assert server.config["local_app_id"] == "generic_desktop"
                assert server.config["host_mode"] == "in_process"
                assert server.config["owner_user_id"] == 7
                assert server.config["workspace_id"] == 12
                assert server.config["auto_start"] is False

            binding = await manager.binding_info(
                "generic_desktop",
                context,
            )
            assert binding["bound"] is True
            assert binding["is_owner"] is True
            assert binding["auto_start"] is False
            assert binding["workspace_bound"] is True
        finally:
            await manager.close_all()
            await engine.dispose()

    asyncio.run(scenario())


def test_other_user_or_workspace_cannot_take_binding(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        session_factory, engine, _ = await _database(tmp_path)
        manager = _manager(tmp_path, session_factory)
        owner = LocalAppContext(
            user_id=7,
            workspace_id=12,
            public_base_url="http://127.0.0.1:8012",
        )
        try:
            await manager.scan()
            await manager.start("generic_desktop", owner)
            for conflicting in (
                LocalAppContext(
                    user_id=8,
                    workspace_id=13,
                    public_base_url="http://127.0.0.1:8012",
                ),
                LocalAppContext(
                    user_id=7,
                    workspace_id=14,
                    public_base_url="http://127.0.0.1:8012",
                ),
            ):
                with pytest.raises(LocalAppError) as captured:
                    await manager.start("generic_desktop", conflicting)
                assert captured.value.code == "local_app_owner_conflict"
            with pytest.raises(LocalAppError) as captured:
                await manager.stop(
                    "generic_desktop",
                    LocalAppContext(user_id=8, workspace_id=13),
                )
            assert captured.value.code == "local_app_owner_conflict"

            other_binding = await manager.binding_info(
                "generic_desktop",
                LocalAppContext(user_id=8, workspace_id=13),
            )
            assert other_binding["bound"] is True
            assert other_binding["is_owner"] is False
            assert other_binding["can_manage"] is False
            assert "owner_user_id" not in other_binding
            assert "workspace_id" not in other_binding
        finally:
            await manager.close_all()
            await engine.dispose()

    asyncio.run(scenario())


def test_auto_start_only_uses_persisted_bound_opt_in(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        session_factory, engine, _ = await _database(tmp_path)
        manager = _manager(tmp_path, session_factory)
        owner = LocalAppContext(
            user_id=7,
            workspace_id=12,
            public_base_url="http://127.0.0.1:8012",
        )
        try:
            await manager.scan()
            await manager.start("generic_desktop", owner)
            await manager.stop("generic_desktop", owner)
            updated = await manager.update_binding(
                "generic_desktop",
                auto_start=True,
                context=owner,
            )
            assert updated["auto_start"] is True
        finally:
            await manager.close_all()

        restarted = _manager(tmp_path, session_factory)
        try:
            await restarted.scan()
            await restarted.auto_start()
            apps = {app.app_id: app for app in restarted.list_apps()}
            assert apps["generic_desktop"].state == LocalAppState.RUNNING
            assert (
                apps["generic_desktop"].host_mode
                == "in_process"
            )
            assert apps["demo_app"].state == LocalAppState.DISCOVERED
            assert restarted.scan_errors == []
        finally:
            await restarted.close_all()
            await engine.dispose()

    asyncio.run(scenario())


def test_unbound_or_corrupt_binding_is_not_auto_started(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        session_factory, engine, broken_id = await _database(tmp_path)
        manager = _manager(tmp_path, session_factory)
        try:
            await manager.scan()
            await manager.auto_start()
            apps = {app.app_id: app for app in manager.list_apps()}
            assert apps["generic_desktop"].state == LocalAppState.DISCOVERED
            async with session_factory() as session:
                broken = await session.get(MCPServer, broken_id)
                assert broken is not None
                assert broken.enabled is True
            unbound = await manager.binding_info(
                "generic_desktop",
                LocalAppContext(user_id=7, workspace_id=12),
            )
            assert unbound["bound"] is False
            assert unbound["auto_start"] is False
        finally:
            await manager.close_all()
            await engine.dispose()

    asyncio.run(scenario())


def test_unbind_stops_runtime_and_keeps_shared_connector(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        session_factory, engine, _ = await _database(tmp_path)
        manager = _manager(tmp_path, session_factory)
        owner = LocalAppContext(
            user_id=7,
            workspace_id=12,
            public_base_url="http://127.0.0.1:8012",
        )
        connector = manager.connectors["desktop"]
        try:
            await manager.scan()
            await manager.start("generic_desktop", owner)
            with pytest.raises(LocalAppError) as captured:
                await manager.remove_binding(
                    "generic_desktop",
                    LocalAppContext(user_id=8, workspace_id=13),
                )
            assert captured.value.code == "local_app_owner_conflict"

            result = await manager.remove_binding(
                "generic_desktop",
                owner,
            )
            assert result["bound"] is False
            assert manager.in_process_runtime("generic_desktop") is None
            assert getattr(connector, "closed", False) is False
            async with session_factory() as session:
                servers = list(
                    (
                        await session.execute(
                            select(MCPServer).where(
                                MCPServer.name == "Generic Desktop"
                            )
                        )
                    ).scalars()
                )
                assert servers
                assert servers[0].config.get("owner_user_id") is None
                assert servers[0].config.get("auto_start") is False

            rebinding_context = LocalAppContext(
                user_id=8,
                workspace_id=13,
                public_base_url="http://127.0.0.1:8012",
            )
            rebound = await manager.start(
                "generic_desktop",
                rebinding_context,
            )
            assert rebound.state == LocalAppState.RUNNING
            rebound_binding = await manager.binding_info(
                "generic_desktop",
                rebinding_context,
            )
            assert rebound_binding["is_owner"] is True
        finally:
            await manager.close_all()
            await engine.dispose()

    asyncio.run(scenario())


def test_workspace_isolation_disabled_binds_null_workspace(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        session_factory, engine, _ = await _database(tmp_path)
        manager = _manager(
            tmp_path,
            session_factory,
            isolation=False,
        )
        context = LocalAppContext(
            user_id=7,
            workspace_id=None,
            public_base_url="http://127.0.0.1:8012",
        )
        try:
            await manager.scan()
            runtime = await manager.start("generic_desktop", context)
            async with session_factory() as session:
                server = await session.get(MCPServer, runtime.server_id)
                assert server is not None
                assert server.config["workspace_id"] is None
        finally:
            await manager.close_all()
            await engine.dispose()

    asyncio.run(scenario())
