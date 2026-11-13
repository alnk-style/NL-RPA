"""Authenticated API for local-only App MCP process lifecycle."""

# ruff: noqa: B008

from __future__ import annotations

import asyncio
import os
import re
from typing import Any
from uuid import uuid4

from fastapi import (
    APIRouter,
    Body,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.agent_config import (
    add_app_to_owned_agent,
    remove_app_from_owned_agent,
)
from app.api.dependencies import get_current_user
from app.api.workspace_deps import get_workspace_context
from app.database import get_db
from app.engine.app_mcp.contracts import ToolCallContext
from app.engine.app_mcp.local_manager import (
    LocalAppContext,
    LocalAppError,
    LocalAppInfo,
    LocalAppManager,
)
from app.engine.app_mcp.server import AppMCPServer
from app.engine.installed_app_mcp import (
    create_installed_app_package,
    generated_integrations_by_key,
    load_generated_integration,
    remove_installed_app_package,
)
from app.engine.installed_apps import (
    cached_app_icon,
    cached_installed_app,
    cached_installed_apps,
)
from app.models.agent import DesktopAgent
from app.models.user import User
from app.schemas.agent import InstalledAppResponse, InstalledAppsResponse
from app.security.workspaces import WorkspaceContext

router = APIRouter(prefix="/api/local-apps", tags=["Local Apps"])
_GENERATED_APP_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


class EmptyLifecycleRequest(BaseModel):
    """Reject command, path and bind-address fields sent by clients."""

    model_config = ConfigDict(extra="forbid")


class LocalAppBindingUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    auto_start: bool


class CreateInstalledAppRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: int


def get_local_app_manager(request: Request) -> Any:
    manager = getattr(request.app.state, "local_app_manager", None)
    if manager is None:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "manager_unavailable",
                "message": "Local application manager is unavailable.",
            },
        )
    return manager


def _request_context(
    request: Request,
    current_user: User,
    workspace: WorkspaceContext,
) -> LocalAppContext:
    return LocalAppContext(
        user_id=current_user.id,
        workspace_id=(
            workspace.workspace_id
            if workspace.isolation_enabled
            else None
        ),
        public_base_url=str(request.base_url).rstrip("/"),
    )


async def _require_owned_agent(
    agent_id: int,
    user_id: int,
    db: AsyncSession,
) -> DesktopAgent:
    agent = await db.get(DesktopAgent, agent_id)
    if agent is None or agent.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "agent_not_found",
                "message": "Desktop Agent was not found.",
            },
        )
    return agent


def _installed_app_payload(
    app: Any,
    *,
    base_url: str,
    generated_app_id: str | None = None,
) -> InstalledAppResponse:
    return InstalledAppResponse(
        key=app.key,
        name=app.name,
        executable_path=app.executable_path,
        source=app.source,
        icon_url=f"{base_url}/{app.key}/icon",
        generated_app_id=generated_app_id,
    )


def _error_response(exc: LocalAppError) -> JSONResponse:
    status_code = {
        "invalid_app_id": 400,
        "app_not_found": 404,
        "app_forbidden": 403,
        "server_conflict": 409,
        "unsupported_platform": 409,
        "unsupported_backend": 409,
        "port_exhausted": 503,
        "process_start_failed": 502,
        "startup_timeout": 504,
        "process_exited": 502,
        "termination_failed": 502,
        "health_check_failed": 502,
        "tool_discovery_failed": 502,
        "server_sync_failed": 502,
        "in_process_start_failed": 502,
        "local_app_owner_conflict": 409,
        "local_app_unbound": 409,
        "local_app_binding_corrupt": 409,
        "local_app_binding_required": 400,
        "local_app_owner_required": 403,
        "workspace_required": 400,
        "backend_base_url_required": 400,
        "app_not_running": 409,
        "not_in_process_app": 400,
        "invalid_local_app_token": 401,
        "local_app_unauthenticated": 401,
        "local_app_credential_error": 500,
        "manager_closed": 503,
        "manager_unavailable": 503,
    }.get(exc.code, 500)
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": str(exc.code),
                "message": exc.safe_message,
            }
        },
    )


def _app_payload(app: LocalAppInfo) -> dict[str, Any]:
    return app.to_dict()


def _runtime_payload(runtime: Any) -> dict[str, Any]:
    return {
        "app": _app_payload(runtime.info),
        "server_id": runtime.server_id,
        "discovered_tool_count": runtime.discovered_tool_count,
    }


async def _run_lifecycle(
    operation: Any,
    app_id: str,
    context: LocalAppContext,
) -> JSONResponse | dict[str, Any]:
    try:
        runtime = await operation(app_id, context)
    except LocalAppError as exc:
        return _error_response(exc)
    return _runtime_payload(runtime)


@router.get("")
async def list_local_apps(
    manager: LocalAppManager = Depends(get_local_app_manager),
    current_user: User = Depends(get_current_user),
    workspace: WorkspaceContext = Depends(get_workspace_context),
) -> dict[str, Any]:
    del current_user, workspace
    return {
        "apps": [_app_payload(app) for app in manager.list_apps()],
        "errors": [
            error.to_dict()
            for error in manager.scan_errors
        ],
    }


@router.post("/scan", response_model=None)
async def scan_local_apps(
    manager: LocalAppManager = Depends(get_local_app_manager),
    current_user: User = Depends(get_current_user),
    workspace: WorkspaceContext = Depends(get_workspace_context),
) -> JSONResponse | dict[str, Any]:
    del current_user, workspace
    try:
        await manager.scan()
    except LocalAppError as exc:
        return _error_response(exc)
    result = manager.last_scan
    return {
        "apps": [_app_payload(app) for app in result.apps],
        "added": list(result.added),
        "removed": list(result.removed),
        "errors": [error.to_dict() for error in result.errors],
    }


@router.get(
    "/discoverable-apps",
    response_model=InstalledAppsResponse,
)
async def list_discoverable_apps(
    refresh: bool = False,
    current_user: User = Depends(get_current_user),
) -> InstalledAppsResponse:
    del current_user
    apps = await asyncio.to_thread(cached_installed_apps, refresh)
    integrations = await asyncio.to_thread(generated_integrations_by_key)
    base_url = "/api/local-apps/discoverable-apps"
    return InstalledAppsResponse(
        supported=os.name == "nt",
        apps=[
            _installed_app_payload(
                app,
                base_url=base_url,
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


@router.get("/discoverable-apps/{app_key}/icon")
async def get_discoverable_app_icon(
    app_key: str,
    current_user: User = Depends(get_current_user),
) -> Response:
    del current_user
    if (
        len(app_key) != 64
        or any(character not in "0123456789abcdef" for character in app_key)
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Installed application not found",
        )
    app = await asyncio.to_thread(cached_installed_app, app_key)
    if app is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Installed application not found",
        )
    try:
        icon = await asyncio.to_thread(
            cached_app_icon,
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


@router.post(
    "/discoverable-apps/{app_key}/create",
    response_model=None,
)
async def create_mcp_for_installed_app(
    app_key: str,
    payload: CreateInstalledAppRequest,
    request: Request,
    manager: LocalAppManager = Depends(get_local_app_manager),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    workspace: WorkspaceContext = Depends(get_workspace_context),
) -> JSONResponse | dict[str, Any]:
    app = await asyncio.to_thread(cached_installed_app, app_key)
    if app is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "installed_app_not_found",
                "message": "Installed application was not found.",
            },
        )
    agent = await _require_owned_agent(
        payload.agent_id,
        current_user.id,
        db,
    )
    previously_allowed = any(
        str(item).casefold() == app.executable_path.casefold()
        for item in list(agent.allowed_apps or [])
    )
    generated = None
    try:
        generated = await asyncio.to_thread(
            create_installed_app_package,
            app,
            root=manager.generated_apps_root,
        )
        await add_app_to_owned_agent(
            request=request,
            agent_id=payload.agent_id,
            user_id=current_user.id,
            app_path=app.executable_path,
            db=db,
        )
        await manager.scan()
        context = _request_context(request, current_user, workspace)
        runtime = await manager.start(generated.app_id, context)
    except LocalAppError as exc:
        if generated is not None:
            await asyncio.to_thread(
                remove_installed_app_package,
                generated.app_id,
                root=manager.generated_apps_root,
            )
        if not previously_allowed:
            try:
                await remove_app_from_owned_agent(
                    request=request,
                    agent_id=payload.agent_id,
                    user_id=current_user.id,
                    app_path=app.executable_path,
                    db=db,
                )
            except Exception:
                pass
        return _error_response(exc)
    except Exception as exc:
        if generated is not None:
            await asyncio.to_thread(
                remove_installed_app_package,
                generated.app_id,
                root=manager.generated_apps_root,
            )
        if not previously_allowed:
            try:
                await remove_app_from_owned_agent(
                    request=request,
                    agent_id=payload.agent_id,
                    user_id=current_user.id,
                    app_path=app.executable_path,
                    db=db,
                )
            except Exception:
                pass
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "generated_app_failed",
                "message": "Could not create the generated MCP application.",
            },
        ) from exc
    return {
        "app": _app_payload(runtime.info),
        "server_id": runtime.server_id,
        "discovered_tool_count": runtime.discovered_tool_count,
        "generated": {
            "app_id": generated.app_id,
            "app_dir": str(generated.app_dir),
            "executable_path": generated.executable_path,
            "template": "scoped_desktop_v1",
        },
    }


@router.delete("/generated/{app_id}", response_model=None)
async def delete_generated_mcp_app(
    app_id: str,
    request: Request,
    agent_id: int | None = Query(default=None),
    remove_from_allowlist: bool = Query(default=False),
    manager: LocalAppManager = Depends(get_local_app_manager),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    workspace: WorkspaceContext = Depends(get_workspace_context),
) -> JSONResponse | dict[str, Any]:
    if not _GENERATED_APP_ID_RE.fullmatch(app_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "generated_app_not_found",
                "message": "Generated application was not found.",
            },
        )
    app_dir = manager.generated_apps_root / app_id
    integration = await asyncio.to_thread(
        load_generated_integration,
        app_dir,
    )
    if integration is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "generated_app_not_found",
                "message": "Generated application was not found.",
            },
        )
    context = _request_context(request, current_user, workspace)
    try:
        await manager.stop(app_id, context)
    except LocalAppError:
        pass
    try:
        await manager.remove_binding(app_id, context)
    except LocalAppError:
        pass
    if remove_from_allowlist and agent_id is not None:
        try:
            await remove_app_from_owned_agent(
                request=request,
                agent_id=agent_id,
                user_id=current_user.id,
                app_path=str(integration.get("executable_path") or ""),
                db=db,
            )
        except Exception:
            pass
    removed = await asyncio.to_thread(
        remove_installed_app_package,
        app_id,
        root=manager.generated_apps_root,
    )
    await manager.scan()
    return {
        "deleted": bool(removed),
        "app_id": app_id,
    }


def _bearer_token(request: Request) -> str:
    authorization = request.headers.get("Authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "invalid_local_app_token",
                "message": "Local App MCP token is required.",
            },
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token.strip()


async def _in_process_target(
    request: Request,
    app_id: str,
    manager: Any,
) -> Any:
    client_host = (
        request.client.host
        if request.client is not None
        else ""
    ).lower()
    if client_host not in {
        "127.0.0.1",
        "::1",
        "localhost",
        "testclient",
    }:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "local_access_only",
                "message": "In-process MCP endpoints only accept local requests.",
            },
        )
    try:
        return await manager.authenticate_in_process(
            app_id,
            _bearer_token(request),
        )
    except LocalAppError as exc:
        if exc.code in {
            "invalid_local_app_token",
            "local_app_unauthenticated",
        }:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "code": exc.code,
                    "message": exc.safe_message,
                },
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc
        if exc.code == "app_not_found":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": exc.code,
                    "message": exc.safe_message,
                },
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": exc.code,
                "message": exc.safe_message,
            },
        ) from exc


def _mcp_tool_context(
    request: Request,
    target: Any,
) -> ToolCallContext:
    return ToolCallContext(
        request_id=(
            request.headers.get("x-nl-rpa-request-id")
            or uuid4().hex
        ),
        user_id=target.user_id,
        workspace_id=target.workspace_id,
        workflow_id=_header_int(
            request.headers.get("x-nl-rpa-workflow-id")
        ),
        execution_id=_header_int(
            request.headers.get("x-nl-rpa-execution-id")
        ),
        confirmed=(
            str(
                request.headers.get("x-nl-rpa-confirmed", "")
            ).lower()
            in {"1", "true", "yes", "on"}
        ),
    )


@router.post("/{app_id}/mcp", response_model=None)
async def call_in_process_mcp(
    app_id: str,
    request: Request,
    payload: dict[str, Any] = Body(...),
    manager: LocalAppManager = Depends(get_local_app_manager),
) -> Response | dict[str, Any]:
    target = await _in_process_target(request, app_id, manager)
    result = await AppMCPServer(target.runtime).handle(
        payload,
        _mcp_tool_context(request, target),
    )
    if result is None:
        return Response(status_code=status.HTTP_202_ACCEPTED)
    return result


@router.get("/{app_id}/mcp", response_model=None)
async def read_in_process_mcp_info(
    app_id: str,
    request: Request,
    manager: LocalAppManager = Depends(get_local_app_manager),
) -> Response | dict[str, Any]:
    target = await _in_process_target(request, app_id, manager)
    app = await manager.status(app_id)
    return {
        "app_id": target.app_id,
        "transport": "streamable-http",
        "runtime": "in_process",
        "url": app.url,
        "server": {
            "name": app.app_id,
            "version": app.version,
        },
    }


@router.get("/{app_id}/binding", response_model=None)
async def read_local_app_binding(
    app_id: str,
    request: Request,
    manager: LocalAppManager = Depends(get_local_app_manager),
    current_user: User = Depends(get_current_user),
    workspace: WorkspaceContext = Depends(get_workspace_context),
) -> JSONResponse | dict[str, Any]:
    context = _request_context(request, current_user, workspace)
    try:
        binding = await manager.binding_info(app_id, context)
    except LocalAppError as exc:
        return _error_response(exc)
    return binding


@router.patch("/{app_id}/binding", response_model=None)
async def update_local_app_binding(
    app_id: str,
    payload: LocalAppBindingUpdateRequest,
    request: Request,
    manager: LocalAppManager = Depends(get_local_app_manager),
    current_user: User = Depends(get_current_user),
    workspace: WorkspaceContext = Depends(get_workspace_context),
) -> JSONResponse | dict[str, Any]:
    context = _request_context(request, current_user, workspace)
    try:
        binding = await manager.update_binding(
            app_id,
            auto_start=payload.auto_start,
            context=context,
        )
    except LocalAppError as exc:
        return _error_response(exc)
    return binding


@router.delete("/{app_id}/binding", response_model=None)
async def delete_local_app_binding(
    app_id: str,
    request: Request,
    manager: LocalAppManager = Depends(get_local_app_manager),
    current_user: User = Depends(get_current_user),
    workspace: WorkspaceContext = Depends(get_workspace_context),
) -> JSONResponse | dict[str, Any]:
    context = _request_context(request, current_user, workspace)
    try:
        binding = await manager.remove_binding(app_id, context)
    except LocalAppError as exc:
        return _error_response(exc)
    return binding


@router.get("/{app_id}", response_model=None)
async def get_local_app(
    app_id: str,
    request: Request,
    manager: LocalAppManager = Depends(get_local_app_manager),
    current_user: User = Depends(get_current_user),
    workspace: WorkspaceContext = Depends(get_workspace_context),
) -> JSONResponse | dict[str, Any]:
    context = _request_context(request, current_user, workspace)
    try:
        app = await manager.status(app_id, context)
    except LocalAppError as exc:
        return _error_response(exc)
    return {"app": _app_payload(app)}


@router.post("/{app_id}/start", response_model=None)
async def start_local_app(
    app_id: str,
    request: Request,
    payload: EmptyLifecycleRequest | None = Body(default=None),
    manager: LocalAppManager = Depends(get_local_app_manager),
    current_user: User = Depends(get_current_user),
    workspace: WorkspaceContext = Depends(get_workspace_context),
) -> JSONResponse | dict[str, Any]:
    del payload
    return await _run_lifecycle(
        manager.start,
        app_id,
        _request_context(request, current_user, workspace),
    )


@router.post("/{app_id}/stop", response_model=None)
async def stop_local_app(
    app_id: str,
    request: Request,
    payload: EmptyLifecycleRequest | None = Body(default=None),
    manager: LocalAppManager = Depends(get_local_app_manager),
    current_user: User = Depends(get_current_user),
    workspace: WorkspaceContext = Depends(get_workspace_context),
) -> JSONResponse | dict[str, Any]:
    del payload
    context = _request_context(request, current_user, workspace)
    try:
        stopped = await manager.stop(app_id, context)
        app = await manager.status(app_id, context)
    except LocalAppError as exc:
        return _error_response(exc)
    return {
        "stopped": bool(stopped),
        "app": _app_payload(app),
    }


@router.post("/{app_id}/restart", response_model=None)
async def restart_local_app(
    app_id: str,
    request: Request,
    payload: EmptyLifecycleRequest | None = Body(default=None),
    manager: LocalAppManager = Depends(get_local_app_manager),
    current_user: User = Depends(get_current_user),
    workspace: WorkspaceContext = Depends(get_workspace_context),
) -> JSONResponse | dict[str, Any]:
    del payload
    return await _run_lifecycle(
        manager.restart,
        app_id,
        _request_context(request, current_user, workspace),
    )


@router.get("/{app_id}/logs", response_model=None)
async def read_local_app_logs(
    app_id: str,
    request: Request,
    lines: int = Query(default=200, ge=1, le=500),
    manager: LocalAppManager = Depends(get_local_app_manager),
    current_user: User = Depends(get_current_user),
    workspace: WorkspaceContext = Depends(get_workspace_context),
) -> JSONResponse | dict[str, Any]:
    context = _request_context(request, current_user, workspace)
    try:
        logs = await manager.logs(app_id, lines, context)
    except LocalAppError as exc:
        return _error_response(exc)
    return {
        "app_id": app_id,
        "lines": lines,
        "logs": logs,
    }

def _header_int(value: str | None) -> int | None:
    if value is None or not str(value).strip():
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


__all__ = ["get_local_app_manager", "router"]
