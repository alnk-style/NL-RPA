from __future__ import annotations

import asyncio

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.api.workspace_deps import get_workspace_context
from app.database import get_db
from app.models.agent import DesktopAgent
from app.models.user import User
from app.schemas.agent import HumanAccessUpdate
from app.schemas.host import HostListResponse, HostResponse
from app.security.workspaces import (
    WorkspaceContext,
    apply_workspace_scope,
)
from app.services.hosts import active_task_counts, host_payload
from app.services.host_events import HostEventBus

router = APIRouter(prefix="/api/hosts", tags=["执行主机"])


def _is_online(request: Request, agent_id: int) -> bool:
    manager = getattr(request.app.state, "connection_manager", None)
    checker = getattr(manager, "is_online", None)
    if callable(checker):
        try:
            return bool(checker(int(agent_id)))
        except Exception:
            return False
    return False


def _session_id(request: Request, agent_id: int) -> str | None:
    manager = getattr(request.app.state, "connection_manager", None)
    getter = getattr(manager, "get_session_id", None)
    if not callable(getter):
        return None
    try:
        value = getter(int(agent_id))
    except Exception:
        return None
    return str(value) if value else None


def _response_from_agent(
    request: Request,
    agent: DesktopAgent,
    count: int,
) -> HostResponse:
    online = _is_online(request, int(agent.id))
    return HostResponse.model_validate(
        host_payload(
            agent,
            online=online,
            active_task_count=count,
            session_id=_session_id(request, int(agent.id)),
        )
    )


async def _owned_agent(
    host_id: int,
    db: AsyncSession,
    user: User,
    context: WorkspaceContext,
) -> DesktopAgent:
    statement = apply_workspace_scope(
        select(DesktopAgent).where(
            DesktopAgent.id == int(host_id),
            DesktopAgent.user_id == user.id,
        ),
        DesktopAgent,
        context,
    )
    agent = (await db.execute(statement)).scalar_one_or_none()
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Host not found",
        )
    return agent


@router.get("", response_model=HostListResponse)
@router.get("/", response_model=HostListResponse, include_in_schema=False)
async def list_hosts(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(get_workspace_context),
) -> HostListResponse:
    statement = apply_workspace_scope(
        select(DesktopAgent).where(
            DesktopAgent.user_id == current_user.id
        ),
        DesktopAgent,
        context,
    )
    agents = list(
        (await db.execute(
            statement.order_by(DesktopAgent.id.desc())
        )).scalars().all()
    )
    counts = await active_task_counts(
        db,
        [int(agent.id) for agent in agents],
    )
    return HostListResponse(
        hosts=[
            _response_from_agent(
                request,
                agent,
                counts.get(int(agent.id), 0),
            )
            for agent in agents
        ]
    )


@router.websocket("/events")
async def host_events(
    websocket: WebSocket,
    db: AsyncSession = Depends(get_db),
) -> None:
    try:
        current_user = await get_current_user(
            websocket=websocket,
            db=db,
        )
    except Exception:
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION
        )
        return
    bus = getattr(websocket.app.state, "host_event_bus", None)
    if not isinstance(bus, HostEventBus):
        await websocket.close(
            code=status.WS_1011_INTERNAL_ERROR
        )
        return
    await websocket.accept()
    queue, history = await bus.subscribe(int(current_user.id))
    try:
        for event in history:
            await websocket.send_json(event)
        while True:
            try:
                event = await asyncio.wait_for(
                    queue.get(),
                    timeout=20,
                )
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "ping"})
                continue
            await websocket.send_json(event)
    except WebSocketDisconnect:
        pass
    finally:
        await bus.unsubscribe(int(current_user.id), queue)


@router.get("/{host_id}", response_model=HostResponse)
async def get_host(
    host_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(get_workspace_context),
) -> HostResponse:
    agent = await _owned_agent(
        host_id,
        db,
        current_user,
        context,
    )
    counts = await active_task_counts(db, [int(agent.id)])
    return _response_from_agent(
        request,
        agent,
        counts.get(int(agent.id), 0),
    )


@router.patch("/{host_id}", response_model=HostResponse)
async def update_host_access(
    host_id: int,
    payload: HumanAccessUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(get_workspace_context),
) -> HostResponse:
    agent = await _owned_agent(
        host_id,
        db,
        current_user,
        context,
    )
    agent.human_access_url = payload.human_access_url
    agent.human_access_instructions = (
        payload.human_access_instructions
    )
    await db.commit()
    await db.refresh(agent)
    counts = await active_task_counts(db, [int(agent.id)])
    return _response_from_agent(
        request,
        agent,
        counts.get(int(agent.id), 0),
    )


__all__ = ["router"]
