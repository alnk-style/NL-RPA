"""统一 Connector 接口与 Desktop Bridge 包装的测试。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401
from app.database import Base
from app.engine.connectors import (
    ConnectorRegistry,
    DesktopBridgeConnector,
    MCPConnector,
)
from app.engine.mcp_registry import MCPRegistry
from app.engine.runner import ExecutionResult
from app.models.mcp import MCPServer
from app.models.user import User


class FakeConnectionManager:
    def __init__(self, agent_ids: list[int]) -> None:
        self.agent_ids = agent_ids

    def get_online_agents(self) -> list[int]:
        return list(self.agent_ids)


class FakeDesktopDispatcher:
    def __init__(self) -> None:
        self.connection_manager = FakeConnectionManager([3])
        self.calls: list[tuple[Any, dict[str, Any]]] = []

    async def inspect(
        self,
        request: dict[str, Any] | None = None,
        *,
        agent_id: int | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        return {
            "success": True,
            "windows": [{"title": "记事本 - 未命名"}],
            "elements": [
                {"automation_id": "txtContent", "name": "文本编辑器"}
            ],
            "target": request,
        }

    async def dispatch(
        self,
        workflow: Any,
        context: dict[str, Any] | None = None,
    ) -> ExecutionResult:
        self.calls.append((workflow, dict(context or {})))
        return ExecutionResult(
            success=True,
            output="desktop ok",
            error=None,
            screenshots=[],
            step_logs=[
                {
                    "step_index": 0,
                    "executor": "windows",
                    "action": "launch_app",
                    "status": "success",
                }
            ],
            duration_seconds=0.01,
        )


class RepairLike:
    """模拟 DesktopRepairCoordinator：有 dispatcher，也有 execute。"""

    def __init__(self, dispatcher: FakeDesktopDispatcher) -> None:
        self.dispatcher = dispatcher

    async def execute(
        self,
        workflow: Any,
        context: dict[str, Any] | None = None,
    ) -> ExecutionResult:
        return await self.dispatcher.dispatch(workflow, context)


def test_desktop_bridge_implements_connector_interface() -> None:
    async def scenario() -> tuple[Any, ...]:
        dispatcher = FakeDesktopDispatcher()
        bridge = DesktopBridgeConnector(RepairLike(dispatcher))
        capabilities = await bridge.capabilities()
        observation = await bridge.observe({"title": "记事本"})
        action = await bridge.act(
            {
                "action": "launch_app",
                "params": {"path": "notepad.exe"},
                "idempotency_key": "open-notepad",
            },
            {
                "workflow_id": 5,
                "user_id": 1,
                "dispatch_context": {"execution_log_id": 9},
            },
        )
        missing_workflow = await bridge.act(
            {"action": "launch_app", "params": {}},
        )
        window = await bridge.verify({"kind": "window", "title": "记事本"})
        element = await bridge.verify(
            {"kind": "element", "automation_id": "txtContent"},
        )
        auth = await bridge.auth_status()
        return (
            bridge,
            capabilities,
            observation,
            action,
            missing_workflow,
            window,
            element,
            auth,
            dispatcher,
        )

    (
        bridge,
        capabilities,
        observation,
        action,
        missing_workflow,
        window,
        element,
        auth,
        dispatcher,
    ) = asyncio.run(scenario())

    assert bridge.name == "desktop"
    assert capabilities["success"] is True
    assert "launch_app" in capabilities["data"]["actions"]
    assert capabilities["online_agents"] == [3]
    assert observation["success"] is True
    assert observation["data"]["windows"][0]["title"].startswith("记事本")

    assert action["success"] is True
    assert action["data"]["action"] == "launch_app"
    assert len(dispatcher.calls) == 1
    workflow, context = dispatcher.calls[0]
    assert workflow.id == 5
    assert workflow.user_id == 1
    assert workflow.structured_plan["steps"][0]["action"] == "launch_app"
    assert (
        workflow.structured_plan["steps"][0]["idempotency_key"]
        == "open-notepad"
    )
    assert context["execution_log_id"] == 9

    assert missing_workflow["success"] is False
    assert "workflow_id" in (missing_workflow["error"] or "")
    assert window["success"] is True
    assert element["success"] is True
    assert auth["data"]["online_agents"] == [3]


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


def test_mcp_connector_reads_registry_state() -> None:
    async def scenario() -> tuple[ConnectorRegistry, Any, Any, Any]:
        session_factory, engine = await _database()
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
                        name="local-tools",
                        transport="http",
                        url="http://127.0.0.1:9/mcp",
                        tools=[
                            {
                                "name": "echo",
                                "risk_level": "read_only",
                                "authorized": True,
                            },
                            {
                                "name": "purge",
                                "risk_level": "destructive",
                                "authorized": False,
                            },
                        ],
                    )
                )
                await session.commit()

            closed: list[str] = []

            async def _aclose() -> None:
                closed.append("closed")

            registry = MCPRegistry(
                session_factory=session_factory,
                config=SimpleNamespace(
                    MCP_CLIENT=True,
                    MCP_CALL_TIMEOUT=5.0,
                    MCP_MAX_CONCURRENCY=2,
                    MCP_DEFAULT_RISK="mutating",
                    MCP_STDIO_ENABLED=False,
                ),
                client_pool=SimpleNamespace(aclose=_aclose),  # type: ignore[arg-type]
            )
            connector = MCPConnector(registry)
            connectors = ConnectorRegistry()
            connectors.register(connector)
            connectors.register(
                DesktopBridgeConnector(FakeDesktopDispatcher()),
                aliases=["windows"],
            )
            capabilities = await connectors.capabilities()
            observed = await connector.observe({"server": "local-tools"})
            verify_ok = await connector.verify(
                {"server": "local-tools", "tool": "echo", "authorized": True}
            )
            verify_denied = await connector.verify(
                {"server": "local-tools", "tool": "purge", "authorized": True}
            )
            auth = await connectors.auth_status()
            await connectors.aclose()
            assert closed == ["closed"]
            return connectors, capabilities, (observed, verify_ok, verify_denied), auth
        finally:
            await engine.dispose()

    connectors, capabilities, mcp_payload, auth = asyncio.run(scenario())

    assert connectors.names() == ["mcp", "desktop"]
    assert connectors.get("windows") is not None
    assert capabilities["mcp"]["success"] is True
    assert capabilities["mcp"]["data"]["servers"][0]["name"] == "local-tools"
    assert capabilities["desktop"]["data"]["resource"] == "desktop"

    observed, verify_ok, verify_denied = mcp_payload
    assert observed["success"] is True
    assert len(observed["data"]["tools"]) == 2
    assert verify_ok["success"] is True
    assert verify_denied["success"] is False
    assert auth["mcp"]["data"]["servers"][0]["authorized_tools"] == 1
    assert auth["mcp"]["data"]["servers"][0][
        "destroyed_requires_confirmation"
    ] == ["purge"]
