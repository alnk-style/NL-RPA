from __future__ import annotations

import ctypes
import os
import threading


PARENT_PID_ENV = "NL_RPA_PARENT_PID"
_SYNCHRONIZE = 0x00100000
_WAIT_INFINITE = 0xFFFFFFFF


def parent_pid_from_env(
    environment: dict[str, str] | None = None,
) -> int | None:
    source = os.environ if environment is None else environment
    raw_value = source.get(PARENT_PID_ENV)
    if raw_value is None:
        return None
    try:
        pid = int(raw_value)
    except (TypeError, ValueError):
        return None
    return pid if pid > 0 else None


def _parent_process_id(pid: int) -> int | None:
    if os.name != "nt" or pid <= 0:
        return None

    from ctypes import wintypes

    th32cs_snapprocess = 0x00000002
    invalid_handle_value = ctypes.c_void_p(-1).value

    class ProcessEntry32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateToolhelp32Snapshot.argtypes = [
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ProcessEntry32W),
    ]
    kernel32.Process32NextW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ProcessEntry32W),
    ]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    snapshot = kernel32.CreateToolhelp32Snapshot(
        th32cs_snapprocess,
        0,
    )
    if snapshot == invalid_handle_value:
        return None

    entry = ProcessEntry32W()
    entry.dwSize = ctypes.sizeof(ProcessEntry32W)
    parent_pid: int | None = None
    try:
        has_entry = kernel32.Process32FirstW(
            snapshot,
            ctypes.byref(entry),
        )
        while has_entry:
            if entry.th32ProcessID == pid:
                candidate = int(entry.th32ParentProcessID)
                if candidate > 0:
                    parent_pid = candidate
                break
            has_entry = kernel32.Process32NextW(
                snapshot,
                ctypes.byref(entry),
            )
    finally:
        kernel32.CloseHandle(snapshot)

    return parent_pid


def watch_parent_process() -> bool:
    """Exit the current sidecar when the desktop App process exits."""

    pid = parent_pid_from_env()
    if pid is None:
        pid = _parent_process_id(os.getppid())
    if pid is None or os.name != "nt":
        return False
    if pid in {0, 4} or pid == os.getpid():
        return False

    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.WaitForSingleObject.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
    ]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

    handle = kernel32.OpenProcess(_SYNCHRONIZE, False, pid)
    if not handle:
        return False

    def wait_for_parent() -> None:
        try:
            kernel32.WaitForSingleObject(handle, _WAIT_INFINITE)
        finally:
            kernel32.CloseHandle(handle)
            os._exit(0)

    threading.Thread(
        target=wait_for_parent,
        name="nl-rpa-parent-watch",
        daemon=True,
    ).start()
    return True


__all__ = ["PARENT_PID_ENV", "parent_pid_from_env", "watch_parent_process"]
