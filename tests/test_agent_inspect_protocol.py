from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from desktop_agent import client as client_module  # noqa: E402
from desktop_agent.client import AgentWebSocketClient  # noqa: E402


def _client() -> AgentWebSocketClient:
    config = SimpleNamespace(
        server_url="http://127.0.0.1:8000",
        agent_id=1,
        token="token",
        heartbeat_interval=30,
    )
    return AgentWebSocketClient(config)


def test_client_answers_desktop_inspect(monkeypatch) -> None:
    client = _client()
    sent: list[dict] = []

    async def fake_send(event: dict) -> None:
        sent.append(event)

    monkeypatch.setattr(client, "send_event", fake_send)
    monkeypatch.setattr(
        client_module,
        "inspect_desktop",
        lambda payload: {
            "success": True,
            "windows": [{"handle": 10, "title": "记事本"}],
            "target": None,
            "elements": [],
            "error": None,
        },
    )

    asyncio.run(client._handle_inspect({"request_id": "req-1"}))

    assert sent == [
        {
            "type": "desktop.inspect.result",
            "request_id": "req-1",
            "success": True,
            "windows": [{"handle": 10, "title": "记事本"}],
            "target": None,
            "elements": [],
            "error": None,
        }
    ]


def test_client_reports_inspect_failure(monkeypatch) -> None:
    client = _client()
    sent: list[dict] = []

    async def fake_send(event: dict) -> None:
        sent.append(event)

    def explode(payload: dict) -> dict:
        raise RuntimeError("UIA unavailable")

    monkeypatch.setattr(client, "send_event", fake_send)
    monkeypatch.setattr(client_module, "inspect_desktop", explode)

    asyncio.run(client._handle_inspect({"request_id": "req-2"}))

    assert sent[0]["type"] == "desktop.inspect.result"
    assert sent[0]["request_id"] == "req-2"
    assert sent[0]["success"] is False
    assert sent[0]["error"] == "UIA unavailable"


def test_client_tracks_inspect_tasks(monkeypatch) -> None:
    client = _client()
    calls: list[dict] = []

    async def fake_handle(message: dict) -> None:
        calls.append(message)

    monkeypatch.setattr(client, "_handle_inspect", fake_handle)

    async def scenario() -> None:
        await client._dispatch_message(
            {"type": "desktop.inspect", "request_id": "req-3"}
        )
        tasks = list(client.inspect_tasks)
        assert tasks
        await asyncio.gather(*tasks)

    asyncio.run(scenario())

    assert calls == [{"type": "desktop.inspect", "request_id": "req-3"}]
