from __future__ import annotations

import asyncio
import inspect
import logging
from datetime import datetime
from types import SimpleNamespace
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import runtime_flags
from app.host_capabilities import normalize_capabilities
from app.config import get_settings
from app.database import AsyncSessionLocal
from app.engine.artifacts import deliver_execution_artifacts
from app.engine.checkpoints import (
    build_checkpoint,
    find_checkpoint,
    is_checkpoint,
    strip_checkpoints,
)
from app.engine.desktop_repair import build_desktop_repair
from app.engine.dag import (
    ExecutionPlanError,
    normalize_execution_plan,
    normalize_executor,
    step_condition,
)
from app.engine.hybrid_executor import HybridExecutor
from app.engine.host_router import (
    HostInfo,
    HostRouter,
    host_step_for_plan,
)
from app.engine.preflight import PreflightService
from app.engine.queue_backends import ExecutionQueue
from app.engine.runner import ExecutionResult
from app.models.desktop_task import DesktopTask
from app.models.agent import DesktopAgent
from app.models.execution import ExecutionLog
from app.models.human_task import HumanTask
from app.models.human_task_event import HumanTaskEvent
from app.models.browser_profile import BrowserProfile
from app.models.workflow import Workflow
from app.models.workspace import Workspace
from app.metrics.steps import summarize_steps
from app.protocols import classify_failure
from app.schemas.execution_policy import validate_execution_policy
from app.security.credentials import load_credential_values
from app.security.workspaces import workspace_quota
from app.security.redaction import (
    redact_execution_result,
    redact_payload,
    secret_values,
)
from app.services.execution_versions import (
    ExecutionSnapshot,
    ExecutionVersionService,
)
from app.services.checkpoint_service import (
    CheckpointService,
    checkpoint_resume_context,
)
from app.services.execution_events import append_execution_event
from app.services.hosts import (
    active_task_counts,
    session_fingerprint,
)
from app.services.human_tasks import (
    HumanTaskManager,
    build_human_task_spec,
)
from app.services.workflow_versions import WorkflowVersionError

logger = logging.getLogger(__name__)

_ACTIVE_EXECUTION_STATUSES = {"pending", "running"}
_BLOCKING_EXECUTION_STATUSES = {"pending", "running", "awaiting_user"}
_TERMINAL_EXECUTION_STATUSES = {
    "success",
    "failed",
    "error",
    "completed",
    "cancelled",
}
_ACTIVE_DESKTOP_TASK_STATUSES = {"pending", "dispatched", "running"}
_PAUSE_WAIT_SECONDS = 5.0


class ExecutionStateError(RuntimeError):
    """Raised when an execution cannot perform the requested transition."""


async def recover_interrupted_executions(
    session_factory: async_sessionmaker[AsyncSession] = AsyncSessionLocal,
) -> int:
    """Pause executions that were left running by a previous process."""

    now = datetime.utcnow()
    recovered = 0
    changed = False

    async with session_factory() as session:
        execution_result = await session.execute(
            select(ExecutionLog).where(
                ExecutionLog.status.in_(_ACTIVE_EXECUTION_STATUSES)
            )
        )
        for execution_log in execution_result.scalars().all():
            execution_log.status = "paused"
            execution_log.finished_at = now
            if not execution_log.error_message:
                execution_log.error_message = (
                    "执行因服务重启已暂停，可继续或重新运行。"
                )
            recovered += 1
            changed = True

        task_result = await session.execute(
            select(DesktopTask).where(
                DesktopTask.status.in_(_ACTIVE_DESKTOP_TASK_STATUSES)
            )
        )
        for task in task_result.scalars().all():
            task.status = "paused"
            task.error_message = "执行因服务重启已暂停。"
            task.finished_at = now
            changed = True

        if changed:
            await session.commit()

    return recovered


class ExecutionManager:
    """Run workflows in background tasks and expose pause/resume controls."""

    def __init__(
        self,
        engine: Any,
        notifier: Any | None = None,
        session_factory: async_sessionmaker[AsyncSession] = AsyncSessionLocal,
        desktop_repair: Any | None = None,
        config: Any | None = None,
        queue: ExecutionQueue | None = None,
        human_task_manager: HumanTaskManager | None = None,
    ) -> None:
        self.engine = engine
        self.notifier = notifier
        self.session_factory = session_factory
        self.config = config
        self.queue = queue
        self.desktop_repair = (
            desktop_repair
            if desktop_repair is not None
            else build_desktop_repair(engine, session_factory)
        )
        self._tasks: dict[int, asyncio.Task[None]] = {}
        self._resume_state: dict[int, dict[str, Any]] = {}
        self._state_lock = asyncio.Lock()
        self._hybrid: HybridExecutor | None = None
        self.human_tasks = human_task_manager or HumanTaskManager(
            session_factory=self.session_factory,
            execution_manager=self,
            online_agent_ids=self.online_agent_ids,
        )

    async def start_workflow(
        self,
        workflow_id: int,
        workspace_id: int | None = None,
        version_id: int | None = None,
    ) -> ExecutionLog:
        async with self.session_factory() as session:
            workflow = await session.get(Workflow, workflow_id)
            if workflow is None:
                raise ExecutionStateError("Workflow not found")

            resolved_workspace_id = (
                workspace_id
                if workspace_id is not None
                else getattr(workflow, "workspace_id", None)
            )
            if resolved_workspace_id is not None:
                await self._ensure_workspace_quota(
                    session,
                    int(resolved_workspace_id),
                )

            active = await session.scalar(
                select(func.count(ExecutionLog.id)).where(
                    ExecutionLog.workflow_id == workflow.id,
                    ExecutionLog.status.in_(
                        _BLOCKING_EXECUTION_STATUSES
                    ),
                )
            )
            if int(active or 0) > 0:
                raise ExecutionStateError(
                    "Workflow already has an active execution"
                )
            if (
                version_id is not None
                and version_id != workflow.active_version_id
            ):
                raise ExecutionStateError(
                    "Only the active workflow version can be executed"
                )
            try:
                snapshot = await ExecutionVersionService(
                    session
                ).resolve_active(workflow)
            except WorkflowVersionError as exc:
                raise ExecutionStateError(str(exc)) from exc
            browser_profile_id = await session.scalar(
                select(BrowserProfile.id)
                .where(BrowserProfile.user_id == workflow.user_id)
                .order_by(BrowserProfile.id)
                .limit(1)
            )

            preflight = PreflightService().check(
                snapshot,
                context={
                    "legacy_code_enabled": self._legacy_code_enabled(),
                    "hosts": await self._load_host_infos(
                        session,
                        workflow,
                    ),
                },
            )
            initial_status = "running"
            error_message: str | None = None
            if preflight.status == "blocked":
                initial_status = "failed"
                error_message = preflight.message
            elif preflight.status == "awaiting_user":
                initial_status = "awaiting_user"
                error_message = preflight.message
            execution_log = ExecutionLog(
                workflow_id=workflow.id,
                workspace_id=resolved_workspace_id,
                workflow_version_id=snapshot.version_id,
                execution_policy_snapshot=snapshot.policy.model_dump(
                    mode="json"
                ),
                browser_profile_id=(
                    int(browser_profile_id)
                    if browser_profile_id is not None
                    else None
                ),
                trigger_type=workflow.trigger_type or "manual",
                status=initial_status,
                error_message=error_message,
                finished_at=(
                    datetime.utcnow()
                    if initial_status != "running"
                    else None
                ),
            )
            session.add(execution_log)
            await session.commit()
            await session.refresh(execution_log)

        if execution_log.status == "running":
            await self._dispatch(execution_log.id)
        return execution_log

    async def _ensure_workspace_quota(
        self,
        session: AsyncSession,
        workspace_id: int,
    ) -> None:
        workspace = await session.get(Workspace, workspace_id)
        quota = workspace_quota(workspace, self.config)
        limit = int(quota.get("max_concurrent_executions", 0) or 0)
        if limit <= 0:
            return
        active = await session.scalar(
            select(func.count(ExecutionLog.id))
            .join(
                Workflow,
                ExecutionLog.workflow_id == Workflow.id,
            )
            .where(
                Workflow.workspace_id == workspace_id,
                ExecutionLog.status.in_(_ACTIVE_EXECUTION_STATUSES),
            )
        )
        if int(active or 0) >= limit:
            raise ExecutionStateError(
                "workspace 并发配额已满："
                f"{active}/{limit} 个执行正在进行。"
            )

    async def pause(self, execution_id: int) -> ExecutionLog:
        task: asyncio.Task[None] | None = None

        async with self._state_lock:
            async with self.session_factory() as session:
                execution_log = await session.get(ExecutionLog, execution_id)
                if execution_log is None:
                    raise ExecutionStateError("Execution not found")
                if execution_log.status == "paused":
                    return execution_log
                if execution_log.status not in _ACTIVE_EXECUTION_STATUSES:
                    raise ExecutionStateError(
                        "Only running executions can be paused"
                    )

                execution_log.status = "paused"
                execution_log.finished_at = datetime.utcnow()
                if not execution_log.error_message:
                    execution_log.error_message = "执行已暂停。"
                execution_log.metrics = _with_intervention(
                    execution_log.metrics
                )
                await session.commit()
                await session.refresh(execution_log)

            task = self._tasks.get(execution_id)
            if task is not None and not task.done():
                task.cancel()

        if task is not None and not task.done():
            try:
                await asyncio.wait_for(
                    asyncio.shield(task),
                    timeout=_PAUSE_WAIT_SECONDS,
                )
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            except Exception:
                pass

        await self._write_pause_checkpoint(execution_id)
        return execution_log

    async def resume(self, execution_id: int) -> ExecutionLog:
        resume_state: dict[str, Any] | None = None
        async with self._state_lock:
            current_task = self._tasks.get(execution_id)
            if current_task is not None and not current_task.done():
                raise ExecutionStateError("Execution is already running")

            async with self.session_factory() as session:
                execution_log = await session.get(ExecutionLog, execution_id)
                if execution_log is None:
                    raise ExecutionStateError("Execution not found")
                if execution_log.status not in {"paused", "failed"}:
                    raise ExecutionStateError(
                        "Only paused executions can be resumed"
                    )

                checkpoint = find_checkpoint(execution_log.step_logs)
                if (
                    checkpoint is None
                    and execution_log.status == "paused"
                ):
                    workflow = await session.get(
                        Workflow,
                        execution_log.workflow_id,
                    )
                    if (
                        workflow is not None
                        and self._is_structured_workflow(workflow)
                    ):
                        checkpoint = self._checkpoint_from_step_logs(
                            execution_log.step_logs
                        )
                if checkpoint is not None:
                    resume_state = self._resume_state_from_checkpoint(
                        checkpoint,
                        execution_log.step_logs,
                    )
                elif execution_log.status == "failed":
                    raise ExecutionStateError(
                        "Only paused executions can be resumed"
                    )
                else:
                    execution_log.step_logs = []
                workflow = await session.get(
                    Workflow,
                    execution_log.workflow_id,
                )
                if workflow is not None:
                    await self._require_bound_host(
                        session,
                        execution_log,
                        workflow,
                    )
                execution_log.status = "running"
                execution_log.finished_at = None
                execution_log.error_message = None
                execution_log.screenshots = []
                execution_log.artifacts = []
                execution_log.metrics = _mark_resumed(
                    execution_log.metrics
                )
                await session.commit()
                await session.refresh(execution_log)

        self._resume_state.pop(execution_id, None)
        if resume_state is not None:
            self._resume_state[execution_id] = resume_state
        await self._dispatch(execution_id)
        return execution_log

    async def resume_after_login(
        self,
        execution_id: int,
    ) -> ExecutionLog:
        """从登录检查点继续执行，不重跑已成功的步骤。"""

        async with self._state_lock:
            current_task = self._tasks.get(execution_id)
            if current_task is not None and not current_task.done():
                raise ExecutionStateError("Execution is already running")

            async with self.session_factory() as session:
                execution_log = await session.get(
                    ExecutionLog,
                    execution_id,
                )
                if execution_log is None:
                    raise ExecutionStateError("Execution not found")
                if execution_log.status != "awaiting_user":
                    raise ExecutionStateError(
                        "Only awaiting_user executions can resume "
                        "after login"
                    )
                if execution_log.human_task_id is not None:
                    pending_task = await session.get(
                        HumanTask,
                        int(execution_log.human_task_id),
                    )
                    if (
                        pending_task is not None
                        and pending_task.status == "pending"
                    ):
                        raise ExecutionStateError(
                            "Execution has a pending human task; "
                            "resume it through the human task API"
                        )

                checkpoint = find_checkpoint(execution_log.step_logs)
                if checkpoint is None:
                    raise ExecutionStateError(
                        "Execution has no checkpoint"
                    )

                resume_state = self._resume_state_from_checkpoint(
                    checkpoint,
                    execution_log.step_logs,
                )
                workflow = await session.get(
                    Workflow,
                    execution_log.workflow_id,
                )
                if workflow is not None:
                    await self._require_bound_host(
                        session,
                        execution_log,
                        workflow,
                    )
                execution_log.status = "running"
                execution_log.finished_at = None
                execution_log.error_message = None
                execution_log.metrics = _mark_resumed(
                    execution_log.metrics
                )
                await session.commit()
                await session.refresh(execution_log)

        self._resume_state[execution_id] = resume_state
        await self._dispatch(execution_id)
        return execution_log

    async def dispatch_human_task_resume(
        self,
        execution_id: int,
    ) -> None:
        """Dispatch an execution already resumed by HumanTaskManager."""

        async with self._state_lock:
            current_task = self._tasks.get(execution_id)
            if current_task is not None and not current_task.done():
                return
            async with self.session_factory() as session:
                execution_log = await session.get(
                    ExecutionLog,
                    execution_id,
                )
                if execution_log is None:
                    raise ExecutionStateError("Execution not found")
                if execution_log.status != "running":
                    raise ExecutionStateError(
                        "Execution is not ready to resume"
                    )
                checkpoint = find_checkpoint(execution_log.step_logs)
                if checkpoint is None:
                    raise ExecutionStateError(
                        "Execution has no checkpoint"
                    )
                resume_state = self._resume_state_from_checkpoint(
                    checkpoint,
                    execution_log.step_logs,
                )
                workflow = await session.get(
                    Workflow,
                    execution_log.workflow_id,
                )
                if workflow is not None:
                    await self._require_bound_host(
                        session,
                        execution_log,
                        workflow,
                    )
        self._resume_state[execution_id] = resume_state
        await self._dispatch(execution_id)

    async def cancel(
        self,
        execution_id: int,
        reason: str,
        *,
        already_persisted: bool = False,
    ) -> ExecutionLog:
        task: asyncio.Task[None] | None = None
        desktop_task_ids: list[int] = []
        normalized_reason = str(reason).strip() or "执行已取消。"
        async with self._state_lock:
            async with self.session_factory() as session:
                execution_log = await session.get(
                    ExecutionLog,
                    execution_id,
                )
                if execution_log is None:
                    raise ExecutionStateError("Execution not found")
                terminal = (
                    execution_log.status
                    in _TERMINAL_EXECUTION_STATUSES
                )
                if terminal and not (
                    execution_log.status == "cancelled"
                    and already_persisted
                ):
                    if already_persisted:
                        return execution_log
                    raise ExecutionStateError(
                        "Terminal execution cannot be cancelled"
                    )
                changed = False
                now = datetime.utcnow()
                if execution_log.status != "cancelled":
                    execution_log.status = "cancelled"
                    execution_log.finished_at = now
                    execution_log.error_message = normalized_reason
                    if not already_persisted:
                        append_execution_event(
                            session,
                            execution_id,
                            "execution.cancelled",
                            {"reason": normalized_reason},
                        )
                    changed = True

                human_task = (
                    await session.get(
                        HumanTask,
                        int(execution_log.human_task_id),
                    )
                    if execution_log.human_task_id is not None
                    else None
                )
                if (
                    human_task is not None
                    and human_task.status == "pending"
                ):
                    human_task.status = "cancelled"
                    human_task.resolved_at = now
                    human_task.dedupe_key = None
                    human_task.verification_started_at = None
                    human_task.payload = redact_payload(
                        {
                            **dict(human_task.payload or {}),
                            "cancellation": {
                                "reason": normalized_reason,
                                "cancelled_at": now.isoformat(),
                            },
                        }
                    )
                    session.add(
                        HumanTaskEvent(
                            human_task_id=human_task.id,
                            event_type="cancelled",
                            from_status="pending",
                            to_status="cancelled",
                            details={"reason": normalized_reason},
                        )
                    )
                    append_execution_event(
                        session,
                        execution_id,
                        "human_task.cancelled",
                        {
                            "human_task_id": human_task.id,
                            "kind": human_task.kind,
                            "reason": normalized_reason,
                        },
                    )
                    changed = True
                if changed:
                    await session.commit()
                    await session.refresh(execution_log)
                desktop_tasks = list(
                    (
                        await session.execute(
                            select(DesktopTask).where(
                                DesktopTask.execution_log_id
                                == execution_id,
                                DesktopTask.status.in_(
                                    _ACTIVE_DESKTOP_TASK_STATUSES
                                ),
                            )
                        )
                    ).scalars().all()
                )
                desktop_task_ids = [
                    int(item.id) for item in desktop_tasks
                ]
            task = self._tasks.get(execution_id)
            if task is not None and not task.done():
                task.cancel()

        if task is not None and not task.done():
            try:
                await asyncio.wait_for(
                    asyncio.shield(task),
                    timeout=_PAUSE_WAIT_SECONDS,
                )
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            except Exception:
                pass
        dispatcher = getattr(self.engine, "desktop_dispatcher", None)
        if dispatcher is None:
            dispatcher = getattr(self.desktop_repair, "dispatcher", None)
        cancel_task = getattr(dispatcher, "cancel", None)
        if callable(cancel_task):
            for desktop_task_id in desktop_task_ids:
                try:
                    result = cancel_task(desktop_task_id)
                    if inspect.isawaitable(result):
                        await result
                except Exception:
                    logger.exception(
                        "Failed to cancel desktop task %s",
                        desktop_task_id,
                    )
        return execution_log

    async def execute_existing(self, execution_id: int) -> None:
        """Run an existing execution record and wait for its completion."""

        self._start_task(execution_id)
        task = self._tasks.get(execution_id)
        if task is None:
            raise ExecutionStateError("Execution task was not created")

        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            if not task.done():
                task.cancel()
                raise
            return

    async def run_local(self, execution_id: int) -> None:
        """队列 worker 入口：在本进程执行并等待完成。"""

        self._start_task(execution_id)
        task = self._tasks.get(execution_id)
        if task is None:
            raise ExecutionStateError("Execution task was not created")
        await asyncio.shield(task)

    async def _dispatch(self, execution_id: int) -> None:
        if self.queue is None:
            self._start_task(execution_id)
            return
        await self.queue.enqueue(execution_id)

    async def shutdown(self) -> None:
        execution_ids = list(self._tasks)
        for execution_id in execution_ids:
            try:
                await self.pause(execution_id)
            except ExecutionStateError:
                continue
            except Exception:
                logger.exception(
                    "Failed to pause execution %s during shutdown",
                    execution_id,
                )

        tasks = [
            task
            for task in self._tasks.values()
            if task is not None and not task.done()
        ]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def is_active(self, execution_id: int) -> bool:
        task = self._tasks.get(execution_id)
        return task is not None and not task.done()

    def _start_task(self, execution_id: int) -> None:
        existing = self._tasks.get(execution_id)
        if existing is not None and not existing.done():
            return

        task = asyncio.create_task(
            self._run(execution_id),
            name=f"nl-rpa-execution-{execution_id}",
        )
        self._tasks[execution_id] = task
        task.add_done_callback(
            lambda completed: self._discard_task(
                execution_id,
                completed,
            )
        )

    def _discard_task(
        self,
        execution_id: int,
        task: asyncio.Task[None],
    ) -> None:
        if self._tasks.get(execution_id) is task:
            self._tasks.pop(execution_id, None)

    async def _run(self, execution_id: int) -> None:
        async with self.session_factory() as session:
            execution_log = await session.get(ExecutionLog, execution_id)
            if execution_log is None:
                return
            workflow = await session.get(
                Workflow,
                execution_log.workflow_id,
            )
            version_id = execution_log.workflow_version_id
            snapshot = None
            if version_id is not None:
                try:
                    snapshot = await ExecutionVersionService(
                        session
                    ).resolve_version(workflow, version_id)
                except WorkflowVersionError:
                    snapshot = None
            else:
                try:
                    snapshot = await ExecutionVersionService(
                        session
                    ).resolve_active(workflow)
                    execution_log.workflow_version_id = (
                        snapshot.version_id
                    )
                    execution_log.execution_policy_snapshot = (
                        snapshot.policy.model_dump(mode="json")
                    )
                    await session.commit()
                except WorkflowVersionError:
                    snapshot = None
        if workflow is None:
            await self._persist_failure(
                execution_id,
                "Workflow not found",
            )
            return
        if snapshot is None:
            await self._persist_failure(
                execution_id,
                "Workflow version not found",
            )
            return

        if isinstance(execution_log.execution_policy_snapshot, dict):
            snapshot = ExecutionSnapshot(
                workflow_id=snapshot.workflow_id,
                version_id=snapshot.version_id,
                version=snapshot.version,
                plan=snapshot.plan,
                policy=validate_execution_policy(
                    execution_log.execution_policy_snapshot
                ),
                action_catalog_version=snapshot.action_catalog_version,
                executor_type=snapshot.executor_type,
                schedule_timezone=snapshot.schedule_timezone,
            )
        workflow_view = snapshot.workflow_view(workflow)

        try:
            executor = self._executor_for(workflow_view)
            context = await self._execution_context(
                execution_id,
                workflow_view,
            )
            result = await executor(workflow_view, context)
            if result is None:
                raise RuntimeError(
                    "Execution engine returned no result"
                )
            credentials = context.get("credentials")
            if isinstance(credentials, dict) and credentials:
                result = redact_execution_result(
                    result,
                    secret_values(credentials),
                )
        except asyncio.CancelledError:
            return
        except Exception as exc:
            await self._persist_failure(
                execution_id,
                str(exc) or exc.__class__.__name__,
            )
            return

        persisted = await self._persist_result(
            execution_id,
            result,
            workflow_view,
        )
        if persisted:
            await self._notify(execution_id, workflow_view)

    async def _execution_context(
        self,
        execution_id: int,
        workflow: Workflow,
    ) -> dict[str, Any]:
        context: dict[str, Any] = {
            "execution_log_id": execution_id,
            "workflow_id": getattr(workflow, "id", None),
            "workflow_version_id": getattr(
                workflow,
                "workflow_version_id",
                None,
            ),
            "user_id": getattr(workflow, "user_id", None),
            "workspace_id": getattr(workflow, "workspace_id", None),
        }
        resume_state = self._resume_state.pop(execution_id, None)
        if isinstance(resume_state, dict):
            context.update(resume_state)

        async with self.session_factory() as session:
            execution_log = await session.get(
                ExecutionLog,
                execution_id,
            )
        if execution_log is not None:
            context["host_id"] = execution_log.host_id
            context["host_environment"] = (
                execution_log.host_environment
            )
            context["host_bound"] = bool(execution_log.host_bound)
            context["host_affinity_hash"] = (
                execution_log.host_affinity_hash
            )
            if isinstance(
                execution_log.execution_policy_snapshot,
                dict,
            ):
                context["execution_policy"] = dict(
                    execution_log.execution_policy_snapshot
                )

        browser_profile = await self._load_browser_profile(execution_id)
        if browser_profile is not None:
            context["browser_profile"] = browser_profile

        credentials = await self._load_credentials(workflow)
        if credentials:
            context["credentials"] = credentials
        return context

    async def _load_browser_profile(
        self,
        execution_id: int,
    ) -> dict[str, Any] | None:
        async with self.session_factory() as session:
            execution = await session.get(ExecutionLog, execution_id)
            if (
                execution is None
                or execution.browser_profile_id is None
            ):
                return None
            profile = await session.get(
                BrowserProfile,
                int(execution.browser_profile_id),
            )
            if profile is None:
                return None
            return {
                "id": profile.id,
                "name": profile.name,
                "user_data_dir": profile.user_data_dir,
                "profile_directory": profile.profile_directory,
            }

    async def _load_credentials(
        self,
        workflow: Workflow,
    ) -> dict[str, str]:
        workflow_id = getattr(workflow, "id", None)
        if workflow_id is None:
            return {}

        config = self.config
        if config is None:
            try:
                config = get_settings()
            except Exception:
                return {}
        try:
            async with self.session_factory() as session:
                return await load_credential_values(
                    session,
                    config,
                    int(workflow_id),
                )
        except Exception:
            logger.warning(
                "加载流程 %s 的凭据失败，本次不注入凭据",
                workflow_id,
                exc_info=True,
            )
            return {}

    def _executor_for(self, workflow: Workflow) -> Any:
        if self._needs_hybrid_execution(workflow):
            hybrid = self._hybrid_executor()
            if hybrid is not None:
                return hybrid.execute
        if (
            self.desktop_repair is not None
            and self._is_desktop_workflow(workflow)
        ):
            return self.desktop_repair.execute
        return self.engine.execute_with_debug

    def _hybrid_executor(self) -> HybridExecutor | None:
        if self.engine is None:
            return None
        if self._hybrid is None:
            desktop = self.desktop_repair or getattr(
                self.engine,
                "desktop_dispatcher",
                None,
            )
            self._hybrid = HybridExecutor(
                self.engine,
                desktop,
                session_factory=self.session_factory,
                config=self.config,
            )
        return self._hybrid

    def online_agent_ids(self) -> list[int]:
        dispatcher = getattr(self.engine, "desktop_dispatcher", None)
        connection_manager = getattr(
            dispatcher,
            "connection_manager",
            None,
        )
        getter = getattr(connection_manager, "get_online_agents", None)
        if not callable(getter):
            return []
        try:
            return [int(value) for value in getter()]
        except Exception:
            return []

    async def _load_host_infos(
        self,
        session: AsyncSession,
        workflow: Workflow,
    ) -> list[HostInfo]:
        statement = select(DesktopAgent).where(
            DesktopAgent.user_id == workflow.user_id
        )
        if workflow.workspace_id is not None:
            statement = statement.where(
                DesktopAgent.workspace_id == workflow.workspace_id
            )
        agents = list(
            (await session.execute(statement)).scalars().all()
        )
        counts = await active_task_counts(
            session,
            [int(agent.id) for agent in agents],
        )
        online_ids = set(self.online_agent_ids())
        hosts: list[HostInfo] = []
        for agent in agents:
            manager = self._agent_connection_manager()
            getter = getattr(manager, "get_session_id", None)
            raw_session = (
                getter(int(agent.id))
                if callable(getter)
                else None
            )
            hosts.append(
                HostInfo(
                    host_id=int(agent.id),
                    name=agent.name,
                    environment=str(agent.environment or "unknown"),
                    capabilities=normalize_capabilities(
                        agent.capabilities
                    ),
                    status=(
                        "online"
                        if int(agent.id) in online_ids
                        else "offline"
                    ),
                    active_task_count=counts.get(int(agent.id), 0),
                    session_fingerprint=session_fingerprint(
                        str(raw_session)
                        if raw_session
                        else None
                    ),
                )
            )
        return hosts

    def _agent_connection_manager(self) -> Any | None:
        dispatcher = getattr(self.engine, "desktop_dispatcher", None)
        if dispatcher is None and self.desktop_repair is not None:
            dispatcher = getattr(
                self.desktop_repair,
                "dispatcher",
                None,
            )
        return getattr(dispatcher, "connection_manager", None)

    async def _require_bound_host(
        self,
        session: AsyncSession,
        execution: ExecutionLog,
        workflow: Workflow,
    ) -> None:
        if execution.host_id is None:
            return
        try:
            snapshot = await ExecutionVersionService(
                session
            ).resolve_version(
                workflow,
                int(execution.workflow_version_id),
            )
        except Exception as exc:
            raise ExecutionStateError(
                f"host_unavailable: {exc}"
            ) from exc
        if isinstance(execution.execution_policy_snapshot, dict):
            snapshot = ExecutionSnapshot(
                workflow_id=snapshot.workflow_id,
                version_id=snapshot.version_id,
                version=snapshot.version,
                plan=snapshot.plan,
                policy=validate_execution_policy(
                    execution.execution_policy_snapshot
                ),
                action_catalog_version=(
                    snapshot.action_catalog_version
                ),
                executor_type=snapshot.executor_type,
                schedule_timezone=snapshot.schedule_timezone,
            )
        plan = dict(snapshot.plan)
        if self._next_step_is_server_only(execution, plan):
            return
        required = {
            str(item)
            for item in (
                plan.get("required_capabilities")
                or getattr(workflow, "required_capabilities", None)
                or []
            )
        }
        hosts = await self._load_host_infos(session, workflow)
        decision = HostRouter().select(
            host_step_for_plan(
                plan,
                default_executor=(
                    getattr(workflow, "executor_type", None)
                    or "windows"
                ),
            ),
            policy=snapshot.policy,
            hosts=hosts,
            bound_host_id=int(execution.host_id),
            bound_session_fingerprint=(
                execution.host_affinity_hash
            ),
            required_capabilities=sorted(required),
        )
        if decision.status == "blocked":
            raise ExecutionStateError(
                f"{decision.error_code or 'host_unavailable'}: "
                f"{decision.reason}"
            )

    @staticmethod
    def _next_step_is_server_only(
        execution: ExecutionLog,
        plan: dict[str, Any],
    ) -> bool:
        checkpoint = find_checkpoint(execution.step_logs)
        if checkpoint is None:
            return False
        try:
            next_index = int(
                checkpoint.get("next_step_index")
                or checkpoint.get("step_index")
                or 0
            )
        except (TypeError, ValueError):
            return False
        steps = plan.get("steps")
        if not isinstance(steps, list) or next_index >= len(steps):
            return False
        step = steps[next_index]
        if not isinstance(step, dict):
            return False
        executor = normalize_executor(
            step.get("executor"),
            default="web",
        )
        requirement = str(
            step.get("host_requirement") or "any"
        )
        return (
            executor in {"web", "transform", "files", "email", "mcp"}
            and requirement in {"any", "headless_web"}
        )

    def _needs_hybrid_execution(self, workflow: Workflow) -> bool:
        """混合执行器负责步骤级路由、DAG 依赖与条件步骤。"""

        steps = self._structured_steps(workflow)
        if not steps:
            return False

        default = getattr(workflow, "executor_type", None) or "web"
        executors = {
            normalize_executor(step.get("executor"), default=default)
            for step in steps
        }
        if len(executors) > 1:
            return True
        if not executors <= {"web", "windows"}:
            return True
        if any(
            any(
                key in step
                for key in (
                    "result_var",
                    "output_var",
                    "foreach",
                    "preconditions",
                    "postconditions",
                    "verify",
                )
            )
            for step in steps
        ):
            return True
        if any(step_condition(step) is not None for step in steps):
            return True

        if self._has_non_trivial_dependencies(steps, default):
            return True
        dag_definition = getattr(workflow, "dag_definition", None)
        if (
            isinstance(dag_definition, dict)
            and dag_definition.get("edges")
            and not any("depends_on" in step for step in steps)
        ):
            return True
        return False

    @staticmethod
    def _has_non_trivial_dependencies(
        steps: list[dict[str, Any]],
        default_executor: str,
    ) -> bool:
        """顺序链沿用原执行器，分支/乱序依赖交给 DAG 调度。"""

        try:
            nodes = normalize_execution_plan(
                {"steps": steps},
                default_executor=default_executor,
            )
        except ExecutionPlanError:
            # 计划本身有问题时交给混合执行器给出确定性的失败结果。
            return True

        previous: list[int] = []
        for node in nodes:
            if "depends_on" not in node.raw:
                # 没有声明依赖时沿用“按列表顺序执行”的旧语义。
                previous = [node.step_id]
                continue
            if node.depends_on != previous:
                return True
            previous = [node.step_id]
        return False

    async def _persist_result(
        self,
        execution_id: int,
        result: ExecutionResult,
        workflow: Workflow | None = None,
    ) -> bool:
        repaired_plan = self._repaired_plan(result)
        human_handoff = True
        if isinstance(
            getattr(workflow, "execution_policy", None),
            dict,
        ):
            human_handoff = bool(
                workflow.execution_policy.get("human_handoff", True)
            )
        awaiting_user = (
            human_handoff
            and self._is_awaiting_user(result)
            and (
                self._auth_pause_enabled()
                or self._has_explicit_human_action(result)
            )
        )
        failure_checkpoint = None
        async with self._state_lock:
            async with self.session_factory() as session:
                execution_log = await session.get(
                    ExecutionLog,
                    execution_id,
                )
                if execution_log is None:
                    return False
                if execution_log.status in {"paused", "cancelled"}:
                    return False

                step_logs = list(result.step_logs or [])
                previous_checkpoint = find_checkpoint(
                    execution_log.step_logs
                )
                if (
                    not awaiting_user
                    and not step_logs
                    and self._uses_step_checkpoints(workflow)
                    and previous_checkpoint is not None
                ):
                    # 续跑没有产生新日志（例如 Agent 不在线）时，
                    # 保留原检查点，避免丢失断点。
                    step_logs = strip_checkpoints(
                        execution_log.step_logs
                    )
                    if not result.success:
                        step_logs.append(previous_checkpoint)
                if awaiting_user:
                    spec = build_human_task_spec(
                        result,
                        execution_id=execution_id,
                        workflow_version_id=(
                            execution_log.workflow_version_id
                        ),
                        browser_profile_id=(
                            execution_log.browser_profile_id
                        ),
                        login_state=str(
                            (
                                getattr(result, "details", None)
                                or {}
                            ).get("login_state")
                            or "login_required"
                        ),
                    )
                    task = None
                    verification = (
                        spec.get("payload", {}).get("verification")
                        if isinstance(spec.get("payload"), dict)
                        else None
                    )
                    if isinstance(verification, dict) and verification:
                        task = await self.human_tasks.create_in_session(
                            session,
                            execution_id,
                            **spec,
                        )
                    next_index = self._checkpoint_index(
                        self._build_checkpoint(result)
                    )
                    checkpoint = CheckpointService().build(
                        execution_log,
                        result,
                        next_step_index=next_index,
                        next_step_id=(
                            task.step_id
                            if task is not None
                            else spec.get("step_id")
                        ),
                        human_task_id=task.id if task is not None else None,
                        verification=(
                            dict(verification)
                            if isinstance(verification, dict)
                            else {}
                        ),
                    )
                    execution_log.status = "awaiting_user"
                    execution_log.error_message = (
                        task.instruction
                        if task is not None
                        else (
                            result.error
                            or "等待人工处理，然后从检查点继续。"
                        )
                    )
                    execution_log.finished_at = datetime.utcnow()
                    execution_log.human_task_id = (
                        task.id if task is not None else None
                    )
                    if task is None:
                        execution_log.metrics = _with_intervention(
                            execution_log.metrics
                        )
                        append_execution_event(
                            session,
                            execution_id,
                            "execution.awaiting_user",
                            {
                                "human_task_id": None,
                                "kind": spec.get("kind"),
                                "legacy": True,
                            },
                        )
                    step_logs = [
                        *strip_checkpoints(step_logs),
                        checkpoint,
                    ]
                else:
                    execution_log.status = (
                        "success" if result.success else "failed"
                    )
                    execution_log.error_message = result.error
                    if (
                        not result.success
                        and self._uses_step_checkpoints(workflow)
                        and isinstance(
                            (
                                getattr(result, "details", None)
                                or {}
                            ).get("failed_step"),
                            dict,
                        )
                    ):
                        failure_checkpoint = CheckpointService().build(
                            execution_log,
                            result,
                            next_step_index=self._checkpoint_index(
                                self._build_checkpoint(result)
                            ),
                            next_step_id=(
                                getattr(result, "details", {})
                                .get("failed_step", {})
                                .get("step_id")
                            ),
                        )
                        step_logs.append(failure_checkpoint)
                execution_log.screenshots = list(
                    result.screenshots or []
                )
                execution_log.step_logs = step_logs
                execution_log.artifacts = (
                    await deliver_execution_artifacts(
                        session,
                        workflow,
                        getattr(result, "artifacts", None),
                        getattr(self.config, "WEB_EXPORT_DIRS", ""),
                    )
                )
                if repaired_plan is not None and workflow is not None:
                    stored_workflow = await session.get(
                        Workflow,
                        workflow.id,
                    )
                    if stored_workflow is not None:
                        stored_workflow.structured_plan = repaired_plan
                execution_log.finished_at = datetime.utcnow()
                metrics = _execution_metrics(
                    execution_log.metrics,
                    step_logs=step_logs,
                    status=execution_log.status,
                    error=execution_log.error_message,
                    started_at=execution_log.started_at,
                    finished_at=execution_log.finished_at,
                )
                details = getattr(result, "details", None)
                extracted = (
                    details.get("extracted")
                    if isinstance(details, dict)
                    else None
                )
                if isinstance(extracted, dict):
                    metrics["variables"] = redact_payload(extracted)
                execution_log.metrics = metrics
                await session.commit()
                return True

    @staticmethod
    def _repaired_plan(
        result: ExecutionResult,
    ) -> dict[str, Any] | None:
        details = getattr(result, "details", None)
        if not isinstance(details, dict):
            return None
        candidate = details.get("repaired_plan")
        return candidate if isinstance(candidate, dict) else None

    def _auth_pause_enabled(self) -> bool:
        config = self.config
        if config is None:
            try:
                config = get_settings()
            except Exception:
                return False
        return bool(getattr(config, "AUTH_REQUIRED_PAUSE", False))

    def _legacy_code_enabled(self) -> bool:
        config = self.config
        if config is None:
            try:
                config = get_settings()
            except Exception:
                return True
        return runtime_flags.legacy_code_execution_enabled(config)

    @staticmethod
    def _is_awaiting_user(result: ExecutionResult) -> bool:
        details = getattr(result, "details", None)
        return (
            isinstance(details, dict)
            and bool(details.get("awaiting_user"))
        )

    @staticmethod
    def _has_explicit_human_action(result: ExecutionResult) -> bool:
        details = getattr(result, "details", None)
        if not isinstance(details, dict):
            return False
        if isinstance(details.get("human_action"), dict) and details.get(
            "human_action"
        ):
            return True
        failed_step = details.get("failed_step")
        return (
            isinstance(failed_step, dict)
            and isinstance(failed_step.get("human_action"), dict)
            and bool(failed_step.get("human_action"))
        )

    @staticmethod
    def _build_checkpoint(result: ExecutionResult) -> dict[str, Any]:
        details = getattr(result, "details", None)
        if not isinstance(details, dict):
            details = {}
        failed_step = details.get("failed_step")
        next_step_index = 0
        if isinstance(failed_step, dict):
            try:
                next_step_index = max(
                    int(failed_step.get("step_index") or 0),
                    0,
                )
            except (TypeError, ValueError):
                next_step_index = 0
        return build_checkpoint(
            next_step_index=next_step_index,
            failed_step=(
                failed_step if isinstance(failed_step, dict) else None
            ),
            completed_idempotency_keys=details.get(
                "completed_idempotency_keys"
            ),
        )

    @staticmethod
    def _checkpoint_index(checkpoint: dict[str, Any]) -> int:
        try:
            return max(
                int(checkpoint.get("next_step_index") or 0),
                0,
            )
        except (TypeError, ValueError):
            return 0

    def _resume_state_from_checkpoint(
        self,
        checkpoint: dict[str, Any],
        step_logs: list[Any] | None,
    ) -> dict[str, Any]:
        return checkpoint_resume_context(checkpoint, step_logs)

    def _structured_failure_checkpoint(
        self,
        workflow: Workflow | None,
        result: ExecutionResult,
    ) -> dict[str, Any] | None:
        if not self._uses_step_checkpoints(workflow):
            return None
        details = getattr(result, "details", None)
        if not isinstance(details, dict):
            return None
        if not isinstance(details.get("failed_step"), dict):
            return None
        return self._build_checkpoint(result)

    @staticmethod
    def _is_desktop_workflow(workflow: Any) -> bool:
        return (
            getattr(workflow, "executor_type", None) or "web"
        ) == "windows"

    @classmethod
    def _structured_steps(cls, workflow: Any) -> list[dict[str, Any]]:
        plan = getattr(workflow, "structured_plan", None)
        if not isinstance(plan, dict):
            return []
        steps = plan.get("steps")
        if not isinstance(steps, list):
            return []
        return [dict(step) for step in steps if isinstance(step, dict)]

    @classmethod
    def _is_structured_workflow(cls, workflow: Any) -> bool:
        return bool(cls._structured_steps(workflow))

    @classmethod
    def _uses_step_checkpoints(cls, workflow: Any) -> bool:
        """桌面流程与结构化流程共用检查点续跑语义。"""

        return cls._is_desktop_workflow(workflow) or (
            cls._is_structured_workflow(workflow)
        )

    @staticmethod
    def _checkpoint_from_step_logs(
        step_logs: list[Any] | None,
    ) -> dict[str, Any] | None:
        indexes: list[int] = []
        keys: list[str] = []
        for entry in step_logs or []:
            if not isinstance(entry, dict) or is_checkpoint(entry):
                continue
            if str(entry.get("status") or "") != "success":
                continue
            try:
                index = int(entry.get("step_index"))
            except (TypeError, ValueError):
                continue
            if index >= 0:
                indexes.append(index)
            key = entry.get("idempotency_key")
            if isinstance(key, str) and key.strip():
                text = key.strip()
                if text not in keys:
                    keys.append(text)
        if not indexes:
            return None
        return build_checkpoint(
            next_step_index=max(indexes) + 1,
            completed_idempotency_keys=keys,
        )

    async def _write_pause_checkpoint(self, execution_id: int) -> None:
        async with self._state_lock:
            async with self.session_factory() as session:
                execution_log = await session.get(
                    ExecutionLog,
                    execution_id,
                )
                if execution_log is None:
                    return
                if execution_log.status != "paused":
                    return
                workflow = await session.get(
                    Workflow,
                    execution_log.workflow_id,
                )
                if not self._uses_step_checkpoints(workflow):
                    return
                existing = find_checkpoint(execution_log.step_logs)
                checkpoint = self._checkpoint_from_step_logs(
                    execution_log.step_logs
                )
                if checkpoint is None:
                    return
                progress = CheckpointService().build(
                    execution_log,
                    SimpleNamespace(
                        step_logs=execution_log.step_logs,
                        details={},
                        artifacts=execution_log.artifacts or [],
                    ),
                    next_step_index=self._checkpoint_index(checkpoint),
                )
                if existing is not None:
                    checkpoint = CheckpointService().merge_pause(
                        existing,
                        progress,
                    )
                else:
                    checkpoint = progress
                execution_log.step_logs = [
                    *strip_checkpoints(execution_log.step_logs),
                    checkpoint,
                ]
                await session.commit()

    async def _persist_failure(
        self,
        execution_id: int,
        error: str,
    ) -> None:
        async with self._state_lock:
            async with self.session_factory() as session:
                execution_log = await session.get(
                    ExecutionLog,
                    execution_id,
                )
                if execution_log is None:
                    return
                if execution_log.status in {"paused", "cancelled"}:
                    return

                execution_log.status = "failed"
                execution_log.error_message = error
                execution_log.finished_at = datetime.utcnow()
                execution_log.metrics = _execution_metrics(
                    execution_log.metrics,
                    step_logs=execution_log.step_logs,
                    status="failed",
                    error=error,
                    started_at=execution_log.started_at,
                    finished_at=execution_log.finished_at,
                )
                await session.commit()

    async def _notify(
        self,
        execution_id: int,
        workflow: Workflow,
    ) -> None:
        if self.notifier is None:
            return

        notify = getattr(self.notifier, "notify", None)
        if not callable(notify):
            return

        async with self.session_factory() as session:
            execution_log = await session.get(ExecutionLog, execution_id)
        if execution_log is None:
            return

        try:
            result = notify(execution_log, workflow)
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.exception(
                "Failed to notify execution %s for workflow %s",
                execution_id,
                workflow.id,
            )


def _as_metrics(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _with_intervention(value: Any) -> dict[str, Any]:
    metrics = _as_metrics(value)
    current = metrics.get("manual_interventions")
    try:
        count = max(int(current or 0), 0)
    except (TypeError, ValueError):
        count = 0
    metrics["manual_interventions"] = count + 1
    return metrics


def _mark_resumed(value: Any) -> dict[str, Any]:
    metrics = _as_metrics(value)
    metrics["resumed"] = True
    return metrics


def _execution_metrics(
    value: Any,
    *,
    step_logs: Any,
    status: str,
    error: str | None,
    started_at: datetime | None,
    finished_at: datetime | None,
) -> dict[str, Any]:
    """把执行结果固化为指标 JSON，便于阶段 5 聚合查询。"""

    metrics = _as_metrics(value)
    summary = summarize_steps(step_logs)
    metrics.update(
        {
            "step_count": summary["step_count"],
            "failed_steps": summary["failed_steps"],
            "locator_failures": summary["locator_failures"],
            "repairs_attempted": summary["repairs_attempted"],
            "repairs_succeeded": summary["repairs_succeeded"],
            "failure_categories": summary["failure_categories"],
        }
    )
    if status in {"failed", "error"} or summary["failed_steps"]:
        metrics["failure_category"] = _top_category(
            summary["failure_categories"],
            error,
        )
    if started_at is not None and finished_at is not None:
        metrics["duration_seconds"] = round(
            max((finished_at - started_at).total_seconds(), 0.0),
            3,
        )
    return metrics


def _top_category(
    categories: dict[str, Any],
    error: str | None,
) -> str:
    best: str | None = None
    best_count = -1
    for name, count in (categories or {}).items():
        try:
            numeric = int(count)
        except (TypeError, ValueError):
            continue
        if numeric > best_count:
            best = str(name)
            best_count = numeric
    if best is None:
        best = classify_failure(error).value
    return best


__all__ = [
    "ExecutionManager",
    "ExecutionStateError",
    "recover_interrupted_executions",
]
