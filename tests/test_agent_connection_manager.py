from __future__ import annotations

from app.engine.agent_connection_manager import AgentConnectionManager


class FakeWebSocket:
    def __init__(self) -> None:
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


def test_register_replaces_previous_connection() -> None:
    manager = AgentConnectionManager()
    first = FakeWebSocket()
    second = FakeWebSocket()

    manager.register(1, first)
    manager.register(1, second)

    assert manager.get(1) is second
    assert manager.get_online_agents() == [1]
    assert first.close_calls == 1


def test_stale_connection_unregister_keeps_current_connection() -> None:
    manager = AgentConnectionManager()
    first = FakeWebSocket()
    second = FakeWebSocket()

    manager.register(1, first)
    manager.register(1, second)
    manager.unregister(1, first)

    assert manager.get(1) is second

    manager.unregister(1, second)

    assert manager.get(1) is None
    assert manager.get_online_agents() == []


def test_unregister_without_websocket_keeps_legacy_behavior() -> None:
    manager = AgentConnectionManager()
    websocket = FakeWebSocket()
    manager.register(1, websocket)

    manager.unregister(1)

    assert manager.get(1) is None
