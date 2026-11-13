from __future__ import annotations

import asyncio
from typing import Any

from desktop_agent.actions import ActionResult, register_action


async def handle_type_text(
    params: dict[str, Any],
    context: dict[str, Any],
) -> ActionResult:
    text = str(params.get("text", ""))
    clear_first = bool(params.get("clear_first", False))
    method = str(params.get("method", "send_keys")).strip().lower()
    if method not in {"send_keys", "clipboard"}:
        raise ValueError("type_text method must be send_keys or clipboard")

    if clear_first:
        await asyncio.to_thread(_send_clear)

    if method == "clipboard":
        await asyncio.to_thread(_set_clipboard_text, text)
        await asyncio.to_thread(_send_keys, "^v")
    else:
        await asyncio.to_thread(_send_text, text)

    return ActionResult(
        success=True,
        message="Text entered",
        text=text,
        window_handle=context.get("last_window_handle"),
    )


def _send_clear() -> None:
    _send_keys("^a{BACKSPACE}")


def _send_text(text: str) -> None:
    from pywinauto.keyboard import send_keys

    send_keys(
        text,
        with_spaces=True,
        with_tabs=True,
        with_newlines=True,
    )


def _send_keys(keys: str) -> None:
    from pywinauto.keyboard import send_keys

    send_keys(keys)


def _set_clipboard_text(text: str) -> None:
    import win32clipboard
    import win32con

    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(
            win32con.CF_UNICODETEXT,
            text,
        )
    finally:
        win32clipboard.CloseClipboard()


register_action("type_text", handle_type_text)

__all__ = ["handle_type_text"]
