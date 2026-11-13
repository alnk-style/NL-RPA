from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DesktopExecutionContext:
    allowed_apps: tuple[str, ...] = ()
    allowed_doc_dirs: tuple[str, ...] = ()
    capabilities: frozenset[str] = frozenset()
    online_agent_count: int = 0
    paired_agent_count: int = 0
    agent_ids: tuple[int, ...] = ()
    app_profiles: tuple[dict[str, Any], ...] = ()

    @property
    def has_agents(self) -> bool:
        return self.paired_agent_count > 0

    @property
    def has_allowed_apps(self) -> bool:
        return bool(self.allowed_apps)


__all__ = ["DesktopExecutionContext"]
