from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from desktop_agent.actions import activate_window as activate_window_module
from desktop_agent.actions import click_element as click_element_module
from desktop_agent.actions import launch_app as launch_app_module
from desktop_agent.actions import read_text as read_text_module
from desktop_agent.actions import wait_action as wait_action_module
from desktop_agent.actions import copy_selected_text as copy_text_module
from desktop_agent.actions import click_relative as click_relative_module
from desktop_agent.actions import read_window_text as read_window_text_module
from desktop_agent.actions import fetch_kugou_lyrics as kugou_lyrics_module
from desktop_agent.actions._uia import find_element
from desktop_agent.actions._win32 import WindowInfo, enum_windows, find_windows
from desktop_agent.actions.activate_window import handle_activate_window
from desktop_agent.actions.click_element import handle_click_element
from desktop_agent.actions.close_app import handle_close_app
from desktop_agent.actions.launch_app import handle_launch_app
from desktop_agent.actions.press_hotkey import handle_press_hotkey
from desktop_agent.actions.read_text import handle_read_text
from desktop_agent.actions.screenshot_action import handle_screenshot
from desktop_agent.actions.type_text import handle_type_text
from desktop_agent.actions.wait_action import handle_wait
from desktop_agent.action_validator import ActionValidator
from desktop_agent.config import AgentConfig

WINDOWS_ONLY = pytest.mark.skipif(
    sys.platform != "win32",
    reason="Windows only",
)


def _context(tmp_path: Path, task_id: int = 1) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "step_index": 0,
        "log_dir": str(tmp_path),
        "processes": {},
    }


def _run(awaitable):
    return asyncio.run(awaitable)


def _notepad_path() -> Path:
    return Path(os.environ.get("SystemRoot", r"C:\Windows")) / (
        "System32/notepad.exe"
    )


def _calc_path() -> Path:
    return Path(os.environ.get("SystemRoot", r"C:\Windows")) / (
        "System32/calc.exe"
    )


def _require_interactive_desktop() -> None:
    try:
        enum_windows()
    except Exception as exc:
        pytest.skip(f"Interactive Windows desktop unavailable: {exc}")


def _cleanup(context: dict[str, Any]) -> None:
    processes = context.get("processes") or {}
    for info in processes.values():
        process = info.get("process") if isinstance(info, dict) else None
        if process is None or process.poll() is not None:
            continue
        try:
            process.kill()
            process.wait(timeout=2)
        except Exception:
            pass


def _wait_for_window(pid: int, timeout: float = 5) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        windows = find_windows(pid=pid)
        if windows:
            return {
                "handle": windows[0].handle,
                "title": windows[0].title,
                "class_name": windows[0].class_name,
            }
        time.sleep(0.1)
    return {}


def test_copy_selected_text_reads_clipboard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    keys: list[str] = []
    monkeypatch.setattr(copy_text_module, "_send_keys", keys.append)
    monkeypatch.setattr(
        copy_text_module,
        "_read_clipboard_text",
        lambda: "第一句\n第二句",
    )

    result = _run(
        copy_text_module.handle_copy_selected_text(
            {"keys": "^a^c", "settle_seconds": 0},
            _context(tmp_path),
        )
    )

    assert result.success is True
    assert result.text == "第一句\n第二句"
    assert keys == ["^a^c"]


def test_copy_selected_text_rejects_empty_clipboard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(copy_text_module, "_send_keys", lambda _keys: None)
    monkeypatch.setattr(
        copy_text_module,
        "_read_clipboard_text",
        lambda: " ",
    )

    with pytest.raises(ValueError, match="empty"):
        _run(
            copy_text_module.handle_copy_selected_text(
                {"settle_seconds": 0},
                _context(tmp_path),
            )
        )


def test_copy_selected_text_has_no_file_or_network_permission(
    tmp_path: Path,
) -> None:
    result = ActionValidator.validate(
        {"type": "copy_selected_text", "params": {"keys": "^a^c"}},
        AgentConfig(
            server_url="http://127.0.0.1:8000",
            agent_id=1,
            token="token",
            allowed_doc_dirs=[],
        ),
    )

    assert result.allowed is True


def test_click_relative_uses_window_dimensions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clicks: list[tuple[int, int, str, bool]] = []
    monkeypatch.setattr(
        click_relative_module,
        "window_rect",
        lambda handle: (100, 200, 500, 600),
    )
    monkeypatch.setattr(
        click_relative_module,
        "_click_coordinates",
        lambda x, y, button, double: clicks.append(
            (x, y, button, double)
        ),
    )
    context = _context(tmp_path)
    context["last_window_handle"] = 42

    result = _run(
        click_relative_module.handle_click_relative(
            {"x": 0.25, "y": 0.5},
            context,
        )
    )

    assert result.success is True
    assert clicks == [(200, 400, "left", False)]


def test_read_window_text_collects_visible_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        read_window_text_module,
        "find_windows",
        lambda **kwargs: [SimpleNamespace(handle=88)],
    )
    monkeypatch.setattr(
        read_window_text_module,
        "_read_window_text",
        lambda handle, min_chars: "第一句\n第二句",
    )

    result = _run(
        read_window_text_module.handle_read_window_text(
            {"title": "桌面歌词", "min_chars": 2},
            _context(tmp_path),
        )
    )

    assert result.success is True
    assert result.text == "第一句\n第二句"
    assert result.window_handle == 88


def test_read_window_text_uses_integer_window_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[object] = []

    class FakeDesktop:
        def window(self, *, handle):
            captured.append(handle)
            return object()

    fake_pywinauto = SimpleNamespace(Desktop=lambda backend: FakeDesktop())
    monkeypatch.setitem(sys.modules, "pywinauto", fake_pywinauto)
    monkeypatch.setattr(
        read_window_text_module,
        "iter_elements",
        lambda window, max_elements, max_depth: [
            (
                SimpleNamespace(
                    element_info=SimpleNamespace(
                        control_type="Text",
                        name="Readable text",
                    )
                ),
                0,
            )
        ],
    )
    monkeypatch.setattr(
        read_window_text_module,
        "element_value",
        lambda element: "",
    )

    text = read_window_text_module._read_window_text(88, 1)

    assert text == "Readable text"
    assert captured == [88]


def test_read_window_text_includes_edit_value_with_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeDesktop:
        def window(self, *, handle):
            del handle
            return object()

    fake_pywinauto = SimpleNamespace(Desktop=lambda backend: FakeDesktop())
    monkeypatch.setitem(sys.modules, "pywinauto", fake_pywinauto)
    monkeypatch.setattr(
        read_window_text_module,
        "iter_elements",
        lambda window, max_elements, max_depth: [
            (
                SimpleNamespace(
                    element_info=SimpleNamespace(
                        control_type="Edit",
                        name="Characters to copy",
                    )
                ),
                0,
            )
        ],
    )
    monkeypatch.setattr(
        read_window_text_module,
        "element_value",
        lambda element: "NL-RPA",
    )

    text = read_window_text_module._read_window_text(88, 1)

    assert "Characters to copy" in text
    assert "NL-RPA" in text


def test_activate_window_uses_visible_window_candidates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, Any]] = []

    def fake_find_windows(**kwargs):
        captured.append(dict(kwargs))
        return []

    monkeypatch.setattr(
        activate_window_module,
        "find_windows",
        fake_find_windows,
    )
    monkeypatch.setattr(
        activate_window_module,
        "describe_window_candidates",
        lambda limit: [],
    )

    result = _run(
        handle_activate_window(
            {"pid": 123},
            _context(tmp_path),
        )
    )

    assert result.success is False
    assert captured
    assert captured[0]["visible_only"] is True


def test_fetch_kugou_lyrics_reads_public_lyrics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        kugou_lyrics_module,
        "_fetch_kugou_lyrics",
        lambda query, timeout: "[00:01.00]第一句\n[00:02.00]第二句",
    )

    result = _run(
        kugou_lyrics_module.handle_fetch_kugou_lyrics(
            {"query": "第一推动力"},
            _context(tmp_path),
        )
    )

    assert result.success is True
    assert result.text == "[00:01.00]第一句\n[00:02.00]第二句"
    assert result.data["query"] == "第一推动力"


@WINDOWS_ONLY
def test_launch_notepad(tmp_path: Path) -> None:
    path = _notepad_path()
    if not path.exists():
        pytest.skip("notepad.exe is not available")

    context = _context(tmp_path, 101)
    try:
        result = _run(
            handle_launch_app(
                {
                    "path": str(path),
                    "wait_for_window": False,
                },
                context,
            )
        )
        assert result.success is True
        assert result.pid is not None
        assert str(path) in context["processes"]
        assert context["processes"][str(path)]["process"].poll() is None
    finally:
        _cleanup(context)


@WINDOWS_ONLY
def test_launch_arbitrary_app(tmp_path: Path) -> None:
    path = _calc_path()
    if not path.exists():
        pytest.skip("calc.exe is not available")

    context = _context(tmp_path, 102)
    try:
        result = _run(
            handle_launch_app(
                {
                    "path": str(path),
                    "wait_for_window": False,
                },
                context,
            )
        )
        assert result.success is True
        assert result.pid is not None
        assert result.pid > 0
        assert str(path) in context["processes"]
    finally:
        _cleanup(context)


def test_launch_app_resolves_bare_executable_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    class _FakeProcess:
        pid = 4321

        def poll(self) -> None:
            return None

    def fake_popen(args, **kwargs):
        calls.append(list(args))
        return _FakeProcess()

    monkeypatch.setattr(
        launch_app_module.shutil,
        "which",
        lambda name: (
            r"C:\Tools\wechat.exe"
            if name.lower() == "wechat.exe"
            else None
        ),
    )
    monkeypatch.setattr(
        launch_app_module.subprocess,
        "Popen",
        fake_popen,
    )
    monkeypatch.setattr(
        launch_app_module,
        "enum_windows",
        lambda **_kwargs: [],
    )

    context = _context(tmp_path)
    result = _run(
        handle_launch_app(
            {"path": "wechat", "wait_for_window": False},
            context,
        )
    )

    assert result.success is True
    assert calls == [[r"C:\Tools\wechat.exe"]]
    assert r"C:\Tools\wechat.exe" in context["processes"]


def test_launch_app_reuses_existing_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing = WindowInfo(
        handle=24680,
        title="WeChat",
        class_name="Qt51514QWindowIcon",
        pid=13579,
    )
    activations: list[tuple[int, float]] = []

    monkeypatch.setattr(
        launch_app_module,
        "enum_windows",
        lambda **_kwargs: [existing],
    )
    monkeypatch.setattr(
        launch_app_module,
        "process_image_path",
        lambda _pid: r"C:\Tools\wechat.exe",
    )
    monkeypatch.setattr(
        launch_app_module,
        "activate_window",
        lambda handle, timeout: (
            activations.append((handle, timeout)) or True
        ),
    )

    def unexpected_launch(*_args, **_kwargs):
        raise AssertionError("launch_app must not start a second process")

    monkeypatch.setattr(
        launch_app_module.subprocess,
        "Popen",
        unexpected_launch,
    )

    context = _context(tmp_path)
    result = _run(
        handle_launch_app(
            {
                "path": r"C:\Tools\wechat.exe",
                "wait_for_window": False,
            },
            context,
        )
    )

    assert result.success is True
    assert result.pid == 13579
    assert result.window_handle == 24680
    assert result.data["state"] == "reused_existing"
    assert activations == [(24680, 2.0)]
    assert context["processes"] == {}


def test_launch_app_activates_new_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = WindowInfo(
        handle=24680,
        title="KuGou",
        class_name="KuGouMainWindow",
        pid=13579,
    )
    activated: list[tuple[int, float]] = []

    class _FakeProcess:
        pid = 13579

        def poll(self) -> None:
            return None

    monkeypatch.setattr(
        launch_app_module,
        "_find_existing_application_window",
        lambda _path: None,
    )
    monkeypatch.setattr(
        launch_app_module,
        "_find_application_processes",
        lambda _path: [],
    )
    monkeypatch.setattr(
        launch_app_module.subprocess,
        "Popen",
        lambda *_args, **_kwargs: _FakeProcess(),
    )
    monkeypatch.setattr(
        launch_app_module,
        "enum_windows",
        lambda **_kwargs: [],
    )
    monkeypatch.setattr(
        launch_app_module,
        "find_windows",
        lambda **_kwargs: [window],
    )
    monkeypatch.setattr(
        launch_app_module,
        "activate_window",
        lambda handle, timeout: (
            activated.append((handle, timeout)) or True
        ),
    )

    context = _context(tmp_path)
    result = _run(
        handle_launch_app(
            {
                "path": r"C:\Tools\KuGou.exe",
                "wait_for_window": True,
            },
            context,
        )
    )

    assert result.success is True
    assert result.window_handle == window.handle
    assert context["last_window_handle"] == window.handle
    assert activated == [(window.handle, 2.0)]


def test_find_existing_window_prefers_exact_executable_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = r"D:\KGMusic\KuGou.exe"
    helper = WindowInfo(
        handle=100,
        title="AsyncDNSWindow",
        class_name="AsyncDNSWinClass",
        pid=14148,
    )
    main = WindowInfo(
        handle=200,
        title="酷狗音乐",
        class_name="kugou_ui",
        pid=7204,
    )
    monkeypatch.setattr(
        launch_app_module,
        "enum_windows",
        lambda **_kwargs: [helper, main],
    )
    monkeypatch.setattr(
        launch_app_module,
        "process_image_path",
        lambda pid: (
            r"D:\KGMusic\20.1.01.27691\KuGou.exe"
            if pid == helper.pid
            else target
        ),
    )
    monkeypatch.setattr(
        launch_app_module,
        "window_rect",
        lambda handle: (
            (0, 0, 1920, 1080)
            if handle == helper.handle
            else (100, 100, 900, 700)
        ),
    )
    monkeypatch.setattr(
        launch_app_module,
        "_window_visible",
        lambda _handle: True,
        raising=False,
    )
    monkeypatch.setattr(
        launch_app_module,
        "_window_iconic",
        lambda handle: handle == main.handle,
        raising=False,
    )

    assert (
        launch_app_module._find_existing_application_window(target)
        == main
    )


@WINDOWS_ONLY
def test_activate_window_by_title(tmp_path: Path) -> None:
    _require_interactive_desktop()
    path = _notepad_path()
    if not path.exists():
        pytest.skip("notepad.exe is not available")

    context = _context(tmp_path, 103)
    try:
        launched = _run(
            handle_launch_app(
                {"path": str(path), "wait_for_window": False},
                context,
            )
        )
        window = _wait_for_window(launched.pid or 0)
        if not window:
            pytest.skip("notepad did not expose a top-level window")

        result = _run(
            handle_activate_window(
                {"title": window["title"], "index": 0},
                context,
            )
        )
        assert result.success is True
        assert result.window_handle == window["handle"]
    finally:
        _cleanup(context)


@WINDOWS_ONLY
def test_activate_window_by_pid(tmp_path: Path) -> None:
    _require_interactive_desktop()
    path = _notepad_path()
    if not path.exists():
        pytest.skip("notepad.exe is not available")

    context = _context(tmp_path, 104)
    try:
        launched = _run(
            handle_launch_app(
                {"path": str(path), "wait_for_window": False},
                context,
            )
        )
        if not _wait_for_window(launched.pid or 0):
            pytest.skip("notepad did not expose a top-level window")

        result = _run(
            handle_activate_window(
                {"pid": launched.pid, "index": 0},
                context,
            )
        )
        assert result.success is True
        assert result.pid == launched.pid
    finally:
        _cleanup(context)


@WINDOWS_ONLY
def test_click_element_by_uia(tmp_path: Path) -> None:
    _require_interactive_desktop()
    path = _notepad_path()
    if not path.exists():
        pytest.skip("notepad.exe is not available")

    context = _context(tmp_path, 105)
    try:
        launched = _run(
            handle_launch_app(
                {"path": str(path), "wait_for_window": False},
                context,
            )
        )
        if not _wait_for_window(launched.pid or 0):
            pytest.skip("notepad did not expose a top-level window")
        _run(handle_activate_window({"pid": launched.pid}, context))

        control_type = None
        for candidate in ("Document", "Edit"):
            if find_element(context, control_type=candidate) is not None:
                control_type = candidate
                break
        if control_type is None:
            pytest.skip("notepad has no accessible text element")

        result = _run(
            handle_click_element(
                {"control_type": control_type},
                context,
            )
        )
        assert result.success is True
    finally:
        _cleanup(context)


@WINDOWS_ONLY
def test_click_element_by_coordinates(tmp_path: Path) -> None:
    _require_interactive_desktop()
    path = _notepad_path()
    if not path.exists():
        pytest.skip("notepad.exe is not available")

    context = _context(tmp_path, 106)
    try:
        launched = _run(
            handle_launch_app(
                {"path": str(path), "wait_for_window": False},
                context,
            )
        )
        window = _wait_for_window(launched.pid or 0)
        if not window:
            pytest.skip("notepad did not expose a top-level window")

        import win32gui

        left, top, right, bottom = win32gui.GetWindowRect(window["handle"])
        result = _run(
            handle_click_element(
                {
                    "x": (left + right) // 2,
                    "y": (top + bottom) // 2,
                },
                context,
            )
        )
        assert result.success is True
    finally:
        _cleanup(context)


@WINDOWS_ONLY
def test_type_text_send_keys(tmp_path: Path) -> None:
    _require_interactive_desktop()
    path = _notepad_path()
    if not path.exists():
        pytest.skip("notepad.exe is not available")

    context = _context(tmp_path, 107)
    try:
        launched = _run(
            handle_launch_app(
                {"path": str(path), "wait_for_window": False},
                context,
            )
        )
        if not _wait_for_window(launched.pid or 0):
            pytest.skip("notepad did not expose a top-level window")
        _run(handle_activate_window({"pid": launched.pid}, context))

        control_type = "Document"
        if find_element(context, control_type=control_type) is None:
            control_type = "Edit"
        if find_element(context, control_type=control_type) is None:
            pytest.skip("notepad has no accessible text element")
        _run(handle_click_element({"control_type": control_type}, context))

        typed = _run(
            handle_type_text(
                {
                    "text": "hello agent",
                    "clear_first": True,
                    "method": "send_keys",
                },
                context,
            )
        )
        assert typed.success is True
        read = _run(
            handle_read_text({"control_type": control_type}, context)
        )
        assert read.success is True
        assert "hello agent" in (read.text or "")
    finally:
        _cleanup(context)


@WINDOWS_ONLY
def test_type_text_clipboard(tmp_path: Path) -> None:
    _require_interactive_desktop()
    path = _notepad_path()
    if not path.exists():
        pytest.skip("notepad.exe is not available")

    context = _context(tmp_path, 108)
    try:
        launched = _run(
            handle_launch_app(
                {"path": str(path), "wait_for_window": False},
                context,
            )
        )
        if not _wait_for_window(launched.pid or 0):
            pytest.skip("notepad did not expose a top-level window")
        _run(handle_activate_window({"pid": launched.pid}, context))

        control_type = "Document"
        if find_element(context, control_type=control_type) is None:
            control_type = "Edit"
        if find_element(context, control_type=control_type) is None:
            pytest.skip("notepad has no accessible text element")
        _run(handle_click_element({"control_type": control_type}, context))

        result = _run(
            handle_type_text(
                {
                    "text": "中文输入测试",
                    "clear_first": True,
                    "method": "clipboard",
                },
                context,
            )
        )
        assert result.success is True

        import win32clipboard
        import win32con

        win32clipboard.OpenClipboard()
        try:
            clipboard_text = win32clipboard.GetClipboardData(
                win32con.CF_UNICODETEXT
            )
        finally:
            win32clipboard.CloseClipboard()
        assert clipboard_text == "中文输入测试"
    finally:
        _cleanup(context)


@WINDOWS_ONLY
def test_press_hotkey(tmp_path: Path) -> None:
    _require_interactive_desktop()
    path = _notepad_path()
    if not path.exists():
        pytest.skip("notepad.exe is not available")

    context = _context(tmp_path, 109)
    try:
        launched = _run(
            handle_launch_app(
                {"path": str(path), "wait_for_window": False},
                context,
            )
        )
        if not _wait_for_window(launched.pid or 0):
            pytest.skip("notepad did not expose a top-level window")
        _run(handle_activate_window({"pid": launched.pid}, context))

        result = _run(handle_press_hotkey({"keys": "^a"}, context))
        assert result.success is True
    finally:
        _cleanup(context)


@WINDOWS_ONLY
def test_copy_file_to_clipboard_writes_file_list(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from desktop_agent.actions import copy_file as copy_file_module

    source = tmp_path / "screenshot.png"
    source.write_bytes(b"png")
    copied: list[Path] = []
    monkeypatch.setattr(
        copy_file_module,
        "_copy_file_to_clipboard",
        lambda path: copied.append(path),
    )
    context = _context(tmp_path)

    result = _run(
        copy_file_module.handle_copy_file_to_clipboard(
            {"path": str(source)},
            context,
        )
    )

    assert result.success is True
    assert copied == [source.resolve()]
    assert result.data["path"] == str(source.resolve())


def test_copy_file_to_clipboard_rejects_missing_file(tmp_path: Path) -> None:
    from desktop_agent.actions import copy_file as copy_file_module

    missing = tmp_path / "missing.png"

    with pytest.raises(ValueError, match="不存在"):
        _run(
            copy_file_module.handle_copy_file_to_clipboard(
                {"path": str(missing)},
                _context(tmp_path),
            )
        )


def test_copy_file_to_clipboard_uses_image_payload_for_png(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from PIL import Image

    from desktop_agent.actions import copy_file as copy_file_module

    source = tmp_path / "screenshot.png"
    Image.new("RGB", (8, 6), "red").save(source)
    image_calls: list[Path] = []
    file_calls: list[Path] = []
    monkeypatch.setattr(
        copy_file_module,
        "_copy_image_to_clipboard",
        lambda path: image_calls.append(path),
        raising=False,
    )
    monkeypatch.setattr(
        copy_file_module,
        "_copy_file_drop_to_clipboard",
        lambda path: file_calls.append(path),
        raising=False,
    )

    result = _run(
        copy_file_module.handle_copy_file_to_clipboard(
            {"path": str(source)},
            _context(tmp_path),
        )
    )

    assert result.success is True
    assert image_calls == [source.resolve()]
    assert file_calls == []


def test_image_dib_payload_omits_bitmap_file_header(tmp_path: Path) -> None:
    from PIL import Image

    from desktop_agent.actions import copy_file as copy_file_module

    source = tmp_path / "screenshot.png"
    Image.new("RGB", (3, 2), "blue").save(source)
    payload = copy_file_module._image_dib_payload(source)

    assert payload
    assert payload[:2] != b"BM"


def test_copy_file_clipboard_requires_allowed_directory(
    tmp_path: Path,
) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    source = outside / "screenshot.png"
    source.write_bytes(b"png")
    config = AgentConfig(
        server_url="http://127.0.0.1:8000",
        agent_id=1,
        token="token",
        allowed_apps=[],
        allowed_doc_dirs=[str(allowed)],
    )

    result = ActionValidator.validate(
        {
            "type": "copy_file_to_clipboard",
            "params": {"path": str(source)},
        },
        config,
    )

    assert result.allowed is False
    assert "outside allowlist" in (result.reason or "")


@WINDOWS_ONLY
def test_wait_seconds(tmp_path: Path) -> None:
    context = _context(tmp_path, 110)
    started_at = time.perf_counter()
    result = _run(handle_wait({"seconds": 0.1}, context))
    elapsed = time.perf_counter() - started_at

    assert result.success is True
    assert elapsed >= 0.08


@WINDOWS_ONLY
def test_wait_window_appears(tmp_path: Path) -> None:
    _require_interactive_desktop()
    path = _notepad_path()
    if not path.exists():
        pytest.skip("notepad.exe is not available")

    context = _context(tmp_path, 111)
    try:
        launched = _run(
            handle_launch_app(
                {"path": str(path), "wait_for_window": False},
                context,
            )
        )
        window = _wait_for_window(launched.pid or 0)
        if not window:
            pytest.skip("notepad did not expose a top-level window")

        result = _run(
            handle_wait(
                {
                    "seconds": 1,
                    "condition": "window_appears",
                    "title": window["title"],
                },
                context,
            )
        )
        assert result.success is True
    finally:
        _cleanup(context)


@WINDOWS_ONLY
def test_screenshot_fullscreen(tmp_path: Path) -> None:
    _require_interactive_desktop()
    context = _context(tmp_path, 112)
    result = _run(
        handle_screenshot(
            {"name": "fullscreen"},
            context,
        )
    )

    assert result.success is True
    assert result.screenshot_path is not None
    assert Path(result.screenshot_path).is_file()


@WINDOWS_ONLY
def test_screenshot_region(tmp_path: Path) -> None:
    _require_interactive_desktop()
    context = _context(tmp_path, 113)
    result = _run(
        handle_screenshot(
            {
                "name": "region",
                "region": {
                    "x": 0,
                    "y": 0,
                    "width": 100,
                    "height": 80,
                },
            },
            context,
        )
    )

    assert result.success is True
    assert result.screenshot_path is not None
    from PIL import Image

    with Image.open(result.screenshot_path) as image:
        assert image.size == (100, 80)


@WINDOWS_ONLY
def test_screenshot_to_explicit_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from PIL import Image

    calls: list[dict[str, Any]] = []

    def fake_grab(bbox=None, **kwargs) -> Image.Image:
        calls.append({"bbox": bbox, **kwargs})
        return Image.new("RGB", (12, 8))

    monkeypatch.setattr("PIL.ImageGrab.grab", fake_grab)
    target_dir = tmp_path / "pictures"
    target_dir.mkdir()
    target = target_dir / "2026-09-24.png"
    context = _context(tmp_path, 115)

    result = _run(
        handle_screenshot(
            {"path": str(target)},
            context,
        )
    )

    assert result.success is True
    assert result.screenshot_path == str(target)
    assert target.is_file()
    assert calls[0]["bbox"] is None
    with Image.open(target) as image:
        assert image.size == (12, 8)


@WINDOWS_ONLY
def test_screenshot_into_directory_with_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from PIL import Image

    monkeypatch.setattr(
        "PIL.ImageGrab.grab",
        lambda bbox=None, **kwargs: Image.new("RGB", (4, 4)),
    )
    target_dir = tmp_path / "pictures"
    target_dir.mkdir()
    context = _context(tmp_path, 116)

    result = _run(
        handle_screenshot(
            {"path": str(target_dir), "name": "2026-09-24"},
            context,
        )
    )

    assert Path(result.screenshot_path or "") == (
        target_dir / "2026-09-24.png"
    )
    assert (target_dir / "2026-09-24.png").is_file()


@WINDOWS_ONLY
def test_screenshot_window_uses_window_rect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from PIL import Image

    from desktop_agent.actions import screenshot_action

    class _FakeWindow:
        handle = 4242

    calls: list[tuple[tuple[int, ...] | None, dict[str, Any]]] = []

    def fake_grab(bbox=None, **kwargs) -> Image.Image:
        calls.append((bbox, kwargs))
        return Image.new("RGB", (100, 200))

    monkeypatch.setattr(
        screenshot_action,
        "find_windows",
        lambda **kwargs: [_FakeWindow()],
    )
    monkeypatch.setattr(
        screenshot_action,
        "window_rect",
        lambda handle: (10, 20, 110, 220),
    )
    monkeypatch.setattr("PIL.ImageGrab.grab", fake_grab)
    context = _context(tmp_path, 120)

    result = _run(
        handle_screenshot(
            {"name": "wechat", "window": {"title": "微信"}},
            context,
        )
    )

    assert result.success is True
    assert result.screenshot_path is not None
    assert calls[0][0] == (10, 20, 110, 220)
    assert calls[0][1].get("all_screens") is True


@WINDOWS_ONLY
def test_screenshot_last_window_handle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from PIL import Image

    from desktop_agent.actions import screenshot_action

    monkeypatch.setattr(
        screenshot_action,
        "window_rect",
        lambda handle: (0, 0, 50, 60),
    )
    monkeypatch.setattr(
        "PIL.ImageGrab.grab",
        lambda bbox=None, **kwargs: Image.new("RGB", (50, 60)),
    )
    context = _context(tmp_path, 121)
    context["last_window_handle"] = 99

    result = _run(handle_screenshot({"window": "last"}, context))

    assert result.success is True
    assert result.data["window"] == "last"


@WINDOWS_ONLY
def test_screenshot_restores_minimized_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from PIL import Image

    from desktop_agent.actions import screenshot_action

    class _FakeWindow:
        handle = 4242

    rects = iter([(0, 0, 0, 0), (10, 20, 110, 220)])
    activated: list[tuple[int, float]] = []
    monkeypatch.setattr(
        screenshot_action,
        "find_windows",
        lambda **kwargs: [_FakeWindow()],
    )
    monkeypatch.setattr(
        screenshot_action,
        "window_rect",
        lambda handle: next(rects),
    )
    monkeypatch.setattr(
        screenshot_action,
        "activate_window",
        lambda handle, timeout: (
            activated.append((handle, timeout)) or True
        ),
        raising=False,
    )
    monkeypatch.setattr(
        "PIL.ImageGrab.grab",
        lambda bbox=None, **kwargs: Image.new("RGB", (100, 200)),
    )
    context = _context(tmp_path, 122)

    result = _run(
        handle_screenshot(
            {"name": "restored", "window": {"title": "KuGou"}},
            context,
        )
    )

    assert result.success is True
    assert activated == [(4242, 2.0)]


@WINDOWS_ONLY
def test_screenshot_missing_directory_is_rejected(tmp_path: Path) -> None:
    context = _context(tmp_path, 117)
    target = tmp_path / "missing" / "shot.png"

    with pytest.raises(ValueError, match="不存在"):
        _run(handle_screenshot({"path": str(target)}, context))

    assert not target.exists()


@WINDOWS_ONLY
def test_screenshot_overwrite_false_is_rejected(tmp_path: Path) -> None:
    target_dir = tmp_path / "pictures"
    target_dir.mkdir()
    target = target_dir / "existing.png"
    target.write_bytes(b"png")
    context = _context(tmp_path, 118)

    with pytest.raises(ValueError, match="已存在"):
        _run(
            handle_screenshot(
                {"path": str(target), "overwrite": False},
                context,
            )
        )


@WINDOWS_ONLY
def test_screenshot_unknown_window_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from desktop_agent.actions import screenshot_action

    monkeypatch.setattr(
        screenshot_action,
        "find_windows",
        lambda **kwargs: [],
    )
    context = _context(tmp_path, 119)

    with pytest.raises(ValueError, match="找不到"):
        _run(
            handle_screenshot(
                {"window": {"title": "NL-RPA 不存在的窗口标题"}},
                context,
            )
        )


@WINDOWS_ONLY
def test_read_text_from_element(tmp_path: Path) -> None:
    _require_interactive_desktop()
    path = _notepad_path()
    if not path.exists():
        pytest.skip("notepad.exe is not available")

    context = _context(tmp_path, 114)
    try:
        launched = _run(
            handle_launch_app(
                {"path": str(path), "wait_for_window": False},
                context,
            )
        )
        if not _wait_for_window(launched.pid or 0):
            pytest.skip("notepad did not expose a top-level window")
        _run(handle_activate_window({"pid": launched.pid}, context))

        control_type = "Document"
        if find_element(context, control_type=control_type) is None:
            control_type = "Edit"
        if find_element(context, control_type=control_type) is None:
            pytest.skip("notepad has no accessible text element")
        _run(handle_click_element({"control_type": control_type}, context))
        _run(handle_type_text({"text": "read me"}, context))

        result = _run(
            handle_read_text({"control_type": control_type}, context)
        )
        assert result.success is True
        assert "read me" in (result.text or "")
    finally:
        _cleanup(context)


@WINDOWS_ONLY
def test_close_app_graceful(tmp_path: Path) -> None:
    _require_interactive_desktop()
    path = _notepad_path()
    if not path.exists():
        pytest.skip("notepad.exe is not available")

    context = _context(tmp_path, 115)
    try:
        launched = _run(
            handle_launch_app(
                {"path": str(path), "wait_for_window": False},
                context,
            )
        )
        if not _wait_for_window(launched.pid or 0):
            pytest.skip("notepad did not expose a top-level window")

        result = _run(
            handle_close_app(
                {
                    "target": "last",
                    "force": False,
                    "graceful_timeout": 3,
                },
                context,
            )
        )
        assert result.success is True
        process = context["processes"].get(str(path), {}).get("process")
        if process is not None:
            process.wait(timeout=2)
    finally:
        _cleanup(context)


@WINDOWS_ONLY
def test_close_app_force(tmp_path: Path) -> None:
    path = _notepad_path()
    if not path.exists():
        pytest.skip("notepad.exe is not available")

    context = _context(tmp_path, 116)
    try:
        launched = _run(
            handle_launch_app(
                {"path": str(path), "wait_for_window": False},
                context,
            )
        )
        process = context["processes"][str(path)]["process"]

        result = _run(
            handle_close_app(
                {
                    "target": "last",
                    "force": True,
                    "graceful_timeout": 0.1,
                },
                context,
            )
        )
        assert result.success is True
        assert result.pid == launched.pid
        process.wait(timeout=2)
        assert process.poll() is not None
    finally:
        _cleanup(context)


def test_click_element_reports_candidate_controls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path)
    monkeypatch.setattr(
        click_element_module,
        "find_element",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        click_element_module,
        "describe_candidates",
        lambda context, **kwargs: ["文件(Button)", "编辑(Edit)"],
    )

    result = _run(
        handle_click_element({"name": "保存"}, context)
    )

    assert result.success is False
    assert "文件(Button)" in result.message
    assert result.data.get("candidates") == ["文件(Button)", "编辑(Edit)"]
    assert result.data.get("candidate_labels") == [
        "文件(Button)",
        "编辑(Edit)",
    ]


def test_read_text_reports_candidate_controls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path)
    monkeypatch.setattr(
        read_text_module,
        "find_element",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        read_text_module,
        "describe_candidates",
        lambda context, **kwargs: ["正文(Document)"],
    )

    result = _run(handle_read_text({"name": "结果"}, context))

    assert result.success is False
    assert "正文(Document)" in result.message
    assert result.data.get("candidates") == ["正文(Document)"]
    assert result.data.get("candidate_labels") == ["正文(Document)"]


def test_click_element_failure_reports_structured_candidate_details(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path)
    candidates = [
        {
            "index": 1,
            "name": "保存",
            "automation_id": "saveBtn",
            "control_type": "Button",
            "class_name": "Button",
            "score": 124,
            "locators": [
                {
                    "strategy": "automation_id",
                    "value": "saveBtn",
                    "count": 1,
                    "unique": True,
                    "score": 100,
                }
            ],
            "best_locator": {
                "strategy": "automation_id",
                "value": "saveBtn",
                "count": 1,
                "unique": True,
                "score": 100,
            },
        },
        {
            "index": 2,
            "name": "关闭",
            "control_type": "Button",
            "score": 30,
        },
    ]
    monkeypatch.setattr(
        click_element_module,
        "find_element",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        click_element_module,
        "describe_candidates",
        lambda context, **kwargs: candidates,
    )

    result = _run(
        handle_click_element({"name": "保存"}, context)
    )

    assert result.success is False
    assert result.data["candidates"] == candidates
    assert result.data["candidate_labels"] == ["保存(Button)", "关闭(Button)"]
    assert "保存(Button)" in result.message


def test_click_element_reports_last_locator_on_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path)
    recorded = {
        "automation_id": None,
        "name": "保存",
        "control_type": "Button",
        "window_handle": 11,
    }

    def fake_find_element(context: dict[str, Any], **kwargs: Any) -> Any:
        context["last_locator"] = dict(recorded)
        return SimpleNamespace()

    monkeypatch.setattr(
        click_element_module,
        "find_element",
        fake_find_element,
    )
    monkeypatch.setattr(
        click_element_module,
        "_click_uia",
        lambda *args, **kwargs: None,
    )

    result = _run(
        handle_click_element(
            {"name": "保存", "control_type": "Button"},
            context,
        )
    )

    assert result.success is True
    assert result.data["last_locator"] == recorded


def test_wait_element_failure_reports_candidate_details(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path)
    candidates = [
        {
            "index": 1,
            "name": "保存",
            "automation_id": "saveBtn",
            "control_type": "Button",
            "score": 100,
        }
    ]
    monkeypatch.setattr(
        wait_action_module,
        "find_element",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        wait_action_module,
        "describe_candidates",
        lambda context, **kwargs: candidates,
    )

    result = _run(
        handle_wait(
            {
                "seconds": 0.05,
                "condition": "element_appears",
                "name": "保存",
            },
            context,
        )
    )

    assert result.success is False
    assert result.data["candidates"] == candidates
    assert result.data["candidate_labels"] == ["保存(Button)"]
    assert "保存(Button)" in result.message


def test_activate_window_failure_reports_window_candidates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path)
    candidates = [
        {
            "handle": 10,
            "title": "记事本",
            "class_name": "Notepad",
            "pid": 100,
        }
    ]
    monkeypatch.setattr(
        activate_window_module,
        "find_windows",
        lambda **kwargs: [],
    )
    monkeypatch.setattr(
        activate_window_module,
        "describe_window_candidates",
        lambda **kwargs: candidates,
    )

    result = _run(
        handle_activate_window({"title": "微信"}, context)
    )

    assert result.success is False
    assert result.data["candidates"] == candidates
    assert result.data["candidate_labels"] == ["记事本(Notepad)"]
    assert "记事本(Notepad)" in result.message
