"""TaskGraph v2 的共享变量、步骤结果与执行入口。

运行时只解析只读变量路径，不执行表达式或模型生成的代码。旧版计划
没有 v2 字段时仍按原有顺序执行，因此调用方可以逐步迁移。
"""

from __future__ import annotations

import inspect
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from app.engine.dag import evaluate_condition
from app.actions.registry import ActionRegistry
from app.actions.protocol import StepStatus
from app.actions.runtime import ActionContext
from app.actions.variables import resolve_template


@dataclass
class StepResult:
    success: bool
    message: str = ""
    text: str | None = None
    value: Any = None
    items: list[Any] | None = None
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)
    verification: dict[str, Any] = field(default_factory=dict)
    human_action: dict[str, Any] | None = None

    def result_value(self) -> Any:
        if self.text is not None:
            return self.text
        if self.value is not None:
            return self.value
        if self.items is not None:
            return self.items
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "message": self.message,
            "text": self.text,
            "value": self.value,
            "items": self.items,
            "artifacts": list(self.artifacts),
            "data": dict(self.data),
            "verification": dict(self.verification),
            "human_action": (
                dict(self.human_action)
                if isinstance(self.human_action, dict)
                else None
            ),
        }


@dataclass
class RuntimeState:
    variables: dict[str, Any] = field(default_factory=dict)
    results: dict[int, StepResult] = field(default_factory=dict)
    completed_steps: list[int] = field(default_factory=list)
    loop_cursors: dict[str, int] = field(default_factory=dict)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    execution_id: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "variables": self.variables,
            "results": self.results,
            "completed_steps": self.completed_steps,
            "loop_cursors": self.loop_cursors,
            "artifacts": self.artifacts,
            "execution_id": self.execution_id,
        }

    def checkpoint(self) -> dict[str, Any]:
        return {
            "variables": dict(self.variables),
            "results": {
                str(step_id): result.to_dict()
                for step_id, result in self.results.items()
            },
            "completed_steps": list(self.completed_steps),
            "loop_cursors": dict(self.loop_cursors),
            "artifacts": [dict(item) for item in self.artifacts],
            "execution_id": self.execution_id,
        }

    @classmethod
    def from_checkpoint(cls, payload: dict[str, Any]) -> RuntimeState:
        results: dict[int, StepResult] = {}
        raw_results = payload.get("results")
        if isinstance(raw_results, dict):
            for key, value in raw_results.items():
                if not isinstance(value, dict):
                    continue
                try:
                    step_id = int(key)
                except (TypeError, ValueError):
                    continue
                results[step_id] = StepResult(
                    success=bool(value.get("success", True)),
                    message=str(value.get("message") or ""),
                    text=value.get("text"),
                    value=value.get("value"),
                    items=value.get("items"),
                    artifacts=[
                        dict(item)
                        for item in value.get("artifacts", [])
                        if isinstance(item, dict)
                    ],
                    data=dict(value.get("data") or {}),
                    verification=dict(
                        value.get("verification") or {}
                    ),
                    human_action=(
                        dict(value["human_action"])
                        if isinstance(value.get("human_action"), dict)
                        else None
                    ),
                )
        return cls(
            variables=dict(payload.get("variables") or {}),
            results=results,
            completed_steps=[
                int(item)
                for item in payload.get("completed_steps", [])
                if isinstance(item, int)
            ],
            loop_cursors={
                str(key): int(value)
                for key, value in (
                    payload.get("loop_cursors") or {}
                ).items()
            },
            artifacts=[
                dict(item)
                for item in payload.get("artifacts", [])
                if isinstance(item, dict)
            ],
            execution_id=(
                int(payload["execution_id"])
                if payload.get("execution_id") is not None
                else None
            ),
        )


@dataclass
class VerificationResult:
    passed: bool
    checks: list[dict[str, Any]]

    @property
    def failed_checks(self) -> list[dict[str, Any]]:
        return [
            item for item in self.checks if not item.get("passed")
        ]


async def execute_foreach(
    step: dict[str, Any],
    state: dict[str, Any] | RuntimeState,
    execute_step: Callable[
        [dict[str, Any], dict[str, Any]],
        Awaitable[StepResult | dict[str, Any]],
    ],
) -> list[Any]:
    """执行有界循环，并在失败或预算耗尽时保留已聚合结果。"""

    runtime = state if isinstance(state, RuntimeState) else None
    runtime_dict = state.as_dict() if runtime is not None else state
    variables = runtime_dict.setdefault("variables", {})
    if not isinstance(variables, dict):
        raise ValueError("runtime variables must be an object")
    if runtime is not None and runtime.variables is not variables:
        runtime.variables = variables

    params = step.get("params")
    if not isinstance(params, dict):
        params = {}
    if isinstance(step.get("foreach"), dict):
        params = {**step["foreach"], **params}

    items = resolve_template(params.get("items"), variables)
    if not isinstance(items, list):
        raise ValueError("foreach items must resolve to a list")

    max_iterations = _bounded_int(
        params.get("max_iterations", 100),
        default=100,
        minimum=0,
    )
    max_steps = _bounded_int(
        params.get("max_steps", 1000),
        default=1000,
        minimum=0,
    )
    max_seconds = _positive_float(params.get("max_seconds"))
    started_at = time.perf_counter()
    item_var = str(params.get("item_var") or "item").strip() or "item"
    child_steps = params.get("steps")
    if not isinstance(child_steps, list):
        child_steps = list(params.get("children") or [])

    output_var = str(
        step.get("output_var") or params.get("output_var") or "output"
    ).strip() or "output"
    aggregated: list[Any] = []
    step_count = int(variables.get("_step_count") or 0)
    completed_iterations = 0
    stop = False

    try:
        for iteration, item in enumerate(items[:max_iterations]):
            if step_count >= max_steps:
                break
            if (
                max_seconds is not None
                and time.perf_counter() - started_at >= max_seconds
            ):
                break

            variables[item_var] = item
            last_result: StepResult | None = None
            failed = False
            for child in child_steps:
                if step_count >= max_steps:
                    stop = True
                    break
                if (
                    max_seconds is not None
                    and time.perf_counter() - started_at >= max_seconds
                ):
                    stop = True
                    break
                if not isinstance(child, dict):
                    raise ValueError(
                        "foreach child steps must be objects"
                    )
                try:
                    resolved_child = resolve_template(child, variables)
                    raw_result = await execute_step(
                        resolved_child,
                        runtime_dict,
                    )
                    result = TaskRuntime._coerce_result(raw_result)
                except Exception:
                    stop = True
                    failed = True
                    break
                step_count += 1
                last_result = result
                child_var = str(
                    child.get("result_var") or ""
                ).strip()
                if child_var:
                    variables[child_var] = result.result_value()
                variables.update(result.data.get("variables", {}))
                if not result.success:
                    stop = True
                    failed = True
                    break

            if last_result is not None:
                iteration_value = last_result.result_value()
                if isinstance(iteration_value, list):
                    aggregated.extend(iteration_value)
                elif iteration_value is not None:
                    aggregated.append(iteration_value)
            if failed or stop:
                break
            completed_iterations = iteration + 1
            runtime_dict.setdefault("loop_cursors", {})[output_var] = (
                completed_iterations
            )
    finally:
        variables["_step_count"] = step_count
        runtime_dict.setdefault("loop_cursors", {})[output_var] = (
            completed_iterations
        )
        variables[output_var] = aggregated
        if runtime is not None:
            runtime.loop_cursors[output_var] = completed_iterations

    return aggregated


def _bounded_int(value: Any, *, default: int, minimum: int) -> int:
    try:
        return max(int(value), minimum)
    except (TypeError, ValueError):
        return default


def _positive_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def evaluate_postconditions(
    checks: list[dict[str, Any]],
    state: dict[str, Any] | RuntimeState,
) -> VerificationResult:
    """执行确定性条件检查，未知条件一律判为失败。"""

    if isinstance(state, RuntimeState):
        runtime = state.as_dict()
    elif isinstance(state, dict):
        runtime = state
    else:
        runtime = {}
    variables = runtime.get("variables")
    if not isinstance(variables, dict):
        variables = {}

    results: list[dict[str, Any]] = []
    for raw_check in checks:
        if not isinstance(raw_check, dict):
            results.append(
                {
                    "type": "<invalid>",
                    "passed": False,
                    "error": "check must be an object",
                }
            )
            continue
        check = raw_check
        check_type = str(check.get("type") or "").strip()
        entry: dict[str, Any] = {"type": check_type}
        try:
            passed = _evaluate_check(check, runtime, variables)
        except Exception as exc:
            passed = False
            entry["error"] = str(exc) or exc.__class__.__name__
        entry["passed"] = passed
        results.append(entry)

    return VerificationResult(
        passed=all(
            item.get("passed") is True for item in results
        ),
        checks=results,
    )


def _evaluate_check(
    check: dict[str, Any],
    state: dict[str, Any],
    variables: dict[str, Any],
) -> bool:
    check_type = str(check.get("type") or "").strip().lower()
    if check_type == "text_non_empty":
        value = resolve_template(check.get("value"), variables)
        return bool(str(value or "").strip())
    if check_type == "text_not_equals":
        value = str(
            resolve_template(check.get("value"), variables) or ""
        ).strip()
        other = str(
            resolve_template(check.get("other"), variables) or ""
        ).strip()
        return bool(value) and value != other
    if check_type == "list_min_length":
        value = resolve_template(check.get("value"), variables)
        minimum = _minimum(check)
        return isinstance(value, list) and len(value) >= minimum
    if check_type == "row_count_min":
        value = resolve_template(check.get("value"), variables)
        minimum = _minimum(check)
        return (
            isinstance(value, (list, tuple, dict))
            and len(value) >= minimum
        )
    if check_type == "file_exists":
        path = Path(
            str(resolve_template(check.get("path"), variables))
        )
        return path.is_file()
    if check_type == "file_non_empty":
        path = Path(
            str(resolve_template(check.get("path"), variables))
        )
        return path.is_file() and path.stat().st_size > 0
    if check_type == "url_contains":
        expected = str(
            resolve_template(check.get("value"), variables) or ""
        )
        actual = str(
            state.get("url")
            or state.get("current_url")
            or variables.get("url")
            or variables.get("current_url")
            or ""
        )
        return bool(expected) and expected in actual
    if check_type == "page_title_contains":
        expected = str(
            resolve_template(check.get("value"), variables) or ""
        )
        actual = str(
            state.get("title")
            or state.get("page_title")
            or state.get("current_title")
            or variables.get("page_title")
            or variables.get("title")
            or ""
        )
        return bool(expected) and expected in actual
    if check_type == "window_visible":
        visible = state.get("window_visible")
        window = state.get("window")
        if isinstance(window, dict) and "visible" in window:
            visible = window.get("visible")
        if visible is None:
            visible = variables.get("window_visible")
        if visible is False:
            return False
        expected_title = str(
            resolve_template(check.get("title"), variables) or ""
        )
        actual_title = str(
            (window or {}).get("title")
            if isinstance(window, dict)
            else variables.get("window_title") or ""
        )
        return visible is True and (
            not expected_title or expected_title in actual_title
        )
    if check_type == "element_visible":
        visible = state.get("element_visible")
        element = state.get("element")
        if isinstance(element, dict) and "visible" in element:
            visible = element.get("visible")
        if visible is None:
            visible = variables.get("element_visible")
        return visible is True
    if check_type == "clipboard_format":
        requested = str(check.get("format") or "text").strip().lower()
        formats = state.get("clipboard_formats")
        if not isinstance(formats, (list, tuple, set)):
            formats = variables.get("clipboard_formats")
        if isinstance(formats, (list, tuple, set)):
            return requested in {
                str(item).strip().lower() for item in formats
            }
        text = str(
            state.get("clipboard_text")
            or variables.get("clipboard_text")
            or ""
        )
        return requested in {"text", "unicode", "unicodetext"} and bool(
            text
        )
    return False


def _minimum(check: dict[str, Any]) -> int:
    try:
        return max(int(check.get("min", 1)), 0)
    except (TypeError, ValueError):
        return 1


@dataclass
class TaskRunResult:
    success: bool
    message: str = ""
    variables: dict[str, Any] = field(default_factory=dict)
    results: dict[int, StepResult] = field(default_factory=dict)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


StepExecutorCallback = Callable[
    [dict[str, Any], dict[str, Any]],
    StepResult | dict[str, Any] | Awaitable[StepResult | dict[str, Any]],
]


class TaskRuntime:
    """执行结构化步骤，并在步骤之间传递共享变量和产物。"""

    def __init__(
        self,
        *,
        step_executor: StepExecutorCallback | None = None,
        action_registry: ActionRegistry | None = None,
        action_services: dict[str, Any] | None = None,
    ) -> None:
        self.step_executor = step_executor
        self.action_registry = action_registry
        self.action_services = dict(action_services or {})

    async def execute(
        self,
        workflow: Any,
        context: dict[str, Any] | None = None,
    ) -> TaskRunResult:
        plan = self._plan(workflow)
        raw_steps = plan.get("steps")
        if not isinstance(raw_steps, list) or not raw_steps:
            return TaskRunResult(
                success=False,
                error="Workflow has no structured steps",
            )

        request_context = dict(context or {})
        variables = self._seed_variables(
            plan.get("variables"),
            request_context.get("variables"),
        )
        runtime_state = RuntimeState(
            variables=variables,
            execution_id=(
                int(request_context["execution_id"])
                if request_context.get("execution_id") is not None
                else None
            ),
        )
        step_results: dict[int, StepResult] = {}
        artifacts: list[dict[str, Any]] = []

        for index, raw_step in enumerate(raw_steps):
            if not isinstance(raw_step, dict):
                return self._failed_run(
                    variables,
                    step_results,
                    artifacts,
                    f"步骤 {index + 1} 必须是 JSON 对象",
                )
            step_id = self._step_id(raw_step, index)
            if not evaluate_condition(
                raw_step.get("condition"),
                variables=variables,
            ):
                step_results[step_id] = StepResult(
                    success=True,
                    message="condition_false",
                    data={"skipped": True},
                )
                runtime_state.results[step_id] = step_results[step_id]
                runtime_state.completed_steps.append(step_id)
                continue

            try:
                if _is_foreach_step(raw_step):
                    result = await self._run_foreach(
                        raw_step,
                        runtime_state,
                    )
                    resolved_step = dict(raw_step)
                else:
                    raw_preconditions = raw_step.get("preconditions")
                    precondition_checks = resolve_template(
                        (
                            raw_preconditions
                            if isinstance(raw_preconditions, list)
                            else []
                        ),
                        variables,
                    )
                    resolved_step = resolve_template(
                        {
                            key: value
                            for key, value in raw_step.items()
                            if key
                            not in {
                                "preconditions",
                                "postconditions",
                                "verify",
                            }
                        },
                        variables,
                    )
                    precondition_result = evaluate_postconditions(
                        precondition_checks,
                        runtime_state,
                    )
                    if not precondition_result.passed:
                        raise RuntimeError(
                            "verification_failed: "
                            + _verification_message(precondition_result)
                        )
                    result = await self._run_step(
                        resolved_step,
                        runtime_state.as_dict(),
                    )
            except Exception as exc:
                return self._failed_run(
                    variables,
                    step_results,
                    artifacts,
                    str(exc) or exc.__class__.__name__,
                    step_id=step_id,
                )

            step_results[step_id] = result
            runtime_state.results[step_id] = result
            result_variables = result.data.get("variables")
            if isinstance(result_variables, dict):
                variables.update(result_variables)
            result_var = str(
                raw_step.get("result_var") or ""
            ).strip()
            if result_var:
                variables[result_var] = result.result_value()
            postcondition_checks = [
                *(
                    raw_step.get("postconditions")
                    if isinstance(
                        raw_step.get("postconditions"),
                        list,
                    )
                    else []
                ),
                *(
                    raw_step.get("verify")
                    if isinstance(raw_step.get("verify"), list)
                    else []
                ),
            ]
            postconditions = evaluate_postconditions(
                postcondition_checks,
                runtime_state,
            )
            result.verification = {
                **dict(result.verification),
                "passed": postconditions.passed,
                "checks": postconditions.checks,
            }
            if not postconditions.passed:
                return self._failed_run(
                    variables,
                    step_results,
                    artifacts,
                    "verification_failed: "
                    + _verification_message(postconditions),
                    step_id=step_id,
                )
            for artifact in result.artifacts:
                if isinstance(artifact, dict) and artifact not in artifacts:
                    artifacts.append(artifact)
                    runtime_state.artifacts.append(artifact)
            if not result.success:
                return self._failed_run(
                    variables,
                    step_results,
                    artifacts,
                    result.message or "step failed",
                    step_id=step_id,
                )
            if step_id not in runtime_state.completed_steps:
                runtime_state.completed_steps.append(step_id)

        return TaskRunResult(
            success=True,
            message="completed",
            variables=variables,
            results=step_results,
            artifacts=runtime_state.artifacts,
        )

    async def _run_step(
        self,
        step: dict[str, Any],
        state: dict[str, Any],
    ) -> StepResult:
        executor = str(step.get("executor") or "").strip().lower()
        action = str(step.get("action") or "").strip()
        if self.action_registry is not None:
            try:
                registered = self.action_registry.resolve(
                    executor,
                    action,
                )
            except KeyError as exc:
                if executor == "files" and action in {
                    "create_spreadsheet",
                    "create_document",
                }:
                    registered = None
                else:
                    return StepResult(success=False, message=str(exc))
            if (
                registered is not None
                and
                registered.definition.legacy_only
                and executor not in {"transform", "files"}
            ):
                registered = None
            params = step.get("params")
            if registered is not None:
                validation = self.action_registry.validate(
                    registered.definition.executor,
                    registered.definition.id,
                    params if isinstance(params, dict) else {},
                )
                if not validation.valid:
                    return StepResult(
                        success=False,
                        message=validation.message,
                    )
            if executor in {"transform", "files", "email"} and registered is not None:
                context = ActionContext(
                    workflow=None,
                    execution_id=(
                        state.get("execution_id")
                        if isinstance(state.get("execution_id"), int)
                        else None
                    ),
                    step_id=(
                        int(step["step_id"])
                        if step.get("step_id") is not None
                        else None
                    ),
                    variables=(
                        state.get("variables")
                        if isinstance(state.get("variables"), dict)
                        else {}
                    ),
                    registry=self.action_registry,
                    action=registered.definition.id,
                    services=dict(self.action_services),
                )
                try:
                    protocol_result = await self.action_registry.execute(
                        registered.definition.executor,
                        registered.definition.id,
                        params if isinstance(params, dict) else {},
                        context=context,
                    )
                except Exception as exc:
                    return StepResult(
                        success=False,
                        message=str(exc) or exc.__class__.__name__,
                    )
                return StepResult(
                    success=protocol_result.status
                    in {StepStatus.SUCCESS, StepStatus.WARNING},
                    message=protocol_result.message,
                    value=protocol_result.value,
                    items=(
                        protocol_result.value
                        if isinstance(protocol_result.value, list)
                        else None
                    ),
                    artifacts=list(protocol_result.artifacts),
                    data={"variables": dict(protocol_result.variables)},
                    verification=dict(protocol_result.verification),
                    human_action=(
                        dict(protocol_result.human_action)
                        if isinstance(protocol_result.human_action, dict)
                        else None
                    ),
                )
        if executor == "transform" and action:
            from app.engine.transforms import (
                TRANSFORM_ACTIONS,
                apply_transform,
            )

            if action in TRANSFORM_ACTIONS:
                value = apply_transform(
                    action,
                    (
                        step.get("params")
                        if isinstance(step.get("params"), dict)
                        else {}
                    ),
                    (
                        state.get("variables")
                        if isinstance(state.get("variables"), dict)
                        else {}
                    ),
                )
                return StepResult(
                    success=True,
                    message=f"transform {action} completed",
                    value=value,
                    items=value if isinstance(value, list) else None,
                )
        if self.step_executor is None:
            return StepResult(
                success=False,
                message=(
                    f"Executor '{step.get('executor') or 'web'}' "
                    "is not configured"
                ),
            )
        outcome = self.step_executor(step, state)
        if inspect.isawaitable(outcome):
            outcome = await outcome
        return self._coerce_result(outcome)

    async def _run_foreach(
        self,
        step: dict[str, Any],
        state: RuntimeState,
    ) -> StepResult:
        params = step.get("params")
        if not isinstance(params, dict):
            params = {}
        if isinstance(step.get("foreach"), dict):
            params = {**step["foreach"], **params}
        prepared = dict(step)
        prepared["params"] = {
            **params,
            "items": resolve_template(
                params.get("items"),
                state.variables,
            ),
        }
        output_var = str(
            step.get("output_var") or "output"
        ).strip() or "output"
        aggregated = await execute_foreach(
            prepared,
            state,
            self._run_step,
        )
        return StepResult(
            success=True,
            message=f"foreach completed: {len(aggregated)}",
            items=aggregated,
            value=aggregated,
            data={"variables": {output_var: aggregated}},
        )

    @staticmethod
    def _coerce_result(value: Any) -> StepResult:
        if isinstance(value, StepResult):
            return value
        if isinstance(value, dict):
            return StepResult(
                success=bool(value.get("success", True)),
                message=str(value.get("message") or ""),
                text=value.get("text"),
                value=value.get("value"),
                items=(
                    list(value["items"])
                    if isinstance(value.get("items"), list)
                    else value.get("items")
                ),
                artifacts=[
                    dict(item)
                    for item in value.get("artifacts", [])
                    if isinstance(item, dict)
                ],
                data=dict(value.get("data") or {}),
                verification=dict(value.get("verification") or {}),
                human_action=(
                    dict(value["human_action"])
                    if isinstance(value.get("human_action"), dict)
                    else None
                ),
            )
        raise TypeError("Step executor must return StepResult or dict")

    @staticmethod
    def _seed_variables(
        declared: Any,
        provided: Any,
    ) -> dict[str, Any]:
        variables: dict[str, Any] = {}
        if isinstance(declared, list):
            for entry in declared:
                if isinstance(entry, dict):
                    name = str(entry.get("name") or "").strip()
                    if name:
                        variables[name] = entry.get("default")
                elif isinstance(entry, str) and entry.strip():
                    variables[entry.strip()] = None
        elif isinstance(declared, dict):
            variables.update(declared)
        if isinstance(provided, dict):
            variables.update(provided)
        elif isinstance(provided, list):
            for entry in provided:
                if not isinstance(entry, dict):
                    continue
                name = str(entry.get("name") or "").strip()
                if name:
                    variables[name] = entry.get("default")
        return variables

    @staticmethod
    def _plan(workflow: Any) -> dict[str, Any]:
        if isinstance(workflow, dict):
            return workflow
        plan = getattr(workflow, "structured_plan", None)
        return plan if isinstance(plan, dict) else {}

    @staticmethod
    def _step_id(step: dict[str, Any], index: int) -> int:
        try:
            value = int(step.get("step_id", index + 1))
        except (TypeError, ValueError):
            return index + 1
        return value if value > 0 else index + 1

    @staticmethod
    def _failed_run(
        variables: dict[str, Any],
        results: dict[int, StepResult],
        artifacts: list[dict[str, Any]],
        error: str,
        *,
        step_id: int | None = None,
    ) -> TaskRunResult:
        failed = StepResult(success=False, message=error)
        if step_id is not None:
            results[step_id] = failed
        return TaskRunResult(
            success=False,
            message=error,
            variables=variables,
            results=results,
            artifacts=artifacts,
            error=error,
        )


def _verification_message(result: VerificationResult) -> str:
    failed = result.failed_checks
    if not failed:
        return "no checks configured"
    return ", ".join(
        str(item.get("type") or "unknown") for item in failed
    )


def _is_foreach_step(step: dict[str, Any]) -> bool:
    action = str(step.get("action") or "").strip().lower()
    return action == "foreach" or isinstance(step.get("foreach"), dict)


__all__ = [
    "StepExecutorCallback",
    "StepResult",
    "RuntimeState",
    "TaskRunResult",
    "TaskRuntime",
    "VerificationResult",
    "execute_foreach",
    "evaluate_postconditions",
    "resolve_template",
]
