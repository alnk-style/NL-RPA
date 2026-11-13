"""执行管理器写入指标 JSON 的行为。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.engine.execution_manager import ExecutionManager
from app.engine.runner import ExecutionResult
from app.models.execution import ExecutionLog
from app.models.user import User
from app.models.workflow import Workflow
from tests.support.versioned_workflows import activate_test_version

STRUCTURED_PLAN = {
    "executor_type": "web",
    "execution_mode": "structured",
    "steps": [
        {
            "step_id": 1,
            "executor": "web",
            "action": "goto",
            "params": {"url": "https://example.com"},
            "idempotency_key": "open",
        },
        {
            "step_id": 2,
            "executor": "web",
            "action": "click",
            "params": {},
        },
    ],
}


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


async def _seed_workflow(session: AsyncSession) -> Workflow:
    session.add(
        User(
            id=1,
            username="tester",
            email="tester@example.com",
            hashed_password="hash",
        )
    )
    workflow = Workflow(
        user_id=1,
        name="metrics workflow",
        natural_language_input="test",
        structured_plan=STRUCTURED_PLAN,
        status="draft",
    )
    session.add(workflow)
    await session.flush()
    await activate_test_version(
        session,
        workflow,
        plan=STRUCTURED_PLAN,
    )
    await session.commit()
    await session.refresh(workflow)
    return workflow


class ScriptedEngine:
    def __init__(self, results: list[ExecutionResult]) -> None:
        self.results = list(results)

    async def execute_with_debug(self, _workflow, _context=None):
        if self.results:
            return self.results.pop(0)
        return ExecutionResult(
            success=True,
            output="done",
            error=None,
            screenshots=[],
            step_logs=[],
            duration_seconds=0.1,
        )


def _config(**overrides):
    values = {
        "AUTH_REQUIRED_PAUSE": False,
        "WEB_EXPORT_DIRS": "",
        "CREDENTIAL_ENCRYPTION_KEY": "test-encryption-key",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_execution_metrics_recorded_after_run() -> None:
    async def scenario() -> None:
        session_factory, engine = await _database()
        async with session_factory() as session:
            workflow = await _seed_workflow(session)

        result = ExecutionResult(
            success=False,
            output="",
            error="locator not found",
            screenshots=[],
            step_logs=[
                {"step_index": 0, "status": "success"},
                {
                    "step_index": 1,
                    "status": "failed",
                    "failure_category": "locator_not_found",
                    "attempts": 2,
                },
            ],
            duration_seconds=0.5,
            details={
                "extracted": {
                    "large_rows": [{"id": 1}],
                    "password": "must-not-persist",
                }
            },
        )
        manager = ExecutionManager(
            ScriptedEngine([result]),
            session_factory=session_factory,
            config=_config(),
        )
        execution = await manager.start_workflow(workflow.id)
        await manager.execute_existing(execution.id)

        async with session_factory() as session:
            stored = await session.get(ExecutionLog, execution.id)
        assert stored is not None
        metrics = stored.metrics or {}
        assert metrics["step_count"] == 2
        assert metrics["failed_steps"] == 1
        assert metrics["failure_category"] == "locator_not_found"
        assert metrics["repairs_attempted"] == 1
        assert metrics["repairs_succeeded"] == 0
        assert metrics["duration_seconds"] >= 0
        assert metrics["variables"]["large_rows"] == [{"id": 1}]
        assert metrics["variables"]["password"] == "***"
        assert stored.status == "failed"
        await engine.dispose()

    asyncio.run(scenario())


def test_auth_pause_and_resume_record_intervention_and_resumed() -> None:
    async def scenario() -> None:
        session_factory, engine = await _database()
        async with session_factory() as session:
            workflow = await _seed_workflow(session)

        awaiting = ExecutionResult(
            success=False,
            output="",
            error="请重新登录",
            screenshots=[],
            step_logs=[
                {"step_index": 0, "status": "success"},
            ],
            duration_seconds=0.2,
            details={
                "awaiting_user": True,
                "failed_step": {
                    "step_index": 1,
                    "failure_category": "auth_required",
                },
            },
        )
        resumed = ExecutionResult(
            success=True,
            output="ok",
            error=None,
            screenshots=[],
            step_logs=[
                {"step_index": 0, "status": "success"},
                {"step_index": 1, "status": "success"},
            ],
            duration_seconds=0.3,
        )
        manager = ExecutionManager(
            ScriptedEngine([awaiting, resumed]),
            session_factory=session_factory,
            config=_config(AUTH_REQUIRED_PAUSE=True),
        )
        execution = await manager.start_workflow(workflow.id)
        await manager.execute_existing(execution.id)

        async with session_factory() as session:
            stored = await session.get(ExecutionLog, execution.id)
        assert stored is not None
        assert stored.status == "awaiting_user"
        assert (stored.metrics or {}).get("manual_interventions") == 1

        await manager.resume_after_login(execution.id)
        await manager.execute_existing(execution.id)
        async with session_factory() as session:
            stored = await session.get(ExecutionLog, execution.id)
        assert stored is not None
        assert stored.status == "success"
        metrics = stored.metrics or {}
        assert metrics.get("resumed") is True
        assert metrics.get("manual_interventions") == 1
        await engine.dispose()

    asyncio.run(scenario())
