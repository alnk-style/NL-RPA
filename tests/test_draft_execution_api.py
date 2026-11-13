from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.api import workflows as workflows_api
from app.api.dependencies import get_current_user
from app.database import Base, get_db
from app.engine.runner import ExecutionResult
from app.models.execution import ExecutionLog
from app.models.user import User
from app.models.workflow import Workflow
from app.models.workflow_version import WorkflowVersion
from app.services.workflow_authoring import WorkflowAuthoringService


def _plan(value: int = 1000, steps: int = 1) -> dict:
    return {
        "schema_version": 1,
        "execution_mode": "structured",
        "executor_type": "transform",
        "variables": [{"name": "rows", "default": []}],
        "steps": [
            {
                "step_id": index + 1,
                "executor": "transform",
                "action": "limit",
                "params": {
                    "values": "${rows}",
                    "count": value,
                    "result_var": f"result_{index + 1}",
                },
                "depends_on": [] if index == 0 else [index],
            }
            for index in range(steps)
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
    async with factory() as session:
        user = User(
            id=1,
            username="draft-runner",
            email="runner@example.com",
            hashed_password="hash",
        )
        workflow = Workflow(
            user_id=1,
            name="Draft runner",
            natural_language_input="goal",
            structured_plan=_plan(steps=2),
            draft_revision=3,
        )
        session.add_all([user, workflow])
        await session.commit()
        await session.refresh(workflow)
    return factory, engine, user, workflow


class _Runner:
    def __init__(self) -> None:
        self.plans: list[dict] = []
        self.modes: list[str] = []

    async def run(self, plan: dict, *, mode: str, context=None):
        self.plans.append(plan)
        self.modes.append(mode)
        return ExecutionResult(
            success=True,
            output="draft ok",
            error=None,
            screenshots=[],
            step_logs=[{"status": "success"}],
            duration_seconds=0.01,
            details={"extracted": {"result": [1, 2]}},
            artifacts=[],
        )


def _app(session, user, runner) -> FastAPI:
    app = FastAPI()
    app.include_router(workflows_api.router)

    async def override_db():
        yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[workflows_api.get_workspace_context] = (
        lambda: SimpleNamespace(
            isolation_enabled=False,
            workspace_id=None,
        )
    )
    app.dependency_overrides[
        workflows_api.get_workflow_authoring_service
    ] = lambda: WorkflowAuthoringService()
    app.dependency_overrides[
        workflows_api.get_workflow_draft_runner
    ] = lambda: runner
    return app


async def _request(app, method, path, payload=None):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        return await client.request(method, path, json=payload)


def test_dry_run_and_test_step_do_not_create_formal_records() -> None:
    async def scenario():
        factory, engine, user, workflow = await _database()
        try:
            async with factory() as session:
                runner = _Runner()
                app = _app(session, user, runner)
                dry_run = await _request(
                    app,
                    "POST",
                    f"/api/workflows/{workflow.id}/dry-run",
                    {
                        "plan": _plan(steps=2),
                        "variables": {"rows": [{"id": 1}]},
                        "draft_revision": 3,
                    },
                )
                single = await _request(
                    app,
                    "POST",
                    f"/api/workflows/{workflow.id}/test-step",
                    {
                        "step_id": 2,
                        "plan": _plan(steps=2),
                        "variables": {"rows": [{"id": 1}]},
                        "draft_revision": 3,
                    },
                )
                execution_count = await session.scalar(
                    select(func.count(ExecutionLog.id))
                )
                version_count = await session.scalar(
                    select(func.count(WorkflowVersion.id))
                )
                return (
                    dry_run,
                    single,
                    runner,
                    int(execution_count or 0),
                    int(version_count or 0),
                )
        finally:
            await engine.dispose()

    dry_run, single, runner, execution_count, version_count = (
        asyncio.run(scenario())
    )

    assert dry_run.status_code == 200
    assert dry_run.json()["requirements"]["mode"] == "dry_run"
    assert dry_run.json()["formal_execution"] is False
    assert single.status_code == 200
    assert single.json()["requirements"]["mode"] == "test"
    assert len(runner.plans[1]["steps"]) == 1
    assert runner.plans[1]["steps"][0]["step_id"] == 2
    assert execution_count == 0
    assert version_count == 0


def test_invalid_draft_execution_is_rejected_before_runner() -> None:
    async def scenario():
        factory, engine, user, workflow = await _database()
        try:
            async with factory() as session:
                runner = _Runner()
                app = _app(session, user, runner)
                response = await _request(
                    app,
                    "POST",
                    f"/api/workflows/{workflow.id}/dry-run",
                    {
                        "plan": {
                            "execution_mode": "structured",
                            "steps": [
                                {
                                    "step_id": 1,
                                    "executor": "web",
                                    "action": "missing",
                                    "params": {},
                                }
                            ],
                        }
                    },
                )
                return response, runner
        finally:
            await engine.dispose()

    response, runner = asyncio.run(scenario())
    assert response.status_code == 422
    assert runner.plans == []


def test_save_revision_conflict_does_not_overwrite_draft() -> None:
    async def scenario():
        factory, engine, user, workflow = await _database()
        try:
            async with factory() as session:
                app = _app(session, user, _Runner())
                response = await _request(
                    app,
                    "PUT",
                    f"/api/workflows/{workflow.id}",
                    {
                        "structured_plan": _plan(9999),
                        "draft_revision": 2,
                    },
                )
                stored = await session.get(Workflow, workflow.id)
                return response, stored
        finally:
            await engine.dispose()

    response, stored = asyncio.run(scenario())
    assert response.status_code == 409
    assert stored is not None
    assert stored.draft_revision == 3
    assert stored.structured_plan["steps"][0]["params"]["count"] == 1000
