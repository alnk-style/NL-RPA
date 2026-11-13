"""MCP 契约测试：测试内启动一个假的 MCP HTTP Server。

覆盖工具发现、工具调用、超时与未授权拒绝；HTTP 与 SDK 两种传输
都跑同一组断言。
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401
from app.database import Base
from app.engine.connectors.permissions import (
    RISK_MUTATING,
    RISK_READ_ONLY,
)
from app.engine.mcp_client import (
    MCPClient,
    MCPServerConfig,
    sdk_available,
)
from app.engine.mcp_registry import MCPRegistry
from app.models.mcp import MCPServer, ToolCallAudit
from app.models.user import User
from app.protocols import FailureCategory

FAKE_TOOLS = [
    {
        "name": "echo",
        "description": "回显参数",
        "inputSchema": {"type": "object"},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "sleepy",
        "description": "慢查询",
        "inputSchema": {"type": "object"},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "delete_all",
        "description": "删除全部数据",
        "inputSchema": {"type": "object"},
        "annotations": {"destructiveHint": True},
    },
]


class FakeMCPHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args: Any) -> None:  # noqa: ANN002
        return None

    def _write(
        self,
        payload: dict[str, Any],
        *,
        status: int = 200,
    ) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _empty(self, status: int) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        self._empty(405)

    def do_DELETE(self) -> None:  # noqa: N802
        self._empty(200)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            self._write(
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": "Parse error"},
                }
            )
            return
        self.server.requests.append(payload)  # type: ignore[attr-defined]
        method = payload.get("method")
        request_id = payload.get("id")

        if method == "initialize":
            requested = (payload.get("params") or {}).get(
                "protocolVersion"
            ) or "2025-03-26"
            self._write(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "protocolVersion": requested,
                        "capabilities": {"tools": {}},
                        "serverInfo": {
                            "name": "fake-mcp",
                            "version": "0.1",
                        },
                    },
                }
            )
            return
        if method == "notifications/initialized":
            self._empty(202)
            return
        if method == "ping":
            self._write({"jsonrpc": "2.0", "id": request_id, "result": {}})
            return
        if method == "tools/list":
            self._write(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"tools": FAKE_TOOLS},
                }
            )
            return
        if method == "tools/call":
            params = payload.get("params") or {}
            name = params.get("name")
            arguments = params.get("arguments") or {}
            if name == "sleepy":
                time.sleep(1.0)
                self._write(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {
                            "content": [{"type": "text", "text": "late"}],
                            "isError": False,
                        },
                    }
                )
                return
            if name == "echo":
                self._write(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {
                            "content": [
                                {
                                    "type": "text",
                                    "text": json.dumps(
                                        arguments,
                                        ensure_ascii=False,
                                    ),
                                }
                            ],
                            "isError": False,
                        },
                    }
                )
                return
            self._write(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32602, "message": "Unknown tool"},
                }
            )
            return
        self._write(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": "Method not found"},
            }
        )


class FakeMCPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), FakeMCPHandler)
        self.requests: list[dict[str, Any]] = []

    @property
    def url(self) -> str:
        host, port = self.server_address[:2]
        return f"http://{host}:{port}/mcp"


@pytest.fixture()
def fake_server():
    server = FakeMCPServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


SDK_PARAMS = [
    pytest.param(False, id="builtin"),
    pytest.param(
        True,
        id="sdk",
        marks=pytest.mark.skipif(
            not sdk_available(),
            reason="官方 mcp SDK 未安装",
        ),
    ),
]


@pytest.mark.parametrize("prefer_sdk", SDK_PARAMS)
def test_mcp_http_discovery_and_call(fake_server, prefer_sdk: bool) -> None:
    async def scenario() -> tuple[list[str], Any]:
        client = MCPClient(
            MCPServerConfig(
                name="fake",
                transport="http",
                url=fake_server.url,
                timeout=5.0,
            ),
            prefer_sdk=prefer_sdk,
        )
        try:
            tools = await client.list_tools()
            call = await client.call_tool(
                "echo",
                {"text": "你好"},
                timeout=5.0,
            )
            return [tool["name"] for tool in tools], call
        finally:
            await client.aclose()

    names, call = asyncio.run(scenario())

    assert names == ["echo", "sleepy", "delete_all"]
    assert call.success is True
    assert "你好" in call.result["text"]


@pytest.mark.parametrize("prefer_sdk", SDK_PARAMS)
def test_mcp_http_timeout_is_enforced(fake_server, prefer_sdk: bool) -> None:
    async def scenario() -> tuple[Any, float]:
        client = MCPClient(
            MCPServerConfig(
                name="fake",
                transport="http",
                url=fake_server.url,
                timeout=0.4,
            ),
            prefer_sdk=prefer_sdk,
        )
        started_at = time.perf_counter()
        try:
            call = await client.call_tool("sleepy", {}, timeout=0.4)
        finally:
            await client.aclose()
        return call, time.perf_counter() - started_at

    call, elapsed = asyncio.run(scenario())

    assert call.success is False
    assert call.category == FailureCategory.TIMEOUT
    assert elapsed < 0.95


async def _registry_database():
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
    return session_factory, engine


def test_unauthorized_tool_never_reaches_server(fake_server) -> None:
    async def scenario() -> tuple[Any, int, list[ToolCallAudit]]:
        session_factory, engine = await _registry_database()
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
                server = MCPServer(
                    user_id=1,
                    name="fake",
                    transport="http",
                    url=fake_server.url,
                    enabled=True,
                    default_risk=RISK_MUTATING,
                    tools=[
                        {
                            "name": "delete_all",
                            "risk_level": "destructive",
                            "authorized": False,
                            "confirmed": False,
                        }
                    ],
                )
                session.add(server)
                await session.commit()

            registry = MCPRegistry(
                session_factory=session_factory,
                config=SimpleNamespace(
                    MCP_CLIENT=True,
                    MCP_CALL_TIMEOUT=5.0,
                    MCP_MAX_CONCURRENCY=2,
                    MCP_DEFAULT_RISK=RISK_MUTATING,
                    MCP_STDIO_ENABLED=False,
                ),
            )
            before = len(fake_server.requests)
            outcome = await registry.call_tool(
                "fake",
                "delete_all",
                {"confirm": True},
            )
            after = len(fake_server.requests)
            async with session_factory() as session:
                result = await session.execute(select(ToolCallAudit))
                audits = list(result.scalars().all())
            await registry.aclose()
            return outcome, after - before, audits
        finally:
            await engine.dispose()

    outcome, request_delta, audits = asyncio.run(scenario())

    assert outcome.success is False
    assert outcome.decision == "denied"
    assert outcome.error_category == FailureCategory.PERMISSION_DENIED
    assert request_delta == 0
    assert len(audits) == 1
    assert audits[0].status == "denied"


def test_authorized_read_only_call_records_success(fake_server) -> None:
    async def scenario() -> tuple[Any, list[ToolCallAudit]]:
        session_factory, engine = await _registry_database()
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
                server = MCPServer(
                    user_id=1,
                    name="fake",
                    transport="http",
                    url=fake_server.url,
                    enabled=True,
                    default_risk=RISK_MUTATING,
                    tools=[
                        {
                            "name": "echo",
                            "risk_level": RISK_READ_ONLY,
                            "authorized": True,
                        }
                    ],
                )
                session.add(server)
                await session.commit()

            registry = MCPRegistry(
                session_factory=session_factory,
                config=SimpleNamespace(
                    MCP_CLIENT=True,
                    MCP_CALL_TIMEOUT=5.0,
                    MCP_MAX_CONCURRENCY=2,
                    MCP_DEFAULT_RISK=RISK_MUTATING,
                    MCP_STDIO_ENABLED=False,
                ),
            )
            outcome = await registry.call_tool(
                "fake",
                "echo",
                {"text": "hi"},
                context={"workflow_id": 11, "execution_id": 12, "user_id": 1},
            )
            async with session_factory() as session:
                result = await session.execute(select(ToolCallAudit))
                audits = list(result.scalars().all())
            await registry.aclose()
            return outcome, audits
        finally:
            await engine.dispose()

    outcome, audits = asyncio.run(scenario())

    assert outcome.success is True
    assert outcome.result is not None
    assert outcome.result["trust"] == "untrusted"
    assert len(audits) == 1
    assert audits[0].status == "success"
    assert audits[0].workflow_id == 11
    assert audits[0].execution_id == 12
    assert audits[0].user_id == 1
