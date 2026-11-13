from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.api.dependencies import get_current_user
from app.api.human_tasks import router as human_tasks_router
from app.database import Base, get_db
from app.models.execution import ExecutionLog
from app.models.human_task import HumanTask
from app.models.user import User
from app.models.workflow import Workflow
from app.services.human_tasks import (
    HumanTaskManager,
    VerificationResult,
)
from tests.support.versioned_workflows import activate_test_version


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
) -> tuple[int, int, int]:
    async with factory() as session:
        session.add_all(
            [
                User(
                    id=1,
                    username="owner",
                    email="owner@example.com",
                    hashed_password="hash",
                ),
                User(
                    id=2,
                    username="other",
                    email="other@example.com",
                    hashed_password="hash",
                ),
            ]
        )
        workflow = Workflow(
            user_id=1,
            name="Owner workflow",
            natural_language_input="login",
            status="draft",
        )
        other_workflow = Workflow(
            user_id=2,
            name="Other workflow",
            natural_language_input="login",
            status="draft",
        )
        session.add_all([workflow, other_workflow])
        await session.flush()
        await activate_test_version(session, workflow)
        await activate_test_version(session, other_workflow)
        execution = ExecutionLog(
            workflow_id=workflow.id,
            workflow_version_id=workflow.active_version_id,
            status="awaiting_user",
            step_logs=[
                {
                    "type": "checkpoint",
                    "action": "checkpoint",
                    "status": "awaiting_user",
                    "next_step_index": 1,
                    "completed_step_ids": [1],
                }
            ],
        )
        other_execution = ExecutionLog(
            workflow_id=other_workflow.id,
            workflow_version_id=other_workflow.active_version_id,
            status="awaiting_user",
        )
        session.add_all([execution, other_execution])
        await session.flush()
        payload = {
            "verification": {
                "type": "web_logged_in",
                "success_selector": "[data-user]",
            },
            "current_url": "https://example.com/login",
        }
        task = HumanTask(
            execution_id=execution.id,
            step_id=2,
            kind="login_required",
            dedupe_key="owner-task",
            instruction="请完成登录",
            payload=payload,
            status="pending",
            expires_at=datetime.utcnow() + timedelta(minutes=30),
        )
        other_task = HumanTask(
            execution_id=other_execution.id,
            step_id=2,
            kind="login_required",
            dedupe_key="other-task",
            instruction="其他用户任务",
            payload=payload,
            status="pending",
            expires_at=datetime.utcnow() + timedelta(minutes=30),
        )
        session.add_all([task, other_task])
        await session.flush()
        execution.human_task_id = task.id
        other_execution.human_task_id = other_task.id
        await session.commit()
        return int(execution.id), int(task.id), int(other_task.id)


class FakeExecutionManager:
    def __init__(self) -> None:
        self.dispatched: list[int] = []
        self.cancelled: list[tuple[int, str]] = []

    async def dispatch_human_task_resume(self, execution_id: int) -> None:
        self.dispatched.append(execution_id)

    async def cancel(
        self,
        execution_id: int,
        reason: str,
        *,
        already_persisted: bool = False,
    ) -> None:
        del already_persisted
        self.cancelled.append((execution_id, reason))


class FakeVerifier:
    def __init__(self, result: VerificationResult) -> None:
        self.result = result

    async def verify(self, task: HumanTask, request: dict) -> VerificationResult:
        del task, request
        return self.result


def _app(
    factory: async_sessionmaker[AsyncSession],
    manager: HumanTaskManager,
) -> FastAPI:
    app = FastAPI()
    app.include_router(human_tasks_router)
    app.state.human_task_manager = manager

    async def override_db():
        async with factory() as session:
            yield session

    async def override_user() -> User:
        async with factory() as session:
            user = await session.get(User, 1)
            assert user is not None
            return user

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    return app


async def _request(
    app: FastAPI,
    method: str,
    path: str,
    *,
    json: dict | None = None,
) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        return await client.request(method, path, json=json)


def test_human_task_list_and_detail_are_user_scoped() -> None:
    async def scenario():
        factory, engine = await _database()
        try:
            execution_id, task_id, other_task_id = await _seed(factory)
            manager = HumanTaskManager(
                session_factory=factory,
                execution_manager=FakeExecutionManager(),
            )
            app = _app(factory, manager)
            listing = await _request(app, "GET", "/api/human-tasks")
            detail = await _request(
                app,
                "GET",
                f"/api/human-tasks/{task_id}",
            )
            forbidden = await _request(
                app,
                "GET",
                f"/api/human-tasks/{other_task_id}",
            )
            return execution_id, listing, detail, forbidden
        finally:
            await engine.dispose()

    execution_id, listing, detail, forbidden = asyncio.run(scenario())

    assert listing.status_code == 200
    items = listing.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == detail.json()["id"]
    assert items[0]["execution_id"] == execution_id
    assert items[0]["workflow_name"] == "Owner workflow"
    assert items[0]["status"] == "pending"
    assert detail.status_code == 200
    assert detail.json()["instruction"] == "请完成登录"
    assert forbidden.status_code == 404


def test_resume_failure_returns_structured_reason_and_keeps_pending() -> None:
    async def scenario():
        factory, engine = await _database()
        try:
            _, task_id, _ = await _seed(factory)
            manager = HumanTaskManager(
                session_factory=factory,
                execution_manager=FakeExecutionManager(),
                verifier=FakeVerifier(
                    VerificationResult(
                        ok=False,
                        reason="登录成功标志仍不存在",
                        login_state="login_required",
                    )
                ),
            )
            app = _app(factory, manager)
            response = await _request(
                app,
                "POST",
                f"/api/human-tasks/{task_id}/resume",
                json={"decision": "resume", "verification": {}},
            )
            detail = await _request(
                app,
                "GET",
                f"/api/human-tasks/{task_id}",
            )
            return response, detail
        finally:
            await engine.dispose()

    response, detail = asyncio.run(scenario())

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "verification_failed"
    assert "登录成功标志" in response.json()["detail"]["reason"]
    assert detail.json()["status"] == "pending"


def test_resume_success_is_idempotent_and_cancel_terminalizes() -> None:
    async def scenario():
        factory, engine = await _database()
        execution_manager = FakeExecutionManager()
        try:
            execution_id, task_id, _ = await _seed(factory)
            manager = HumanTaskManager(
                session_factory=factory,
                execution_manager=execution_manager,
                verifier=FakeVerifier(
                    VerificationResult(
                        ok=True,
                        login_state="ready",
                        details={"selector": "[data-user]"},
                    )
                ),
            )
            app = _app(factory, manager)
            first = await _request(
                app,
                "POST",
                f"/api/human-tasks/{task_id}/resume",
                json={"decision": "resume", "verification": {}},
            )
            second = await _request(
                app,
                "POST",
                f"/api/human-tasks/{task_id}/resume",
                json={"decision": "resume", "verification": {}},
            )
            cancel_after_resolve = await _request(
                app,
                "POST",
                f"/api/human-tasks/{task_id}/cancel",
                json={"reason": "取消"},
            )
            return (
                execution_id,
                first,
                second,
                cancel_after_resolve,
                execution_manager,
            )
        finally:
            await engine.dispose()

    execution_id, first, second, cancel_after_resolve, manager = (
        asyncio.run(scenario())
    )

    assert first.status_code == 200
    assert first.json()["human_task"]["status"] == "resolved"
    assert first.json()["execution"]["status"] == "running"
    assert second.status_code == 200
    assert second.json()["idempotent"] is True
    assert manager.dispatched == [execution_id]
    assert cancel_after_resolve.status_code == 409


def test_cancel_pending_task_and_expired_task_response() -> None:
    async def scenario():
        factory, engine = await _database()
        execution_manager = FakeExecutionManager()
        try:
            execution_id, task_id, _ = await _seed(factory)
            manager = HumanTaskManager(
                session_factory=factory,
                execution_manager=execution_manager,
            )
            app = _app(factory, manager)
            cancelled = await _request(
                app,
                "POST",
                f"/api/human-tasks/{task_id}/cancel",
                json={"reason": "用户取消"},
            )
            async with factory() as session:
                task = await session.get(HumanTask, task_id)
                assert task is not None
                task.status = "pending"
                task.resolved_at = None
                task.dedupe_key = "expired-task"
                task.expires_at = datetime.utcnow() - timedelta(seconds=1)
                execution = await session.get(ExecutionLog, execution_id)
                assert execution is not None
                execution.status = "awaiting_user"
                await session.commit()
            expired = await _request(
                app,
                "POST",
                f"/api/human-tasks/{task_id}/resume",
                json={"decision": "resume", "verification": {}},
            )
            return cancelled, expired, execution_manager
        finally:
            await engine.dispose()

    cancelled, expired, manager = asyncio.run(scenario())

    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert manager.cancelled
    assert expired.status_code == 409
    assert expired.json()["detail"]["code"] == "task_expired"


def test_visual_candidate_resume_api_uses_server_candidate_id() -> None:
    async def scenario():
        factory, engine = await _database()
        execution_manager = FakeExecutionManager()
        try:
            execution_id, login_task_id, _ = await _seed(factory)
            manager = HumanTaskManager(
                session_factory=factory,
                execution_manager=execution_manager,
            )
            await manager.cancel(
                login_task_id,
                reason="切换到视觉任务",
                actor_id=1,
            )
            async with factory() as session:
                execution = await session.get(ExecutionLog, execution_id)
                assert execution is not None
                execution.status = "awaiting_user"
                execution.human_task_id = None
                await session.commit()
            visual = await manager.create(
                execution_id,
                step_id=3,
                kind="visual_ambiguous",
                instruction="请选择目标",
                payload={
                    "action": "click",
                    "risk_level": "read_only",
                    "execution_allowed": True,
                    "candidates": [
                        {
                            "candidate_id": "cand_api",
                            "target": "导出",
                            "bbox": [10, 10, 40, 30],
                            "confidence": 0.95,
                        }
                    ],
                    "verification": {"type": "visual_selection"},
                },
            )
            app = _app(factory, manager)
            missing = await _request(
                app,
                "POST",
                f"/api/human-tasks/{visual.id}/resume",
                json={"decision": "resume", "verification": {}},
            )
            valid = await _request(
                app,
                "POST",
                f"/api/human-tasks/{visual.id}/resume",
                json={
                    "decision": "resume",
                    "verification": {
                        "candidate_id": "cand_api",
                        "action": "delete",
                        "bbox": [0, 0, 1, 1],
                    },
                },
            )
            return missing, valid, execution_manager.dispatched
        finally:
            await engine.dispose()

    missing, valid, dispatched = asyncio.run(scenario())

    assert missing.status_code == 409
    assert missing.json()["detail"]["code"] == "candidate_missing"
    assert valid.status_code == 200
    assert valid.json()["human_task"]["status"] == "resolved"
    assert dispatched
