from __future__ import annotations

import asyncio
from types import SimpleNamespace

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401
from app.database import Base
from app.engine.mcp_registry import MCPRegistry
from app.engine.mcp_tool_catalog import MCPToolCatalog
from app.models.mcp import MCPServer
from app.models.user import User
from app.models.workspace import Workspace


def _settings(**overrides):
    values = {
        "MCP_CLIENT": True,
        "MCP_CALL_TIMEOUT": 5.0,
        "MCP_MAX_CONCURRENCY": 2,
        "MCP_DEFAULT_RISK": "mutating",
        "MCP_STDIO_ENABLED": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


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


async def _seed(session_factory) -> None:
    async with session_factory() as session:
        session.add_all(
            [
                User(
                    id=1,
                    username="one",
                    email="one@example.com",
                    hashed_password="hash",
                ),
                User(
                    id=2,
                    username="two",
                    email="two@example.com",
                    hashed_password="hash",
                ),
                Workspace(
                    id=1,
                    owner_id=1,
                    name="Main",
                    slug="main",
                    is_default=True,
                ),
                Workspace(
                    id=2,
                    owner_id=1,
                    name="Other",
                    slug="other",
                    is_default=False,
                ),
            ]
        )
        await session.commit()
        session.add_all(
            [
                MCPServer(
                    user_id=1,
                    workspace_id=1,
                    name="demo_app",
                    enabled=True,
                    default_risk="mutating",
                    description="Demo application",
                    config={"app_name": "演示应用"},
                    tools=[
                        {
                            "name": "demo_status",
                            "description": "读取应用状态",
                            "input_schema": {
                                "type": "object",
                                "properties": {},
                            },
                            "risk_level": "read_only",
                            "available": True,
                        },
                        {
                            "name": "demo_write",
                            "description": "写入数据",
                            "risk_level": "mutating",
                            "authorized": False,
                            "available": True,
                        },
                        {
                            "name": "demo_authorized",
                            "description": "已授权写入",
                            "risk_level": "mutating",
                            "authorized": True,
                            "available": True,
                        },
                        {
                            "name": "demo_confirm",
                            "description": "已确认破坏性工具",
                            "risk_level": "destructive",
                            "authorized": True,
                            "confirmed": True,
                            "available": True,
                        },
                        {
                            "name": "demo_unconfirmed",
                            "description": "未确认破坏性工具",
                            "risk_level": "destructive",
                            "authorized": True,
                            "confirmed": False,
                            "available": True,
                        },
                        {
                            "name": "demo_gone",
                            "description": "已下线",
                            "risk_level": "read_only",
                            "available": False,
                        },
                    ],
                ),
                MCPServer(
                    user_id=1,
                    workspace_id=1,
                    name="disabled_server",
                    enabled=False,
                    tools=[
                        {
                            "name": "disabled_tool",
                            "risk_level": "read_only",
                            "available": True,
                        }
                    ],
                ),
                MCPServer(
                    user_id=2,
                    workspace_id=1,
                    name="other_user",
                    enabled=True,
                    tools=[
                        {
                            "name": "other_tool",
                            "risk_level": "read_only",
                            "available": True,
                        }
                    ],
                ),
                MCPServer(
                    user_id=1,
                    workspace_id=2,
                    name="other_workspace",
                    enabled=True,
                    tools=[
                        {
                            "name": "workspace_tool",
                            "risk_level": "read_only",
                            "available": True,
                        }
                    ],
                ),
            ]
        )
        await session.commit()


def test_catalog_filters_by_permission_and_workspace() -> None:
    async def scenario() -> list[dict]:
        session_factory, engine = await _database()
        try:
            await _seed(session_factory)
            registry = MCPRegistry(
                session_factory=session_factory,
                config=_settings(),
            )
            catalog = MCPToolCatalog(registry, max_tools=50)
            return await catalog.list_tools(
                user_id=1,
                workspace_id=1,
                limit=50,
            )
        finally:
            await engine.dispose()

    tools = asyncio.run(scenario())
    by_name = {item["tool"]: item for item in tools}

    assert set(by_name) == {
        "demo_status",
        "demo_authorized",
        "demo_confirm",
    }
    assert by_name["demo_status"]["authorized"] is True
    assert by_name["demo_authorized"]["risk_level"] == "mutating"
    assert by_name["demo_confirm"]["confirmed"] is True
    assert by_name["demo_status"]["app_name"] == "演示应用"


def test_catalog_supports_keyword_and_bounded_limit() -> None:
    async def scenario() -> tuple[list[dict], list[dict], list[dict]]:
        session_factory, engine = await _database()
        try:
            await _seed(session_factory)
            registry = MCPRegistry(
                session_factory=session_factory,
                config=_settings(),
            )
            catalog = MCPToolCatalog(registry, max_tools=2)
            all_tools = await catalog.list_tools(
                user_id=1,
                workspace_id=1,
                limit=500,
            )
            filtered = await catalog.list_tools(
                user_id=1,
                workspace_id=1,
                q="authorized",
            )
            chinese = await catalog.list_tools(
                user_id=1,
                workspace_id=1,
                q="演示",
            )
            return all_tools, filtered, chinese
        finally:
            await engine.dispose()

    all_tools, filtered, chinese = asyncio.run(scenario())

    assert len(all_tools) == 2
    assert [item["tool"] for item in filtered] == ["demo_authorized"]
    assert [item["tool"] for item in chinese] == [
        "demo_status",
        "demo_authorized",
    ][:2]


def test_catalog_does_not_expose_transport_secrets() -> None:
    async def scenario() -> tuple[list[dict], str]:
        session_factory, engine = await _database()
        try:
            await _seed(session_factory)
            async with session_factory() as session:
                server = await session.get(MCPServer, 1)
                assert server is not None
                server.env = {"API_TOKEN": "env-secret"}
                server.headers = {"Authorization": "header-secret"}
                server.secret_encrypted = b"encrypted-secret"
                server.tools = [
                    {
                        "name": "leaky",
                        "description": (
                            "Read C:\\private\\config.json token=secret-token"
                        ),
                        "input_schema": {
                            "type": "object",
                            "description": "C:\\private\\schema.json",
                            "properties": {
                                "token": {"type": "string"},
                            },
                        },
                        "risk_level": "read_only",
                        "available": True,
                    }
                ]
                await session.commit()
            registry = MCPRegistry(
                session_factory=session_factory,
                config=_settings(),
            )
            catalog = MCPToolCatalog(registry)
            tools = await catalog.list_tools(
                user_id=1,
                workspace_id=1,
            )
            import json

            return tools, json.dumps(tools, ensure_ascii=False)
        finally:
            await engine.dispose()

    tools, rendered = asyncio.run(scenario())

    assert tools
    assert "env-secret" not in rendered
    assert "header-secret" not in rendered
    assert "encrypted-secret" not in rendered
    assert "secret-token" not in rendered
    assert "C:\\private" not in rendered


def test_catalog_returns_empty_when_client_disabled() -> None:
    async def scenario() -> list[dict]:
        session_factory, engine = await _database()
        try:
            await _seed(session_factory)
            registry = MCPRegistry(
                session_factory=session_factory,
                config=_settings(MCP_CLIENT=False),
            )
            catalog = MCPToolCatalog(registry)
            return await catalog.list_tools(
                user_id=1,
                workspace_id=1,
            )
        finally:
            await engine.dispose()

    assert asyncio.run(scenario()) == []
