"""Copy the current selection and read its text from the clipboard."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from desktop_agent.actions import ActionResult, register_action


def _send_keys(keys: str) -> None:
    from pywinauto.keyboard import send_keys

    send_keys(keys)


def _read_clipboard_text() -> str:
    import win32clipboard
    import win32con

    for attempt in range(5):
        try:
            win32clipboard.OpenClipboard()
            break
        except Exception:
            if attempt == 4:
                raise
            time.sleep(0.05)
    try:
        if not win32clipboard.IsClipboardFormatAvailable(
            win32con.CF_UNICODETEXT
        ):
            return ""
        value = win32clipboard.GetClipboardData(
            win32con.CF_UNICODETEXT
        )
        return str(value or "")
    finally:
        win32clipboard.CloseClipboard()


async def handle_copy_selected_text(
    params: dict[str, Any],
    context: dict[str, Any],
) -> ActionResult:
    del context
    keys = str(params.get("keys") or "^a^c").strip()
    if not keys:
        raise ValueError("copy_selected_text requires non-empty keys")
    min_chars = max(0, int(params.get("min_chars", 1)))
    settle_seconds = max(
        0.0,
        float(params.get("settle_seconds", 0.4)),
    )
    timeout_seconds = max(
        0.1,
        float(params.get("timeout_seconds", 3)),
    )

    text = ""
    for attempt in range(2):
        await asyncio.to_thread(_send_keys, keys)
        await asyncio.sleep(settle_seconds + (0.2 * attempt))
        text = await asyncio.wait_for(
            asyncio.to_thread(_read_clipboard_text),
            timeout=timeout_seconds,
        )
        if len(text.strip()) >= min_chars:
            break

    if len(text.strip()) < min_chars:
        raise ValueError("Clipboard text is empty after copy")
    return ActionResult(
        success=True,
        message="Text copied",
        text=text,
        data={
            "keys": keys,
            "characters": len(text),
            "format": "text",
        },
    )


register_action("copy_selected_text", handle_copy_selected_text)

__all__ = ["handle_copy_selected_text"]
