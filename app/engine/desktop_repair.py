"""桌面流程执行失败后的自动修复。

当失败原因是“找不到控件”时，用执行现场回报的候选控件重写失败步骤，并从
该步骤继续执行，最多重试指定次数。
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.actions.validation import validate_workflow_draft
from app.engine.dag import normalize_execution_plan
from app.engine.desktop_context import DesktopExecutionContext
from app.engine.desktop_catalog import capabilities_for_actions
from app.config import get_settings
from app.database import AsyncSessionLocal
from app.models.agent import DesktopAgent
from app.models.workflow import Workflow

logger = logging.getLogger(__name__)

MAX_REPAIR_ATTEMPTS = 2
_ELEMENT_ACTIONS = {"click_element", "read_text"}
_WAIT_ACTIONS = {"wait"}
_WINDOW_ACTIONS = {"activate_window"}
_CANDIDATE_ACTIONS = (
    _ELEMENT_ACTIONS | _WAIT_ACTIONS | _WINDOW_ACTIONS
)


class DesktopRepairPlanError(ValueError):
    pass


class DesktopDispatcherLike(Protocol):
    async def dispatch(
        self,
        workflow: Any,
        context: dict[str, Any] | None = None,
    ) -> Any: ...


def repairable_failure(result: Any) -> dict[str, Any] | None:
    """判断执行结果是否属于可以用真实控件修复的失败。"""

    details = getattr(result, "details", None)
    if not isinstance(details, dict):
        return None
    failed_step = details.get("failed_step")
    if not isinstance(failed_step, dict):
        return None

    candidates = candidate_controls(failed_step)
    details = candidate_details(failed_step)
    last_locator = _last_locator(failed_step)
    action_type = str(failed_step.get("action_type") or "")
    error = str(failed_step.get("error") or "")
    params = failed_step.get("params")
    if not _is_repairable(
        action_type,
        error,
        candidates,
        params if isinstance(params, dict) else {},
    ):
        return None

    try:
        step_index = int(failed_step.get("step_index"))
    except (TypeError, ValueError):
        return None
    if step_index < 0:
        return None

    return {
        "step_index": step_index,
        "action_type": action_type,
        "error": error,
        "candidates": candidates,
        "candidate_details": details,
        "last_locator": last_locator,
    }


def candidate_controls(failed_step: dict[str, Any]) -> list[str]:
    """把失败详情里的候选统一压成人类可读标签列表。"""

    details = failed_step.get("details")
    if not isinstance(details, dict):
        return []

    labels = details.get("candidate_labels")
    controls: list[str] = []
    if isinstance(labels, list):
        for label in labels:
            text = str(label).strip()
            if text and text not in controls:
                controls.append(text)
    if controls:
        return controls

    candidates = details.get("candidates")
    if not isinstance(candidates, list):
        return []
    for candidate in candidates:
        label = _candidate_label(candidate)
        if label and label not in controls:
            controls.append(label)
    return controls


def candidate_details(failed_step: dict[str, Any]) -> list[dict[str, Any]]:
    """保留候选的结构化详情，供修复规划器选择真实控件。"""

    details = failed_step.get("details")
    if not isinstance(details, dict):
        return []
    candidates = details.get("candidates")
    if not isinstance(candidates, list):
        return []
    return [
        dict(candidate)
        for candidate in candidates
        if isinstance(candidate, dict)
    ]


def _is_repairable(
    action_type: str,
    error: str,
    candidates: list[str],
    params: dict[str, Any],
) -> bool:
    """按失败动作和错误信息判断是否值得进入修复回路。"""

    if candidates:
        return True
    lowered = error.lower()
    if action_type in _ELEMENT_ACTIONS:
        return any(
            marker in lowered
            for marker in ("ui automation", "no matching", "not found")
        )
    if action_type in _WAIT_ACTIONS:
        condition = str(params.get("condition") or "")
        if condition == "element_appears":
            return "element_appears" in error or "ui automation" in lowered
        if condition == "window_appears":
            return "window_appears" in error
        return False
    if action_type in _WINDOW_ACTIONS:
        return "window" in lowered and (
            "no matching" in lowered or "not found" in lowered
        )
    return False


def _candidate_label(candidate: Any) -> str:
    if isinstance(candidate, str):
        return candidate.strip()
    if not isinstance(candidate, dict):
        return str(candidate).strip()

    label = candidate.get("label")
    if isinstance(label, str) and label.strip():
        return label.strip()

    name = str(
        candidate.get("name") or candidate.get("title") or ""
    ).strip()
    kind = str(
        candidate.get("control_type")
        or candidate.get("process_name")
        or candidate.get("class_name")
        or ""
    ).strip()
    if name and kind:
        return f"{name}({kind})"
    if name:
        return name

    automation_id = str(candidate.get("automation_id") or "").strip()
    if automation_id:
        return f"automation_id={automation_id}"
    return ""


def _last_locator(failed_step: dict[str, Any]) -> dict[str, Any] | None:
    details = failed_step.get("details")
    if not isinstance(details, dict):
        return None
    last_locator = details.get("last_locator")
    return dict(last_locator) if isinstance(last_locator, dict) else None


class DesktopRepairCoordinator:
    """执行桌面流程，并在可修复失败时自动重写失败步骤。"""

    def __init__(
        self,
        dispatcher: DesktopDispatcherLike,
        *,
        planner: Any | None = None,
        session_factory: async_sessionmaker[AsyncSession] = AsyncSessionLocal,
        max_attempts: int = MAX_REPAIR_ATTEMPTS,
    ) -> None:
        self.dispatcher = dispatcher
        self._planner = planner
        self.session_factory = session_factory
        self.max_attempts = max(int(max_attempts), 0)

    async def execute(
        self,
        workflow: Any,
        context: dict[str, Any] | None = None,
    ) -> Any:
        request_context = dict(context or {})
        result = await self.dispatcher.dispatch(workflow, request_context)
        collected_logs = list(getattr(result, "step_logs", None) or [])
        repairs: list[dict[str, Any]] = []
        attempts = 0

        while attempts < self.max_attempts:
            failure = repairable_failure(result)
            if failure is None:
                break

            plan = _plan_dict(workflow)
            steps = plan.get("steps")
            if not isinstance(steps, list):
                break
            index = failure["step_index"]
            if index >= len(steps):
                break

            execution_context = await self._desktop_context(workflow)
            if execution_context is None or not execution_context.allowed_apps:
                break

            try:
                planner = self._planner_instance()
                if planner is None:
                    break
                repaired_steps = await planner.repair(
                    plan=plan,
                    step_index=index,
                    failure=failure,
                    context=execution_context,
                )
            except Exception:
                logger.warning(
                    "Desktop repair planning failed",
                    exc_info=True,
                )
                break
            if not repaired_steps:
                break

            try:
                merged = _merge_plan(
                    plan,
                    index,
                    repaired_steps,
                    execution_context,
                )
            except DesktopRepairPlanError as exc:
                logger.warning("Desktop repair plan rejected: %s", exc)
                break

            attempts += 1
            plan = merged
            await self._store_plan(workflow, merged)
            collected_logs.append(
                {
                    "step_index": index,
                    "action_type": "auto_repair",
                    "status": "skipped",
                    "message": (
                        f"自动修复第 {index + 1} 步：{failure['error']}"
                    ),
                    "candidates": failure["candidates"],
                    "attempt": attempts,
                }
            )

            resume_context = dict(request_context)
            for key in (
                "resume_from_step",
                "next_step_index",
                "initial_step_logs",
            ):
                resume_context.pop(key, None)
            resume_context["step_offset"] = index
            result = await self.dispatcher.dispatch(
                _resume_view(
                    workflow,
                    {"steps": list(merged["steps"][index:])},
                ),
                resume_context,
            )
            collected_logs.extend(
                list(getattr(result, "step_logs", None) or [])
            )
            result.step_logs = collected_logs
            repair_record: dict[str, Any] = {
                "attempt": attempts,
                "step_index": index,
                "action": failure["action_type"],
                "error": failure["error"],
                "candidates": failure["candidates"],
            }
            if failure.get("candidate_details"):
                repair_record["candidate_details"] = failure[
                    "candidate_details"
                ]
            if failure.get("last_locator"):
                repair_record["last_locator"] = failure["last_locator"]
            repairs.append(repair_record)

        if repairs:
            details = dict(getattr(result, "details", None) or {})
            details["repairs"] = repairs
            result.details = details
        return result

    def _planner_instance(self) -> Any | None:
        return self._planner

    async def _desktop_context(
        self,
        workflow: Any,
    ) -> DesktopExecutionContext | None:
        user_id = getattr(workflow, "user_id", None)
        try:
            async with self.session_factory() as session:
                statement = select(DesktopAgent).where(
                    DesktopAgent.platform == "windows"
                )
                if user_id is not None:
                    statement = statement.where(
                        DesktopAgent.user_id == user_id
                    )
                result = await session.execute(statement)
                agents = list(result.scalars().all())
        except Exception:
            logger.warning(
                "Could not load desktop agents for repair",
                exc_info=True,
            )
            return None

        if not agents:
            return None

        online = set(_online_agent_ids(self.dispatcher))
        ordered = sorted(
            agents,
            key=lambda agent: (agent.id not in online, int(agent.id)),
        )

        allowed_apps: list[str] = []
        capabilities: set[str] = set()
        for agent in ordered:
            for app in agent.allowed_apps or []:
                text = str(app).strip()
                if text and text not in allowed_apps:
                    allowed_apps.append(text)
            for capability in agent.capabilities or []:
                text = str(capability).strip()
                if text:
                    capabilities.add(text)

        return DesktopExecutionContext(
            allowed_apps=tuple(allowed_apps),
            capabilities=frozenset(capabilities),
            online_agent_count=len(online),
            paired_agent_count=len(agents),
            agent_ids=tuple(int(agent.id) for agent in ordered),
        )

    async def _store_plan(
        self,
        workflow: Any,
        plan: dict[str, Any],
    ) -> None:
        # 混合执行器按单步派发桌面步骤时，修复结果只回写到临时视图，
        # 不能覆盖数据库里的完整混合计划。
        if getattr(workflow, "_hybrid_step_dispatch", False):
            workflow.structured_plan = plan
            return

        workflow_id = getattr(workflow, "id", None)
        if workflow_id is not None:
            try:
                async with self.session_factory() as session:
                    stored = await session.get(Workflow, workflow_id)
                    if stored is not None:
                        stored.structured_plan = plan
                        await session.commit()
            except Exception:
                logger.warning(
                    "Could not persist repaired desktop plan",
                    exc_info=True,
                )
        workflow.structured_plan = plan


def build_desktop_repair(
    engine: Any,
    session_factory: async_sessionmaker[AsyncSession] = AsyncSessionLocal,
) -> DesktopRepairCoordinator | None:
    dispatcher = getattr(engine, "desktop_dispatcher", None)
    if dispatcher is None:
        return None

    attempts = getattr(
        get_settings(),
        "DESKTOP_REPAIR_ATTEMPTS",
        MAX_REPAIR_ATTEMPTS,
    )
    try:
        max_attempts = max(int(attempts), 0)
    except (TypeError, ValueError):
        max_attempts = MAX_REPAIR_ATTEMPTS
    if max_attempts <= 0:
        return None

    return DesktopRepairCoordinator(
        dispatcher,
        session_factory=session_factory,
        max_attempts=max_attempts,
    )


def _merge_plan(
    plan: dict[str, Any],
    index: int,
    repaired_steps: list[dict[str, Any]],
    context: DesktopExecutionContext,
) -> dict[str, Any]:
    original_steps = plan.get("steps")
    prefix = (
        list(original_steps[:index])
        if isinstance(original_steps, list)
        else []
    )
    merged_steps = [*prefix, *list(repaired_steps)]
    candidate = {
        "schema_version": 1,
        "execution_mode": "structured",
        "executor_type": "windows",
        "steps": merged_steps,
        "variables": plan.get("variables", []),
    }
    validation = validate_workflow_draft(
        candidate,
        executor_type="windows",
    )
    if not validation.valid:
        raise DesktopRepairPlanError("; ".join(validation.errors))
    try:
        nodes = normalize_execution_plan(
            candidate,
            default_executor="windows",
        )
    except ValueError as exc:
        raise DesktopRepairPlanError(str(exc)) from exc
    for node in nodes:
        if node.executor != "windows":
            raise DesktopRepairPlanError(
                f"Repair step {node.step_id} is not a Windows action"
            )
        if node.action == "launch_app":
            path = str(node.raw.get("params", {}).get("path") or "")
            if path and not _allowed_repair_app(path, context.allowed_apps):
                raise DesktopRepairPlanError(
                    f"Repair app is not allowlisted: {path}"
                )
    validated = {
        **candidate,
        "steps": [dict(node.raw) for node in nodes],
    }

    steps = list(validated["steps"])

    merged = dict(plan)
    merged["executor_type"] = "windows"
    merged["steps"] = steps
    merged["required_capabilities"] = capabilities_for_actions(
        str(step.get("action") or "")
        for step in steps
        if isinstance(step, dict)
    )
    return merged


def _allowed_repair_app(
    path: str,
    allowed_apps: tuple[str, ...],
) -> bool:
    normalized = path.strip().replace("/", "\\").lower()
    basename = normalized.rsplit("\\", 1)[-1]
    for allowed in allowed_apps:
        candidate = str(allowed).strip().replace("/", "\\").lower()
        if candidate in {normalized, basename} or (
            "\\" in candidate
            and candidate.rsplit("\\", 1)[-1] == basename
        ):
            return True
    return False


def _resume_view(
    workflow: Any,
    plan: dict[str, Any],
) -> SimpleNamespace:
    return SimpleNamespace(
        id=getattr(workflow, "id", None),
        user_id=getattr(workflow, "user_id", None),
        name=getattr(workflow, "name", None),
        structured_plan=plan,
        executor_type="windows",
        required_capabilities=getattr(
            workflow,
            "required_capabilities",
            None,
        ),
        _hybrid_step_dispatch=getattr(
            workflow,
            "_hybrid_step_dispatch",
            False,
        ),
    )


def _plan_dict(workflow: Any) -> dict[str, Any]:
    plan = getattr(workflow, "structured_plan", None)
    return dict(plan) if isinstance(plan, dict) else {}


def _online_agent_ids(dispatcher: Any) -> list[int]:
    manager = getattr(dispatcher, "connection_manager", None)
    getter = getattr(manager, "get_online_agents", None)
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


__all__ = [
    "DesktopRepairCoordinator",
    "MAX_REPAIR_ATTEMPTS",
    "build_desktop_repair",
    "candidate_controls",
    "repairable_failure",
]
