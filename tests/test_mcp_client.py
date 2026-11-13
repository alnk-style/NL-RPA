from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import httpx
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
    RISK_DESTRUCTIVE,
    RISK_MUTATING,
    RISK_READ_ONLY,
    evaluate_permission,
    infer_risk_level,
)
from app.engine.connectors.untrusted import wrap_untrusted_result
from app.engine.mcp_client import (
    MCPClient,
    MCPClientError,
    _HTTPTransport,
    MCPServerConfig,
    MCPToolCall,
    map_mcp_error,
)
from app.engine.mcp_registry import MCPRegistry, merge_tool_manifest
from app.models.mcp import MCPServer, ToolCallAudit
from app.models.user import User
from app.protocols import FailureCategory
from app.security.redaction import redact_payload


def _config(**overrides: Any) -> SimpleNamespace:
    values = {
        "MCP_CLIENT": True,
        "MCP_CALL_TIMEOUT": 5.0,
        "MCP_MAX_CONCURRENCY": 2,
        "MCP_DEFAULT_RISK": "mutating",
        "MCP_STDIO_ENABLED": False,
        "CREDENTIAL_ENCRYPTION_KEY": "dGVzdC1lbmNyeXB0aW9uLWtleS0zMmJ5dGVzISE=",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class FakePool:
    """替代 MCPClientPool 的测试桩，记录调用并返回注入结果。"""

    def __init__(self, results: list[MCPToolCall] | None = None) -> None:
        self.results = list(results or [])
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.header_calls: list[dict[str, str]] = []
        self.semaphore = asyncio.Semaphore(4)
        self.dropped: list[Any] = []

    def get(self, config: MCPServerConfig) -> FakePool:
        return self

    def drop(self, key: Any) -> None:
        self.dropped.append(key)

    async def call_tool(
        self,
        tool: str,
        arguments: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
        headers: dict[str, str] | None = None,
    ) -> MCPToolCall:
        self.calls.append((tool, dict(arguments or {})))
        self.header_calls.append(dict(headers or {}))
        if self.results:
            return self.results.pop(0)
        return MCPToolCall(
            success=True,
            result={
                "content": [{"type": "text", "text": "ok"}],
                "text": "ok",
                "is_error": False,
            },
        )

    async def aclose(self) -> None:
        return None


async def _database():
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


async def _seed_server(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    tools: list[dict[str, Any]],
    default_risk: str = RISK_MUTATING,
) -> int:
    async with session_factory() as session:
        user = User(
            id=1,
            username="tester",
            email="tester@example.com",
            hashed_password="hash",
        )
        session.add(user)
        server = MCPServer(
            user_id=1,
            name="local-tools",
            transport="http",
            url="http://127.0.0.1:9/mcp",
            enabled=True,
            default_risk=default_risk,
            tools=tools,
        )
        session.add(server)
        await session.commit()
        await session.refresh(server)
        return int(server.id)


def test_server_config_parsing_and_validation() -> None:
    config = MCPServerConfig.from_mapping(
        {
            "name": "local",
            "transport": "http",
            "url": "http://127.0.0.1:8001/mcp",
            "args": ["--flag"],
            "env": {"A": 1},
            "headers": {"X-Trace": "1"},
            "timeout_seconds": 3,
        }
    )
    assert config.name == "local"
    assert config.args == ["--flag"]
    assert config.env == {"A": "1"}
    assert config.timeout == 3.0
    config.validate()

    with pytest.raises(MCPClientError):
        MCPServerConfig(name="", transport="http", url="http://x").validate()
    with pytest.raises(MCPClientError):
        MCPServerConfig(name="local", transport="http").validate()
    with pytest.raises(MCPClientError):
        MCPServerConfig(name="local", transport="stdio").validate()
    with pytest.raises(MCPClientError):
        MCPServerConfig(
            name="local",
            transport="weird",
            url="http://x",
        ).validate()


def test_mcp_client_passes_per_call_context_headers() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        method = payload.get("method")
        if method == "initialize":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "result": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "serverInfo": {"name": "test", "version": "1"},
                    },
                },
            )
        if method == "notifications/initialized":
            return httpx.Response(202)
        captured.update(
            {
                key.lower(): value
                for key, value in request.headers.items()
            }
        )
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": payload["id"],
                "result": {
                    "content": [{"type": "text", "text": "ok"}],
                    "structuredContent": {"ok": True},
                    "isError": False,
                },
            },
        )

    async def scenario() -> MCPToolCall:
        config = MCPServerConfig(
            name="headers",
            transport="http",
            url="http://127.0.0.1:8123/mcp",
            timeout=3.0,
        )
        client = MCPClient(config, prefer_sdk=False)
        http_client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        )
        setattr(
            client,
            "_http",
            _HTTPTransport(config, client=http_client),
        )
        try:
            return await client.call_tool(
                "echo",
                {},
                headers={"X-NL-RPA-Workflow-Id": "77"},
            )
        finally:
            await http_client.aclose()

    result = asyncio.run(scenario())
    assert result.success is True
    assert captured["x-nl-rpa-workflow-id"] == "77"


def test_permission_gate_levels() -> None:
    read_only = evaluate_permission(RISK_READ_ONLY, None)
    assert read_only.allowed is True
    assert read_only.authorized_by == "default"

    mutating = evaluate_permission(RISK_MUTATING, {"authorized": False})
    assert mutating.allowed is False
    assert mutating.decision == "denied"

    authorized = evaluate_permission(
        RISK_MUTATING,
        {"authorized": True},
    )
    assert authorized.allowed is True
    assert authorized.authorized_by == "explicit"

    unconfirmed = evaluate_permission(
        RISK_DESTRUCTIVE,
        {"authorized": True},
    )
    assert unconfirmed.allowed is False
    assert unconfirmed.requires_confirmation is True

    confirmed = evaluate_permission(
        RISK_DESTRUCTIVE,
        {"authorized": True},
        confirmed=True,
    )
    assert confirmed.allowed is True
    assert confirmed.authorized_by == "confirmation"

    pre_confirmed = evaluate_permission(
        RISK_DESTRUCTIVE,
        {"authorized": True, "confirmed": True},
    )
    assert pre_confirmed.allowed is True


def test_infer_risk_level_from_annotations() -> None:
    assert (
        infer_risk_level(
            {"annotations": {"readOnlyHint": True}},
            default=RISK_MUTATING,
        )
        == RISK_READ_ONLY
    )
    assert (
        infer_risk_level(
            {"annotations": {"destructiveHint": True}},
            default=RISK_MUTATING,
        )
        == RISK_DESTRUCTIVE
    )
    assert (
        infer_risk_level({"name": "x"}, default=RISK_MUTATING)
        == RISK_MUTATING
    )
    assert (
        infer_risk_level({"risk_level": "read-only"}, default=RISK_MUTATING)
        == RISK_READ_ONLY
    )


def test_merge_tool_manifest_preserves_authorization() -> None:
    existing = [
        {
            "name": "delete_all",
            "risk_level": RISK_DESTRUCTIVE,
            "authorized": True,
            "confirmed": True,
        },
        {"name": "gone", "authorized": True},
    ]
    merged = merge_tool_manifest(
        existing,
        [
            {
                "name": "delete_all",
                "description": "清理",
                "inputSchema": {"type": "object"},
                "annotations": {"destructiveHint": True},
            },
            {
                "name": "read_page",
                "annotations": {"readOnlyHint": True},
            },
        ],
        default_risk=RISK_MUTATING,
    )
    by_name = {item["name"]: item for item in merged}
    assert by_name["delete_all"]["authorized"] is True
    assert by_name["delete_all"]["confirmed"] is True
    assert by_name["delete_all"]["risk_level"] == RISK_DESTRUCTIVE
    assert by_name["read_page"]["risk_level"] == RISK_READ_ONLY
    assert by_name["gone"]["available"] is False
    assert by_name["gone"]["authorized"] is True


def test_redact_payload_masks_sensitive_keys_and_values() -> None:
    payload = {
        "token": "abc",
        "nested": {"password": "p", "note": "secret-value"},
        "list": [{"authorization": "Bearer x"}],
    }
    redacted = redact_payload(payload, ["secret-value"])
    assert redacted["token"] == "***"
    assert redacted["nested"]["password"] == "***"
    assert redacted["nested"]["note"] == "***"
    assert redacted["list"][0]["authorization"] == "***"


def test_wrap_untrusted_result_marks_and_truncates() -> None:
    wrapped = wrap_untrusted_result(
        {
            "content": [{"type": "text", "text": "x" * 100}],
            "is_error": False,
        },
        server="srv",
        tool="tool",
        max_chars=10,
    )
    assert wrapped["trust"] == "untrusted"
    assert wrapped["source"] == {"server": "srv", "tool": "tool"}
    assert wrapped["text"].startswith("x" * 10)
    assert "truncated" in wrapped["text"]


def test_error_mapping_categories() -> None:
    assert (
        map_mcp_error(asyncio.TimeoutError()).category
        == FailureCategory.TIMEOUT
    )
    assert (
        map_mcp_error(httpx.TimeoutException("slow")).category
        == FailureCategory.TIMEOUT
    )

    request = httpx.Request("POST", "http://127.0.0.1:9/mcp")
    unauthorized = httpx.HTTPStatusError(
        "401",
        request=request,
        response=httpx.Response(401, request=request),
    )
    assert (
        map_mcp_error(unauthorized).category
        == FailureCategory.AUTH_REQUIRED
    )
    forbidden = httpx.HTTPStatusError(
        "403",
        request=request,
        response=httpx.Response(403, request=request),
    )
    assert (
        map_mcp_error(forbidden).category
        == FailureCategory.PERMISSION_DENIED
    )
    assert (
        map_mcp_error(RuntimeError("boom")).category
        == FailureCategory.UNKNOWN
    )


def test_registry_denies_unauthorized_before_calling_server() -> None:
    async def scenario() -> tuple[Any, FakePool, list[ToolCallAudit]]:
        session_factory, engine = await _database()
        try:
            await _seed_server(
                session_factory,
                tools=[
                    {
                        "name": "delete_all",
                        "risk_level": RISK_DESTRUCTIVE,
                        "authorized": False,
                        "confirmed": False,
                    }
                ],
            )
            pool = FakePool()
            registry = MCPRegistry(
                session_factory=session_factory,
                config=_config(),
                client_pool=pool,  # type: ignore[arg-type]
            )
            outcome = await registry.call_tool(
                "local-tools",
                "delete_all",
                {"path": "/tmp/x"},
                context={
                    "workflow_id": 7,
                    "execution_id": 9,
                    "user_id": 1,
                    "credentials": {"api_token": "shhh"},
                },
            )
            async with session_factory() as session:
                result = await session.execute(select(ToolCallAudit))
                audits = list(result.scalars().all())
            return outcome, pool, audits
        finally:
            await engine.dispose()

    outcome, pool, audits = asyncio.run(scenario())

    assert outcome.success is False
    assert outcome.decision == "denied"
    assert outcome.error_category == FailureCategory.PERMISSION_DENIED
    assert outcome.requires_confirmation is True
    assert pool.calls == []
    assert len(audits) == 1
    audit = audits[0]
    assert audit.server_name == "local-tools"
    assert audit.tool == "delete_all"
    assert audit.status == "denied"
    assert audit.decision == "denied"
    assert audit.workflow_id == 7
    assert audit.execution_id == 9
    assert audit.user_id == 1
    assert audit.duration_ms is not None
    assert outcome.audit_id == audit.id


def test_registry_executes_authorized_tool_and_audits() -> None:
    async def scenario() -> tuple[Any, FakePool, list[ToolCallAudit]]:
        session_factory, engine = await _database()
        try:
            await _seed_server(
                session_factory,
                tools=[
                    {
                        "name": "echo",
                        "risk_level": RISK_READ_ONLY,
                        "authorized": False,
                    }
                ],
            )
            pool = FakePool(
                results=[
                    MCPToolCall(
                        success=True,
                        result={
                            "content": [
                                {"type": "text", "text": "hello"}
                            ],
                            "text": "hello",
                            "is_error": False,
                        },
                    )
                ]
            )
            registry = MCPRegistry(
                session_factory=session_factory,
                config=_config(),
                client_pool=pool,  # type: ignore[arg-type]
            )
            outcome = await registry.call_tool(
                "local-tools",
                "echo",
                {"text": "hi", "access_token": "topsecret"},
                context={
                    "user_id": 1,
                    "workflow_id": 11,
                    "execution_id": 3,
                    "request_id": "request-ctx",
                    "confirmed": True,
                },
            )
            async with session_factory() as session:
                result = await session.execute(select(ToolCallAudit))
                audits = list(result.scalars().all())
            return outcome, pool, audits
        finally:
            await engine.dispose()

    outcome, pool, audits = asyncio.run(scenario())

    assert outcome.success is True
    assert outcome.risk_level == RISK_READ_ONLY
    assert outcome.decision == "allowed"
    assert outcome.result is not None
    assert outcome.result["trust"] == "untrusted"
    assert pool.calls == [("echo", {"text": "hi", "access_token": "topsecret"})]
    assert pool.header_calls == [
        {
            "X-NL-RPA-Workflow-Id": "11",
            "X-NL-RPA-Execution-Id": "3",
            "X-NL-RPA-Request-Id": "request-ctx",
            "X-NL-RPA-Confirmed": "true",
        }
    ]
    assert len(audits) == 1
    audit = audits[0]
    assert audit.status == "success"
    assert audit.arguments_summary is not None
    assert audit.arguments_summary["access_token"] == "***"
    assert audit.arguments_summary["text"] == "hi"
    assert audit.result_summary is not None
    assert audit.result_summary["trust"] == "untrusted"
    assert audit.attempts == 1


def test_registry_retries_only_read_only_tools() -> None:
    async def scenario() -> tuple[Any, FakePool, Any, FakePool, int]:
        session_factory, engine = await _database()
        try:
            server_id = await _seed_server(
                session_factory,
                tools=[
                    {
                        "name": "read",
                        "risk_level": RISK_READ_ONLY,
                    },
                    {
                        "name": "write",
                        "risk_level": RISK_MUTATING,
                        "authorized": True,
                    },
                ],
            )
            read_pool = FakePool(
                results=[
                    MCPToolCall(
                        success=False,
                        error="超时",
                        category=FailureCategory.TIMEOUT,
                    ),
                    MCPToolCall(
                        success=True,
                        result={"text": "done", "is_error": False},
                    ),
                ]
            )
            registry = MCPRegistry(
                session_factory=session_factory,
                config=_config(),
                client_pool=read_pool,  # type: ignore[arg-type]
            )
            read_outcome = await registry.call_tool(
                server_id,
                "read",
                {},
                retry_policy={"max_attempts": 3, "backoff_seconds": 0},
            )

            write_pool = FakePool(
                results=[
                    MCPToolCall(
                        success=False,
                        error="超时",
                        category=FailureCategory.TIMEOUT,
                    )
                ]
            )
            registry.client_pool = write_pool  # type: ignore[assignment]
            write_outcome = await registry.call_tool(
                server_id,
                "write",
                {},
                retry_policy={"max_attempts": 3, "backoff_seconds": 0},
            )
            return (
                read_outcome,
                read_pool,
                write_outcome,
                write_pool,
                server_id,
            )
        finally:
            await engine.dispose()

    read_outcome, read_pool, write_outcome, write_pool, _ = asyncio.run(
        scenario()
    )

    assert read_outcome.success is True
    assert read_outcome.attempts == 2
    assert len(read_pool.calls) == 2
    assert write_outcome.success is False
    assert write_outcome.attempts == 1
    assert len(write_pool.calls) == 1
    assert write_outcome.error_category == FailureCategory.TIMEOUT


def test_registry_audits_timeout_failures() -> None:
    async def scenario() -> list[ToolCallAudit]:
        session_factory, engine = await _database()
        try:
            server_id = await _seed_server(
                session_factory,
                tools=[{"name": "slow", "risk_level": RISK_READ_ONLY}],
            )
            pool = FakePool(
                results=[
                    MCPToolCall(
                        success=False,
                        error="MCP 调用超时",
                        category=FailureCategory.TIMEOUT,
                        duration_ms=100.0,
                    )
                ]
            )
            registry = MCPRegistry(
                session_factory=session_factory,
                config=_config(),
                client_pool=pool,  # type: ignore[arg-type]
            )
            await registry.call_tool(server_id, "slow", {})
            async with session_factory() as session:
                result = await session.execute(select(ToolCallAudit))
                return list(result.scalars().all())
        finally:
            await engine.dispose()

    audits = asyncio.run(scenario())

    assert len(audits) == 1
    assert audits[0].status == "timeout"
    assert audits[0].error == "MCP 调用超时"
    assert audits[0].decision == "allowed"
