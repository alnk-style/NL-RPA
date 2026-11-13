"""录制会话接口：把浏览器或桌面录制的操作转成可编辑草稿。

默认关闭，需要 RECORDER_MODE=true 才可用。录制事件保存在内存会话里，
结束时写入日志目录，并可以落库成一个 draft 状态的 Workflow。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.config import get_settings
from app.database import get_db
from app.services.recording_compiler import (
    DESKTOP_MODE,
    WEB_MODE,
    RecordingDraft,
    RecordingError,
    RecordingEvent,
    compile_recording,
    step_summaries,
)
from app.models.agent import DesktopAgent
from app.models.user import User
from app.models.workflow import Workflow
from app.services.workflow_authoring import (
    WorkflowAuthoringError,
    WorkflowAuthoringService,
)
from runtime_paths import stable_logs_dir

router = APIRouter(prefix="/api/recordings", tags=["录制"])

# 会话只保存在内存中，进程重启后录制需要重新开始；事件原文在结束时
# 会写入日志目录，方便排查与回放。
recording_sessions: dict[str, RecordingSession] = {}


class RecordingStartRequest(BaseModel):
    mode: Literal["web", "windows", "desktop"] = "web"
    name: str | None = None
    goal: str | None = None
    workflow_id: int | None = None
    start_url: str | None = None


class RecordingEventsRequest(BaseModel):
    events: list[RecordingEvent] = Field(min_length=1)


class RecordingFinishRequest(BaseModel):
    name: str | None = None
    goal: str | None = None
    create_workflow: bool = True
    workflow_id: int | None = None


class RecordingPickRequest(BaseModel):
    agent_id: int | None = None
    timeout: float | None = Field(default=None, ge=0.01, le=60)
    x: int | None = None
    y: int | None = None


class RecordingDraftPayload(BaseModel):
    plan: dict[str, Any]
    warnings: list[str] = Field(default_factory=list)
    event_count: int = 0
    dropped_count: int = 0
    steps: list[dict[str, Any]] = Field(default_factory=list)


class RecordingStateResponse(BaseModel):
    id: str
    mode: str
    status: str
    name: str | None = None
    goal: str | None = None
    event_count: int
    workflow_id: int | None = None
    recording_path: str | None = None
    created_at: datetime
    finished_at: datetime | None = None
    error: str | None = None
    draft: RecordingDraftPayload | None = None


class RecordingPickResponse(RecordingStateResponse):
    picked: dict[str, Any]


@dataclass
class RecordingSession:
    id: str
    user_id: int
    mode: str
    created_at: datetime
    name: str | None = None
    goal: str | None = None
    workflow_id: int | None = None
    status: str = "recording"
    finished_at: datetime | None = None
    recording_path: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)


def require_recorder_enabled() -> None:
    if not get_settings().RECORDER_MODE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="录制功能未启用，请先在设置中开启 RECORDER_MODE。",
        )


def _normalize_mode(value: str) -> str:
    text = str(value or "").strip().lower()
    if text in {"desktop", DESKTOP_MODE}:
        return DESKTOP_MODE
    if text == WEB_MODE:
        return WEB_MODE
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail="录制模式只支持 web 或 windows",
    )


def _recording_dir() -> Path:
    if os.getenv("NL_RPA_DESKTOP_MODE") == "1":
        return stable_logs_dir() / "recordings"
    return Path("logs") / "recordings"


def persist_recording(
    session: RecordingSession,
    draft: RecordingDraft,
    directory: Path | None = None,
) -> str:
    """把录制事件与草稿写入日志目录，返回文件路径。"""

    target_dir = directory or _recording_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    timestamp = session.created_at.strftime("%Y%m%d_%H%M%S")
    path = target_dir / f"recording_{timestamp}_{session.id[:8]}.json"
    path.write_text(
        json.dumps(
            {
                "id": session.id,
                "mode": session.mode,
                "name": session.name,
                "goal": session.goal,
                "created_at": session.created_at.isoformat(),
                "events": session.events,
                "warnings": draft.warnings,
                "draft": draft.plan,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return str(path)


async def _owned_session(
    recording_id: str,
    current_user: User,
) -> RecordingSession:
    session = recording_sessions.get(recording_id)
    if session is None or session.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="录制会话不存在",
        )
    return session


async def _allowed_apps(
    db: AsyncSession,
    user_id: int,
) -> list[str]:
    result = await db.execute(
        select(DesktopAgent).where(DesktopAgent.user_id == user_id)
    )
    apps: list[str] = []
    for agent in result.scalars().all():
        for app in agent.allowed_apps or []:
            text = str(app).strip()
            if text and text not in apps:
                apps.append(text)
    return apps


def _compile_session(
    session: RecordingSession,
    *,
    allowed_apps: list[str] | None = None,
) -> RecordingDraft:
    return compile_recording(
        session.events,
        mode=session.mode,
        allowed_apps=allowed_apps or (),
    )


def _draft_payload(draft: RecordingDraft) -> RecordingDraftPayload:
    return RecordingDraftPayload(
        plan=draft.plan,
        warnings=list(draft.warnings),
        event_count=draft.event_count,
        dropped_count=draft.dropped_count,
        steps=step_summaries(draft.plan),
    )


def _state(
    session: RecordingSession,
    *,
    draft: RecordingDraft | None = None,
    error: str | None = None,
) -> RecordingStateResponse:
    return RecordingStateResponse(
        id=session.id,
        mode=session.mode,
        status=session.status,
        name=session.name,
        goal=session.goal,
        event_count=len(session.events),
        workflow_id=session.workflow_id,
        recording_path=session.recording_path,
        created_at=session.created_at,
        finished_at=session.finished_at,
        error=error,
        draft=_draft_payload(draft) if draft is not None else None,
    )


def _preview(
    session: RecordingSession,
    *,
    allowed_apps: list[str] | None = None,
) -> tuple[RecordingDraft | None, str | None]:
    if not session.events:
        return None, None
    try:
        return _compile_session(session, allowed_apps=allowed_apps), None
    except RecordingError as exc:
        return None, str(exc)


@router.post(
    "",
    response_model=RecordingStateResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_recorder_enabled)],
)
async def start_recording(
    payload: RecordingStartRequest,
    current_user: User = Depends(get_current_user),
) -> RecordingStateResponse:
    mode = _normalize_mode(payload.mode)
    session = RecordingSession(
        id=uuid4().hex,
        user_id=current_user.id,
        mode=mode,
        created_at=datetime.now(timezone.utc),
        name=payload.name,
        goal=payload.goal,
        workflow_id=payload.workflow_id,
    )
    start_url = (payload.start_url or "").strip()
    if mode == WEB_MODE and start_url:
        session.events.append(
            {"kind": "navigate", "url": start_url}
        )
    recording_sessions[session.id] = session
    return _state(session)


@router.post(
    "/{recording_id}/events",
    response_model=RecordingStateResponse,
    dependencies=[Depends(require_recorder_enabled)],
)
async def append_recording_events(
    recording_id: str,
    payload: RecordingEventsRequest,
    current_user: User = Depends(get_current_user),
) -> RecordingStateResponse:
    session = await _owned_session(recording_id, current_user)
    if session.status != "recording":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="录制已经结束，不能再追加事件",
        )
    session.events.extend(
        event.model_dump(mode="json", exclude_none=True)
        for event in payload.events
    )
    return _state(session)


@router.get(
    "/{recording_id}",
    response_model=RecordingStateResponse,
    dependencies=[Depends(require_recorder_enabled)],
)
async def get_recording(
    recording_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RecordingStateResponse:
    session = await _owned_session(recording_id, current_user)
    allowed_apps = (
        await _allowed_apps(db, current_user.id)
        if session.mode == DESKTOP_MODE
        else None
    )
    draft, error = _preview(session, allowed_apps=allowed_apps)
    return _state(session, draft=draft, error=error)


@router.post(
    "/{recording_id}/pick",
    response_model=RecordingPickResponse,
    dependencies=[Depends(require_recorder_enabled)],
)
async def pick_recording_element(
    recording_id: str,
    payload: RecordingPickRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RecordingPickResponse:
    session = await _owned_session(recording_id, current_user)
    if session.status != "recording":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="录制已经结束，不能再拾取控件",
        )
    if session.mode != DESKTOP_MODE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="只有桌面录制可以拾取控件",
        )

    dispatcher = getattr(
        request.app.state,
        "desktop_dispatcher",
        None,
    )
    if dispatcher is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="桌面派发器未就绪",
        )

    pick_request = {
        key: value
        for key, value in (("x", payload.x), ("y", payload.y))
        if value is not None
    }
    result = await dispatcher.pick(
        pick_request,
        agent_id=payload.agent_id,
        timeout=payload.timeout,
    )
    if not isinstance(result, dict) or not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(
                (result or {}).get("error") or "桌面拾取失败"
            ),
        )

    event = _event_from_pick(result)
    session.events.append(event)
    allowed_apps = await _allowed_apps(db, current_user.id)
    draft, error = _preview(session, allowed_apps=allowed_apps)
    state = _state(session, draft=draft, error=error)
    return RecordingPickResponse(
        **state.model_dump(),
        picked={
            "event": event,
            "element": result.get("element"),
            "window": result.get("window"),
        },
    )


@router.post(
    "/{recording_id}/finish",
    response_model=RecordingStateResponse,
    dependencies=[Depends(require_recorder_enabled)],
)
async def finish_recording(
    recording_id: str,
    payload: RecordingFinishRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RecordingStateResponse:
    session = await _owned_session(recording_id, current_user)
    if session.status == "finished":
        return _state(session)

    allowed_apps = (
        await _allowed_apps(db, current_user.id)
        if session.mode == DESKTOP_MODE
        else None
    )
    try:
        draft = _compile_session(session, allowed_apps=allowed_apps)
        draft.plan = WorkflowAuthoringService().normalize_recorded_plan(
            draft.plan
        )
    except RecordingError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except WorkflowAuthoringError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Recording draft validation failed: {exc}",
        ) from exc

    session.recording_path = persist_recording(session, draft)
    if payload.create_workflow:
        workflow = await _save_workflow(
            db,
            current_user,
            session,
            draft,
            payload,
        )
        session.workflow_id = workflow.id
    session.status = "finished"
    session.finished_at = datetime.now(timezone.utc)
    return _state(session, draft=draft)


@router.delete(
    "/{recording_id}",
    dependencies=[Depends(require_recorder_enabled)],
)
async def discard_recording(
    recording_id: str,
    current_user: User = Depends(get_current_user),
) -> dict[str, str]:
    session = await _owned_session(recording_id, current_user)
    recording_sessions.pop(session.id, None)
    return {"status": "deleted", "recording_id": session.id}


def _event_from_pick(result: dict[str, Any]) -> dict[str, Any]:
    element = result.get("element")
    if not isinstance(element, dict):
        element = {}
    params: dict[str, Any] = {}
    for key in ("automation_id", "name", "control_type"):
        value = element.get(key)
        if isinstance(value, str) and value.strip():
            params[key] = value.strip()
    if not params:
        center = element.get("center")
        point = result.get("point")
        source = center if isinstance(center, dict) else point
        if isinstance(source, dict):
            x = source.get("x")
            y = source.get("y")
            if isinstance(x, (int, float)) and isinstance(
                y, (int, float)
            ):
                params = {"x": int(x), "y": int(y)}

    event: dict[str, Any] = {
        "kind": "desktop_click",
        "params": params,
    }
    label = element.get("name")
    if isinstance(label, str) and label.strip():
        event["label"] = label.strip()
    window = result.get("window")
    if isinstance(window, dict) and window:
        event["window"] = window
    return event


async def _save_workflow(
    db: AsyncSession,
    current_user: User,
    session: RecordingSession,
    draft: RecordingDraft,
    payload: RecordingFinishRequest,
) -> Workflow:
    plan = draft.plan
    name = (
        (payload.name or session.name or "").strip()
        or "录制流程"
    )
    goal = (
        (payload.goal or session.goal or "").strip()
        or f"由录制生成的流程：{name}"
    )
    target_id = payload.workflow_id or session.workflow_id
    if target_id is not None:
        workflow = await db.get(Workflow, int(target_id))
        if workflow is None or workflow.user_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="目标流程不存在",
            )
    else:
        workflow = Workflow(
            user_id=current_user.id,
            name=name,
            natural_language_input=goal,
        )
        db.add(workflow)

    workflow.name = name
    workflow.natural_language_input = goal
    workflow.description = "由录制生成的草稿，可继续编辑步骤。"
    workflow.structured_plan = plan
    workflow.executor_type = str(
        plan.get("executor_type") or session.mode
    )
    workflow.required_capabilities = list(
        plan.get("required_capabilities") or []
    )
    workflow.status = "draft"
    await db.commit()
    await db.refresh(workflow)
    return workflow


__all__ = [
    "RecordingDraftPayload",
    "RecordingEventsRequest",
    "RecordingFinishRequest",
    "RecordingPickRequest",
    "RecordingStartRequest",
    "RecordingStateResponse",
    "persist_recording",
    "recording_sessions",
    "require_recorder_enabled",
    "router",
]
