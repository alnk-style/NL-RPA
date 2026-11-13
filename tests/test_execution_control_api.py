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

from app.api.dependencies import get_current_user
from app.api.executions import router as executions_router
from app.config import settings
from app.database import Base, get_db
from app.engine.execution_manager import ExecutionManager
from app.engine.runner import ExecutionResult
from app.models.execution import ExecutionLog
from app.models.user import User
from app.models.workflow import Workflow
from app.models.workflow_version import WorkflowVersion


async def _activate_workflow(
    session: AsyncSession,
    workflow: Workflow,
) -> WorkflowVersion:
    plan = {
        "schema_version": 1,
        "execution_mode": "structured",
        "executor_type": "web",
        "variables": [],
        "steps": [
            {
                "step_id": 1,
                "executor": "web",
                "action": "goto",
                "params": {"url": "https://example.com"},
            },
            {
                "step_id": 2,
                "executor": "web",
                "action": "assert_logged_in",
                "params": {},
                "depends_on": [1],
            },
        ],
    }
    workflow.structured_plan = plan
    workflow.execution_mode = "structured"
    version = WorkflowVersion(
        workflow_id=workflow.id,
        version=1,
        plan=plan,
        validation={"valid": True},
        action_catalog_version="2026-10-28.1",
    )
    session.add(version)
    await session.flush()
    workflow.active_version_id = version.id
    workflow.status = "published"
    return version


class PausableEngine:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.calls = 0

    async def execute_with_debug(
        self,
        _workflow,
        _context=None,
    ) -> ExecutionResult:
        self.calls += 1
        if self.calls == 1:
            self.started.set()
            await asyncio.Event().wait()
        return ExecutionResult(
            success=True,
            output="finished",
            error=None,
            screenshots=[],
            step_logs=[],
            duration_seconds=0.1,
        )


async def _request(
    app: FastAPI,
    method: str,
    path: str,
) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        return await client.request(method, path)


class ResumeEngine:
    def __init__(self) -> None:
        self.contexts: list[dict] = []

    async def execute_with_debug(
        self,
        _workflow,
        context: dict | None = None,
    ) -> ExecutionResult:
        self.contexts.append(dict(context or {}))
        return ExecutionResult(
            success=True,
            output="resumed",
            error=None,
            screenshots=[],
            step_logs=[
                {
                    "step_index": 0,
                    "action": "goto",
                    "status": "success",
                },
                {
                    "step_index": 1,
                    "action": "assert_logged_in",
                    "status": "success",
                },
            ],
            duration_seconds=0.1,
        )


def test_execution_pause_and_resume_api(tmp_path) -> None:
    async def scenario() -> None:
        database_path = tmp_path / "execution-control.db"
        database_engine = create_async_engine(
            f"sqlite+aiosqlite:///{database_path.as_posix()}"
        )
        async with database_engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        session_factory = async_sessionmaker(
            database_engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        async with session_factory() as session:
            user = User(
                id=1,
                username="tester",
                email="tester@example.com",
                hashed_password="hash",
            )
            workflow = Workflow(
                user_id=1,
                name="Test workflow",
                natural_language_input="Test",
                status="draft",
            )
            session.add_all([user, workflow])
            await session.flush()
            await _activate_workflow(session, workflow)
            await session.commit()
            await session.refresh(workflow)
            workflow_id = workflow.id

        fake_engine = PausableEngine()
        manager = ExecutionManager(
            fake_engine,
            session_factory=session_factory,
        )
        app = FastAPI()
        app.include_router(executions_router)
        app.state.engine = fake_engine
        app.state.execution_manager = manager

        async def override_db():
            async with session_factory() as session:
                yield session

        async def override_user() -> User:
            async with session_factory() as session:
                current_user = await session.get(User, 1)
                assert current_user is not None
                return current_user

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = override_user

        try:
            run_response = await _request(
                app,
                "POST",
                f"/api/executions/{workflow_id}/run",
            )
            assert run_response.status_code == 200
            run_payload = run_response.json()
            assert run_payload["status"] == "running"
            execution_id = run_payload["id"]

            await asyncio.wait_for(fake_engine.started.wait(), timeout=1)
            pause_response = await _request(
                app,
                "POST",
                f"/api/executions/{execution_id}/pause",
            )
            assert pause_response.status_code == 200
            assert pause_response.json()["status"] == "paused"

            resume_response = await _request(
                app,
                "POST",
                f"/api/executions/{execution_id}/resume",
            )
            assert resume_response.status_code == 200
            assert resume_response.json()["status"] == "running"

            for _ in range(100):
                detail_response = await _request(
                    app,
                    "GET",
                    f"/api/executions/{execution_id}/detail",
                )
                if detail_response.json()["status"] == "success":
                    break
                await asyncio.sleep(0.01)

            assert detail_response.json()["status"] == "success"
            assert fake_engine.calls == 2
        finally:
            await manager.shutdown()
            await database_engine.dispose()

    asyncio.run(scenario())


def test_resume_after_login_api(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "AUTH_REQUIRED_PAUSE", True)

    async def scenario() -> tuple[
        httpx.Response,
        httpx.Response,
        list[dict],
    ]:
        database_path = tmp_path / "resume-after-login.db"
        database_engine = create_async_engine(
            f"sqlite+aiosqlite:///{database_path.as_posix()}"
        )
        async with database_engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        session_factory = async_sessionmaker(
            database_engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        async with session_factory() as session:
            user = User(
                id=1,
                username="tester",
                email="tester@example.com",
                hashed_password="hash",
            )
            workflow = Workflow(
                user_id=1,
                name="Login workflow",
                natural_language_input="登录后抓取",
                status="draft",
            )
            session.add_all([user, workflow])
            await session.flush()
            await _activate_workflow(session, workflow)
            execution = ExecutionLog(
                workflow_id=workflow.id,
                status="awaiting_user",
                error_message="AUTH_REQUIRED: 请登录",
                step_logs=[
                    {
                        "step_index": 0,
                        "action": "goto",
                        "status": "success",
                    },
                    {
                        "type": "checkpoint",
                        "action": "checkpoint",
                        "status": "awaiting_user",
                        "next_step_index": 1,
                        "completed_idempotency_keys": [],
                    },
                ],
            )
            session.add(execution)
            await session.commit()
            await session.refresh(execution)
            execution_id = execution.id

        fake_engine = ResumeEngine()
        manager = ExecutionManager(
            fake_engine,
            session_factory=session_factory,
            config=SimpleNamespace(AUTH_REQUIRED_PAUSE=True),
        )
        app = FastAPI()
        app.include_router(executions_router)
        app.state.execution_manager = manager

        async def override_db():
            async with session_factory() as session:
                yield session

        async def override_user() -> User:
            async with session_factory() as session:
                current_user = await session.get(User, 1)
                assert current_user is not None
                return current_user

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = override_user

        try:
            response = await _request(
                app,
                "POST",
                f"/api/executions/{execution_id}/resume-after-login",
            )
            assert response.status_code == 200
            assert response.json()["status"] == "running"

            for _ in range(100):
                detail = await _request(
                    app,
                    "GET",
                    f"/api/executions/{execution_id}/detail",
                )
                if detail.json()["status"] == "success":
                    break
                await asyncio.sleep(0.01)

            return response, detail, fake_engine.contexts
        finally:
            await manager.shutdown()
            await database_engine.dispose()

    response, detail, contexts = asyncio.run(scenario())

    assert response.status_code == 200
    assert detail.json()["status"] == "success"
    assert contexts
    assert contexts[0]["resume_from_step"] == 1
    assert all(
        entry.get("type") != "checkpoint"
        for entry in detail.json()["step_logs"]
    )


def test_resume_after_login_requires_switch(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "AUTH_REQUIRED_PAUSE", False)

    async def scenario() -> int:
        database_path = tmp_path / "resume-flag.db"
        database_engine = create_async_engine(
            f"sqlite+aiosqlite:///{database_path.as_posix()}"
        )
        async with database_engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(
            database_engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        async with session_factory() as session:
            user = User(
                id=1,
                username="tester",
                email="tester@example.com",
                hashed_password="hash",
            )
            workflow = Workflow(
                user_id=1,
                name="Login workflow",
                natural_language_input="登录后抓取",
                status="draft",
            )
            session.add_all([user, workflow])
            await session.flush()
            await _activate_workflow(session, workflow)
            execution = ExecutionLog(
                workflow_id=workflow.id,
                status="awaiting_user",
            )
            session.add(execution)
            await session.commit()
            await session.refresh(execution)
            execution_id = execution.id

        app = FastAPI()
        app.include_router(executions_router)

        async def override_db():
            async with session_factory() as session:
                yield session

        async def override_user() -> User:
            async with session_factory() as session:
                current_user = await session.get(User, 1)
                assert current_user is not None
                return current_user

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = override_user

        try:
            response = await _request(
                app,
                "POST",
                f"/api/executions/{execution_id}/resume-after-login",
            )
            return response.status_code
        finally:
            await database_engine.dispose()

    assert asyncio.run(scenario()) == 403
