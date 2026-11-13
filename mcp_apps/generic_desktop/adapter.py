"""Generic desktop adapter backed exclusively by DesktopBridgeConnector."""

from __future__ import annotations

from typing import Any

from app.engine.app_mcp.backends.desktop import DesktopBackend
from app.engine.app_mcp.contracts import (
    ActionResult,
    AdapterEnvironment,
    AppProbe,
    AppToolError,
    current_call_context,
    sanitize_message,
)


class GenericDesktopAdapter:
    def __init__(self, environment: AdapterEnvironment) -> None:
        self.environment = environment
        connector_name = str(environment.options.get("connector") or "desktop")
        connector = environment.connectors.get(connector_name)
        if connector is None and connector_name == "desktop":
            connector = environment.connectors.get("windows")
        self.backend = DesktopBackend(connector) if connector is not None else None

    async def probe(self) -> AppProbe:
        if self.backend is None:
            return AppProbe(
                installed=False,
                running=False,
                requires_login=False,
            )
        try:
            return await self.backend.probe()
        except Exception:  # noqa: BLE001
            return AppProbe(
                installed=False,
                running=False,
                requires_login=False,
            )

    async def observe(self) -> dict[str, Any]:
        if self.backend is None:
            return {
                "success": False,
                "error": "Desktop connector is unavailable.",
                "data": None,
            }
        return await self.backend.observe({})

    async def execute(
        self,
        action: str,
        args: dict[str, Any],
    ) -> ActionResult:
        name = str(action or "").strip()
        payload = dict(args or {})
        if name == "desktop_probe":
            return await self._probe_result()
        if self.backend is None:
            return ActionResult.fail(
                code="backend_unavailable",
                message="Desktop connector is unavailable.",
            )

        if name == "desktop_observe":
            observed = await self.backend.observe(payload)
            return _from_connector_result(observed, "desktop_observe")
        if name == "desktop_launch":
            return await self._act(
                "launch_app",
                payload,
                "desktop_launch",
            )
        if name == "desktop_activate":
            return await self._act(
                "activate_window",
                payload,
                "desktop_activate",
            )
        if name == "desktop_read_text":
            return await self._act(
                "read_text",
                payload,
                "desktop_read_text",
            )
        return ActionResult.fail(
            code="unknown_tool",
            message=f"Unknown desktop tool: {name}",
        )

    async def close(self) -> None:
        # The connector is shared with the platform and owned by it.
        return None

    async def _probe_result(self) -> ActionResult:
        probe = await self.probe()
        if self.backend is None:
            return ActionResult.fail(
                code="backend_unavailable",
                message="Desktop connector is unavailable.",
            )
        try:
            capabilities = await self.backend.connector.capabilities()
        except Exception:  # noqa: BLE001
            capabilities = {}
        return ActionResult.ok(
            message="desktop_probe ok",
            data={
                "probe": probe.to_dict(),
                "capabilities": capabilities,
            },
        )

    async def _act(
        self,
        connector_action: str,
        params: dict[str, Any],
        tool_name: str,
    ) -> ActionResult:
        if self.backend is None:
            return ActionResult.fail(
                code="backend_unavailable",
                message="Desktop connector is unavailable.",
            )
        try:
            result = await self.backend.act(
                connector_action,
                params,
                current_call_context(),
            )
        except AppToolError as exc:
            return ActionResult.fail(
                code=exc.code,
                message=exc.safe_message,
                details=exc.details,
                retryable=exc.retryable,
            )
        return _from_connector_result(result, tool_name)


def _from_connector_result(
    result: dict[str, Any],
    tool_name: str,
) -> ActionResult:
    if bool(result.get("success")):
        return ActionResult.ok(
            message=f"{tool_name} ok",
            data=result.get("data") if isinstance(result.get("data"), dict) else {},
        )
    return ActionResult.fail(
        code="desktop_action_failed",
        message=sanitize_message(
            result.get("error") or "Desktop connector action failed."
        ),
    )


__all__ = ["GenericDesktopAdapter"]
