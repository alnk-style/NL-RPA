from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.workflow_version import WorkflowVersionChange


class DraftRequirements(BaseModel):
    mode: Literal["test"] = "test"
    creates_version: bool = False
    runtime_kind: Literal["draft"] = "draft"


class WorkflowDraftResult(BaseModel):
    plan: dict[str, Any]
    base_plan: dict[str, Any] | None = None
    valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    needs_confirmation: bool = True
    observations: dict[str, Any] | None = None
    changes: list[WorkflowVersionChange] = Field(default_factory=list)
    requirements: DraftRequirements = Field(default_factory=DraftRequirements)


class DraftFromGoalRequest(BaseModel):
    goal: str = Field(min_length=1)
    start_url: str | None = None
    session_id: str | None = None
    workflow_id: int | None = None
    base_plan: dict[str, Any] | None = None
    selected_step_id: int | None = Field(default=None, ge=1)


class WorkflowValidateRequest(BaseModel):
    plan: dict[str, Any] | None = None
    draft_revision: int | None = Field(default=None, ge=0)


class WorkflowObserveRequest(BaseModel):
    url: str = Field(min_length=1)
    mode: Literal["web"] = "web"
    max_elements: int = Field(default=80, ge=1, le=300)
    include_accessibility: bool = True


class WorkflowDraftRunRequest(BaseModel):
    plan: dict[str, Any]
    variables: dict[str, Any] = Field(default_factory=dict)
    draft_revision: int | None = Field(default=None, ge=0)


class WorkflowTestStepRequest(WorkflowDraftRunRequest):
    step_id: int = Field(ge=1)


class WorkflowDraftExecutionResponse(BaseModel):
    workflow_id: int
    draft_revision: int
    mode: Literal["test", "dry_run"]
    formal_execution: bool = False
    success: bool
    status: str
    message: str = ""
    error: str | None = None
    duration_seconds: float = 0.0
    variables: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    step_logs: list[dict[str, Any]] = Field(default_factory=list)
    requirements: dict[str, Any] = Field(
        default_factory=lambda: {
            "mode": "test",
            "creates_version": False,
        }
    )


__all__ = [
    "DraftFromGoalRequest",
    "DraftRequirements",
    "WorkflowDraftResult",
    "WorkflowDraftExecutionResponse",
    "WorkflowDraftRunRequest",
    "WorkflowObserveRequest",
    "WorkflowTestStepRequest",
    "WorkflowValidateRequest",
]
