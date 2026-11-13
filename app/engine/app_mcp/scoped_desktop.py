"""Desktop adapter scoped to one discovered executable."""

from __future__ import annotations

from pathlib import Path
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


class ScopedDesktopAdapter:
    def __init__(self, environment: AdapterEnvironment) -> None:
        self.environment = environment
        connector_name = str(
            environment.options.get("connector") or "desktop"
        )
        connector = environment.connectors.get(connector_name)
        if connector is None and connector_name == "desktop":
            connector = environment.connectors.get("windows")
        self.backend = (
            DesktopBackend(connector) if connector is not None else None
        )
        raw_path = str(
            environment.options.get("executable_path") or ""
        ).strip()
        self.executable_path = (
            str(Path(raw_path).expanduser().resolve(strict=False))
            if raw_path
            else ""
        )

    async def probe(self) -> AppProbe:
        if self.backend is None or not self.executable_path:
            return AppProbe(
                installed=False,
                running=False,
                requires_login=False,
            )
        probe = await self.backend.probe()
        return AppProbe(
            installed=Path(self.executable_path).is_file(),
            running=probe.running,
            requires_login=probe.requires_login,
            version=probe.version,
        )

    async def observe(self) -> dict[str, Any]:
        if self.backend is None or not self.executable_path:
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
        if self.backend is None or not self.executable_path:
            return ActionResult.fail(
                code="backend_unavailable",
                message="Desktop connector is unavailable.",
            )
        if name == "app_launch":
            launch_params: dict[str, Any] = {
                "path": self.executable_path,
            }
            for key in ("wait_for_window", "window_timeout"):
                if key in payload:
                    launch_params[key] = payload[key]
            return await self._act(
                "launch_app",
                launch_params,
                name,
            )
        if name == "app_observe":
            observed = await self.backend.observe(payload)
            return _from_connector_result(observed, name)
        if name == "app_activate":
            return await self._act(
                "activate_window",
                payload,
                name,
            )
        if name == "app_read_text":
            return await self._act(
                "read_text",
                payload,
                name,
            )
        return ActionResult.fail(
            code="unknown_tool",
            message=f"Unknown scoped desktop tool: {name}",
        )

    async def close(self) -> None:
        return None

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
        data = result.get("data")
        return ActionResult.ok(
            message=f"{tool_name} ok",
            data=data if isinstance(data, dict) else {},
        )
    return ActionResult.fail(
        code="desktop_action_failed",
        message=sanitize_message(
            result.get("error") or "Desktop connector action failed."
        ),
    )


__all__ = ["ScopedDesktopAdapter"]
