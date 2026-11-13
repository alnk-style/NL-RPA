from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class DeliveryRecordResponse(BaseModel):
    id: int
    execution_id: int
    step_id: int | None = None
    channel: str
    status: str
    recipient: Any = None
    attachment_paths: list[Any] | None = None
    provider_message_id: str | None = None
    error: str | None = None
    created_at: datetime
    finished_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


__all__ = ["DeliveryRecordResponse"]
