from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.actions.protocol import StepError, StepResult, StepStatus
from app.engine.runner import ExecutionResult


class WindowsActionAdapter:
    def __init__(self, dispatcher: Any) -> None:
        self.dispatcher = dispatcher

    async def execute(
        self,
        context: Any,
        action: str,
        params: dict[str, Any],
    ) -> StepResult:
        services = getattr(context, "services", {})
        if not isinstance(services, dict):
            services = {}
        execution_context = services.get("execution_context")
        if not isinstance(execution_context, dict):
            execution_context = {}
        workflow = getattr(context, "workflow", None)
        view = SimpleNamespace(
            id=getattr(workflow, "id", None),
            user_id=getattr(workflow, "user_id", None),
            workspace_id=getattr(workflow, "workspace_id", None),
            name=getattr(workflow, "name", None),
            executor_type="windows",
            required_capabilities=[],
            structured_plan={
                "executor_type": "windows",
                "steps": [
                    {
                        "step_id": 1,
                        "executor": "windows",
                        "action": action,
                        "params": dict(params),
                    }
                ],
            },
        )
        result = await self.dispatcher.dispatch(
            view,
            {
                **execution_context,
                "variables": dict(getattr(context, "variables", {}) or {}),
            },
        )
        if not isinstance(result, ExecutionResult):
            raise TypeError("Desktop dispatcher must return ExecutionResult")
        details = result.details if isinstance(result.details, dict) else {}
        variables = details.get("extracted")
        return StepResult(
            status=(
                StepStatus.SUCCESS
                if result.success
                else StepStatus.FAILED
            ),
            message=(
                result.output
                or result.error
                or ("Desktop action completed" if result.success else "")
            ),
            variables=variables if isinstance(variables, dict) else {},
            screenshots=list(result.screenshots or []),
            artifacts=[
                dict(item)
                for item in result.artifacts
                if isinstance(item, dict)
            ],
            verification=(
                dict(details.get("verification") or {})
                if isinstance(details.get("verification"), dict)
                else {}
            ),
            duration_seconds=float(result.duration_seconds or 0.0),
            error=(
                None
                if result.success
                else StepError(
                    category=str(
                        details.get("failure_category") or "unknown"
                    ),
                    message=str(result.error or "Desktop action failed"),
                )
            ),
        )


__all__ = ["WindowsActionAdapter"]
