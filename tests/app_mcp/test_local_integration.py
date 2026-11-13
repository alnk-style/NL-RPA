from __future__ import annotations

import asyncio
import socket
import time
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app import models  # noqa: F401
from app.database import Base
from app.engine.app_mcp.local_manager import (
    LocalAppContext,
    LocalAppManager,
    LocalAppState,
)
from app.engine.mcp_registry import MCPRegistry
from app.models.mcp import MCPServer
from app.models.user import User
from app.models.workspace import Workspace

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _port_is_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _wait_for_port_closed(port: int) -> None:
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if not _port_is_open(port):
            return
        time.sleep(0.05)
    raise AssertionError(f"local App MCP port {port} remained open")


def test_demo_app_real_process_lifecycle_and_mcp_call(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{(tmp_path / 'integration.db').as_posix()}",
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
                User(
                    id=7,
                    username="local-user",
                    email="local@example.com",
                    hashed_password="hash",
                )
            )
            await session.commit()
            session.add(
                Workspace(
                    id=12,
                    owner_id=7,
                    name="Local",
                    slug="default",
                    is_default=True,
                )
            )
            await session.commit()

        registry = MCPRegistry(
            session_factory=session_factory,
            config=SimpleNamespace(
                MCP_CLIENT=True,
                MCP_CALL_TIMEOUT=5.0,
                MCP_MAX_CONCURRENCY=2,
                MCP_DEFAULT_RISK="mutating",
                MCP_STDIO_ENABLED=False,
                CREDENTIAL_ENCRYPTION_KEY=(
                    "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="
                ),
            ),
        )
        manager = LocalAppManager(
            project_root=PROJECT_ROOT,
            apps_root=PROJECT_ROOT / "mcp_apps",
            logs_root=tmp_path / "logs",
            session_factory=session_factory,
            registry=registry,
        )
        context = LocalAppContext(user_id=7, workspace_id=12)
        ports: list[int] = []
        try:
            apps = {app.app_id: app for app in await manager.scan()}
            assert apps["demo_app"].state == LocalAppState.DISCOVERED
            assert apps["generic_desktop"].state == LocalAppState.DISCOVERED
            assert apps["generic_desktop"].host_mode == "in_process"

            runtime = await manager.start("demo_app", context)
            assert runtime.state == LocalAppState.RUNNING
            assert runtime.port is not None
            ports.append(runtime.port)
            assert runtime.server_id is not None
            assert runtime.discovered_tool_count == 3

            async with session_factory() as session:
                server = (
                    await session.execute(select(MCPServer))
                ).scalar_one()
                names = {tool["name"] for tool in server.tools}
                assert names == {
                    "demo_status",
                    "demo_execute",
                    "demo_export",
                }

            status = await registry.call_tool(
                runtime.server_id,
                "demo_status",
                {},
                context={
                    "user_id": 7,
                    "workspace_id": 12,
                    "workflow_id": 21,
                },
            )
            assert status.success is True
            assert status.audit_id is not None
            assert status.result is not None
            assert status.result["structured"]["ok"] is True

            stopped = await manager.stop("demo_app", context)
            assert stopped is True
            _wait_for_port_closed(runtime.port)
            assert (
                await manager.status("demo_app", context)
            ).state == LocalAppState.STOPPED
            async with session_factory() as session:
                server = (
                    await session.execute(select(MCPServer))
                ).scalar_one()
                assert server.enabled is False

            restarted = await manager.restart("demo_app", context)
            assert restarted.state == LocalAppState.RUNNING
            assert restarted.pid != runtime.pid
            assert restarted.server_id == runtime.server_id
            assert restarted.port is not None
            ports.append(restarted.port)
        finally:
            await manager.close_all()
            await registry.aclose()
            await engine.dispose()
            for port in ports:
                _wait_for_port_closed(port)

    asyncio.run(scenario())
