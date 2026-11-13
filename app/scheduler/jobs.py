from __future__ import annotations

import inspect
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from apscheduler.executors.asyncio import AsyncIOExecutor
from apscheduler.executors.pool import (
    ProcessPoolExecutor,
    ThreadPoolExecutor,
)
from apscheduler.jobstores.base import JobLookupError
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.schedulers.base import (
    STATE_PAUSED,
    SchedulerNotRunningError,
)
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select
from sqlalchemy.engine import make_url

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.engine.artifacts import deliver_execution_artifacts
from app.models.execution import ExecutionLog
from app.models.workflow import Workflow

if TYPE_CHECKING:
    from app.config import Settings


logger = logging.getLogger(__name__)


def _sync_database_url(database_url: str) -> str:
    """Return a synchronous SQLAlchemy URL for APScheduler's job store."""
    url = make_url(database_url)
    if url.drivername == "sqlite+aiosqlite":
        url = url.set(drivername="sqlite")
    return url.render_as_string(hide_password=False)


def _cron_trigger_args(cron_expression: str | None) -> dict[str, str]:
    if not isinstance(cron_expression, str) or not cron_expression.strip():
        raise ValueError("Cron expression is required for scheduled workflows")

    expression = cron_expression.strip()
    try:
        CronTrigger.from_crontab(expression)
    except ValueError as exc:
        raise ValueError(
            f"Invalid cron expression: {cron_expression}"
        ) from exc

    minute, hour, day, month, day_of_week = expression.split()
    return {
        "minute": minute,
        "hour": hour,
        "day": day,
        "month": month,
        "day_of_week": day_of_week,
    }


def _get_application() -> Any:
    # Imported lazily because app.main imports the scheduler package.
    from app.main import app

    return app


async def _notify(
    notifier: Any,
    execution_log: ExecutionLog,
    workflow: Workflow,
) -> None:
    if notifier is None:
        return

    notify = getattr(notifier, "notify", None)
    if not callable(notify):
        logger.warning("Notifier does not expose notify()")
        return

    try:
        result = notify(execution_log, workflow)
        if inspect.isawaitable(result):
            await result
    except Exception:
        logger.exception(
            "Failed to notify workflow %s execution %s",
            workflow.id,
            execution_log.id,
        )


class SchedulerManager:
    def __init__(self, config: Settings):
        self.config = config
        database_url = getattr(
            config,
            "DATABASE_URL",
            "sqlite+aiosqlite:///./rpa.db",
        )
        self.scheduler = AsyncIOScheduler(
            jobstores={
                "default": SQLAlchemyJobStore(
                    url=_sync_database_url(database_url),
                )
            },
            executors={
                "default": AsyncIOExecutor(),
                "threadpool": ThreadPoolExecutor(max_workers=10),
                "processpool": ProcessPoolExecutor(max_workers=5),
            },
            job_defaults={
                "coalesce": True,
                "max_instances": 1,
                "misfire_grace_time": 60,
            },
        )

    async def schedule_workflow(self, workflow: Workflow) -> None:
        if workflow.trigger_type != "cron":
            raise ValueError(
                "Scheduled workflows must use the 'cron' trigger type"
            )

        trigger_args = _cron_trigger_args(workflow.cron_expression)
        self.scheduler.add_job(
            self._execute_workflow_job,
            trigger="cron",
            args=[workflow.id],
            id=f"workflow_{workflow.id}",
            replace_existing=True,
            **trigger_args,
        )
        workflow.status = "published"

    async def unschedule_workflow(self, workflow_id: int) -> None:
        try:
            self.scheduler.remove_job(f"workflow_{workflow_id}")
        except JobLookupError:
            pass

    async def reschedule(self, workflow: Workflow) -> None:
        await self.unschedule_workflow(workflow.id)
        await self.schedule_workflow(workflow)

    @staticmethod
    async def _execute_workflow_job(workflow_id: int) -> None:
        application = _get_application()

        async with AsyncSessionLocal() as session:
            workflow = await session.get(Workflow, workflow_id)
            if workflow is None:
                logger.warning(
                    "Scheduled workflow %s no longer exists",
                    workflow_id,
                )
                return

            engine = getattr(application.state, "engine", None)
            if engine is None:
                raise RuntimeError("Execution engine is not ready")

            execution_log = ExecutionLog(
                workflow_id=workflow_id,
                workspace_id=getattr(workflow, "workspace_id", None),
                status="running",
            )
            session.add(execution_log)
            await session.commit()
            await session.refresh(execution_log)

            execution_manager = getattr(
                application.state,
                "execution_manager",
                None,
            )
            if execution_manager is not None:
                try:
                    await execution_manager.execute_existing(
                        execution_log.id
                    )
                except Exception:
                    logger.exception(
                        "Scheduled workflow %s execution %s failed",
                        workflow_id,
                        execution_log.id,
                    )
                return

            try:
                result = await engine.execute_with_debug(
                    workflow,
                    {"execution_log_id": execution_log.id},
                )
                if result is None:
                    raise RuntimeError(
                        "Execution engine returned no result"
                    )
            except Exception as exc:
                execution_log.status = "failed"
                execution_log.error_message = (
                    str(exc) or exc.__class__.__name__
                )
                execution_log.finished_at = datetime.utcnow()
                await session.commit()
                await _notify(
                    getattr(application.state, "notifier", None),
                    execution_log,
                    workflow,
                )
                raise

            execution_log.status = (
                "success" if result.success else "failed"
            )
            execution_log.error_message = getattr(result, "error", None)
            execution_log.screenshots = list(
                getattr(result, "screenshots", None) or []
            )
            execution_log.step_logs = list(
                getattr(result, "step_logs", None) or []
            )
            execution_log.artifacts = await deliver_execution_artifacts(
                session,
                workflow,
                getattr(result, "artifacts", None),
                get_settings().WEB_EXPORT_DIRS,
            )
            execution_log.finished_at = datetime.utcnow()
            await session.commit()

            await _notify(
                getattr(application.state, "notifier", None),
                execution_log,
                workflow,
            )

    async def start(self) -> None:
        if self.scheduler.running:
            await self.resume()
            return

        self.scheduler.start()
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Workflow).where(
                    Workflow.status == "published",
                    Workflow.trigger_type == "cron",
                )
            )
            workflows = list(result.scalars().all())

        for workflow in workflows:
            try:
                await self.schedule_workflow(workflow)
            except ValueError:
                logger.exception(
                    "Cannot restore scheduled workflow %s",
                    workflow.id,
                )

    async def shutdown(self) -> None:
        try:
            self.scheduler.shutdown()
        except SchedulerNotRunningError:
            pass

    async def close(self) -> None:
        await self.shutdown()

    @property
    def is_running(self) -> bool:
        return self.scheduler.running and not self.is_paused

    @property
    def is_paused(self) -> bool:
        return self.scheduler.state == STATE_PAUSED

    async def pause(self) -> None:
        if self.scheduler.running and not self.is_paused:
            self.scheduler.pause()

    async def resume(self) -> None:
        if self.scheduler.running and self.is_paused:
            self.scheduler.resume()

    async def stop(self) -> None:
        # APScheduler executors cannot be restarted after shutdown.
        await self.pause()

    async def toggle(self) -> bool:
        if not self.scheduler.running:
            await self.start()
            return True

        if self.is_paused:
            await self.resume()
            return True

        await self.pause()
        return False


__all__ = ["SchedulerManager"]
