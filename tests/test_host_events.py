from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.api.agent_ws import _publish_host_event
from app.services.host_events import HostEventBus


def test_host_event_bus_scopes_subscribers_and_keeps_history() -> None:
    async def scenario():
        bus = HostEventBus()
        queue, history = await bus.subscribe(1)
        assert history == []
        await bus.publish(
            1,
            "host_connected",
            {"host_id": 7, "environment": "vm"},
        )
        await bus.publish(
            2,
            "host_connected",
            {"host_id": 8},
        )
        event = await asyncio.wait_for(queue.get(), timeout=1)
        await bus.unsubscribe(1, queue)
        return event

    event = asyncio.run(scenario())
    assert event["type"] == "host_connected"
    assert event["payload"]["host_id"] == 7


def test_agent_host_event_uses_current_database_state() -> None:
    async def scenario():
        bus = HostEventBus()
        websocket = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(host_event_bus=bus)
            )
        )
        agent = SimpleNamespace(
            id=3,
            user_id=4,
            name="vm-agent",
            environment="vm",
            capabilities=["isolated_desktop"],
            status="online",
        )
        queue, _ = await bus.subscribe(4)
        await _publish_host_event(
            websocket,
            agent,
            "host_capability_changed",
            capabilities_changed=True,
        )
        return await asyncio.wait_for(queue.get(), timeout=1)

    event = asyncio.run(scenario())
    assert event["type"] == "host_capability_changed"
    assert event["payload"]["host_id"] == 3
    assert event["payload"]["capabilities_changed"] is True
