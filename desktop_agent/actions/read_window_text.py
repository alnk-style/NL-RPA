"""Read visible UIA text from a target window."""

from __future__ import annotations

import asyncio
import re
from typing import Any

from desktop_agent.actions import ActionResult, register_action
from desktop_agent.actions._win32 import find_windows
from desktop_agent.uia_observation import (
    element_value,
    iter_elements,
)


_TIME_ONLY = re.compile(r"^\d{1,2}:\d{2}(?:/\d{1,2}:\d{2})?$")
_IGNORED_TEXT = {
    "播放",
    "暂停",
    "上一首",
    "下一首",
    "收藏",
    "下载",
    "评论",
}


async def handle_read_window_text(
    params: dict[str, Any],
    context: dict[str, Any],
) -> ActionResult:
    title = str(params.get("title") or "").strip()
    class_name = str(params.get("class_name") or "").strip() or None
    pid = params.get("pid")
    handle = params.get("handle") or context.get("last_window_handle")
    if title or class_name or pid is not None:
        windows = await asyncio.to_thread(
            find_windows,
            title=title or None,
            class_name=class_name,
            pid=int(pid) if pid is not None else None,
            visible_only=True,
        )
        if not windows:
            raise ValueError(
                f"read_window_text window not found: {title or class_name}"
            )
        handle = windows[0].handle
    if not handle:
        raise ValueError("read_window_text requires a target window")

    min_chars = max(1, int(params.get("min_chars", 2)))
    text = await asyncio.to_thread(
        _read_window_text,
        int(handle),
        min_chars,
    )
    return ActionResult(
        success=True,
        message="Window text read",
        window_handle=int(handle),
        text=text,
        data={
            "characters": len(text),
            "line_count": len(text.splitlines()),
            "title": title,
        },
    )


def _read_window_text(handle: int, min_chars: int) -> str:
    from pywinauto import Desktop

    window = Desktop(backend="uia").window(handle=int(handle))
    lines: list[str] = []
    seen: set[str] = set()
    for element, _depth in iter_elements(
        window,
        max_elements=500,
        max_depth=10,
    ):
        try:
            info = element.element_info
            control_type = str(
                getattr(info, "control_type", "") or ""
            )
            if control_type not in {"Text", "Document", "Edit"}:
                continue
            name = " ".join(
                str(getattr(info, "name", "") or "").split()
            )
            value = " ".join(
                str(element_value(element) or "").split()
            )
            text = " ".join(
                item
                for item in dict.fromkeys((name, value))
                if item
            )
        except Exception:
            continue
        if not text or text in _IGNORED_TEXT or _TIME_ONLY.fullmatch(text):
            continue
        if text in seen:
            continue
        seen.add(text)
        lines.append(text)
    result = "\n".join(lines).strip()
    if len(result) < min_chars:
        raise ValueError("No readable text found in target window")
    return result


register_action("read_window_text", handle_read_window_text)

__all__ = ["handle_read_window_text"]
