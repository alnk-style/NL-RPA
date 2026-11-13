from __future__ import annotations

from typing import Any

from app.actions.protocol import (
    StepError,
    StepResult,
    StepStatus,
)
from app.engine.runner import ExecutionResult


class WebActionAdapter:
    """Thin typed boundary over the existing WebExecutor."""

    def __init__(self, executor: Any) -> None:
        self.executor = executor

    async def execute(
        self,
        context: Any,
        action: str,
        params: dict[str, Any],
        *,
        locator: dict[str, Any] | None = None,
    ) -> StepResult:
        services = getattr(context, "services", {})
        if not isinstance(services, dict):
            services = {}
        page = services.get("page")
        if page is None:
            return StepResult(
                status=StepStatus.FAILED,
                message="Web page is not available",
                error=StepError(
                    category="environment_unavailable",
                    message="Web page is not available",
                ),
            )
        step = {
            "step_id": 1,
            "executor": "web",
            "action": action,
            "params": dict(params),
        }
        if isinstance(locator, dict):
            step["locator"] = dict(locator)
        execution_context = services.get("execution_context")
        if not isinstance(execution_context, dict):
            execution_context = {}
        execution_context = {
            **execution_context,
            "variables": dict(getattr(context, "variables", {}) or {}),
        }
        result = await self.executor.execute_steps(
            [step],
            page=page,
            context=execution_context,
            execution_id=getattr(context, "execution_id", None),
        )
        if not isinstance(result, ExecutionResult):
            raise TypeError("WebExecutor must return ExecutionResult")
        return _convert_result(result)


def _convert_result(result: ExecutionResult) -> StepResult:
    details = result.details if isinstance(result.details, dict) else {}
    logs = [
        dict(entry)
        for entry in (result.step_logs or [])
        if isinstance(entry, dict)
    ]
    status = StepStatus.SUCCESS if result.success else StepStatus.FAILED
    if details.get("awaiting_user"):
        status = StepStatus.AWAITING_USER
    elif logs and all(
        entry.get("status") == "warning" for entry in logs
    ):
        status = StepStatus.WARNING
    variables = details.get("extracted")
    if not isinstance(variables, dict):
        variables = {}
    category = str(
        details.get("failure_category")
        or next(
            (
                entry.get("failure_category")
                for entry in reversed(logs)
                if entry.get("failure_category")
            ),
            "unknown" if status is StepStatus.FAILED else "",
        )
    )
    error = None
    if result.error or status is StepStatus.FAILED:
        error = StepError(
            category=category or "unknown",
            message=str(result.error or "Web action failed"),
        )
    human_action = None
    if status is StepStatus.AWAITING_USER:
        human_action = {
            "kind": "login_required",
            "instruction": result.error or "请在浏览器中完成登录",
            "payload": {"step_logs": logs},
        }
    return StepResult(
        status=status,
        message=(
            result.error
            or str(details.get("message") or "")
            or ("Web action completed" if result.success else "")
        ),
        value=details.get("value"),
        variables=variables,
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
        error=error,
        human_action=human_action,
    )


__all__ = ["WebActionAdapter"]
