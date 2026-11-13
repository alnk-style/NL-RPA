from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.engine.desktop_dispatcher import DesktopDispatcher
from app.engine.hybrid_executor import HybridExecutor
from app.engine.runner import ExecutionResult
from app.models.agent import DesktopAgent
from app.models.execution import ExecutionLog
from app.models.workflow import Workflow
from app.services.checkpoint_service import CheckpointService


async def _database():
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
    return session_factory, engine


def _workflow() -> Workflow:
    return Workflow(
        id=1,
        user_id=1,
        name="pinned-desktop",
        natural_language_input="desktop",
        structured_plan={
            "steps": [
                {
                    "step_id": 1,
                    "executor": "windows",
                    "action": "launch_app",
                    "host_requirement": "interactive_desktop",
                    "params": {},
                },
                {
                    "step_id": 2,
                    "executor": "windows",
                    "action": "read_window_text",
                    "host_requirement": "interactive_desktop",
                    "params": {},
                },
            ]
        },
        execution_policy={
            "mode": "interactive_preferred",
            "host_policy": "local_then_isolated",
        },
        executor_type="windows",
        required_capabilities=["interactive_desktop"],
    )


class FakeConnectionManager:
    def __init__(self, online_ids: list[int]) -> None:
        self.online_ids = list(online_ids)
        self.sessions = {
            agent_id: f"session-{agent_id}"
            for agent_id in online_ids
        }
        self.sent: list[tuple[int, dict[str, Any]]] = []

    def get_online_agents(self) -> list[int]:
        return list(self.online_ids)

    def get_session_id(self, agent_id: int) -> str | None:
        return self.sessions.get(int(agent_id))

    async def send_task(
        self,
        agent_id: int,
        task_payload: dict[str, Any],
    ) -> None:
        self.sent.append((agent_id, task_payload))


def _completion(task_id: int) -> dict[str, Any]:
    return {
        "type": "desktop.completed",
        "task_id": task_id,
        "success": True,
        "output": "ok",
        "screenshots": [],
        "step_logs": [
            {"step_index": 0, "status": "success"},
            {"step_index": 1, "status": "success"},
        ],
        "duration_seconds": 0.01,
    }


def test_first_desktop_step_pins_host_and_session() -> None:
    async def scenario() -> tuple[ExecutionResult, ExecutionLog]:
        session_factory, engine = await _database()
        try:
            async with session_factory() as session:
                session.add_all(
                    [
                        DesktopAgent(
                            id=1,
                            user_id=1,
                            name="local",
                            platform="windows",
                            version="0.2.0",
                            environment="local",
                            capabilities=["interactive_desktop"],
                            status="online",
                            token_hash="hash-1",
                        ),
                        DesktopAgent(
                            id=2,
                            user_id=1,
                            name="vm",
                            platform="windows",
                            version="0.2.0",
                            environment="vm",
                            capabilities=[
                                "interactive_desktop",
                                "isolated_desktop",
                            ],
                            status="online",
                            token_hash="hash-2",
                        ),
                    ]
                )
                await session.commit()

            manager = FakeConnectionManager([1, 2])
            dispatcher = DesktopDispatcher(
                manager,
                session_factory=session_factory,
            )
            original_send = manager.send_task

            async def send_task(
                agent_id: int,
                payload: dict[str, Any],
            ) -> None:
                await original_send(agent_id, payload)
                dispatcher.complete_task(
                    payload["task_id"],
                    _completion(payload["task_id"]),
                )

            manager.send_task = send_task  # type: ignore[method-assign]
            result = await dispatcher.dispatch(
                _workflow(),
                {"execution_policy": _workflow().execution_policy},
            )
            async with session_factory() as session:
                execution = (
                    await session.execute(
                        ExecutionLog.__table__.select()
                    )
                ).mappings().one()
                return result, ExecutionLog(**execution)
        finally:
            await engine.dispose()

    result, execution = asyncio.run(scenario())
    assert result.success is True
    assert execution.host_id == 1
    assert execution.host_environment == "local"
    assert execution.host_bound is True
    assert execution.host_affinity_hash
    assert execution.host_affinity_hash != "session-1"


def test_bound_host_loss_does_not_fallback_or_dispatch() -> None:
    async def scenario() -> tuple[ExecutionResult, FakeConnectionManager]:
        session_factory, engine = await _database()
        try:
            async with session_factory() as session:
                session.add_all(
                    [
                        DesktopAgent(
                            id=1,
                            user_id=1,
                            name="bound",
                            platform="windows",
                            version="0.2.0",
                            environment="local",
                            capabilities=["interactive_desktop"],
                            status="offline",
                            token_hash="hash-1",
                        ),
                        DesktopAgent(
                            id=2,
                            user_id=1,
                            name="fallback",
                            platform="windows",
                            version="0.2.0",
                            environment="local",
                            capabilities=["interactive_desktop"],
                            status="online",
                            token_hash="hash-2",
                        ),
                    ]
                )
                await session.commit()
            manager = FakeConnectionManager([2])
            dispatcher = DesktopDispatcher(
                manager,
                session_factory=session_factory,
            )
            result = await dispatcher.dispatch(
                _workflow(),
                {
                    "execution_policy": _workflow().execution_policy,
                    "host_id": 1,
                    "host_affinity_hash": "old-fingerprint",
                },
            )
            return result, manager
        finally:
            await engine.dispose()

    result, manager = asyncio.run(scenario())
    assert result.success is False
    assert (result.details or {})["host_error_code"] == "host_disconnected"
    assert manager.sent == []


def test_checkpoint_persists_host_affinity_and_session() -> None:
    execution = SimpleNamespace(
        id=5,
        workflow_version_id=7,
        host_id=2,
        host_environment="vm",
        host_bound=True,
        host_affinity_hash="affinity-hash",
        browser_profile_id=3,
        artifacts=[],
        step_logs=[],
    )
    result = ExecutionResult(
        success=False,
        output="",
        error="host lost",
        screenshots=[],
        step_logs=[],
        duration_seconds=0.1,
        details={"failed_step": {"step_id": 4}},
    )
    checkpoint = CheckpointService().build(
        execution,
        result,
        next_step_index=3,
    )
    assert checkpoint["host_id"] == 2
    assert checkpoint["host_environment"] == "vm"
    assert checkpoint["host_bound"] is True
    assert checkpoint["host_affinity_hash"] == "affinity-hash"


def test_hybrid_reuses_pinned_host_for_later_desktop_steps() -> None:
    async def scenario() -> list[int]:
        session_factory, engine = await _database()
        try:
            async with session_factory() as session:
                session.add_all(
                    [
                        DesktopAgent(
                            id=1,
                            user_id=1,
                            name="local-a",
                            platform="windows",
                            version="0.2.0",
                            environment="local",
                            capabilities=["interactive_desktop"],
                            status="online",
                            token_hash="hash-1",
                        ),
                        DesktopAgent(
                            id=2,
                            user_id=1,
                            name="local-b",
                            platform="windows",
                            version="0.2.0",
                            environment="local",
                            capabilities=["interactive_desktop"],
                            status="online",
                            token_hash="hash-2",
                        ),
                    ]
                )
                await session.commit()
            manager = FakeConnectionManager([1, 2])
            dispatcher = DesktopDispatcher(
                manager,
                session_factory=session_factory,
            )
            original_send = manager.send_task

            async def send_task(
                agent_id: int,
                payload: dict[str, Any],
            ) -> None:
                await original_send(agent_id, payload)
                dispatcher.complete_task(
                    payload["task_id"],
                    {
                        **_completion(payload["task_id"]),
                        "step_logs": [
                            {
                                "step_index": 0,
                                "status": "success",
                            }
                        ],
                    },
                )

            manager.send_task = send_task  # type: ignore[method-assign]
            executor = HybridExecutor(
                SimpleNamespace(web_executor=None),
                dispatcher,
                session_factory=session_factory,
            )
            result = await executor.execute(_workflow())
            assert result.success is True
            return [
                agent_id
                for agent_id, payload in manager.sent
                if payload.get("type") == "desktop.task"
            ]
        finally:
            await engine.dispose()

    agent_ids = asyncio.run(scenario())
    assert agent_ids == [1, 1]


def test_concurrent_dispatch_does_not_send_two_tasks_to_same_agent() -> None:
    async def scenario():
        session_factory, engine = await _database()
        try:
            async with session_factory() as session:
                session.add(
                    DesktopAgent(
                        id=1,
                        user_id=1,
                        name="only-host",
                        platform="windows",
                        version="0.2.0",
                        environment="local",
                        capabilities=["interactive_desktop"],
                        status="online",
                        token_hash="hash",
                    )
                )
                await session.commit()
            manager = FakeConnectionManager([1])
            dispatcher = DesktopDispatcher(
                manager,
                session_factory=session_factory,
            )
            first = asyncio.create_task(dispatcher.dispatch(_workflow()))
            while not manager.sent:
                await asyncio.sleep(0)
            second = await dispatcher.dispatch(
                _workflow(),
                {"execution_policy": _workflow().execution_policy},
            )
            first_task_id = manager.sent[0][1]["task_id"]
            dispatcher.complete_task(
                first_task_id,
                _completion(first_task_id),
            )
            first_result = await first
            return first_result, second, manager
        finally:
            await engine.dispose()

    first_result, second_result, manager = asyncio.run(scenario())
    assert first_result.success is True
    assert second_result.success is False
    assert (second_result.details or {})["host_error_code"] == "host_busy"
    assert len([
        payload
        for _agent_id, payload in manager.sent
        if payload.get("type") == "desktop.task"
    ]) == 1
