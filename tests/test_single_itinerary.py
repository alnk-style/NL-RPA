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
from app.engine.execution_manager import (
    ExecutionManager,
    ExecutionStateError,
)
from app.engine.runner import ExecutionResult
from app.models.execution import ExecutionLog
from app.models.user import User
from app.models.workflow import Workflow
from app.models.workflow_version import WorkflowVersion


def _plan(url: str) -> dict:
    return {
        "schema_version": 1,
        "execution_mode": "structured",
        "executor_type": "web",
        "steps": [
            {
                "step_id": 1,
                "executor": "web",
                "action": "goto",
                "params": {"url": url},
            }
        ],
    }


def test_execution_uses_version_plan_and_policy_snapshot() -> None:
    async def scenario() -> tuple[dict, ExecutionLog]:
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
        captured: dict = {}

        class FakeEngine:
            async def execute_with_debug(self, workflow, context):
                captured.update(workflow.structured_plan)
                return ExecutionResult(
                    success=True,
                    output="ok",
                    error=None,
                    screenshots=[],
                    step_logs=[],
                    duration_seconds=0.1,
                )

        async with session_factory() as session:
            session.add(
                User(
                    id=1,
                    username="u",
                    email="u@example.com",
                    hashed_password="x",
                )
            )
            workflow = Workflow(
                user_id=1,
                name="one",
                natural_language_input="goal",
                structured_plan=_plan("https://draft.example"),
            )
            session.add(workflow)
            await session.flush()
            version = WorkflowVersion(
                workflow_id=workflow.id,
                version=1,
                plan=_plan("https://snapshot.example"),
                validation={"valid": True},
                action_catalog_version="2026-10-28.1",
            )
            session.add(version)
            await session.flush()
            workflow.active_version_id = version.id
            workflow.status = "published"
            await session.commit()
            workflow_id = workflow.id
            version_id = version.id

        manager = ExecutionManager(
            FakeEngine(),
            session_factory=session_factory,
        )
        execution = await manager.start_workflow(workflow_id)
        await manager.execute_existing(execution.id)

        async with session_factory() as session:
            stored = await session.get(ExecutionLog, execution.id)
            assert stored is not None
            assert stored.workflow_version_id == version_id
            assert stored.execution_policy_snapshot is not None
        await engine.dispose()
        return captured, stored

    captured, stored = asyncio.run(scenario())
    assert captured["steps"][0]["params"]["url"].endswith(
        "snapshot.example"
    )
    assert stored.status == "success"


def test_same_workflow_rejects_second_active_execution() -> None:
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

        class FakeEngine:
            async def execute_with_debug(self, workflow, context):
                await asyncio.sleep(0.2)
                return ExecutionResult(
                    success=True,
                    output="ok",
                    error=None,
                    screenshots=[],
                    step_logs=[],
                    duration_seconds=0.2,
                )

        async with session_factory() as session:
            session.add(
                User(
                    id=1,
                    username="u",
                    email="u@example.com",
                    hashed_password="x",
                )
            )
            workflow = Workflow(
                user_id=1,
                name="one",
                natural_language_input="goal",
                structured_plan=_plan("https://example.com"),
            )
            session.add(workflow)
            await session.flush()
            version = WorkflowVersion(
                workflow_id=workflow.id,
                version=1,
                plan=_plan("https://example.com"),
                validation={"valid": True},
                action_catalog_version="2026-10-28.1",
            )
            session.add(version)
            await session.flush()
            workflow.active_version_id = version.id
            workflow.status = "published"
            await session.commit()
            workflow_id = workflow.id

        manager = ExecutionManager(
            FakeEngine(),
            session_factory=session_factory,
        )
        first = await manager.start_workflow(workflow_id)
        with pytest.raises(ExecutionStateError, match="active execution"):
            await manager.start_workflow(workflow_id)
        await manager.execute_existing(first.id)
        await engine.dispose()

    asyncio.run(scenario())
