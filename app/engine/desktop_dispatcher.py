from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.host_capabilities import (
    environment_or_unknown,
    normalize_capabilities,
    normalize_required_capabilities,
)
from app.config import get_settings
from app.database import AsyncSessionLocal
from app.engine.host_router import (
    HostDecision,
    HostInfo,
    HostRouter,
    host_step_for_plan,
)
from app.engine.runner import ExecutionResult
from app.models.agent import DesktopAgent
from app.models.desktop_task import DesktopTask
from app.models.execution import ExecutionLog
from app.models.workflow import Workflow
from app.models.workspace import Workspace
from app.security.workspaces import workspace_quota
from app.services.hosts import active_task_counts, session_fingerprint

logger = logging.getLogger(__name__)

INSPECT_TIMEOUT_SECONDS = 10.0

_ACTIVE_TASK_STATUSES = {"pending", "dispatched", "running"}
_TERMINAL_TASK_STATUSES = {
    "success",
    "failed",
    "error",
    "completed",
    "cancelled",
}


def _normalize_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_keys(values: Any) -> list[str]:
    if not isinstance(values, (list, tuple, set)):
        return []
    normalized: list[str] = []
    for value in values:
        text = _normalize_text(value)
        if text and text not in normalized:
            normalized.append(text)
    return normalized


class DesktopDispatcher:
    """Dispatch structured desktop plans to connected Agents."""

    def __init__(
        self,
        connection_manager: Any,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        visual_runtime: Any | None = None,
        host_router: HostRouter | None = None,
        host_events: Any | None = None,
    ) -> None:
        self.connection_manager = connection_manager
        self.session_factory = session_factory
        self.visual_runtime = visual_runtime
        self.host_router = host_router or HostRouter()
        self.host_events = host_events
        self.timeout = float(get_settings().EXECUTION_TIMEOUT)
        self._task_completion_events: dict[int, asyncio.Event] = {}
        # Keep the old private name as an alias for callers from earlier
        # dispatcher revisions.
        self._task_events = self._task_completion_events
        self._task_results: dict[int, dict[str, Any]] = {}
        self._task_agents: dict[int, int] = {}
        self._inspect_events: dict[str, asyncio.Event] = {}
        self._inspect_results: dict[str, dict[str, Any]] = {}
        self._pick_events: dict[str, asyncio.Event] = {}
        self._pick_results: dict[str, dict[str, Any]] = {}
        self._selection_lock = asyncio.Lock()

    async def inspect(
        self,
        request: dict[str, Any] | None = None,
        *,
        agent_id: int | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """请求 Agent 回报可见窗口与目标窗口的控件摘要。"""

        payload = dict(request) if isinstance(request, dict) else {}
        target_agent = agent_id
        if target_agent is None:
            online_ids = self._online_agent_ids()
            target_agent = online_ids[0] if online_ids else None
        if target_agent is None:
            return self._inspect_failure("No available desktop agent")

        request_id = uuid4().hex
        event = asyncio.Event()
        self._inspect_events[request_id] = event
        wait_timeout = (
            float(timeout)
            if isinstance(timeout, (int, float))
            else INSPECT_TIMEOUT_SECONDS
        )
        wait_timeout = max(wait_timeout, 0.01)

        try:
            await self.connection_manager.send_task(
                int(target_agent),
                {
                    "type": "desktop.inspect",
                    "request_id": request_id,
                    **payload,
                },
            )
            await asyncio.wait_for(event.wait(), timeout=wait_timeout)
        except asyncio.TimeoutError:
            return self._inspect_failure(
                f"Desktop inspect timed out after {wait_timeout:g} seconds"
            )
        except Exception as exc:
            return self._inspect_failure(
                str(exc) or exc.__class__.__name__
            )
        finally:
            self._inspect_events.pop(request_id, None)

        result = self._inspect_results.pop(request_id, None)
        if not isinstance(result, dict):
            return self._inspect_failure(
                "Desktop agent returned no inspect result"
            )
        return result

    def handle_inspect_result(self, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            return
        request_id = str(payload.get("request_id") or "")
        if not request_id:
            return
        event = self._inspect_events.get(request_id)
        if event is None:
            return
        self._inspect_results[request_id] = dict(payload)
        event.set()

    async def pick(
        self,
        request: dict[str, Any] | None = None,
        *,
        agent_id: int | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """请求 Agent 回报鼠标指针下方的控件，用于录制。"""

        payload = dict(request) if isinstance(request, dict) else {}
        target_agent = agent_id
        if target_agent is None:
            online_ids = self._online_agent_ids()
            target_agent = online_ids[0] if online_ids else None
        if target_agent is None:
            return self._pick_failure("No available desktop agent")

        request_id = uuid4().hex
        event = asyncio.Event()
        self._pick_events[request_id] = event
        wait_timeout = (
            float(timeout)
            if isinstance(timeout, (int, float))
            else INSPECT_TIMEOUT_SECONDS
        )
        wait_timeout = max(wait_timeout, 0.01)

        try:
            await self.connection_manager.send_task(
                int(target_agent),
                {
                    "type": "desktop.pick",
                    "request_id": request_id,
                    **payload,
                },
            )
            await asyncio.wait_for(event.wait(), timeout=wait_timeout)
        except asyncio.TimeoutError:
            return self._pick_failure(
                f"Desktop pick timed out after {wait_timeout:g} seconds"
            )
        except Exception as exc:
            return self._pick_failure(
                str(exc) or exc.__class__.__name__
            )
        finally:
            self._pick_events.pop(request_id, None)

        result = self._pick_results.pop(request_id, None)
        if not isinstance(result, dict):
            return self._pick_failure(
                "Desktop agent returned no pick result"
            )
        return result

    def handle_pick_result(self, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            return
        request_id = str(payload.get("request_id") or "")
        if not request_id:
            return
        event = self._pick_events.get(request_id)
        if event is None:
            return
        self._pick_results[request_id] = dict(payload)
        event.set()

    def _online_agent_ids(self) -> list[int]:
        getter = getattr(
            self.connection_manager,
            "get_online_agents",
            None,
        )
        if not callable(getter):
            return []
        try:
            values = list(getter())
        except Exception:
            return []

        agent_ids: list[int] = []
        for value in values:
            try:
                agent_ids.append(int(value))
            except (TypeError, ValueError):
                continue
        return agent_ids

    @staticmethod
    def _inspect_failure(error: str) -> dict[str, Any]:
        return {
            "success": False,
            "windows": [],
            "target": None,
            "elements": [],
            "error": error,
        }

    @staticmethod
    def _pick_failure(error: str) -> dict[str, Any]:
        return {
            "success": False,
            "element": None,
            "window": None,
            "point": None,
            "error": error,
        }

    async def dispatch(
        self,
        workflow: Workflow,
        context: dict[str, Any] | None = None,
    ) -> ExecutionResult:
        started_at = time.perf_counter()
        quota_error = await self._workspace_quota_error(workflow)
        if quota_error is not None:
            return self._failure_result(quota_error, started_at)
        structured_plan = getattr(workflow, "structured_plan", None) or {}
        actions = self._convert_plan_to_actions(
            structured_plan,
            context,
        )
        existing_execution_log_id = (
            context.get("execution_log_id")
            if isinstance(context, dict)
            else None
        )
        task_id, execution_log_id = await self._create_task_records(
            workflow,
            actions,
            execution_log_id=(
                int(existing_execution_log_id)
                if existing_execution_log_id is not None
                else None
            ),
        )
        event = self._register_task_event(task_id)

        requested_agent_id = (
            context.get("desktop_agent_id")
            if isinstance(context, dict)
            else None
        )
        bound_host_id = (
            context.get("host_id")
            if isinstance(context, dict)
            else None
        )
        if bound_host_id is None:
            bound_host_id = requested_agent_id
        bound_affinity_hash = (
            context.get("host_affinity_hash")
            if isinstance(context, dict)
            else None
        )
        async with self._selection_lock:
            host_decision = await self._select_host(
                workflow,
                context=context,
                bound_host_id=(
                    int(bound_host_id)
                    if bound_host_id is not None
                    else None
                ),
                bound_session_fingerprint=(
                    str(bound_affinity_hash)
                    if bound_affinity_hash
                    else None
                ),
            )
            if (
                host_decision.status != "agent"
                or host_decision.host_id is None
            ):
                error = self._host_error_message(host_decision)
                self._clear_task_event(task_id)
                await self._mark_task_failed(
                    task_id,
                    execution_log_id,
                    error,
                    details=self._host_decision_details(
                        host_decision
                    ),
                )
                return self._failure_result(
                    error,
                    started_at,
                    details=self._host_decision_details(
                        host_decision
                    ),
                )

            agent_id = int(host_decision.host_id)
            await self._mark_task_dispatched(
                task_id,
                agent_id,
                host_decision,
            )
        self._task_agents[task_id] = agent_id
        timeout = self._resolve_timeout(workflow, context)
        payload_timeout = int(timeout) if float(timeout).is_integer() else timeout
        task_payload = {
            "type": "desktop.task",
            "task_id": task_id,
            "execution_log_id": execution_log_id,
            "workflow_id": getattr(workflow, "id", None),
            "timeout_seconds": payload_timeout,
            "step_offset": self._step_offset(context),
            "resume_from_step": self._resume_from_step(context),
            "completed_idempotency_keys": (
                self._completed_idempotency_keys(context)
            ),
            "actions": actions,
        }

        try:
            await self.connection_manager.send_task(agent_id, task_payload)
        except Exception as exc:
            logger.exception(
                "Failed to dispatch desktop task %s",
                task_id,
            )
            error = str(exc) or exc.__class__.__name__
            self._clear_task_event(task_id)
            await self._mark_task_failed(
                task_id,
                execution_log_id,
                error,
            )
            return self._failure_result(error, started_at)

        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except asyncio.CancelledError:
            await self._send_cancel(agent_id, task_id)
            await self._mark_task_cancelled(
                task_id,
                "Task paused",
            )
            self._clear_task_event(task_id)
            raise
        except asyncio.TimeoutError:
            error = f"Desktop task timed out after {timeout:g} seconds"
            await self._send_cancel(agent_id, task_id)
            await self._mark_task_failed(
                task_id,
                execution_log_id,
                error,
            )
            self._clear_task_event(task_id)
            return self._failure_result(error, started_at)

        result = self._take_task_result(task_id)
        self._clear_task_event(task_id)
        if result is None:
            error = "Desktop agent returned no result"
            await self._mark_task_failed(
                task_id,
                execution_log_id,
                error,
            )
            return self._failure_result(error, started_at)

        await self._persist_task_result(
            task_id,
            execution_log_id,
            result,
        )
        parsed = self._result_from_payload(result, started_at)
        parsed.details = {
            **(
                dict(parsed.details)
                if isinstance(parsed.details, dict)
                else {}
            ),
            **self._host_decision_details(host_decision),
        }
        return await self._maybe_apply_visual_fallback(
            workflow,
            parsed,
            context,
            agent_id=agent_id,
        )

    async def cancel(self, task_id: int) -> None:
        async with self._get_session_factory()() as db:
            task = await db.get(DesktopTask, task_id)
            if task is None or task.status in _TERMINAL_TASK_STATUSES:
                return

            agent_id = task.agent_id or self._task_agents.get(task_id)
            now = datetime.utcnow()
            task.status = "cancelled"
            task.error_message = "Task cancelled"
            task.finished_at = now
            task.result = {
                "success": False,
                "output": "",
                "error": "Task cancelled",
                "screenshots": [],
                "step_logs": [],
                "duration_seconds": 0.0,
            }

            execution_log = None
            if task.execution_log_id is not None:
                execution_log = await db.get(
                    ExecutionLog,
                    task.execution_log_id,
                )
            if execution_log is not None:
                execution_log.status = "cancelled"
                execution_log.error_message = "Task cancelled"
                execution_log.finished_at = now
            await db.commit()

        if agent_id is not None:
            try:
                await self.connection_manager.send_task(
                    agent_id,
                    {
                        "type": "desktop.cancel",
                        "task_id": task_id,
                    },
                )
            except Exception:
                logger.debug(
                    "Could not send cancellation for task %s",
                    task_id,
                    exc_info=True,
                )

        self.complete_task(
            task_id,
            {
                "success": False,
                "output": "",
                "error": "Task cancelled",
                "screenshots": [],
                "step_logs": [],
                "duration_seconds": 0.0,
            },
        )

    def complete_task(
        self,
        task_id: int,
        result: dict[str, Any],
    ) -> None:
        self._on_task_completed(task_id, result)

    def _on_task_completed(
        self,
        task_id: int,
        result: dict[str, Any],
    ) -> None:
        payload = dict(result)
        self._task_results[task_id] = payload
        event = self._task_completion_events.get(task_id)
        if event is not None:
            event.set()

        manager_complete = getattr(
            self.connection_manager,
            "complete_task",
            None,
        )
        if callable(manager_complete):
            manager_complete(task_id, payload)

    async def handle_agent_event(
        self,
        event: dict[str, Any],
        db: AsyncSession | None = None,
        *,
        notify_completion: bool = True,
    ) -> None:
        if not isinstance(event, dict):
            return
        task_id = self._task_id(event)
        if task_id is None:
            return

        if db is None:
            async with self._get_session_factory()() as session:
                await self._apply_agent_event(session, event, task_id)
        else:
            await self._apply_agent_event(db, event, task_id)

        event_type = str(event.get("type", ""))
        if notify_completion and event_type in {"desktop.completed", "desktop.failed"}:
            self._on_task_completed(
                task_id,
                self._result_from_event(event),
            )

    async def handle_agent_disconnect(self, agent_id: int) -> None:
        failed_task_ids: list[int] = []
        async with self._get_session_factory()() as db:
            result = await db.execute(
                select(DesktopTask).where(
                    DesktopTask.agent_id == agent_id,
                    DesktopTask.status.in_(_ACTIVE_TASK_STATUSES),
                )
            )
            tasks = list(result.scalars().all())
            now = datetime.utcnow()
            for task in tasks:
                task.status = "failed"
                task.error_message = "Agent disconnected"
                task.finished_at = now
                task.result = {
                    "success": False,
                    "output": "",
                    "error": "Agent disconnected",
                    "screenshots": [],
                    "step_logs": [],
                    "duration_seconds": 0.0,
                }
                failed_task_ids.append(task.id)

                if task.execution_log_id is None:
                    continue
                execution_log = await db.get(
                    ExecutionLog,
                    task.execution_log_id,
                )
                if execution_log is not None:
                    execution_log.status = "failed"
                    execution_log.error_message = "Agent disconnected"
                    execution_log.finished_at = now
            await db.commit()

        for task_id in failed_task_ids:
            self.complete_task(
                task_id,
                {
                    "success": False,
                    "output": "",
                    "error": "Agent disconnected",
                    "screenshots": [],
                    "step_logs": [],
                    "duration_seconds": 0.0,
                },
            )

    def _convert_plan_to_actions(
        self,
        structured_plan: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if not isinstance(structured_plan, dict):
            return []

        steps = structured_plan.get("steps", [])
        if not isinstance(steps, (list, tuple)):
            return []

        actions: list[dict[str, Any]] = []
        visual_selection = (
            context.get("visual_selection")
            if isinstance(context, dict)
            else None
        )
        for step in steps:
            if not isinstance(step, dict):
                continue
            action_type = step.get("action", step.get("type"))
            if action_type is None:
                continue
            params = step.get("params")
            action: dict[str, Any] = {
                "type": str(action_type),
                "params": dict(params) if isinstance(params, dict) else {},
            }
            step_id = step.get("step_id")
            if (
                isinstance(visual_selection, dict)
                and str(visual_selection.get("step_id"))
                == str(step_id)
                and isinstance(visual_selection.get("bbox"), list)
                and len(visual_selection["bbox"]) == 4
            ):
                x1, y1, x2, y2 = (
                    int(item) for item in visual_selection["bbox"]
                )
                window_info = (
                    visual_selection.get("window_info")
                    if isinstance(
                        visual_selection.get("window_info"),
                        dict,
                    )
                    else {}
                )
                if any(
                    window_info.get(key)
                    for key in ("handle", "title", "class_name", "pid")
                ):
                    actions.append(
                        {
                            "type": "activate_window",
                            "params": {
                                key: window_info[key]
                                for key in (
                                    "handle",
                                    "title",
                                    "class_name",
                                    "pid",
                                )
                                if key in window_info
                            },
                        }
                    )
                action = {
                    "type": "click_relative",
                    "params": {
                        "x": (x1 + x2) / 2,
                        "y": (y1 + y2) / 2,
                    },
                    "visual_candidate_id": visual_selection.get(
                        "candidate_id"
                    ),
                }
            if step_id is not None:
                action["step_id"] = step_id
            idempotency_key = _normalize_text(
                step.get("idempotency_key")
            )
            if idempotency_key is not None:
                action["idempotency_key"] = idempotency_key
            actions.append(action)
        return actions

    async def _create_task_records(
        self,
        workflow: Workflow,
        actions: list[dict[str, Any]],
        *,
        execution_log_id: int | None = None,
    ) -> tuple[int, int]:
        async with self._get_session_factory()() as db:
            execution_log = (
                await db.get(ExecutionLog, execution_log_id)
                if execution_log_id is not None
                else None
            )
            if execution_log is None:
                execution_log = ExecutionLog(
                    workflow_id=workflow.id,
                    status="running",
                )
                db.add(execution_log)
                await db.flush()

            task = DesktopTask(
                workflow_id=workflow.id,
                execution_log_id=execution_log.id,
                status="pending",
                actions=actions,
            )
            db.add(task)
            await db.commit()
            await db.refresh(task)
            return task.id, execution_log.id

    async def _select_agent(
        self,
        workflow: Workflow,
        *,
        preferred_agent_id: int | None = None,
    ) -> int | None:
        decision = await self._select_host(
            workflow,
            context=None,
            bound_host_id=preferred_agent_id,
            bound_session_fingerprint=None,
        )
        return (
            int(decision.host_id)
            if decision.status == "agent"
            and decision.host_id is not None
            else None
        )

    async def _select_host(
        self,
        workflow: Workflow,
        *,
        context: dict[str, Any] | None,
        bound_host_id: int | None,
        bound_session_fingerprint: str | None,
    ) -> HostDecision:
        online_ids = [
            int(agent_id) for agent_id in self.connection_manager.get_online_agents()
        ]
        required_values = set(
            self._required_capabilities(workflow)
        )
        plan = getattr(workflow, "structured_plan", None)
        if isinstance(plan, dict):
            required_values.update(
                str(item)
                for item in (
                    plan.get("required_capabilities") or []
                )
                if str(item).strip()
            )
            for step in plan.get("steps") or []:
                if not isinstance(step, dict):
                    continue
                required_values.update(
                    str(item)
                    for item in (
                        step.get("required_capabilities") or []
                    )
                    if str(item).strip()
                )
        required = sorted(
            normalize_required_capabilities(required_values)
        )
        workspace_id = getattr(workflow, "workspace_id", None)
        query_ids = list(online_ids)
        if bound_host_id is not None and int(bound_host_id) not in query_ids:
            query_ids.append(int(bound_host_id))
        if not query_ids:
            return self.host_router.select(
                self._workflow_host_step(workflow),
                policy=self._execution_policy(workflow, context),
                hosts=[],
                bound_host_id=bound_host_id,
                bound_session_fingerprint=bound_session_fingerprint,
                required_capabilities=required,
            )
        async with self._get_session_factory()() as db:
            statement = select(DesktopAgent).where(
                DesktopAgent.id.in_(query_ids)
            )
            user_id = getattr(workflow, "user_id", None)
            if user_id is not None:
                statement = statement.where(DesktopAgent.user_id == user_id)
            if workspace_id is not None:
                statement = statement.where(
                    DesktopAgent.workspace_id == workspace_id
                )
            result = await db.execute(statement)
            agents = {agent.id: agent for agent in result.scalars().all()}
            counts = await active_task_counts(db, list(agents))

        online_set = set(online_ids)
        hosts: list[HostInfo] = []
        for agent in agents.values():
            session_getter = getattr(
                self.connection_manager,
                "get_session_id",
                None,
            )
            session_id = (
                session_getter(int(agent.id))
                if callable(session_getter)
                else None
            )
            hosts.append(
                HostInfo(
                    host_id=int(agent.id),
                    name=agent.name,
                    environment=environment_or_unknown(
                        agent.environment
                    ),
                    capabilities=normalize_capabilities(
                        agent.capabilities
                    ),
                    status=(
                        "online"
                        if int(agent.id) in online_set
                        else "offline"
                    ),
                    active_task_count=counts.get(int(agent.id), 0),
                    session_fingerprint=(
                        session_fingerprint(str(session_id))
                        if session_id
                        else None
                    ),
                )
            )
        return self.host_router.select(
            self._workflow_host_step(workflow),
            policy=self._execution_policy(workflow, context),
            hosts=hosts,
            bound_host_id=bound_host_id,
            bound_session_fingerprint=bound_session_fingerprint,
            required_capabilities=required,
        )

    @staticmethod
    def _execution_policy(
        workflow: Workflow,
        context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if isinstance(context, dict):
            policy = context.get("execution_policy")
            if isinstance(policy, dict):
                return dict(policy)
        policy = getattr(workflow, "execution_policy", None)
        return dict(policy) if isinstance(policy, dict) else {}

    @staticmethod
    def _workflow_host_step(workflow: Workflow) -> dict[str, Any]:
        plan = getattr(workflow, "structured_plan", None)
        return host_step_for_plan(
            plan,
            default_executor=(
                getattr(workflow, "executor_type", None)
                or "windows"
            ),
        )

    async def _maybe_apply_visual_fallback(
        self,
        workflow: Workflow,
        result: ExecutionResult,
        context: dict[str, Any] | None,
        *,
        agent_id: int,
    ) -> ExecutionResult:
        if self.visual_runtime is None or result.success:
            return result
        if (
            isinstance(context, dict)
            and isinstance(context.get("visual_selection"), dict)
        ):
            return result
        details = (
            dict(result.details)
            if isinstance(result.details, dict)
            else {}
        )
        failed_step = details.get("failed_step")
        if not isinstance(failed_step, dict):
            return result
        step = self._find_plan_step(
            workflow,
            failed_step.get("step_id"),
        )
        if step is None:
            return result
        screenshot_path = str(
            failed_step.get("screenshot_path")
            or next(
                iter(result.screenshots or []),
                "",
            )
        ).strip()
        if not screenshot_path:
            return result
        try:
            from PIL import Image

            file_path = Path(screenshot_path)
            screenshot = file_path.read_bytes()
            with Image.open(file_path) as image:
                width, height = image.size
        except Exception:
            logger.debug(
                "Desktop visual screenshot unavailable",
                exc_info=True,
            )
            return result
        window_info = (
            failed_step.get("window")
            if isinstance(failed_step.get("window"), dict)
            else {}
        )
        if not window_info and isinstance(
            failed_step.get("target"),
            dict,
        ):
            window_info = dict(failed_step["target"])
        if not any(
            window_info.get(key)
            for key in ("handle", "title", "class_name", "pid")
        ):
            return result
        visible_text = " ".join(
            [
                str(failed_step.get("message") or ""),
                str(failed_step.get("error") or ""),
                " ".join(
                    str(item.get("name") or "")
                    for item in failed_step.get(
                        "candidate_details",
                        [],
                    )
                    if isinstance(item, dict)
                ),
            ]
        )
        outcome = await self.visual_runtime.evaluate_desktop(
            SimpleNamespace(
                step_id=int(step.get("step_id") or 0),
                executor="windows",
                action=str(step.get("action") or ""),
                params=dict(step.get("params") or {}),
                visual_fallback=bool(
                    step.get("visual_fallback")
                    or (step.get("params") or {}).get(
                        "visual_fallback"
                    )
                ),
                postconditions=list(
                    step.get("postconditions") or []
                ),
            ),
            screenshot=screenshot,
            image_size=(width, height),
            window_info=window_info,
            visible_text=visible_text,
            uia_candidates=list(
                failed_step.get("candidate_details") or []
            ),
            failure_category=str(
                failed_step.get("failure_category") or ""
            ),
            context=dict(context or {}),
            screenshot_path=screenshot_path,
            execute_callback=self._visual_desktop_executor(
                workflow=workflow,
                agent_id=agent_id,
                failed_step_index=int(
                    failed_step.get("step_index") or 0
                ),
            ),
        )
        visual_screenshot_path = (
            outcome.payload.get("screenshot_path")
            or outcome.audit.get("screenshot_path")
        )
        if visual_screenshot_path:
            result.screenshots = [
                (
                    str(visual_screenshot_path)
                    if item == screenshot_path
                    else item
                )
                for item in (result.screenshots or [])
            ]
            failed_step["screenshot_path"] = str(
                visual_screenshot_path
            )
        if outcome.status == "success":
            result.success = True
            result.error = None
            details["visual_audit"] = dict(outcome.audit)
            if result.step_logs:
                result.step_logs[-1] = {
                    **dict(result.step_logs[-1]),
                    "status": "success",
                    "verification": dict(outcome.verification),
                    "visual_audit": dict(outcome.audit),
                }
        elif outcome.status == "awaiting_user":
            result.success = False
            result.error = outcome.reason
            visual_payload = dict(outcome.payload)
            visual_payload["window_info"] = dict(window_info)
            visual_payload["screenshot_path"] = (
                visual_screenshot_path or screenshot_path
            )
            details["awaiting_user"] = True
            details["failed_step"] = {
                **failed_step,
                "failure_category": "visual_ambiguous",
                "error": outcome.reason,
                "human_action": {
                    "kind": "visual_ambiguous",
                    "instruction": visual_payload.get(
                        "instruction",
                        "请选择正确的目标",
                    ),
                    "window_title": window_info.get("title"),
                    "process_name": window_info.get("process_name"),
                    "screenshot_path": (
                        visual_screenshot_path or screenshot_path
                    ),
                    "verification": {
                        "type": "visual_selection"
                    },
                    "visual": visual_payload,
                    **visual_payload,
                },
            }
            if result.step_logs:
                result.step_logs[-1] = {
                    **dict(result.step_logs[-1]),
                    "status": "awaiting_user",
                    "visual_audit": dict(outcome.audit),
                }
        result.details = details
        return result

    def _visual_desktop_executor(
        self,
        *,
        workflow: Workflow,
        agent_id: int,
        failed_step_index: int,
    ) -> Any:
        async def execute(candidate, step, context):
            del step
            replay_context = {
                **dict(context or {}),
                "desktop_agent_id": agent_id,
                "visual_selection": dict(candidate),
                "resume_from_step": failed_step_index,
                "step_offset": failed_step_index,
            }
            replay = await self.dispatch(workflow, replay_context)
            return bool(replay.success), {
                "passed": bool(replay.success),
                "type": "desktop_full_plan_replay",
                "error": replay.error,
            }

        return execute

    @staticmethod
    def _find_plan_step(
        workflow: Workflow,
        step_id: Any,
    ) -> dict[str, Any] | None:
        plan = getattr(workflow, "structured_plan", None)
        if not isinstance(plan, dict):
            return None
        for step in plan.get("steps") or []:
            if (
                isinstance(step, dict)
                and str(step.get("step_id")) == str(step_id)
            ):
                return dict(step)
        return None

    async def _workspace_quota_error(
        self,
        workflow: Workflow,
    ) -> str | None:
        workspace_id = getattr(workflow, "workspace_id", None)
        if workspace_id is None:
            return None
        async with self._get_session_factory()() as db:
            workspace = await db.get(Workspace, int(workspace_id))
            quota = workspace_quota(workspace)
            limit = int(
                quota.get("max_concurrent_agent_tasks", 0) or 0
            )
            if limit <= 0:
                return None
            active = await db.scalar(
                select(func.count(DesktopTask.id))
                .join(
                    DesktopAgent,
                    DesktopTask.agent_id == DesktopAgent.id,
                )
                .where(
                    DesktopAgent.workspace_id == int(workspace_id),
                    DesktopTask.status.in_(_ACTIVE_TASK_STATUSES),
                )
            )
        if int(active or 0) >= limit:
            return (
                "workspace 桌面任务配额已满："
                f"{active}/{limit} 个任务正在进行。"
            )
        return None

    async def _mark_task_dispatched(
        self,
        task_id: int,
        agent_id: int,
        host_decision: HostDecision | None = None,
    ) -> None:
        async with self._get_session_factory()() as db:
            task = await db.get(DesktopTask, task_id)
            if task is None:
                return
            if task.status in _TERMINAL_TASK_STATUSES:
                return
            task.agent_id = agent_id
            if task.status == "pending":
                task.status = "dispatched"
            if task.started_at is None:
                task.started_at = datetime.utcnow()
            if task.execution_log_id is not None:
                execution_log = await db.get(
                    ExecutionLog,
                    task.execution_log_id,
                )
                if execution_log is not None:
                    execution_log.host_id = agent_id
                    execution_log.host_environment = (
                        host_decision.environment
                        if host_decision is not None
                        else None
                    )
                    execution_log.host_bound = True
                    execution_log.host_affinity_hash = (
                        host_decision.session_fingerprint
                        if host_decision is not None
                        else None
                    )
                    execution_log.host_reason = (
                        host_decision.reason
                        if host_decision is not None
                        else None
                    )
            await db.commit()
        await self._publish_host_busy_state(agent_id)

    async def _send_cancel(self, agent_id: int, task_id: int) -> None:
        try:
            await self.connection_manager.send_task(
                agent_id,
                {
                    "type": "desktop.cancel",
                    "task_id": task_id,
                },
            )
        except Exception:
            logger.debug(
                "Could not send cancellation for timed out task %s",
                task_id,
                exc_info=True,
            )

    async def _mark_task_failed(
        self,
        task_id: int,
        execution_log_id: int,
        error: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        now = datetime.utcnow()
        async with self._get_session_factory()() as db:
            task = await db.get(DesktopTask, task_id)
            if task is not None:
                task.status = "failed"
                task.error_message = error
                task.finished_at = now
                task.result = {
                    "success": False,
                    "output": "",
                    "error": error,
                    "screenshots": [],
                    "step_logs": [],
                    "duration_seconds": 0.0,
                    "details": dict(details or {}),
                }

            execution_log = await db.get(ExecutionLog, execution_log_id)
            if execution_log is not None:
                execution_log.status = "failed"
                execution_log.error_message = error
                execution_log.finished_at = now
                execution_log.host_bound = bool(
                    execution_log.host_id is not None
                )
            await db.commit()
            if task is not None and task.agent_id is not None:
                await self._publish_host_busy_state(
                    int(task.agent_id)
                )

    async def _mark_task_cancelled(
        self,
        task_id: int,
        error: str,
    ) -> None:
        now = datetime.utcnow()
        async with self._get_session_factory()() as db:
            task = await db.get(DesktopTask, task_id)
            if task is None or task.status in _TERMINAL_TASK_STATUSES:
                return
            task.status = "cancelled"
            task.error_message = error
            task.finished_at = now
            task.result = {
                "success": False,
                "output": "",
                "error": error,
                "screenshots": [],
                "step_logs": [],
                "duration_seconds": 0.0,
            }
            await db.commit()
            if task.agent_id is not None:
                await self._publish_host_busy_state(
                    int(task.agent_id)
                )

    async def _persist_task_result(
        self,
        task_id: int,
        execution_log_id: int,
        result: dict[str, Any],
    ) -> None:
        success = bool(result.get("success"))
        error = result.get("error")
        screenshots = list(result.get("screenshots") or [])
        step_logs = list(result.get("step_logs") or [])
        now = datetime.utcnow()

        async with self._get_session_factory()() as db:
            task = await db.get(DesktopTask, task_id)
            if task is None or task.status in {"failed", "cancelled"}:
                return
            task.status = "success" if success else "failed"
            task.result = dict(result)
            task.error_message = error if isinstance(error, str) else None
            task.finished_at = now
            if task.started_at is None:
                task.started_at = now

            execution_log = await db.get(ExecutionLog, execution_log_id)
            if execution_log is not None:
                execution_log.status = task.status
                execution_log.error_message = task.error_message
                if screenshots:
                    execution_log.screenshots = screenshots
                if step_logs:
                    execution_log.step_logs = step_logs
                execution_log.finished_at = now
            await db.commit()
            if task.agent_id is not None:
                await self._publish_host_busy_state(
                    int(task.agent_id)
                )

    async def _publish_host_busy_state(self, agent_id: int) -> None:
        if self.host_events is None:
            return
        async with self._get_session_factory()() as db:
            agent = await db.get(DesktopAgent, int(agent_id))
            if agent is None:
                return
            active = await db.scalar(
                select(func.count(DesktopTask.id)).where(
                    DesktopTask.agent_id == int(agent_id),
                    DesktopTask.status.in_(_ACTIVE_TASK_STATUSES),
                )
            )
            publish = getattr(self.host_events, "publish", None)
            if not callable(publish):
                return
            await publish(
                int(agent.user_id),
                "host_busy",
                {
                    "host_id": int(agent.id),
                    "busy": bool(int(active or 0) > 0),
                    "active_task_count": int(active or 0),
                },
            )

    @staticmethod
    async def _apply_agent_event(
        db: AsyncSession,
        event: dict[str, Any],
        task_id: int,
    ) -> None:
        task = await db.get(DesktopTask, task_id)
        if task is None or task.status in {"failed", "cancelled"}:
            return

        event_type = str(event.get("type", ""))
        execution_log = None
        if task.execution_log_id is not None:
            execution_log = await db.get(
                ExecutionLog,
                task.execution_log_id,
            )

        if event_type in {"desktop.progress", "desktop.accepted"}:
            if task.status not in _TERMINAL_TASK_STATUSES:
                task.status = "running"
            if task.started_at is None:
                task.started_at = datetime.utcnow()

            if execution_log is not None:
                step_logs = list(execution_log.step_logs or [])
                step_log = DesktopDispatcher._step_log_from_event(event)
                step_index = step_log["step_index"]
                replaced = False
                for index, existing in enumerate(step_logs):
                    if (
                        isinstance(existing, dict)
                        and existing.get("step_index") == step_index
                    ):
                        step_logs[index] = step_log
                        replaced = True
                        break
                if not replaced:
                    step_logs.append(step_log)

                execution_log.step_logs = step_logs
                screenshot_path = event.get("screenshot_path")
                if isinstance(screenshot_path, str):
                    screenshots = list(execution_log.screenshots or [])
                    if screenshot_path not in screenshots:
                        screenshots.append(screenshot_path)
                    execution_log.screenshots = screenshots
            await db.commit()
            return

        if event_type not in {"desktop.completed", "desktop.failed"}:
            return

        result = DesktopDispatcher._result_from_event(event)
        task.status = "success" if result["success"] else "failed"
        task.result = result
        task.error_message = result["error"]
        task.finished_at = datetime.utcnow()
        if task.started_at is None:
            task.started_at = task.finished_at

        if execution_log is not None:
            execution_log.status = task.status
            execution_log.error_message = result["error"]
            if result["screenshots"]:
                execution_log.screenshots = result["screenshots"]
            if result["step_logs"]:
                execution_log.step_logs = result["step_logs"]
            execution_log.finished_at = task.finished_at
        await db.commit()

    @staticmethod
    def _step_log_from_event(
        event: dict[str, Any],
    ) -> dict[str, Any]:
        raw = event.get("step_log")
        step_log = dict(raw) if isinstance(raw, dict) else {}
        step_log.setdefault("step_index", event.get("step_index", 0))
        step_log.setdefault("status", event.get("status", "running"))
        if "message" in event:
            step_log.setdefault("message", event["message"])
        if "screenshot_path" in event:
            step_log.setdefault(
                "screenshot_path",
                event["screenshot_path"],
            )
        return step_log

    @staticmethod
    def _task_id(event: dict[str, Any]) -> int | None:
        try:
            return int(event.get("task_id"))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _result_from_event(event: dict[str, Any]) -> dict[str, Any]:
        event_type = str(event.get("type", ""))
        success = event.get("success")
        if success is None:
            success = event_type != "desktop.failed"
        elif isinstance(success, str):
            success = success.strip().lower() in {"1", "true", "yes"}
        else:
            success = bool(success)
        if event_type == "desktop.failed":
            success = False

        screenshots = event.get("screenshots")
        if not isinstance(screenshots, list):
            screenshots = []
        step_logs = event.get("step_logs")
        if not isinstance(step_logs, list):
            step_logs = []

        failed_step = event.get("failed_step")
        event_details = event.get("details")
        details: dict[str, Any] = {}
        if isinstance(failed_step, dict):
            details["failed_step"] = failed_step
        if isinstance(event_details, dict):
            details.update(event_details)
        completed_keys = _normalize_keys(
            event.get("completed_idempotency_keys")
        )
        if not completed_keys:
            completed_keys = _normalize_keys(
                event_details.get("completed_idempotency_keys")
                if isinstance(event_details, dict)
                else None
            )
        if completed_keys:
            details["completed_idempotency_keys"] = completed_keys
        return {
            "success": success,
            "output": str(event.get("output") or ""),
            "error": event.get("error"),
            "screenshots": screenshots,
            "step_logs": step_logs,
            "duration_seconds": float(event.get("duration_seconds") or 0),
            "details": details or None,
        }

    def _register_task_event(self, task_id: int) -> asyncio.Event:
        register = getattr(self.connection_manager, "register_task", None)
        if callable(register):
            event = register(task_id)
            if isinstance(event, asyncio.Event):
                self._task_completion_events[task_id] = event
                return event
        event = asyncio.Event()
        self._task_completion_events[task_id] = event
        return event

    def _take_task_result(self, task_id: int) -> dict[str, Any] | None:
        getter = getattr(self.connection_manager, "get_result", None)
        if callable(getter):
            result = getter(task_id)
            if isinstance(result, dict):
                return result
        return self._task_results.pop(task_id, None)

    def _clear_task_event(self, task_id: int) -> None:
        self._task_completion_events.pop(task_id, None)
        self._task_results.pop(task_id, None)
        self._task_agents.pop(task_id, None)
        clear = getattr(self.connection_manager, "clear_task", None)
        if callable(clear):
            clear(task_id)

    def _get_session_factory(self):
        return self.session_factory or AsyncSessionLocal

    def _resolve_timeout(
        self,
        workflow: Workflow,
        context: dict[str, Any] | None,
    ) -> float:
        candidates: list[Any] = []
        if isinstance(context, dict):
            candidates.extend(
                [
                    context.get("timeout_seconds"),
                    context.get("timeout"),
                    context.get("execution_timeout"),
                    context.get("EXECUTION_TIMEOUT"),
                ]
            )
        candidates.extend(
            [
                getattr(workflow, "EXECUTION_TIMEOUT", None),
                getattr(workflow, "execution_timeout", None),
                getattr(workflow, "timeout", None),
                getattr(workflow, "timeout_seconds", None),
                self.timeout,
            ]
        )
        for candidate in candidates:
            if isinstance(candidate, (int, float)) and candidate > 0:
                return float(candidate)
        return float(self.timeout)

    @staticmethod
    def _required_capabilities(workflow: Workflow) -> set[str]:
        capabilities = getattr(
            workflow,
            "required_capabilities",
            None,
        )
        if isinstance(capabilities, str):
            return {capabilities}
        if not isinstance(capabilities, (list, tuple, set)):
            return set()
        return {str(capability) for capability in capabilities}

    @staticmethod
    def _step_offset(context: dict[str, Any] | None) -> int:
        """续跑时用于把步骤编号还原到完整流程中的位置。"""

        if not isinstance(context, dict):
            return 0
        try:
            offset = int(context.get("step_offset") or 0)
        except (TypeError, ValueError):
            return 0
        return max(offset, 0)

    @staticmethod
    def _resume_from_step(context: dict[str, Any] | None) -> int:
        if not isinstance(context, dict):
            return 0
        for key in ("resume_from_step", "next_step_index"):
            try:
                value = int(context.get(key) or 0)
            except (TypeError, ValueError):
                continue
            if value > 0:
                return value
        return 0

    @staticmethod
    def _completed_idempotency_keys(
        context: dict[str, Any] | None,
    ) -> list[str]:
        if not isinstance(context, dict):
            return []
        return _normalize_keys(context.get("completed_idempotency_keys"))

    @staticmethod
    def _host_decision_details(
        decision: HostDecision,
    ) -> dict[str, Any]:
        return {
            "failure_category": "environment_unavailable",
            "host_error_code": decision.error_code,
            "host_id": decision.host_id,
            "host_environment": decision.environment,
            "host_capabilities": list(decision.capabilities),
            "host_reason": decision.reason,
            "host_fallback_allowed": decision.fallback_allowed,
            "host_affinity_hash": decision.session_fingerprint,
        }

    @staticmethod
    def _host_error_message(decision: HostDecision) -> str:
        code = decision.error_code or "host_unavailable"
        if code == "host_unavailable":
            return "No available desktop agent"
        return f"{code}: {decision.reason or code}"

    @staticmethod
    def _failure_result(
        error: str,
        started_at: float,
        *,
        details: dict[str, Any] | None = None,
    ) -> ExecutionResult:
        return ExecutionResult(
            success=False,
            output="",
            error=error,
            screenshots=[],
            step_logs=[],
            duration_seconds=time.perf_counter() - started_at,
            details=dict(details or {}) or None,
        )

    @staticmethod
    def _result_from_payload(
        payload: dict[str, Any],
        started_at: float,
    ) -> ExecutionResult:
        duration = payload.get("duration_seconds")
        if not isinstance(duration, (int, float)):
            duration = time.perf_counter() - started_at
        raw_details = payload.get("details")
        details: dict[str, Any] = {}
        if isinstance(raw_details, dict):
            details.update(raw_details)
        else:
            failed_step = payload.get("failed_step")
            if isinstance(failed_step, dict):
                details["failed_step"] = failed_step
        completed_keys = _normalize_keys(
            payload.get("completed_idempotency_keys")
        )
        if not completed_keys:
            completed_keys = _normalize_keys(
                details.get("completed_idempotency_keys")
            )
        if completed_keys:
            details["completed_idempotency_keys"] = completed_keys
        return ExecutionResult(
            success=bool(payload.get("success")),
            output=str(payload.get("output") or ""),
            error=payload.get("error"),
            screenshots=list(payload.get("screenshots") or []),
            step_logs=list(payload.get("step_logs") or []),
            duration_seconds=float(duration),
            details=details or None,
        )


async def apply_agent_event(
    db: AsyncSession,
    event: dict[str, Any],
) -> None:
    """Persist a desktop Agent event using a caller-owned DB session."""

    task_id = DesktopDispatcher._task_id(event)
    if task_id is None:
        return
    await DesktopDispatcher._apply_agent_event(db, event, task_id)


__all__ = ["DesktopDispatcher", "apply_agent_event"]
