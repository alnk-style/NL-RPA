"""Generated adapter skeleton for one application MCP integration."""

from __future__ import annotations

from typing import Any

from app.engine.app_mcp.contracts import (
    ActionResult,
    AdapterEnvironment,
    AppProbe,
    current_call_context,
)


class ExampleAppAdapter:
    """Replace this placeholder with API, CLI, UIA or browser automation."""

    def __init__(self, environment: AdapterEnvironment) -> None:
        self.environment = environment

    async def probe(self) -> AppProbe:
        # TODO: Check the real application installation and process state.
        return AppProbe(
            installed=True,
            running=True,
            requires_login=False,
            version="0.1.0",
        )

    async def observe(self) -> dict[str, Any]:
        # TODO: Return a small, stable observation instead of raw UI trees.
        return {
            "success": True,
            "data": {
                "adapter": "generated",
                "connected": True,
            },
        }

    async def execute(
        self,
        action: str,
        args: dict[str, Any],
    ) -> ActionResult:
        if action == "example_status":
            probe = await self.probe()
            return ActionResult.ok(
                message="example_status ok",
                data={"probe": probe.to_dict()},
            )

        if action == "example_write":
            # TODO: Replace this placeholder with a bounded API/CLI/UIA call.
            context = current_call_context()
            return ActionResult.ok(
                message="example_write accepted",
                data={
                    "value": str(args.get("value") or ""),
                    "request_id": context.request_id,
                    "placeholder": True,
                },
            )

        return ActionResult.fail(
            code="unknown_tool",
            message=f"Unknown application tool: {action}",
        )

    async def close(self) -> None:
        # TODO: Close SDK clients, subprocesses or browser sessions.
        return None
