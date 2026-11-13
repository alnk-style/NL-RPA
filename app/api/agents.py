from __future__ import annotations

import hashlib
import inspect
import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.host_capabilities import (
    environment_or_unknown,
    normalize_capabilities,
)
from app.core.pairing_cache import pairing_cache
from app.database import get_db
from app.models.agent import DesktopAgent
from app.models.user import User
from app.schemas.agent import (
    AgentListResponse,
    AgentResponse,
    PairRequest,
    PairResponse,
)
from app.security.workspaces import (
    WorkspaceContext,
    apply_workspace_scope,
    ensure_default_workspace,
    workspace_isolation_enabled,
)
from app.services.hosts import safe_human_access_url
from app.api.workspace_deps import get_workspace_context

router = APIRouter(prefix="/api/agents", tags=["Agent管理"])

PAIRING_CODE_TTL = 300
logger = logging.getLogger(__name__)


def hash_agent_token(token: str) -> str:
    """Return the digest stored for an Agent authentication token."""

    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _generate_pairing_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def _get_desktop_agent_hub() -> object | None:
    """Load the WebSocket hub when that optional module is present."""

    try:
        from app.desktop.hub import agent_hub
    except ImportError:
        return None
    return agent_hub


async def _get_owned_agent(
    agent_id: int,
    user_id: int,
    db: AsyncSession,
    workspace_id: int | None = None,
) -> DesktopAgent:
    statement = select(DesktopAgent).where(
        DesktopAgent.id == agent_id,
        DesktopAgent.user_id == user_id,
    )
    if workspace_id is not None:
        statement = statement.where(
            DesktopAgent.workspace_id == workspace_id
        )
    result = await db.execute(statement)
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found",
        )
    return agent


async def _disconnect_agent(
    request: Request,
    agent_id: int,
) -> None:
    """Best-effort disconnect for a registered Agent WebSocket."""

    managers = (
        getattr(request.app.state, "agent_hub", None),
        _get_desktop_agent_hub(),
        getattr(request.app.state, "agent_manager", None),
        getattr(request.app.state, "connection_manager", None),
        getattr(request.app.state, "websocket_manager", None),
    )
    for manager in managers:
        if manager is None:
            continue
        websocket = None
        getter = getattr(manager, "get", None)
        if callable(getter):
            websocket = getter(agent_id)
        disconnect = getattr(manager, "unregister", None)
        if not callable(disconnect):
            disconnect = getattr(manager, "disconnect", None)
        if not callable(disconnect):
            continue
        try:
            result = disconnect(agent_id)
            if inspect.isawaitable(result):
                await result
            close = getattr(websocket, "close", None)
            if callable(close):
                result = close()
                if inspect.isawaitable(result):
                    await result
        except Exception:
            logger.warning(
                "Failed to disconnect Agent %s",
                agent_id,
                exc_info=True,
            )
        return

    connections = getattr(
        request.app.state,
        "agent_connections",
        None,
    )
    if not isinstance(connections, dict):
        return

    websocket = connections.pop(agent_id, None)
    close = getattr(websocket, "close", None)
    if not callable(close):
        return
    try:
        result = close()
        if inspect.isawaitable(result):
            await result
    except Exception:
        logger.warning(
            "Failed to close Agent %s WebSocket",
            agent_id,
            exc_info=True,
        )


@router.post("/pair", response_model=PairResponse)
async def pair_agent(
    payload: PairRequest,
    db: AsyncSession = Depends(get_db),
) -> PairResponse:
    user_id = pairing_cache.consume_code(payload.pairing_code)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired pairing code",
        )

    token = secrets.token_urlsafe(32)
    user = await db.get(User, user_id)
    workspace = None
    if user is not None and workspace_isolation_enabled():
        workspace = await ensure_default_workspace(db, user)
    agent = DesktopAgent(
        user_id=user_id,
        workspace_id=workspace.id if workspace is not None else None,
        name=payload.name,
        platform=payload.platform,
        version=payload.version,
        environment=payload.environment,
        capabilities=payload.capabilities,
        human_access_url=payload.human_access_url,
        human_access_instructions=payload.human_access_instructions,
        connection_security=payload.connection_security,
        status="offline",
        token_hash=hash_agent_token(token),
    )
    db.add(agent)
    await db.commit()
    await db.refresh(agent)

    return PairResponse(agent_id=agent.id, token=token)


@router.post("/pairing-code")
async def create_pairing_code(
    current_user: User = Depends(get_current_user),
) -> dict[str, str | int]:
    pairing_code = _generate_pairing_code()
    pairing_cache.store_code(
        pairing_code,
        current_user.id,
        PAIRING_CODE_TTL,
    )
    return {
        "pairing_code": pairing_code,
        "expires_in": PAIRING_CODE_TTL,
    }


@router.get("", response_model=AgentListResponse)
@router.get("/", response_model=AgentListResponse, include_in_schema=False)
async def list_agents(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(get_workspace_context),
) -> AgentListResponse:
    statement = apply_workspace_scope(
        select(DesktopAgent).where(
            DesktopAgent.user_id == current_user.id
        ),
        DesktopAgent,
        context,
    )
    result = await db.execute(
        statement.order_by(DesktopAgent.id.desc())
    )
    manager = getattr(request.app.state, "connection_manager", None)
    is_online = getattr(manager, "is_online", None)
    agents = []
    for agent in result.scalars().all():
        response = AgentResponse(
            id=agent.id,
            name=agent.name,
            platform=agent.platform,
            version=agent.version,
            environment=environment_or_unknown(agent.environment),
            capabilities=normalize_capabilities(agent.capabilities),
            status=agent.status,
            last_seen_at=agent.last_seen_at,
            human_access_url=safe_human_access_url(
                agent.human_access_url
            ),
            human_access_instructions=(
                agent.human_access_instructions
            ),
            connection_security=agent.connection_security,
        )
        if callable(is_online):
            response.status = "online" if is_online(agent.id) else "offline"
        agents.append(response)
    return AgentListResponse(agents=agents)


@router.delete("/{agent_id}")
async def delete_agent(
    agent_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(get_workspace_context),
) -> dict[str, str]:
    agent = await _get_owned_agent(
        agent_id,
        current_user.id,
        db,
        context.workspace_id if context.isolation_enabled else None,
    )
    await _disconnect_agent(request, agent_id)
    await db.delete(agent)
    await db.commit()
    return {"status": "revoked"}


@router.post("/{agent_id}/revoke")
async def revoke_agent(
    agent_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(get_workspace_context),
) -> dict[str, str]:
    agent = await _get_owned_agent(
        agent_id,
        current_user.id,
        db,
        context.workspace_id if context.isolation_enabled else None,
    )
    agent.token_hash = ""
    agent.status = "offline"
    await db.commit()
    await db.refresh(agent)
    await _disconnect_agent(request, agent_id)
    return {"status": "revoked"}


__all__ = [
    "PAIRING_CODE_TTL",
    "hash_agent_token",
    "pairing_cache",
    "router",
]
