from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.actions.protocol import StepStatus


@dataclass
class EmptyPolicyResult:
    status: StepStatus
    error_category: str | None = None
    message: str = ""


@dataclass
class ActionContext:
    workflow: Any
    version: Any = None
    execution_id: int | None = None
    step_id: int | None = None
    variables: dict[str, Any] = field(default_factory=dict)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    registry: Any = None
    services: dict[str, Any] = field(default_factory=dict)
    action: str = ""


def apply_empty_policy(
    value: Any,
    *,
    allow_empty: bool = False,
    empty_policy: str = "failed",
) -> EmptyPolicyResult:
    empty = isinstance(value, (list, tuple, dict)) and len(value) == 0
    if not empty:
        return EmptyPolicyResult(status=StepStatus.SUCCESS)
    if not allow_empty:
        return EmptyPolicyResult(
            status=StepStatus.FAILED,
            error_category="data_empty",
            message="Action returned empty data",
        )
    if str(empty_policy).strip().lower() == "warning":
        return EmptyPolicyResult(
            status=StepStatus.WARNING,
            error_category="data_empty",
            message="Action returned empty data",
        )
    return EmptyPolicyResult(status=StepStatus.SUCCESS)


__all__ = [
    "ActionContext",
    "EmptyPolicyResult",
    "apply_empty_policy",
]
