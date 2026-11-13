from __future__ import annotations

import asyncio
import inspect
import logging
import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.database import get_db
from app.engine.installed_apps import (
    InstalledApp,
    cached_app_icon,
    cached_installed_apps,
)
from app.engine.installed_app_mcp import generated_integrations_by_key
from app.models.agent import DesktopAgent
from app.models.user import User
from app.schemas.agent import (
    AgentConfigResponse,
    AgentConfigUpdate,
    InstalledAppResponse,
    InstalledAppsResponse,
)

router = APIRouter(
    prefix="/api/agents/{agent_id}/config",
    tags=["Agent配置"],
)
logger = logging.getLogger(__name__)

class AddAppRequest(BaseModel):
    app: str


class AddDirectoryRequest(BaseModel):
    dir: str


def _cached_installed_apps(
    refresh: bool = False,
) -> tuple[InstalledApp, ...]:
    return cached_installed_apps(refresh)


def _cached_app_icon(
    app_key: str,
    executable_path: str,
) -> bytes:
    return cached_app_icon(app_key, executable_path)


async def _get_owned_agent(
    agent_id: int,
    user_id: int,
    db: AsyncSession,
) -> DesktopAgent:
    result = await db.execute(
        select(DesktopAgent).where(
            DesktopAgent.id == agent_id,
            DesktopAgent.user_id == user_id,
        )
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found",
        )
    return agent


def _normalize_entries(values: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = value.strip()
        if not item:
            continue
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(item)
    return normalized


def _response(agent: DesktopAgent) -> AgentConfigResponse:
    return AgentConfigResponse(
        allowed_apps=list(agent.allowed_apps or []),
        allowed_doc_dirs=list(agent.allowed_doc_dirs or []),
    )


def _connection_managers(request: Request) -> list[Any]:
    managers: list[Any] = []
    for name in (
        "connection_manager",
        "agent_hub",
        "agent_manager",
        "websocket_manager",
    ):
        manager = getattr(request.app.state, name, None)
        if manager is not None and all(manager is not current for current in managers):
            managers.append(manager)
    return managers


async def _send_config_update(
    request: Request,
    agent: DesktopAgent,
    config: AgentConfigResponse,
) -> bool:
    payload = {
        "type": "config.update",
        "agent_id": agent.id,
        "config": config.model_dump(),
    }
    for manager in _connection_managers(request):
        is_online = getattr(manager, "is_online", None)
        if callable(is_online) and not is_online(agent.id):
            continue
        if not callable(is_online) and agent.status != "online":
            continue

        get_connection = getattr(manager, "get", None)
        websocket = get_connection(agent.id) if callable(get_connection) else None
        if websocket is not None:
            send_json = getattr(websocket, "send_json", None)
            if callable(send_json):
                result = send_json(payload)
                if inspect.isawaitable(result):
                    await result
                return True

        for method_name in ("send_config", "send", "send_task"):
            method = getattr(manager, method_name, None)
            if not callable(method):
                continue
            result = method(agent.id, payload)
            if inspect.isawaitable(result):
                await result
            return True
    return False


async def _update_config(
    request: Request,
    agent: DesktopAgent,
    db: AsyncSession,
    *,
    allowed_apps: list[str],
    allowed_doc_dirs: list[str],
) -> AgentConfigResponse:
    agent.allowed_apps = _normalize_entries(allowed_apps)
    agent.allowed_doc_dirs = _normalize_entries(allowed_doc_dirs)
    await db.commit()
    await db.refresh(agent)
    config = _response(agent)

    try:
        await _send_config_update(request, agent, config)
    except Exception:
        logger.warning(
            "Failed to send config.update to Agent %s",
            agent.id,
            exc_info=True,
        )
    return config


@router.get("", response_model=AgentConfigResponse)
@router.get(
    "/",
    response_model=AgentConfigResponse,
    include_in_schema=False,
)
async def get_agent_config(
    agent_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AgentConfigResponse:
    agent = await _get_owned_agent(agent_id, current_user.id, db)
    return _response(agent)


@router.get(
    "/installed-apps",
    response_model=InstalledAppsResponse,
)
async def list_installed_apps(
    agent_id: int,
    refresh: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> InstalledAppsResponse:
    await _get_owned_agent(agent_id, current_user.id, db)
    apps = await asyncio.to_thread(_cached_installed_apps, refresh)
    integrations = await asyncio.to_thread(
        generated_integrations_by_key
    )
    base_url = (
        f"/api/agents/{agent_id}/config/installed-apps"
    )
    return InstalledAppsResponse(
        supported=os.name == "nt",
        apps=[
            InstalledAppResponse(
                key=app.key,
                name=app.name,
                executable_path=app.executable_path,
                source=app.source,
                icon_url=f"{base_url}/{app.key}/icon",
                generated_app_id=(
                    str(integrations[app.key].get("app_id") or "")
                    or None
                    if app.key in integrations
                    else None
                ),
            )
            for app in apps
        ],
    )


@router.get("/installed-apps/{app_key}/icon")
async def get_installed_app_icon(
    agent_id: int,
    app_key: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    await _get_owned_agent(agent_id, current_user.id, db)
    if (
        len(app_key) != 64
        or any(character not in "0123456789abcdef" for character in app_key)
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Installed application not found",
        )
    apps = await asyncio.to_thread(_cached_installed_apps, False)
    app = next((item for item in apps if item.key == app_key), None)
    if app is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Installed application not found",
        )
    try:
        icon = await asyncio.to_thread(
            _cached_app_icon,
            app.key,
            app.executable_path,
        )
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Application icon is unavailable",
        ) from exc
    return Response(
        content=icon,
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@router.put("", response_model=AgentConfigResponse)
@router.put(
    "/",
    response_model=AgentConfigResponse,
    include_in_schema=False,
)
async def update_agent_config(
    agent_id: int,
    payload: AgentConfigUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AgentConfigResponse:
    agent = await _get_owned_agent(agent_id, current_user.id, db)
    return await _update_config(
        request,
        agent,
        db,
        allowed_apps=payload.allowed_apps,
        allowed_doc_dirs=payload.allowed_doc_dirs,
    )


@router.post("/apps", response_model=AgentConfigResponse)
async def add_allowed_app(
    agent_id: int,
    payload: AddAppRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AgentConfigResponse:
    agent = await _get_owned_agent(agent_id, current_user.id, db)
    allowed_apps = list(agent.allowed_apps or [])
    allowed_apps.append(payload.app)
    return await _update_config(
        request,
        agent,
        db,
        allowed_apps=allowed_apps,
        allowed_doc_dirs=list(agent.allowed_doc_dirs or []),
    )


@router.delete("/apps/{app_path:path}", response_model=AgentConfigResponse)
async def remove_allowed_app(
    agent_id: int,
    app_path: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AgentConfigResponse:
    agent = await _get_owned_agent(agent_id, current_user.id, db)
    target = app_path.casefold()
    allowed_apps = [
        item
        for item in list(agent.allowed_apps or [])
        if str(item).casefold() != target
    ]
    return await _update_config(
        request,
        agent,
        db,
        allowed_apps=allowed_apps,
        allowed_doc_dirs=list(agent.allowed_doc_dirs or []),
    )


@router.post("/dirs", response_model=AgentConfigResponse)
async def add_allowed_directory(
    agent_id: int,
    payload: AddDirectoryRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AgentConfigResponse:
    agent = await _get_owned_agent(agent_id, current_user.id, db)
    allowed_doc_dirs = list(agent.allowed_doc_dirs or [])
    allowed_doc_dirs.append(payload.dir)
    return await _update_config(
        request,
        agent,
        db,
        allowed_apps=list(agent.allowed_apps or []),
        allowed_doc_dirs=allowed_doc_dirs,
    )


@router.delete("/dirs/{dir_path:path}", response_model=AgentConfigResponse)
async def remove_allowed_directory(
    agent_id: int,
    dir_path: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AgentConfigResponse:
    agent = await _get_owned_agent(agent_id, current_user.id, db)
    target = dir_path.casefold()
    allowed_doc_dirs = [
        item
        for item in list(agent.allowed_doc_dirs or [])
        if str(item).casefold() != target
    ]
    return await _update_config(
        request,
        agent,
        db,
        allowed_apps=list(agent.allowed_apps or []),
        allowed_doc_dirs=allowed_doc_dirs,
    )


async def add_app_to_owned_agent(
    *,
    request: Request,
    agent_id: int,
    user_id: int,
    app_path: str,
    db: AsyncSession,
) -> AgentConfigResponse:
    agent = await _get_owned_agent(agent_id, user_id, db)
    allowed_apps = list(agent.allowed_apps or [])
    allowed_apps.append(app_path)
    return await _update_config(
        request,
        agent,
        db,
        allowed_apps=allowed_apps,
        allowed_doc_dirs=list(agent.allowed_doc_dirs or []),
    )


async def remove_app_from_owned_agent(
    *,
    request: Request,
    agent_id: int,
    user_id: int,
    app_path: str,
    db: AsyncSession,
) -> AgentConfigResponse:
    agent = await _get_owned_agent(agent_id, user_id, db)
    target = app_path.casefold()
    allowed_apps = [
        item
        for item in list(agent.allowed_apps or [])
        if str(item).casefold() != target
    ]
    return await _update_config(
        request,
        agent,
        db,
        allowed_apps=allowed_apps,
        allowed_doc_dirs=list(agent.allowed_doc_dirs or []),
    )


__all__ = [
    "AddAppRequest",
    "AddDirectoryRequest",
    "add_app_to_owned_agent",
    "remove_app_from_owned_agent",
    "router",
]
