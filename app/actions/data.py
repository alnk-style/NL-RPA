from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any, Callable

from app.actions.conditions import evaluate_condition_spec
from app.actions.protocol import StepError, StepResult, StepStatus
from app.actions.variables import VariableScope, resolve_template


DATA_ACTIONS = frozenset(
    {
        "filter",
        "map",
        "cast",
        "sort",
        "deduplicate",
        "aggregate",
        "join",
        "flatten",
        "limit",
        "validate",
    }
)


async def execute_data_action(
    action: str,
    params: dict[str, Any],
    variables: dict[str, Any] | None = None,
) -> StepResult:
    normalized = str(action or "").strip().lower()
    if normalized not in DATA_ACTIONS:
        return _failed(
            "business_validation_failed",
            f"Unsupported data action: {action}",
        )
    try:
        resolved = resolve_template(dict(params or {}), variables or {})
    except Exception as exc:
        return _failed("business_validation_failed", str(exc))
    if not isinstance(resolved, dict):
        return _failed(
            "business_validation_failed",
            "Data action params must be an object",
        )
    try:
        value = _dispatch(normalized, resolved)
    except (TypeError, ValueError, KeyError) as exc:
        return _failed(
            "business_validation_failed",
            str(exc) or exc.__class__.__name__,
        )
    if isinstance(value, list) and not value:
        if not bool(resolved.get("allow_empty", False)):
            return _failed("data_empty", "Data action returned empty data")
    result = StepResult(
        status=StepStatus.SUCCESS,
        message=f"transform {normalized} completed",
        value=value,
        verification=_row_counts(normalized, resolved, value),
    )
    result_var = str(resolved.get("result_var") or "").strip()
    if result_var:
        result.variables[result_var] = value
    return result


def _row_counts(
    action: str,
    params: dict[str, Any],
    value: Any,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    if action == "join":
        left = params.get("left")
        right = params.get("right")
        if isinstance(left, list):
            counts["left_count"] = len(left)
        if isinstance(right, list):
            counts["right_count"] = len(right)
    else:
        values = params.get("values")
        if isinstance(values, list):
            counts["input_count"] = len(values)
    if isinstance(value, list):
        counts["output_count"] = len(value)
    return counts


def _dispatch(action: str, params: dict[str, Any]) -> Any:
    handlers: dict[str, Callable[[dict[str, Any]], Any]] = {
        "filter": _filter,
        "map": _map,
        "cast": _cast,
        "sort": _sort,
        "deduplicate": _deduplicate,
        "aggregate": _aggregate,
        "join": _join,
        "flatten": _flatten,
        "limit": _limit,
        "validate": _validate,
    }
    return handlers[action](params)


def _filter(params: dict[str, Any]) -> list[Any]:
    values = _rows(params.get("values"))
    field = str(params.get("field") or "").strip()
    operator = str(params.get("operator") or "eq")
    right = params.get("value")
    cast = params.get("cast")
    result: list[Any] = []
    for row in values:
        variables = {"row": row}
        condition = {
            "left": {"var": f"row.{field}" if field else "row"},
            "operator": operator,
            "right": {"literal": right},
        }
        if cast:
            condition["cast"] = str(cast)
        try:
            if evaluate_condition_spec(condition, variables):
                result.append(row)
        except (TypeError, ValueError):
            continue
    return result


def _map(params: dict[str, Any]) -> list[Any]:
    values = _rows(params.get("values"))
    mapping = params.get("mapping", params.get("fields"))
    if not isinstance(mapping, Mapping):
        raise ValueError("map requires mapping")
    strict = bool(params.get("strict", False))
    mapped: list[dict[str, Any]] = []
    for row in values:
        if not isinstance(row, Mapping):
            raise ValueError("map values must be objects")
        target: dict[str, Any] = {}
        for source, destination in mapping.items():
            if strict and _get_path(row, str(source)) is _MISSING:
                raise ValueError(f"Missing map field: {source}")
            target[str(destination)] = _get_path(row, str(source))
        mapped.append(target)
    return mapped


def _cast(params: dict[str, Any]) -> list[Any]:
    values = _rows(params.get("values"))
    target = str(params.get("target") or "").strip().lower()
    field = str(params.get("field") or "").strip()
    on_error = str(params.get("on_error") or "error").strip().lower()
    converted: list[Any] = []
    for row in values:
        try:
            if field:
                if not isinstance(row, Mapping):
                    raise ValueError("cast with field requires objects")
                item = dict(row)
                if _get_path(item, field) is _MISSING:
                    raise ValueError(f"Missing cast field: {field}")
                _set_path(item, field, _cast_value(_get_path(item, field), target))
                converted.append(item)
            else:
                converted.append(_cast_value(row, target))
        except (TypeError, ValueError):
            if on_error == "error":
                raise
            if on_error == "null":
                if field and isinstance(row, Mapping):
                    item = dict(row)
                    _set_path(item, field, None)
                    converted.append(item)
                else:
                    converted.append(None)
            elif on_error == "skip":
                continue
            else:
                raise ValueError(f"Unsupported on_error: {on_error}")
    return converted


def _sort(params: dict[str, Any]) -> list[Any]:
    values = list(_rows(params.get("values")))
    field = str(params.get("field") or "").strip()
    reverse = str(params.get("order") or "asc").lower() == "desc"
    missing = str(params.get("missing") or "last").lower()
    if missing not in {"first", "last"}:
        raise ValueError("sort missing must be first or last")

    def key(row: Any) -> tuple[int, Any]:
        value = _get_path(row, field) if field else row
        absent = value is _MISSING or value is None
        if not absent:
            return (0, value)
        return (0 if missing == "first" else 1, None)

    present = [row for row in values if key(row)[1] is not None]
    absent = [row for row in values if key(row)[1] is None]
    present.sort(
        key=lambda row: (
            _get_path(row, field) if field else row
        ),
        reverse=reverse,
    )
    return (
        [*absent, *present]
        if missing == "first"
        else [*present, *absent]
    )


def _deduplicate(params: dict[str, Any]) -> list[Any]:
    values = _rows(params.get("values"))
    keys = params.get("keys")
    keep = str(params.get("keep") or "first").lower()
    trim = bool(params.get("trim", False))
    if keep not in {"first", "last"}:
        raise ValueError("deduplicate keep must be first or last")
    result: list[Any] = []
    indexes: dict[Any, int] = {}
    for row in values:
        marker = _marker(row, keys, trim)
        if marker in indexes:
            if keep == "last":
                result[indexes[marker]] = row
            continue
        indexes[marker] = len(result)
        result.append(row)
    return result


def _aggregate(params: dict[str, Any]) -> Any:
    values = _rows(params.get("values"))
    operation = str(params.get("operation") or "count").lower()
    field = str(params.get("field") or "").strip()
    group_by = str(params.get("group_by") or "").strip()
    groups: dict[Any, list[Any]] = {}
    if group_by:
        for row in values:
            key = _get_path(row, group_by)
            groups.setdefault(None if key is _MISSING else key, []).append(row)
    else:
        groups[None] = values
    output = [
        {
            "group": key,
            "value": _aggregate_rows(rows, operation, field),
        }
        for key, rows in groups.items()
    ]
    return output if group_by else output[0]["value"]


def _join(params: dict[str, Any]) -> list[dict[str, Any]]:
    left = _rows(params.get("left"))
    right = _rows(params.get("right"))
    left_key = str(params.get("left_key") or "").strip()
    right_key = str(params.get("right_key") or "").strip()
    how = str(params.get("how") or "inner").lower()
    if not left_key or not right_key:
        raise ValueError("join requires left_key and right_key")
    right_index: dict[Any, list[Mapping[str, Any]]] = {}
    for row in right:
        if isinstance(row, Mapping):
            right_index.setdefault(_get_path(row, right_key), []).append(row)
    result: list[dict[str, Any]] = []
    for row in left:
        if not isinstance(row, Mapping):
            continue
        matches = right_index.get(_get_path(row, left_key), [])
        if matches:
            for match in matches:
                result.append({**dict(row), **dict(match)})
        elif how == "left":
            result.append(dict(row))
        elif how != "inner":
            raise ValueError("join how must be inner or left")
    return result


def _flatten(params: dict[str, Any]) -> list[Any]:
    values = _rows(params.get("values"))
    field = str(params.get("field") or "").strip()
    depth = max(int(params.get("depth", 1)), 1)
    result: list[Any] = []
    for row in values:
        value = _get_path(row, field) if field else row
        _flatten_into(result, value, depth)
    return result


def _limit(params: dict[str, Any]) -> list[Any]:
    values = _rows(params.get("values"))
    count = max(int(params.get("count", 0)), 0)
    offset = max(int(params.get("offset", 0)), 0)
    return values[offset: offset + count]


def _validate(params: dict[str, Any]) -> list[Any]:
    values = _rows(params.get("values"))
    required = params.get("required_fields")
    if isinstance(required, str):
        required = [required]
    if isinstance(required, list):
        for index, row in enumerate(values):
            if not isinstance(row, Mapping):
                raise ValueError(f"Row {index} is not an object")
            missing = [
                str(field)
                for field in required
                if _get_path(row, str(field)) is _MISSING
            ]
            if missing:
                raise ValueError(
                    f"Row {index} missing fields: {', '.join(missing)}"
                )
    minimum = params.get("min_rows")
    if minimum is not None and len(values) < max(int(minimum), 0):
        raise ValueError(
            f"Expected at least {int(minimum)} rows, got {len(values)}"
        )
    maximum = params.get("max_rows")
    if maximum is not None and len(values) > max(int(maximum), 0):
        raise ValueError(
            f"Expected at most {int(maximum)} rows, got {len(values)}"
        )
    unique_by = params.get("unique_by")
    if isinstance(unique_by, str):
        unique_by = [unique_by]
    if isinstance(unique_by, list) and unique_by:
        seen: set[Any] = set()
        for row in values:
            marker = tuple(
                _get_path(row, str(field)) for field in unique_by
            )
            if marker in seen:
                raise ValueError("Duplicate values violate unique_by")
            seen.add(marker)
    return values


def _rows(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    raise ValueError("values must resolve to an array")


_MISSING = object()


def _get_path(value: Any, path: str) -> Any:
    if not path:
        return value
    current = value
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return _MISSING
        current = current[part]
    return current


def _set_path(value: dict[str, Any], path: str, replacement: Any) -> None:
    parts = path.split(".")
    current = value
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    current[parts[-1]] = replacement


def _cast_value(value: Any, target: str) -> Any:
    if target in {"string", "str"}:
        return str(value)
    if target in {"number", "float"}:
        return float(value)
    if target in {"integer", "int"}:
        return int(value)
    if target in {"boolean", "bool"}:
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "1", "yes", "on"}:
                return True
            if lowered in {"false", "0", "no", "off"}:
                return False
            raise ValueError(f"Cannot cast boolean: {value}")
        return bool(value)
    if target == "date":
        if isinstance(value, date):
            return value.isoformat()
        return date.fromisoformat(str(value)).isoformat()
    raise ValueError(f"Unsupported cast target: {target}")


def _marker(row: Any, keys: Any, trim: bool) -> Any:
    if isinstance(keys, str):
        keys = [keys]
    if isinstance(keys, list) and keys:
        marker = tuple(_get_path(row, str(key)) for key in keys)
    else:
        marker = row
    if trim and isinstance(marker, str):
        return marker.strip()
    if trim and isinstance(marker, tuple):
        return tuple(
            item.strip() if isinstance(item, str) else item
            for item in marker
        )
    return marker


def _aggregate_rows(rows: list[Any], operation: str, field: str) -> Any:
    if operation == "count":
        return len(rows)
    numbers: list[float] = []
    for row in rows:
        value = _get_path(row, field) if field else row
        if value is _MISSING or value is None:
            continue
        numbers.append(float(value))
    if operation == "sum":
        return _number(sum(numbers))
    if operation == "min":
        return _number(min(numbers)) if numbers else None
    if operation == "max":
        return _number(max(numbers)) if numbers else None
    if operation == "avg":
        return _number(sum(numbers) / len(numbers)) if numbers else None
    raise ValueError(f"Unsupported aggregate operation: {operation}")


def _number(value: float | int) -> float | int:
    numeric = float(value)
    return int(numeric) if numeric.is_integer() else numeric


def _flatten_into(result: list[Any], value: Any, depth: int) -> None:
    if isinstance(value, (list, tuple)) and depth > 0:
        for item in value:
            _flatten_into(result, item, depth - 1)
    else:
        result.append(value)


def _failed(category: str, message: str) -> StepResult:
    return StepResult(
        status=StepStatus.FAILED,
        message=message,
        error=StepError(category=category, message=message),
    )


__all__ = ["DATA_ACTIONS", "execute_data_action"]
