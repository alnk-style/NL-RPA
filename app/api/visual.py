from __future__ import annotations

from io import BytesIO
from typing import Any

from fastapi import APIRouter, Depends, Request
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.config import get_settings
from app.database import get_db
from app.models.credential import Credential
from app.models.user import User
from app.visual.local_ocr import LocalOCRProvider
from app.visual.provider import (
    DisabledVisualProvider,
    VisualLocateRequest,
    VisualProviderUnavailable,
)
from app.visual.remote_vlm import RemoteVLMProvider
from app.visual.settings import (
    VISUAL_CREDENTIAL_KEY,
    VisualSettings,
    VisualSettingsUpdate,
    load_visual_settings,
    save_visual_settings,
    visual_settings_payload,
)

router = APIRouter(
    prefix="/api/settings/visual",
    tags=["visual-settings"],
)


async def _api_key_configured(
    db: AsyncSession,
    user_id: int,
) -> bool:
    return (
        await db.execute(
            select(Credential.id).where(
                Credential.user_id == int(user_id),
                Credential.scope == "visual",
                Credential.key == VISUAL_CREDENTIAL_KEY,
            )
        )
    ).scalar_one_or_none() is not None


@router.get("")
async def get_visual_settings(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    settings = await load_visual_settings(
        db,
        get_settings(),
        int(current_user.id),
    )
    return visual_settings_payload(
        settings,
        api_key_configured=await _api_key_configured(
            db,
            int(current_user.id),
        ),
    )


@router.put("")
async def update_visual_settings(
    payload: VisualSettingsUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    settings = await save_visual_settings(
        db,
        get_settings(),
        int(current_user.id),
        payload,
    )
    await db.commit()
    return visual_settings_payload(
        settings,
        api_key_configured=await _api_key_configured(
            db,
            int(current_user.id),
        ),
    )


@router.post("/test")
async def test_visual_provider(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    settings = await load_visual_settings(
        db,
        get_settings(),
        int(current_user.id),
        include_api_key=True,
    )
    if not settings.enabled or settings.provider == "disabled":
        return {
            "status": "disabled",
            "provider": "disabled",
            "message": "视觉兜底已关闭。",
        }
    provider = _build_provider(request, settings)
    if provider is None:
        return {
            "status": "unavailable",
            "provider": settings.provider,
            "message": "视觉 Provider 配置不完整或依赖不可用。",
        }
    image = Image.new("RGB", (32, 32), "white")
    output = BytesIO()
    image.save(output, format="PNG")
    test_request = VisualLocateRequest(
        step_id=1,
        action="click",
        target_description="provider self test",
        screenshot=output.getvalue(),
        image_width=32,
        image_height=32,
        viewport={"width": 32, "height": 32},
        dom_candidates=[],
        safe_visible_text="",
        allowed_actions=["click"],
        max_candidates=settings.max_candidates,
    )
    try:
        result = await provider.locate(test_request)
    except VisualProviderUnavailable as exc:
        return {
            "status": "failed",
            "provider": settings.provider,
            "message": str(exc),
        }
    return {
        "status": "ok",
        "provider": result.provider,
        "model": result.model,
        "candidate_count": len(result.candidates),
    }


def _build_provider(
    request: Request,
    settings: VisualSettings,
) -> Any | None:
    if settings.provider == "disabled":
        return DisabledVisualProvider()
    if settings.provider == "local_ocr":
        factory = getattr(
            request.app.state,
            "local_ocr_backend_factory",
            None,
        )
        backend = factory() if callable(factory) else None
        return LocalOCRProvider(ocr_backend=backend)
    if settings.provider == "remote_vlm":
        try:
            return RemoteVLMProvider(
                endpoint=settings.endpoint,
                model=settings.model,
                api_key=str(settings.api_key or ""),
            )
        except VisualProviderUnavailable:
            return None
    return None


__all__ = ["router"]
