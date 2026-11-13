from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.execution_policy import (
    ExecutionPolicy,
    default_execution_policy,
)
from app.schemas.delivery import DeliveryRecordResponse


class WorkflowCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str | None = None
    natural_language_input: str
    executor_type: str = "web"
    required_capabilities: list[str] = Field(default_factory=list)


class WorkflowUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    description: str | None = None
    natural_language_input: str | None = None
    structured_plan: dict[str, Any] | None = None
    generated_code: str | None = None
    dag_definition: dict[str, Any] | None = None
    trigger_type: str | None = None
    cron_expression: str | None = None
    status: str | None = None
    notification_config: dict[str, Any] | None = None
    executor_type: str | None = None
    required_capabilities: list[str] | None = None
    execution_policy: dict[str, Any] | None = None
    schedule_timezone: str | None = None
    draft_revision: int | None = Field(default=None, ge=0)


class WorkflowResponse(BaseModel):
    id: int
    user_id: int
    name: str
    description: str | None
    natural_language_input: str
    structured_plan: dict[str, Any] | None
    generated_code: str | None
    dag_definition: dict[str, Any] | None
    trigger_type: str
    cron_expression: str | None
    status: str
    notification_config: dict[str, Any] | None
    executor_type: str
    required_capabilities: list[str] | None
    execution_mode: str
    execution_policy: dict[str, Any]
    active_version_id: int | None = None
    schedule_timezone: str
    draft_revision: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ExecutionResponse(BaseModel):
    id: int
    workflow_id: int
    status: str
    started_at: datetime
    finished_at: datetime | None
    error_message: str | None
    screenshots: list[Any] | None
    artifacts: list[Any] | None = None
    step_logs: list[Any] | None
    workflow_version_id: int | None = None
    execution_policy_snapshot: dict[str, Any] | None = None
    host_id: int | None = None
    host_environment: str | None = None
    host_bound: bool = False
    host_affinity_configured: bool = False
    host_name: str | None = None
    host_status: str | None = None
    host_capabilities: list[str] = Field(default_factory=list)
    host_reason: str | None = None
    browser_profile_id: int | None = None
    human_task_id: int | None = None
    trigger_type: str = "manual"

    model_config = ConfigDict(from_attributes=True)


class ExecutionListResponse(ExecutionResponse):
    workflow_name: str
    workflow_version: int | None = None


class ExecutionDetailResponse(ExecutionResponse):
    workflow_name: str
    workflow_version: int | None = None
    deliveries: list[DeliveryRecordResponse] = Field(default_factory=list)
    variables: dict[str, Any] = Field(default_factory=dict)


class ExecutionCancelRequest(BaseModel):
    reason: str = Field(default="用户取消了执行。", max_length=2000)


class ChatMessage(BaseModel):
    session_id: str
    message: str
    workflow_id: int | None = None


class ChatResponse(BaseModel):
    reply: str
    structured_plan: dict[str, Any] | None = None
    generated_code: str | None = None
    needs_confirmation: bool = False
    suggestions: list[str] = Field(default_factory=list)
    executor_type: str = "web"


class ChatSessionPayload(BaseModel):
    title: str
    messages: list[dict[str, Any]] = Field(default_factory=list)
    structured_plan: dict[str, Any] | None = None
    generated_code: str | None = None
    needs_confirmation: bool = False
    active_workflow_id: int | None = None
    initial_input: str = ""
    updated_at: datetime | None = None


class ChatSessionResponse(ChatSessionPayload):
    session_id: str
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
