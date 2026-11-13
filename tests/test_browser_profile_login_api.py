from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.api.connections import router as connections_router
from app.api.dependencies import get_current_user
from app.database import Base, get_db
from app.models.browser_profile import BrowserProfile
from app.models.execution import ExecutionLog
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


async def _seed(factory: async_sessionmaker[AsyncSession]) -> tuple[int, int]:
    async with factory() as session:
        session.add_all(
            [
                User(
                    id=1,
                    username="profile-owner",
                    email="profile-owner@example.com",
                    hashed_password="hash",
                ),
                User(
                    id=2,
                    username="other-profile-owner",
                    email="other-profile-owner@example.com",
                    hashed_password="hash",
                ),
            ]
        )
        workflow = Workflow(
            user_id=1,
            name="Login workflow",
            natural_language_input="login",
        )
        other_workflow = Workflow(
            user_id=2,
            name="Other workflow",
            natural_language_input="login",
        )
        session.add_all([workflow, other_workflow])
        await session.flush()
        profile = BrowserProfile(
            user_id=1,
            name="Visible profile",
            user_data_dir="C:/rpa-profile",
            profile_directory="Default",
            login_state="expired",
        )
        other_profile = BrowserProfile(
            user_id=2,
            name="Other profile",
            user_data_dir="C:/other-profile",
            profile_directory="Default",
            login_state="expired",
        )
        execution = ExecutionLog(
            workflow_id=workflow.id,
            status="awaiting_user",
        )
        other_execution = ExecutionLog(
            workflow_id=other_workflow.id,
            status="awaiting_user",
        )
        session.add_all([profile, other_profile, execution, other_execution])
        await session.flush()
        task = HumanTask(
            execution_id=execution.id,
            step_id=2,
            kind="login_required",
            instruction="login",
            payload={
                "current_url": "https://example.com/login",
                "browser_profile_id": profile.id,
            },
            status="pending",
        )
        other_task = HumanTask(
            execution_id=other_execution.id,
            step_id=2,
            kind="login_required",
            instruction="login",
            payload={
                "current_url": "https://example.com/login",
                "browser_profile_id": other_profile.id,
            },
            status="pending",
        )
        session.add_all([task, other_task])
        await session.flush()
        execution.human_task_id = task.id
        other_execution.human_task_id = other_task.id
        await session.commit()
        return int(profile.id), int(task.id)


class FakeBrowserSessions:
    def __init__(self) -> None:
        self.calls = []

    async def open_login(self, task, profile, *, url=None):
        self.calls.append((task.id, profile.id, url))
        return {
            "human_task_id": task.id,
            "browser_profile_id": profile.id,
            "profile_name": profile.name,
            "status": "open",
            "current_url": url or task.payload["current_url"],
        }


async def _request(
    app: FastAPI,
    path: str,
    *,
    json: dict | None = None,
) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        return await client.post(path, json=json)


def test_browser_profile_login_requires_owned_profile_and_task() -> None:
    async def scenario():
        factory, engine = await _database()
        try:
            profile_id, task_id = await _seed(factory)
            sessions = FakeBrowserSessions()
            app = FastAPI()
            app.include_router(connections_router)
            app.state.browser_login_sessions = sessions

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

            response = await _request(
                app,
                f"/api/browser-profiles/{profile_id}/login",
                json={
                    "human_task_id": task_id,
                    "url": "https://example.com/login",
                },
            )
            forbidden = await _request(
                app,
                "/api/browser-profiles/999/login",
                json={"human_task_id": task_id},
            )
            return response, forbidden, sessions
        finally:
            await engine.dispose()

    response, forbidden, sessions = asyncio.run(scenario())

    assert response.status_code == 200
    assert response.json()["status"] == "open"
    assert forbidden.status_code == 404
    assert len(sessions.calls) == 1
