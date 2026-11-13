from __future__ import annotations

import asyncio
import socket
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import uvicorn
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401
from app.database import Base
from app.engine.app_mcp.contracts import (
    ActionResult,
    AdapterEnvironment,
    AppProbe,
    ToolCallContext,
)
from app.engine.app_mcp.manifest import load_manifest
from app.engine.app_mcp.runtime import AppRuntime
from app.engine.app_mcp.server import AppMCPServer, create_http_app
from app.engine.mcp_client import MCPClient, MCPServerConfig, sdk_available
from app.engine.mcp_registry import MCPRegistry
from app.models.mcp import MCPServer, ToolCallAudit
from app.models.user import User
from mcp_apps.demo_app.adapter import DemoAppAdapter

PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_DIR = PROJECT_ROOT / "mcp_apps" / "demo_app"


class CountingDemoAdapter(DemoAppAdapter):
    def __init__(self, environment: AdapterEnvironment) -> None:
        super().__init__(environment)
        self.execute_calls = 0

    async def execute(self, action: str, args: dict) -> ActionResult:
        self.execute_calls += 1
        return await super().execute(action, args)


class BrokenAdapter:
    async def probe(self) -> AppProbe:
        return AppProbe(installed=True, running=True, version="0.1.0")

    async def observe(self) -> dict[str, Any]:
        return {}

    async def execute(self, action: str, args: dict) -> ActionResult:
        raise RuntimeError("secret C:\\private\\path token=abc")

    async def close(self) -> None:
        return None


class UnsafeFailureAdapter(BrokenAdapter):
    async def execute(self, action: str, args: dict) -> ActionResult:
        return ActionResult.fail(
            code="adapter_error",
            message=("Failed at C:\\private\\adapter.py password=hunter2"),
            data={
                "token": "hunter2",
                "path": "C:\\private\\adapter.py",
            },
        )


def _runtime(
    adapter: Any,
    export_root: Path,
    *,
    timeout_seconds: float = 5.0,
) -> AppRuntime:
    manifest = load_manifest(APP_DIR / "manifest.yaml")
    return AppRuntime(
        manifest=manifest,
        adapter=adapter,
        export_root=export_root,
        timeout_seconds=timeout_seconds,
        max_concurrency=1,
    )


def _start_http_server(
    runtime: AppRuntime,
) -> tuple[uvicorn.Server, threading.Thread, str]:
    server = AppMCPServer(runtime)
    context = ToolCallContext(
        request_id="test",
        user_id=1,
        workflow_id=42,
        execution_id=99,
    )
    app = create_http_app(server, default_context=context)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
    )
    uvicorn_server = uvicorn.Server(config)
    thread = threading.Thread(
        target=uvicorn_server.run,
        name="app-mcp-test-server",
        daemon=True,
    )
    thread.start()
    deadline = time.monotonic() + 5.0
    while not uvicorn_server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    if not uvicorn_server.started:
        raise RuntimeError("App MCP test server did not start")
    return uvicorn_server, thread, f"http://127.0.0.1:{port}/mcp"


def _stop_http_server(
    server: uvicorn.Server,
    thread: threading.Thread,
) -> None:
    server.should_exit = True
    thread.join(timeout=5)
    assert not thread.is_alive()


def test_server_tools_list_exposes_risk_annotations(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = DemoAppAdapter(
            AdapterEnvironment(
                app_dir=APP_DIR,
                export_root=tmp_path / "exports",
                connectors={},
                options={},
            )
        )
        runtime = _runtime(adapter, tmp_path / "exports")
        server = AppMCPServer(runtime)
        response = await server.handle(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            ToolCallContext(request_id="list"),
        )
        await runtime.close()

        assert response is not None
        tools = {tool["name"]: tool for tool in response["result"]["tools"]}
        assert tools["demo_status"]["annotations"]["readOnlyHint"] is True
        assert tools["demo_execute"]["annotations"]["readOnlyHint"] is False
        assert tools["demo_execute"]["_meta"]["risk_level"] == "mutating"

    asyncio.run(scenario())


def test_unknown_tool_and_invalid_arguments_return_is_error(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        adapter = DemoAppAdapter(
            AdapterEnvironment(
                app_dir=APP_DIR,
                export_root=tmp_path / "exports",
                connectors={},
                options={},
            )
        )
        runtime = _runtime(adapter, tmp_path / "exports")
        server = AppMCPServer(runtime)
        context = ToolCallContext(request_id="errors")
        unknown = await server.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "missing_tool", "arguments": {}},
            },
            context,
        )
        invalid = await server.handle(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "demo_execute",
                    "arguments": {"note": 9},
                },
            },
            context,
        )
        await runtime.close()

        assert unknown is not None
        assert unknown["result"]["isError"] is True
        assert unknown["result"]["structuredContent"]["error"]["code"] == "unknown_tool"
        assert invalid is not None
        assert invalid["result"]["isError"] is True
        assert (
            invalid["result"]["structuredContent"]["error"]["code"]
            == "invalid_arguments"
        )

    asyncio.run(scenario())


def test_adapter_exception_is_sanitized(tmp_path: Path) -> None:
    async def scenario() -> None:
        runtime = _runtime(BrokenAdapter(), tmp_path / "exports")
        server = AppMCPServer(runtime)
        response = await server.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "demo_status",
                    "arguments": {},
                },
            },
            ToolCallContext(request_id="broken"),
        )
        await runtime.close()

        assert response is not None
        result = response["result"]
        assert result["isError"] is True
        text = result["content"][0]["text"]
        assert "Traceback" not in text
        assert "secret" not in text
        assert "C:\\private" not in text
        assert result["structuredContent"]["error"]["code"] == "adapter_error"

    asyncio.run(scenario())


def test_structured_adapter_error_is_sanitized(tmp_path: Path) -> None:
    async def scenario() -> None:
        runtime = _runtime(UnsafeFailureAdapter(), tmp_path / "exports")
        server = AppMCPServer(runtime)
        response = await server.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "demo_status",
                    "arguments": {},
                },
            },
            ToolCallContext(request_id="unsafe"),
        )
        await runtime.close()

        assert response is not None
        result = response["result"]
        rendered = str(result)
        assert "C:\\private" not in rendered
        assert "hunter2" not in rendered
        assert "[redacted]" in rendered

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "prefer_sdk",
    [
        False,
        pytest.param(
            True,
            marks=pytest.mark.skipif(
                not sdk_available(),
                reason="official mcp SDK unavailable",
            ),
        ),
    ],
)
def test_existing_mcp_client_discovers_and_calls_demo_app(
    tmp_path: Path,
    prefer_sdk: bool,
) -> None:
    async def scenario() -> tuple[list[str], Any]:
        adapter = DemoAppAdapter(
            AdapterEnvironment(
                app_dir=APP_DIR,
                export_root=tmp_path / "exports",
                connectors={},
                options={},
            )
        )
        runtime = _runtime(adapter, tmp_path / "exports")
        uv_server, thread, url = _start_http_server(runtime)
        client = MCPClient(
            MCPServerConfig(
                name="demo-app",
                transport="http",
                url=url,
                timeout=5.0,
            ),
            prefer_sdk=prefer_sdk,
        )
        try:
            tools = await client.list_tools()
            call = await client.call_tool("demo_status", {})
            return [tool["name"] for tool in tools], call
        finally:
            await client.aclose()
            await runtime.close()
            _stop_http_server(uv_server, thread)

    names, call = asyncio.run(scenario())

    assert names == ["demo_status", "demo_execute", "demo_export"]
    assert call.success is True
    assert "demo_app" in call.result["text"]
    assert call.result["structured"]["ok"] is True


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        MCP_CLIENT=True,
        MCP_CALL_TIMEOUT=5.0,
        MCP_MAX_CONCURRENCY=2,
        MCP_DEFAULT_RISK="mutating",
        MCP_STDIO_ENABLED=False,
        CREDENTIAL_ENCRYPTION_KEY=("MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="),
    )


def test_registry_blocks_unauthorized_write_and_audits(
    tmp_path: Path,
) -> None:
    async def scenario() -> tuple[Any, Any, list[ToolCallAudit]]:
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
        adapter = CountingDemoAdapter(
            AdapterEnvironment(
                app_dir=APP_DIR,
                export_root=tmp_path / "exports",
                connectors={},
                options={},
            )
        )
        runtime = _runtime(adapter, tmp_path / "exports")
        uv_server, thread, url = _start_http_server(runtime)
        try:
            async with session_factory() as session:
                session.add(
                    User(
                        id=1,
                        username="tester",
                        email="tester@example.com",
                        hashed_password="hash",
                    )
                )
                session.add(
                    MCPServer(
                        user_id=1,
                        name="demo-app",
                        transport="http",
                        url=url,
                        enabled=True,
                        default_risk="mutating",
                        tools=[
                            {
                                "name": "demo_execute",
                                "risk_level": "mutating",
                                "authorized": False,
                                "confirmed": False,
                            }
                        ],
                    )
                )
                await session.commit()

            registry = MCPRegistry(
                session_factory=session_factory,
                config=_settings(),
            )
            denied = await registry.call_tool(
                "demo-app",
                "demo_execute",
                {"value": "blocked"},
                context={"user_id": 1, "workflow_id": 5},
            )
            assert denied.decision == "denied"
            assert adapter.execute_calls == 0

            authorized = await registry.authorize_tool(
                "demo-app",
                "demo_execute",
                authorized=True,
                user_id=1,
                authorized_by="user:1",
            )
            assert authorized["success"] is True

            allowed = await registry.call_tool(
                "demo-app",
                "demo_execute",
                {"value": "allowed"},
                context={"user_id": 1, "workflow_id": 5},
            )
            assert allowed.success is True
            assert adapter.execute_calls == 1
            await registry.aclose()

            async with session_factory() as session:
                from sqlalchemy import select

                result = await session.execute(select(ToolCallAudit))
                audits = list(result.scalars().all())
            return denied, allowed, audits
        finally:
            await runtime.close()
            _stop_http_server(uv_server, thread)
            await engine.dispose()

    denied, allowed, audits = asyncio.run(scenario())

    assert denied.success is False
    assert allowed.success is True
    assert [audit.status for audit in audits] == ["denied", "success"]
