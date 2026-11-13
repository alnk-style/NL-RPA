from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from desktop_agent import inspector  # noqa: E402
from desktop_agent.actions._win32 import WindowInfo  # noqa: E402


class _FakeElement:
    def __init__(
        self,
        name: str = "",
        automation_id: str = "",
        control_type: str = "",
        class_name: str = "",
        children: list["_FakeElement"] | None = None,
        *,
        value: str | None = None,
        visible: bool | None = None,
        enabled: bool | None = None,
        rectangle: tuple[int, int, int, int] | None = None,
        patterns: list[str] | None = None,
    ) -> None:
        self.element_info = SimpleNamespace(
            name=name,
            automation_id=automation_id,
            control_type=control_type,
            class_name=class_name,
        )
        self._children = list(children or [])
        self._value = value
        self._visible = visible
        self._enabled = enabled
        self._rectangle = rectangle
        self._patterns = list(patterns or [])
        self._parent_element: _FakeElement | None = None
        for child in self._children:
            child._parent_element = self

    def children(self) -> list["_FakeElement"]:
        return list(self._children)

    def parent(self) -> "_FakeElement | None":
        return self._parent_element

    def get_value(self) -> str:
        if self._value is None:
            raise RuntimeError("value not supported")
        return self._value

    def is_visible(self) -> bool:
        if self._visible is None:
            raise RuntimeError("visibility not supported")
        return self._visible

    def is_enabled(self) -> bool:
        if self._enabled is None:
            raise RuntimeError("enabled state not supported")
        return self._enabled

    def rectangle(self) -> SimpleNamespace:
        if self._rectangle is None:
            raise RuntimeError("rectangle not supported")
        left, top, right, bottom = self._rectangle
        return SimpleNamespace(
            left=left,
            top=top,
            right=right,
            bottom=bottom,
        )

    def get_supported_patterns(self) -> list[str]:
        if not self._patterns:
            raise RuntimeError("patterns not supported")
        return list(self._patterns)


def test_describe_windows_enriches_process_name(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        inspector,
        "enum_windows",
        lambda visible_only=True: [
            WindowInfo(
                handle=10,
                title="  记事本  ",
                class_name="Notepad",
                pid=100,
            ),
            WindowInfo(
                handle=11,
                title="   ",
                class_name="Hidden",
                pid=101,
            ),
        ],
    )
    monkeypatch.setattr(
        inspector,
        "process_image_path",
        lambda pid: (
            r"C:\Windows\System32\notepad.exe" if pid == 100 else None
        ),
    )

    windows = inspector.describe_windows()

    assert windows == [
        {
            "handle": 10,
            "title": "记事本",
            "class_name": "Notepad",
            "pid": 100,
            "process_name": "notepad.exe",
        }
    ]


def test_collect_elements_respects_depth_and_limit() -> None:
    deep = _FakeElement(name="深层控件", control_type="Button")
    middle = _FakeElement(
        name="中层控件",
        control_type="Pane",
        children=[deep],
    )
    root = _FakeElement(
        name="窗口",
        automation_id="root",
        control_type="Window",
        children=[
            middle,
            _FakeElement(name="菜单", control_type="MenuBar"),
            _FakeElement(name="", automation_id="", control_type="Pane"),
        ],
    )

    elements = inspector.collect_elements(
        root,
        max_elements=3,
        max_depth=1,
    )

    assert [element["name"] for element in elements] == [
        "窗口",
        "中层控件",
        "菜单",
    ]
    assert elements[0]["control_type"] == "Window"
    assert elements[1]["depth"] == 1


def test_collect_elements_reports_rich_fields_and_locator_scores() -> None:
    save = _FakeElement(
        name="保存",
        automation_id="saveBtn",
        control_type="Button",
        class_name="Button",
        value="保存",
        visible=True,
        enabled=True,
        rectangle=(10, 20, 80, 50),
        patterns=["Invoke"],
    )
    duplicate = _FakeElement(
        name="保存",
        control_type="Button",
        class_name="Button",
    )
    cancel = _FakeElement(name="关闭", control_type="Button")
    editor = _FakeElement(
        name="文本编辑器",
        control_type="Document",
        class_name="Edit",
        value="正文",
    )
    root = _FakeElement(
        name="记事本",
        automation_id="root",
        control_type="Window",
        children=[save, duplicate, cancel, editor],
    )

    elements = inspector.collect_elements(
        root,
        max_elements=10,
        max_depth=2,
    )

    assert [element["index"] for element in elements] == [0, 1, 2, 3, 4]
    first = elements[1]
    assert first["class_name"] == "Button"
    assert first["value"] == "保存"
    assert first["visible"] is True
    assert first["enabled"] is True
    assert first["rectangle"] == {
        "left": 10,
        "top": 20,
        "right": 80,
        "bottom": 50,
    }
    assert first["parent_path"] == [
        {"name": "记事本", "control_type": "Window"}
    ]
    assert first["patterns"] == ["Invoke"]
    assert first["best_locator"] == {
        "strategy": "automation_id",
        "value": "saveBtn",
        "name": None,
        "control_type": None,
        "count": 1,
        "unique": True,
        "score": 100,
    }
    name_locator = first["locators"][1]
    assert name_locator == {
        "strategy": "name",
        "value": "保存",
        "name": "保存",
        "control_type": "Button",
        "count": 2,
        "unique": False,
        "score": 47,
    }
    assert elements[2]["best_locator"]["strategy"] == "name"
    assert elements[2]["best_locator"]["score"] == 47


def test_inspect_desktop_returns_elements_for_target_window(
    monkeypatch,
) -> None:
    window = {
        "handle": 10,
        "title": "记事本",
        "class_name": "Notepad",
        "pid": 100,
        "process_name": "notepad.exe",
    }
    monkeypatch.setattr(
        inspector,
        "describe_windows",
        lambda limit=40: [window],
    )
    monkeypatch.setattr(
        inspector,
        "_uia_window",
        lambda handle: _FakeElement(
            name="记事本",
            control_type="Window",
            children=[
                _FakeElement(
                    name="文本编辑器",
                    automation_id="15",
                    control_type="Document",
                )
            ],
        ),
    )

    result = inspector.inspect_desktop(
        {"window": {"handle": 10}, "max_elements": 10, "max_depth": 2}
    )

    assert result["success"] is True
    assert result["windows"] == [window]
    assert result["target"] == window
    assert [element["name"] for element in result["elements"]] == [
        "记事本",
        "文本编辑器",
    ]
    assert result["elements"][1]["automation_id"] == "15"
    assert result["elements"][1]["best_locator"] == {
        "strategy": "automation_id",
        "value": "15",
        "name": None,
        "control_type": None,
        "count": 1,
        "unique": True,
        "score": 100,
    }
    assert result["elements"][1]["locators"]


def test_inspect_desktop_skips_unknown_window(monkeypatch) -> None:
    monkeypatch.setattr(
        inspector,
        "describe_windows",
        lambda limit=40: [],
    )

    result = inspector.inspect_desktop(
        {"window": {"title": "不存在的窗口"}}
    )

    assert result["success"] is True
    assert result["target"] is None
    assert result["elements"] == []
