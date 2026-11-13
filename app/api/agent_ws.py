from __future__ import annotations

import asyncio
import contextlib
import hmac
import inspect
import logging
import time
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.agents import hash_agent_token
from app.host_capabilities import (
    normalize_capabilities,
    normalize_environment,
)
from app.database import get_db
from app.engine.agent_connection_manager import AgentConnectionManager
from app.engine.desktop_dispatcher import (
    DesktopDispatcher,
    apply_agent_event,
)
from app.models.agent import DesktopAgent

router = APIRouter(prefix="/api/agents", tags=["Agent管理"])
logger = logging.getLogger(__name__)

PING_INTERVAL_SECONDS = 30
PONG_TIMEOUT_SECONDS = 60
INVALID_TOKEN_CLOSE_CODE = 4001
INSECURE_VM_CLOSE_CODE = 4003


async def _heartbeat_loop(
    websocket: WebSocket,
    state: dict[str, Any],
) -> None:
    try:
        while True:
            await asyncio.sleep(PING_INTERVAL_SECONDS)
            if time.monotonic() - float(state["last_pong"]) >= PONG_TIMEOUT_SECONDS:
                await websocket.close(code=1001)
                return
            await websocket.send_json({"type": "ping"})
    except asyncio.CancelledError:
        raise
    except (RuntimeError, WebSocketDisconnect):
        return
    except Exception:
        logger.debug("Agent heartbeat loop stopped", exc_info=True)


def _connection_manager(websocket: WebSocket) -> AgentConnectionManager:
    manager = getattr(
        websocket.app.state,
        "connection_manager",
        None,
    )
    if manager is None:
        manager = AgentConnectionManager()
        websocket.app.state.connection_manager = manager
    return manager


def _desktop_dispatcher(websocket: WebSocket) -> DesktopDispatcher | None:
    return getattr(
        websocket.app.state,
        "desktop_dispatcher",
        None,
    )


async def _publish_host_event(
    websocket: WebSocket,
    agent: DesktopAgent,
    event_type: str,
    *,
    capabilities_changed: bool = False,
) -> None:
    bus = getattr(websocket.app.state, "host_event_bus", None)
    publish = getattr(bus, "publish", None)
    if not callable(publish):
        return
    await publish(
        int(agent.user_id),
        event_type,
        {
            "host_id": int(agent.id),
            "name": agent.name,
            "environment": agent.environment,
            "capabilities": list(agent.capabilities or []),
            "online": agent.status == "online",
            "capabilities_changed": capabilities_changed,
        },
    )


def _result_from_event(event: dict[str, Any]) -> dict[str, Any]:
    return DesktopDispatcher._result_from_event(event)


async def _process_agent_message(
    db: AsyncSession,
    manager: AgentConnectionManager,
    dispatcher: DesktopDispatcher | None,
    agent: DesktopAgent,
    message: dict[str, Any],
    heartbeat_state: dict[str, Any],
) -> None:
    message_type = str(message.get("type", ""))
    if message_type == "agent.hello":
        previous_environment = agent.environment
        previous_capabilities = list(agent.capabilities or [])
        environment = normalize_environment(
            message.get("environment") or agent.environment
        )
        connection_security = str(
            message.get("connection_security") or "unknown"
        ).strip().lower()
        if environment == "vm" and connection_security != "wss":
            raise ValueError("VM Agent requires WSS transport")
        agent.environment = environment
        agent.capabilities = normalize_capabilities(
            message.get("capabilities")
            if "capabilities" in message
            else agent.capabilities
        )
        agent.platform = str(
            message.get("platform") or agent.platform
        )
        agent.version = str(
            message.get("version") or agent.version
        )
        agent.human_access_url = (
            str(message["human_access_url"]).strip()
            if message.get("human_access_url")
            else agent.human_access_url
        )
        agent.human_access_instructions = (
            str(message["human_access_instructions"]).strip()
            if message.get("human_access_instructions")
            else agent.human_access_instructions
        )
        agent.connection_security = connection_security
        agent.last_capabilities_at = datetime.utcnow()
        agent.last_seen_at = datetime.utcnow()
        await db.commit()
        changed = (
            previous_environment != agent.environment
            or previous_capabilities != list(agent.capabilities or [])
        )
        if changed:
            await _publish_host_event(
                websocket,
                agent,
                "host_capability_changed",
                capabilities_changed=True,
            )
        return

    if message_type == "pong":
        heartbeat_state["last_pong"] = time.monotonic()
        agent.last_seen_at = datetime.utcnow()
        await db.commit()
        return

    agent.last_seen_at = datetime.utcnow()
    await db.commit()

    if message_type == "desktop.inspect.result":
        inspect_handler = getattr(
            dispatcher,
            "handle_inspect_result",
            None,
        )
        if callable(inspect_handler):
            inspect_handler(message)
        return

    if message_type == "desktop.pick.result":
        pick_handler = getattr(
            dispatcher,
            "handle_pick_result",
            None,
        )
        if callable(pick_handler):
            pick_handler(message)
        return

    handler = getattr(dispatcher, "handle_agent_event", None)
    if callable(handler):
        handler_kwargs: dict[str, Any] = {"db": db}
        try:
            parameters = inspect.signature(handler).parameters
        except (TypeError, ValueError):
            parameters = {}
        if "notify_completion" in parameters:
            handler_kwargs["notify_completion"] = False
        await handler(message, **handler_kwargs)
    else:
        await apply_agent_event(db, message)

    if message_type in {"desktop.completed", "desktop.failed"}:
        try:
            task_id = int(message.get("task_id"))
        except (TypeError, ValueError):
            task_id = None
        if task_id is not None:
            result = _result_from_event(message)
            complete = getattr(
                dispatcher,
                "_on_task_completed",
                None,
            )
            if callable(complete):
                completion = complete(task_id, result)
                if inspect.isawaitable(completion):
                    await completion
            else:
                manager_complete = getattr(
                    dispatcher,
                    "complete_task",
                    None,
                )
                if not callable(manager_complete):
                    manager_complete = getattr(
                        manager,
                        "complete_task",
                        None,
                    )
                if callable(manager_complete):
                    completion = manager_complete(task_id, result)
                    if inspect.isawaitable(completion):
                        await completion


@router.websocket("/ws")
async def agent_websocket(
    websocket: WebSocket,
    agent_id: int,
    token: str,
    db: AsyncSession = Depends(get_db),
) -> None:
    result = await db.execute(select(DesktopAgent).where(DesktopAgent.id == agent_id))
    agent = result.scalar_one_or_none()
    if agent is None or not hmac.compare_digest(
        agent.token_hash,
        hash_agent_token(token),
    ):
        await websocket.close(code=INVALID_TOKEN_CLOSE_CODE)
        return
    try:
        registered_environment = normalize_environment(
            agent.environment
        )
    except ValueError:
        registered_environment = ""
    if (
        registered_environment == "vm"
        and (agent.connection_security or "unknown") != "wss"
    ):
        await websocket.close(code=INSECURE_VM_CLOSE_CODE)
        return

    await websocket.accept()
    manager = _connection_manager(websocket)
    dispatcher = _desktop_dispatcher(websocket)
    agent.status = "online"
    agent.last_seen_at = datetime.utcnow()
    await db.commit()
    manager.register(agent_id, websocket)
    await _publish_host_event(
        websocket,
        agent,
        "host_connected",
    )

    heartbeat_state: dict[str, Any] = {
        "last_pong": time.monotonic(),
    }
    heartbeat_task = asyncio.create_task(_heartbeat_loop(websocket, heartbeat_state))

    try:
        while True:
            message = await websocket.receive_json()
            if not isinstance(message, dict):
                continue
            await _process_agent_message(
                db,
                manager,
                dispatcher,
                agent,
                message,
                heartbeat_state,
            )
    except WebSocketDisconnect:
        pass
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.debug(
            "Agent WebSocket %s disconnected unexpectedly",
            agent_id,
            exc_info=True,
        )
    finally:
        heartbeat_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat_task

        is_current_connection = manager.get(agent_id) is websocket
        if is_current_connection:
            try:
                await db.rollback()
                current_agent = await db.get(DesktopAgent, agent_id)
                if current_agent is not None:
                    current_agent.status = "offline"
                    current_agent.last_seen_at = datetime.utcnow()
                    await db.commit()
                    await _publish_host_event(
                        websocket,
                        current_agent,
                        "host_disconnected",
                    )
            except Exception:
                logger.debug(
                    "Failed to update Agent %s offline state",
                    agent_id,
                    exc_info=True,
                )
        manager.unregister(agent_id, websocket)

        if is_current_connection:
            disconnect_handler = getattr(
                dispatcher,
                "handle_agent_disconnect",
                None,
            )
            if callable(disconnect_handler):
                try:
                    await disconnect_handler(agent_id)
                except Exception:
                    logger.debug(
                        "Failed to fail tasks for Agent %s",
                        agent_id,
                        exc_info=True,
                    )


__all__ = [
    "INVALID_TOKEN_CLOSE_CODE",
    "INSECURE_VM_CLOSE_CODE",
    "PING_INTERVAL_SECONDS",
    "PONG_TIMEOUT_SECONDS",
    "router",
]
