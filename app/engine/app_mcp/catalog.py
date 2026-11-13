"""In-memory application and tool catalog.

This is not a second MCP registry: it stores loaded runtimes and manifest
metadata only. Authorization remains in the existing MCPRegistry.
"""

from __future__ import annotations

from typing import Any

from app.engine.app_mcp.manifest import ToolSpec
from app.engine.app_mcp.runtime import AppRuntime


class AppCatalog:
    def __init__(self) -> None:
        self._apps: dict[str, AppRuntime] = {}

    def register(self, runtime: AppRuntime) -> None:
        app_id = runtime.manifest.id
        if app_id in self._apps:
            raise ValueError(f"Application already registered: {app_id}")
        self._apps[app_id] = runtime

    def get(self, app_id: str) -> AppRuntime | None:
        return self._apps.get(str(app_id or "").strip())

    def list(self) -> list[AppRuntime]:
        return [self._apps[key] for key in sorted(self._apps)]

    def tools(
        self,
        app_id: str | None = None,
    ) -> list[dict[str, Any]]:
        runtimes = (
            [self._apps[app_id]]
            if app_id and app_id in self._apps
            else self.list()
            if app_id is None
            else []
        )
        payload: list[dict[str, Any]] = []
        for runtime in runtimes:
            for tool in runtime.manifest.tools:
                payload.append(
                    {
                        "app_id": runtime.manifest.id,
                        "app_name": runtime.manifest.name,
                        **tool.model_dump(mode="json"),
                    }
                )
        return payload

    def tool(self, app_id: str, tool_name: str) -> ToolSpec | None:
        runtime = self.get(app_id)
        if runtime is None:
            return None
        return runtime.manifest.tool_map.get(str(tool_name))

    async def close(self) -> None:
        for runtime in list(self._apps.values()):
            await runtime.close()
        self._apps.clear()


__all__ = ["AppCatalog"]
