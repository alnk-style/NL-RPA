"""MCP 管理接口与 MCP Server 导出的契约测试。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app import models, runtime_flags  # noqa: F401
from app.api import mcp as mcp_api
from app.api import mcp_export as mcp_export_api
from app.api.dependencies import get_current_user
from app.database import Base, get_db
from app.engine.mcp_client import MCPServerConfig, MCPToolCall
from app.engine.mcp_registry import MCPRegistry
from app.models.user import User

FIXTURE_TOOLS = [
    {
        "name": "echo",
        "description": "回显",
        "inputSchema": {"type": "object"},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "purge",
        "description": "清理",
        "inputSchema": {"type": "object"},
        "annotations": {"destructiveHint": True},
    },
]


@pytest.fixture(autouse=True)
def _reset_runtime_flags():
    runtime_flags.reset_overrides()
    yield
    runtime_flags.reset_overrides()


class FakePool:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.semaphore = asyncio.Semaphore(2)
        self.transport_name = "builtin:http"

    def get(self, config: MCPServerConfig) -> "FakePool":
        return self

    def drop(self, key: Any) -> None:
        return None

    async def list_tools(
        self,
        *,
        timeout: float | None = None,
    ) -> list[dict[str, Any]]:
        return [dict(tool) for tool in FIXTURE_TOOLS]

    async def call_tool(
        self,
        tool: str,
        arguments: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> MCPToolCall:
        self.calls.append((tool, dict(arguments or {})))
        return MCPToolCall(
            success=True,
            result={"text": "ok", "is_error": False},
        )

    async def aclose(self) -> None:
        return None


def _settings(**overrides: Any) -> SimpleNamespace:
    values = {
        "MCP_CLIENT": True,
        "MCP_CALL_TIMEOUT": 5.0,
        "MCP_MAX_CONCURRENCY": 2,
        "MCP_DEFAULT_RISK": "mutating",
        "MCP_STDIO_ENABLED": False,
        "MCP_SERVER_EXPORT": False,
        "MCP_SERVER_TOKEN": "",
        "MCP_SERVER_ALLOW_MUTATING": False,
        "CREDENTIAL_ENCRYPTION_KEY": (
            "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="
        ),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class Harness:
    def __init__(
        self,
        client: TestClient,
        registry: MCPRegistry,
        pool: FakePool,
        session_factory: async_sessionmaker[AsyncSession],
        engine: Any,
    ) -> None:
        self.client = client
        self.registry = registry
        self.pool = pool
        self.session_factory = session_factory
        self.engine = engine


@pytest.fixture()
def harness(tmp_path) -> Any:
    async def build() -> Harness:
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{tmp_path.as_posix()}/mcp.db",
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
                    id=1,
                    username="tester",
                    email="tester@example.com",
                    hashed_password="hash",
                )
            )
            await session.commit()

        pool = FakePool()
        registry = MCPRegistry(
            session_factory=session_factory,
            config=_settings(),
            client_pool=pool,  # type: ignore[arg-type]
        )

        app = FastAPI()
        app.include_router(
            mcp_api.router,
            dependencies=[Depends(get_current_user)],
        )
        app.include_router(
            mcp_export_api.router,
            dependencies=[Depends(get_current_user)],
        )

        async def override_db():
            async with session_factory() as session:
                yield session

        async def override_user() -> User:
            async with session_factory() as session:
                user = await session.get(User, 1)
                assert user is not None
                return user

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = override_user
        app.dependency_overrides[mcp_api.get_mcp_registry] = lambda: registry
        app.state.mcp_registry = registry
        app.state.execution_manager = SimpleNamespace(
            start_workflow=_async_result(
                SimpleNamespace(id=5, status="running")
            )
        )

        client = TestClient(app)
        return Harness(client, registry, pool, session_factory, engine)

    harness_obj = asyncio.run(build())
    yield harness_obj
    harness_obj.client.close()
    asyncio.run(harness_obj.engine.dispose())


def _async_result(value: Any):
    async def _call(*_args: Any, **_kwargs: Any) -> Any:
        return value

    return _call


def test_mcp_endpoints_are_gated_by_switch(harness, monkeypatch) -> None:
    monkeypatch.setattr(
        mcp_api,
        "get_settings",
        lambda: _settings(MCP_CLIENT=False),
    )
    response = harness.client.get("/api/mcp/servers")
    assert response.status_code == 403
    catalog = harness.client.get("/api/mcp/catalog")
    assert catalog.status_code == 403


def test_mcp_server_crud_and_discovery(harness, monkeypatch) -> None:
    monkeypatch.setattr(mcp_api, "get_settings", _settings)

    created = harness.client.post(
        "/api/mcp/servers",
        json={
            "name": "local-tools",
            "description": "本地工具",
            "transport": "http",
            "url": "http://127.0.0.1:9/mcp",
            "auth_token": "super-secret-token",
            "headers": {"X-Api-Key": "header-secret"},
            "default_risk": "mutating",
        },
    )
    assert created.status_code == 200
    body = created.json()["server"]
    assert body["has_secret"] is True
    assert "super-secret-token" not in created.text
    assert "header-secret" not in created.text
    assert body["headers"]["X-Api-Key"] == "***"
    server_id = body["id"]

    listed = harness.client.get("/api/mcp/servers")
    assert listed.status_code == 200
    assert len(listed.json()["servers"]) == 1

    tested = harness.client.post(f"/api/mcp/servers/{server_id}/test")
    assert tested.status_code == 200
    payload = tested.json()
    assert payload["success"] is True
    tools = {tool["name"]: tool for tool in payload["tools"]}
    assert tools["echo"]["risk_level"] == "read_only"
    assert tools["purge"]["risk_level"] == "destructive"
    assert tools["purge"]["authorized"] is False

    authorized = harness.client.post(
        f"/api/mcp/servers/{server_id}/tools/purge/authorize",
        json={"authorized": True, "confirmed": True},
    )
    assert authorized.status_code == 200
    assert authorized.json()["tool"]["authorized"] is True
    assert authorized.json()["tool"]["confirmed"] is True

    async def call_tool() -> Any:
        return await harness.registry.call_tool(
            server_id,
            "echo",
            {"text": "hi"},
            context={"user_id": 1, "workflow_id": 3, "execution_id": 4},
        )

    outcome = asyncio.run(call_tool())
    assert outcome.success is True

    audits = harness.client.get("/api/mcp/audits")
    assert audits.status_code == 200
    rows = audits.json()["audits"]
    assert rows and rows[0]["tool"] == "echo"
    assert rows[0]["workflow_id"] == 3
    assert rows[0]["execution_id"] == 4

    deleted = harness.client.delete(f"/api/mcp/servers/{server_id}")
    assert deleted.status_code == 200
    assert harness.client.get("/api/mcp/servers").json()["servers"] == []
    # 删除 Server 后审计仍然保留
    assert harness.client.get("/api/mcp/audits").json()["audits"]


def test_mcp_catalog_api_returns_only_available_tools(
    harness,
    monkeypatch,
) -> None:
    monkeypatch.setattr(mcp_api, "get_settings", _settings)

    async def seed() -> None:
        from app.models.mcp import MCPServer

        async with harness.session_factory() as session:
            session.add(
                MCPServer(
                    user_id=1,
                    name="demo_app",
                    enabled=True,
                    config={"app_name": "演示应用"},
                    tools=[
                        {
                            "name": "demo_status",
                            "description": "读取状态",
                            "risk_level": "read_only",
                            "available": True,
                        },
                        {
                            "name": "demo_write",
                            "description": "写入",
                            "risk_level": "mutating",
                            "authorized": False,
                            "available": True,
                        },
                        {
                            "name": "demo_export",
                            "description": "导出",
                            "risk_level": "mutating",
                            "authorized": True,
                            "available": True,
                        },
                    ],
                )
            )
            await session.commit()

    asyncio.run(seed())
    response = harness.client.get(
        "/api/mcp/catalog",
        params={"q": "demo", "limit": 2},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["enabled"] is True
    assert [item["tool"] for item in payload["tools"]] == [
        "demo_status",
        "demo_export",
    ]
    assert payload["tools"][0]["app_name"] == "演示应用"


def test_mcp_server_export_contract(harness, monkeypatch) -> None:
    monkeypatch.setattr(
        mcp_export_api,
        "get_settings",
        lambda: _settings(
            MCP_SERVER_EXPORT=True,
            MCP_SERVER_TOKEN="s3cret",
            MCP_SERVER_ALLOW_MUTATING=False,
        ),
    )
    headers = {"X-MCP-Token": "s3cret"}

    unauthorized = harness.client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        headers={"X-MCP-Token": "wrong"},
    )
    assert unauthorized.status_code == 401

    initialized = harness.client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-03-26"},
        },
        headers=headers,
    )
    assert initialized.status_code == 200
    assert (
        initialized.json()["result"]["serverInfo"]["name"]
        == "nl-rpa-platform"
    )

    tools = harness.client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        headers=headers,
    )
    names = [tool["name"] for tool in tools.json()["result"]["tools"]]
    assert "platform_health" in names
    assert "platform_run_workflow" in names

    health = harness.client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "platform_health", "arguments": {}},
        },
        headers=headers,
    )
    assert health.status_code == 200
    assert health.json()["result"]["isError"] is False

    blocked = harness.client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "platform_run_workflow",
                "arguments": {"workflow_id": 1, "confirm": True},
            },
        },
        headers=headers,
    )
    assert "error" in blocked.json()
    assert blocked.json()["error"]["code"] == -32003

    unknown = harness.client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "platform_delete_everything"},
        },
        headers=headers,
    )
    assert unknown.json()["error"]["code"] == -32003


def test_mcp_server_export_runs_workflow_with_confirmation(
    harness,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        mcp_export_api,
        "get_settings",
        lambda: _settings(
            MCP_SERVER_EXPORT=True,
            MCP_SERVER_ALLOW_MUTATING=True,
        ),
    )

    async def seed_workflow() -> None:
        from app.models.workflow import Workflow

        async with harness.session_factory() as session:
            session.add(
                Workflow(
                    id=1,
                    user_id=1,
                    name="demo",
                    natural_language_input="test",
                )
            )
            await session.commit()

    asyncio.run(seed_workflow())

    response = harness.client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "platform_run_workflow",
                "arguments": {"workflow_id": 1, "confirm": True},
            },
        },
    )
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["isError"] is False
    assert result["structuredContent"]["execution_id"] == 5


def test_mcp_settings_toggle_persists_without_env_change(
    harness,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        mcp_api,
        "get_settings",
        lambda: _settings(MCP_CLIENT=False),
    )

    initial = harness.client.get("/api/mcp/settings")
    assert initial.status_code == 200
    assert initial.json()["enabled"] is False
    assert initial.json()["source"] == "environment"
    assert initial.json()["env_default"] is False
    assert isinstance(initial.json()["sdk_available"], bool)

    # 关闭状态下管理接口仍然被拦截
    assert harness.client.get("/api/mcp/servers").status_code == 403

    enabled = harness.client.patch(
        "/api/mcp/settings",
        json={"enabled": True},
    )
    assert enabled.status_code == 200
    assert enabled.json()["enabled"] is True
    assert enabled.json()["source"] == "runtime"

    # 不改环境变量，开关立即生效
    listed = harness.client.get("/api/mcp/servers")
    assert listed.status_code == 200
    assert listed.json()["servers"] == []

    # 模拟服务重启：清空进程缓存后从数据库恢复开关
    runtime_flags.reset_overrides()
    restored = asyncio.run(
        runtime_flags.load_overrides(harness.session_factory)
    )
    assert restored == {"mcp_client": True}

    disabled = harness.client.patch(
        "/api/mcp/settings",
        json={"enabled": False},
    )
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False
    assert disabled.json()["source"] == "runtime"
    assert harness.client.get("/api/mcp/servers").status_code == 403
