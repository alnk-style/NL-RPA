from __future__ import annotations

import asyncio
import inspect
import logging
import mimetypes
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.config import get_settings
from app.database import get_db
from app.engine.artifacts import deliver_execution_artifacts
from app.engine.execution_manager import (
    ExecutionManager,
    ExecutionStateError,
)
from app.models.agent import DesktopAgent
from app.models.delivery_record import DeliveryRecord
from app.models.execution import ExecutionLog
from app.models.user import User
from app.models.workflow import Workflow
from app.models.workflow_version import WorkflowVersion
from app.schemas.delivery import DeliveryRecordResponse
from app.schemas.workflow import (
    ExecutionCancelRequest,
    ExecutionDetailResponse,
    ExecutionListResponse,
    ExecutionResponse,
)
from app.services.execution_versions import ExecutionVersionService
from app.services.execution_events import list_execution_events
from app.services.hosts import active_task_counts
from app.services.workflow_versions import WorkflowVersionError
from app.security.workspaces import (
    WorkspaceContext,
    apply_workspace_scope,
    resolve_workspace,
    workspace_isolation_enabled,
)
from app.api.workspace_deps import get_workspace_context
from runtime_paths import stable_logs_dir

router = APIRouter(prefix="/api/executions", tags=["执行与日志"])

LOGS_DIR = (
    stable_logs_dir()
    if os.getenv("NL_RPA_DESKTOP_MODE") == "1"
    else Path("logs")
)
logger = logging.getLogger(__name__)
_TERMINAL_STATUSES = {
    "success",
    "failed",
    "error",
    "completed",
    "cancelled",
}


def _running_status_error(current_status: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=(
            "Workflow cannot be executed while its status is "
            f"'{current_status}'"
        ),
    )


def _get_execution_manager(request: Request) -> ExecutionManager | None:
    manager = getattr(request.app.state, "execution_manager", None)
    if manager is None:
        return None
    return manager  # type: ignore[return-value]


def require_auth_pause_enabled() -> None:
    if not get_settings().AUTH_REQUIRED_PAUSE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "登录暂停恢复未启用，请先在设置中开启 "
                "AUTH_REQUIRED_PAUSE。"
            ),
        )


async def _get_owned_execution(
    execution_id: int,
    db: AsyncSession,
    current_user: User,
    workspace_id: int | None = None,
) -> ExecutionLog:
    statement = (
        select(ExecutionLog)
        .join(Workflow, ExecutionLog.workflow_id == Workflow.id)
        .where(
            ExecutionLog.id == execution_id,
            Workflow.user_id == current_user.id,
        )
    )
    if workspace_id is not None:
        statement = statement.where(
            Workflow.workspace_id == workspace_id
        )
    result = await db.execute(statement)
    execution_log = result.scalar_one_or_none()
    if execution_log is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Execution not found",
        )
    return execution_log


def _execution_summary(
    log: ExecutionLog,
    result: Any | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": log.id,
        "execution_id": log.id,
        "workflow_id": log.workflow_id,
        "status": log.status,
        "started_at": log.started_at,
        "finished_at": log.finished_at,
        "error_message": log.error_message,
        "screenshots": log.screenshots or [],
        "artifacts": log.artifacts or [],
        "step_logs": log.step_logs or [],
        "workflow_version_id": log.workflow_version_id,
        "execution_policy_snapshot": log.execution_policy_snapshot,
        "host_id": log.host_id,
        "host_environment": log.host_environment,
        "host_bound": bool(log.host_bound),
        "host_affinity_configured": bool(
            log.host_affinity_hash
        ),
        "browser_profile_id": log.browser_profile_id,
        "human_task_id": log.human_task_id,
        "trigger_type": log.trigger_type,
    }
    if result is not None:
        payload.update(
            {
                "success": result.success,
                "output": result.output,
                "error": result.error,
                "duration_seconds": result.duration_seconds,
            }
        )
    return payload


def _execution_response(log: ExecutionLog) -> ExecutionResponse:
    payload = _execution_summary(log)
    payload.pop("execution_id", None)
    return ExecutionResponse(**payload)


async def _host_runtime_fields(
    request: Request,
    db: AsyncSession,
    log: ExecutionLog,
) -> dict[str, Any]:
    if log.host_id is None:
        return {}
    agent = await db.get(DesktopAgent, int(log.host_id))
    if agent is None:
        return {
            "host_name": None,
            "host_status": "offline",
            "host_capabilities": [],
            "host_reason": "执行主机记录已不存在",
        }
    manager = getattr(request.app.state, "connection_manager", None)
    checker = getattr(manager, "is_online", None)
    online = False
    if callable(checker):
        try:
            online = bool(checker(int(agent.id)))
        except Exception:
            online = False
    counts = await active_task_counts(db, [int(agent.id)])
    active = counts.get(int(agent.id), 0)
    policy = (
        log.execution_policy_snapshot
        if isinstance(log.execution_policy_snapshot, dict)
        else {}
    )
    reason_parts = []
    if log.host_reason:
        reason_parts.append(log.host_reason)
    if policy.get("host_policy"):
        reason_parts.append(
            f"host_policy={policy['host_policy']}"
        )
    if policy.get("mode"):
        reason_parts.append(f"mode={policy['mode']}")
    return {
        "host_name": agent.name,
        "host_status": (
            "busy"
            if online and active > 0
            else "online"
            if online
            else "offline"
        ),
        "host_capabilities": list(agent.capabilities or []),
        "host_reason": "; ".join(reason_parts) or None,
    }


async def _notify_execution(
    request: Request,
    execution_log: ExecutionLog,
    workflow: Workflow,
) -> None:
    notifier = getattr(request.app.state, "notifier", None)
    if notifier is None:
        return

    notify = getattr(notifier, "notify", None)
    if not callable(notify):
        return

    try:
        result = notify(execution_log, workflow)
        if inspect.isawaitable(result):
            await result
    except Exception:
        logger.exception(
            "Failed to notify execution %s for workflow %s",
            execution_log.id,
            workflow.id,
        )


def _screenshot_candidate(
    screenshot_value: str,
    extra_roots: list[Path] | None = None,
) -> Path | None:
    logs_root = LOGS_DIR.resolve()
    roots: list[Path] = [logs_root]
    for root in extra_roots or []:
        try:
            resolved_root = Path(root).expanduser().resolve()
        except OSError:
            continue
        if resolved_root not in roots:
            roots.append(resolved_root)

    raw_path = Path(screenshot_value)
    candidates: list[Path] = []

    if raw_path.is_absolute():
        candidates.append(raw_path)
    else:
        candidates.append(Path.cwd() / raw_path)
        if not raw_path.parts or raw_path.parts[0].lower() != "logs":
            candidates.append(logs_root / raw_path)

    for candidate in candidates:
        resolved = candidate.resolve()
        if not resolved.is_file():
            continue
        if any(
            resolved == root or root in resolved.parents
            for root in roots
        ):
            return resolved
    return None


async def _allowed_screenshot_roots(
    db: AsyncSession,
    execution_log: ExecutionLog,
) -> list[Path]:
    """Agent 白名单里的目录同样可以存放截图。"""

    workflow_id = getattr(execution_log, "workflow_id", None)
    if workflow_id is None:
        return []
    workflow = await db.get(Workflow, workflow_id)
    if workflow is None or workflow.user_id is None:
        return []

    result = await db.execute(
        select(DesktopAgent).where(
            DesktopAgent.user_id == workflow.user_id
        )
    )
    roots: list[Path] = []
    for agent in result.scalars().all():
        for entry in agent.allowed_doc_dirs or []:
            text = str(entry).strip()
            if text:
                roots.append(Path(text))
    return roots


@router.post("/{workflow_id}/run")
async def run_workflow(
    workflow_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(get_workspace_context),
) -> dict[str, Any]:
    statement = select(Workflow).where(
        Workflow.id == workflow_id,
        Workflow.user_id == current_user.id,
    )
    if context.isolation_enabled:
        statement = statement.where(
            Workflow.workspace_id == context.workspace_id
        )
    workflow = (await db.execute(statement)).scalar_one_or_none()
    if workflow is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow not found",
        )
    if workflow.status not in {"active", "published"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Workflow has no published active version",
        )
    if workflow.active_version_id is None:
        raise _running_status_error(workflow.status)

    engine = getattr(request.app.state, "engine", None)
    if engine is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Execution engine is not ready",
        )

    execution_manager = _get_execution_manager(request)
    if execution_manager is not None:
        workflow_id = workflow.id
        workspace_id = workflow.workspace_id
        await db.rollback()
        try:
            execution_log = await execution_manager.start_workflow(
                workflow_id,
                workspace_id=workspace_id,
            )
        except ExecutionStateError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc
        return _execution_summary(execution_log)

    try:
        snapshot = await ExecutionVersionService(db).resolve_active(
            workflow
        )
    except WorkflowVersionError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    execution_log = ExecutionLog(
        workflow_id=workflow.id,
        workspace_id=workflow.workspace_id,
        workflow_version_id=snapshot.version_id,
        execution_policy_snapshot=snapshot.policy.model_dump(mode="json"),
        status="running",
    )
    db.add(execution_log)
    await db.commit()
    await db.refresh(execution_log)

    try:
        result = await engine.execute_with_debug(
            snapshot.workflow_view(workflow),
            {"execution_log_id": execution_log.id},
        )
    except Exception as exc:
        execution_log.status = "failed"
        execution_log.error_message = str(exc) or exc.__class__.__name__
        execution_log.finished_at = datetime.utcnow()
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=execution_log.error_message,
        ) from exc

    execution_log.status = "success" if result.success else "failed"
    execution_log.error_message = result.error
    execution_log.screenshots = list(result.screenshots or [])
    execution_log.step_logs = list(result.step_logs or [])
    execution_log.artifacts = await deliver_execution_artifacts(
        db,
        workflow,
        getattr(result, "artifacts", None),
        get_settings().WEB_EXPORT_DIRS,
    )
    execution_log.finished_at = datetime.utcnow()
    await db.commit()
    await db.refresh(execution_log)
    await _notify_execution(request, execution_log, workflow)

    return _execution_summary(execution_log, result)


@router.post("/{id}/pause", response_model=ExecutionResponse)
async def pause_execution(
    id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(get_workspace_context),
) -> ExecutionResponse:
    execution_log = await _get_owned_execution(
        id,
        db,
        current_user,
        context.workspace_id if context.isolation_enabled else None,
    )
    execution_manager = _get_execution_manager(request)

    if execution_manager is not None:
        await db.rollback()
        try:
            execution_log = await execution_manager.pause(id)
        except ExecutionStateError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc
        return _execution_response(execution_log)

    if execution_log.status == "paused":
        return _execution_response(execution_log)
    if execution_log.status not in {"pending", "running"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only running executions can be paused",
        )

    execution_log.status = "paused"
    execution_log.finished_at = datetime.utcnow()
    if not execution_log.error_message:
        execution_log.error_message = "执行已暂停。"
    await db.commit()
    await db.refresh(execution_log)
    return _execution_response(execution_log)


@router.post("/{id}/resume", response_model=ExecutionResponse)
async def resume_execution(
    id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(get_workspace_context),
) -> ExecutionResponse:
    await _get_owned_execution(
        id,
        db,
        current_user,
        context.workspace_id if context.isolation_enabled else None,
    )
    execution_manager = _get_execution_manager(request)
    if execution_manager is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Execution manager is not ready",
        )

    await db.rollback()
    try:
        execution_log = await execution_manager.resume(id)
    except ExecutionStateError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    return _execution_response(execution_log)


@router.post(
    "/{id}/resume-after-login",
    dependencies=[Depends(require_auth_pause_enabled)],
)
async def resume_after_login(
    id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(get_workspace_context),
) -> dict[str, Any]:
    """人工完成登录后，从检查点继续执行。"""

    await _get_owned_execution(
        id,
        db,
        current_user,
        context.workspace_id if context.isolation_enabled else None,
    )
    execution_manager = _get_execution_manager(request)
    if execution_manager is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Execution manager is not ready",
        )

    await db.rollback()
    try:
        execution_log = await execution_manager.resume_after_login(id)
    except ExecutionStateError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    return _execution_summary(execution_log)


@router.post("/{id}/cancel", response_model=ExecutionResponse)
async def cancel_execution(
    id: int,
    payload: ExecutionCancelRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(get_workspace_context),
) -> ExecutionResponse:
    await _get_owned_execution(
        id,
        db,
        current_user,
        context.workspace_id if context.isolation_enabled else None,
    )
    execution_manager = _get_execution_manager(request)
    if execution_manager is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Execution manager is not ready",
        )
    await db.rollback()
    try:
        execution_log = await execution_manager.cancel(
            id,
            payload.reason,
        )
    except ExecutionStateError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    if execution_log.human_task_id is not None:
        human_task_manager = getattr(
            request.app.state,
            "human_task_manager",
            None,
        )
        session_manager = getattr(
            human_task_manager,
            "session_manager",
            None,
        )
        close_task = getattr(session_manager, "close_task", None)
        if callable(close_task):
            try:
                await close_task(int(execution_log.human_task_id))
            except Exception:
                logger.debug(
                    "Failed to close human task browser session",
                    exc_info=True,
                )
    return _execution_response(execution_log)


@router.get("", response_model=list[ExecutionListResponse])
async def list_executions(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(get_workspace_context),
) -> list[ExecutionListResponse]:
    statement = (
        select(ExecutionLog, Workflow.name, WorkflowVersion.version)
        .join(Workflow, ExecutionLog.workflow_id == Workflow.id)
        .outerjoin(
            WorkflowVersion,
            ExecutionLog.workflow_version_id == WorkflowVersion.id,
        )
        .where(Workflow.user_id == current_user.id)
        .order_by(
            ExecutionLog.started_at.desc(),
            ExecutionLog.id.desc(),
        )
    )
    statement = apply_workspace_scope(statement, Workflow, context)
    rows = (await db.execute(statement)).all()
    response: list[ExecutionListResponse] = []
    for log, workflow_name, workflow_version in rows:
        payload = _execution_response(log).model_dump()
        payload.update(
            await _host_runtime_fields(request, db, log)
        )
        response.append(
            ExecutionListResponse(
                **payload,
                workflow_name=workflow_name,
                workflow_version=workflow_version,
            )
        )
    return response


@router.get("/{workflow_id}/logs", response_model=list[ExecutionResponse])
async def get_workflow_logs(
    workflow_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(get_workspace_context),
) -> list[ExecutionResponse]:
    workflow_statement = select(Workflow.id).where(
        Workflow.id == workflow_id,
        Workflow.user_id == current_user.id,
    )
    if context.isolation_enabled:
        workflow_statement = workflow_statement.where(
            Workflow.workspace_id == context.workspace_id
        )
    owned_id = (
        await db.execute(workflow_statement)
    ).scalar_one_or_none()
    if owned_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow not found",
        )
    statement = (
        select(ExecutionLog)
        .where(ExecutionLog.workflow_id == owned_id)
        .order_by(
            ExecutionLog.started_at.desc(),
            ExecutionLog.id.desc(),
        )
    )
    result = await db.execute(statement)
    logs = list(result.scalars().all())
    return [
        ExecutionResponse(
            **{
                **_execution_response(log).model_dump(),
                **await _host_runtime_fields(request, db, log),
            }
        )
        for log in logs
    ]


@router.get("/{id}/detail", response_model=ExecutionDetailResponse)
async def get_execution_detail(
    id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(get_workspace_context),
) -> ExecutionDetailResponse:
    execution_log = await _get_owned_execution(
        id,
        db,
        current_user,
        context.workspace_id if context.isolation_enabled else None,
    )
    workflow = await db.get(Workflow, execution_log.workflow_id)
    version = (
        await db.get(WorkflowVersion, execution_log.workflow_version_id)
        if execution_log.workflow_version_id is not None
        else None
    )
    delivery_rows = (
        await db.execute(
            select(DeliveryRecord)
            .where(DeliveryRecord.execution_id == execution_log.id)
            .order_by(
                DeliveryRecord.created_at,
                DeliveryRecord.id,
            )
        )
    ).scalars().all()
    metrics = (
        execution_log.metrics
        if isinstance(execution_log.metrics, dict)
        else {}
    )
    variables = metrics.get("variables")
    host_fields = await _host_runtime_fields(
        request,
        db,
        execution_log,
    )
    return ExecutionDetailResponse(
        **{
            **_execution_response(execution_log).model_dump(),
            **host_fields,
        },
        workflow_name=workflow.name if workflow is not None else "",
        workflow_version=version.version if version is not None else None,
        deliveries=[
            DeliveryRecordResponse.model_validate(record)
            for record in delivery_rows
        ],
        variables=variables if isinstance(variables, dict) else {},
    )


@router.get("/{id}/events")
async def get_execution_events(
    id: int,
    after_id: int | None = None,
    limit: int = 200,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(get_workspace_context),
) -> dict[str, Any]:
    await _get_owned_execution(
        id,
        db,
        current_user,
        context.workspace_id if context.isolation_enabled else None,
    )
    events = await list_execution_events(
        db,
        id,
        after_id=after_id,
        limit=limit,
    )
    return {
        "items": [
            {
                "id": event.id,
                "execution_id": event.execution_id,
                "type": event.type,
                "payload": event.payload or {},
                "created_at": event.created_at.isoformat(),
            }
            for event in events
        ]
    }


@router.get("/{id}", response_model=ExecutionDetailResponse)
async def get_execution(
    id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(get_workspace_context),
) -> ExecutionDetailResponse:
    return await get_execution_detail(
        id,
        request,
        db,
        current_user,
        context,
    )


@router.get("/{id}/artifact/{artifact_index}")
async def get_execution_artifact(
    id: int,
    artifact_index: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(get_workspace_context),
) -> FileResponse:
    execution_log = await _get_owned_execution(
        id,
        db,
        current_user,
        context.workspace_id if context.isolation_enabled else None,
    )

    artifacts = execution_log.artifacts or []
    if artifact_index < 0 or artifact_index >= len(artifacts):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Artifact not found",
        )

    entry = artifacts[artifact_index]
    if not isinstance(entry, dict):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Artifact not found",
        )

    extra_roots = await _allowed_screenshot_roots(db, execution_log)
    artifact_path = _screenshot_candidate(
        str(entry.get("path") or ""),
        extra_roots,
    )
    if artifact_path is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Artifact file not found",
        )

    media_type = (
        mimetypes.guess_type(artifact_path.name)[0]
        or "application/octet-stream"
    )
    return FileResponse(
        artifact_path,
        media_type=media_type,
        filename=artifact_path.name,
    )


@router.get("/{id}/screenshot/{step_index}")
async def get_execution_screenshot(
    id: int,
    step_index: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(get_workspace_context),
) -> FileResponse:
    execution_log = await _get_owned_execution(
        id,
        db,
        current_user,
        context.workspace_id if context.isolation_enabled else None,
    )

    screenshots = execution_log.screenshots or []
    if step_index < 0 or step_index >= len(screenshots):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Screenshot not found",
        )

    extra_roots = await _allowed_screenshot_roots(db, execution_log)
    screenshot_path = _screenshot_candidate(
        str(screenshots[step_index]),
        extra_roots,
    )
    if screenshot_path is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Screenshot file not found",
        )

    return FileResponse(
        screenshot_path,
        media_type="image/png",
        filename=screenshot_path.name,
    )


@router.websocket("/{id}/live")
async def live_execution(
    websocket: WebSocket,
    id: int,
    db: AsyncSession = Depends(get_db),
) -> None:
    await websocket.accept()
    sent_steps: set[int] = set()
    last_status: str | None = None
    last_event_id = 0
    try:
        current_user = await get_current_user(websocket=websocket, db=db)
    except HTTPException:
        await websocket.send_json(
            {
                "type": "error",
                "execution_id": id,
                "detail": "Could not validate credentials",
            }
        )
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    current_user_id = int(current_user.id)

    workspace_id: int | None = None
    if workspace_isolation_enabled():
        raw_workspace = websocket.query_params.get("workspace_id")
        try:
            parsed_workspace = (
                int(raw_workspace) if raw_workspace else None
            )
        except ValueError:
            parsed_workspace = None
        workspace = await resolve_workspace(
            db,
            current_user,
            parsed_workspace,
        )
        if workspace is None:
            await websocket.send_json(
                {
                    "type": "error",
                    "execution_id": id,
                    "detail": "Workspace not found",
                }
            )
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        workspace_id = workspace.id

    try:
        while True:
            await db.rollback()
            statement = (
                select(ExecutionLog)
                .join(
                    Workflow,
                    ExecutionLog.workflow_id == Workflow.id,
                )
                .where(
                    ExecutionLog.id == id,
                    Workflow.user_id == current_user_id,
                )
                .execution_options(populate_existing=True)
            )
            if workspace_id is not None:
                statement = statement.where(
                    Workflow.workspace_id == workspace_id
                )
            result = await db.execute(statement)
            execution_log = result.scalar_one_or_none()

            if execution_log is None:
                await websocket.send_json(
                    {
                        "type": "error",
                        "execution_id": id,
                        "detail": "Execution not found",
                    }
                )
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                return

            events = await list_execution_events(
                db,
                id,
                after_id=last_event_id,
                limit=100,
            )
            for event in events:
                last_event_id = max(last_event_id, int(event.id))
                await websocket.send_json(
                    {
                        "type": event.type,
                        "event_id": event.id,
                        "execution_id": id,
                        "payload": event.payload or {},
                        "created_at": event.created_at.isoformat(),
                    }
                )

            if last_status != execution_log.status:
                status_payload = {
                    "type": "status",
                    "execution_id": id,
                    "status": execution_log.status,
                }
                status_payload.update(
                    _execution_live_timestamps(execution_log)
                )
                await websocket.send_json(status_payload)
                last_status = execution_log.status

            step_logs = execution_log.step_logs or []
            screenshots = execution_log.screenshots or []
            for position, step_log in enumerate(step_logs):
                if not isinstance(step_log, dict):
                    continue
                step_index = step_log.get("step_index", position)
                if step_index in sent_steps:
                    continue
                sent_steps.add(step_index)
                screenshot_path = step_log.get("screenshot_path")
                if screenshot_path is None and position < len(screenshots):
                    screenshot_path = screenshots[position]
                await websocket.send_json(
                    {
                        "type": "step",
                        "execution_id": id,
                        "step_log": step_log,
                        "screenshot_path": screenshot_path,
                    }
                )

            if execution_log.status in _TERMINAL_STATUSES:
                completed_payload = {
                    "type": "completed",
                    "execution_id": id,
                    "status": execution_log.status,
                }
                completed_payload.update(
                    _execution_live_timestamps(execution_log)
                )
                await websocket.send_json(completed_payload)
                break

            try:
                await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=0.2,
                )
            except asyncio.TimeoutError:
                pass
    except WebSocketDisconnect:
        pass
    finally:
        sent_steps.clear()
        try:
            await db.rollback()
        except Exception:
            pass


def _execution_live_timestamps(
    execution_log: Any,
) -> dict[str, str]:
    payload: dict[str, str] = {}
    for key in ("started_at", "finished_at"):
        value = getattr(execution_log, key, None)
        if value is None:
            continue
        payload[key] = (
            value.isoformat()
            if hasattr(value, "isoformat")
            else str(value)
        )
    return payload


__all__ = ["LOGS_DIR", "router"]
