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

from app.database import Base
from app.engine.execution_manager import ExecutionManager
from app.engine.execution_manager import ExecutionStateError
from app.engine.runner import ExecutionResult
from app.models.agent import DesktopAgent
from app.models.execution import ExecutionLog
from app.models.user import User
from app.models.workflow import Workflow
from app.models.workflow_version import WorkflowVersion


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


async def _seed_workflow(
    session: AsyncSession,
    *,
    mode: str,
    host_policy: str,
    host_requirement: str,
) -> Workflow:
    user = User(
        id=1,
        username="host-execution",
        email="host@example.com",
        hashed_password="hash",
    )
    workflow = Workflow(
        id=1,
        user_id=1,
        name="host execution",
        natural_language_input="desktop",
        executor_type="windows",
        execution_policy={
            "mode": mode,
            "host_policy": host_policy,
        },
        structured_plan={
            "execution_mode": "structured",
            "steps": [
                {
                    "step_id": 1,
                    "executor": "windows",
                    "action": "launch_app",
                    "host_requirement": host_requirement,
                    "params": {"path": "notepad.exe"},
                }
            ],
        },
    )
    session.add_all([user, workflow])
    await session.flush()
    version = WorkflowVersion(
        workflow_id=workflow.id,
        version=1,
        plan=dict(workflow.structured_plan),
        validation={"valid": True},
        action_catalog_version="2026-10-28.1",
    )
    session.add(version)
    await session.flush()
    workflow.active_version_id = version.id
    workflow.status = "published"
    await session.commit()
    return workflow


class Client:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.web_executor = SimpleNamespace()

    async def execute_with_debug(self, *_args, **_kwargs):
        raise AssertionError("preflight must block before executor")


def test_manager_preflight_blocks_background_local_interactive_desktop() -> None:
    async def scenario() -> tuple[ExecutionLog, Client]:
        session_factory, engine = await _database()
        try:
            async with session_factory() as session:
                await _seed_workflow(
                    session,
                    mode="background_if_safe",
                    host_policy="local_only",
                    host_requirement="interactive_desktop",
                )
                session.add(
                    DesktopAgent(
                        id=1,
                        user_id=1,
                        name="local",
                        platform="windows",
                        version="0.2.0",
                        environment="local",
                        capabilities=["interactive_desktop"],
                        status="online",
                        token_hash="hash",
                    )
                )
                await session.commit()
            client = Client()
            manager = ExecutionManager(
                client,
                session_factory=session_factory,
            )
            manager.online_agent_ids = lambda: [1]
            execution = await manager.start_workflow(1)
            return execution, client
        finally:
            await engine.dispose()

    execution, client = asyncio.run(scenario())
    assert execution.status == "failed"
    assert "policy_conflict" in str(execution.error_message)
    assert client.calls == []


def test_execution_context_loads_host_affinity_from_execution() -> None:
    async def scenario() -> dict:
        session_factory, engine = await _database()
        try:
            async with session_factory() as session:
                workflow = await _seed_workflow(
                    session,
                    mode="interactive_preferred",
                    host_policy="local_then_isolated",
                    host_requirement="interactive_desktop",
                )
                execution = ExecutionLog(
                    workflow_id=workflow.id,
                    status="awaiting_user",
                    host_id=1,
                    host_environment="local",
                    host_bound=True,
                    host_affinity_hash="affinity",
                )
                session.add(execution)
                await session.commit()
                await session.refresh(execution)
                execution_id = execution.id

            manager = ExecutionManager(
                Client(),
                session_factory=session_factory,
            )
            async with session_factory() as session:
                workflow = await session.get(Workflow, 1)
            return await manager._execution_context(
                execution_id,
                workflow,
            )
        finally:
            await engine.dispose()

    context = asyncio.run(scenario())
    assert context["host_id"] == 1
    assert context["host_environment"] == "local"
    assert context["host_bound"] is True
    assert context["host_affinity_hash"] == "affinity"


def test_resume_rejects_lost_bound_host_before_running() -> None:
    async def scenario() -> tuple[ExecutionStateError, str]:
        session_factory, engine = await _database()
        try:
            async with session_factory() as session:
                workflow = await _seed_workflow(
                    session,
                    mode="interactive_preferred",
                    host_policy="local_then_isolated",
                    host_requirement="interactive_desktop",
                )
                session.add_all(
                    [
                        DesktopAgent(
                            id=1,
                            user_id=1,
                            name="bound",
                            platform="windows",
                            version="0.2.0",
                            environment="local",
                            capabilities=["interactive_desktop"],
                            status="offline",
                            token_hash="hash-1",
                        ),
                        DesktopAgent(
                            id=2,
                            user_id=1,
                            name="other",
                            platform="windows",
                            version="0.2.0",
                            environment="local",
                            capabilities=["interactive_desktop"],
                            status="online",
                            token_hash="hash-2",
                        ),
                    ]
                )
                execution = ExecutionLog(
                    workflow_id=workflow.id,
                    workflow_version_id=workflow.active_version_id,
                    status="paused",
                    host_id=1,
                    host_environment="local",
                    host_bound=True,
                    host_affinity_hash="affinity",
                    step_logs=[
                        {
                            "type": "checkpoint",
                            "action": "checkpoint",
                            "status": "awaiting_user",
                            "next_step_index": 1,
                        }
                    ],
                )
                session.add(execution)
                await session.commit()
                await session.refresh(execution)
                execution_id = execution.id

            manager = ExecutionManager(
                Client(),
                session_factory=session_factory,
            )
            manager.online_agent_ids = lambda: [2]
            caught = None
            try:
                await manager.resume(execution_id)
            except ExecutionStateError as exc:
                caught = exc
            async with session_factory() as session:
                status = (
                    await session.get(ExecutionLog, execution_id)
                ).status
            return caught, status
        finally:
            await engine.dispose()

    error, status = asyncio.run(scenario())
    assert isinstance(error, ExecutionStateError)
    assert "host_disconnected" in str(error)
    assert status == "paused"


def test_host_loss_allows_next_server_only_step_to_continue() -> None:
    async def scenario() -> None:
        session_factory, engine = await _database()
        try:
            async with session_factory() as session:
                workflow = await _seed_workflow(
                    session,
                    mode="interactive_preferred",
                    host_policy="local_then_isolated",
                    host_requirement="interactive_desktop",
                )
                version = await session.get(
                    WorkflowVersion,
                    workflow.active_version_id,
                )
                assert version is not None
                plan = {
                    **dict(version.plan),
                    "steps": [
                        dict(version.plan["steps"][0]),
                        {
                            "step_id": 2,
                            "executor": "transform",
                            "action": "filter",
                            "host_requirement": "any",
                            "params": {},
                        },
                    ],
                }
                version.plan = plan
                workflow.structured_plan = plan
                session.add(
                    DesktopAgent(
                        id=1,
                        user_id=1,
                        name="bound",
                        platform="windows",
                        version="0.2.0",
                        environment="local",
                        capabilities=["interactive_desktop"],
                        status="offline",
                        token_hash="hash",
                    )
                )
                execution = ExecutionLog(
                    workflow_id=workflow.id,
                    workflow_version_id=workflow.active_version_id,
                    status="paused",
                    host_id=1,
                    host_environment="local",
                    host_bound=True,
                    host_affinity_hash="affinity",
                    step_logs=[
                        {
                            "type": "checkpoint",
                            "action": "checkpoint",
                            "status": "awaiting_user",
                            "next_step_index": 1,
                        }
                    ],
                )
                session.add(execution)
                await session.commit()
                await session.refresh(execution)
            manager = ExecutionManager(
                Client(),
                session_factory=session_factory,
            )
            manager.online_agent_ids = lambda: []
            async with session_factory() as session:
                execution = await session.get(
                    ExecutionLog,
                    execution.id,
                )
                workflow = await session.get(Workflow, 1)
                assert execution is not None and workflow is not None
                await manager._require_bound_host(
                    session,
                    execution,
                    workflow,
                )
        finally:
            await engine.dispose()

    asyncio.run(scenario())
