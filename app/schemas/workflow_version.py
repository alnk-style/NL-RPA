from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class WorkflowVersionResponse(BaseModel):
    id: int
    workflow_id: int
    version: int
    plan: dict[str, Any]
    validation: dict[str, Any]
    action_catalog_version: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class WorkflowPublishResponse(BaseModel):
    status: str = "published"
    workflow_id: int
    active_version_id: int
    version: int
    action_catalog_version: str


class WorkflowRollbackRequest(BaseModel):
    version_id: int = Field(gt=0)


class WorkflowVersionChange(BaseModel):
    kind: str
    path: str
    before: Any = None
    after: Any = None


class WorkflowVersionDiff(BaseModel):
    from_version_id: int
    to_version_id: int
    changes: list[WorkflowVersionChange] = Field(default_factory=list)


__all__ = [
    "WorkflowPublishResponse",
    "WorkflowRollbackRequest",
    "WorkflowVersionChange",
    "WorkflowVersionDiff",
    "WorkflowVersionResponse",
]
