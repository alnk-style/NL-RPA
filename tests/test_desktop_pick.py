from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.engine.desktop_dispatcher import DesktopDispatcher  # noqa: E402
from desktop_agent import client as client_module  # noqa: E402
from desktop_agent import pick as pick_module  # noqa: E402
from desktop_agent.client import AgentWebSocketClient  # noqa: E402


class _FakeInfo:
    def __init__(
        self,
        *,
        name: str = "",
        automation_id: str = "",
        control_type: str = "",
        class_name: str = "",
        handle: int | None = None,
        process_id: int | None = None,
        parent: _FakeInfo | None = None,
    ) -> None:
        self.name = name
        self.automation_id = automation_id
        self.control_type = control_type
        self.class_name = class_name
        self.handle = handle
        self.process_id = process_id
        self.parent = parent


class _FakeElement:
    def __init__(
        self,
        info: _FakeInfo,
        *,
        parent: _FakeElement | None = None,
        value: str | None = None,
        visible: bool | None = None,
        enabled: bool | None = None,
        rectangle: tuple[int, int, int, int] | None = None,
        patterns: list[str] | None = None,
    ) -> None:
        self.element_info = info
        self._parent = parent
        self._value = value
        self._visible = visible
        self._enabled = enabled
        self._rectangle = rectangle
        self._patterns = list(patterns or [])

    def parent(self) -> _FakeElement | None:
        return self._parent

    def children(self) -> list[Any]:
        return []

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


def _pick_tree() -> _FakeElement:
    window_info = _FakeInfo(
        name="记事本",
        control_type="Window",
        class_name="Notepad",
        handle=10,
        process_id=105,
    )
    window = _FakeElement(window_info)
    button_info = _FakeInfo(
        name="保存",
        automation_id="saveBtn",
        control_type="Button",
        class_name="Button",
        process_id=105,
        parent=window_info,
    )
    return _FakeElement(
        button_info,
        parent=window,
        visible=True,
        enabled=True,
        rectangle=(10, 20, 90, 50),
        patterns=["Invoke"],
        value="保存",
    )


def test_pick_element_returns_locator_and_window(monkeypatch) -> None:
    monkeypatch.setattr(
        pick_module,
        "_element_from_point",
        lambda x, y: _pick_tree(),
    )

    result = pick_module.pick_element({"x": 40, "y": 30})

    assert result["success"] is True
    assert result["element"]["automation_id"] == "saveBtn"
    assert result["element"]["control_type"] == "Button"
    assert result["element"]["center"] == {"x": 50, "y": 35}
    assert result["window"] == {
        "handle": 10,
        "title": "记事本",
        "class_name": "Notepad",
        "pid": 105,
    }
    assert result["point"] == {"x": 40, "y": 30}
    assert result["error"] is None


def test_pick_element_rejects_unnamed_element(monkeypatch) -> None:
    info = _FakeInfo(control_type="Pane")
    element = _FakeElement(info)
    monkeypatch.setattr(
        pick_module,
        "_element_from_point",
        lambda x, y: element,
    )

    result = pick_module.pick_element({"x": 1, "y": 2})

    assert result["success"] is False
    assert result["element"] is None
    assert "AutomationId" in result["error"]


def test_pick_element_reports_cursor_failure(monkeypatch) -> None:
    def explode(_x: int, _y: int):
        raise RuntimeError("UIA unavailable")

    monkeypatch.setattr(pick_module, "_element_from_point", explode)

    result = pick_module.pick_element({"x": 1, "y": 2})

    assert result["success"] is False
    assert result["error"] == "UIA unavailable"


def _client() -> AgentWebSocketClient:
    config = SimpleNamespace(
        server_url="http://127.0.0.1:8000",
        agent_id=1,
        token="token",
        heartbeat_interval=30,
    )
    return AgentWebSocketClient(config)


def test_client_answers_desktop_pick(monkeypatch) -> None:
    client = _client()
    sent: list[dict] = []

    async def fake_send(event: dict) -> None:
        sent.append(event)

    monkeypatch.setattr(client, "send_event", fake_send)
    monkeypatch.setattr(
        client_module,
        "pick_element",
        lambda payload: {
            "success": True,
            "element": {"name": "保存", "automation_id": "saveBtn"},
            "window": {"title": "记事本"},
            "point": {"x": 1, "y": 2},
            "error": None,
        },
    )

    asyncio.run(client._handle_pick({"request_id": "pick-1"}))

    assert sent == [
        {
            "type": "desktop.pick.result",
            "request_id": "pick-1",
            "success": True,
            "element": {"name": "保存", "automation_id": "saveBtn"},
            "window": {"title": "记事本"},
            "point": {"x": 1, "y": 2},
            "error": None,
        }
    ]


class _FakeConnectionManager:
    def __init__(self, online_ids: list[int]) -> None:
        self.online_ids = list(online_ids)
        self.sent: list[tuple[int, dict[str, Any]]] = []

    def get_online_agents(self) -> list[int]:
        return list(self.online_ids)

    async def send_task(
        self,
        agent_id: int,
        payload: dict[str, Any],
    ) -> None:
        self.sent.append((agent_id, payload))


def test_dispatcher_pick_round_trip() -> None:
    async def scenario() -> tuple[dict[str, Any], dict[str, Any]]:
        manager = _FakeConnectionManager([1])
        dispatcher = DesktopDispatcher(manager)
        original_send = manager.send_task

        async def send_task(
            agent_id: int,
            payload: dict[str, Any],
        ) -> None:
            await original_send(agent_id, payload)
            dispatcher.handle_pick_result(
                {
                    "type": "desktop.pick.result",
                    "request_id": payload["request_id"],
                    "success": True,
                    "element": {"name": "保存", "automation_id": "saveBtn"},
                    "window": {"title": "记事本", "pid": 100},
                    "point": {"x": 5, "y": 6},
                    "error": None,
                }
            )

        manager.send_task = send_task  # type: ignore[method-assign]
        result = await dispatcher.pick({"x": 5, "y": 6}, agent_id=1, timeout=2)
        return result, manager.sent[0][1]

    result, sent = asyncio.run(scenario())

    assert sent["type"] == "desktop.pick"
    assert sent["request_id"]
    assert sent["x"] == 5
    assert result["success"] is True
    assert result["element"]["automation_id"] == "saveBtn"
    assert result["window"]["title"] == "记事本"


def test_dispatcher_pick_without_agent_reports_error() -> None:
    async def scenario() -> dict[str, Any]:
        dispatcher = DesktopDispatcher(_FakeConnectionManager([]))
        return await dispatcher.pick({}, timeout=0.1)

    result = asyncio.run(scenario())

    assert result["success"] is False
    assert "agent" in str(result["error"]).lower()
