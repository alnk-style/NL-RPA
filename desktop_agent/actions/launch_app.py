from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from desktop_agent.actions import ActionResult, register_action
from desktop_agent.actions._win32 import (
    WindowInfo,
    activate_window,
    enum_windows,
    find_windows,
    process_image_path,
    window_rect,
)

logger = logging.getLogger(__name__)


async def handle_launch_app(
    params: dict[str, Any],
    context: dict[str, Any],
) -> ActionResult:
    path = params.get("path")
    if not isinstance(path, str) or not path.strip():
        raise ValueError("launch_app requires a non-empty path")
    path = _resolve_executable(path.strip())

    args = params.get("args") or []
    if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
        raise ValueError("launch_app args must be a list of strings")

    reuse_existing = bool(params.get("reuse_existing", True))
    if reuse_existing:
        existing_window = await asyncio.to_thread(
            _find_existing_application_window,
            path,
        )
        if existing_window is not None:
            focused = await asyncio.to_thread(
                activate_window,
                existing_window.handle,
                2.0,
            )
            context["last_window_handle"] = existing_window.handle
            context["last_window_title"] = existing_window.title
            context["last_pid"] = existing_window.pid
            return ActionResult(
                success=True,
                message=(
                    "Activated existing application window: "
                    f"{existing_window.title or path}"
                ),
                pid=existing_window.pid,
                window_handle=existing_window.handle,
                data={
                    "state": "reused_existing",
                    "focused": focused,
                },
            )

        running_pids = await asyncio.to_thread(
            _find_application_processes,
            path,
        )
        if running_pids:
            return ActionResult(
                success=False,
                message=(
                    "Application is already running but no window "
                    "was found"
                ),
                pid=running_pids[0],
                data={"state": "running_without_window"},
            )

    wait_for_window = bool(params.get("wait_for_window", True))
    existing_window_handles: set[int] = set()
    if wait_for_window:
        existing_window_handles = {
            window.handle
            for window in await asyncio.to_thread(
                enum_windows,
                visible_only=True,
            )
        }

    process = await asyncio.to_thread(
        subprocess.Popen,
        [path, *args],
        close_fds=True,
    )
    process_info = {
        "process": process,
        "pid": process.pid,
        "launcher_pid": process.pid,
        "path": path,
        "args": list(args),
    }
    processes = context.setdefault("processes", {})
    processes[path] = process_info
    context["last_process"] = process_info
    context["last_pid"] = process.pid

    state = "started"
    window_handle: int | None = None
    if wait_for_window:
        try:
            timeout = max(float(params.get("window_timeout", 10)), 0)
        except (TypeError, ValueError):
            timeout = 10.0
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if process.poll() is not None:
                state = "process_exited"
            windows = await asyncio.to_thread(find_windows, pid=process.pid)
            if not windows:
                windows = await asyncio.to_thread(
                    _find_new_application_window,
                    existing_window_handles,
                    path,
                )
            if windows:
                window_handle = windows[0].handle
                process_info["pid"] = windows[0].pid
                process_info["window_handle"] = window_handle
                context["last_window_handle"] = window_handle
                context["last_window_title"] = windows[0].title
                context["last_pid"] = windows[0].pid
                await asyncio.to_thread(
                    activate_window,
                    window_handle,
                    2.0,
                )
                state = "window_found"
                break
            await asyncio.sleep(0.1)
        if window_handle is None:
            logger.warning(
                "Application started but no main window appeared: %s",
                path,
            )
            state = "window_timeout"

    return ActionResult(
        success=True,
        message=f"已启动 {path}",
        pid=int(process_info["pid"]),
        window_handle=window_handle,
        data={"state": state},
    )


def _find_new_application_window(
    existing_handles: set[int],
    app_path: str,
) -> list[WindowInfo]:
    target_name = Path(app_path).stem.casefold()
    matches: list[WindowInfo] = []
    for window in enum_windows(visible_only=True):
        if window.handle in existing_handles:
            continue

        image_path = process_image_path(window.pid)
        if image_path is not None:
            if Path(image_path).stem.casefold() == target_name:
                matches.append(window)
                continue

        if target_name in window.title.casefold():
            matches.append(window)
    return matches


def _find_existing_application_window(
    app_path: str,
) -> WindowInfo | None:
    matches: list[tuple[WindowInfo, str | None]] = []
    target_name = Path(app_path).stem.casefold()
    for window in enum_windows(visible_only=False):
        image_path = process_image_path(window.pid)
        if image_path is not None and _same_application_path(
            image_path,
            app_path,
        ):
            matches.append((window, image_path))
        if image_path is not None:
            continue
        if target_name and target_name in window.title.casefold():
            matches.append((window, None))
    if not matches:
        return None
    return max(
        matches,
        key=lambda item: _window_score(
            item[0],
            app_path=app_path,
            image_path=item[1],
            visible=_window_visible(item[0].handle),
            iconic=_window_iconic(item[0].handle),
        ),
    )[0]


def _find_application_processes(app_path: str) -> list[int]:
    try:
        import win32process
    except ImportError:
        return []

    matches: list[int] = []
    for pid in win32process.EnumProcesses():
        image_path = process_image_path(pid)
        if image_path and _same_application_path(image_path, app_path):
            matches.append(int(pid))
    return matches


def _same_application_path(candidate: str, target: str) -> bool:
    candidate_normalized = os.path.normcase(
        os.path.normpath(candidate)
    )
    target_normalized = os.path.normcase(os.path.normpath(target))
    if candidate_normalized == target_normalized:
        return True
    return (
        Path(candidate).name.casefold()
        == Path(target).name.casefold()
    )


def _window_visible(handle: int) -> bool:
    try:
        import win32gui

        return bool(win32gui.IsWindowVisible(handle))
    except Exception:
        return False


def _window_iconic(handle: int) -> bool:
    try:
        import win32gui

        return bool(win32gui.IsIconic(handle))
    except Exception:
        return False


def _window_score(
    window: WindowInfo,
    *,
    app_path: str | None = None,
    image_path: str | None = None,
    visible: bool = False,
    iconic: bool = False,
) -> tuple[bool, bool, bool, int]:
    exact_path = False
    if app_path and image_path:
        exact_path = (
            os.path.normcase(os.path.normpath(image_path))
            == os.path.normcase(os.path.normpath(app_path))
        )
    try:
        left, top, right, bottom = window_rect(window.handle)
        area = max((right - left) * (bottom - top), 0)
    except Exception:
        area = 0
    return (
        exact_path,
        bool(visible),
        bool(window.title.strip()),
        area,
    )


def _resolve_executable(path: str) -> str:
    """把白名单里的程序名解析成可启动的路径。

    白名单允许填写完整路径、notepad.exe 这类可执行名，或仅程序名。
    """

    if os.path.dirname(path) or os.path.isabs(path):
        return path

    candidates = [path]
    if not os.path.splitext(path)[1]:
        candidates.insert(0, f"{path}.exe")

    for candidate in candidates:
        located = shutil.which(candidate)
        if located:
            return located

    for candidate in candidates:
        registered = _lookup_app_paths(candidate)
        if registered:
            return registered

    return candidates[0]


def _lookup_app_paths(name: str) -> str | None:
    try:
        import winreg
    except ImportError:
        return None

    subkey = (
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"
        f"\\{name}"
    )
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, subkey) as key:
                value, _ = winreg.QueryValueEx(key, "")
        except OSError:
            continue
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


register_action("launch_app", handle_launch_app)

__all__ = ["handle_launch_app"]
