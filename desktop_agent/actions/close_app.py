from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from desktop_agent.actions import ActionResult, register_action
from desktop_agent.actions._win32 import (
    activate_window,
    find_windows,
    is_process_running,
    terminate_process,
)


async def handle_close_app(
    params: dict[str, Any],
    context: dict[str, Any],
) -> ActionResult:
    target = str(params.get("target", "last")).strip() or "last"
    force = bool(params.get("force", False))
    try:
        graceful_timeout = max(float(params.get("graceful_timeout", 5)), 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("close_app graceful_timeout must be a number") from exc

    path, process_info = _resolve_process(target, context)
    if process_info is None:
        return ActionResult(
            success=False,
            message=f"No launched process matches target: {target}",
        )

    process = process_info["process"]
    pid = int(process_info.get("pid") or process.pid)
    windows = await asyncio.to_thread(find_windows, pid=pid)
    if not is_process_running(pid) and not windows:
        _remove_process(context, path)
        return ActionResult(
            success=True,
            message=f"Process already exited: {path}",
            pid=pid,
        )

    if windows:
        await asyncio.to_thread(activate_window, windows[0].handle, 1.0)
        await asyncio.to_thread(_send_alt_f4)

    deadline = time.monotonic() + graceful_timeout
    while time.monotonic() < deadline:
        if not is_process_running(pid):
            _remove_process(context, path)
            return ActionResult(
                success=True,
                message=f"Closed process gracefully: {path}",
                pid=pid,
            )
        await asyncio.sleep(0.1)

    if is_process_running(pid):
        await asyncio.to_thread(terminate_process, pid)

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and is_process_running(pid):
        await asyncio.sleep(0.1)

    if is_process_running(pid) and process.poll() is None:
        process.terminate()
        try:
            await asyncio.to_thread(process.wait, timeout=2)
        except Exception:
            process.kill()
            await asyncio.to_thread(process.wait, timeout=2)

    if is_process_running(pid):
        return ActionResult(
            success=False,
            message=f"Failed to close process: {path}",
            pid=pid,
        )

    _remove_process(context, path)
    return ActionResult(
        success=True,
        message=f"Force closed process: {path}",
        pid=pid,
        data={"force_requested": force},
    )


def _resolve_process(
    target: str,
    context: dict[str, Any],
) -> tuple[str, dict[str, Any] | None]:
    processes = context.get("processes") or {}
    if target == "last":
        info = context.get("last_process")
        if not isinstance(info, dict):
            return "last", None
        return str(info.get("path", "last")), info

    normalized = target.lower()
    for path, value in processes.items():
        if not isinstance(value, dict):
            continue
        path_name = Path(str(path)).name.lower()
        path_stem = Path(path_name).stem
        if normalized in {str(path).lower(), path_name, path_stem}:
            return str(path), value
        try:
            if int(value.get("pid")) == int(target):
                return str(path), value
        except (TypeError, ValueError):
            pass
    return target, None


def _remove_process(context: dict[str, Any], path: str) -> None:
    processes = context.get("processes")
    if isinstance(processes, dict):
        processes.pop(path, None)
    if (
        isinstance(context.get("last_process"), dict)
        and context["last_process"].get("path") == path
    ):
        context["last_process"] = None


def _send_alt_f4() -> None:
    from pywinauto.keyboard import send_keys

    send_keys("%{F4}")


register_action("close_app", handle_close_app)

__all__ = ["handle_close_app"]
