from __future__ import annotations

from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.app_setting import AppSetting
from app.models.credential import Credential
from app.security.credentials import decrypt_value, encrypt_value

VISUAL_SETTINGS_KEY = "visual_settings"
VISUAL_CREDENTIAL_KEY = "remote_vlm_api_key"


class VisualRedactionPolicy(BaseModel):
    mask_password_fields: bool = True
    mask_sensitive_text: bool = True
    custom_rects: list[tuple[int, int, int, int]] = Field(
        default_factory=list,
        max_length=20,
    )

    @field_validator("custom_rects")
    @classmethod
    def _valid_rects(
        cls,
        value: list[tuple[int, int, int, int]],
    ) -> list[tuple[int, int, int, int]]:
        for x1, y1, x2, y2 in value:
            if min(x1, y1, x2, y2) < 0 or x2 <= x1 or y2 <= y1:
                raise ValueError("custom_rects must be positive rectangles")
        return value


class VisualSettings(BaseModel):
    enabled: bool = False
    provider: Literal["disabled", "local_ocr", "remote_vlm"] = "disabled"
    endpoint: str = ""
    model: str = ""
    confidence_threshold: float = Field(default=0.9, ge=0.0, le=1.0)
    max_candidates: int = Field(default=3, ge=1, le=3)
    max_attempts: int = Field(default=2, ge=1, le=10)
    screenshot_scope: Literal["viewport", "window"] = "viewport"
    redaction_policy: VisualRedactionPolicy = Field(
        default_factory=VisualRedactionPolicy
    )
    api_key: str | None = None


class VisualSettingsUpdate(VisualSettings):
    @model_validator(mode="after")
    def _validate_provider_config(self) -> "VisualSettingsUpdate":
        if self.provider == "remote_vlm":
            parsed = urlparse(self.endpoint.strip())
            if parsed.scheme != "https" or not parsed.netloc:
                raise ValueError(
                    "remote_vlm endpoint must be an HTTPS URL"
                )
            if not self.model.strip():
                raise ValueError(
                    "remote_vlm model must not be empty"
                )
        return self


async def load_visual_settings(
    session: AsyncSession,
    config: Any,
    user_id: int,
    *,
    include_api_key: bool = False,
) -> VisualSettings:
    setting = (
        await session.execute(
            select(AppSetting).where(
                AppSetting.user_id == int(user_id),
                AppSetting.key == VISUAL_SETTINGS_KEY,
            )
        )
    ).scalar_one_or_none()
    values = dict(setting.value) if setting is not None else {}
    try:
        settings = VisualSettings.model_validate(values)
    except Exception:
        settings = VisualSettings()
    if include_api_key:
        credential = (
            await session.execute(
                select(Credential).where(
                    Credential.user_id == int(user_id),
                    Credential.scope == "visual",
                    Credential.key == VISUAL_CREDENTIAL_KEY,
                )
            )
        ).scalar_one_or_none()
        if credential is not None:
            settings = settings.model_copy(
                update={
                    "api_key": decrypt_value(
                        config,
                        bytes(credential.encrypted_value),
                    )
                }
            )
    return settings


async def save_visual_settings(
    session: AsyncSession,
    config: Any,
    user_id: int,
    payload: VisualSettingsUpdate,
) -> VisualSettings:
    setting = (
        await session.execute(
            select(AppSetting).where(
                AppSetting.user_id == int(user_id),
                AppSetting.key == VISUAL_SETTINGS_KEY,
            )
        )
    ).scalar_one_or_none()
    values = payload.model_dump(
        mode="json",
        exclude={"api_key"},
    )
    if setting is None:
        setting = AppSetting(
            user_id=int(user_id),
            key=VISUAL_SETTINGS_KEY,
            value=values,
        )
        session.add(setting)
    else:
        setting.value = values
    if payload.api_key is not None:
        encrypted = encrypt_value(config, payload.api_key)
        credential = (
            await session.execute(
                select(Credential).where(
                    Credential.user_id == int(user_id),
                    Credential.scope == "visual",
                    Credential.key == VISUAL_CREDENTIAL_KEY,
                )
            )
        ).scalar_one_or_none()
        if credential is None:
            session.add(
                Credential(
                    user_id=int(user_id),
                    workflow_id=None,
                    scope="visual",
                    key=VISUAL_CREDENTIAL_KEY,
                    name="Remote visual API key",
                    encrypted_value=encrypted,
                )
            )
        else:
            credential.encrypted_value = encrypted
    await session.flush()
    return payload.model_copy(update={"api_key": None})


def visual_settings_payload(
    settings: VisualSettings,
    *,
    api_key_configured: bool,
) -> dict[str, Any]:
    values = settings.model_dump(
        mode="json",
        exclude={"api_key"},
    )
    values["api_key_configured"] = bool(api_key_configured)
    return values


__all__ = [
    "VISUAL_CREDENTIAL_KEY",
    "VISUAL_SETTINGS_KEY",
    "VisualSettings",
    "VisualSettingsUpdate",
    "VisualRedactionPolicy",
    "load_visual_settings",
    "save_visual_settings",
    "visual_settings_payload",
]
