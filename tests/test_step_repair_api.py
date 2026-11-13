from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
from fastapi import FastAPI
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

from app.api import workflows as workflows_api
from app.api import workflows_router
from app.api.observations import get_browser_observer
from app.config import settings
from app.database import Base, get_db
from app.models.user import User
from app.models.workflow import Workflow
from app.protocols import ObservationKind, ObservationSnapshot

PLAN = {
    "executor_type": "web",
    "steps": [
        {
            "step_id": 1,
            "action": "goto",
            "params": {"url": "https://example.com"},
        },
        {
            "step_id": 2,
            "action": "click",
            "locator": {"strategy": "css", "value": "#old"},
        },
    ],
}

FIXED_TAIL = [
    {
        "step_id": 2,
        "action": "click",
        "locator": {
            "strategy": "role",
            "value": "button",
            "name": "登录",
        },
    }
]


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


async def _seed_workflow(session: AsyncSession) -> tuple[User, Workflow]:
    user = User(
        id=1,
        username="tester",
        email="tester@example.com",
        hashed_password="hash",
    )
    workflow = Workflow(
        user_id=1,
        name="Structured workflow",
        natural_language_input="打开示例站点并登录",
        structured_plan=dict(PLAN),
        status="draft",
    )
    session.add_all([user, workflow])
    await session.commit()
    await session.refresh(workflow)
    return user, workflow


def _app(session: AsyncSession, user: User) -> FastAPI:
    app = FastAPI()
    app.include_router(workflows_router)

    async def override_db():
        yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[workflows_api.get_current_user] = lambda: user
    return app


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


def test_validate_workflow_step_returns_normalized_step() -> None:
    async def scenario() -> tuple[int, dict]:
        session_factory, engine = await _database()
        try:
            async with session_factory() as session:
                user, workflow = await _seed_workflow(session)
                app = _app(session, user)
                response = await _request(
                    app,
                    "POST",
                    f"/api/workflows/{workflow.id}/steps/2/validate",
                )
                return response.status_code, response.json()
        finally:
            await engine.dispose()

    status_code, payload = asyncio.run(scenario())

    assert status_code == 200
    assert payload["valid"] is True
    assert payload["step"]["action"] == "click"
    assert payload["step"]["executor"] == "web"
    assert payload["locator_candidates"][0]["strategy"] == "css"


def test_validate_workflow_step_rejects_invalid_plan() -> None:
    async def scenario() -> tuple[int, str]:
        session_factory, engine = await _database()
        try:
            async with session_factory() as session:
                user, workflow = await _seed_workflow(session)
                workflow.structured_plan = {
                    "steps": [{"action": "navigate", "params": {}}]
                }
                await session.commit()
                app = _app(session, user)
                response = await _request(
                    app,
                    "POST",
                    f"/api/workflows/{workflow.id}/steps/1/validate",
                )
                return response.status_code, response.json()["detail"]
        finally:
            await engine.dispose()

    status_code, detail = asyncio.run(scenario())

    assert status_code == 400
    assert "动作不在网页动作目录" in detail


def test_repair_workflow_step_requires_flag(monkeypatch) -> None:
    monkeypatch.setattr(settings, "STEP_LEVEL_REPAIR", False)

    async def scenario() -> int:
        session_factory, engine = await _database()
        try:
            async with session_factory() as session:
                user, workflow = await _seed_workflow(session)
                app = _app(session, user)
                response = await _request(
                    app,
                    "POST",
                    f"/api/workflows/{workflow.id}/steps/2/repair",
                    json={"observation": {"elements": []}},
                )
                return response.status_code
        finally:
            await engine.dispose()

    assert asyncio.run(scenario()) == 403


def test_repair_workflow_step_persists_plan(monkeypatch) -> None:
    monkeypatch.setattr(settings, "STEP_LEVEL_REPAIR", True)

    async def scenario() -> tuple[int, dict, AsyncMock, SimpleNamespace]:
        session_factory, engine = await _database()
        try:
            async with session_factory() as session:
                user, workflow = await _seed_workflow(session)
                app = _app(session, user)
                app.dependency_overrides[
                    workflows_api.require_step_repair_enabled
                ] = lambda: None

                repair_agent = SimpleNamespace(
                    repair_steps=AsyncMock(return_value=list(FIXED_TAIL))
                )
                observer = SimpleNamespace(observe=AsyncMock())
                app.dependency_overrides[
                    workflows_api.get_debug_agent
                ] = lambda: repair_agent
                app.dependency_overrides[
                    get_browser_observer
                ] = lambda: observer

                response = await _request(
                    app,
                    "POST",
                    f"/api/workflows/{workflow.id}/steps/2/repair",
                    json={
                        "observation": {
                            "kind": "browser",
                            "url": "https://example.com",
                            "elements": [],
                        },
                        "error": "未找到元素：css=#old",
                    },
                )
                return (
                    response.status_code,
                    response.json(),
                    repair_agent.repair_steps,
                    observer,
                )
        finally:
            await engine.dispose()

    status_code, payload, repair_steps, observer = asyncio.run(scenario())

    assert status_code == 200
    assert payload["status"] == "repaired"
    assert payload["steps"][1]["locator"]["strategy"] == "role"
    call = repair_steps.await_args.kwargs
    assert call["step_index"] == 1
    assert call["failure"]["failure_category"] == "locator_not_found"
    assert call["failure"]["step_id"] == 2
    assert call["observation"]["elements"] == []
    assert "click" in call["action_catalog"]
    observer.observe.assert_not_awaited()


def test_repair_workflow_step_observes_previous_goto_url(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "STEP_LEVEL_REPAIR", True)
    monkeypatch.setattr(settings, "OBSERVE_BEFORE_PLAN", True)

    async def scenario() -> tuple[int, AsyncMock]:
        session_factory, engine = await _database()
        try:
            async with session_factory() as session:
                user, workflow = await _seed_workflow(session)
                app = _app(session, user)
                app.dependency_overrides[
                    workflows_api.require_step_repair_enabled
                ] = lambda: None

                repair_agent = SimpleNamespace(
                    repair_steps=AsyncMock(return_value=list(FIXED_TAIL))
                )
                snapshot = ObservationSnapshot(
                    kind=ObservationKind.BROWSER,
                    source="playwright",
                    url="https://example.com",
                    title="示例",
                    elements=[
                        {
                            "name": "登录",
                            "best_locator": {
                                "strategy": "role",
                                "value": "button",
                                "name": "登录",
                            },
                        }
                    ],
                )
                observer = SimpleNamespace(
                    observe=AsyncMock(return_value=snapshot)
                )
                app.dependency_overrides[
                    workflows_api.get_debug_agent
                ] = lambda: repair_agent
                app.dependency_overrides[
                    get_browser_observer
                ] = lambda: observer

                response = await _request(
                    app,
                    "POST",
                    f"/api/workflows/{workflow.id}/steps/2/repair",
                    json={"error": "waiting for locator"},
                )
                return response.status_code, observer.observe
        finally:
            await engine.dispose()

    status_code, observe = asyncio.run(scenario())

    assert status_code == 200
    observe.assert_awaited_once()
    assert observe.await_args.args[0] == "https://example.com"


def test_publish_structured_workflow_without_code(monkeypatch) -> None:
    monkeypatch.setattr(settings, "WEB_STRUCTURED_ACTIONS", True)

    async def scenario() -> int:
        session_factory, engine = await _database()
        try:
            async with session_factory() as session:
                user, workflow = await _seed_workflow(session)
                app = _app(session, user)
                response = await _request(
                    app,
                    "POST",
                    f"/api/workflows/{workflow.id}/publish",
                )
                return response.status_code
        finally:
            await engine.dispose()

    assert asyncio.run(scenario()) == 200


def test_publish_structured_workflow_does_not_require_legacy_chat_switch(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "WEB_STRUCTURED_ACTIONS", False)

    async def scenario() -> int:
        session_factory, engine = await _database()
        try:
            async with session_factory() as session:
                user, workflow = await _seed_workflow(session)
                app = _app(session, user)
                response = await _request(
                    app,
                    "POST",
                    f"/api/workflows/{workflow.id}/publish",
                )
                return response.status_code
        finally:
            await engine.dispose()

    assert asyncio.run(scenario()) == 200
