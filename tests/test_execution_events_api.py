from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.api.dependencies import get_current_user
from app.api.executions import router as executions_router
from app.database import Base, get_db
from app.config import settings
from app.models.execution import ExecutionLog
from app.models.execution_event import ExecutionEvent
from app.models.human_task import HumanTask
from app.models.user import User
from app.models.workflow import Workflow


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


async def _seed(factory: async_sessionmaker[AsyncSession]) -> int:
    async with factory() as session:
        session.add(
            User(
                id=1,
                username="events-owner",
                email="events-owner@example.com",
                hashed_password="hash",
            )
        )
        workflow = Workflow(
            user_id=1,
            name="Waiting workflow",
            natural_language_input="wait",
        )
        session.add(workflow)
        await session.flush()
        execution = ExecutionLog(
            workflow_id=workflow.id,
            status="awaiting_user",
        )
        session.add(execution)
        await session.flush()
        task = HumanTask(
            execution_id=execution.id,
            step_id=2,
            kind="login_required",
            instruction="login",
            payload={},
            status="pending",
        )
        session.add(task)
        await session.flush()
        execution.human_task_id = task.id
        session.add_all(
            [
                ExecutionEvent(
                    execution_id=execution.id,
                    type="execution.awaiting_user",
                    payload={"human_task_id": task.id},
                ),
                ExecutionEvent(
                    execution_id=execution.id,
                    type="human_task.created",
                    payload={"human_task_id": task.id},
                ),
            ]
        )
        await session.commit()
        return int(execution.id)


def _app(factory: async_sessionmaker[AsyncSession]) -> FastAPI:
    app = FastAPI()
    app.include_router(executions_router)

    async def override_db():
        async with factory() as session:
            yield session

    async def override_user():
        async with factory() as session:
            user = await session.get(User, 1)
            assert user is not None
            return user

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    return app


async def _get(app: FastAPI, path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        return await client.get(path)


def test_execution_events_endpoint_reads_persisted_after_cursor() -> None:
    async def scenario():
        factory, engine = await _database()
        try:
            execution_id = await _seed(factory)
            app = _app(factory)
            first = await _get(
                app,
                f"/api/executions/{execution_id}/events",
            )
            after_first = await _get(
                app,
                (
                    f"/api/executions/{execution_id}/events"
                    "?after_id=1"
                ),
            )
            return first, after_first
        finally:
            await engine.dispose()

    first, after_first = asyncio.run(scenario())

    assert first.status_code == 200
    assert [
        item["type"] for item in first.json()["items"]
    ] == ["execution.awaiting_user", "human_task.created"]
    assert [
        item["type"] for item in after_first.json()["items"]
    ] == ["human_task.created"]


def test_live_websocket_emits_persisted_semantic_events() -> None:
    async def setup():
        factory, engine = await _database()
        execution_id = await _seed(factory)
        return factory, engine, execution_id

    factory, engine, execution_id = asyncio.run(setup())
    app = _app(factory)
    try:
        with TestClient(app) as client:
            with client.websocket_connect(
                (
                    f"/api/executions/{execution_id}/live"
                    f"?token={_token()}"
                )
            ) as websocket:
                first = websocket.receive_json()
                second = websocket.receive_json()
        assert [first["type"], second["type"]] == [
            "execution.awaiting_user",
            "human_task.created",
        ]
    finally:
        asyncio.run(engine.dispose())


def _token() -> str:
    return jwt.encode(
        {"sub": "1"},
        settings.CREDENTIAL_ENCRYPTION_KEY,
        algorithm="HS256",
    )
