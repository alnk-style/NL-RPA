from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.engine.desktop_dispatcher import DesktopDispatcher
from app.engine.runner import ExecutionResult
from app.models.agent import DesktopAgent
from app.models.desktop_task import DesktopTask
from app.models.execution import ExecutionLog
from app.models.workflow import Workflow


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


async def _seed_workflow(
    session: AsyncSession,
    *,
    required_capabilities: list[str] | None = None,
) -> Workflow:
    workflow = Workflow(
        id=1,
        user_id=1,
        name="Desktop workflow",
        natural_language_input="Launch Notepad",
        structured_plan={
            "steps": [
                {
                    "step_id": 1,
                    "action": "launch_app",
                    "params": {
                        "path": r"C:\Windows\System32\notepad.exe",
                        "args": [],
                    },
                },
                {
                    "step_id": 2,
                    "action": "screenshot",
                    "params": {"name": "notepad-open"},
                },
            ]
        },
        executor_type="windows",
        required_capabilities=required_capabilities or [],
    )
    session.add(workflow)
    await session.commit()
    await session.refresh(workflow)
    return workflow


async def _seed_agent(
    session: AsyncSession,
    *,
    agent_id: int = 1,
    capabilities: list[str] | None = None,
) -> DesktopAgent:
    agent = DesktopAgent(
        id=agent_id,
        user_id=1,
        name=f"DESKTOP-{agent_id:02d}",
        platform="windows",
        version="0.1.0",
        capabilities=capabilities or [],
        status="online",
        token_hash="hash",
    )
    session.add(agent)
    await session.commit()
    await session.refresh(agent)
    return agent


class FakeConnectionManager:
    def __init__(self, online_ids: list[int] | None = None) -> None:
        self.online_ids = list(online_ids or [])
        self.sent: list[tuple[int, dict[str, Any]]] = []
        self.dispatcher: DesktopDispatcher | None = None

    def get_online_agents(self) -> list[int]:
        return list(self.online_ids)

    async def send_task(
        self,
        agent_id: int,
        task_payload: dict[str, Any],
    ) -> None:
        self.sent.append((agent_id, task_payload))


def _completion_payload(task_id: int) -> dict[str, Any]:
    return {
        "type": "desktop.completed",
        "task_id": task_id,
        "success": True,
        "output": "Notepad opened",
        "error": None,
        "screenshots": ["logs/task/step-1.png"],
        "step_logs": [{"step_index": 0, "status": "success"}],
        "duration_seconds": 0.25,
    }


def test_dispatch_no_agent_online() -> None:
    async def scenario() -> tuple[ExecutionResult, int, int]:
        session, engine = await _database()
        try:
            workflow = await _seed_workflow(session, required_capabilities=["window"])
            manager = FakeConnectionManager()
            dispatcher = DesktopDispatcher(
                manager,
                session_factory=session.info["session_factory"],
            )

            result = await dispatcher.dispatch(workflow)

            tasks = (
                (await session.execute(DesktopTask.__table__.select())).mappings().all()
            )
            logs = (
                (await session.execute(ExecutionLog.__table__.select()))
                .mappings()
                .all()
            )
            return result, len(tasks), len(logs)
        finally:
            await session.close()
            await engine.dispose()

    result, task_count, log_count = asyncio.run(scenario())

    assert result.success is False
    assert result.error == "No available desktop agent"
    assert task_count == 1
    assert log_count == 1


def test_dispatch_success() -> None:
    async def scenario() -> tuple[ExecutionResult, DesktopTask, ExecutionLog, dict]:
        session, engine = await _database()
        try:
            workflow = await _seed_workflow(
                session,
                required_capabilities=["window"],
            )
            await _seed_agent(session, capabilities=["window"])
            manager = FakeConnectionManager([1])
            dispatcher = DesktopDispatcher(
                manager,
                session_factory=session.info["session_factory"],
            )
            manager.dispatcher = dispatcher

            original_send = manager.send_task

            async def send_task(agent_id: int, payload: dict[str, Any]) -> None:
                await original_send(agent_id, payload)
                dispatcher.complete_task(
                    payload["task_id"],
                    _completion_payload(payload["task_id"]),
                )

            manager.send_task = send_task  # type: ignore[method-assign]
            result = await dispatcher.dispatch(workflow)

            task = (
                (await session.execute(DesktopTask.__table__.select())).mappings().one()
            )
            log = (
                (await session.execute(ExecutionLog.__table__.select()))
                .mappings()
                .one()
            )
            return result, DesktopTask(**task), ExecutionLog(**log), manager.sent[0][1]
        finally:
            await session.close()
            await engine.dispose()

    result, task, log, payload = asyncio.run(scenario())

    assert result.success is True
    assert result.output == "Notepad opened"
    assert task.status == "success"
    assert log.status == "success"
    assert payload["type"] == "desktop.task"
    assert payload["execution_log_id"] == log.id
    assert payload["workflow_id"] == 1
    assert payload["actions"][0]["type"] == "launch_app"


def test_dispatch_waits_for_registered_completion_event() -> None:
    async def scenario() -> tuple[ExecutionResult, DesktopTask]:
        session, engine = await _database()
        try:
            workflow = await _seed_workflow(
                session,
                required_capabilities=["window"],
            )
            await _seed_agent(session, capabilities=["window"])
            manager = FakeConnectionManager([1])
            dispatcher = DesktopDispatcher(
                manager,
                session_factory=session.info["session_factory"],
            )

            dispatch_task = asyncio.create_task(dispatcher.dispatch(workflow))
            while not manager.sent:
                await asyncio.sleep(0)

            task_id = manager.sent[0][1]["task_id"]
            assert task_id in dispatcher._task_completion_events

            dispatcher._on_task_completed(
                task_id,
                _completion_payload(task_id),
            )
            result = await dispatch_task

            task = (
                (await session.execute(DesktopTask.__table__.select())).mappings().one()
            )
            return result, DesktopTask(**task)
        finally:
            await session.close()
            await engine.dispose()

    result, task = asyncio.run(scenario())

    assert result.success is True
    assert result.output == "Notepad opened"
    assert task.status == "success"


def test_dispatch_timeout() -> None:
    async def scenario() -> tuple[
        ExecutionResult,
        DesktopTask,
        ExecutionLog,
        list[dict[str, Any]],
    ]:
        session, engine = await _database()
        try:
            workflow = await _seed_workflow(
                session,
                required_capabilities=["window"],
            )
            await _seed_agent(session, capabilities=["window"])
            manager = FakeConnectionManager([1])
            dispatcher = DesktopDispatcher(
                manager,
                session_factory=session.info["session_factory"],
            )
            dispatcher.timeout = 0.01

            result = await dispatcher.dispatch(workflow)

            task = (
                (await session.execute(DesktopTask.__table__.select())).mappings().one()
            )
            log = (
                (await session.execute(ExecutionLog.__table__.select()))
                .mappings()
                .one()
            )
            cancel_payloads = [
                payload
                for _agent_id, payload in manager.sent
                if payload.get("type") == "desktop.cancel"
            ]
            return (
                result,
                DesktopTask(**task),
                ExecutionLog(**log),
                cancel_payloads,
            )
        finally:
            await session.close()
            await engine.dispose()

    result, task, log, cancel_payloads = asyncio.run(scenario())

    assert result.success is False
    assert result.error is not None
    assert "timed out" in result.error.lower()
    assert task.status == "failed"
    assert log.status == "failed"
    assert len(cancel_payloads) == 1
    assert cancel_payloads[0]["task_id"] == task.id


def test_cancel_task() -> None:
    async def scenario() -> tuple[ExecutionResult, DesktopTask, list[dict[str, Any]]]:
        session, engine = await _database()
        try:
            workflow = await _seed_workflow(
                session,
                required_capabilities=["window"],
            )
            await _seed_agent(session, capabilities=["window"])
            manager = FakeConnectionManager([1])
            dispatcher = DesktopDispatcher(
                manager,
                session_factory=session.info["session_factory"],
            )

            dispatch_task = asyncio.create_task(dispatcher.dispatch(workflow))
            await asyncio.sleep(0)
            while not manager.sent:
                await asyncio.sleep(0)

            task_id = manager.sent[0][1]["task_id"]
            await dispatcher.cancel(task_id)
            result = await dispatch_task

            task = (
                (await session.execute(DesktopTask.__table__.select())).mappings().one()
            )
            cancel_messages = [
                payload
                for _agent_id, payload in manager.sent
                if payload.get("type") == "desktop.cancel"
            ]
            return result, DesktopTask(**task), cancel_messages
        finally:
            await session.close()
            await engine.dispose()

    result, task, cancel_messages = asyncio.run(scenario())

    assert result.success is False
    assert task.status == "cancelled"
    assert len(cancel_messages) == 1
    assert cancel_messages[0]["task_id"] == task.id


def test_convert_plan_to_actions() -> None:
    manager = FakeConnectionManager()
    dispatcher = DesktopDispatcher(manager)

    actions = dispatcher._convert_plan_to_actions(
        {
            "steps": [
                {
                    "action": "launch_app",
                    "params": {
                        "path": r"C:\Windows\System32\notepad.exe",
                        "args": [],
                    },
                },
                {
                    "action": "screenshot",
                    "params": {"name": "notepad-open"},
                },
            ]
        }
    )

    assert actions == [
        {
            "type": "launch_app",
            "params": {
                "path": r"C:\Windows\System32\notepad.exe",
                "args": [],
            },
        },
        {
            "type": "screenshot",
            "params": {"name": "notepad-open"},
        },
    ]


def test_inspect_round_trip() -> None:
    async def scenario() -> tuple[dict[str, Any], dict[str, Any]]:
        manager = FakeConnectionManager([1])
        dispatcher = DesktopDispatcher(manager)
        original_send = manager.send_task

        async def send_task(
            agent_id: int,
            payload: dict[str, Any],
        ) -> None:
            await original_send(agent_id, payload)
            dispatcher.handle_inspect_result(
                {
                    "type": "desktop.inspect.result",
                    "request_id": payload["request_id"],
                    "success": True,
                    "windows": [{"handle": 10, "title": "记事本"}],
                    "target": None,
                    "elements": [],
                    "error": None,
                }
            )

        manager.send_task = send_task  # type: ignore[method-assign]
        result = await dispatcher.inspect(
            {"max_elements": 0},
            agent_id=1,
            timeout=2,
        )
        return result, manager.sent[0][1]

    result, sent = asyncio.run(scenario())

    assert sent["type"] == "desktop.inspect"
    assert sent["request_id"]
    assert result["success"] is True
    assert result["windows"] == [{"handle": 10, "title": "记事本"}]


def test_inspect_without_agent_reports_error() -> None:
    async def scenario() -> dict[str, Any]:
        dispatcher = DesktopDispatcher(FakeConnectionManager())
        return await dispatcher.inspect({}, timeout=0.1)

    result = asyncio.run(scenario())

    assert result["success"] is False
    assert "agent" in str(result["error"]).lower()


def test_inspect_timeout_reports_error() -> None:
    async def scenario() -> dict[str, Any]:
        manager = FakeConnectionManager([1])
        dispatcher = DesktopDispatcher(manager)
        return await dispatcher.inspect({}, agent_id=1, timeout=0.05)

    result = asyncio.run(scenario())

    assert result["success"] is False
    error = str(result["error"]).lower()
    assert "超时" in str(result["error"]) or "timed out" in error


def test_dispatch_includes_step_offset() -> None:
    async def scenario() -> dict[str, Any]:
        session, engine = await _database()
        try:
            workflow = await _seed_workflow(
                session,
                required_capabilities=["window"],
            )
            await _seed_agent(session, capabilities=["window"])
            manager = FakeConnectionManager([1])
            dispatcher = DesktopDispatcher(
                manager,
                session_factory=session.info["session_factory"],
            )
            original_send = manager.send_task

            async def send_task(
                agent_id: int,
                payload: dict[str, Any],
            ) -> None:
                await original_send(agent_id, payload)
                dispatcher.complete_task(
                    payload["task_id"],
                    _completion_payload(payload["task_id"]),
                )

            manager.send_task = send_task  # type: ignore[method-assign]
            await dispatcher.dispatch(workflow, {"step_offset": 2})
            return manager.sent[0][1]
        finally:
            await session.close()
            await engine.dispose()

    payload = asyncio.run(scenario())

    assert payload["step_offset"] == 2


def test_failed_event_details_are_preserved() -> None:
    result = DesktopDispatcher._result_from_event(
        {
            "type": "desktop.failed",
            "task_id": 1,
            "success": False,
            "error": "No matching UI Automation element",
            "failed_step": {
                "step_index": 1,
                "action_type": "click_element",
                "error": "No matching UI Automation element",
                "details": {"candidates": ["保存(Button)"]},
            },
        }
    )

    assert result["success"] is False
    assert result["details"]["failed_step"]["details"]["candidates"] == [
        "保存(Button)"
    ]


def test_result_from_payload_keeps_failed_step_details() -> None:
    result = DesktopDispatcher._result_from_payload(
        {
            "success": False,
            "output": "",
            "error": "No matching UI Automation element",
            "screenshots": [],
            "step_logs": [],
            "duration_seconds": 0.1,
            "details": {
                "failed_step": {
                    "step_index": 1,
                    "action_type": "click_element",
                    "details": {"candidates": ["保存(Button)"]},
                }
            },
        },
        started_at=0.0,
    )

    assert result.success is False
    assert result.details["failed_step"]["details"]["candidates"] == [
        "保存(Button)"
    ]


def test_convert_plan_to_actions_carries_step_markers() -> None:
    manager = FakeConnectionManager()
    dispatcher = DesktopDispatcher(manager)

    actions = dispatcher._convert_plan_to_actions(
        {
            "steps": [
                {
                    "step_id": 1,
                    "action": "create_document",
                    "params": {"path": r"C:\docs\report.docx"},
                    "idempotency_key": " wps-doc-created ",
                },
                {
                    "step_id": 2,
                    "action": "screenshot",
                    "params": {"name": "wps-done"},
                },
            ]
        }
    )

    assert actions == [
        {
            "type": "create_document",
            "params": {"path": r"C:\docs\report.docx"},
            "step_id": 1,
            "idempotency_key": "wps-doc-created",
        },
        {
            "type": "screenshot",
            "params": {"name": "wps-done"},
            "step_id": 2,
        },
    ]


def test_dispatch_payload_includes_resume_markers() -> None:
    async def scenario() -> dict[str, Any]:
        session, engine = await _database()
        try:
            workflow = await _seed_workflow(
                session,
                required_capabilities=["window"],
            )
            await _seed_agent(session, capabilities=["window"])
            manager = FakeConnectionManager([1])
            dispatcher = DesktopDispatcher(
                manager,
                session_factory=session.info["session_factory"],
            )
            original_send = manager.send_task

            async def send_task(
                agent_id: int,
                payload: dict[str, Any],
            ) -> None:
                await original_send(agent_id, payload)
                dispatcher.complete_task(
                    payload["task_id"],
                    _completion_payload(payload["task_id"]),
                )

            manager.send_task = send_task  # type: ignore[method-assign]
            await dispatcher.dispatch(
                workflow,
                {
                    "next_step_index": 1,
                    "completed_idempotency_keys": ["open-wps"],
                },
            )
            return manager.sent[0][1]
        finally:
            await session.close()
            await engine.dispose()

    payload = asyncio.run(scenario())

    assert payload["step_offset"] == 0
    assert payload["resume_from_step"] == 1
    assert payload["completed_idempotency_keys"] == ["open-wps"]
    assert len(payload["actions"]) == 2


def test_failed_event_details_include_completed_idempotency_keys() -> None:
    result = DesktopDispatcher._result_from_event(
        {
            "type": "desktop.failed",
            "task_id": 1,
            "success": False,
            "error": "WPS 文档服务繁忙",
            "failed_step": {
                "step_index": 1,
                "step_id": 2,
                "action_type": "insert_image",
                "idempotency_key": "wps-image-inserted",
                "error": "WPS 文档服务繁忙",
                "details": {},
            },
            "completed_idempotency_keys": ["wps-doc-created"],
        }
    )

    assert result["success"] is False
    assert result["details"]["failed_step"]["step_id"] == 2
    assert result["details"]["completed_idempotency_keys"] == [
        "wps-doc-created"
    ]


def test_result_from_payload_keeps_completed_idempotency_keys() -> None:
    result = DesktopDispatcher._result_from_payload(
        {
            "success": False,
            "output": "",
            "error": "WPS 文档服务繁忙",
            "screenshots": [],
            "step_logs": [],
            "duration_seconds": 0.1,
            "completed_idempotency_keys": ["wps-doc-created"],
            "details": {
                "failed_step": {
                    "step_index": 1,
                    "action_type": "insert_image",
                }
            },
        },
        started_at=0.0,
    )

    assert result.success is False
    assert result.details["completed_idempotency_keys"] == [
        "wps-doc-created"
    ]
