"""MCP 步骤与混合执行、检查点续跑的集成测试。"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

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
    RISK_READ_ONLY,
)
from app.engine.execution_manager import ExecutionManager
from app.engine.hybrid_executor import HybridExecutor
from app.engine.mcp_client import MCPServerConfig, MCPToolCall
from app.engine.mcp_registry import MCPRegistry
from app.engine.runner import ExecutionResult
from app.models.execution import ExecutionLog
from app.models.mcp import MCPServer, ToolCallAudit
from app.models.user import User
from app.models.workflow import Workflow
from tests.support.versioned_workflows import activate_test_version


class FakeWebExecutor:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.contexts: list[dict[str, Any]] = []

    @asynccontextmanager
    async def browser_session(self):
        yield object()

    async def execute_steps(
        self,
        steps: list[dict[str, Any]],
        *,
        page: Any,
        context: dict[str, Any] | None = None,
        execution_id: Any = None,
        step_offset: int = 0,
        completed_idempotency_keys: list[str] | None = None,
        **_: Any,
    ) -> ExecutionResult:
        step = steps[0]
        self.calls.append(step)
        self.contexts.append(dict(context or {}))
        keys = list(completed_idempotency_keys or [])
        key = step.get("idempotency_key")
        if key and key not in keys:
            keys.append(key)
        return ExecutionResult(
            success=True,
            output=f"web {step.get('action')} ok",
            error=None,
            screenshots=[],
            step_logs=[
                {
                    "step_index": step_offset,
                    "step_id": step.get("step_id"),
                    "executor": "web",
                    "action": step.get("action"),
                    "status": "success",
                }
            ],
            duration_seconds=0.01,
            details={"completed_idempotency_keys": keys},
        )


class FakeDesktopExecutor:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    async def execute(
        self,
        workflow: Any,
        context: dict[str, Any] | None = None,
    ) -> ExecutionResult:
        self.calls.append((workflow, dict(context or {})))
        step = workflow.structured_plan["steps"][0]
        keys = list((context or {}).get("completed_idempotency_keys") or [])
        key = step.get("idempotency_key")
        if key and key not in keys:
            keys.append(key)
        return ExecutionResult(
            success=True,
            output=f"desktop {step.get('action')} ok",
            error=None,
            screenshots=[],
            step_logs=[
                {
                    "step_index": (context or {}).get("step_offset", 0),
                    "step_id": step.get("step_id"),
                    "executor": "windows",
                    "action": step.get("action"),
                    "status": "success",
                }
            ],
            duration_seconds=0.01,
            details={"completed_idempotency_keys": keys},
        )


class FakeMCPPool:
    def __init__(self, results: list[MCPToolCall] | None = None) -> None:
        self.results = list(results or [])
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.semaphore = asyncio.Semaphore(2)

    def get(self, config: MCPServerConfig) -> "FakeMCPPool":
        return self

    def drop(self, key: Any) -> None:
        return None

    async def call_tool(
        self,
        tool: str,
        arguments: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> MCPToolCall:
        self.calls.append((tool, dict(arguments or {})))
        if self.results:
            return self.results.pop(0)
        return MCPToolCall(
            success=True,
            result={"text": "mcp ok", "is_error": False},
        )

    async def aclose(self) -> None:
        return None


def _config(**overrides: Any) -> SimpleNamespace:
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


async def _seed(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    steps: list[dict[str, Any]],
    tools: list[dict[str, Any]],
) -> int:
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
                enabled=True,
                default_risk="mutating",
                tools=tools,
            )
        )
        workflow = Workflow(
            user_id=1,
            name="mcp-flow",
            natural_language_input="test",
            executor_type="web",
            structured_plan={"steps": steps},
        )
        session.add(workflow)
        await session.flush()
        await activate_test_version(
            session,
            workflow,
            plan={
                "executor_type": "web",
                "execution_mode": "structured",
                "steps": steps,
            },
        )
        await session.commit()
        await session.refresh(workflow)
        return int(workflow.id)


def _engine(web: FakeWebExecutor) -> Any:
    return SimpleNamespace(web_executor=web)


def test_manager_runs_web_mcp_windows_mixed_flow() -> None:
    async def scenario() -> tuple[
        ExecutionLog,
        FakeWebExecutor,
        FakeDesktopExecutor,
        FakeMCPPool,
        list[ToolCallAudit],
    ]:
        session_factory, engine = await _database()
        try:
            workflow_id = await _seed(
                session_factory,
                steps=[
                    {
                        "step_id": 1,
                        "executor": "web",
                        "action": "goto",
                        "params": {"url": "https://example.com"},
                        "depends_on": [],
                    },
                    {
                        "step_id": 2,
                        "executor": "mcp",
                        "action": "call_tool",
                        "params": {
                            "server": "local-tools",
                            "tool": "lookup",
                            "arguments": {"key": "demo"},
                        },
                        "depends_on": [1],
                    },
                    {
                        "step_id": 3,
                        "executor": "windows",
                        "action": "launch_app",
                        "params": {"path": "notepad.exe"},
                        "depends_on": [],
                    },
                    {
                        "step_id": 4,
                        "executor": "web",
                        "action": "screenshot",
                        "depends_on": [2, 3],
                    },
                ],
                tools=[
                    {
                        "name": "lookup",
                        "risk_level": RISK_READ_ONLY,
                        "authorized": False,
                    }
                ],
            )
            web = FakeWebExecutor()
            desktop = FakeDesktopExecutor()
            pool = FakeMCPPool()
            registry = MCPRegistry(
                session_factory=session_factory,
                config=_config(),
                client_pool=pool,  # type: ignore[arg-type]
            )
            manager = ExecutionManager(
                _engine(web),
                session_factory=session_factory,
            )
            manager._hybrid = HybridExecutor(
                _engine(web),
                desktop,
                session_factory=session_factory,
                config=_config(),
                mcp_registry=registry,
            )
            execution = await manager.start_workflow(workflow_id)
            await manager.execute_existing(execution.id)
            async with session_factory() as session:
                stored = await session.get(ExecutionLog, execution.id)
                result = await session.execute(select(ToolCallAudit))
                audits = list(result.scalars().all())
            assert stored is not None
            return stored, web, desktop, pool, audits
        finally:
            await engine.dispose()

    stored, web, desktop, pool, audits = asyncio.run(scenario())

    assert stored.status == "success"
    assert [call["action"] for call in web.calls] == ["goto", "screenshot"]
    assert len(desktop.calls) == 1
    assert pool.calls == [("lookup", {"key": "demo"})]
    assert len(audits) == 1
    audit = audits[0]
    assert audit.tool == "lookup"
    assert audit.status == "success"
    assert audit.execution_id == stored.id
    assert audit.workflow_id == stored.workflow_id

    mcp_logs = [
        entry
        for entry in stored.step_logs or []
        if entry.get("executor") == "mcp"
    ]
    assert len(mcp_logs) == 1
    assert mcp_logs[0]["server"] == "local-tools"
    assert mcp_logs[0]["tool"] == "lookup"
    assert mcp_logs[0]["audit_id"] == audit.id
    assert mcp_logs[0]["status"] == "success"


def test_unauthorized_destructive_step_is_blocked_and_audited() -> None:
    async def scenario() -> tuple[
        ExecutionLog,
        FakeMCPPool,
        list[ToolCallAudit],
    ]:
        session_factory, engine = await _database()
        try:
            workflow_id = await _seed(
                session_factory,
                steps=[
                    {
                        "step_id": 1,
                        "executor": "mcp",
                        "action": "call_tool",
                        "params": {
                            "server": "local-tools",
                            "tool": "purge",
                            "arguments": {"scope": "all"},
                            "confirm": True,
                        },
                        "depends_on": [],
                    },
                ],
                tools=[
                    {
                        "name": "purge",
                        "risk_level": RISK_DESTRUCTIVE,
                        "authorized": False,
                        "confirmed": False,
                    }
                ],
            )
            web = FakeWebExecutor()
            pool = FakeMCPPool()
            registry = MCPRegistry(
                session_factory=session_factory,
                config=_config(),
                client_pool=pool,  # type: ignore[arg-type]
            )
            manager = ExecutionManager(
                _engine(web),
                session_factory=session_factory,
            )
            manager._hybrid = HybridExecutor(
                _engine(web),
                FakeDesktopExecutor(),
                session_factory=session_factory,
                config=_config(),
                mcp_registry=registry,
            )
            execution = await manager.start_workflow(workflow_id)
            await manager.execute_existing(execution.id)
            async with session_factory() as session:
                stored = await session.get(ExecutionLog, execution.id)
                result = await session.execute(select(ToolCallAudit))
                audits = list(result.scalars().all())
            assert stored is not None
            return stored, pool, audits
        finally:
            await engine.dispose()

    stored, pool, audits = asyncio.run(scenario())

    assert stored.status == "failed"
    assert pool.calls == []
    assert len(audits) == 1
    assert audits[0].status == "denied"
    assert audits[0].decision == "denied"
    assert audits[0].risk_level == RISK_DESTRUCTIVE
    failed = (stored.step_logs or [])[0]
    assert failed["status"] == "failed"
    assert failed["decision"] == "denied"
    checkpoint = (stored.step_logs or [])[-1]
    assert checkpoint["type"] == "checkpoint"


def test_destructive_step_resumes_after_authorization() -> None:
    async def scenario() -> tuple[
        ExecutionLog,
        FakeMCPPool,
        list[ToolCallAudit],
    ]:
        session_factory, engine = await _database()
        try:
            workflow_id = await _seed(
                session_factory,
                steps=[
                    {
                        "step_id": 1,
                        "executor": "mcp",
                        "action": "call_tool",
                        "params": {
                            "server": "local-tools",
                            "tool": "purge",
                            "arguments": {"scope": "all"},
                        },
                        "depends_on": [],
                    },
                ],
                tools=[
                    {
                        "name": "purge",
                        "risk_level": RISK_DESTRUCTIVE,
                        "authorized": False,
                    }
                ],
            )
            web = FakeWebExecutor()
            pool = FakeMCPPool()
            registry = MCPRegistry(
                session_factory=session_factory,
                config=_config(),
                client_pool=pool,  # type: ignore[arg-type]
            )
            manager = ExecutionManager(
                _engine(web),
                session_factory=session_factory,
            )
            manager._hybrid = HybridExecutor(
                _engine(web),
                FakeDesktopExecutor(),
                session_factory=session_factory,
                config=_config(),
                mcp_registry=registry,
            )
            execution = await manager.start_workflow(workflow_id)
            await manager.execute_existing(execution.id)

            authorized = await registry.authorize_tool(
                "local-tools",
                "purge",
                authorized=True,
                confirmed=True,
                risk_level=RISK_DESTRUCTIVE,
                user_id=1,
            )
            assert authorized["success"] is True

            await manager.resume(execution.id)
            await manager.execute_existing(execution.id)
            async with session_factory() as session:
                stored = await session.get(ExecutionLog, execution.id)
                result = await session.execute(select(ToolCallAudit))
                audits = list(result.scalars().all())
            assert stored is not None
            return stored, pool, audits
        finally:
            await engine.dispose()

    stored, pool, audits = asyncio.run(scenario())

    assert stored.status == "success"
    assert pool.calls == [("purge", {"scope": "all"})]
    assert [audit.status for audit in audits] == ["denied", "success"]
    assert audits[1].decision == "allowed"


def test_mcp_step_skips_completed_idempotency_key() -> None:
    async def scenario() -> tuple[ExecutionResult, FakeMCPPool]:
        session_factory, engine = await _database()
        try:
            await _seed(
                session_factory,
                steps=[
                    {
                        "step_id": 1,
                        "executor": "mcp",
                        "action": "call_tool",
                        "params": {
                            "server": "local-tools",
                            "tool": "lookup",
                            "arguments": {},
                        },
                        "idempotency_key": "lookup-done",
                        "depends_on": [],
                    }
                ],
                tools=[
                    {
                        "name": "lookup",
                        "risk_level": RISK_READ_ONLY,
                        "authorized": True,
                    }
                ],
            )
            web = FakeWebExecutor()
            pool = FakeMCPPool()
            registry = MCPRegistry(
                session_factory=session_factory,
                config=_config(),
                client_pool=pool,  # type: ignore[arg-type]
            )
            executor = HybridExecutor(
                _engine(web),
                FakeDesktopExecutor(),
                session_factory=session_factory,
                config=_config(),
                mcp_registry=registry,
            )
            workflow = SimpleNamespace(
                id=1,
                user_id=1,
                executor_type="web",
                structured_plan={
                    "steps": [
                        {
                            "step_id": 1,
                            "executor": "mcp",
                            "action": "call_tool",
                            "params": {
                                "server": "local-tools",
                                "tool": "lookup",
                            },
                            "idempotency_key": "lookup-done",
                            "depends_on": [],
                        }
                    ]
                },
            )
            result = await executor.execute(
                workflow,
                {"completed_idempotency_keys": ["lookup-done"]},
            )
            return result, pool
        finally:
            await engine.dispose()

    result, pool = asyncio.run(scenario())

    assert result.success is True
    assert pool.calls == []
    assert [entry["status"] for entry in result.step_logs] == ["skipped"]
    assert (result.details or {})["completed_idempotency_keys"] == [
        "lookup-done"
    ]


def test_mcp_structured_result_path_writes_variable(
    tmp_path: Path,
) -> None:
    async def scenario() -> tuple[ExecutionResult, FakeWebExecutor]:
        session_factory, engine = await _database()
        try:
            artifact = tmp_path / "report.csv"
            artifact.write_text("name,count\nalice,2\n", encoding="utf-8")
            workflow_id = await _seed(
                session_factory,
                steps=[
                    {
                        "step_id": 1,
                        "executor": "mcp",
                        "action": "call_tool",
                        "params": {
                            "server": "local-tools",
                            "tool": "export_report",
                            "arguments": {"format": "csv"},
                            "result_var": "report_path",
                            "result_path": "/artifacts/0/path",
                        },
                        "depends_on": [],
                    },
                    {
                        "step_id": 2,
                        "executor": "web",
                        "action": "goto",
                        "params": {"url": "https://example.com"},
                        "depends_on": [1],
                    },
                ],
                tools=[
                    {
                        "name": "export_report",
                        "risk_level": RISK_READ_ONLY,
                        "authorized": True,
                    }
                ],
            )
            async with session_factory() as session:
                workflow = await session.get(Workflow, workflow_id)
                assert workflow is not None

            pool = FakeMCPPool(
                results=[
                    MCPToolCall(
                        success=True,
                        result={
                            "text": json.dumps(
                                {"artifacts": [{"path": str(artifact)}]}
                            ),
                            "structured": {
                                "ok": True,
                                "artifacts": [
                                    {
                                        "name": "report.csv",
                                        "path": str(artifact),
                                        "media_type": "text/csv",
                                        "size": artifact.stat().st_size,
                                    }
                                ],
                            },
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
            web = FakeWebExecutor()
            executor = HybridExecutor(
                _engine(web),
                FakeDesktopExecutor(),
                session_factory=session_factory,
                config=_config(),
                mcp_registry=registry,
            )
            return (
                await executor.execute(workflow, {"variables": {}}),
                web,
            )
        finally:
            await engine.dispose()

    result, web = asyncio.run(scenario())

    assert result.success is True
    assert (result.details or {})["extracted"]["report_path"].endswith(
        "report.csv"
    )
    assert len(result.artifacts) == 1
    assert result.artifacts[0]["source"] == {
        "server": "local-tools",
        "tool": "export_report",
    }
    assert web.contexts[0]["variables"]["report_path"].endswith(
        "report.csv"
    )


def test_mcp_text_result_falls_back_to_result_var() -> None:
    async def scenario() -> ExecutionResult:
        session_factory, engine = await _database()
        try:
            workflow_id = await _seed(
                session_factory,
                steps=[
                    {
                        "step_id": 1,
                        "executor": "mcp",
                        "action": "call_tool",
                        "params": {
                            "server": "local-tools",
                            "tool": "lookup",
                            "arguments": {},
                            "result_var": "lookup_text",
                        },
                        "depends_on": [],
                    }
                ],
                tools=[
                    {
                        "name": "lookup",
                        "risk_level": RISK_READ_ONLY,
                        "authorized": True,
                    }
                ],
            )
            async with session_factory() as session:
                workflow = await session.get(Workflow, workflow_id)
                assert workflow is not None
            pool = FakeMCPPool()
            registry = MCPRegistry(
                session_factory=session_factory,
                config=_config(),
                client_pool=pool,  # type: ignore[arg-type]
            )
            executor = HybridExecutor(
                _engine(FakeWebExecutor()),
                FakeDesktopExecutor(),
                session_factory=session_factory,
                config=_config(),
                mcp_registry=registry,
            )
            return await executor.execute(workflow, {"variables": {}})
        finally:
            await engine.dispose()

    result = asyncio.run(scenario())

    assert result.success is True
    assert (result.details or {})["extracted"]["lookup_text"] == "mcp ok"


def test_mcp_missing_result_path_fails_step(tmp_path: Path) -> None:
    async def scenario() -> ExecutionResult:
        session_factory, engine = await _database()
        try:
            workflow_id = await _seed(
                session_factory,
                steps=[
                    {
                        "step_id": 1,
                        "executor": "mcp",
                        "action": "call_tool",
                        "params": {
                            "server": "local-tools",
                            "tool": "lookup",
                            "arguments": {},
                            "result_var": "missing",
                            "result_path": "missing.value",
                        },
                        "depends_on": [],
                    }
                ],
                tools=[
                    {
                        "name": "lookup",
                        "risk_level": RISK_READ_ONLY,
                        "authorized": True,
                    }
                ],
            )
            async with session_factory() as session:
                workflow = await session.get(Workflow, workflow_id)
                assert workflow is not None
            pool = FakeMCPPool(
                results=[
                    MCPToolCall(
                        success=True,
                        result={
                            "text": "fallback",
                            "structured": {"status": "ok"},
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
            executor = HybridExecutor(
                _engine(FakeWebExecutor()),
                FakeDesktopExecutor(),
                session_factory=session_factory,
                config=_config(),
                mcp_registry=registry,
            )
            return await executor.execute(workflow, {"variables": {}})
        finally:
            await engine.dispose()

    result = asyncio.run(scenario())

    assert result.success is False
    assert "result_path" in (result.error or "")


def test_mcp_artifacts_are_persisted_in_execution_log(
    tmp_path: Path,
) -> None:
    async def scenario() -> ExecutionLog:
        session_factory, engine = await _database()
        try:
            artifact = tmp_path / "export.txt"
            artifact.write_text("hello", encoding="utf-8")
            workflow_id = await _seed(
                session_factory,
                steps=[
                    {
                        "step_id": 1,
                        "executor": "mcp",
                        "action": "call_tool",
                        "params": {
                            "server": "local-tools",
                            "tool": "export_file",
                            "arguments": {},
                        },
                        "depends_on": [],
                    }
                ],
                tools=[
                    {
                        "name": "export_file",
                        "risk_level": RISK_READ_ONLY,
                        "authorized": True,
                    }
                ],
            )
            pool = FakeMCPPool(
                results=[
                    MCPToolCall(
                        success=True,
                        result={
                            "text": "exported",
                            "structured": {
                                "data": {
                                    "artifacts": [
                                        {
                                            "name": "export.txt",
                                            "path": str(artifact),
                                            "media_type": "text/plain",
                                            "size": 5,
                                        }
                                    ]
                                }
                            },
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
            manager = ExecutionManager(
                _engine(FakeWebExecutor()),
                session_factory=session_factory,
            )
            manager._hybrid = HybridExecutor(
                _engine(FakeWebExecutor()),
                FakeDesktopExecutor(),
                session_factory=session_factory,
                config=_config(),
                mcp_registry=registry,
            )
            execution = await manager.start_workflow(workflow_id)
            await manager.execute_existing(execution.id)
            async with session_factory() as session:
                stored = await session.get(ExecutionLog, execution.id)
                assert stored is not None
                return stored
        finally:
            await engine.dispose()

    stored = asyncio.run(scenario())

    assert stored.artifacts
    assert stored.artifacts[0]["path"].endswith("export.txt")
    assert stored.artifacts[0]["source"]["tool"] == "export_file"
