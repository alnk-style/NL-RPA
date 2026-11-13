"""workspace 并发配额、Agent 绑定与历史数据回填。"""

from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.engine.desktop_dispatcher import DesktopDispatcher
from app.engine.execution_manager import (
    ExecutionManager,
    ExecutionStateError,
)
from app.engine.runner import ExecutionResult
from app.models.agent import DesktopAgent
from app.models.desktop_task import DesktopTask
from app.models.execution import ExecutionLog
from app.models.user import User
from app.models.workflow import Workflow
from tests.support.versioned_workflows import activate_test_version
from app.models.workspace import Workspace
from app.security.workspaces import backfill_workspace_ids


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


async def _seed_users_and_workspaces(
    session: AsyncSession,
    *,
    quota: dict | None = None,
) -> tuple[Workspace, Workspace, Workflow, Workflow]:
    session.add_all(
        [
            User(
                id=1,
                username="alice",
                email="a@example.com",
                hashed_password="x",
            ),
            User(
                id=2,
                username="bob",
                email="b@example.com",
                hashed_password="x",
            ),
        ]
    )
    await session.commit()
    ws_a = Workspace(
        owner_id=1,
        name="A",
        slug="default",
        is_default=True,
        quota=quota or {},
    )
    ws_b = Workspace(
        owner_id=2,
        name="B",
        slug="default",
        is_default=True,
        quota={},
    )
    session.add_all([ws_a, ws_b])
    await session.commit()
    await session.refresh(ws_a)
    await session.refresh(ws_b)
    flow_a = Workflow(
        user_id=1,
        workspace_id=ws_a.id,
        name="flow-a",
        natural_language_input="a",
    )
    flow_b = Workflow(
        user_id=1,
        workspace_id=ws_b.id,
        name="flow-b",
        natural_language_input="b",
    )
    session.add_all([flow_a, flow_b])
    await session.flush()
    await activate_test_version(session, flow_a)
    await activate_test_version(session, flow_b)
    await session.commit()
    await session.refresh(flow_a)
    await session.refresh(flow_b)
    return ws_a, ws_b, flow_a, flow_b


def test_execution_quota_blocks_parallel_runs() -> None:
    async def scenario() -> None:
        session_factory, engine = await _database()
        async with session_factory() as session:
            ws_a, _ws_b, flow_a, _flow_b = (
                await _seed_users_and_workspaces(
                    session,
                    quota={"max_concurrent_executions": 1},
                )
            )
            session.add(
                ExecutionLog(
                    workflow_id=flow_a.id,
                    workspace_id=ws_a.id,
                    status="running",
                    started_at=datetime.utcnow(),
                )
            )
            await session.commit()

        engine_mock = SimpleNamespace(
            execute_with_debug=AsyncMock(
                return_value=ExecutionResult(
                    success=True,
                    output="",
                    error=None,
                    screenshots=[],
                    step_logs=[],
                    duration_seconds=0.01,
                )
            )
        )
        manager = ExecutionManager(
            engine_mock,
            session_factory=session_factory,
            config=SimpleNamespace(
                WORKSPACE_DEFAULT_MAX_CONCURRENT_EXECUTIONS=2,
                WORKSPACE_DEFAULT_MAX_CONCURRENT_AGENT_TASKS=2,
            ),
        )
        with pytest.raises(ExecutionStateError):
            await manager.start_workflow(
                flow_a.id,
                workspace_id=ws_a.id,
            )

        async with session_factory() as session:
            flow = await session.get(Workflow, flow_a.id)
            other = Workflow(
                user_id=2,
                workspace_id=(await session.get(Workspace, 2)).id,
                name="other",
                natural_language_input="x",
            )
            session.add(other)
            await session.flush()
            await activate_test_version(session, other)
            await session.commit()
            await session.refresh(other)
            # 其他工作区不受 A 的配额影响。
            started = await manager.start_workflow(
                other.id,
                workspace_id=other.workspace_id,
            )
            await manager.execute_existing(started.id)
            await manager.shutdown()
            assert started.id is not None
            assert flow is not None
        await engine.dispose()

    asyncio.run(scenario())


def test_desktop_agent_binding_by_workspace() -> None:
    async def scenario() -> None:
        session_factory, engine = await _database()
        async with session_factory() as session:
            ws_a, ws_b, flow_a, _flow_b = (
                await _seed_users_and_workspaces(session)
            )
            session.add_all(
                [
                    DesktopAgent(
                        id=1,
                        user_id=1,
                        workspace_id=ws_b.id,
                        name="agent-in-b",
                        platform="windows",
                        version="1",
                            capabilities=["interactive_desktop"],
                        allowed_apps=[],
                        allowed_doc_dirs=[],
                        token_hash="a",
                    ),
                ]
            )
            await session.commit()

            connection_manager = SimpleNamespace(
                get_online_agents=lambda: [1],
            )
            dispatcher = DesktopDispatcher(
                connection_manager,
                session_factory=session_factory,
            )
            selected = await dispatcher._select_agent(flow_a)
            assert selected is None

            # 把 Agent 换到 A 工作区后可以被选中。
            async with session_factory() as session:
                agent = await session.get(DesktopAgent, 1)
                agent.workspace_id = ws_a.id
                await session.commit()
            selected = await dispatcher._select_agent(flow_a)
            assert selected == 1
        await engine.dispose()

    asyncio.run(scenario())


def test_desktop_task_quota_blocks_dispatch() -> None:
    async def scenario() -> None:
        session_factory, engine = await _database()
        async with session_factory() as session:
            ws_a, _ws_b, flow_a, _flow_b = (
                await _seed_users_and_workspaces(
                    session,
                    quota={"max_concurrent_agent_tasks": 1},
                )
            )
            flow_a.structured_plan = {
                "executor_type": "windows",
                "steps": [
                    {
                        "step_id": 1,
                        "executor": "windows",
                        "action": "activate_window",
                        "params": {"title": "记事本"},
                    }
                ],
            }
            session.add(
                DesktopAgent(
                    id=1,
                    user_id=1,
                    workspace_id=ws_a.id,
                    name="agent-a",
                    platform="windows",
                    version="1",
                    capabilities=["window"],
                    allowed_apps=[],
                    allowed_doc_dirs=[],
                    token_hash="a",
                )
            )
            await session.commit()
            session.add(
                DesktopTask(
                    agent_id=1,
                    workflow_id=flow_a.id,
                    actions=[],
                    status="running",
                )
            )
            await session.commit()

            dispatcher = DesktopDispatcher(
                SimpleNamespace(get_online_agents=lambda: [1]),
                session_factory=session_factory,
            )
            result = await dispatcher.dispatch(flow_a, {})
            assert result.success is False
            assert "配额" in (result.error or "")
        await engine.dispose()

    asyncio.run(scenario())


def test_backfill_workspace_ids_assigns_legacy_rows() -> None:
    async def scenario() -> None:
        session_factory, engine = await _database()
        async with session_factory() as session:
            session.add(
                User(
                    id=1,
                    username="legacy",
                    email="legacy@example.com",
                    hashed_password="x",
                )
            )
            workflow = Workflow(
                user_id=1,
                name="legacy-flow",
                natural_language_input="x",
            )
            session.add(workflow)
            await session.commit()
            await session.refresh(workflow)
            execution = ExecutionLog(
                workflow_id=workflow.id,
                status="success",
            )
            session.add(execution)
            await session.commit()
            await session.refresh(execution)

        counts = await backfill_workspace_ids(session_factory)
        assert counts["workflow"] == 1
        assert counts["execution_log"] == 1

        async with session_factory() as session:
            workspace = (
                await session.execute(
                    Workspace.__table__.select()
                )
            ).mappings().one()
            stored_workflow = await session.get(Workflow, workflow.id)
            stored_execution = await session.get(
                ExecutionLog,
                execution.id,
            )
            assert stored_workflow.workspace_id == workspace["id"]
            assert stored_execution.workspace_id == workspace["id"]
        await engine.dispose()

    asyncio.run(scenario())
