from __future__ import annotations

import re
from typing import Any

from app.actions.variables import VariableScope


_MISSING = object()


def evaluate_condition_spec(
    condition: Any,
    variables: dict[str, Any] | None = None,
) -> bool:
    if condition is None:
        return True
    if isinstance(condition, bool):
        return condition
    if not isinstance(condition, dict):
        raise ValueError("Condition must be an object or boolean")
    scope = VariableScope(variables or {})

    all_items = _group_items(condition, "all", "conditions", "items")
    if all_items is not None:
        return bool(all_items) and all(
            evaluate_condition_spec(item, scope.values)
            for item in all_items
        )
    any_items = _group_items(condition, "any")
    if any_items is not None:
        return any(
            evaluate_condition_spec(item, scope.values)
            for item in any_items
        )
    not_items = _group_items(condition, "not")
    if not_items is not None:
        if len(not_items) != 1:
            raise ValueError("not condition requires exactly one item")
        return not evaluate_condition_spec(
            not_items[0],
            scope.values,
        )

    kind = str(
        condition.get("type") or condition.get("kind") or ""
    ).strip().lower()
    if kind in {"all", "and"}:
        return _evaluate_group(condition, scope, "all")
    if kind in {"any", "or"}:
        return _evaluate_group(condition, scope, "any")
    if kind in {"not", "negate"}:
        return _evaluate_group(condition, scope, "not")

    if "left" not in condition or "operator" not in condition:
        raise ValueError("Comparison condition requires left and operator")
    left = _operand(condition.get("left"), scope)
    right = _operand(condition.get("right"), scope)
    operator = str(condition["operator"]).strip().lower()
    cast = str(condition.get("cast") or "").strip().lower()
    if cast:
        left = _cast(left, cast)
        right = _cast(right, cast)
    return _apply_operator(operator, left, right)


def _group_items(
    condition: dict[str, Any],
    key: str,
    *alternate_keys: str,
) -> list[Any] | None:
    if key in condition:
        value = condition[key]
        return list(value) if isinstance(value, list) else [value]
    for alternate in alternate_keys:
        if alternate in condition:
            value = condition[alternate]
            return list(value) if isinstance(value, list) else [value]
    return None


def _evaluate_group(
    condition: dict[str, Any],
    scope: VariableScope,
    group: str,
) -> bool:
    items = _group_items(condition, "conditions", "items", "value")
    if items is None:
        raise ValueError(f"{group} condition requires conditions")
    if group == "all":
        return bool(items) and all(
            evaluate_condition_spec(item, scope.values)
            for item in items
        )
    if group == "any":
        return any(
            evaluate_condition_spec(item, scope.values)
            for item in items
        )
    if len(items) != 1:
        raise ValueError("not condition requires exactly one item")
    return not evaluate_condition_spec(items[0], scope.values)


def _operand(value: Any, scope: VariableScope) -> Any:
    if isinstance(value, dict):
        if "literal" in value:
            return value.get("literal")
        variable = value.get("var", value.get("variable"))
        if variable is not None:
            try:
                return scope.get(str(variable))
            except ValueError:
                return _MISSING
        if set(value) == {"value"}:
            return value.get("value")
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("${") and text.endswith("}"):
            try:
                return scope.get(text[2:-1])
            except ValueError:
                return _MISSING
    return value


def _cast(value: Any, cast: str) -> Any:
    if value is _MISSING:
        return value
    if cast in {"string", "str"}:
        return str(value)
    if cast in {"number", "float"}:
        return float(value)
    if cast in {"integer", "int"}:
        return int(value)
    if cast in {"boolean", "bool"}:
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)
    raise ValueError(f"Unsupported cast: {cast}")


def _apply_operator(operator: str, left: Any, right: Any) -> bool:
    if operator == "exists":
        return left is not _MISSING
    if operator == "not_exists":
        return left is _MISSING
    if left is _MISSING or right is _MISSING:
        return False
    if operator == "eq":
        return left == right
    if operator == "ne":
        return left != right
    if operator in {"gt", "gte", "lt", "lte"}:
        try:
            a, b = float(left), float(right)
        except (TypeError, ValueError):
            a, b = left, right
        if operator == "gt":
            return a > b
        if operator == "gte":
            return a >= b
        if operator == "lt":
            return a < b
        return a <= b
    if operator == "contains":
        return right in left
    if operator == "not_contains":
        return right not in left
    if operator == "startswith":
        return str(left).startswith(str(right))
    if operator == "endswith":
        return str(left).endswith(str(right))
    if operator == "matches":
        return re.search(str(right), str(left)) is not None
    if operator == "in":
        return left in right
    if operator == "not_in":
        return left not in right
    raise ValueError(f"Unsupported operator: {operator}")


__all__ = ["evaluate_condition_spec"]
