from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class HostResponse(BaseModel):
    host_id: int
    name: str
    environment: Literal["local", "vm", "unknown"]
    capabilities: list[str] = Field(default_factory=list)
    status: Literal["online", "offline", "busy"]
    online: bool
    last_seen_at: datetime | None = None
    agent_version: str
    platform: str
    active_task_count: int = 0
    session_ready: bool = False
    human_access_info: dict[str, Any] = Field(default_factory=dict)
    connection_security: str = "unknown"


class HostListResponse(BaseModel):
    hosts: list[HostResponse] = Field(default_factory=list)

