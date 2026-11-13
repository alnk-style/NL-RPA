from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.execution_event import ExecutionEvent
from app.security.redaction import redact_payload


def append_execution_event(
    session: AsyncSession,
    execution_id: int,
    event_type: str,
    payload: dict[str, Any] | None = None,
) -> ExecutionEvent:
    event = ExecutionEvent(
        execution_id=int(execution_id),
        type=str(event_type),
        payload=redact_payload(dict(payload or {})),
    )
    session.add(event)
    return event


async def list_execution_events(
    session: AsyncSession,
    execution_id: int,
    *,
    after_id: int | None = None,
    limit: int = 200,
) -> list[ExecutionEvent]:
    statement = select(ExecutionEvent).where(
        ExecutionEvent.execution_id == int(execution_id)
    )
    if after_id is not None:
        statement = statement.where(ExecutionEvent.id > int(after_id))
    statement = statement.order_by(
        ExecutionEvent.id
    ).limit(max(1, min(int(limit), 1000)))
    result = await session.execute(statement)
    scalars = getattr(result, "scalars", None)
    if not callable(scalars):
        return []
    return list(scalars().all())


__all__ = [
    "append_execution_event",
    "list_execution_events",
]
