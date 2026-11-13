from __future__ import annotations

from typing import Any

from desktop_agent.uia_observation import (
    MAX_SCAN_DEPTH,
    MAX_SCAN_ELEMENTS,
    annotate_locators,
    best_locator_score,
    element_summary,
    element_value,
    iter_elements,
    match_score,
)


def _desktop() -> Any:
    from pywinauto import Desktop

    return Desktop(backend="uia")


def _uia_windows(context: dict[str, Any]) -> list[Any]:
    desktop = _desktop()
    handle = context.get("last_window_handle")
    if handle:
        try:
            return [desktop.window(handle=int(handle))]
        except Exception:
            return []
    try:
        return list(desktop.windows())
    except Exception:
        return []


def find_element(
    context: dict[str, Any],
    *,
    automation_id: str | None = None,
    name: str | None = None,
    control_type: str | None = None,
) -> Any | None:
    if not any((automation_id, name, control_type)):
        return None

    handle = context.get("last_window_handle")
    for window in _uia_windows(context):
        for element, _depth in iter_elements(
            window,
            max_elements=MAX_SCAN_ELEMENTS,
            max_depth=MAX_SCAN_DEPTH,
        ):
            if not _matches(
                element,
                automation_id=automation_id,
                name=name,
                control_type=control_type,
            ):
                continue
            context["last_locator"] = {
                "automation_id": automation_id,
                "name": name,
                "control_type": control_type,
                "window_handle": _window_handle(window, handle),
            }
            return element
    return None


def element_text(element: Any) -> str:
    return element_value(element)


def describe_candidates(
    context: dict[str, Any],
    *,
    name: str | None = None,
    automation_id: str | None = None,
    control_type: str | None = None,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """列出当前窗口内候选控件的结构化详情，按匹配度排序。"""

    if limit <= 0:
        return []

    candidates: list[dict[str, Any]] = []
    scanned = 0
    visited = 0
    for window in _uia_windows(context):
        if scanned >= MAX_SCAN_ELEMENTS:
            break
        for element, depth in iter_elements(
            window,
            max_elements=MAX_SCAN_ELEMENTS - scanned,
            max_depth=MAX_SCAN_DEPTH,
        ):
            scanned += 1
            visited += 1
            if depth == 0:
                # 顶层窗口由窗口候选负责，控件候选只收集子控件。
                continue
            summary = element_summary(
                element,
                depth=depth,
                index=visited - 1,
            )
            if summary is not None:
                candidates.append(summary)

    if not candidates:
        return []

    annotate_locators(candidates)
    targeted = any((name, automation_id, control_type))
    for candidate in candidates:
        if targeted:
            candidate["score"] = match_score(
                candidate,
                name=name,
                automation_id=automation_id,
                control_type=control_type,
            )
        else:
            candidate["score"] = best_locator_score(candidate)

    if targeted:
        candidates.sort(
            key=lambda candidate: (
                -int(candidate["score"]),
                -best_locator_score(candidate),
                int(candidate["index"]),
            )
        )
    else:
        candidates.sort(
            key=lambda candidate: (
                -int(candidate["score"]),
                int(candidate["depth"]),
                int(candidate["index"]),
            )
        )
    return candidates[:limit]


def _matches(
    element: Any,
    *,
    automation_id: str | None,
    name: str | None,
    control_type: str | None,
) -> bool:
    try:
        info = element.element_info
    except Exception:
        return False

    if automation_id and str(getattr(info, "automation_id", "")) != automation_id:
        return False
    if name and name.lower() not in str(getattr(info, "name", "")).lower():
        return False
    if control_type and (
        str(getattr(info, "control_type", "")).lower()
        != control_type.lower()
    ):
        return False
    return True


def _window_handle(window: Any, fallback: Any) -> int | None:
    try:
        handle = int(window.handle)
    except Exception:
        handle = 0
    if handle:
        return handle
    try:
        return int(fallback) if fallback is not None else None
    except (TypeError, ValueError):
        return None


__all__ = [
    "describe_candidates",
    "element_text",
    "find_element",
]
