"""插件版本、sha256 指纹与可选签名的校验。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401
from app.database import Base
from app.engine.mcp_client import MCPServerConfig, MCPToolCall
from app.engine.mcp_registry import MCPRegistry
from app.models.mcp import MCPServer
from app.security.plugin_integrity import (
    canonical_plugin_spec,
    compute_fingerprint,
    compute_signature,
    compute_toolset_fingerprint,
    verify_plugin_config,
    verify_signature,
)


class FakePool:
    def __init__(self) -> None:
        self.tools = [
            {
                "name": "echo",
                "description": "echo",
                "inputSchema": {"type": "object"},
            }
        ]
        self.list_calls = 0
        self.call_calls = 0
        self.semaphore = asyncio.Semaphore(2)
        self.transport_name = "fake"

    def get(self, _config: MCPServerConfig) -> "FakePool":
        return self

    def drop(self, _key) -> None:
        return None

    async def list_tools(self, *, timeout=None):
        self.list_calls += 1
        return [dict(tool) for tool in self.tools]

    async def call_tool(self, tool, arguments=None, *, timeout=None):
        self.call_calls += 1
        return MCPToolCall(success=True, result={"ok": True})

    async def aclose(self) -> None:
        return None


def _settings(**overrides):
    values = {
        "MCP_CLIENT": True,
        "MCP_CALL_TIMEOUT": 5.0,
        "MCP_MAX_CONCURRENCY": 2,
        "MCP_DEFAULT_RISK": "mutating",
        "MCP_STDIO_ENABLED": False,
        "PLUGIN_INTEGRITY": True,
        "PLUGIN_REQUIRE_SIGNATURE": True,
        "PLUGIN_SIGNING_SECRET": "signing-secret",
        "CREDENTIAL_ENCRYPTION_KEY": (
            "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="
        ),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_fingerprint_and_signature_are_deterministic() -> None:
    spec = canonical_plugin_spec(
        transport="http",
        url="https://tools.example.com/mcp",
        command=None,
        args=["--flag"],
        env={"B": "2", "A": "1"},
        headers={"X-Token": "abc"},
        auth_token="secret",
    )
    fingerprint = compute_fingerprint(spec)
    assert fingerprint.startswith("sha256:")
    assert fingerprint == compute_fingerprint(dict(reversed(list(spec.items()))))

    changed = compute_fingerprint(
        canonical_plugin_spec(
            transport="http",
            url="https://evil.example.com/mcp",
            command=None,
            args=["--flag"],
            env={"A": "1", "B": "2"},
            headers={"X-Token": "abc"},
            auth_token="secret",
        )
    )
    assert changed != fingerprint

    signature = compute_signature(fingerprint, "signing-secret")
    assert verify_signature(fingerprint, signature, "signing-secret")
    assert not verify_signature(fingerprint, signature, "wrong-secret")
    assert not verify_signature(fingerprint, None, "signing-secret")


def test_verify_plugin_config_rejects_unsigned_and_mismatch() -> None:
    base = dict(
        transport="http",
        url="http://127.0.0.1:1/mcp",
        command=None,
        args=[],
        env={},
        headers={},
        auth_token=None,
    )
    fingerprint = compute_fingerprint(canonical_plugin_spec(**base))
    signature = compute_signature(fingerprint, "signing-secret")

    assert verify_plugin_config(
        **base,
        version="1.0.0",
        fingerprint=fingerprint,
        signature=signature,
        enforce=False,
    ).ok

    assert not verify_plugin_config(
        **base,
        version=None,
        fingerprint=fingerprint,
        signature=signature,
        enforce=True,
    ).ok
    assert not verify_plugin_config(
        **base,
        version="1.0.0",
        fingerprint=None,
        signature=signature,
        enforce=True,
    ).ok
    assert not verify_plugin_config(
        **base,
        version="1.0.0",
        fingerprint="sha256:deadbeef",
        signature=signature,
        enforce=True,
    ).ok
    assert not verify_plugin_config(
        **base,
        version="1.0.0",
        fingerprint=fingerprint,
        signature=None,
        enforce=True,
        require_signature=True,
        signing_secret="signing-secret",
    ).ok
    assert verify_plugin_config(
        **base,
        version="1.0.0",
        fingerprint=fingerprint,
        signature=signature,
        enforce=True,
        require_signature=True,
        signing_secret="signing-secret",
    ).ok


def test_registry_blocks_unsigned_and_tampered_plugin() -> None:
    async def scenario() -> None:
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
        pool = FakePool()
        config = _settings()
        registry = MCPRegistry(
            session_factory=session_factory,
            config=config,
            client_pool=pool,
        )
        server = await registry.create_server(
            {
                "name": "signed-tool",
                "transport": "http",
                "url": "http://127.0.0.1:1/mcp",
                "version": "1.0.0",
            },
            user_id=1,
        )

        # 未固定指纹：拒绝加载，且不发起任何网络调用。
        blocked = await registry.test_server(server.id, user_id=1)
        assert blocked["success"] is False
        assert "指纹" in blocked["error"]
        assert pool.list_calls == 0

        pin = await registry.pin_fingerprint(
            server.id,
            user_id=1,
            sign=True,
        )
        assert pin["success"] is True
        assert pin["fingerprint"].startswith("sha256:")
        assert pin["signature"].startswith("hmac-sha256:")

        ok = await registry.test_server(server.id, user_id=1)
        assert ok["success"] is True
        assert pool.list_calls == 1

        # 篡改地址后指纹不匹配，直接拒绝。
        async with session_factory() as session:
            record = await session.get(MCPServer, server.id)
            record.url = "http://127.0.0.1:2/mcp"
            await session.commit()
        tampered = await registry.test_server(server.id, user_id=1)
        assert tampered["success"] is False
        assert "指纹不匹配" in tampered["error"]
        assert pool.list_calls == 1
        await engine.dispose()

    asyncio.run(scenario())


def test_toolset_change_requires_reconfirmation() -> None:
    async def scenario() -> None:
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
        pool = FakePool()
        registry = MCPRegistry(
            session_factory=session_factory,
            config=_settings(PLUGIN_INTEGRITY=False),
            client_pool=pool,
        )
        server = await registry.create_server(
            {
                "name": "toolset",
                "transport": "http",
                "url": "http://127.0.0.1:1/mcp",
            },
            user_id=1,
        )
        first = await registry.test_server(server.id, user_id=1)
        assert first["success"] is True
        await registry.authorize_tool(
            server.id,
            "echo",
            authorized=True,
            user_id=1,
        )
        async with session_factory() as session:
            stored = await session.get(MCPServer, server.id)
            echo = [
                item for item in stored.tools if item["name"] == "echo"
            ][0]
            assert echo["authorized"] is True
            before = stored.toolset_fingerprint

        pool.tools.append(
            {
                "name": "write",
                "inputSchema": {"type": "object"},
            }
        )
        second = await registry.test_server(server.id, user_id=1)
        assert second["success"] is True
        async with session_factory() as session:
            stored = await session.get(MCPServer, server.id)
            echo = [
                item for item in stored.tools if item["name"] == "echo"
            ][0]
            assert echo["authorized"] is False
            assert echo["needs_reconfirmation"] is True
            assert stored.toolset_fingerprint != before
        await engine.dispose()

    asyncio.run(scenario())


def test_toolset_fingerprint_changes_with_schema() -> None:
    first = compute_toolset_fingerprint([{"name": "echo"}])
    second = compute_toolset_fingerprint(
        [{"name": "echo", "input_schema": {"type": "object"}}]
    )
    assert first != second
