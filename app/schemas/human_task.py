from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class HumanTaskResponse(BaseModel):
    id: int
    execution_id: int
    step_id: int | None = None
    kind: str
    instruction: str
    payload: dict[str, Any] = Field(default_factory=dict)
    screenshot_path: str | None = None
    status: str
    created_at: datetime
    resolved_at: datetime | None = None
    expires_at: datetime | None = None
    workflow_name: str | None = None
    workflow_version: int | None = None
    execution_status: str | None = None
    host_id: int | None = None
    host_environment: str | None = None
    host_bound: bool = False
    host_affinity_configured: bool = False
    waiting_seconds: int = 0
    can_resume: bool = False
    current_url: str | None = None
    browser_profile_id: int | None = None
    last_verification_error: str | None = None

    model_config = ConfigDict(from_attributes=True)


class HumanTaskListResponse(BaseModel):
    items: list[HumanTaskResponse] = Field(default_factory=list)


class HumanTaskResumeRequest(BaseModel):
    decision: Literal["resume"] = "resume"
    verification: dict[str, Any] = Field(default_factory=dict)


class HumanTaskCancelRequest(BaseModel):
    reason: str = Field(default="用户取消了人工任务。", max_length=2000)


class HumanTaskExecutionState(BaseModel):
    id: int
    status: str
    human_task_id: int | None = None


class HumanTaskResumeResponse(BaseModel):
    human_task: HumanTaskResponse
    execution: HumanTaskExecutionState
    idempotent: bool = False


__all__ = [
    "HumanTaskCancelRequest",
    "HumanTaskExecutionState",
    "HumanTaskListResponse",
    "HumanTaskResponse",
    "HumanTaskResumeRequest",
    "HumanTaskResumeResponse",
]
