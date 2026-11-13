from __future__ import annotations

import asyncio
from typing import Any

from desktop_agent.actions import ActionResult, register_action


async def handle_press_hotkey(
    params: dict[str, Any],
    context: dict[str, Any],
) -> ActionResult:
    keys = params.get("keys")
    if not isinstance(keys, str) or not keys:
        raise ValueError("press_hotkey requires a non-empty keys string")

    await asyncio.to_thread(_send_hotkey, keys)
    return ActionResult(
        success=True,
        message=f"Sent hotkey: {keys}",
        window_handle=context.get("last_window_handle"),
        data={"keys": keys},
    )


def _send_hotkey(keys: str) -> None:
    from pywinauto.keyboard import send_keys

    send_keys(keys)


register_action("press_hotkey", handle_press_hotkey)

__all__ = ["handle_press_hotkey"]
