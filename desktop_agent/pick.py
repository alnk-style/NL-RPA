"""桌面元素拾取：读取鼠标指针下方的控件，生成录制用的定位信息。

只读取 UI Automation 信息，不点击、不修改任何界面状态。
"""

from __future__ import annotations

from typing import Any

from desktop_agent.uia_observation import element_summary

MAX_PARENT_STEPS = 64


def pick_element(
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """处理 desktop.pick 请求，返回光标下控件与所属窗口。"""

    request = payload if isinstance(payload, dict) else {}
    try:
        x, y = _cursor_position(request)
        element = _element_from_point(x, y)
    except Exception as exc:
        return {
            "success": False,
            "element": None,
            "window": None,
            "point": None,
            "error": str(exc) or exc.__class__.__name__,
        }

    summary = _element_payload(element)
    if summary is None:
        return {
            "success": False,
            "element": None,
            "window": None,
            "point": {"x": x, "y": y},
            "error": "光标下的控件没有名称或 AutomationId",
        }

    return {
        "success": True,
        "element": summary,
        "window": _window_payload(element),
        "point": {"x": x, "y": y},
        "error": None,
    }


def _element_payload(element: Any) -> dict[str, Any] | None:
    summary = element_summary(element, depth=0, index=0)
    if summary is None:
        return None
    rectangle = summary.get("rectangle")
    if isinstance(rectangle, dict):
        summary = dict(summary)
        summary["center"] = {
            "x": int(
                (int(rectangle.get("left", 0)) + int(rectangle.get("right", 0)))
                / 2
            ),
            "y": int(
                (int(rectangle.get("top", 0)) + int(rectangle.get("bottom", 0)))
                / 2
            ),
        }
    return summary


def _window_payload(element: Any) -> dict[str, Any] | None:
    """沿父链找到带有原生窗口句柄的祖先，作为流程窗口。"""

    info = getattr(element, "element_info", None)
    for _ in range(MAX_PARENT_STEPS):
        if info is None:
            return None
        handle = getattr(info, "handle", None)
        if handle:
            window: dict[str, Any] = {"handle": int(handle)}
            title = _text(getattr(info, "name", ""))
            class_name = _text(getattr(info, "class_name", ""))
            pid = getattr(info, "process_id", None)
            if title:
                window["title"] = title
            if class_name:
                window["class_name"] = class_name
            if isinstance(pid, int) and pid > 0:
                window["pid"] = pid
            return window
        info = getattr(info, "parent", None)
    return None


def _cursor_position(request: dict[str, Any]) -> tuple[int, int]:
    x = request.get("x")
    y = request.get("y")
    if isinstance(x, (int, float)) and isinstance(y, (int, float)):
        return int(x), int(y)

    import win32api

    position = win32api.GetCursorPos()
    return int(position[0]), int(position[1])


def _element_from_point(x: int, y: int) -> Any:
    from pywinauto import Desktop

    desktop = Desktop(backend="uia")
    return desktop.from_point(int(x), int(y))


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())[:120]


__all__ = ["pick_element"]
