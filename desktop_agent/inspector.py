"""桌面巡检：回报可见窗口与目标窗口的控件摘要。

巡检只读取窗口和 UI Automation 信息，不修改任何界面状态，供后端规划器
在生成桌面步骤前了解真实控件。
"""

from __future__ import annotations

from pathlib import PureWindowsPath
from typing import Any

from desktop_agent.actions._win32 import (
    enum_windows,
    process_image_path,
)
from desktop_agent.uia_observation import (
    annotate_locators,
    element_summary,
    iter_elements,
)

DEFAULT_MAX_WINDOWS = 40
DEFAULT_MAX_ELEMENTS = 60
DEFAULT_MAX_DEPTH = 4
MAX_WINDOWS_LIMIT = 200
MAX_ELEMENTS_LIMIT = 200
MAX_DEPTH_LIMIT = 10


def _clamp(
    value: Any,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def describe_windows(
    limit: int = DEFAULT_MAX_WINDOWS,
) -> list[dict[str, Any]]:
    """列出可见窗口，并附带进程可执行文件名。"""

    windows: list[dict[str, Any]] = []
    max_windows = max(int(limit), 1)
    for window in enum_windows(visible_only=True):
        title = window.title.strip()
        if not title:
            continue

        summary: dict[str, Any] = {
            "handle": window.handle,
            "title": title,
            "class_name": window.class_name,
            "pid": window.pid,
        }
        image_path = process_image_path(window.pid)
        if image_path:
            summary["process_name"] = PureWindowsPath(image_path).name
        windows.append(summary)
        if len(windows) >= max_windows:
            break
    return windows


def collect_elements(
    window: Any,
    *,
    max_elements: int = DEFAULT_MAX_ELEMENTS,
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> list[dict[str, Any]]:
    """按广度优先收集控件摘要，忽略没有名称和 AutomationId 的节点。

    每个元素附带类名、父路径、边界框、可见/可用状态、值、索引与按
    唯一性评分排序的 locators 和 best_locator，供规划器和修复复用。
    """

    limit = _clamp(
        max_elements,
        default=DEFAULT_MAX_ELEMENTS,
        minimum=0,
        maximum=MAX_ELEMENTS_LIMIT,
    )
    depth_limit = _clamp(
        max_depth,
        default=DEFAULT_MAX_DEPTH,
        minimum=0,
        maximum=MAX_DEPTH_LIMIT,
    )
    if limit <= 0:
        return []

    elements: list[dict[str, Any]] = []
    for element, depth in iter_elements(
        window,
        max_elements=limit,
        max_depth=depth_limit,
    ):
        summary = element_summary(
            element,
            depth=depth,
            index=len(elements),
        )
        if summary is not None:
            elements.append(summary)
    annotate_locators(elements)
    return elements


def inspect_desktop(
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """处理 desktop.inspect 请求，返回窗口列表和可选的控件摘要。"""

    request = payload if isinstance(payload, dict) else {}
    max_windows = _clamp(
        request.get("max_windows"),
        default=DEFAULT_MAX_WINDOWS,
        minimum=1,
        maximum=MAX_WINDOWS_LIMIT,
    )
    windows = describe_windows(limit=max_windows)
    result: dict[str, Any] = {
        "success": True,
        "windows": windows,
        "target": None,
        "elements": [],
        "error": None,
    }

    target_request = request.get("window")
    if not isinstance(target_request, dict):
        return result

    target = _resolve_target_window(target_request, windows)
    if target is None:
        result["warning"] = "window not found"
        return result

    max_elements = _clamp(
        request.get("max_elements"),
        default=DEFAULT_MAX_ELEMENTS,
        minimum=0,
        maximum=MAX_ELEMENTS_LIMIT,
    )
    max_depth = _clamp(
        request.get("max_depth"),
        default=DEFAULT_MAX_DEPTH,
        minimum=0,
        maximum=MAX_DEPTH_LIMIT,
    )

    try:
        window = _uia_window(int(target["handle"]))
        elements = collect_elements(
            window,
            max_elements=max_elements,
            max_depth=max_depth,
        )
    except Exception as exc:
        result["success"] = False
        result["error"] = str(exc) or exc.__class__.__name__
        return result

    result["target"] = target
    result["elements"] = elements
    return result


def _uia_window(handle: int) -> Any:
    from pywinauto import Desktop

    desktop = Desktop(backend="uia")
    return desktop.window(handle=int(handle))


def _resolve_target_window(
    request: dict[str, Any],
    windows: list[dict[str, Any]],
) -> dict[str, Any] | None:
    handle = request.get("handle")
    if handle is not None:
        for window in windows:
            if str(window.get("handle")) == str(handle):
                return window

    title = request.get("title")
    class_name = request.get("class_name")
    pid = request.get("pid")
    if title is None and class_name is None and pid is None:
        return None

    for window in windows:
        if title is not None and str(title).lower() not in str(
            window.get("title", "")
        ).lower():
            continue
        if class_name is not None and str(class_name) != str(
            window.get("class_name", "")
        ):
            continue
        if pid is not None and str(pid) != str(window.get("pid")):
            continue
        return window
    return None


__all__ = [
    "DEFAULT_MAX_DEPTH",
    "DEFAULT_MAX_ELEMENTS",
    "DEFAULT_MAX_WINDOWS",
    "collect_elements",
    "describe_windows",
    "inspect_desktop",
]
