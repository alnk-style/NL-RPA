from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AppSettingResponse(BaseModel):
    id: int
    user_id: int
    key: str
    value: dict[str, Any]
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class BrowserProfileResponse(BaseModel):
    id: int
    user_id: int
    name: str
    user_data_dir: str | None = None
    profile_directory: str
    login_state: str
    last_verified_at: datetime | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class BrowserProfileCreate(BaseModel):
    name: str = Field(min_length=1)
    user_data_dir: str | None = None
    profile_directory: str = "Default"


class BrowserProfileLoginRequest(BaseModel):
    human_task_id: int
    url: str | None = None


class BrowserProfileLoginResponse(BaseModel):
    human_task_id: int
    browser_profile_id: int
    profile_name: str
    status: str
    current_url: str | None = None


class EmailSettingsUpdate(BaseModel):
    host: str = ""
    port: int = Field(default=587, ge=1, le=65535)
    username: str = ""
    password: str | None = Field(default=None, max_length=10000)
    from_email: str = ""
    start_tls: bool = True
    default_recipient: str = ""
    credential_key: str = Field(
        default="smtp_default",
        pattern=r"^[A-Za-z0-9_.-]{1,255}$",
    )


class EmailTestSendRequest(BaseModel):
    recipient: str | None = None


class ExportDirectoriesUpdate(BaseModel):
    directories: list[str] = Field(default_factory=list, max_length=20)


class ExportDirectoriesResponse(BaseModel):
    directories: list[str] = Field(default_factory=list)


__all__ = [
    "AppSettingResponse",
    "BrowserProfileCreate",
    "BrowserProfileLoginRequest",
    "BrowserProfileLoginResponse",
    "BrowserProfileResponse",
    "EmailSettingsUpdate",
    "EmailTestSendRequest",
    "ExportDirectoriesResponse",
    "ExportDirectoriesUpdate",
]
