from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.api.dependencies import get_current_user
from app.api.executions import router as executions_router
from app.database import Base, get_db
from app.engine.execution_manager import ExecutionManager
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


def test_execution_cancel_endpoint_persists_terminal_state_and_event() -> None:
    async def scenario():
        factory, engine = await _database()
        manager = None
        try:
            async with factory() as session:
                session.add(
                    User(
                        id=1,
                        username="cancel-owner",
                        email="cancel-owner@example.com",
                        hashed_password="hash",
                    )
                )
                workflow = Workflow(
                    user_id=1,
                    name="Cancel workflow",
                    natural_language_input="cancel",
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
                await session.commit()
                execution_id = execution.id
                task_id = task.id

            manager = ExecutionManager(
                SimpleNamespace(),
                session_factory=factory,
            )
            app = FastAPI()
            app.include_router(executions_router)
            app.state.execution_manager = manager

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
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://test",
            ) as client:
                response = await client.post(
                    f"/api/executions/{execution_id}/cancel",
                    json={"reason": "用户停止"},
                )
            async with factory() as session:
                stored = await session.get(ExecutionLog, execution_id)
                stored_task = await session.get(HumanTask, task_id)
                events = list(
                    (
                        await session.execute(
                            select(ExecutionEvent.type)
                        )
                    ).scalars()
                )
                assert stored is not None
                assert stored_task is not None
                return response, stored, stored_task, events
        finally:
            if manager is not None:
                await manager.shutdown()
            await engine.dispose()

    response, execution, task, events = asyncio.run(scenario())

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert execution.status == "cancelled"
    assert task.status == "cancelled"
    assert "execution.cancelled" in events
    assert "human_task.cancelled" in events
