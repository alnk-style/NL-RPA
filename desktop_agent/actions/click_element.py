from __future__ import annotations

import asyncio
import time
from typing import Any

from desktop_agent.actions import ActionResult, register_action
from desktop_agent.actions._uia import describe_candidates, find_element
from desktop_agent.uia_observation import candidate_labels


async def handle_click_element(
    params: dict[str, Any],
    context: dict[str, Any],
) -> ActionResult:
    automation_id = _optional_string(params.get("automation_id"))
    name = _optional_string(params.get("name"))
    control_type = _optional_string(params.get("control_type"))
    button = str(params.get("button", "left")).lower()
    double_click = bool(params.get("double_click", False))
    if button not in {"left", "right"}:
        raise ValueError("click_element button must be left or right")

    if any((automation_id, name, control_type)):
        element = await asyncio.to_thread(
            find_element,
            context,
            automation_id=automation_id,
            name=name,
            control_type=control_type,
        )
        if element is not None:
            await asyncio.to_thread(
                _click_uia,
                element,
                button,
                double_click,
            )
            data: dict[str, Any] = {}
            last_locator = context.get("last_locator")
            if isinstance(last_locator, dict):
                data["last_locator"] = dict(last_locator)
            return ActionResult(
                success=True,
                message="Clicked UI Automation element",
                window_handle=context.get("last_window_handle"),
                data=data,
            )

    if "x" not in params or "y" not in params:
        candidates = await asyncio.to_thread(
            describe_candidates,
            context,
            name=name,
            automation_id=automation_id,
            control_type=control_type,
        )
        labels = candidate_labels(candidates)
        message = "No matching UI Automation element and no coordinates"
        if labels:
            message = (
                "No matching UI Automation element；"
                "当前窗口可用控件："
                + "、".join(labels)
            )
        data = {"candidates": candidates}
        if labels:
            data["candidate_labels"] = labels
        last_locator = context.get("last_locator")
        if isinstance(last_locator, dict):
            data["last_locator"] = dict(last_locator)
        return ActionResult(
            success=False,
            message=message,
            data=data,
        )

    x = int(params["x"])
    y = int(params["y"])
    await asyncio.to_thread(_click_coordinates, x, y, button, double_click)
    return ActionResult(
        success=True,
        message=f"Clicked screen coordinates ({x}, {y})",
        window_handle=context.get("last_window_handle"),
        data={"x": x, "y": y},
    )


def _click_uia(element: Any, button: str, double_click: bool) -> None:
    clicks = 2 if double_click else 1
    for index in range(clicks):
        if index:
            time.sleep(0.1)
        element.click_input(button=button)


def _click_coordinates(
    x: int,
    y: int,
    button: str,
    double_click: bool,
) -> None:
    import win32api
    import win32con

    down, up = (
        (win32con.MOUSEEVENTF_LEFTDOWN, win32con.MOUSEEVENTF_LEFTUP)
        if button == "left"
        else (win32con.MOUSEEVENTF_RIGHTDOWN, win32con.MOUSEEVENTF_RIGHTUP)
    )
    win32api.SetCursorPos((x, y))
    for index in range(2 if double_click else 1):
        if index:
            time.sleep(0.1)
        win32api.mouse_event(down, 0, 0, 0, 0)
        win32api.mouse_event(up, 0, 0, 0, 0)


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


register_action("click_element", handle_click_element)

__all__ = ["handle_click_element"]
