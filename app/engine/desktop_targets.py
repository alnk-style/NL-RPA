"""为桌面规划收集可用的 Agent、能力与白名单。"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from app.engine.desktop_context import DesktopExecutionContext
from app.database import AsyncSessionLocal
from app.models.agent import DesktopAgent

logger = logging.getLogger(__name__)

EMPTY_DESKTOP_CONTEXT = DesktopExecutionContext()


def _as_int(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def online_agent_ids(request: Any) -> list[int]:
    state = getattr(getattr(request, "app", None), "state", None)
    manager = getattr(state, "connection_manager", None)
    getter = getattr(manager, "get_online_agents", None)
    if not callable(getter):
        return []

    try:
        raw_ids = list(getter())
    except Exception:
        logger.debug("Could not read online agents", exc_info=True)
        return []

    agent_ids: list[int] = []
    for value in raw_ids:
        agent_id = _as_int(value)
        if agent_id is not None:
            agent_ids.append(agent_id)
    return agent_ids


async def load_desktop_execution_context(
    request: Any,
) -> DesktopExecutionContext:
    agent_ids = online_agent_ids(request)

    try:
        async with AsyncSessionLocal() as db:
            statement = select(DesktopAgent).where(
                DesktopAgent.platform == "windows"
            )
            if agent_ids:
                statement = statement.where(
                    DesktopAgent.id.in_(agent_ids)
                )
            result = await db.execute(statement)
            agents = list(result.scalars().all())
    except Exception:
        logger.warning(
            "Could not load desktop agents for planning",
            exc_info=True,
        )
        return EMPTY_DESKTOP_CONTEXT

    allowed_apps: list[str] = []
    allowed_doc_dirs: list[str] = []
    capabilities: set[str] = set()
    for agent in agents:
        for app in agent.allowed_apps or []:
            text = str(app).strip()
            if text and text not in allowed_apps:
                allowed_apps.append(text)
        for directory in agent.allowed_doc_dirs or []:
            text = str(directory).strip()
            if text and text not in allowed_doc_dirs:
                allowed_doc_dirs.append(text)
        for capability in agent.capabilities or []:
            text = str(capability).strip()
            if text:
                capabilities.add(text)

    if agent_ids:
        online = set(agent_ids)
        context_agent_ids = tuple(
            int(agent.id) for agent in agents if agent.id in online
        )
    else:
        context_agent_ids = tuple(int(agent.id) for agent in agents)

    return DesktopExecutionContext(
        allowed_apps=tuple(allowed_apps),
        allowed_doc_dirs=tuple(allowed_doc_dirs),
        capabilities=frozenset(capabilities),
        online_agent_count=len(agent_ids),
        paired_agent_count=len(agents),
        agent_ids=context_agent_ids,
    )


__all__ = [
    "EMPTY_DESKTOP_CONTEXT",
    "load_desktop_execution_context",
    "online_agent_ids",
]
