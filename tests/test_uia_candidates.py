from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from desktop_agent.actions import _uia  # noqa: E402


class _FakeElement:
    def __init__(
        self,
        name: str = "",
        automation_id: str = "",
        control_type: str = "",
        class_name: str = "",
        children: list[_FakeElement] | None = None,
        handle: int | None = None,
        **extra: Any,
    ) -> None:
        self.element_info = SimpleNamespace(
            name=name,
            automation_id=automation_id,
            control_type=control_type,
            class_name=class_name,
        )
        self._children = list(children or [])
        self.handle = handle
        self._extra = extra
        self._parent_element: _FakeElement | None = None
        for child in self._children:
            child._parent_element = self

    def children(self) -> list[_FakeElement]:
        return list(self._children)

    def descendants(self) -> list[_FakeElement]:
        found: list[_FakeElement] = []
        queue = list(self._children)
        while queue:
            element = queue.pop(0)
            found.append(element)
            queue.extend(element.children())
        return found

    def parent(self) -> _FakeElement | None:
        return self._parent_element

    def __getattr__(self, name: str) -> Any:
        if name in self._extra:
            return self._extra[name]
        raise AttributeError(name)


def test_describe_candidates_ranks_match_and_scores_locators(
    monkeypatch,
) -> None:
    root = _FakeElement(
        name="记事本",
        control_type="Window",
        children=[
            _FakeElement(
                name="保存",
                automation_id="saveBtn",
                control_type="Button",
                class_name="Button",
            ),
            _FakeElement(name="保存", control_type="Button"),
            _FakeElement(name="关闭", control_type="Button"),
        ],
    )
    monkeypatch.setattr(
        _uia,
        "_uia_windows",
        lambda context: [root],
    )

    candidates = _uia.describe_candidates(
        {"last_window_handle": 10},
        name="保存",
        control_type="Button",
    )

    assert [candidate["name"] for candidate in candidates] == [
        "保存",
        "保存",
        "关闭",
    ]
    first = candidates[0]
    assert first["automation_id"] == "saveBtn"
    assert first["class_name"] == "Button"
    assert first["index"] > 0
    assert first["best_locator"]["strategy"] == "automation_id"
    assert first["best_locator"]["score"] == 100
    assert first["score"] >= candidates[1]["score"]
    ambiguous = candidates[1]["best_locator"]
    assert ambiguous["strategy"] == "name"
    assert ambiguous["count"] == 2
    assert ambiguous["score"] == 47


def test_find_element_records_last_locator(monkeypatch) -> None:
    target = _FakeElement(
        name="保存",
        automation_id="saveBtn",
        control_type="Button",
    )
    window = _FakeElement(
        name="记事本",
        control_type="Window",
        handle=42,
        children=[target],
    )
    desktop = SimpleNamespace(
        windows=lambda: [window],
        window=lambda handle: window,
    )
    monkeypatch.setattr(_uia, "_desktop", lambda: desktop)

    context = {"last_window_handle": 42}
    found = _uia.find_element(context, automation_id="saveBtn")

    assert found is target
    assert context["last_locator"] == {
        "automation_id": "saveBtn",
        "name": None,
        "control_type": None,
        "window_handle": 42,
    }


def test_find_element_uses_same_breadth_first_scan_as_observation(
    monkeypatch,
) -> None:
    target = _FakeElement(
        name="搜索",
        control_type="Edit",
    )
    window = _FakeElement(
        name="酷狗音乐",
        control_type="Window",
        handle=42,
        children=[target],
    )
    window.descendants = lambda: (_ for _ in ()).throw(
        RuntimeError("dynamic descendants unavailable")
    )
    desktop = SimpleNamespace(
        windows=lambda: [window],
        window=lambda handle: window,
    )
    monkeypatch.setattr(_uia, "_desktop", lambda: desktop)

    found = _uia.find_element(
        {"last_window_handle": 42},
        name="搜索",
        control_type="Edit",
    )

    assert found is target
