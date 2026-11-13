from __future__ import annotations

import asyncio
from copy import deepcopy
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
from app.models.user import User
from app.models.workflow import Workflow
from app.models.workflow_version import WorkflowVersion
from app.protocols import ObservationKind, ObservationSnapshot


def _base_plan() -> dict:
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
                    "value": 1000,
                    "result_var": "large_rows",
                },
                "depends_on": [],
            }
        ],
    }


def _updated_plan() -> dict:
    plan = _base_plan()
    plan["steps"][0]["params"]["value"] = 2000
    return plan


def _observation() -> ObservationSnapshot:
    return ObservationSnapshot(
        kind=ObservationKind.BROWSER,
        source="playwright",
        url="https://example.com",
        title="Example",
        elements=[
            {
                "name": "Export",
                "best_locator": {
                    "strategy": "role",
                    "value": "button",
                    "name": "Export",
                },
                "locators": [
                    {
                        "strategy": "role",
                        "value": "button",
                        "name": "Export",
                    }
                ],
            }
        ],
    )


def test_draft_modification_returns_diff_and_preserves_base_plan() -> None:
    from app.services.workflow_authoring import WorkflowAuthoringService

    base = _base_plan()
    original = deepcopy(base)
    service = WorkflowAuthoringService(
        step_planner=SimpleNamespace(
            plan=AsyncMock(return_value=_updated_plan())
        ),
    )

    result = asyncio.run(
        service.draft_from_goal(
            goal="Change amount to 2000",
            base_plan=base,
            selected_step_id=1,
        )
    )

    assert result.valid is True
    assert result.plan["steps"][0]["params"]["value"] == 2000
    assert base == original
    assert any(
        item.path.endswith("value")
        and item.before == 1000
        and item.after == 2000
        for item in result.changes
    )


def test_invalid_generated_plan_preserves_base_and_never_returns_code() -> None:
    from app.services.workflow_authoring import WorkflowAuthoringService

    base = _base_plan()
    invalid = _base_plan()
    invalid["steps"][0]["action"] = "code_step"
    invalid["steps"][0]["params"] = {"script": "print('unsafe')"}
    invalid["generated_code"] = "print('unsafe')"
    service = WorkflowAuthoringService(
        step_planner=SimpleNamespace(
            plan=AsyncMock(return_value=invalid)
        ),
    )

    result = asyncio.run(
        service.draft_from_goal(
            goal="Change the workflow",
            base_plan=base,
            selected_step_id=1,
        )
    )

    assert result.valid is False
    assert result.plan == base
    assert result.errors
    assert "generated_code" not in result.plan


def test_observation_rejects_locator_not_present_in_observed_candidates() -> None:
    from app.services.workflow_authoring import WorkflowAuthoringService

    planned = {
        "executor_type": "web",
        "execution_mode": "structured",
        "variables": [],
        "steps": [
            {
                "step_id": 1,
                "executor": "web",
                "action": "click",
                "locator": {
                    "strategy": "role",
                    "value": "button",
                    "name": "Invented",
                },
                "params": {},
                "depends_on": [],
            }
        ],
    }
    observer = SimpleNamespace(
        observe=AsyncMock(return_value=_observation())
    )
    service = WorkflowAuthoringService(
        observer=observer,
        intent_parser=SimpleNamespace(
            parse=AsyncMock(
                return_value={
                    "executor_type": "web",
                    "trigger": {"type": "manual", "cron": None},
                    "steps": [],
                    "variables": [],
                }
            )
        ),
        step_planner=SimpleNamespace(
            plan=AsyncMock(return_value=deepcopy(planned))
        ),
        web_planner=SimpleNamespace(
            plan=AsyncMock(return_value=planned)
        ),
    )

    result = asyncio.run(
        service.draft_from_goal(
            goal="Click export",
            start_url="https://example.com",
        )
    )

    assert result.valid is False
    assert result.plan["steps"] == []
    assert any("locator" in error.lower() for error in result.errors)


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
            username="author",
            email="author@example.com",
            hashed_password="hash",
        )
        workflow = Workflow(
            user_id=1,
            name="Authoring",
            natural_language_input="goal",
            structured_plan=_base_plan(),
        )
        session.add_all([user, workflow])
        await session.commit()
        await session.refresh(workflow)
    return factory, engine, user, workflow


def _app(
    session: AsyncSession,
    user: User,
    service,
) -> FastAPI:
    app = FastAPI()
    app.include_router(workflows_api.router)

    async def override_db():
        yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[
        workflows_api.get_workflow_authoring_service
    ] = lambda: service
    app.dependency_overrides[
        workflows_api.get_workspace_context
    ] = lambda: SimpleNamespace(
        isolation_enabled=False,
        workspace_id=None,
    )
    return app


async def _request(
    app: FastAPI,
    method: str,
    path: str,
    payload: dict | None = None,
) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        return await client.request(method, path, json=payload)


def test_draft_and_validate_api_do_not_create_versions() -> None:
    from app.services.workflow_authoring import WorkflowAuthoringService

    async def scenario():
        factory, engine, user, workflow = await _database()
        try:
            async with factory() as session:
                service = WorkflowAuthoringService(
                    intent_parser=SimpleNamespace(
                        parse=AsyncMock(
                            return_value={
                                "executor_type": "transform",
                                "trigger": {
                                    "type": "manual",
                                    "cron": None,
                                },
                                "steps": [],
                                "variables": [],
                            }
                        )
                    ),
                    step_planner=SimpleNamespace(
                        plan=AsyncMock(return_value=_updated_plan())
                    ),
                )
                app = _app(session, user, service)
                drafted = await _request(
                    app,
                    "POST",
                    "/api/workflows/draft-from-goal",
                    {
                        "goal": "filter rows",
                        "workflow_id": workflow.id,
                        "base_plan": _base_plan(),
                    },
                )
                validated = await _request(
                    app,
                    "POST",
                    f"/api/workflows/{workflow.id}/validate",
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
                version_count = await session.scalar(
                    select(func.count(WorkflowVersion.id))
                )
                return drafted, validated, int(version_count or 0)
        finally:
            await engine.dispose()

    drafted, validated, version_count = asyncio.run(scenario())

    assert drafted.status_code == 200
    assert drafted.json()["valid"] is True
    assert drafted.json()["requirements"]["mode"] == "test"
    assert validated.status_code == 200
    assert validated.json()["valid"] is False
    assert version_count == 0
