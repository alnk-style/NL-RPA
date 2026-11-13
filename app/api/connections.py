from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.config import get_settings
from app.database import get_db
from app.engine.artifacts import parse_export_dirs
from app.engine.browser_login import BrowserLoginError
from app.models.app_setting import AppSetting
from app.models.browser_profile import BrowserProfile
from app.models.execution import ExecutionLog
from app.models.human_task import HumanTask
from app.models.user import User
from app.models.workflow import Workflow
from app.schemas.settings import (
    BrowserProfileCreate,
    BrowserProfileLoginRequest,
    BrowserProfileLoginResponse,
    BrowserProfileResponse,
    ExportDirectoriesResponse,
    ExportDirectoriesUpdate,
)

router = APIRouter(tags=["connections"])

EXPORT_DIRECTORIES_KEY = "export_directories"


@router.get(
    "/api/browser-profiles",
    response_model=list[BrowserProfileResponse],
)
async def list_browser_profiles(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[BrowserProfile]:
    result = await db.execute(
        select(BrowserProfile)
        .where(BrowserProfile.user_id == current_user.id)
        .order_by(BrowserProfile.created_at, BrowserProfile.id)
    )
    return list(result.scalars().all())


@router.post(
    "/api/browser-profiles",
    response_model=BrowserProfileResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_browser_profile(
    payload: BrowserProfileCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> BrowserProfile:
    name = payload.name.strip()
    if not name:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Browser profile name is required",
        )
    profile = BrowserProfile(
        user_id=current_user.id,
        name=name,
        user_data_dir=(
            str(Path(payload.user_data_dir).resolve())
            if payload.user_data_dir
            else None
        ),
        profile_directory=payload.profile_directory.strip() or "Default",
        login_state="unknown",
    )
    db.add(profile)
    await db.commit()
    await db.refresh(profile)
    return profile


@router.post(
    "/api/browser-profiles/{profile_id}/login",
    response_model=BrowserProfileLoginResponse,
)
async def open_browser_profile_login(
    profile_id: int,
    payload: BrowserProfileLoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> BrowserProfileLoginResponse:
    profile = (
        await db.execute(
            select(BrowserProfile).where(
                BrowserProfile.id == int(profile_id),
                BrowserProfile.user_id == current_user.id,
            )
        )
    ).scalar_one_or_none()
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Browser profile not found",
        )
    row = (
        await db.execute(
            select(HumanTask)
            .join(
                ExecutionLog,
                HumanTask.execution_id == ExecutionLog.id,
            )
            .join(Workflow, ExecutionLog.workflow_id == Workflow.id)
            .where(
                HumanTask.id == int(payload.human_task_id),
                Workflow.user_id == current_user.id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Human task not found",
        )
    if row.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only pending human tasks can open a login window",
        )
    task_payload = dict(row.payload or {})
    task_payload["browser_profile_id"] = int(profile.id)
    row.payload = task_payload
    profile.login_state = "login_required"
    await db.commit()

    sessions = getattr(
        request.app.state,
        "browser_login_sessions",
        None,
    )
    if sessions is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Browser login sessions are not ready",
        )
    try:
        opened = await sessions.open_login(
            row,
            profile,
            url=payload.url,
        )
    except BrowserLoginError as exc:
        raise HTTPException(
            status_code=(
                status.HTTP_503_SERVICE_UNAVAILABLE
                if exc.code == "browser_unavailable"
                else status.HTTP_422_UNPROCESSABLE_ENTITY
            ),
            detail={
                "code": exc.code,
                "reason": exc.reason,
            },
        ) from exc
    return BrowserProfileLoginResponse.model_validate(opened)


@router.get(
    "/api/settings/export-directories",
    response_model=ExportDirectoriesResponse,
)
async def get_export_directories(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ExportDirectoriesResponse:
    setting = await _get_setting(
        db,
        current_user.id,
        EXPORT_DIRECTORIES_KEY,
    )
    if setting is not None and isinstance(setting.value, dict):
        values = setting.value.get("directories")
        if isinstance(values, list):
            return ExportDirectoriesResponse(
                directories=[
                    str(value) for value in values if str(value).strip()
                ]
            )
    return ExportDirectoriesResponse(
        directories=[
            str(path)
            for path in parse_export_dirs(get_settings().WEB_EXPORT_DIRS)
        ]
    )


@router.put(
    "/api/settings/export-directories",
    response_model=ExportDirectoriesResponse,
)
async def update_export_directories(
    payload: ExportDirectoriesUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ExportDirectoriesResponse:
    normalized: list[str] = []
    for raw in payload.directories:
        text = raw.strip()
        if not text:
            continue
        path = Path(text).expanduser()
        if not path.is_absolute():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Export directory must be absolute: {text}",
            )
        resolved = path.resolve()
        if not resolved.is_dir():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Export directory does not exist: {resolved}",
            )
        if not os.access(resolved, os.W_OK):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Export directory is not writable: {resolved}",
            )
        value = str(resolved)
        if value not in normalized:
            normalized.append(value)

    setting = await _get_setting(
        db,
        current_user.id,
        EXPORT_DIRECTORIES_KEY,
    )
    if setting is None:
        setting = AppSetting(
            user_id=current_user.id,
            key=EXPORT_DIRECTORIES_KEY,
            value={"directories": normalized},
        )
        db.add(setting)
    else:
        setting.value = {"directories": normalized}
    await db.commit()
    return ExportDirectoriesResponse(directories=normalized)


async def _get_setting(
    db: AsyncSession,
    user_id: int,
    key: str,
) -> AppSetting | None:
    return (
        await db.execute(
            select(AppSetting).where(
                AppSetting.user_id == user_id,
                AppSetting.key == key,
            )
        )
    ).scalar_one_or_none()


__all__ = [
    "EXPORT_DIRECTORIES_KEY",
    "router",
]
