from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class StepStatus(str, Enum):
    SUCCESS = "success"
    WARNING = "warning"
    FAILED = "failed"
    CANCELLED = "cancelled"
    AWAITING_USER = "awaiting_user"


class StepError(BaseModel):
    category: str = "unknown"
    message: str = ""
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class HumanActionRequest(BaseModel):
    kind: str
    instruction: str
    payload: dict[str, Any] = Field(default_factory=dict)
    screenshot_path: str | None = None


class ArtifactRecord(BaseModel):
    path: str
    name: str
    size: int = Field(ge=0)
    sha256: str
    delivered: bool = False
    delivered_path: str | None = None


class ActionDefinition(BaseModel):
    id: str = Field(min_length=1)
    executor: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str = ""
    category: str = Field(min_length=1)
    risk_level: str = "read_only"
    params_schema: dict[str, Any]
    output_schema: dict[str, Any] = Field(default_factory=dict)
    requires: dict[str, bool] = Field(default_factory=dict)
    catalog_version: str = Field(min_length=1)
    legacy_only: bool = False


class StepResult(BaseModel):
    status: StepStatus
    message: str = ""
    value: Any = None
    variables: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    verification: dict[str, Any] = Field(default_factory=dict)
    duration_seconds: float = Field(default=0.0, ge=0)
    error: StepError | None = None
    human_action: dict[str, Any] | None = None

    @property
    def success(self) -> bool:
        return self.status in {StepStatus.SUCCESS, StepStatus.WARNING}

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def coerce_legacy_step_result(value: Any) -> StepResult:
    """Convert existing dict/dataclass style results to the stable protocol."""

    if isinstance(value, StepResult):
        return value
    if not isinstance(value, dict):
        value = {
            "success": bool(getattr(value, "success", True)),
            "message": str(getattr(value, "message", "") or ""),
            "text": getattr(value, "text", None),
            "value": getattr(value, "value", None),
            "items": getattr(value, "items", None),
            "artifacts": getattr(value, "artifacts", []),
            "data": getattr(value, "data", {}),
            "verification": getattr(value, "verification", {}),
        }

    raw_status = str(value.get("status") or "").strip().lower()
    human_action = value.get("human_action")
    if raw_status:
        try:
            status = StepStatus(raw_status)
        except ValueError:
            status = StepStatus.FAILED
    elif human_action:
        status = StepStatus.AWAITING_USER
    elif bool(value.get("success", False)):
        status = StepStatus.SUCCESS
    else:
        status = StepStatus.FAILED

    error_value = value.get("error")
    category = str(
        value.get("error_category")
        or value.get("failure_category")
        or ("unknown" if status is StepStatus.FAILED else "")
    ).strip()
    error: StepError | None = None
    if status in {StepStatus.FAILED, StepStatus.AWAITING_USER}:
        if isinstance(error_value, dict):
            error = StepError.model_validate(error_value)
        else:
            error = StepError(
                category=category or "unknown",
                message=str(error_value or value.get("message") or ""),
            )
    elif isinstance(error_value, dict):
        error = StepError.model_validate(error_value)

    data = value.get("data")
    variables = value.get("variables")
    if not isinstance(variables, dict):
        variables = dict(data) if isinstance(data, dict) else {}

    try:
        duration = max(float(value.get("duration_seconds") or 0.0), 0.0)
    except (TypeError, ValueError):
        duration = 0.0

    result_value = value.get("value")
    if result_value is None:
        result_value = value.get("text")
    if result_value is None:
        result_value = value.get("items")

    return StepResult(
        status=status,
        message=str(value.get("message") or ""),
        value=result_value,
        variables=variables,
        artifacts=[
            dict(item)
            for item in (value.get("artifacts") or [])
            if isinstance(item, dict)
        ],
        verification=(
            dict(value.get("verification") or {})
            if isinstance(value.get("verification"), dict)
            else {}
        ),
        duration_seconds=duration,
        error=error,
        human_action=(
            dict(human_action) if isinstance(human_action, dict) else None
        ),
    )


__all__ = [
    "ActionDefinition",
    "ArtifactRecord",
    "HumanActionRequest",
    "StepError",
    "StepResult",
    "StepStatus",
    "coerce_legacy_step_result",
]
