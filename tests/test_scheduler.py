from __future__ import annotations

# ruff: noqa: E402

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from apscheduler.jobstores.base import JobLookupError
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

from app.database import Base
from app.models.execution import ExecutionLog
from app.models.user import User
from app.models.workflow import Workflow
from app.scheduler import SchedulerManager
from app.scheduler import jobs


def _config() -> SimpleNamespace:
    return SimpleNamespace(DATABASE_URL="sqlite+aiosqlite:///./rpa.db")


def _workflow() -> SimpleNamespace:
    return SimpleNamespace(
        id=42,
        trigger_type="cron",
        cron_expression="0 9 * * *",
        status="draft",
    )


def test_schedule_workflow() -> None:
    async def scenario() -> tuple[
        SchedulerManager,
        SimpleNamespace,
        MagicMock,
    ]:
        with (
            patch.object(jobs, "SQLAlchemyJobStore") as jobstore_cls,
            patch.object(jobs, "AsyncIOScheduler") as scheduler_cls,
        ):
            manager = SchedulerManager(_config())
            scheduler = scheduler_cls.return_value
            workflow = _workflow()

            await manager.schedule_workflow(workflow)

        jobstore_cls.assert_called_once_with(url="sqlite:///./rpa.db")
        return manager, workflow, scheduler

    manager, workflow, scheduler = asyncio.run(scenario())

    scheduler.add_job.assert_called_once_with(
        manager._execute_workflow_job,
        trigger="cron",
        args=[workflow.id],
        id=f"workflow_{workflow.id}",
        replace_existing=True,
        minute="0",
        hour="9",
        day="*",
        month="*",
        day_of_week="*",
    )
    assert workflow.status == "published"


def test_unschedule_workflow() -> None:
    async def scenario() -> MagicMock:
        with patch.object(jobs, "AsyncIOScheduler") as scheduler_cls:
            manager = SchedulerManager(_config())
            scheduler = scheduler_cls.return_value
            scheduler.remove_job.side_effect = JobLookupError("workflow_42")

            await manager.unschedule_workflow(42)
            return scheduler

    scheduler = asyncio.run(scenario())

    scheduler.remove_job.assert_called_once_with("workflow_42")


def test_execute_workflow_job() -> None:
    async def scenario() -> tuple[
        ExecutionLog,
        SimpleNamespace,
        SimpleNamespace,
    ]:
        database_engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
        )
        try:
            async with database_engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)

            session_factory = async_sessionmaker(
                database_engine,
                class_=AsyncSession,
                expire_on_commit=False,
            )
            async with session_factory() as session:
                session.add(
                    User(
                        id=1,
                        username="scheduler-user",
                        email="scheduler@example.com",
                        hashed_password="hash",
                    )
                )
                session.add(
                    Workflow(
                        id=42,
                        user_id=1,
                        name="Scheduled workflow",
                        natural_language_input="Open example.com",
                        generated_code=(
                            "await page.goto('https://example.com')"
                        ),
                        trigger_type="cron",
                        cron_expression="0 9 * * *",
                        status="published",
                    )
                )
                await session.commit()

            execution_engine = SimpleNamespace(
                execute_with_debug=AsyncMock(
                    return_value=SimpleNamespace(
                        success=True,
                        error=None,
                        screenshots=["logs/run-1/step_00.png"],
                        step_logs=[{"step_index": 0, "status": "success"}],
                    )
                )
            )
            notifier = SimpleNamespace(notify=AsyncMock())
            application = SimpleNamespace(
                state=SimpleNamespace(
                    engine=execution_engine,
                    notifier=notifier,
                )
            )

            with (
                patch.object(jobs, "AsyncSessionLocal", session_factory),
                patch.object(
                    jobs,
                    "_get_application",
                    return_value=application,
                ),
            ):
                await SchedulerManager._execute_workflow_job(42)

            async with session_factory() as session:
                result = await session.execute(
                    select(ExecutionLog).where(
                        ExecutionLog.workflow_id == 42
                    )
                )
                execution_log = result.scalar_one()

            return execution_log, execution_engine, notifier
        finally:
            await database_engine.dispose()

    execution_log, execution_engine, notifier = asyncio.run(scenario())

    execution_engine.execute_with_debug.assert_awaited_once()
    assert execution_log.status == "success"
    assert execution_log.finished_at is not None
    assert execution_log.error_message is None
    assert execution_log.screenshots == ["logs/run-1/step_00.png"]
    assert execution_log.step_logs == [
        {"step_index": 0, "status": "success"}
    ]
    notifier.notify.assert_awaited_once()
    notified_log, notified_workflow = notifier.notify.await_args.args
    assert notified_workflow.id == 42
    assert notified_log.id == execution_log.id
