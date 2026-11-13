from __future__ import annotations

# ruff: noqa: E402
import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("DEEPSEEK_API_KEY", "test-key")
os.environ.setdefault("CREDENTIAL_ENCRYPTION_KEY", "test-encryption-key")
os.environ.setdefault("JWT_SECRET_KEY", "test-encryption-key")

from app.api import dependencies as auth_api
from app.api import workflows_router
from app.api.dependencies import get_current_user
from app.config import get_settings
from app.database import Base, get_db
from app.engine.mcp_registry import MCPRegistry
from app.main import app as main_app
from app.models.execution import ExecutionLog
from app.models.user import User
from app.models.workflow import Workflow


async def _database() -> tuple[AsyncSession, object]:
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
    return session_factory(), engine


async def _seed_user(
    session: AsyncSession,
    user_id: int = 1,
    username: str = "tester",
) -> User:
    user = User(
        id=user_id,
        username=username,
        email=f"{username}@example.com",
        hashed_password="hash",
    )
    session.add(user)
    await session.commit()
    return user


async def _seed_workflow(
    session: AsyncSession,
    user_id: int = 1,
    *,
    name: str = "Example workflow",
    status: str = "draft",
    generated_code: str | None = "await page.goto('https://example.com')",
) -> Workflow:
    workflow = Workflow(
        user_id=user_id,
        name=name,
        description="Test workflow",
        natural_language_input="Open example.com",
        structured_plan={
            "trigger": {"type": "manual", "cron": None},
            "steps": [{"step_id": 1, "action": "navigate"}],
            "variables": [],
        },
        generated_code=generated_code,
        dag_definition={"nodes": [{"id": "start"}]},
        trigger_type="manual",
        status=status,
        notification_config={"on_failure": True},
    )
    session.add(workflow)
    await session.commit()
    await session.refresh(workflow)
    return workflow


async def _request(
    app: FastAPI,
    method: str,
    path: str,
    **kwargs,
) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        return await client.request(method, path, **kwargs)


def _workflow_app(
    session: AsyncSession,
    user: User,
    scheduler: object | None = None,
    registry: object | None = None,
) -> FastAPI:
    app = FastAPI()
    app.include_router(workflows_router)
    if scheduler is not None:
        app.state.scheduler = scheduler
    if registry is not None:
        app.state.mcp_registry = registry

    async def override_db():
        yield session

    async def override_user() -> User:
        return user

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    return app


def test_workflow_create() -> None:
    async def scenario() -> tuple[dict, Workflow]:
        session, engine = await _database()
        try:
            user = await _seed_user(session)
            response = await _request(
                _workflow_app(session, user),
                "POST",
                "/api/workflows",
                json={
                    "name": "New workflow",
                    "description": "Created through the API",
                    "natural_language_input": "Open example.com",
                },
            )
            payload = response.json()
            workflow = await session.get(Workflow, payload["id"])
            return payload, workflow
        finally:
            await session.close()
            await engine.dispose()

    payload, workflow = asyncio.run(scenario())

    assert payload["name"] == "New workflow"
    assert payload["user_id"] == 1
    assert payload["status"] == "draft"
    assert payload["generated_code"] is None
    assert workflow is not None
    assert workflow.user_id == 1


def test_workflow_requires_auth() -> None:
    async def scenario() -> int:
        session, engine = await _database()
        try:
            app = FastAPI()
            app.include_router(workflows_router)

            async def override_db():
                yield session

            app.dependency_overrides[get_db] = override_db
            response = await _request(app, "GET", "/api/workflows")
            return response.status_code
        finally:
            await session.close()
            await engine.dispose()

    assert asyncio.run(scenario()) == 401


def test_workflow_dev_auth_bypass(monkeypatch) -> None:
    async def scenario() -> tuple[int, list[dict], list[User]]:
        session, engine = await _database()
        try:
            monkeypatch.setattr(auth_api.settings, "DEV_AUTH_BYPASS", True)

            app = FastAPI()
            app.include_router(workflows_router)

            async def override_db():
                yield session

            app.dependency_overrides[get_db] = override_db
            response = await _request(app, "GET", "/api/workflows")
            users = (
                await session.execute(select(User))
            ).scalars().all()
            return response.status_code, response.json(), list(users)
        finally:
            await session.close()
            await engine.dispose()

    status_code, workflows, users = asyncio.run(scenario())

    assert status_code == 200
    assert workflows == []
    assert len(users) == 1
    assert users[0].username == "local-dev"


def test_workflow_list() -> None:
    async def scenario() -> list[dict]:
        session, engine = await _database()
        try:
            user = await _seed_user(session)
            await _seed_user(session, user_id=2, username="other")
            await _seed_workflow(
                session,
                user_id=user.id,
                name="Owned draft",
                status="draft",
            )
            await _seed_workflow(
                session,
                user_id=user.id,
                name="Owned published",
                status="published",
            )
            await _seed_workflow(
                session,
                user_id=2,
                name="Other published",
                status="published",
            )

            response = await _request(
                _workflow_app(session, user),
                "GET",
                "/api/workflows?status=published",
            )
            assert response.status_code == 200
            return response.json()
        finally:
            await session.close()
            await engine.dispose()

    workflows = asyncio.run(scenario())

    assert [workflow["name"] for workflow in workflows] == [
        "Owned published"
    ]


def test_workflow_update() -> None:
    async def scenario() -> tuple[dict, Workflow]:
        session, engine = await _database()
        try:
            user = await _seed_user(session)
            workflow = await _seed_workflow(session, user.id)
            workflow.updated_at = datetime(2020, 1, 1)
            await session.commit()

            response = await _request(
                _workflow_app(session, user),
                "PUT",
                f"/api/workflows/{workflow.id}",
                json={
                    "name": "Updated workflow",
                    "generated_code": "await page.goto('https://updated.test')",
                    "cron_expression": "0 9 * * *",
                },
            )
            await session.refresh(workflow)
            return response.json(), workflow
        finally:
            await session.close()
            await engine.dispose()

    payload, workflow = asyncio.run(scenario())

    assert payload["name"] == "Updated workflow"
    assert payload["cron_expression"] == "0 9 * * *"
    assert workflow.name == "Updated workflow"
    assert workflow.updated_at > datetime(2020, 1, 1)


def test_workflow_delete() -> None:
    async def scenario() -> tuple[dict, bool, bool]:
        session, engine = await _database()
        try:
            user = await _seed_user(session)
            workflow = await _seed_workflow(session, user.id)
            execution_log = ExecutionLog(
                workflow_id=workflow.id,
                status="success",
            )
            session.add(execution_log)
            await session.commit()
            await session.refresh(execution_log)

            response = await _request(
                _workflow_app(session, user),
                "DELETE",
                f"/api/workflows/{workflow.id}",
            )
            workflow_exists = (
                await session.execute(
                    select(Workflow).where(Workflow.id == workflow.id)
                )
            ).scalar_one_or_none() is not None
            log_exists = (
                await session.execute(
                    select(ExecutionLog).where(
                        ExecutionLog.id == execution_log.id
                    )
                )
            ).scalar_one_or_none() is not None
            return response.json(), workflow_exists, log_exists
        finally:
            await session.close()
            await engine.dispose()

    payload, workflow_exists, log_exists = asyncio.run(scenario())

    assert payload == {"status": "deleted", "workflow_id": 1}
    assert workflow_exists is False
    assert log_exists is False


def test_workflow_publish() -> None:
    async def scenario() -> tuple[dict, Workflow, AsyncMock, AsyncMock]:
        session, engine = await _database()
        try:
            user = await _seed_user(session)
            workflow = await _seed_workflow(session, user.id)
            schedule = AsyncMock()
            unschedule = AsyncMock()
            scheduler = SimpleNamespace(
                schedule_workflow=schedule,
                unschedule_workflow=unschedule,
            )

            response = await _request(
                _workflow_app(session, user, scheduler),
                "POST",
                f"/api/workflows/{workflow.id}/publish",
            )
            await session.refresh(workflow)
            return response.json(), workflow, schedule, unschedule
        finally:
            await session.close()
            await engine.dispose()

    payload, workflow, schedule, _unschedule = asyncio.run(scenario())

    assert payload["status"] == "published"
    assert payload["workflow_id"] == 1
    assert payload["version"] >= 1
    assert payload["active_version_id"] > 0
    assert workflow.status == "published"
    schedule.assert_not_awaited()


def test_workflow_publish_accepts_hybrid_plan(monkeypatch) -> None:
    async def scenario() -> tuple[dict, Workflow]:
        session, engine = await _database()
        try:
            user = await _seed_user(session)
            workflow = await _seed_workflow(session, user.id)
            workflow.executor_type = "web"
            workflow.execution_mode = "structured"
            workflow.generated_code = None
            workflow.structured_plan = {
                "executor_type": "web",
                "steps": [
                    {
                        "step_id": 1,
                        "executor": "web",
                        "action": "goto",
                        "params": {"url": "https://example.com"},
                        "depends_on": [],
                    },
                    {
                        "step_id": 2,
                        "executor": "windows",
                        "action": "launch_app",
                        "params": {"path": "notepad.exe"},
                        "depends_on": [1],
                    },
                    {
                        "step_id": 3,
                        "executor": "web",
                        "action": "click",
                        "params": {},
                        "locator": {
                            "strategy": "role",
                            "value": "button",
                            "name": "Save",
                        },
                        "depends_on": [2],
                    },
                ],
            }
            await session.commit()

            response = await _request(
                _workflow_app(session, user),
                "POST",
                f"/api/workflows/{workflow.id}/publish",
            )
            await session.refresh(workflow)
            return response.json(), workflow
        finally:
            await session.close()
            await engine.dispose()

    monkeypatch.setattr(get_settings(), "WEB_STRUCTURED_ACTIONS", True)
    payload, workflow = asyncio.run(scenario())

    assert payload["status"] == "published"
    assert payload["workflow_id"] == 1
    assert payload["version"] == 1
    assert payload["active_version_id"] > 0
    assert workflow.status == "published"


def test_workflow_publish_rejects_invalid_hybrid_step(monkeypatch) -> None:
    async def scenario() -> int:
        session, engine = await _database()
        try:
            user = await _seed_user(session)
            workflow = await _seed_workflow(session, user.id)
            workflow.executor_type = "web"
            workflow.execution_mode = "structured"
            workflow.generated_code = None
            workflow.structured_plan = {
                "executor_type": "web",
                "steps": [
                    {
                        "step_id": 1,
                        "executor": "web",
                        "action": "goto",
                        "params": {"url": "https://example.com"},
                        "depends_on": [],
                    },
                    {
                        "step_id": 2,
                        "executor": "windows",
                        "action": "not_a_desktop_action",
                        "params": {},
                        "depends_on": [1],
                    },
                ],
            }
            await session.commit()

            response = await _request(
                _workflow_app(session, user),
                "POST",
                f"/api/workflows/{workflow.id}/publish",
            )
            return response.status_code
        finally:
            await session.close()
            await engine.dispose()

    monkeypatch.setattr(get_settings(), "WEB_STRUCTURED_ACTIONS", True)
    assert asyncio.run(scenario()) == 400


def test_workflow_duplicate() -> None:
    async def scenario() -> tuple[dict, Workflow, list[Workflow]]:
        session, engine = await _database()
        try:
            user = await _seed_user(session)
            original = await _seed_workflow(session, user.id)
            response = await _request(
                _workflow_app(session, user),
                "POST",
                f"/api/workflows/{original.id}/duplicate",
                json={"new_name": "Copied workflow"},
            )
            workflows = (
                await session.execute(
                    select(Workflow).where(Workflow.user_id == user.id)
                )
            ).scalars().all()
            return response.json(), original, list(workflows)
        finally:
            await session.close()
            await engine.dispose()

    payload, original, workflows = asyncio.run(scenario())

    duplicate = next(
        workflow
        for workflow in workflows
        if workflow.name == "Copied workflow"
    )
    assert payload["id"] == duplicate.id
    assert duplicate.id != original.id
    assert duplicate.user_id == original.user_id
    assert duplicate.status == "draft"
    assert duplicate.structured_plan == original.structured_plan
    assert duplicate.generated_code == original.generated_code
    assert duplicate.dag_definition == original.dag_definition


def test_health_endpoint() -> None:
    response = asyncio.run(_request(main_app, "GET", "/health"))

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert "database" in payload


def test_workflow_publish_accepts_mcp_step(monkeypatch) -> None:
    async def scenario() -> tuple[dict, Workflow]:
        session, engine = await _database()
        try:
            user = await _seed_user(session)
            from app.models.mcp import MCPServer

            session.add(
                MCPServer(
                    user_id=user.id,
                    name="local-tools",
                    enabled=True,
                    tools=[
                        {
                            "name": "echo",
                            "risk_level": "read_only",
                            "available": True,
                        }
                    ],
                )
            )
            await session.commit()
            workflow = await _seed_workflow(session, user.id)
            workflow.executor_type = "web"
            workflow.execution_mode = "structured"
            workflow.generated_code = None
            workflow.structured_plan = {
                "executor_type": "web",
                "steps": [
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
                            "tool": "echo",
                            "arguments": {"text": "hi"},
                        },
                        "depends_on": [1],
                    },
                ],
            }
            await session.commit()

            response = await _request(
                _workflow_app(
                    session,
                    user,
                    registry=MCPRegistry(
                        session_factory=session.info.get("session_factory"),
                        config=get_settings(),
                    ),
                ),
                "POST",
                f"/api/workflows/{workflow.id}/publish",
            )
            await session.refresh(workflow)
            return response.json(), workflow
        finally:
            await session.close()
            await engine.dispose()

    monkeypatch.setattr(get_settings(), "WEB_STRUCTURED_ACTIONS", True)
    monkeypatch.setattr(get_settings(), "MCP_CLIENT", True)
    payload, workflow = asyncio.run(scenario())

    assert payload["status"] == "published"
    assert payload["workflow_id"] == 1
    assert payload["version"] == 1
    assert payload["active_version_id"] > 0
    assert workflow.status == "published"


def test_workflow_publish_rejects_mcp_step_without_tool(monkeypatch) -> None:
    async def scenario() -> tuple[int, Workflow]:
        session, engine = await _database()
        try:
            user = await _seed_user(session)
            workflow = await _seed_workflow(session, user.id)
            workflow.executor_type = "web"
            workflow.generated_code = None
            workflow.structured_plan = {
                "steps": [
                    {
                        "step_id": 1,
                        "executor": "mcp",
                        "action": "call_tool",
                        "params": {"server": "local-tools"},
                        "depends_on": [],
                    }
                ]
            }
            await session.commit()

            response = await _request(
                _workflow_app(session, user),
                "POST",
                f"/api/workflows/{workflow.id}/publish",
            )
            await session.refresh(workflow)
            return response.status_code, workflow
        finally:
            await session.close()
            await engine.dispose()

    monkeypatch.setattr(get_settings(), "MCP_CLIENT", True)
    status_code, workflow = asyncio.run(scenario())

    assert status_code == 400
    assert workflow.status == "draft"


def test_workflow_publish_rejects_mcp_tool_not_in_catalog(
    monkeypatch,
) -> None:
    async def scenario() -> int:
        session, engine = await _database()
        try:
            user = await _seed_user(session)
            workflow = await _seed_workflow(session, user.id)
            workflow.executor_type = "mcp"
            workflow.generated_code = None
            workflow.structured_plan = {
                "executor_type": "mcp",
                "needs_confirmation": True,
                "steps": [
                    {
                        "step_id": 1,
                        "executor": "mcp",
                        "action": "call_tool",
                        "params": {
                            "server": "invented",
                            "tool": "invented_tool",
                            "arguments": {},
                        },
                        "depends_on": [],
                    }
                ],
            }
            await session.commit()
            registry = MCPRegistry(
                session_factory=session.info.get("session_factory"),
                config=get_settings(),
            )
            response = await _request(
                _workflow_app(session, user, registry=registry),
                "POST",
                f"/api/workflows/{workflow.id}/publish",
            )
            return response.status_code
        finally:
            await session.close()
            await engine.dispose()

    monkeypatch.setattr(get_settings(), "MCP_CLIENT", True)

    assert asyncio.run(scenario()) == 400
