from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.api import executions as executions_api
from app.api.dependencies import get_current_user
from app.database import Base, get_db
from app.engine.execution_manager import (
    ExecutionManager,
    ExecutionStateError,
)
from app.engine.runner import ExecutionResult
from app.models.execution import ExecutionLog
from app.models.user import User
from app.models.workflow import Workflow
from app.models.workflow_version import WorkflowVersion


def _plan(value: int = 1000) -> dict:
    return {
        "schema_version": 1,
        "execution_mode": "structured",
        "executor_type": "transform",
        "variables": [{"name": "rows", "default": []}],
        "steps": [
            {
                "step_id": 1,
                "executor": "transform",
                "action": "filter",
                "params": {
                    "values": "${rows}",
                    "field": "amount",
                    "operator": "gt",
                    "value": value,
                },
            }
        ],
    }


async def _database():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    return factory, engine


async def _seed(
    factory: async_sessionmaker[AsyncSession],
    *,
    published: bool,
) -> tuple[int, int | None]:
    async with factory() as session:
        session.add(
            User(
                id=1,
                username="runner",
                email="runner@example.com",
                hashed_password="hash",
            )
        )
        workflow = Workflow(
            user_id=1,
            name="Versioned run",
            natural_language_input="goal",
            structured_plan=_plan(),
            status="draft",
        )
        session.add(workflow)
        await session.flush()
        version_id = None
        if published:
            version = WorkflowVersion(
                workflow_id=workflow.id,
                version=1,
                plan=_plan(),
                validation={"valid": True},
                action_catalog_version="2026-10-28.1",
            )
            session.add(version)
            await session.flush()
            version_id = version.id
            workflow.active_version_id = version.id
            workflow.status = "published"
        await session.commit()
        return workflow.id, version_id


class _Engine:
    def __init__(self) -> None:
        self.plans: list[dict] = []

    async def execute_with_debug(self, workflow, context):
        self.plans.append(dict(workflow.structured_plan))
        return ExecutionResult(
            success=True,
            output="ok",
            error=None,
            screenshots=[],
            step_logs=[],
            duration_seconds=0.01,
        )


def test_formal_run_rejects_workflow_without_active_version() -> None:
    async def scenario():
        factory, engine = await _database()
        try:
            workflow_id, _ = await _seed(factory, published=False)
            manager = ExecutionManager(
                _Engine(),
                session_factory=factory,
            )
            with pytest.raises(
                ExecutionStateError,
                match="active version",
            ):
                await manager.start_workflow(workflow_id)
            async with factory() as session:
                executions = await session.scalar(
                    select(func.count(ExecutionLog.id))
                )
                versions = await session.scalar(
                    select(func.count(WorkflowVersion.id))
                )
            return int(executions or 0), int(versions or 0)
        finally:
            await engine.dispose()

    executions, versions = asyncio.run(scenario())
    assert executions == 0
    assert versions == 0


def test_formal_run_rejects_non_active_version_override() -> None:
    async def scenario():
        factory, engine = await _database()
        try:
            workflow_id, active_id = await _seed(
                factory,
                published=True,
            )
            manager = ExecutionManager(
                _Engine(),
                session_factory=factory,
            )
            with pytest.raises(
                ExecutionStateError,
                match="Only the active",
            ):
                await manager.start_workflow(
                    workflow_id,
                    version_id=int(active_id or 0) + 999,
                )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_formal_run_api_returns_conflict_for_draft_workflow() -> None:
    async def scenario():
        factory, engine = await _database()
        try:
            workflow_id, _ = await _seed(factory, published=False)
            async with factory() as session:
                user = await session.get(User, 1)
                assert user is not None
                app = FastAPI()
                app.include_router(executions_api.router)
                app.state.engine = _Engine()
                app.state.execution_manager = None

                async def override_db():
                    yield session

                app.dependency_overrides[get_db] = override_db
                app.dependency_overrides[get_current_user] = lambda: user
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(
                    transport=transport,
                    base_url="http://test",
                ) as client:
                    response = await client.post(
                        f"/api/executions/{workflow_id}/run"
                    )
                return response
        finally:
            await engine.dispose()

    response = asyncio.run(scenario())
    assert response.status_code == 409
    assert "active version" in response.json()["detail"].lower()


def test_resume_can_load_pinned_historical_version() -> None:
    from app.services.execution_versions import ExecutionVersionService

    async def scenario():
        factory, engine = await _database()
        try:
            workflow_id, active_id = await _seed(
                factory,
                published=True,
            )
            async with factory() as session:
                workflow = await session.get(Workflow, workflow_id)
                assert workflow is not None
                historical = WorkflowVersion(
                    workflow_id=workflow_id,
                    version=2,
                    plan=_plan(2000),
                    validation={"valid": True},
                    action_catalog_version="2026-10-28.1",
                )
                session.add(historical)
                await session.flush()
                historical_id = historical.id
                workflow.active_version_id = historical_id
                await session.commit()

                service = ExecutionVersionService(session)
                snapshot = await service.resolve_version(
                    workflow,
                    int(active_id or 0),
                )
                return snapshot.version_id, snapshot.plan["steps"][0][
                    "params"
                ]["value"]
        finally:
            await engine.dispose()

    version_id, value = asyncio.run(scenario())
    assert version_id > 0
    assert value == 1000
