"""Click a point expressed as a fraction of the active window."""

from __future__ import annotations

import asyncio
from typing import Any

from desktop_agent.actions import ActionResult, register_action
from desktop_agent.actions._win32 import (
    foreground_window,
    window_rect,
)
from desktop_agent.actions.click_element import _click_coordinates


async def handle_click_relative(
    params: dict[str, Any],
    context: dict[str, Any],
) -> ActionResult:
    try:
        x_ratio = float(params.get("x"))
        y_ratio = float(params.get("y"))
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "click_relative requires x and y in 0..1"
        ) from exc
    if not 0 <= x_ratio <= 1 or not 0 <= y_ratio <= 1:
        raise ValueError("click_relative x and y must be in 0..1")

    handle = context.get("last_window_handle")
    if not handle:
        handle = await asyncio.to_thread(foreground_window)
    if not handle:
        raise ValueError("click_relative requires an active window")
    left, top, right, bottom = await asyncio.to_thread(
        window_rect,
        int(handle),
    )
    width = max(1, right - left)
    height = max(1, bottom - top)
    x = left + round(width * x_ratio)
    y = top + round(height * y_ratio)
    button = str(params.get("button") or "left").lower()
    if button not in {"left", "right"}:
        raise ValueError("click_relative button must be left or right")
    double_click = bool(params.get("double_click", False))
    await asyncio.to_thread(
        _click_coordinates,
        x,
        y,
        button,
        double_click,
    )
    return ActionResult(
        success=True,
        message=f"Clicked relative point ({x_ratio}, {y_ratio})",
        window_handle=int(handle),
        data={"x": x, "y": y, "ratio_x": x_ratio, "ratio_y": y_ratio},
    )


register_action("click_relative", handle_click_relative)

__all__ = ["handle_click_relative"]
