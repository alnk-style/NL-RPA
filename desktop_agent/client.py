from __future__ import annotations

import asyncio
import inspect
import json
import logging
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

import websockets

from desktop_agent.config import AgentConfig
from desktop_agent.executor import TaskExecutor
from desktop_agent.inspector import inspect_desktop
from desktop_agent.pick import pick_element

logger = logging.getLogger(__name__)


class AgentWebSocketClient:
    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self.executor: TaskExecutor | None = None
        self.websocket: Any | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._current_task: asyncio.Task[None] | None = None
        self._send_lock = asyncio.Lock()
        self._closing = False
        self.inspect_tasks: set[asyncio.Task[None]] = set()

    @property
    def uri(self) -> str:
        server_url = self.config.server_url.strip()
        if "://" not in server_url:
            server_url = f"ws://{server_url}"

        parsed = urlsplit(server_url)
        scheme = {
            "http": "ws",
            "https": "wss",
        }.get(parsed.scheme.lower(), parsed.scheme.lower())
        if scheme not in {"ws", "wss"}:
            raise ValueError("server_url must use http, https, ws, or wss")

        base_path = parsed.path.rstrip("/")
        path = f"{base_path}/api/agents/ws"
        query = urlencode(
            {
                "agent_id": self.config.agent_id,
                "token": self.config.token,
            }
        )
        return urlunsplit((scheme, parsed.netloc, path, query, ""))

    async def connect(self) -> None:
        if self.websocket is not None:
            return
        self._closing = False
        self.websocket = await websockets.connect(self.uri)
        await self.send_event(self.config.hello_payload())
        self._heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(),
            name="desktop-agent-heartbeat",
        )

    async def disconnect(self) -> None:
        self._closing = True
        inspect_tasks = list(self.inspect_tasks)
        self.inspect_tasks.clear()
        for task in inspect_tasks:
            task.cancel()
        for task in inspect_tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass

        heartbeat_task = self._heartbeat_task
        self._heartbeat_task = None
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass

        websocket = self.websocket
        self.websocket = None
        if websocket is not None:
            close_result = websocket.close()
            if inspect.isawaitable(close_result):
                await close_result

        current_task = self._current_task
        self._current_task = None
        if current_task is not None and not current_task.done():
            current_task.cancel()
            try:
                await current_task
            except asyncio.CancelledError:
                pass

    async def run(self) -> None:
        await self._message_loop()

    async def _heartbeat_loop(self) -> None:
        while not self._closing:
            await asyncio.sleep(self.config.heartbeat_interval)
            await self.send_event({"type": "ping"})

    async def _message_loop(self) -> None:
        websocket = self.websocket
        if websocket is None:
            raise RuntimeError("Agent WebSocket is not connected")

        async for raw_message in websocket:
            if isinstance(raw_message, dict):
                message = raw_message
            elif isinstance(raw_message, bytes):
                try:
                    message = json.loads(raw_message.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    logger.warning("Ignoring invalid Agent message")
                    continue
            else:
                try:
                    message = json.loads(raw_message)
                except (TypeError, json.JSONDecodeError):
                    logger.warning("Ignoring invalid Agent message")
                    continue
            if not isinstance(message, dict):
                continue
            await self._dispatch_message(message)

        current_task = self._current_task
        if current_task is not None and not current_task.done():
            await current_task

    async def _dispatch_message(self, message: dict[str, Any]) -> None:
        message_type = str(message.get("type", ""))
        if message_type == "desktop.task":
            self._start_task(message)
            return
        if message_type == "desktop.cancel":
            if self.executor is not None:
                await self.executor.cancel()
            return
        if message_type == "desktop.inspect":
            task = asyncio.create_task(
                self._handle_inspect(message),
                name=f"desktop-inspect-{message.get('request_id')}",
            )
            self.inspect_tasks.add(task)
            task.add_done_callback(self.inspect_tasks.discard)
            return
        if message_type == "desktop.pick":
            task = asyncio.create_task(
                self._handle_pick(message),
                name=f"desktop-pick-{message.get('request_id')}",
            )
            self.inspect_tasks.add(task)
            task.add_done_callback(self.inspect_tasks.discard)
            return
        if message_type == "config.update":
            self._apply_config_update(message)
            return
        if message_type == "ping":
            await self.send_event({"type": "pong"})
            return

    async def _handle_inspect(self, message: dict[str, Any]) -> None:
        request_id = message.get("request_id")
        try:
            raw_result = await asyncio.to_thread(inspect_desktop, message)
            result = raw_result if isinstance(raw_result, dict) else {}
            payload = {
                "success": bool(result.get("success", True)),
                "windows": list(result.get("windows") or []),
                "target": result.get("target"),
                "elements": list(result.get("elements") or []),
                "error": result.get("error"),
            }
        except Exception as exc:
            payload = {
                "success": False,
                "windows": [],
                "target": None,
                "elements": [],
                "error": str(exc) or exc.__class__.__name__,
            }

        try:
            await self.send_event(
                {
                    "type": "desktop.inspect.result",
                    "request_id": request_id,
                    **payload,
                }
            )
        except Exception:
            logger.debug(
                "Could not send desktop.inspect.result",
                exc_info=True,
            )

    async def _handle_pick(self, message: dict[str, Any]) -> None:
        request_id = message.get("request_id")
        try:
            raw_result = await asyncio.to_thread(pick_element, message)
            result = raw_result if isinstance(raw_result, dict) else {}
            payload = {
                "success": bool(result.get("success", False)),
                "element": result.get("element"),
                "window": result.get("window"),
                "point": result.get("point"),
                "error": result.get("error"),
            }
        except Exception as exc:
            payload = {
                "success": False,
                "element": None,
                "window": None,
                "point": None,
                "error": str(exc) or exc.__class__.__name__,
            }

        try:
            await self.send_event(
                {
                    "type": "desktop.pick.result",
                    "request_id": request_id,
                    **payload,
                }
            )
        except Exception:
            logger.debug(
                "Could not send desktop.pick.result",
                exc_info=True,
            )

    def _start_task(self, message: dict[str, Any]) -> None:
        if self.executor is None:
            asyncio.create_task(
                self.send_event(
                    {
                        "type": "desktop.failed",
                        "task_id": message.get("task_id"),
                        "success": False,
                        "error": "Task executor is not configured",
                    }
                )
            )
            return

        if self._current_task is not None and not self._current_task.done():
            asyncio.create_task(
                self.send_event(
                    {
                        "type": "desktop.failed",
                        "task_id": message.get("task_id"),
                        "success": False,
                        "error": "Agent is already executing a task",
                    }
                )
            )
            return

        self._current_task = asyncio.create_task(
            self.executor.execute(message),
            name=f"desktop-task-{message.get('task_id')}",
        )

    def _apply_config_update(self, message: dict[str, Any]) -> None:
        payload = message.get("config")
        if payload is None:
            payload = message.get("data")
        if not isinstance(payload, dict):
            payload = message

        allowed_apps = payload.get("allowed_apps")
        allowed_doc_dirs = payload.get("allowed_doc_dirs")
        if allowed_apps is None and allowed_doc_dirs is None:
            return

        self.config.update_allowlists(
            allowed_apps=allowed_apps,
            allowed_doc_dirs=allowed_doc_dirs,
        )

    async def send_event(self, event: dict) -> None:
        websocket = self.websocket
        if websocket is None:
            raise RuntimeError("Agent WebSocket is not connected")
        payload = json.dumps(event, ensure_ascii=False, default=str)
        async with self._send_lock:
            await websocket.send(payload)


__all__ = ["AgentWebSocketClient"]
