"""Desktop backend adapter over the existing Connector interface."""

from __future__ import annotations

from typing import Any

from app.engine.app_mcp.contracts import (
    AppProbe,
    AppToolError,
    ToolCallContext,
)


class DesktopBackend:
    """Thin wrapper around a DesktopBridge-compatible connector."""

    def __init__(self, connector: Any) -> None:
        self.connector = connector

    async def probe(self) -> AppProbe:
        auth = await self.connector.auth_status()
        if not isinstance(auth, dict):
            return AppProbe(
                installed=False,
                running=False,
            )
        data = auth.get("data")
        payload = data if isinstance(data, dict) else {}
        connected = bool(auth.get("success")) and bool(payload.get("connected"))
        return AppProbe(
            installed=connected,
            running=connected,
            requires_login=False,
            version=None,
        )

    async def observe(
        self,
        request: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        result = await self.connector.observe(dict(request or {}))
        return (
            result
            if isinstance(result, dict)
            else {
                "success": False,
                "error": "Desktop connector returned no result",
                "data": None,
            }
        )

    async def act(
        self,
        action: str,
        params: dict[str, Any],
        context: ToolCallContext,
    ) -> dict[str, Any]:
        if context.workflow_id is None:
            raise AppToolError(
                "missing_context",
                "Desktop actions require a workflow context.",
            )
        dispatch_context: dict[str, Any] = {}
        if context.execution_id is not None:
            dispatch_context["execution_log_id"] = context.execution_id
        result = await self.connector.act(
            {
                "action": action,
                "params": dict(params),
            },
            {
                "workflow_id": context.workflow_id,
                "user_id": context.user_id,
                "workspace_id": context.workspace_id,
                "dispatch_context": dispatch_context,
            },
        )
        return (
            result
            if isinstance(result, dict)
            else {
                "success": False,
                "error": "Desktop connector returned no result",
                "data": None,
            }
        )


__all__ = ["DesktopBackend"]
