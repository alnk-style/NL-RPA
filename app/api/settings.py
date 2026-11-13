from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.config import get_settings
from app.database import get_db
from app.models.user import User
from app.schemas.settings import (
    EmailSettingsUpdate,
    EmailTestSendRequest,
)
from app.services.email import (
    EmailResult,
    EmailService,
    email_result_payload,
    email_settings_payload,
    load_email_settings,
    save_email_settings,
)

router = APIRouter(
    prefix="/api/settings/email",
    tags=["settings"],
)


@router.get("")
async def get_email_settings(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    value = await load_email_settings(
        db,
        get_settings(),
        current_user.id,
        include_password=True,
    )
    return email_settings_payload(
        value,
        password_configured=bool(value.password),
    )


@router.put("")
async def update_email_settings(
    payload: EmailSettingsUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    value = await save_email_settings(
        db,
        get_settings(),
        current_user.id,
        payload.model_dump(exclude={"password"}),
        password=payload.password,
    )
    await db.commit()
    configured = await load_email_settings(
        db,
        get_settings(),
        current_user.id,
        credential_key=value.credential_key,
        include_password=True,
    )
    return email_settings_payload(
        configured,
        password_configured=bool(configured.password),
    )


@router.post("/test")
async def test_email_connection(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    value = await load_email_settings(
        db,
        get_settings(),
        current_user.id,
        include_password=True,
    )
    result = await EmailService(value).test_connection()
    return email_result_payload(result)


@router.post("/test-send")
async def send_test_email(
    payload: EmailTestSendRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    value = await load_email_settings(
        db,
        get_settings(),
        current_user.id,
        include_password=True,
    )
    recipient = str(
        payload.recipient or value.default_recipient or ""
    ).strip()
    if not recipient:
        result = EmailResult(
            status="failed",
            error_category="delivery_failed",
            error="Test recipient is not configured",
        )
    else:
        result = await EmailService(value).send_test(recipient)
    return email_result_payload(result)


__all__ = ["router"]
