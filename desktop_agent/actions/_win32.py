from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import PureWindowsPath
from typing import Any


@dataclass(frozen=True)
class WindowInfo:
    handle: int
    title: str
    class_name: str
    pid: int


def enum_windows(*, visible_only: bool = False) -> list[WindowInfo]:
    import win32gui
    import win32process

    windows: list[WindowInfo] = []

    def callback(handle: int, _extra: object) -> bool:
        if visible_only and not win32gui.IsWindowVisible(handle):
            return True

        try:
            _, process_id = win32process.GetWindowThreadProcessId(handle)
            windows.append(
                WindowInfo(
                    handle=handle,
                    title=win32gui.GetWindowText(handle),
                    class_name=win32gui.GetClassName(handle),
                    pid=int(process_id),
                )
            )
        except Exception:
            pass
        return True

    win32gui.EnumWindows(callback, None)
    return windows


def find_windows(
    *,
    title: str | None = None,
    class_name: str | None = None,
    pid: int | None = None,
    visible_only: bool = False,
) -> list[WindowInfo]:
    windows = enum_windows(visible_only=visible_only)
    if title is not None:
        normalized_title = title.lower()
        windows = [
            window for window in windows if normalized_title in window.title.lower()
        ]
    if class_name is not None:
        windows = [window for window in windows if window.class_name == class_name]
    if pid is not None:
        windows = [window for window in windows if window.pid == pid]
    return windows


def describe_window_candidates(
    *,
    limit: int = 8,
    visible_only: bool = True,
) -> list[dict[str, Any]]:
    """列出可见窗口的候选详情，供窗口定位失败时修复参考。"""

    if limit <= 0:
        return []

    candidates: list[dict[str, Any]] = []
    for window in enum_windows(visible_only=visible_only):
        title = window.title.strip()
        if not title:
            continue
        candidate: dict[str, Any] = {
            "handle": int(window.handle),
            "title": title,
            "class_name": window.class_name,
            "pid": int(window.pid),
        }
        image_path = process_image_path(window.pid)
        if image_path:
            candidate["process_name"] = PureWindowsPath(image_path).name
        candidates.append(candidate)
        if len(candidates) >= limit:
            break
    return candidates


def activate_window(handle: int, timeout: float = 2.0) -> bool:
    import win32con
    import win32gui

    if not win32gui.IsWindow(handle):
        return False

    try:
        if win32gui.IsIconic(handle):
            win32gui.ShowWindow(handle, win32con.SW_RESTORE)
        else:
            win32gui.ShowWindow(handle, win32con.SW_SHOW)
        win32gui.BringWindowToTop(handle)
        win32gui.SetForegroundWindow(handle)
    except Exception:
        pass

    deadline = time.monotonic() + max(timeout, 0)
    while time.monotonic() < deadline:
        if win32gui.GetForegroundWindow() == handle:
            return True
        time.sleep(0.05)
    return win32gui.GetForegroundWindow() == handle


def window_title(handle: int) -> str:
    import win32gui

    return win32gui.GetWindowText(handle)


def window_rect(handle: int) -> tuple[int, int, int, int]:
    import win32gui

    left, top, right, bottom = win32gui.GetWindowRect(handle)
    return int(left), int(top), int(right), int(bottom)


def foreground_window() -> int | None:
    import win32gui

    handle = int(win32gui.GetForegroundWindow())
    return handle or None


def process_image_path(pid: int) -> str | None:
    import pywintypes
    import win32api
    import win32con
    import win32process

    access = win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ
    try:
        handle = win32api.OpenProcess(access, False, pid)
    except pywintypes.error:
        return None
    try:
        return str(win32process.GetModuleFileNameEx(handle, 0))
    except pywintypes.error:
        return None
    finally:
        win32api.CloseHandle(handle)


def is_process_running(pid: int) -> bool:
    import pywintypes
    import win32api
    import win32con
    import win32process

    try:
        handle = win32api.OpenProcess(
            win32con.PROCESS_QUERY_LIMITED_INFORMATION,
            False,
            pid,
        )
    except pywintypes.error:
        return False
    try:
        exit_code = win32process.GetExitCodeProcess(handle)
        return exit_code == win32con.STILL_ACTIVE
    except pywintypes.error:
        return False
    finally:
        win32api.CloseHandle(handle)


def terminate_process(pid: int) -> bool:
    import pywintypes
    import win32api
    import win32con
    import win32process

    try:
        handle = win32api.OpenProcess(
            win32con.PROCESS_TERMINATE,
            False,
            pid,
        )
    except pywintypes.error:
        return False
    try:
        win32process.TerminateProcess(handle, 1)
        return True
    except pywintypes.error:
        return False
    finally:
        win32api.CloseHandle(handle)
