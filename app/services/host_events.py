from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime
from typing import Any


class HostEventBus:
    def __init__(self, *, history_limit: int = 100) -> None:
        self._subscribers: dict[int, set[asyncio.Queue[dict[str, Any]]]] = {}
        self._history: dict[int, deque[dict[str, Any]]] = {}
        self._history_limit = max(int(history_limit), 1)
        self._lock = asyncio.Lock()

    async def publish(
        self,
        user_id: int,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        event = {
            "type": event_type,
            "payload": dict(payload),
            "created_at": datetime.utcnow().isoformat(),
        }
        async with self._lock:
            history = self._history.setdefault(
                int(user_id),
                deque(maxlen=self._history_limit),
            )
            history.append(event)
            subscribers = list(
                self._subscribers.get(int(user_id), set())
            )
        for queue in subscribers:
            try:
                queue.put_nowait(dict(event))
            except asyncio.QueueFull:
                continue

    async def subscribe(
        self,
        user_id: int,
    ) -> tuple[asyncio.Queue[dict[str, Any]], list[dict[str, Any]]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(
            maxsize=100
        )
        async with self._lock:
            self._subscribers.setdefault(int(user_id), set()).add(queue)
            history = list(self._history.get(int(user_id), []))
        return queue, history

    async def unsubscribe(
        self,
        user_id: int,
        queue: asyncio.Queue[dict[str, Any]],
    ) -> None:
        async with self._lock:
            subscribers = self._subscribers.get(int(user_id))
            if subscribers is None:
                return
            subscribers.discard(queue)
            if not subscribers:
                self._subscribers.pop(int(user_id), None)


__all__ = ["HostEventBus"]
