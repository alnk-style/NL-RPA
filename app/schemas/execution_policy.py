from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ExecutionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal[
        "interactive_preferred",
        "background_if_safe",
        "isolated_preferred",
    ] = "interactive_preferred"
    login_policy: Literal["pause_and_resume"] = "pause_and_resume"
    visual_fallback: Literal[
        "disabled",
        "on_locator_failure",
        "always",
    ] = "on_locator_failure"
    host_policy: Literal[
        "local_only",
        "local_then_isolated",
        "isolated_only",
    ] = "local_then_isolated"
    failure_policy: Literal["stop", "pause"] = "stop"
    required_delivery: bool = True
    max_duration_seconds: int = Field(default=1800, gt=0, le=86400)
    human_handoff: bool = True


def default_execution_policy() -> ExecutionPolicy:
    return ExecutionPolicy()


def default_execution_policy_dict() -> dict[str, Any]:
    return default_execution_policy().model_dump(mode="json")


def validate_execution_policy(value: Any) -> ExecutionPolicy:
    if value is None:
        return default_execution_policy()
    if isinstance(value, ExecutionPolicy):
        return value
    if not isinstance(value, dict):
        raise ValueError("execution_policy must be an object")
    merged = {
        **default_execution_policy_dict(),
        **value,
    }
    return ExecutionPolicy.model_validate(merged)


__all__ = [
    "ExecutionPolicy",
    "default_execution_policy",
    "default_execution_policy_dict",
    "validate_execution_policy",
]
