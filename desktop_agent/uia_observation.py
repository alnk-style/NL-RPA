"""UI Automation 元素的观察字段与定位评分。

巡检（desktop.inspect）和执行失败时的候选详情共用这套逻辑，保证规划器
看到的控件字段、定位候选和分数与执行现场一致。

评分只用于候选排序：AutomationId 唯一时最可靠，其次是名称加控件类型，
再次是名称和控件类型。匹配数量不为 1 时降分；分数不代表元素一定能命中，
执行器仍会逐个验证候选。
"""

from __future__ import annotations

import difflib
from collections import deque
from collections.abc import Iterable, Iterator
from typing import Any

MAX_TEXT_LENGTH = 120
MAX_PATTERNS = 12
MAX_PARENT_DEPTH = 6
MAX_SCAN_ELEMENTS = 200
MAX_SCAN_DEPTH = 6

STRATEGY_SCORE = {
    "automation_id": 100,
    "name_control_type": 92,
    "name": 80,
    "control_type": 45,
}
AMBIGUOUS_PENALTY = 45
MISSING_PENALTY = 35


def text(value: Any, limit: int = MAX_TEXT_LENGTH) -> str:
    """压缩空白并截断过长文本，避免观察结果撑大 Prompt。"""

    normalized = " ".join(str(value or "").split())
    if len(normalized) > limit:
        normalized = normalized[: max(limit - 1, 0)] + "…"
    return normalized


def element_info(element: Any) -> Any | None:
    try:
        return element.element_info
    except Exception:
        return None


def iter_elements(
    root: Any,
    *,
    max_elements: int = MAX_SCAN_ELEMENTS,
    max_depth: int = MAX_SCAN_DEPTH,
) -> Iterator[tuple[Any, int]]:
    """按广度优先遍历控件，产出 (element, depth) 且跳过异常分支。"""

    try:
        limit = max(int(max_elements), 0)
        depth_limit = max(int(max_depth), 0)
    except (TypeError, ValueError):
        return
    if limit <= 0:
        return

    queue: deque[tuple[Any, int]] = deque([(root, 0)])
    produced = 0
    while queue and produced < limit:
        element, depth = queue.popleft()
        yield element, depth
        produced += 1
        if depth >= depth_limit:
            continue
        for child in children(element):
            queue.append((child, depth + 1))


def children(element: Any) -> list[Any]:
    try:
        return list(element.children())
    except Exception:
        return []


def element_summary(
    element: Any,
    *,
    depth: int = 0,
    index: int = 0,
) -> dict[str, Any] | None:
    """提取单个控件的观察字段；没有名称和 AutomationId 时返回 None。"""

    info = element_info(element)
    if info is None:
        return None

    name = text(getattr(info, "name", ""))
    automation_id = text(getattr(info, "automation_id", ""))
    if not name and not automation_id:
        return None

    control_type = text(getattr(info, "control_type", ""))
    class_name = text(getattr(info, "class_name", ""))

    summary: dict[str, Any] = {
        "index": int(index),
        "depth": int(depth),
        "name": name,
    }
    if automation_id:
        summary["automation_id"] = automation_id
    if control_type:
        summary["control_type"] = control_type
    if class_name:
        summary["class_name"] = class_name

    value = element_value(element)
    if value:
        summary["value"] = value

    visible = _bool_value(element, "is_visible")
    if visible is not None:
        summary["visible"] = visible

    enabled = _bool_value(element, "is_enabled")
    if enabled is not None:
        summary["enabled"] = enabled

    rectangle = element_rectangle(element)
    if rectangle is not None:
        summary["rectangle"] = rectangle

    parent_path = element_parent_path(element)
    if parent_path:
        summary["parent_path"] = parent_path

    patterns = element_patterns(element)
    if patterns:
        summary["patterns"] = patterns

    return summary


def element_value(element: Any) -> str:
    """读取控件值，兼容 get_value、window_text 和 legacy_properties。"""

    values: list[str] = []
    value = _call(element, "get_value")
    if value not in (None, ""):
        values.append(str(value))

    window_text = _call(element, "window_text")
    if window_text and str(window_text) not in values:
        values.append(str(window_text))

    legacy = _call(element, "legacy_properties")
    legacy_value = legacy.get("Value") if isinstance(legacy, dict) else None
    if legacy_value and str(legacy_value) not in values:
        values.append(str(legacy_value))

    return text("\n".join(values))


def element_rectangle(element: Any) -> dict[str, int] | None:
    rectangle = _call(element, "rectangle")
    if rectangle is None:
        return None

    if isinstance(rectangle, (list, tuple)) and len(rectangle) == 4:
        left, top, right, bottom = rectangle
    else:
        keys = ("left", "top", "right", "bottom")
        values = []
        for key in keys:
            if isinstance(rectangle, dict):
                value = rectangle.get(key)
            else:
                value = getattr(rectangle, key, None)
            if value is None:
                return None
            values.append(value)
        left, top, right, bottom = values

    try:
        return {
            "left": int(left),
            "top": int(top),
            "right": int(right),
            "bottom": int(bottom),
        }
    except (TypeError, ValueError):
        return None


def element_parent_path(element: Any) -> list[dict[str, str]]:
    """从窗口到直接父级列出自上而下的父路径，最多 6 层。"""

    entries: list[dict[str, str]] = []
    seen = {id(element)}
    current = element
    for _ in range(MAX_PARENT_DEPTH):
        parent = _call(current, "parent")
        if parent is None or id(parent) in seen:
            break
        seen.add(id(parent))
        info = element_info(parent)
        if info is None:
            break
        entry: dict[str, str] = {}
        name = text(getattr(info, "name", ""))
        control_type = text(getattr(info, "control_type", ""))
        if name:
            entry["name"] = name
        if control_type:
            entry["control_type"] = control_type
        if entry:
            entries.append(entry)
        current = parent
    entries.reverse()
    return entries


def element_patterns(element: Any) -> list[str]:
    """尽力读取控件支持的 UI Automation Patterns，读取失败时省略。"""

    for attribute in (
        "get_supported_patterns",
        "supported_patterns",
        "patterns",
    ):
        value = _call(element, attribute)
        patterns = _normalize_patterns(value)
        if patterns:
            return patterns

    info = element_info(element)
    if info is not None:
        for attribute in ("supported_patterns", "patterns"):
            value = getattr(info, attribute, None)
            patterns = _normalize_patterns(value)
            if patterns:
                return patterns
    return []


def annotate_locators(elements: list[dict[str, Any]]) -> None:
    """给元素列表补充按评分降序的 locators 与 best_locator。"""

    automation_counts: dict[str, int] = {}
    name_counts: dict[str, int] = {}
    name_type_counts: dict[tuple[str, str], int] = {}
    type_counts: dict[str, int] = {}

    for element in elements:
        automation_id = str(element.get("automation_id") or "")
        name = str(element.get("name") or "")
        control_type = str(element.get("control_type") or "")
        if automation_id:
            automation_counts[automation_id] = (
                automation_counts.get(automation_id, 0) + 1
            )
        if name:
            name_counts[name] = name_counts.get(name, 0) + 1
        if name and control_type:
            key = (name, control_type)
            name_type_counts[key] = name_type_counts.get(key, 0) + 1
        if control_type:
            type_counts[control_type] = type_counts.get(control_type, 0) + 1

    for element in elements:
        automation_id = str(element.get("automation_id") or "")
        name = str(element.get("name") or "")
        control_type = str(element.get("control_type") or "")

        locators: list[dict[str, Any]] = []
        if automation_id:
            locators.append(
                _locator(
                    "automation_id",
                    automation_id,
                    count=automation_counts.get(automation_id, 0),
                    base=STRATEGY_SCORE["automation_id"],
                )
            )
        if name and control_type:
            locators.append(
                _locator(
                    "name",
                    name,
                    name=name,
                    control_type=control_type,
                    count=name_type_counts.get((name, control_type), 0),
                    base=STRATEGY_SCORE["name_control_type"],
                )
            )
        elif name:
            locators.append(
                _locator(
                    "name",
                    name,
                    name=name,
                    count=name_counts.get(name, 0),
                    base=STRATEGY_SCORE["name"],
                )
            )
        elif control_type:
            locators.append(
                _locator(
                    "control_type",
                    control_type,
                    control_type=control_type,
                    count=type_counts.get(control_type, 0),
                    base=STRATEGY_SCORE["control_type"],
                )
            )

        locators.sort(key=lambda locator: (-int(locator["score"]), locator["strategy"]))
        element["locators"] = locators
        element["best_locator"] = locators[0] if locators else None


def match_score(
    candidate: dict[str, Any],
    *,
    name: str | None = None,
    automation_id: str | None = None,
    control_type: str | None = None,
) -> int:
    """按失败时的定位条件给候选打匹配分，用于排序候选详情。"""

    score = 0
    if automation_id and str(candidate.get("automation_id") or "") == (
        automation_id
    ):
        score += 100

    if name:
        candidate_name = str(candidate.get("name") or "")
        target = name.strip().lower()
        lowered = candidate_name.lower()
        if lowered == target:
            score += 80
        elif target and target in lowered:
            score += 55
        else:
            score += int(
                40 * difflib.SequenceMatcher(None, target, lowered).ratio()
            )

    if control_type and str(candidate.get("control_type") or "").lower() == (
        control_type.strip().lower()
    ):
        score += 20

    if candidate.get("visible") is True:
        score += 4
    if candidate.get("enabled") is True:
        score += 4
    return score


def best_locator_score(candidate: dict[str, Any]) -> int:
    locator = candidate.get("best_locator")
    if not isinstance(locator, dict):
        return 0
    try:
        return int(locator.get("score") or 0)
    except (TypeError, ValueError):
        return 0


def candidate_label(candidate: Any) -> str:
    """把候选元素或窗口压成一行人类可读标签。"""

    if isinstance(candidate, str):
        return candidate.strip()
    if not isinstance(candidate, dict):
        return str(candidate).strip()

    label = candidate.get("label")
    if isinstance(label, str) and label.strip():
        return label.strip()

    name = str(candidate.get("name") or candidate.get("title") or "").strip()
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


def candidate_labels(candidates: Iterable[Any]) -> list[str]:
    labels: list[str] = []
    for candidate in candidates:
        label = candidate_label(candidate)
        if label and label not in labels:
            labels.append(label)
    return labels


def _locator(
    strategy: str,
    value: str,
    *,
    name: str | None = None,
    control_type: str | None = None,
    count: int,
    base: int,
) -> dict[str, Any]:
    return {
        "strategy": strategy,
        "value": text(value),
        "name": text(name) if name else None,
        "control_type": text(control_type) if control_type else None,
        "count": int(count),
        "unique": int(count) == 1,
        "score": _score_for(base, int(count)),
    }


def _score_for(base: int, count: int) -> int:
    if count == 1:
        return base
    if count <= 0:
        return max(1, base - MISSING_PENALTY)
    return max(5, base - AMBIGUOUS_PENALTY)


def _normalize_patterns(value: Any) -> list[str]:
    if isinstance(value, dict):
        value = list(value)
    if not isinstance(value, (list, tuple, set, frozenset)):
        return []
    patterns: list[str] = []
    for item in value:
        pattern = str(item).strip()
        if pattern and pattern not in patterns:
            patterns.append(pattern)
    return patterns[:MAX_PATTERNS]


def _bool_value(element: Any, attribute: str) -> bool | None:
    value = _call(element, attribute)
    if value is None:
        return None
    return bool(value)


def _call(element: Any, attribute: str) -> Any:
    try:
        value = getattr(element, attribute)
    except Exception:
        return None
    if not callable(value):
        return value
    try:
        return value()
    except Exception:
        return None


__all__ = [
    "MAX_SCAN_DEPTH",
    "MAX_SCAN_ELEMENTS",
    "annotate_locators",
    "best_locator_score",
    "candidate_label",
    "candidate_labels",
    "children",
    "element_info",
    "element_parent_path",
    "element_patterns",
    "element_rectangle",
    "element_summary",
    "element_value",
    "iter_elements",
    "match_score",
    "text",
]
