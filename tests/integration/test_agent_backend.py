from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
import websockets
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.engine.desktop_dispatcher import DesktopDispatcher
from app.models.agent import DesktopAgent
from app.models.desktop_task import DesktopTask
from app.models.user import User
from app.models.workflow import Workflow
from desktop_agent.action_validator import ActionValidator
from desktop_agent.client import AgentWebSocketClient
from desktop_agent.config import AgentConfig
from desktop_agent.executor import TaskExecutor


pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="Windows-only desktop Agent integration test",
)


def _notepad_pids() -> set[int]:
    result = subprocess.run(
        [
            "tasklist",
            "/FI",
            "IMAGENAME eq Notepad.exe",
            "/FO",
            "CSV",
            "/NH",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
        check=False,
    )
    pids: set[int] = set()
    for line in result.stdout.splitlines():
        fields = [field.strip().strip('"') for field in line.split(",")]
        if (
            len(fields) >= 2
            and fields[0].casefold() == "notepad.exe"
            and fields[1].isdigit()
        ):
            pids.add(int(fields[1]))
    return pids


def _wait_for_notepad_exit(
    existing_pids: set[int],
    *,
    timeout: float = 5,
) -> set[int]:
    deadline = time.monotonic() + timeout
    remaining = _notepad_pids() - existing_pids
    while remaining and time.monotonic() < deadline:
        time.sleep(0.1)
        remaining = _notepad_pids() - existing_pids
    return remaining


class MockAgentServer:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.websocket = None
        self.connected = asyncio.Event()
        self._server = None

    async def start(self) -> None:
        self._server = await websockets.serve(
            self._handler,
            "127.0.0.1",
            0,
        )
        port = self._server.sockets[0].getsockname()[1]
        self.url = f"ws://127.0.0.1:{port}"

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _handler(self, websocket) -> None:
        self.websocket = websocket
        self.connected.set()
        try:
            async for raw_message in websocket:
                if isinstance(raw_message, bytes):
                    raw_message = raw_message.decode("utf-8")
                self.messages.append(json.loads(raw_message))
        except websockets.ConnectionClosed:
            return

    async def send(self, payload: dict[str, Any]) -> None:
        if self.websocket is None:
            raise RuntimeError("Agent is not connected")
        await self.websocket.send(json.dumps(payload))

    async def wait_for(
        self,
        predicate: Callable[[dict[str, Any]], bool],
        *,
        timeout: float = 20,
    ) -> dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            for message in self.messages:
                if predicate(message):
                    return message
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError("Timed out waiting for Agent message")
            await asyncio.sleep(0.05)

    async def wait_for_type(
        self,
        message_type: str,
        *,
        task_id: int | None = None,
        timeout: float = 20,
    ) -> dict[str, Any]:
        def matches(message: dict[str, Any]) -> bool:
            if message.get("type") != message_type:
                return False
            return task_id is None or message.get("task_id") == task_id

        return await self.wait_for(matches, timeout=timeout)


async def _run_agent(
    tmp_path: Path,
    scenario: Callable[
        [MockAgentServer, AgentWebSocketClient],
        Awaitable[Any],
    ],
    *,
    allowed_apps: list[str] | None = None,
) -> Any:
    server = MockAgentServer()
    await server.start()
    config = AgentConfig(
        server_url=server.url,
        agent_id=1,
        token="test-token",
        allowed_apps=list(allowed_apps or []),
        allowed_doc_dirs=[str(tmp_path)],
        log_dir=str(tmp_path / "logs"),
        heartbeat_interval=60,
    )
    config._config_path = tmp_path / "agent-config.json"
    client = AgentWebSocketClient(config)
    client.executor = TaskExecutor(config, client)

    await client.connect()
    run_task = asyncio.create_task(client.run())
    await asyncio.wait_for(server.connected.wait(), timeout=5)
    try:
        return await scenario(server, client)
    finally:
        await client.disconnect()
        run_task.cancel()
        try:
            await run_task
        except (asyncio.CancelledError, websockets.ConnectionClosed):
            pass
        await server.stop()


def test_full_task_lifecycle(tmp_path: Path) -> None:
    notepad_path = shutil.which("notepad.exe")
    if notepad_path is None:
        pytest.skip("notepad.exe is not available")
    pids_before = _notepad_pids()

    async def scenario(
        server: MockAgentServer,
        _client: AgentWebSocketClient,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        task_id = 101
        await server.send(
            {
                "type": "desktop.task",
                "task_id": task_id,
                "execution_log_id": 1,
                "workflow_id": 1,
                "timeout_seconds": 30,
                "actions": [
                    {
                        "type": "launch_app",
                        "params": {
                            "path": notepad_path,
                            "args": [],
                            "wait_for_window": True,
                            "window_timeout": 10,
                        },
                    },
                    {
                        "type": "screenshot",
                        "params": {"name": "notepad-open"},
                    },
                    {
                        "type": "close_app",
                        "params": {
                            "target": "last",
                            "graceful_timeout": 2,
                        },
                    },
                ],
            }
        )

        completed = await server.wait_for_type(
            "desktop.completed",
            task_id=task_id,
            timeout=30,
        )
        progress = [
            message
            for message in server.messages
            if message.get("type") == "desktop.progress"
            and message.get("task_id") == task_id
        ]
        return completed, progress

    completed, progress = asyncio.run(
        _run_agent(
            tmp_path,
            scenario,
            allowed_apps=[notepad_path],
        )
    )

    new_notepad_pids = _wait_for_notepad_exit(pids_before)
    try:
        assert not new_notepad_pids, json.dumps(
            {
                "new_pids": sorted(new_notepad_pids),
                "completed": completed,
                "progress": progress,
            },
            ensure_ascii=False,
        )
    finally:
        for pid in new_notepad_pids:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                check=False,
            )

    assert completed["success"] is True
    assert [message["step_index"] for message in progress] == [0, 1, 2]
    assert progress[0]["status"] == "success"
    assert Path(completed["screenshots"][0]).is_file()

    audit_files = list((tmp_path / "logs").glob("audit_*.jsonl"))
    assert len(audit_files) == 1
    records = [
        json.loads(line)
        for line in audit_files[0].read_text(encoding="utf-8").splitlines()
    ]
    assert [record["action_type"] for record in records] == [
        "launch_app",
        "launch_app",
        "screenshot",
        "screenshot",
        "close_app",
        "close_app",
    ]
    assert all(record["status"] != "failed" for record in records)


async def _database() -> tuple[AsyncSession, object]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    session = session_factory()
    session.info["session_factory"] = session_factory
    return session, engine


async def _seed_dispatch_records(
    session: AsyncSession,
) -> Workflow:
    user = User(
        id=1,
        username="integration-user",
        email="integration@example.com",
        hashed_password="hash",
    )
    agent = DesktopAgent(
        id=1,
        user_id=user.id,
        name="DESKTOP-01",
        platform="windows",
        version="0.1.0",
        capabilities=["window"],
        status="online",
        token_hash="hash",
    )
    workflow = Workflow(
        id=1,
        user_id=user.id,
        name="Desktop integration workflow",
        natural_language_input="Launch Notepad",
        structured_plan={
            "steps": [
                {
                    "step_id": 1,
                    "action": "launch_app",
                    "params": {"path": "notepad.exe", "args": []},
                }
            ]
        },
        executor_type="windows",
        required_capabilities=["window"],
    )
    session.add_all([user, agent, workflow])
    await session.commit()
    await session.refresh(workflow)
    return workflow


class SilentConnectionManager:
    def __init__(self) -> None:
        self.sent: list[tuple[int, dict[str, Any]]] = []

    def get_online_agents(self) -> list[int]:
        return [1]

    async def send_task(
        self,
        agent_id: int,
        payload: dict[str, Any],
    ) -> None:
        self.sent.append((agent_id, payload))


def test_task_timeout() -> None:
    async def scenario() -> tuple[bool, str | None, str]:
        session, engine = await _database()
        try:
            workflow = await _seed_dispatch_records(session)
            manager = SilentConnectionManager()
            dispatcher = DesktopDispatcher(
                manager,
                session_factory=session.info["session_factory"],
            )
            dispatcher.timeout = 0.02

            result = await dispatcher.dispatch(workflow)
            task = (await session.execute(select(DesktopTask))).scalar_one()
            return result.success, result.error, task.status
        finally:
            await session.close()
            await engine.dispose()

    success, error, task_status = asyncio.run(scenario())

    assert success is False
    assert error is not None and "timed out" in error.lower()
    assert task_status == "failed"


def test_cancel_task() -> None:
    async def scenario() -> tuple[bool, str, list[dict[str, Any]]]:
        session, engine = await _database()
        try:
            workflow = await _seed_dispatch_records(session)
            manager = SilentConnectionManager()
            dispatcher = DesktopDispatcher(
                manager,
                session_factory=session.info["session_factory"],
            )

            dispatch_task = asyncio.create_task(dispatcher.dispatch(workflow))
            while not manager.sent:
                await asyncio.sleep(0)

            task_id = manager.sent[0][1]["task_id"]
            await dispatcher.cancel(task_id)
            result = await dispatch_task
            task = (await session.execute(select(DesktopTask))).scalar_one()
            cancel_messages = [
                payload
                for _agent_id, payload in manager.sent
                if payload.get("type") == "desktop.cancel"
            ]
            return result.success, task.status, cancel_messages
        finally:
            await session.close()
            await engine.dispose()

    success, task_status, cancel_messages = asyncio.run(scenario())

    assert success is False
    assert task_status == "cancelled"
    assert len(cancel_messages) == 1


def test_config_hot_update(tmp_path: Path) -> None:
    notepad_path = shutil.which("notepad.exe")
    if notepad_path is None:
        pytest.skip("notepad.exe is not available")

    old_app = r"C:\Windows\System32\mspaint.exe"
    pids_before = _notepad_pids()

    async def scenario(
        server: MockAgentServer,
        client: AgentWebSocketClient,
    ) -> tuple[
        dict[str, Any],
        dict[str, Any],
        list[list[str]],
        list[bool],
    ]:
        await server.send(
            {
                "type": "config.update",
                "config": {
                    "allowed_apps": [notepad_path],
                    "allowed_doc_dirs": [str(tmp_path)],
                },
            }
        )
        deadline = asyncio.get_running_loop().time() + 5
        while client.config.allowed_apps != [notepad_path]:
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError("Agent config was not hot-updated")
            await asyncio.sleep(0.02)

        await server.send(
            {
                "type": "desktop.task",
                "task_id": 201,
                "actions": [
                    {
                        "type": "launch_app",
                        "params": {
                            "path": notepad_path,
                            "wait_for_window": True,
                            "window_timeout": 10,
                        },
                    },
                    {
                        "type": "close_app",
                        "params": {
                            "target": "last",
                            "graceful_timeout": 1,
                        },
                    },
                ],
            }
        )
        new_allowed = await server.wait_for_type(
            "desktop.completed",
            task_id=201,
            timeout=15,
        )

        await server.send(
            {
                "type": "desktop.task",
                "task_id": 202,
                "actions": [
                    {
                        "type": "launch_app",
                        "params": {
                            "path": old_app,
                            "wait_for_window": False,
                        },
                    }
                ],
            }
        )
        old_blocked = await server.wait_for_type(
            "desktop.failed",
            task_id=202,
            timeout=5,
        )

        old_validation = ActionValidator.validate(
            {
                "type": "launch_app",
                "params": {"path": old_app},
            },
            client.config,
        )
        new_validation = ActionValidator.validate(
            {
                "type": "launch_app",
                "params": {"path": notepad_path},
            },
            client.config,
        )
        return (
            new_allowed,
            old_blocked,
            [client.config.allowed_apps, client.config.allowed_doc_dirs],
            [old_validation.allowed, new_validation.allowed],
        )

    new_allowed, old_blocked, config_lists, validation = asyncio.run(
        _run_agent(
            tmp_path,
            scenario,
            allowed_apps=[old_app],
        )
    )

    new_notepad_pids = _wait_for_notepad_exit(pids_before)
    try:
        assert not new_notepad_pids
    finally:
        for pid in new_notepad_pids:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                check=False,
            )

    assert new_allowed["success"] is True
    assert old_blocked["success"] is False
    assert "not allowlisted" in old_blocked["error"].lower()
    assert config_lists == [[notepad_path], [str(tmp_path)]]
    assert validation == [False, True]


def test_inspect_protocol_round_trip(tmp_path: Path) -> None:
    async def scenario(
        server: MockAgentServer,
        _client: AgentWebSocketClient,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        await server.send(
            {
                "type": "desktop.inspect",
                "request_id": "inventory",
                "max_windows": 10,
                "max_elements": 0,
            }
        )
        inventory = await server.wait_for(
            lambda message: (
                message.get("type") == "desktop.inspect.result"
                and message.get("request_id") == "inventory"
            ),
            timeout=15,
        )

        detail: dict[str, Any] | None = None
        handles = [
            window.get("handle")
            for window in inventory.get("windows", [])
            if window.get("title")
        ]
        for handle in handles[:5]:
            request_id = f"inspect-{handle}"
            await server.send(
                {
                    "type": "desktop.inspect",
                    "request_id": request_id,
                    "window": {"handle": handle},
                    "max_elements": 5,
                    "max_depth": 2,
                }
            )
            result = await server.wait_for(
                lambda message, expected=request_id: (
                    message.get("type") == "desktop.inspect.result"
                    and message.get("request_id") == expected
                ),
                timeout=15,
            )
            if result.get("target"):
                detail = result
                break
        return inventory, detail

    inventory, detail = asyncio.run(_run_agent(tmp_path, scenario))

    assert inventory["success"] is True
    assert isinstance(inventory["windows"], list)
    assert inventory["elements"] == []
    if not inventory["windows"]:
        pytest.skip("No visible windows available for inspection")
    assert detail is not None
    assert detail["success"] is True
    assert detail["target"]
    assert isinstance(detail["elements"], list)
