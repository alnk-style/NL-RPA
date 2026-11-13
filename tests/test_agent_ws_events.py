from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from app.api.agent_ws import _process_agent_message


def test_completed_event_calls_dispatcher_completion_handler() -> None:
    async def scenario() -> tuple[Mock, Mock, Mock]:
        db = SimpleNamespace(commit=AsyncMock())
        manager = SimpleNamespace(complete_task=Mock())
        dispatcher = SimpleNamespace(
            handle_agent_event=AsyncMock(),
            _on_task_completed=Mock(),
        )
        agent = SimpleNamespace(last_seen_at=None)
        heartbeat = {"last_pong": 0.0}

        await _process_agent_message(
            db,
            manager,
            dispatcher,
            agent,
            {
                "type": "desktop.completed",
                "task_id": 7,
                "success": True,
                "output": "ok",
                "error": None,
                "screenshots": [],
                "step_logs": [],
                "duration_seconds": 0.1,
            },
            heartbeat,
        )
        return (
            dispatcher.handle_agent_event,
            dispatcher._on_task_completed,
            manager.complete_task,
        )

    handler, completion, manager_completion = asyncio.run(scenario())

    handler.assert_awaited_once()
    completion.assert_called_once()
    assert completion.call_args.args[0] == 7
    assert completion.call_args.args[1]["success"] is True
    manager_completion.assert_not_called()


def test_inspect_result_is_routed_to_dispatcher() -> None:
    async def scenario() -> tuple[Mock, Mock]:
        db = SimpleNamespace(commit=AsyncMock())
        manager = SimpleNamespace()
        dispatcher = SimpleNamespace(
            handle_agent_event=AsyncMock(),
            handle_inspect_result=Mock(),
        )
        agent = SimpleNamespace(last_seen_at=None)

        await _process_agent_message(
            db,
            manager,
            dispatcher,
            agent,
            {
                "type": "desktop.inspect.result",
                "request_id": "req-1",
                "success": True,
                "windows": [],
                "target": None,
                "elements": [],
                "error": None,
            },
            {"last_pong": 0.0},
        )
        return (
            dispatcher.handle_inspect_result,
            dispatcher.handle_agent_event,
        )

    inspect_handler, event_handler = asyncio.run(scenario())

    inspect_handler.assert_called_once()
    event_handler.assert_not_awaited()
