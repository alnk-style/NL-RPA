"""纯数据转换，不执行表达式或模型生成的代码。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


_LRC_TAG = re.compile(r"^\[[^\]]+\]\s*")

TRANSFORM_ACTIONS = frozenset(
    {
        "split_lines",
        "trim_lines",
        "remove_blank_lines",
        "deduplicate",
        "filter_contains",
        "map_fields",
        "merge_rows",
        "count_chars",
        "aggregate_sum",
        "sort_by",
    }
)


def split_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw_line in str(text or "").splitlines():
        line = _LRC_TAG.sub("", raw_line).strip()
        if line:
            lines.append(line)
    return lines


def count_chars(text: str) -> int:
    return len("".join(str(text or "").split()))


def apply_transform(
    action: str,
    params: dict[str, Any],
    variables: dict[str, Any],
) -> Any:
    """按动作名执行纯转换；参数中的变量路径保持原始类型。"""

    from app.engine.task_runtime import resolve_template

    normalized = str(action or "").strip().lower()
    values = resolve_template(dict(params or {}), variables)
    if not isinstance(values, dict):
        raise ValueError("transform params must be an object")

    if normalized == "split_lines":
        return split_lines(str(values.get("text") or ""))
    if normalized == "trim_lines":
        return [
            str(item).strip() for item in _list(values.get("values"))
        ]
    if normalized == "remove_blank_lines":
        return [
            str(item).strip()
            for item in _list(values.get("values"))
            if str(item).strip()
        ]
    if normalized == "deduplicate":
        return _deduplicate(
            values.get("values"),
            trim=bool(values.get("trim", False)),
        )
    if normalized == "filter_contains":
        needle = str(
            values.get("text") or values.get("needle") or ""
        ).casefold()
        return [
            item
            for item in _list(values.get("values"))
            if needle in str(item).casefold()
        ]
    if normalized == "map_fields":
        mapping = values.get("mapping")
        if not isinstance(mapping, Mapping):
            raise ValueError("map_fields requires a mapping object")
        return [
            {
                str(target): row.get(source)
                for source, target in mapping.items()
            }
            for row in _list(values.get("values"))
            if isinstance(row, Mapping)
        ]
    if normalized == "merge_rows":
        merged: list[Any] = []
        for group in _list(values.get("values")):
            if isinstance(group, (list, tuple)):
                merged.extend(group)
            else:
                merged.append(group)
        return merged
    if normalized == "count_chars":
        if "values" in values:
            return [
                count_chars(str(item))
                for item in _list(values.get("values"))
            ]
        return count_chars(str(values.get("text") or ""))
    if normalized == "aggregate_sum":
        numbers = _aggregate_values(
            values.get("values"),
            field=values.get("field"),
        )
        return sum(numbers)
    if normalized == "sort_by":
        field = str(values.get("field") or "").strip()
        rows = list(_list(values.get("values")))
        if not field:
            raise ValueError("sort_by requires field")
        return sorted(
            rows,
            key=lambda item: (
                item.get(field)
                if isinstance(item, Mapping)
                else item
            ),
            reverse=bool(values.get("reverse", False)),
        )
    raise ValueError(f"Unsupported transform: {action}")


def _list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _deduplicate(value: Any, *, trim: bool) -> list[Any]:
    seen: set[Any] = set()
    result: list[Any] = []
    for item in _list(value):
        candidate = str(item).strip() if trim else item
        marker = (
            candidate.casefold()
            if isinstance(candidate, str)
            else candidate
        )
        if marker in seen:
            continue
        seen.add(marker)
        result.append(candidate)
    return result


def _aggregate_values(
    value: Any,
    *,
    field: Any,
) -> list[float | int]:
    field_name = str(field or "").strip()
    numbers: list[float | int] = []
    for item in _list(value):
        if field_name and isinstance(item, Mapping):
            item = item.get(field_name)
        try:
            number = float(item)
        except (TypeError, ValueError):
            continue
        numbers.append(int(number) if number.is_integer() else number)
    return numbers


__all__ = [
    "TRANSFORM_ACTIONS",
    "apply_transform",
    "count_chars",
    "split_lines",
]
