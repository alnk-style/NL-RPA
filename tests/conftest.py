from __future__ import annotations

# ruff: noqa: E402

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from jose import jwt
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
os.environ.setdefault("DEV_AUTH_BYPASS", "false")

from app.api import chat_router, executions_router, workflows_router
from app.api import chat as chat_api
from app.api.dependencies import get_current_user
from app.config import settings
from app.database import Base, get_db
from app.engine.runner import ExecutionResult
from app.models.user import User


DETAILED_PLAN = {
    "trigger": {"type": "cron", "cron": "0 9 * * *"},
    "steps": [
        {
            "step_id": 1,
            "action": "navigate",
            "target": "https://example.com",
            "selector": None,
            "params": {"url": "https://example.com"},
        },
        {
            "step_id": 2,
            "action": "screenshot",
            "target": "example-home",
            "selector": None,
            "params": {},
        },
    ],
    "variables": [],
}

GENERATED_CODE = """from playwright.async_api import Error as PlaywrightError


async def main(page):
    try:
        await page.goto("https://example.com")
        await screenshot("step_1_example_home")
    except PlaywrightError as exc:
        raise RuntimeError("Workflow failed") from exc
"""

STRUCTURED_PLAN = {
    "executor_type": "web",
    "execution_mode": "structured",
    "trigger": {"type": "manual", "cron": None},
    "variables": [],
    "steps": [
        {
            "step_id": 1,
            "executor": "web",
            "action": "goto",
            "params": {"url": "https://example.com"},
            "depends_on": [],
        }
    ],
}


@pytest_asyncio.fixture
async def db_session() -> AsyncSession:
    database_engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
    )
    async with database_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(
        database_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    session = session_factory()
    session.info["session_factory"] = session_factory
    try:
        yield session
    finally:
        await session.close()
        await database_engine.dispose()


@pytest.fixture
def mock_engine() -> SimpleNamespace:
    return SimpleNamespace(
        execute_with_debug=AsyncMock(
            return_value=ExecutionResult(
                success=True,
                output="integration execution complete",
                error=None,
                screenshots=["logs/integration/step_1.png"],
                step_logs=[
                    {
                        "step_index": 0,
                        "action": "navigate",
                        "status": "success",
                    }
                ],
                duration_seconds=0.25,
            )
        )
    )


@pytest.fixture
def mock_notifier() -> SimpleNamespace:
    return SimpleNamespace(notify=AsyncMock())


@pytest_asyncio.fixture
async def async_client(
    db_session: AsyncSession,
    mock_engine: SimpleNamespace,
    mock_notifier: SimpleNamespace,
) -> TestClient:
    user = User(
        id=1,
        username="integration-user",
        email="integration@example.com",
        hashed_password="hash",
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    scheduler = SimpleNamespace(
        schedule_workflow=AsyncMock(),
        unschedule_workflow=AsyncMock(),
    )
    app = FastAPI()
    app.include_router(chat_router)
    app.include_router(
        workflows_router,
        dependencies=[Depends(get_current_user)],
    )
    app.include_router(
        executions_router,
        dependencies=[Depends(get_current_user)],
    )
    app.state.engine = mock_engine
    app.state.notifier = mock_notifier
    app.state.scheduler = scheduler
    app.state.session_factory = db_session.info["session_factory"]
    app.state.test_user = user

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[chat_api.get_intent_parser_agent] = (
        lambda: SimpleNamespace(
            parse=AsyncMock(return_value=dict(DETAILED_PLAN))
        )
    )
    app.dependency_overrides[chat_api.get_step_planner_agent] = (
        lambda: SimpleNamespace(
            plan=AsyncMock(return_value=dict(DETAILED_PLAN))
        )
    )
    app.dependency_overrides[chat_api.get_code_generator_agent] = (
        lambda: SimpleNamespace(generate=AsyncMock(return_value=GENERATED_CODE))
    )
    app.dependency_overrides[chat_api.get_web_planner_agent] = (
        lambda: SimpleNamespace(
            plan=AsyncMock(return_value=dict(STRUCTURED_PLAN))
        )
    )

    token = jwt.encode(
        {"sub": str(user.id)},
        settings.CREDENTIAL_ENCRYPTION_KEY,
        algorithm="HS256",
    )
    client = TestClient(
        app,
        headers={"Authorization": f"Bearer {token}"},
    )
    chat_api.session_contexts.clear()
    try:
        yield client
    finally:
        client.close()
        chat_api.session_contexts.clear()
