from __future__ import annotations

from typing import Any

from app.actions.protocol import StepError, StepResult, StepStatus


class MCPActionAdapter:
    def __init__(self, registry: Any) -> None:
        self.registry = registry

    async def execute(
        self,
        context: Any,
        action: str,
        params: dict[str, Any],
    ) -> StepResult:
        if action != "call_tool":
            return StepResult(
                status=StepStatus.FAILED,
                message=f"Unsupported MCP action: {action}",
                error=StepError(
                    category="business_validation_failed",
                    message=f"Unsupported MCP action: {action}",
                ),
            )
        server = str(params.get("server") or "").strip()
        tool = str(params.get("tool") or "").strip()
        if not server or not tool:
            return StepResult(
                status=StepStatus.FAILED,
                message="MCP call_tool requires server and tool",
                error=StepError(
                    category="business_validation_failed",
                    message="MCP call_tool requires server and tool",
                ),
            )
        workflow = getattr(context, "workflow", None)
        arguments = params.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}
        outcome = await self.registry.call_tool(
            server,
            tool,
            arguments,
            context={
                "workflow_id": getattr(workflow, "id", None),
                "execution_id": getattr(context, "execution_id", None),
                "user_id": getattr(workflow, "user_id", None),
                "workspace_id": getattr(workflow, "workspace_id", None),
                "credentials": (
                    getattr(context, "services", {}) or {}
                ).get("credentials")
                if isinstance(getattr(context, "services", {}), dict)
                else None,
                "step_id": 1,
            },
            timeout=params.get("timeout_seconds"),
            retry_policy=None,
            confirm=params.get("confirm"),
        )
        if outcome.success:
            value = _extract_text(outcome.result)
            return StepResult(
                status=StepStatus.SUCCESS,
                message=f"MCP {server}.{tool} completed",
                value=value,
                duration_seconds=float(outcome.duration_ms or 0.0) / 1000,
                verification={
                    "audit_id": outcome.audit_id,
                    "risk_level": outcome.risk_level,
                },
            )
        category = getattr(outcome.error_category, "value", "unknown")
        return StepResult(
            status=StepStatus.FAILED,
            message=outcome.error or f"MCP {server}.{tool} failed",
            duration_seconds=float(outcome.duration_ms or 0.0) / 1000,
            error=StepError(
                category=str(category),
                message=str(outcome.error or "MCP tool failed"),
                details=dict(outcome.to_step_details()),
            ),
        )


def _extract_text(result: Any) -> Any:
    if not isinstance(result, dict):
        return result
    content = result.get("content")
    if isinstance(content, list):
        texts = [
            str(item.get("text") or "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        if texts:
            return "\n".join(texts)
    return result


__all__ = ["MCPActionAdapter"]
