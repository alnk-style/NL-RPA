from __future__ import annotations

import hashlib
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.host_capabilities import (
    environment_or_unknown,
    normalize_capabilities,
)
from app.engine.host_router import HostInfo
from app.models.agent import DesktopAgent
from app.models.desktop_task import DesktopTask
from app.visual.redaction import redact_visible_text

ACTIVE_TASK_STATUSES = {"pending", "dispatched", "running"}


async def active_task_counts(
    session: AsyncSession,
    agent_ids: list[int],
) -> dict[int, int]:
    if not agent_ids:
        return {}
    rows = (
        await session.execute(
            select(
                DesktopTask.agent_id,
                func.count(DesktopTask.id),
            )
            .where(
                DesktopTask.agent_id.in_(agent_ids),
                DesktopTask.status.in_(ACTIVE_TASK_STATUSES),
            )
            .group_by(DesktopTask.agent_id)
        )
    ).all()
    return {
        int(agent_id): int(count)
        for agent_id, count in rows
        if agent_id is not None
    }


def host_info_from_agent(
    agent: DesktopAgent,
    *,
    online: bool,
    active_task_count: int,
    session_fingerprint: str | None = None,
) -> HostInfo:
    return HostInfo(
        host_id=int(agent.id),
        name=agent.name,
        environment=environment_or_unknown(agent.environment),
        capabilities=normalize_capabilities(agent.capabilities),
        status="online" if online else "offline",
        active_task_count=max(int(active_task_count), 0),
        session_fingerprint=session_fingerprint,
    )


def host_payload(
    agent: DesktopAgent,
    *,
    online: bool,
    active_task_count: int,
    session_id: str | None = None,
) -> dict[str, Any]:
    environment = environment_or_unknown(agent.environment)
    busy = online and int(active_task_count) > 0
    return {
        "host_id": int(agent.id),
        "name": agent.name,
        "environment": environment,
        "capabilities": normalize_capabilities(agent.capabilities),
        "status": (
            "busy"
            if busy
            else "online"
            if online
            else "offline"
        ),
        "online": bool(online),
        "last_seen_at": agent.last_seen_at,
        "agent_version": agent.version,
        "platform": agent.platform,
        "active_task_count": max(int(active_task_count), 0),
        "session_ready": bool(session_id),
        "human_access_info": {
            "url": safe_human_access_url(agent.human_access_url),
            "instructions": (
                redact_visible_text(
                    agent.human_access_instructions
                )
                if agent.human_access_instructions
                else None
            ),
        },
        "connection_security": agent.connection_security or "unknown",
    }


def session_fingerprint(session_id: str | None) -> str | None:
    if not session_id:
        return None
    return hashlib.sha256(
        str(session_id).encode("utf-8")
    ).hexdigest()[:32]


def safe_human_access_url(value: str | None) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    parsed = urlsplit(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, "", "")
    )


__all__ = [
    "ACTIVE_TASK_STATUSES",
    "active_task_counts",
    "host_info_from_agent",
    "host_payload",
    "safe_human_access_url",
    "session_fingerprint",
]

