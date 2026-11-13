"""执行计划 DAG：步骤依赖解析、拓扑约束与条件求值。

混合执行器用这里的纯函数把 ``structured_plan`` 与 ``dag_definition``
合并成一张可执行的步骤图：依赖来自步骤自身的 ``depends_on``，当步骤
没有声明依赖时再回退到 ``dag_definition`` 的连线。条件步骤在运行时
按变量和执行状态求值，不满足条件的分支记录为 skipped。
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

EXECUTOR_ALIASES: dict[str, str] = {
    "web": "web",
    "browser": "web",
    "windows": "windows",
    "desktop": "windows",
    "win": "windows",
    "mcp": "mcp",
    "files": "files",
    "file": "files",
    "transform": "transform",
    "transforms": "transform",
    "email": "email",
    "mail": "email",
    "code": "code",
    "python": "code",
}

EXECUTORS = frozenset(
    {"web", "windows", "files", "transform", "email", "mcp", "code"}
)

#: 依赖完成且允许后继步骤继续执行的状态。
DEPENDENCY_SATISFIED_STATUSES = frozenset({"success", "skipped"})
#: 依赖失败时后继步骤不再执行。
DEPENDENCY_BLOCKED_STATUSES = frozenset(
    {"failed", "dependency_failed"}
)

MISSING = object()


class ExecutionPlanError(ValueError):
    """执行计划缺少步骤、依赖非法或存在环。"""


@dataclass
class PlanStep:
    """规范化后的单个执行步骤。"""

    step_id: int
    index: int
    executor: str
    action: str
    raw: dict[str, Any]
    original_id: Any = None
    depends_on: list[int] = field(default_factory=list)
    condition: Any = None

    @property
    def idempotency_key(self) -> str | None:
        value = self.raw.get("idempotency_key")
        if value is None:
            return None
        text = str(value).strip()
        return text or None


def normalize_executor(value: Any, default: str = "web") -> str:
    """把执行器别名统一成 web/windows/mcp/code。"""

    text = str(value or "").strip().lower()
    if not text:
        text = str(default or "web").strip().lower()
    return EXECUTOR_ALIASES.get(text, text)


def normalize_execution_plan(
    plan: object,
    *,
    default_executor: str = "web",
) -> list[PlanStep]:
    """校验并规范化执行计划，返回声明顺序的步骤列表。"""

    if not isinstance(plan, dict):
        raise ExecutionPlanError("执行计划必须是 JSON 对象")

    raw_steps = plan.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ExecutionPlanError("执行计划至少需要一个步骤")

    id_map = _step_id_map(raw_steps)
    steps: list[PlanStep] = []
    seen_ids: set[int] = set()

    for index, raw in enumerate(raw_steps):
        if not isinstance(raw, dict):
            raise ExecutionPlanError(
                f"第 {index + 1} 个步骤必须是 JSON 对象"
            )

        action = str(raw.get("action") or raw.get("type") or "").strip()
        if not action:
            raise ExecutionPlanError(f"第 {index + 1} 个步骤缺少 action")

        step_id = id_map[str(raw.get("step_id", index + 1))]
        if step_id in seen_ids:
            raise ExecutionPlanError(f"step_id 不能重复：{step_id}")
        seen_ids.add(step_id)

        executor = normalize_executor(
            raw.get("executor"),
            default=default_executor,
        )
        if executor not in EXECUTORS:
            raise ExecutionPlanError(
                f"步骤 {step_id} 的执行器不受支持：{executor}"
            )

        depends_on = _normalize_dependencies(
            raw.get("depends_on"),
            id_map,
            step_id,
        )
        steps.append(
            PlanStep(
                step_id=step_id,
                index=index,
                executor=executor,
                action=action,
                raw=dict(raw),
                original_id=raw.get("step_id", index + 1),
                depends_on=depends_on,
                condition=step_condition(raw),
            )
        )

    _check_cycles(steps)
    return steps


def step_condition(raw: dict[str, Any]) -> Any:
    """读取步骤条件；enabled=false 等价于永不执行。"""

    if raw.get("enabled") is False:
        return {"type": "never"}

    for key in ("condition", "when", "if"):
        value = raw.get(key)
        if value is not None:
            return value

    params = raw.get("params")
    if isinstance(params, dict):
        value = params.get("condition")
        # wait 等动作的 condition 是字符串枚举（window_appears 等），
        # 只有结构化对象才当作执行条件。
        if isinstance(value, dict):
            return value
    return None


def merge_dag_definition(
    raw_steps: Iterable[Any],
    dag_definition: Any,
) -> list[dict[str, Any]]:
    """把 ``dag_definition`` 的连线补进没有显式依赖的步骤。

    步骤自带 ``depends_on`` 时以步骤声明为准：前端编辑器可能刚删除
    一条连线，而 ``dag_definition`` 还是上一次保存的版本。只有步骤
    完全没有依赖字段（旧计划）时，才用 DAG 连线兜底。
    """

    materialized = [
        dict(step) for step in raw_steps if isinstance(step, dict)
    ]
    if not isinstance(dag_definition, dict):
        return materialized

    node_map = _dag_node_map(dag_definition)
    edges = _dag_edges(dag_definition, node_map)
    conditions = _dag_conditions(dag_definition, node_map)

    merged: list[dict[str, Any]] = []
    for index, step in enumerate(materialized):
        reference = str(step.get("step_id", index + 1))
        item = dict(step)
        if "depends_on" not in item and edges.get(reference):
            item["depends_on"] = list(edges[reference])
        if (
            step_condition(item) is None
            and reference in conditions
        ):
            item["condition"] = conditions[reference]
        merged.append(item)
    return merged


def evaluate_condition(
    condition: Any,
    *,
    variables: dict[str, Any] | None = None,
    extracted: dict[str, Any] | None = None,
    statuses: dict[int, str] | None = None,
) -> bool:
    """对结构化条件求值；无法识别的条件按不满足处理。"""

    if condition is None:
        return True
    if isinstance(condition, bool):
        return condition

    variable_values = dict(variables or {})
    extracted_values = dict(extracted or {})
    step_statuses = dict(statuses or {})

    if isinstance(condition, str):
        text = condition.strip()
        if not text:
            return True
        lowered = text.lower()
        if lowered in {"always", "true", "yes", "1"}:
            return True
        if lowered in {"never", "false", "no", "0"}:
            return False
        return bool(
            _resolve_operand(
                text,
                variable_values,
                extracted_values,
            )
            is not MISSING
        ) and bool(
            _resolve_operand(
                text,
                variable_values,
                extracted_values,
            )
        )

    if not isinstance(condition, dict):
        return False

    condition_type = str(
        condition.get("type") or condition.get("kind") or ""
    ).strip().lower()
    if not condition_type:
        condition_type = _infer_condition_type(condition)

    if condition_type in {"always", "true"}:
        return True
    if condition_type in {"never", "false"}:
        return False
    if condition_type in {"all", "and"}:
        items = _condition_items(condition)
        return bool(items) and all(
            evaluate_condition(
                item,
                variables=variable_values,
                extracted=extracted_values,
                statuses=step_statuses,
            )
            for item in items
        )
    if condition_type in {"any", "or"}:
        items = _condition_items(condition)
        return any(
            evaluate_condition(
                item,
                variables=variable_values,
                extracted=extracted_values,
                statuses=step_statuses,
            )
            for item in items
        )
    if condition_type in {"not", "negate"}:
        items = _condition_items(condition)
        return not items or not evaluate_condition(
            items[0],
            variables=variable_values,
            extracted=extracted_values,
            statuses=step_statuses,
        )
    if condition_type in {"variable", "var"}:
        return _evaluate_variable_condition(
            condition,
            variable_values,
            extracted_values,
        )
    if condition_type in {"step_success", "step_completed", "step_ok"}:
        return (
            _step_status(condition, step_statuses) == "success"
        )
    if condition_type in {"step_failed", "step_fail"}:
        return _step_status(condition, step_statuses) == "failed"
    if condition_type in {"step_status", "step_state"}:
        expected = str(
            condition.get("status") or condition.get("state") or ""
        ).strip().lower()
        return _step_status(condition, step_statuses) == expected
    if condition_type in {"expression", "compare"}:
        return _evaluate_expression(
            condition,
            variable_values,
            extracted_values,
        )
    return False


def _step_id_map(raw_steps: list[Any]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    used: set[int] = set()
    for index, raw in enumerate(raw_steps):
        if not isinstance(raw, dict):
            raise ExecutionPlanError(
                f"第 {index + 1} 个步骤必须是 JSON 对象"
            )
        original = raw.get("step_id", index + 1)
        normalized = _coerce_step_id(original, index)
        if normalized in used:
            raise ExecutionPlanError(f"step_id 不能重复：{normalized}")
        used.add(normalized)
        mapping[str(original)] = normalized
        mapping.setdefault(str(normalized), normalized)
    return mapping


def _coerce_step_id(value: Any, index: int) -> int:
    if isinstance(value, bool):
        return index + 1
    if isinstance(value, int):
        if value < 1:
            raise ExecutionPlanError(f"step_id 必须是正整数：{value}")
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            number = int(text)
            if number >= 1:
                return number
    # 兼容编辑器里的字符串步骤 ID（例如 "open"）：用声明顺序编号。
    return index + 1


def _normalize_dependencies(
    value: Any,
    id_map: dict[str, int],
    step_id: int,
) -> list[int]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple, set)):
        raise ExecutionPlanError(
            f"步骤 {step_id} 的 depends_on 必须是数组"
        )

    dependencies: list[int] = []
    for item in value:
        if isinstance(item, str) and item.strip().lower() in {
            "",
            "trigger",
            "start",
        }:
            continue
        reference = str(item).strip()
        mapped = id_map.get(reference)
        if mapped is None:
            raise ExecutionPlanError(
                f"步骤 {step_id} 依赖不存在的步骤：{item}"
            )
        if mapped == step_id:
            raise ExecutionPlanError(f"步骤 {step_id} 不能依赖自身")
        if mapped not in dependencies:
            dependencies.append(mapped)
    return dependencies


def _check_cycles(steps: list[PlanStep]) -> None:
    dependencies = {
        step.step_id: list(step.depends_on) for step in steps
    }
    visited: set[int] = set()
    visiting: set[int] = set()

    def visit(step_id: int) -> None:
        if step_id in visited:
            return
        if step_id in visiting:
            raise ExecutionPlanError("步骤依赖存在环")
        visiting.add(step_id)
        for dependency in dependencies.get(step_id, []):
            visit(dependency)
        visiting.discard(step_id)
        visited.add(step_id)

    for step_id in dependencies:
        visit(step_id)


def _dag_node_map(dag_definition: dict[str, Any]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    nodes = dag_definition.get("nodes")
    if isinstance(nodes, list):
        for node in nodes:
            if not isinstance(node, dict):
                continue
            node_id = node.get("id")
            if node_id is None:
                continue
            reference = node.get("step_id")
            if reference is None:
                text = str(node_id)
                if text.startswith("step-"):
                    reference = text[5:]
                elif _looks_like_reference(text):
                    reference = text
            if reference is not None:
                mapping[str(node_id)] = str(reference)
    return mapping


def _dag_edges(
    dag_definition: dict[str, Any],
    node_map: dict[str, str],
) -> dict[str, list[Any]]:
    edges = dag_definition.get("edges")
    if not isinstance(edges, list):
        return {}

    dependencies: dict[str, list[Any]] = {}
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        source = edge.get("source")
        target = edge.get("target")
        if source is None or target is None:
            continue
        source_reference = node_map.get(str(source), str(source))
        target_reference = node_map.get(str(target), str(target))
        if source_reference.strip().lower() in {
            "",
            "trigger",
            "start",
        }:
            continue
        if target_reference.strip().lower() == "trigger":
            continue
        bucket = dependencies.setdefault(target_reference, [])
        if source_reference not in bucket:
            bucket.append(source_reference)
    return dependencies


def _dag_conditions(
    dag_definition: dict[str, Any],
    node_map: dict[str, str],
) -> dict[str, Any]:
    nodes = dag_definition.get("nodes")
    if not isinstance(nodes, list):
        return {}

    conditions: dict[str, Any] = {}
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_id = node.get("id")
        if node_id is None:
            continue
        reference = node_map.get(
            str(node_id),
            str(node_id),
        )
        for key in ("condition", "when", "if"):
            value = node.get(key)
            if value is not None:
                conditions[reference] = value
                break
    return conditions


def _looks_like_reference(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_.-]+", value))


def _infer_condition_type(condition: dict[str, Any]) -> str:
    if "conditions" in condition or "items" in condition:
        return "all"
    if "left" in condition and "operator" in condition:
        return "expression"
    if "variable" in condition or "var" in condition:
        return "variable"
    if "step_id" in condition:
        if "status" in condition or "state" in condition:
            return "step_status"
        return "step_success"
    return "always" if not condition else "never"


def _condition_items(condition: dict[str, Any]) -> list[Any]:
    for key in ("conditions", "items", "value"):
        value = condition.get(key)
        if isinstance(value, list):
            return value
        if value is not None and key != "value":
            return [value]
    return []


def _evaluate_variable_condition(
    condition: dict[str, Any],
    variables: dict[str, Any],
    extracted: dict[str, Any],
) -> bool:
    name = condition.get("name")
    if name is None:
        name = condition.get("variable", condition.get("var"))
    if isinstance(name, dict):
        name = name.get("var") or name.get("variable")
    text = str(name or "").strip()
    if not text:
        return False

    actual = variables.get(text, extracted.get(text, MISSING))
    operator = str(
        condition.get("operator")
        or condition.get("op")
        or ("truthy" if "expected" not in condition
            and "equals" not in condition
            and "value" not in condition
            else "equals")
    ).strip().lower()
    expected = condition.get("expected")
    if "equals" in condition:
        expected = condition.get("equals")
    elif "value" in condition:
        expected = condition.get("value")

    return _apply_operator(
        operator,
        actual,
        _resolve_operand(expected, variables, extracted),
    )


def _evaluate_expression(
    condition: dict[str, Any],
    variables: dict[str, Any],
    extracted: dict[str, Any],
) -> bool:
    left = _resolve_operand(
        condition.get("left"),
        variables,
        extracted,
    )
    right = _resolve_operand(
        condition.get("right"),
        variables,
        extracted,
    )
    operator = str(
        condition.get("operator") or condition.get("op") or "eq"
    ).strip().lower()
    return _apply_operator(operator, left, right)


def _apply_operator(operator: str, actual: Any, expected: Any) -> bool:
    operator = operator.strip().lower()
    if operator in {"truthy", "is_true", "true"}:
        return bool(actual) and actual is not MISSING
    if operator in {"falsy", "is_false", "false"}:
        return not bool(actual) or actual is MISSING
    if operator in {"exists", "present"}:
        return actual is not MISSING
    if operator in {"not_exists", "missing"}:
        return actual is MISSING
    if actual is MISSING:
        return False
    if operator in {"eq", "equals", "equal", "==", "is"}:
        return actual == expected
    if operator in {"ne", "not_equals", "!=", "is_not"}:
        return actual != expected
    if operator in {"gt", "greater_than", ">"}:
        return _compare(actual, expected, lambda a, b: a > b)
    if operator in {"gte", "greater_or_equal", ">="}:
        return _compare(actual, expected, lambda a, b: a >= b)
    if operator in {"lt", "less_than", "<"}:
        return _compare(actual, expected, lambda a, b: a < b)
    if operator in {"lte", "less_or_equal", "<="}:
        return _compare(actual, expected, lambda a, b: a <= b)
    if operator in {"contains", "includes"}:
        return _contains(actual, expected)
    if operator in {"not_contains", "excludes"}:
        return not _contains(actual, expected)
    if operator in {"in"}:
        return _in(expected, actual)
    if operator in {"not_in"}:
        return not _in(expected, actual)
    if operator in {"startswith", "starts_with"}:
        return str(actual).startswith(str(expected))
    if operator in {"endswith", "ends_with"}:
        return str(actual).endswith(str(expected))
    if operator in {"matches", "regex"}:
        try:
            return re.search(str(expected), str(actual)) is not None
        except re.error:
            return False
    return False


def _compare(actual: Any, expected: Any, operation: Any) -> bool:
    try:
        return bool(operation(actual, expected))
    except TypeError:
        try:
            return bool(
                operation(
                    float(actual),  # type: ignore[arg-type]
                    float(expected),  # type: ignore[arg-type]
                )
            )
        except (TypeError, ValueError):
            return False


def _contains(actual: Any, expected: Any) -> bool:
    if isinstance(actual, (list, tuple, set, dict)):
        try:
            return expected in actual
        except TypeError:
            return False
    return str(expected) in str(actual)


def _in(candidates: Any, actual: Any) -> bool:
    if isinstance(candidates, (list, tuple, set)):
        return actual in candidates
    return str(actual) in str(candidates)


def _resolve_operand(
    value: Any,
    variables: dict[str, Any],
    extracted: dict[str, Any],
) -> Any:
    if isinstance(value, dict):
        if "literal" in value:
            return value.get("literal")
        name = value.get("var", value.get("variable"))
        if name is not None:
            return _lookup(
                str(name),
                variables,
                extracted,
            )
        if set(value) == {"value"}:
            return value.get("value")
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("${") and text.endswith("}"):
            return _lookup(text[2:-1], variables, extracted)
    return value


def _lookup(
    name: str,
    variables: dict[str, Any],
    extracted: dict[str, Any],
) -> Any:
    text = str(name or "").strip()
    if not text:
        return MISSING
    if text in variables:
        return variables[text]
    if text in extracted:
        return extracted[text]

    # 支持 a.b 形式的嵌套变量。
    current: Any = variables
    for part in text.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
            continue
        if text.split(".", 1)[0] in extracted:
            current = extracted
            for inner in text.split("."):
                if isinstance(current, dict) and inner in current:
                    current = current[inner]
                else:
                    return MISSING
            return current
        return MISSING
    return current


def _step_status(
    condition: dict[str, Any],
    statuses: dict[int, str],
) -> str:
    value = condition.get("step_id")
    try:
        step_id = int(value)
    except (TypeError, ValueError):
        return ""
    return statuses.get(step_id, "")


__all__ = [
    "DEPENDENCY_BLOCKED_STATUSES",
    "DEPENDENCY_SATISFIED_STATUSES",
    "EXECUTORS",
    "ExecutionPlanError",
    "PlanStep",
    "evaluate_condition",
    "merge_dag_definition",
    "normalize_execution_plan",
    "normalize_executor",
    "step_condition",
]
