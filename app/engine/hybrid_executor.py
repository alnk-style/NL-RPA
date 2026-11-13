"""混合执行器：按 ``step.executor`` 分发步骤，并按 DAG 依赖调度。

同一个 Workflow 里可以同时包含 ``web``、``windows``、``code`` 与后续
的 ``mcp`` 步骤。调度器遵守 ``depends_on`` 的拓扑顺序：依赖未完成的
步骤不会启动；依赖失败的步骤连同其下游一起记为 skipped；互不依赖的
分支可以并行执行，但共享同一资源的步骤（同一个浏览器页、同一个桌面
Agent）会串行化，避免相互抢占。

续跑复用阶段 2 的幂等语义：``completed_idempotency_keys`` 命中的步骤
直接记为 skipped，执行完成后返回的 ``details`` 里带有失败步骤与已完成
键，交给 ``ExecutionManager`` 写检查点。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import runtime_flags
from app.actions.registry import default_action_registry
from app.engine.desktop_catalog import capabilities_for_actions
from app.config import get_settings
from app.database import AsyncSessionLocal
from app.engine.app_mcp.artifacts import extract_mcp_artifacts
from app.engine.artifacts import resolve_export_roots
from app.engine.app_mcp.results import (
    ResultPathError,
    extract_result_value,
    validate_result_path,
)
from app.engine.dag import (
    DEPENDENCY_BLOCKED_STATUSES,
    DEPENDENCY_SATISFIED_STATUSES,
    ExecutionPlanError,
    PlanStep,
    evaluate_condition,
    merge_dag_definition,
    normalize_execution_plan,
)
from app.engine.mcp_registry import MCPRegistry
from app.engine.runner import ExecutionResult
from app.engine.task_runtime import (
    RuntimeState,
    StepResult,
    TaskRuntime,
)
from app.models.execution import ExecutionLog
from app.protocols import FailureCategory, classify_failure
from app.services.email import EmailService, load_email_settings

logger = logging.getLogger(__name__)

MAX_PARALLEL_STEPS = 4

_ACTION_ALIASES = {
    "navigate": "goto",
}

_MCP_ACTIONS = {"call_tool", "call", "tool"}


def _as_positive_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


@dataclass
class _StepOutcome:
    status: str
    logs: list[dict[str, Any]] = field(default_factory=list)
    output: list[str] = field(default_factory=list)
    screenshots: list[str] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    failure: dict[str, Any] | None = None
    extracted: dict[str, Any] = field(default_factory=dict)
    completed_keys: list[str] = field(default_factory=list)
    skipped_reason: str | None = None
    repaired_steps: list[dict[str, Any]] | None = None
    awaiting_user: bool = False


class HybridExecutor:
    """按步骤执行器路由，并在运行时执行 DAG 依赖与条件。"""

    def __init__(
        self,
        engine: Any,
        desktop_executor: Any | None = None,
        *,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        config: Any | None = None,
        max_parallel: int = MAX_PARALLEL_STEPS,
        mcp_registry: Any | None = None,
    ) -> None:
        self.engine = engine
        self.desktop_executor = desktop_executor
        self.session_factory = session_factory or AsyncSessionLocal
        self.config = config
        self.mcp_registry = mcp_registry
        try:
            self.max_parallel = max(1, int(max_parallel))
        except (TypeError, ValueError):
            self.max_parallel = MAX_PARALLEL_STEPS
        self._progress_lock = asyncio.Lock()

    async def execute(
        self,
        workflow: Any,
        context: dict[str, Any] | None = None,
    ) -> ExecutionResult:
        started_at = time.perf_counter()
        request_context = dict(context or {})
        raw_steps = self._raw_steps(workflow)
        if not raw_steps:
            return self._failure_result(
                "Workflow has no structured steps",
                started_at,
            )

        merged = merge_dag_definition(
            raw_steps,
            getattr(workflow, "dag_definition", None),
        )
        try:
            nodes = normalize_execution_plan(
                {"steps": merged},
                default_executor=(
                    getattr(workflow, "executor_type", None) or "web"
                ),
            )
        except ExecutionPlanError as exc:
            return self._failure_result(str(exc), started_at)

        if self._uses_task_runtime(nodes):
            return await self._execute_task_runtime(
                workflow,
                request_context,
                nodes,
                started_at,
            )

        variables = request_context.get("variables")
        if not isinstance(variables, dict):
            variables = {}
        extracted: dict[str, Any] = dict(variables)
        completed_keys = self._normalize_keys(
            request_context.get("completed_idempotency_keys")
        )
        completed_key_set = set(completed_keys)
        prior_statuses = self._prior_statuses(
            request_context.get("initial_step_logs"),
            nodes,
        )

        statuses: dict[int, str] = {}
        logs_by_step: dict[int, list[dict[str, Any]]] = {}
        output: list[str] = []
        screenshots: list[str] = []
        artifacts: list[dict[str, Any]] = []
        skipped_steps: list[int] = []
        failures: list[dict[str, Any]] = []
        repairs: list[dict[str, Any]] = []
        plan_steps = [dict(node.raw) for node in nodes]
        awaiting_user = False

        # 续跑：命中幂等键或上次执行已完成的步骤直接记为 skipped。
        for node in nodes:
            key_completed = bool(
                node.idempotency_key
                and node.idempotency_key in completed_key_set
            )
            prior = prior_statuses.get(node.step_id)
            if not key_completed and prior not in DEPENDENCY_SATISFIED_STATUSES:
                continue
            statuses[node.step_id] = "success"
            if (
                node.idempotency_key
                and node.idempotency_key not in completed_key_set
            ):
                completed_key_set.add(node.idempotency_key)
                completed_keys.append(node.idempotency_key)
            logs_by_step[node.step_id] = [
                self._skip_log(
                    node,
                    message=(
                        "幂等标记已完成："
                        f"{node.idempotency_key}"
                        if key_completed
                        else "续跑跳过已完成步骤"
                    ),
                    reason=(
                        "idempotency_key"
                        if key_completed
                        else "resume_completed"
                    ),
                )
            ]
            output.append(
                f"[{node.index + 1}] {node.action} 已按幂等标记跳过"
            )

        pending: dict[int, PlanStep] = {
            node.step_id: node
            for node in nodes
            if node.step_id not in statuses
        }
        running: dict[asyncio.Task[_StepOutcome], PlanStep] = {}
        busy_resources: dict[str, int] = {}
        web_session: Any | None = None
        web_page: Any | None = None
        execution_id = self._execution_id(request_context)

        try:
            while pending or running:
                progressed = True
                while progressed:
                    progressed = False
                    for node in nodes:
                        if node.step_id not in pending:
                            continue

                        blocker = self._blocking_dependency(
                            node,
                            statuses,
                        )
                        if blocker is not None:
                            self._record_condition_skip(
                                node,
                                statuses,
                                logs_by_step,
                                skipped_steps,
                                output,
                                reason="dependency_failed",
                                message=(
                                    "依赖步骤失败，已跳过："
                                    f"{blocker}"
                                ),
                            )
                            pending.pop(node.step_id)
                            progressed = True
                            continue

                        if not self._dependencies_ready(node, statuses):
                            continue

                        if not evaluate_condition(
                            node.condition,
                            variables=variables,
                            extracted=extracted,
                            statuses=statuses,
                        ):
                            self._record_condition_skip(
                                node,
                                statuses,
                                logs_by_step,
                                skipped_steps,
                                output,
                                reason="condition_false",
                                message="条件不满足，已跳过",
                            )
                            pending.pop(node.step_id)
                            progressed = True
                            continue

                        if len(running) >= self.max_parallel:
                            continue
                        resource = self._resource_key(node)
                        if resource in busy_resources:
                            continue

                        if node.executor == "web" and web_page is None:
                            executor = self._web_executor()
                            if executor is None:
                                outcome = self._unavailable_outcome(
                                    node,
                                    "Web executor is not ready",
                                )
                                self._apply_outcome(
                                    node,
                                    outcome,
                                    statuses,
                                    logs_by_step,
                                    skipped_steps,
                                    failures,
                                    output,
                                    screenshots,
                                    artifacts,
                                    repairs,
                                    plan_steps,
                                    completed_keys,
                                    extracted,
                                )
                                pending.pop(node.step_id)
                                progressed = True
                                continue
                            web_session = executor.browser_session()
                            web_page = await web_session.__aenter__()

                        task = asyncio.create_task(
                            self._execute_step(
                                node,
                                workflow,
                                request_context,
                                page=web_page,
                                variables=variables,
                                extracted=extracted,
                                completed_keys=completed_keys,
                            ),
                            name=(
                                f"nl-rpa-step-{node.step_id}-"
                                f"{node.executor}"
                            ),
                        )
                        running[task] = node
                        pending.pop(node.step_id)
                        busy_resources[resource] = node.step_id
                        progressed = True

                if not running:
                    # 依赖环在计划校验阶段已拦截；剩余步骤只可能是
                    # 等待被阻塞依赖，统一记为 skipped，避免死循环。
                    for node in list(pending.values()):
                        self._record_condition_skip(
                            node,
                            statuses,
                            logs_by_step,
                            skipped_steps,
                            output,
                            reason="dependency_blocked",
                            message="依赖无法完成，已跳过",
                        )
                        pending.pop(node.step_id)
                    break

                done, _ = await asyncio.wait(
                    set(running),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in done:
                    node = running.pop(task)
                    busy_resources.pop(
                        self._resource_key(node),
                        None,
                    )
                    try:
                        outcome = task.result()
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:  # noqa: BLE001
                        outcome = _StepOutcome(
                            status="failed",
                            logs=[
                                self._base_log(
                                    node,
                                    status="failed",
                                )
                            ],
                            output=[
                                (
                                    f"[{node.index + 1}] {node.action} "
                                    f"执行异常：{exc}"
                                )
                            ],
                            error=str(exc) or exc.__class__.__name__,
                            failure=self._failure_detail(
                                node,
                                str(exc) or exc.__class__.__name__,
                            ),
                        )
                    if outcome.awaiting_user:
                        awaiting_user = True
                    self._apply_outcome(
                        node,
                        outcome,
                        statuses,
                        logs_by_step,
                        skipped_steps,
                        failures,
                        output,
                        screenshots,
                        artifacts,
                        repairs,
                        plan_steps,
                        completed_keys,
                        extracted,
                    )
                    await self._persist_progress(
                        execution_id,
                        self._ordered_logs(nodes, logs_by_step),
                        screenshots,
                    )
        except asyncio.CancelledError:
            for task in list(running):
                task.cancel()
            if running:
                await asyncio.gather(
                    *running,
                    return_exceptions=True,
                )
            raise
        except Exception as exc:
            logger.exception("Hybrid execution failed")
            failures.insert(
                0,
                self._failure_detail(
                    nodes[0],
                    str(exc) or exc.__class__.__name__,
                ),
            )
        finally:
            if web_session is not None:
                try:
                    await web_session.__aexit__(None, None, None)
                except Exception:
                    logger.debug(
                        "关闭混合执行浏览器会话失败",
                        exc_info=True,
                    )

        failed_step = failures[0] if failures else None
        success = failed_step is None
        details: dict[str, Any] = {
            "executor": "hybrid",
            "step_count": len(nodes),
            "extracted": {
                key: value
                for key, value in extracted.items()
                if key not in variables
            },
            "completed_idempotency_keys": completed_keys,
        }
        if skipped_steps:
            details["skipped_steps"] = skipped_steps
        if failed_step is not None:
            details["failed_step"] = failed_step
        if awaiting_user:
            details["awaiting_user"] = True
        if repairs:
            details["repairs"] = repairs
        changed = any(
            plan_steps[index] != node.raw
            for index, node in enumerate(nodes)
        )
        if changed:
            details["repaired_plan"] = {
                "executor_type": (
                    getattr(workflow, "executor_type", None) or "web"
                ),
                "execution_mode": "hybrid",
                "steps": plan_steps,
            }

        return ExecutionResult(
            success=success,
            output="\n".join(output),
            error=(
                failed_step.get("error") if failed_step else None
            ),
            screenshots=screenshots,
            step_logs=self._ordered_logs(nodes, logs_by_step),
            duration_seconds=time.perf_counter() - started_at,
            details=details,
            artifacts=artifacts,
        )

    @staticmethod
    def _uses_task_runtime(nodes: list[PlanStep]) -> bool:
        for node in nodes:
            if node.executor in {"files", "transform", "email"}:
                return True
            if any(
                key in node.raw
                for key in (
                    "result_var",
                    "output_var",
                    "foreach",
                    "preconditions",
                    "postconditions",
                    "verify",
                )
            ):
                return True
        return False

    async def _execute_task_runtime(
        self,
        workflow: Any,
        request_context: dict[str, Any],
        nodes: list[PlanStep],
        started_at: float,
    ) -> ExecutionResult:
        state = RuntimeState()
        logs: list[dict[str, Any]] = []
        artifacts: list[dict[str, Any]] = []
        completed_keys: list[str] = []
        awaiting_user = False
        web_session: Any | None = None
        web_page: Any | None = None

        async def execute_step(
            step: dict[str, Any],
            runtime_state: dict[str, Any],
        ) -> StepResult:
            nonlocal web_session, web_page, awaiting_user
            default_executor = str(
                getattr(workflow, "executor_type", None) or "windows"
            )
            isolated_step = {
                **step,
                "depends_on": [],
            }
            runtime_nodes = normalize_execution_plan(
                {"steps": [isolated_step]},
                default_executor=default_executor,
            )
            node = runtime_nodes[0]
            variables = runtime_state.get("variables")
            if not isinstance(variables, dict):
                variables = {}
            extracted: dict[str, Any] = {}
            if node.executor == "web":
                if web_page is None:
                    executor = self._web_executor()
                    if executor is None:
                        return StepResult(
                            success=False,
                            message="Web executor is not ready",
                        )
                    web_session = executor.browser_session()
                    web_page = await web_session.__aenter__()
                outcome = await self._execute_web_step(
                    node,
                    request_context,
                    page=web_page,
                    variables=variables,
                    extracted=extracted,
                    completed_keys=completed_keys,
                )
            elif node.executor in {"windows", "files"}:
                outcome = await self._execute_windows_step(
                    node,
                    workflow,
                    request_context,
                    variables=variables,
                    extracted=extracted,
                    completed_keys=completed_keys,
                )
            elif node.executor == "mcp":
                outcome = await self._execute_mcp_step(
                    node,
                    workflow,
                    request_context,
                    variables=variables,
                    extracted=extracted,
                )
            elif node.executor == "code":
                outcome = await self._execute_code_step(
                    node,
                    request_context,
                    variables=variables,
                    extracted=extracted,
                )
            else:
                outcome = self._unavailable_outcome(
                    node,
                    f"Executor '{node.executor}' is not implemented",
                )

            logs.extend(dict(entry) for entry in outcome.logs)
            artifacts.extend(
                dict(item)
                for item in outcome.artifacts
                if isinstance(item, dict)
            )
            for key in outcome.completed_keys:
                if key not in completed_keys:
                    completed_keys.append(key)
            if outcome.awaiting_user:
                awaiting_user = True
            extracted_values = dict(outcome.extracted)
            expected_var = str(
                step.get("params", {}).get("variable")
                if isinstance(step.get("params"), dict)
                else ""
            ).strip()
            result_value: Any = None
            if expected_var and expected_var in extracted_values:
                result_value = extracted_values[expected_var]
            elif extracted_values:
                result_value = next(iter(extracted_values.values()))
            else:
                for entry in reversed(outcome.logs):
                    result_payload = entry.get("result")
                    if not isinstance(result_payload, dict):
                        continue
                    if result_payload.get("text") is not None:
                        result_value = result_payload.get("text")
                        break
                    if result_payload.get("value") is not None:
                        result_value = result_payload.get("value")
                        break
            return StepResult(
                success=outcome.status == "success",
                message=(
                    outcome.error
                    or f"{node.action} {outcome.status}"
                ),
                value=result_value,
                items=(
                    result_value
                    if isinstance(result_value, list)
                    else None
                ),
                artifacts=list(outcome.artifacts),
                data={"variables": extracted_values},
                verification={
                    "passed": outcome.status == "success",
                    "checks": [],
                },
            )

        ordered_steps = [
            dict(node.raw)
            for node in self._ordered_nodes(nodes)
        ]
        user_id = getattr(workflow, "user_id", None)

        async def email_service_provider(credential_key: str):
            if user_id is None:
                return None
            config = self.config or get_settings()
            async with self.session_factory() as session:
                settings = await load_email_settings(
                    session,
                    config,
                    int(user_id),
                    credential_key=credential_key,
                    include_password=True,
                )
            allowed_roots = resolve_export_roots(
                getattr(self.config, "WEB_EXPORT_DIRS", "")
                if self.config is not None
                else ""
            )
            return EmailService(
                settings,
                session_factory=self.session_factory,
                allowed_roots=allowed_roots,
            )

        runtime = TaskRuntime(
            step_executor=execute_step,
            action_registry=default_action_registry(),
            action_services={
                "allowed_roots": resolve_export_roots(
                    getattr(
                        self.config,
                        "WEB_EXPORT_DIRS",
                        "",
                    )
                ),
                "email_service_provider": email_service_provider,
            },
        )
        try:
            result = await runtime.execute(
                {
                    "variables": self._declared_variables(
                        getattr(workflow, "structured_plan", None)
                    ),
                    "steps": ordered_steps,
                },
                request_context,
            )
        finally:
            if web_session is not None:
                try:
                    await web_session.__aexit__(None, None, None)
                except Exception:
                    logger.debug(
                        "关闭任务运行时浏览器会话失败",
                        exc_info=True,
                    )

        logged_step_ids = {
            int(entry["step_id"])
            for entry in logs
            if entry.get("step_id") is not None
            and str(entry.get("step_id")).isdigit()
        }
        for node in self._ordered_nodes(nodes):
            step_result = result.results.get(node.step_id)
            if step_result is None or node.step_id in logged_step_ids:
                continue
            entry = self._base_log(
                node,
                status="success" if step_result.success else "failed",
                message=step_result.message,
                error=(
                    None
                    if step_result.success
                    else step_result.message or result.error
                ),
            )
            entry["verification"] = dict(step_result.verification)
            logs.append(entry)
        logs.sort(key=lambda entry: int(entry.get("step_index") or 0))

        state.variables = dict(result.variables)
        state.results = dict(result.results)
        state.artifacts = list(result.artifacts or artifacts)
        state.completed_steps = list(result.results)
        human_action: dict[str, Any] = {}
        failed_step_result = None
        failed_step_id: int | None = None
        for step_id, step_result in result.results.items():
            if not step_result.success and step_result.human_action:
                human_action = dict(step_result.human_action)
                failed_step_result = step_result
                failed_step_id = step_id
                break
        if human_action:
            awaiting_user = True
        details: dict[str, Any] = {
            "executor": "hybrid",
            "execution_mode": "task_runtime",
            "step_count": len(nodes),
            "extracted": dict(result.variables),
            "completed_idempotency_keys": completed_keys,
            "checkpoint": state.checkpoint(),
        }
        if awaiting_user:
            details["awaiting_user"] = True
        if human_action:
            details["human_action"] = human_action
        failure = None
        if not result.success:
            failure = {
                "error": result.error or result.message,
                "failure_category": classify_failure(
                    result.error or result.message
                ).value,
            }
            if failed_step_id is not None:
                failed_node = next(
                    (
                        node
                        for node in nodes
                        if node.step_id == failed_step_id
                    ),
                    None,
                )
                failure.update(
                    {
                        "step_index": (
                            failed_node.index
                            if failed_node is not None
                            else 0
                        ),
                        "step_id": failed_step_id,
                        "executor": (
                            failed_node.executor
                            if failed_node is not None
                            else "hybrid"
                        ),
                        "action": (
                            failed_node.action
                            if failed_node is not None
                            else ""
                        ),
                    }
                )
            if failed_step_result is not None:
                failure["verification"] = dict(
                    failed_step_result.verification
                )
                if human_action:
                    failure["human_action"] = human_action
                    failure["current_url"] = human_action.get(
                        "current_url"
                    )
                    failure["interaction_kind"] = human_action.get(
                        "kind"
                    )
            details["failed_step"] = failure
        output = [
            (
                f"{step_id}: {step_result.message}"
                if step_result.message
                else f"{step_id}: completed"
            )
            for step_id, step_result in result.results.items()
        ]
        return ExecutionResult(
            success=result.success,
            output="\n".join(output),
            error=result.error,
            screenshots=[],
            step_logs=logs,
            duration_seconds=time.perf_counter() - started_at,
            details=details,
            artifacts=list(result.artifacts or artifacts),
        )

    @staticmethod
    def _ordered_nodes(nodes: list[PlanStep]) -> list[PlanStep]:
        remaining = {node.step_id: node for node in nodes}
        ordered: list[PlanStep] = []
        completed: set[int] = set()
        while remaining:
            progressed = False
            for step_id, node in list(remaining.items()):
                if all(
                    dependency in completed
                    for dependency in node.depends_on
                ):
                    ordered.append(node)
                    completed.add(step_id)
                    remaining.pop(step_id)
                    progressed = True
            if not progressed:
                return list(nodes)
        return ordered

    @staticmethod
    def _declared_variables(plan: Any) -> list[Any]:
        if isinstance(plan, dict) and isinstance(
            plan.get("variables"),
            list,
        ):
            return list(plan["variables"])
        return []

    async def _execute_step(
        self,
        node: PlanStep,
        workflow: Any,
        request_context: dict[str, Any],
        *,
        page: Any | None,
        variables: dict[str, Any],
        extracted: dict[str, Any],
        completed_keys: list[str],
    ) -> _StepOutcome:
        if node.executor == "web":
            return await self._execute_web_step(
                node,
                request_context,
                page=page,
                variables=variables,
                extracted=extracted,
                completed_keys=completed_keys,
            )
        if node.executor == "windows":
            return await self._execute_windows_step(
                node,
                workflow,
                request_context,
                variables=variables,
                extracted=extracted,
                completed_keys=completed_keys,
            )
        if node.executor == "code":
            return await self._execute_code_step(
                node,
                request_context,
                variables=variables,
                extracted=extracted,
            )
        if node.executor == "mcp":
            return await self._execute_mcp_step(
                node,
                workflow,
                request_context,
                variables=variables,
                extracted=extracted,
            )
        return self._unavailable_outcome(
            node,
            f"Executor '{node.executor}' is not implemented yet",
        )

    async def _execute_web_step(
        self,
        node: PlanStep,
        request_context: dict[str, Any],
        *,
        page: Any | None,
        variables: dict[str, Any],
        extracted: dict[str, Any],
        completed_keys: list[str],
    ) -> _StepOutcome:
        executor = self._web_executor()
        if executor is None or page is None:
            return self._unavailable_outcome(
                node,
                "Web executor is not ready",
            )

        step_context = dict(request_context)
        step_context["variables"] = {
            **variables,
            **extracted,
        }
        step_context.pop("resume_from_step", None)
        result = await executor.execute_steps(
            [self._web_step_payload(node)],
            page=page,
            context=step_context,
            execution_id=self._execution_id(request_context),
            step_offset=node.index,
            completed_idempotency_keys=list(completed_keys),
        )
        return self._outcome_from_result(node, result)

    async def _execute_windows_step(
        self,
        node: PlanStep,
        workflow: Any,
        request_context: dict[str, Any],
        *,
        variables: dict[str, Any],
        extracted: dict[str, Any],
        completed_keys: list[str],
    ) -> _StepOutcome:
        dispatch = self._desktop_callable()
        if dispatch is None:
            return self._unavailable_outcome(
                node,
                "Desktop executor is not ready",
            )

        payload = dict(node.raw)
        payload["step_id"] = node.step_id
        payload["executor"] = "windows"
        payload["depends_on"] = []
        view = SimpleNamespace(
            id=getattr(workflow, "id", None),
            user_id=getattr(workflow, "user_id", None),
            name=getattr(workflow, "name", None),
            structured_plan={
                "executor_type": "windows",
                "steps": [payload],
            },
            executor_type="windows",
            required_capabilities=list(
                capabilities_for_actions([node.action])
            ),
            _hybrid_step_dispatch=True,
        )
        step_context = {
            key: value
            for key, value in request_context.items()
            if key
            not in {
                "resume_from_step",
                "next_step_index",
                "initial_step_logs",
            }
        }
        step_context["variables"] = {**variables, **extracted}
        step_context["step_offset"] = node.index
        step_context["completed_idempotency_keys"] = list(completed_keys)

        result = await dispatch(view, step_context)
        if not isinstance(result, ExecutionResult):
            error = "Desktop executor returned no result"
            return _StepOutcome(
                status="failed",
                logs=[self._base_log(node, status="failed")],
                output=[f"[{node.index + 1}] {node.action} {error}"],
                error=error,
                failure=self._failure_detail(node, error),
            )
        details = (
            result.details
            if isinstance(result.details, dict)
            else {}
        )
        host_id = details.get("host_id")
        if host_id is not None:
            request_context["host_id"] = host_id
            request_context["host_environment"] = details.get(
                "host_environment"
            )
            request_context["host_bound"] = True
            request_context["host_affinity_hash"] = details.get(
                "host_affinity_hash"
            )
        return self._outcome_from_result(node, result)

    async def _execute_code_step(
        self,
        node: PlanStep,
        request_context: dict[str, Any],
        *,
        variables: dict[str, Any],
        extracted: dict[str, Any],
    ) -> _StepOutcome:
        params = (
            node.raw.get("params")
            if isinstance(node.raw.get("params"), dict)
            else {}
        )
        code = params.get("code") or params.get("script")
        if not isinstance(code, str) or not code.strip():
            return self._unavailable_outcome(
                node,
                "code step requires params.code",
            )

        execute_code = getattr(self._web_engine(), "execute_code", None)
        if not callable(execute_code):
            return self._unavailable_outcome(
                node,
                "Code executor is not ready",
            )
        result = await execute_code(
            code,
            {
                **request_context,
                "variables": {**variables, **extracted},
            },
        )
        if not isinstance(result, ExecutionResult):
            error = "Code executor returned no result"
            return _StepOutcome(
                status="failed",
                logs=[self._base_log(node, status="failed")],
                output=[f"[{node.index + 1}] {node.action} {error}"],
                error=error,
                failure=self._failure_detail(node, error),
            )
        return self._outcome_from_result(node, result)

    async def _execute_mcp_step(
        self,
        node: PlanStep,
        workflow: Any,
        request_context: dict[str, Any],
        *,
        variables: dict[str, Any],
        extracted: dict[str, Any],
    ) -> _StepOutcome:
        """调用一个 MCP 工具，权限判断与审计都在注册表里完成。"""

        action = str(node.action or "").strip()
        if action not in _MCP_ACTIONS:
            return self._unavailable_outcome(
                node,
                f"不支持的 mcp 动作：{action}（当前只支持 call_tool）",
            )

        params = (
            node.raw.get("params")
            if isinstance(node.raw.get("params"), dict)
            else {}
        )
        server = (
            params.get("server")
            or params.get("server_id")
            or params.get("mcp_server")
        )
        tool = params.get("tool") or params.get("name")
        if not server or not tool:
            return self._unavailable_outcome(
                node,
                "mcp 步骤需要 params.server 与 params.tool",
            )

        registry = self._mcp_registry()
        if registry is None:
            return self._unavailable_outcome(
                node,
                "MCP 客户端未启用，请在「连接器与 MCP」页面打开开关",
            )

        arguments = params.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}
        result_path = params.get("result_path")
        if result_path is not None:
            try:
                validate_result_path(result_path)
            except ResultPathError as exc:
                error = f"无效的 result_path：{exc}"
                return _StepOutcome(
                    status="failed",
                    logs=[
                        self._mcp_log(
                            node,
                            status="failed",
                            error=error,
                            server=str(server),
                            tool=str(tool),
                        )
                    ],
                    output=[
                        f"[{node.index + 1}] mcp 参数错误：{error}"
                    ],
                    error=error,
                    failure=self._failure_detail(
                        node,
                        error,
                        category=FailureCategory.BUSINESS_ERROR.value,
                    ),
                )
        context = {
            "workflow_id": getattr(workflow, "id", None),
            "execution_id": self._execution_id(request_context),
            "user_id": getattr(workflow, "user_id", None),
            "workspace_id": getattr(workflow, "workspace_id", None),
            "credentials": request_context.get("credentials"),
            "step_id": node.step_id,
            "idempotency_key": node.idempotency_key,
        }
        timeout = _as_positive_float(
            params.get("timeout_seconds")
            if params.get("timeout_seconds") is not None
            else node.raw.get("timeout_seconds")
        )
        try:
            outcome = await registry.call_tool(
                server,
                tool,
                arguments,
                context=context,
                timeout=timeout,
                retry_policy=node.raw.get("retry_policy"),
                confirm=(
                    params.get("confirm")
                    if params.get("confirm") is not None
                    else node.raw.get("confirm")
                ),
            )
        except Exception as exc:  # noqa: BLE001
            error = str(exc) or exc.__class__.__name__
            return _StepOutcome(
                status="failed",
                logs=[
                    self._mcp_log(
                        node,
                        status="failed",
                        error=error,
                        server=str(server),
                        tool=str(tool),
                    )
                ],
                output=[
                    f"[{node.index + 1}] {node.action} 失败：{error}"
                ],
                error=error,
                failure=self._failure_detail(node, error),
            )

        details = outcome.to_step_details()
        log_details = {
            key: value
            for key, value in details.items()
            if key not in {"server", "tool"}
        }
        log = self._mcp_log(
            node,
            status="success" if outcome.success else "failed",
            error=outcome.error,
            server=outcome.server,
            tool=outcome.tool,
            **log_details,
        )
        result_text = ""
        if isinstance(outcome.result, dict):
            result_text = str(outcome.result.get("text") or "")
            log["untrusted"] = True
            log["result_summary"] = result_text[:500]

        if outcome.success:
            extracted_result: dict[str, Any] = {}
            try:
                result_value = extract_result_value(
                    outcome.result or {},
                    result_path if isinstance(result_path, str) else None,
                )
            except ResultPathError as exc:
                error = f"MCP result_path 无法解析：{exc}"
                failure = self._failure_detail(
                    node,
                    error,
                    category=FailureCategory.BUSINESS_ERROR.value,
                )
                failure["mcp"] = details
                log["status"] = "failed"
                log["error"] = error
                log["result_path"] = result_path
                return _StepOutcome(
                    status="failed",
                    logs=[log],
                    output=[
                        (
                            f"[{node.index + 1}] mcp "
                            f"{outcome.server}.{outcome.tool} 失败：{error}"
                        )
                    ],
                    error=error,
                    failure=failure,
                )
            result_var = params.get("result_var")
            if isinstance(result_var, str) and result_var.strip():
                extracted_result[result_var.strip()] = result_value
            artifacts = extract_mcp_artifacts(
                outcome.result or {},
                server=outcome.server,
                tool=outcome.tool,
            )
            log["artifact_count"] = len(artifacts)
            if isinstance(result_var, str) and result_var.strip():
                log["result_var"] = result_var.strip()
            if result_path is not None:
                log["result_path"] = result_path
            output = [
                (
                    f"[{node.index + 1}] mcp {outcome.server}."
                    f"{outcome.tool} 成功"
                    f"（{outcome.risk_level}，"
                    f"{outcome.duration_ms:.0f}ms，"
                    f"audit={outcome.audit_id}）"
                )
            ]
            if result_text:
                output.append(
                    f"[{node.index + 1}] <<<MCP_UNTRUSTED_RESULT "
                    f"server={outcome.server} tool={outcome.tool}\n"
                    f"{result_text[:1000]}\nMCP_UNTRUSTED_RESULT>>>"
                )
            return _StepOutcome(
                status="success",
                logs=[log],
                output=output,
                extracted=extracted_result,
                artifacts=artifacts,
                completed_keys=(
                    [node.idempotency_key]
                    if node.idempotency_key
                    else []
                ),
            )

        error = outcome.error or (
            f"mcp {outcome.server}.{outcome.tool} 调用失败"
        )
        failure = self._failure_detail(
            node,
            error,
            category=outcome.error_category.value,
        )
        failure["mcp"] = details
        if outcome.requires_confirmation:
            failure["requires_confirmation"] = True
        return _StepOutcome(
            status="failed",
            logs=[log],
            output=[
                (
                    f"[{node.index + 1}] mcp {outcome.server}."
                    f"{outcome.tool} 失败：{error}"
                )
            ],
            error=error,
            failure=failure,
        )

    def _mcp_log(
        self,
        node: PlanStep,
        *,
        status: str,
        error: str | None,
        server: str,
        tool: str,
        **extra: Any,
    ) -> dict[str, Any]:
        log = self._base_log(node, status=status, error=error)
        log["server"] = server
        log["tool"] = tool
        for key, value in extra.items():
            if value is not None:
                log[key] = value
        return log

    def _outcome_from_result(
        self,
        node: PlanStep,
        result: ExecutionResult,
    ) -> _StepOutcome:
        details = (
            dict(result.details)
            if isinstance(result.details, dict)
            else {}
        )
        logs = [
            self._normalize_log(node, entry)
            for entry in (result.step_logs or [])
            if isinstance(entry, dict)
        ]
        if not logs:
            logs = [
                self._base_log(
                    node,
                    status="success" if result.success else "failed",
                    error=result.error,
                )
            ]

        status = "success" if result.success else "failed"
        skipped_reason: str | None = None
        statuses = {
            str(entry.get("status") or "")
            for entry in logs
        }
        if statuses and statuses <= {"skipped", "repaired"}:
            if "repaired" in statuses and "skipped" not in statuses:
                status = "success"
            else:
                status = "skipped"
                skipped_reason = "step_skipped"

        failure: dict[str, Any] | None = None
        error = result.error
        if status == "failed":
            raw_failure = details.get("failed_step")
            if isinstance(raw_failure, dict):
                failure = {
                    **raw_failure,
                    "step_index": node.index,
                    "step_id": node.step_id,
                    "executor": node.executor,
                    "action": node.action,
                }
            else:
                failure = self._failure_detail(
                    node,
                    error or "step failed",
                    category=details.get("failure_category"),
                )

        extracted = details.get("extracted")
        if not isinstance(extracted, dict):
            extracted = {}

        output: list[str] = []
        if result.output:
            output = [
                line
                for line in str(result.output).splitlines()
                if line.strip()
            ]
        if not output and status == "success":
            output = [f"[{node.index + 1}] {node.action} 完成"]

        repaired_steps = None
        repaired_plan = details.get("repaired_plan")
        if isinstance(repaired_plan, dict):
            steps = repaired_plan.get("steps")
            if isinstance(steps, list) and steps:
                repaired_steps = [
                    dict(step)
                    for step in steps
                    if isinstance(step, dict)
                ]

        return _StepOutcome(
            status=status,
            logs=logs,
            output=output,
            screenshots=list(result.screenshots or []),
            artifacts=list(result.artifacts or []),
            error=(
                error
                if isinstance(error, str) and error
                else (
                    failure.get("error")
                    if isinstance(failure, dict)
                    else None
                )
            ),
            failure=failure,
            extracted=extracted,
            completed_keys=self._normalize_keys(
                details.get("completed_idempotency_keys")
            ),
            skipped_reason=skipped_reason,
            repaired_steps=repaired_steps,
            awaiting_user=bool(details.get("awaiting_user")),
        )

    def _apply_outcome(
        self,
        node: PlanStep,
        outcome: _StepOutcome,
        statuses: dict[int, str],
        logs_by_step: dict[int, list[dict[str, Any]]],
        skipped_steps: list[int],
        failures: list[dict[str, Any]],
        output: list[str],
        screenshots: list[str],
        artifacts: list[dict[str, Any]],
        repairs: list[dict[str, Any]],
        plan_steps: list[dict[str, Any]],
        completed_keys: list[str],
        extracted: dict[str, Any],
    ) -> None:
        if outcome.status not in {"success", "skipped", "failed"}:
            outcome.status = "failed"
        statuses[node.step_id] = outcome.status
        logs_by_step.setdefault(node.step_id, []).extend(outcome.logs)
        output.extend(outcome.output)
        for key in outcome.completed_keys:
            if key not in completed_keys:
                completed_keys.append(key)
        if outcome.extracted:
            extracted.update(outcome.extracted)

        for screenshot in outcome.screenshots:
            if screenshot not in screenshots:
                screenshots.append(screenshot)
        for artifact in outcome.artifacts:
            if isinstance(artifact, dict):
                artifacts.append(artifact)

        if outcome.status == "skipped":
            if node.step_id not in skipped_steps:
                skipped_steps.append(node.step_id)
        elif outcome.status == "failed":
            failure = outcome.failure or self._failure_detail(
                node,
                outcome.error or "step failed",
            )
            failure.setdefault("step_index", node.index)
            failure.setdefault("step_id", node.step_id)
            failure.setdefault("executor", node.executor)
            failure.setdefault("action", node.action)
            failures.append(failure)

        if outcome.repaired_steps:
            plan_steps[node.index] = {
                **plan_steps[node.index],
                **outcome.repaired_steps[0],
                "step_id": plan_steps[node.index].get(
                    "step_id",
                    node.step_id,
                ),
                "depends_on": list(node.depends_on),
            }
            repairs.append(
                {
                    "step_index": node.index,
                    "step_id": node.step_id,
                    "executor": node.executor,
                    "action": node.action,
                    "repaired_step": outcome.repaired_steps[0],
                }
            )

    def _record_condition_skip(
        self,
        node: PlanStep,
        statuses: dict[int, str],
        logs_by_step: dict[int, list[dict[str, Any]]],
        skipped_steps: list[int],
        output: list[str],
        *,
        reason: str,
        message: str,
    ) -> None:
        statuses[node.step_id] = (
            "dependency_failed"
            if reason in {"dependency_failed", "dependency_blocked"}
            else "skipped"
        )
        logs_by_step.setdefault(node.step_id, []).append(
            self._skip_log(node, message=message, reason=reason)
        )
        if node.step_id not in skipped_steps:
            skipped_steps.append(node.step_id)
        output.append(
            f"[{node.index + 1}] {node.action} 已跳过：{message}"
        )

    async def _persist_progress(
        self,
        execution_id: int | None,
        step_logs: list[dict[str, Any]],
        screenshots: list[str],
    ) -> None:
        if execution_id is None:
            return
        async with self._progress_lock:
            try:
                async with self.session_factory() as session:
                    execution_log = await session.get(
                        ExecutionLog,
                        execution_id,
                    )
                    if execution_log is None or execution_log.status in {
                        "paused",
                        "cancelled",
                    }:
                        return
                    execution_log.step_logs = [
                        dict(entry) for entry in step_logs
                    ]
                    merged = list(execution_log.screenshots or [])
                    for screenshot in screenshots:
                        if screenshot not in merged:
                            merged.append(screenshot)
                    execution_log.screenshots = merged
                    await session.commit()
            except Exception:
                logger.debug(
                    "写入混合执行进度失败（execution=%s）",
                    execution_id,
                    exc_info=True,
                )

    def _web_executor(self) -> Any | None:
        return getattr(self._web_engine(), "web_executor", None)

    def _web_engine(self) -> Any:
        """ExecutionRouter 会把真正的网页引擎放在 web_engine 上。"""

        return getattr(self.engine, "web_engine", self.engine)

    def _desktop_callable(self) -> Callable[..., Any] | None:
        desktop = self.desktop_executor
        if desktop is None:
            desktop = getattr(self.engine, "desktop_dispatcher", None)
        if desktop is None:
            return None

        # 优先使用桌面修复协调器，单步失败同样享受自动修复。
        execute = getattr(desktop, "execute", None)
        if callable(execute):
            return execute

        dispatcher = getattr(desktop, "dispatcher", None) or desktop
        dispatch = getattr(dispatcher, "dispatch", None)
        if callable(dispatch):
            return dispatch
        return None

    def _mcp_registry(self) -> Any | None:
        if self.mcp_registry is not None:
            return self.mcp_registry
        for candidate in (
            self.engine,
            getattr(self.engine, "web_engine", None),
        ):
            registry = getattr(candidate, "mcp_registry", None)
            if registry is not None:
                self.mcp_registry = registry
                return registry
        config = self.config
        if config is None:
            try:
                config = get_settings()
            except Exception:  # noqa: BLE001
                return None
        if not runtime_flags.resolve("mcp_client", config):
            return None
        self.mcp_registry = MCPRegistry(
            session_factory=self.session_factory,
            config=config,
        )
        return self.mcp_registry

    @staticmethod
    def _raw_steps(workflow: Any) -> list[dict[str, Any]]:
        plan = getattr(workflow, "structured_plan", None)
        if not isinstance(plan, dict):
            return []
        steps = plan.get("steps")
        if not isinstance(steps, list):
            return []
        return [dict(step) for step in steps if isinstance(step, dict)]

    @staticmethod
    def _web_step_payload(node: PlanStep) -> dict[str, Any]:
        payload = dict(node.raw)
        action = str(
            payload.get("action") or payload.get("type") or ""
        ).strip()
        payload["action"] = _ACTION_ALIASES.get(action, action)
        payload["step_id"] = node.step_id
        payload["executor"] = "web"
        payload["depends_on"] = []
        params = payload.get("params")
        if not isinstance(params, dict):
            payload["params"] = {}
        return payload

    @staticmethod
    def _resource_key(node: PlanStep) -> str:
        if node.executor == "windows":
            return "desktop"
        if node.executor == "web":
            return "web"
        if node.executor == "code":
            return "code"
        params = node.raw.get("params")
        server = ""
        if isinstance(params, dict):
            server = str(
                params.get("server")
                or params.get("server_id")
                or params.get("mcp_server")
                or ""
            ).strip()
        return f"mcp:{server or 'default'}"

    @staticmethod
    def _blocking_dependency(
        node: PlanStep,
        statuses: dict[int, str],
    ) -> int | None:
        for dependency in node.depends_on:
            if statuses.get(dependency) in DEPENDENCY_BLOCKED_STATUSES:
                return dependency
        return None

    @staticmethod
    def _dependencies_ready(
        node: PlanStep,
        statuses: dict[int, str],
    ) -> bool:
        return all(
            statuses.get(dependency)
            in DEPENDENCY_SATISFIED_STATUSES
            for dependency in node.depends_on
        )

    def _prior_statuses(
        self,
        step_logs: Any,
        nodes: list[PlanStep],
    ) -> dict[int, str]:
        """从上次执行的日志里恢复每个步骤的终态。"""

        if not isinstance(step_logs, list):
            return {}
        by_index = {node.index: node.step_id for node in nodes}
        statuses: dict[int, str] = {}
        for entry in step_logs:
            if not isinstance(entry, dict):
                continue
            if entry.get("type") == "checkpoint":
                continue
            status = str(entry.get("status") or "")
            if status not in {
                "success",
                "skipped",
                "repaired",
                "failed",
            }:
                continue
            step_id = self._coerce_step_id(entry.get("step_id"))
            if step_id is None:
                try:
                    step_index = int(entry.get("step_index"))
                except (TypeError, ValueError):
                    continue
                step_id = by_index.get(step_index)
            if step_id is None:
                continue
            if status == "failed":
                continue
            statuses[step_id] = (
                "success" if status == "repaired" else status
            )
        return statuses

    @staticmethod
    def _coerce_step_id(value: Any) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
        return None

    @staticmethod
    def _execution_id(context: dict[str, Any]) -> int | None:
        value = context.get("execution_log_id")
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _skip_log(
        self,
        node: PlanStep,
        *,
        message: str,
        reason: str,
    ) -> dict[str, Any]:
        entry = self._base_log(node, status="skipped", message=message)
        entry["skip_reason"] = reason
        return entry

    @staticmethod
    def _base_log(
        node: PlanStep,
        *,
        status: str,
        message: str | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        now = time.perf_counter()
        params = (
            node.raw.get("params")
            if isinstance(node.raw.get("params"), dict)
            else {}
        )
        target = (
            params.get("url")
            or params.get("name")
            or params.get("text")
            or params.get("path")
            or node.action
        )
        entry: dict[str, Any] = {
            "step_index": node.index,
            "step_id": node.step_id,
            "executor": node.executor,
            "action": node.action,
            "target": str(target),
            "status": status,
            "attempts": 0,
            "retried": False,
            "error": error,
            "failure_category": None,
            "screenshot_path": None,
            "started_at": now,
            "finished_at": now,
        }
        if message is not None:
            entry["message"] = message
        if node.idempotency_key is not None:
            entry["idempotency_key"] = node.idempotency_key
        return entry

    def _normalize_log(
        self,
        node: PlanStep,
        entry: dict[str, Any],
    ) -> dict[str, Any]:
        log = dict(entry)
        log.setdefault("step_id", node.step_id)
        log.setdefault("executor", node.executor)
        log.setdefault("action", node.action)
        index = log.get("step_index")
        try:
            index_int = int(index)
        except (TypeError, ValueError):
            index_int = node.index
        # 单步派发时有些执行器把序号写成 0，这里还原成完整流程里的位置。
        if index_int == 0 and node.index != 0:
            index_int = node.index
        log["step_index"] = index_int
        if node.idempotency_key is not None:
            log.setdefault("idempotency_key", node.idempotency_key)
        return log

    @staticmethod
    def _failure_detail(
        node: PlanStep,
        error: str,
        *,
        category: Any = None,
    ) -> dict[str, Any]:
        resolved = (
            str(category).strip()
            if isinstance(category, str) and category.strip()
            else classify_failure(error).value
        )
        return {
            "step_index": node.index,
            "step_id": node.step_id,
            "executor": node.executor,
            "action": node.action,
            "error": error,
            "failure_category": resolved,
            "attempts": 1,
        }

    def _unavailable_outcome(
        self,
        node: PlanStep,
        error: str,
    ) -> _StepOutcome:
        return _StepOutcome(
            status="failed",
            logs=[
                self._base_log(
                    node,
                    status="failed",
                    error=error,
                )
            ],
            output=[f"[{node.index + 1}] {node.action} 失败：{error}"],
            error=error,
            failure=self._failure_detail(node, error),
        )

    @staticmethod
    def _ordered_logs(
        nodes: list[PlanStep],
        logs_by_step: dict[int, list[dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        ordered: list[dict[str, Any]] = []
        seen: set[int] = set()
        for node in nodes:
            entries = logs_by_step.get(node.step_id)
            if entries:
                ordered.extend(dict(entry) for entry in entries)
            seen.add(node.step_id)
        for step_id, entries in logs_by_step.items():
            if step_id in seen:
                continue
            ordered.extend(dict(entry) for entry in entries)
        return ordered

    @staticmethod
    def _normalize_keys(values: Any) -> list[str]:
        if not isinstance(values, (list, tuple, set)):
            return []
        normalized: list[str] = []
        for value in values:
            text = str(value or "").strip()
            if text and text not in normalized:
                normalized.append(text)
        return normalized

    @staticmethod
    def _failure_result(
        error: str,
        started_at: float,
    ) -> ExecutionResult:
        return ExecutionResult(
            success=False,
            output="",
            error=error,
            screenshots=[],
            step_logs=[],
            duration_seconds=time.perf_counter() - started_at,
            details={"executor": "hybrid"},
        )


__all__ = ["MAX_PARALLEL_STEPS", "HybridExecutor"]
