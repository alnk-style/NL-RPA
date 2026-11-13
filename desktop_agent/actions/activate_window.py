from __future__ import annotations

import asyncio
from typing import Any

from desktop_agent.actions import ActionResult, register_action
from desktop_agent.actions._win32 import (
    activate_window,
    describe_window_candidates,
    find_windows,
)
from desktop_agent.uia_observation import candidate_labels


async def handle_activate_window(
    params: dict[str, Any],
    context: dict[str, Any],
) -> ActionResult:
    title = params.get("title")
    class_name = params.get("class_name")
    pid_value = params.get("pid")
    if isinstance(pid_value, str) and pid_value in {"last", "last_process"}:
        pid_value = context.get("last_pid")

    if title is not None and not isinstance(title, str):
        raise ValueError("activate_window title must be a string")
    if class_name is not None and not isinstance(class_name, str):
        raise ValueError("activate_window class_name must be a string")

    pid: int | None = None
    if pid_value is not None:
        try:
            pid = int(pid_value)
        except (TypeError, ValueError) as exc:
            raise ValueError("activate_window pid must be an integer") from exc

    if pid is None and not title and not class_name:
        raise ValueError(
            "activate_window requires title, class_name, or pid"
        )

    try:
        index = int(params.get("index", 0))
    except (TypeError, ValueError) as exc:
        raise ValueError("activate_window index must be an integer") from exc
    if index < 0:
        raise ValueError("activate_window index must not be negative")

    windows = await asyncio.to_thread(
        find_windows,
        title=title,
        class_name=class_name,
        pid=pid,
        visible_only=True,
    )
    if index >= len(windows):
        candidates = await asyncio.to_thread(
            describe_window_candidates,
            limit=8,
        )
        labels = candidate_labels(candidates)
        message = "No matching window found"
        if labels:
            message += "；当前可见窗口：" + "、".join(labels)
        data: dict[str, Any] = {}
        if candidates:
            data["candidates"] = candidates
            data["candidate_labels"] = labels
        return ActionResult(
            success=False,
            message=message,
            data=data,
        )

    window = windows[index]
    focused = await asyncio.to_thread(activate_window, window.handle, 2.0)
    context["last_window_handle"] = window.handle
    context["last_window_title"] = window.title

    return ActionResult(
        success=True,
        message=f"Activated window: {window.title}",
        pid=window.pid,
        window_handle=window.handle,
        data={"focused": focused},
    )


register_action("activate_window", handle_activate_window)

__all__ = ["handle_activate_window"]
