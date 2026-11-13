from __future__ import annotations

import asyncio
import inspect
from typing import Any
from uuid import uuid4

from fastapi import WebSocket


class AgentConnectionManager:
    """Track live Agent WebSocket connections and task completion events."""

    def __init__(self) -> None:
        self._connections: dict[int, WebSocket] = {}
        self._sessions: dict[int, str] = {}
        self._task_events: dict[int, asyncio.Event] = {}
        self._task_results: dict[int, dict[str, Any]] = {}

    def register(self, agent_id: int, websocket: WebSocket) -> None:
        previous = self._connections.get(agent_id)
        self._connections[agent_id] = websocket
        self._sessions[agent_id] = uuid4().hex
        if previous is not None and previous is not websocket:
            self._schedule_close(previous)

    def unregister(
        self,
        agent_id: int,
        websocket: WebSocket | None = None,
    ) -> None:
        if (
            websocket is not None
            and self._connections.get(agent_id) is not websocket
        ):
            return
        self._connections.pop(agent_id, None)
        self._sessions.pop(agent_id, None)

    def connect(self, agent_id: int, websocket: WebSocket) -> None:
        """Compatibility alias for callers using the old hub naming."""

        self.register(agent_id, websocket)

    def disconnect(
        self,
        agent_id: int,
        websocket: WebSocket | None = None,
    ) -> None:
        """Compatibility alias for callers using the old hub naming."""

        if websocket is None:
            websocket = self._connections.pop(agent_id, None)
            self._sessions.pop(agent_id, None)
        elif self._connections.get(agent_id) is websocket:
            self._connections.pop(agent_id, None)
            self._sessions.pop(agent_id, None)
        else:
            return
        self._schedule_close(websocket)

    @staticmethod
    def _schedule_close(websocket: Any | None) -> None:
        close = getattr(websocket, "close", None)
        if not callable(close):
            return
        result = close()
        if not inspect.isawaitable(result):
            return
        try:
            asyncio.create_task(result)
        except RuntimeError:
            close_method = getattr(result, "close", None)
            if callable(close_method):
                close_method()

    def get(self, agent_id: int) -> WebSocket | None:
        return self._connections.get(agent_id)

    def is_online(self, agent_id: int) -> bool:
        return agent_id in self._connections

    async def send_task(
        self,
        agent_id: int,
        task_payload: dict[str, Any],
    ) -> None:
        websocket = self.get(agent_id)
        if websocket is None:
            raise RuntimeError(f"Agent {agent_id} is offline")
        await websocket.send_json(task_payload)

    def get_online_agents(self) -> list[int]:
        return list(self._connections)

    def get_session_id(self, agent_id: int) -> str | None:
        return self._sessions.get(int(agent_id))

    # These task-event helpers are used by the dispatcher and the WebSocket
    # route to wake a caller waiting for a desktop task result.
    def register_task(self, task_id: int) -> asyncio.Event:
        event = asyncio.Event()
        self._task_events[task_id] = event
        return event

    def complete_task(
        self,
        task_id: int,
        result: dict[str, Any],
    ) -> None:
        self._task_results[task_id] = dict(result)
        event = self._task_events.get(task_id)
        if event is not None:
            event.set()

    def get_result(self, task_id: int) -> dict[str, Any] | None:
        return self._task_results.pop(task_id, None)

    def clear_task(self, task_id: int) -> None:
        self._task_events.pop(task_id, None)
        self._task_results.pop(task_id, None)


__all__ = ["AgentConnectionManager"]
