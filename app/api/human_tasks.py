from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.api.executions import (
    _allowed_screenshot_roots,
    _screenshot_candidate,
)
from app.database import get_db
from app.models.execution import ExecutionLog
from app.models.human_task import HumanTask
from app.models.user import User
from app.models.workflow import Workflow
from app.models.workflow_version import WorkflowVersion
from app.schemas.human_task import (
    HumanTaskCancelRequest,
    HumanTaskExecutionState,
    HumanTaskListResponse,
    HumanTaskResponse,
    HumanTaskResumeRequest,
    HumanTaskResumeResponse,
)
from app.services.human_tasks import (
    HumanTaskManager,
    HumanTaskStateError,
    HumanTaskVerificationError,
)

router = APIRouter(prefix="/api/human-tasks", tags=["人工任务"])


def _manager(request: Request) -> HumanTaskManager:
    manager = getattr(request.app.state, "human_task_manager", None)
    if manager is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Human task manager is not ready",
        )
    return manager


async def _owned_task(
    task_id: int,
    db: AsyncSession,
    current_user: User,
) -> tuple[HumanTask, ExecutionLog, Workflow, WorkflowVersion | None]:
    row = (
        await db.execute(
            select(
                HumanTask,
                ExecutionLog,
                Workflow,
                WorkflowVersion,
            )
            .join(
                ExecutionLog,
                HumanTask.execution_id == ExecutionLog.id,
            )
            .join(Workflow, ExecutionLog.workflow_id == Workflow.id)
            .outerjoin(
                WorkflowVersion,
                ExecutionLog.workflow_version_id == WorkflowVersion.id,
            )
            .where(
                HumanTask.id == int(task_id),
                Workflow.user_id == current_user.id,
            )
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Human task not found",
        )
    return row[0], row[1], row[2], row[3]


def _response(
    task: HumanTask,
    execution: ExecutionLog,
    workflow: Workflow,
    version: WorkflowVersion | None,
    *,
    now: datetime | None = None,
) -> HumanTaskResponse:
    payload = task.payload if isinstance(task.payload, dict) else {}
    current_url = payload.get("current_url")
    browser_profile_id = payload.get("browser_profile_id")
    last_verification = payload.get("last_verification")
    last_reason = (
        last_verification.get("reason")
        if isinstance(last_verification, dict)
        else None
    )
    current_time = now or datetime.utcnow()
    end_time = task.resolved_at or current_time
    waiting_seconds = max(
        int((end_time - task.created_at).total_seconds()),
        0,
    )
    active = (
        task.status == "pending"
        and (
            task.expires_at is None
            or task.expires_at > current_time
        )
    )
    return HumanTaskResponse(
        id=task.id,
        execution_id=task.execution_id,
        step_id=task.step_id,
        kind=task.kind,
        instruction=task.instruction,
        payload=payload,
        screenshot_path=task.screenshot_path,
        status=task.status,
        created_at=task.created_at,
        resolved_at=task.resolved_at,
        expires_at=task.expires_at,
        workflow_name=workflow.name,
        workflow_version=version.version if version is not None else None,
        execution_status=execution.status,
        host_id=execution.host_id,
        host_environment=execution.host_environment,
        host_bound=bool(execution.host_bound),
        host_affinity_configured=bool(
            execution.host_affinity_hash
        ),
        waiting_seconds=waiting_seconds,
        can_resume=active,
        current_url=str(current_url) if current_url else None,
        browser_profile_id=(
            int(browser_profile_id)
            if str(browser_profile_id or "").isdigit()
            else None
        ),
        last_verification_error=(
            str(last_reason) if last_reason else None
        ),
    )


@router.get("", response_model=HumanTaskListResponse)
async def list_human_tasks(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    task_status: str | None = Query(default=None, alias="status"),
    kind: str | None = None,
) -> HumanTaskListResponse:
    statement = (
        select(
            HumanTask,
            ExecutionLog,
            Workflow,
            WorkflowVersion,
        )
        .join(ExecutionLog, HumanTask.execution_id == ExecutionLog.id)
        .join(Workflow, ExecutionLog.workflow_id == Workflow.id)
        .outerjoin(
            WorkflowVersion,
            ExecutionLog.workflow_version_id == WorkflowVersion.id,
        )
        .where(Workflow.user_id == current_user.id)
        .order_by(
            HumanTask.created_at.desc(),
            HumanTask.id.desc(),
        )
    )
    if task_status:
        statement = statement.where(HumanTask.status == task_status)
    if kind:
        statement = statement.where(HumanTask.kind == kind)
    now = datetime.utcnow()
    rows = (await db.execute(statement)).all()
    return HumanTaskListResponse(
        items=[
            _response(
                task,
                execution,
                workflow,
                version,
                now=now,
            )
            for task, execution, workflow, version in rows
        ]
    )


@router.get("/{task_id}", response_model=HumanTaskResponse)
async def get_human_task(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> HumanTaskResponse:
    task, execution, workflow, version = await _owned_task(
        task_id,
        db,
        current_user,
    )
    return _response(task, execution, workflow, version)


@router.get("/{task_id}/screenshot")
async def get_human_task_screenshot(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> FileResponse:
    task, execution, _, _ = await _owned_task(
        task_id,
        db,
        current_user,
    )
    if not task.screenshot_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Screenshot not found",
        )
    extra_roots = await _allowed_screenshot_roots(db, execution)
    screenshot = _screenshot_candidate(
        task.screenshot_path,
        extra_roots,
    )
    if screenshot is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Screenshot file not found",
        )
    return FileResponse(
        screenshot,
        media_type="image/png",
        filename=screenshot.name,
    )


@router.post(
    "/{task_id}/resume",
    response_model=HumanTaskResumeResponse,
)
async def resume_human_task(
    task_id: int,
    payload: HumanTaskResumeRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> HumanTaskResumeResponse:
    manager = _manager(request)
    task, execution, workflow, version = await _owned_task(
        task_id,
        db,
        current_user,
    )
    was_resolved = task.status == "resolved"
    await db.rollback()
    try:
        resolved = await manager.resume(
            int(task_id),
            decision=payload.decision,
            verification=payload.verification,
            actor_id=current_user.id,
        )
    except HumanTaskVerificationError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": exc.code,
                "reason": exc.reason,
                "verification": exc.verification,
            },
        ) from exc
    except HumanTaskStateError as exc:
        raise HTTPException(
            status_code=(
                status.HTTP_404_NOT_FOUND
                if exc.code == "task_not_found"
                else status.HTTP_409_CONFLICT
            ),
            detail={
                "code": exc.code,
                "reason": exc.reason,
            },
        ) from exc

    await db.rollback()
    task, execution, workflow, version = await _owned_task(
        task_id,
        db,
        current_user,
    )
    return HumanTaskResumeResponse(
        human_task=_response(task, execution, workflow, version),
        execution=HumanTaskExecutionState(
            id=execution.id,
            status=execution.status,
            human_task_id=execution.human_task_id,
        ),
        idempotent=was_resolved,
    )


@router.post("/{task_id}/cancel", response_model=HumanTaskResponse)
async def cancel_human_task(
    task_id: int,
    payload: HumanTaskCancelRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> HumanTaskResponse:
    manager = _manager(request)
    await _owned_task(task_id, db, current_user)
    await db.rollback()
    try:
        await manager.cancel(
            int(task_id),
            reason=payload.reason,
            actor_id=current_user.id,
        )
    except HumanTaskStateError as exc:
        raise HTTPException(
            status_code=(
                status.HTTP_404_NOT_FOUND
                if exc.code == "task_not_found"
                else status.HTTP_409_CONFLICT
            ),
            detail={
                "code": exc.code,
                "reason": exc.reason,
            },
        ) from exc

    await db.rollback()
    task, execution, workflow, version = await _owned_task(
        task_id,
        db,
        current_user,
    )
    return _response(task, execution, workflow, version)


__all__ = ["router"]
