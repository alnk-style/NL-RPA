from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.database import Base  # noqa: E402
from app.engine.desktop_repair import (  # noqa: E402
    DesktopRepairCoordinator,
    repairable_failure,
)
from app.engine.runner import ExecutionResult  # noqa: E402
from app.models.agent import DesktopAgent  # noqa: E402
from app.models.user import User  # noqa: E402
from app.models.workflow import Workflow  # noqa: E402


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


async def _seed_workflow(session: AsyncSession) -> Workflow:
    session.add(
        User(
            id=1,
            username="tester",
            email="tester@example.com",
            hashed_password="hash",
        )
    )
    session.add(
        DesktopAgent(
            id=1,
            user_id=1,
            name="DESKTOP-01",
            platform="windows",
            version="0.1.0",
            capabilities=["window", "uia"],
            allowed_apps=["notepad.exe"],
            status="online",
            token_hash="hash",
        )
    )
    workflow = Workflow(
        user_id=1,
        name="桌面流程",
        natural_language_input="在记事本里保存",
        structured_plan={
            "executor_type": "windows",
            "steps": [
                {
                    "step_id": 1,
                    "action": "launch_app",
                    "params": {"path": "notepad.exe"},
                    "depends_on": [],
                },
                {
                    "step_id": 2,
                    "action": "click_element",
                    "params": {"name": "保存"},
                    "depends_on": [1],
                },
                {
                    "step_id": 3,
                    "action": "screenshot",
                    "params": {"name": "result"},
                    "depends_on": [2],
                },
            ],
            "variables": [],
        },
        executor_type="windows",
    )
    session.add(workflow)
    await session.commit()
    await session.refresh(workflow)
    return workflow


def _failure_result() -> ExecutionResult:
    return ExecutionResult(
        success=False,
        output="",
        error="No matching UI Automation element",
        screenshots=[],
        step_logs=[
            {
                "step_index": 0,
                "action_type": "launch_app",
                "status": "success",
            }
        ],
        duration_seconds=0.1,
        details={
            "failed_step": {
                "step_index": 1,
                "action_type": "click_element",
                "status": "failed",
                "error": "No matching UI Automation element",
                "details": {"candidates": ["保存(Button)", "关闭(Button)"]},
            }
        },
    )


def _success_result(step_index: int = 1) -> ExecutionResult:
    return ExecutionResult(
        success=True,
        output="ok",
        error=None,
        screenshots=[],
        step_logs=[
            {
                "step_index": step_index,
                "action_type": "click_element",
                "status": "success",
            }
        ],
        duration_seconds=0.1,
    )


def _structured_wait_failure() -> ExecutionResult:
    return ExecutionResult(
        success=False,
        output="",
        error="Timed out waiting for element_appears",
        screenshots=[],
        step_logs=[
            {
                "step_index": 0,
                "action_type": "launch_app",
                "status": "success",
            }
        ],
        duration_seconds=0.1,
        details={
            "failed_step": {
                "step_index": 1,
                "action_type": "wait",
                "status": "failed",
                "error": "Timed out waiting for element_appears",
                "params": {
                    "seconds": 5,
                    "condition": "element_appears",
                    "name": "保存",
                },
                "details": {
                    "candidates": [
                        {
                            "index": 1,
                            "name": "保存",
                            "automation_id": "saveBtn",
                            "control_type": "Button",
                            "class_name": "Button",
                            "enabled": True,
                            "visible": True,
                            "score": 124,
                            "best_locator": {
                                "strategy": "automation_id",
                                "value": "saveBtn",
                                "count": 1,
                                "unique": True,
                                "score": 100,
                            },
                        }
                    ],
                    "candidate_labels": ["保存(Button)"],
                    "last_locator": {
                        "automation_id": None,
                        "name": "保存",
                        "control_type": "Button",
                        "window_handle": 11,
                    },
                },
            }
        },
    )


class FakeDispatcher:
    def __init__(self, results: list[ExecutionResult]) -> None:
        self.results = list(results)
        self.calls: list[tuple[Any, dict]] = []

    async def dispatch(
        self,
        workflow,
        context: dict | None = None,
    ) -> ExecutionResult:
        self.calls.append((workflow, dict(context or {})))
        if not self.results:
            raise AssertionError("dispatcher called more than expected")
        return self.results.pop(0)


def _repaired_steps() -> list[dict]:
    return [
        {
            "step_id": 2,
            "action": "click_element",
            "params": {"name": "保存", "control_type": "Button"},
            "depends_on": [1],
        },
        {
            "step_id": 3,
            "action": "screenshot",
            "params": {"name": "result"},
            "depends_on": [2],
        },
    ]


def test_repairable_failure_requires_candidates() -> None:
    assert repairable_failure(_failure_result()) is not None
    plain = ExecutionResult(
        success=False,
        output="",
        error="Application is not allowlisted",
        screenshots=[],
        step_logs=[],
        duration_seconds=0.1,
        details={
            "failed_step": {
                "step_index": 0,
                "action_type": "launch_app",
                "error": "Application is not allowlisted",
            }
        },
    )
    assert repairable_failure(plain) is None


def test_repairable_failure_accepts_structured_candidates() -> None:
    failure = repairable_failure(_structured_wait_failure())

    assert failure is not None
    assert failure["step_index"] == 1
    assert failure["action_type"] == "wait"
    assert failure["candidates"] == ["保存(Button)"]
    assert failure["candidate_details"][0]["automation_id"] == "saveBtn"
    assert failure["candidate_details"][0]["score"] == 124
    assert failure["last_locator"]["window_handle"] == 11


def test_repairable_failure_covers_activate_window() -> None:
    result = ExecutionResult(
        success=False,
        output="",
        error="No matching window found",
        screenshots=[],
        step_logs=[],
        duration_seconds=0.1,
        details={
            "failed_step": {
                "step_index": 0,
                "action_type": "activate_window",
                "status": "failed",
                "error": "No matching window found",
                "params": {"title": "微信"},
                "details": {
                    "candidates": [
                        {
                            "handle": 10,
                            "title": "记事本",
                            "class_name": "Notepad",
                            "pid": 100,
                        }
                    ],
                },
            }
        },
    )

    failure = repairable_failure(result)

    assert failure is not None
    assert failure["action_type"] == "activate_window"
    assert failure["candidates"] == ["记事本(Notepad)"]
    assert failure["candidate_details"][0]["handle"] == 10


def test_repairable_failure_rejects_window_disappears_timeout() -> None:
    result = ExecutionResult(
        success=False,
        output="",
        error="Timed out waiting for window_disappears",
        screenshots=[],
        step_logs=[],
        duration_seconds=0.1,
        details={
            "failed_step": {
                "step_index": 0,
                "action_type": "wait",
                "status": "failed",
                "error": "Timed out waiting for window_disappears",
                "params": {"condition": "window_disappears", "title": "记事本"},
                "details": {},
            }
        },
    )

    assert repairable_failure(result) is None


def test_repair_replaces_failed_step_and_resumes() -> None:
    async def scenario():
        session_factory, db_engine = await _database()
        try:
            async with session_factory() as session:
                await _seed_workflow(session)

            dispatcher = FakeDispatcher(
                [_failure_result(), _success_result()]
            )
            planner = SimpleNamespace(
                repair=AsyncMock(return_value=_repaired_steps())
            )
            coordinator = DesktopRepairCoordinator(
                dispatcher,
                planner=planner,
                session_factory=session_factory,
                max_attempts=2,
            )

            async with session_factory() as session:
                workflow = await session.get(Workflow, 1)

            result = await coordinator.execute(
                workflow,
                {"execution_log_id": 5},
            )

            async with session_factory() as session:
                stored = await session.get(Workflow, 1)
            return result, dispatcher.calls, stored.structured_plan
        finally:
            await db_engine.dispose()

    result, calls, stored_plan = asyncio.run(scenario())

    assert result.success is True
    assert len(calls) == 2
    first_workflow, first_context = calls[0]
    assert first_context["execution_log_id"] == 5
    assert len(first_workflow.structured_plan["steps"]) == 3

    resume_workflow, resume_context = calls[1]
    assert resume_context["step_offset"] == 1
    assert resume_context["execution_log_id"] == 5
    assert [
        step["step_id"] for step in resume_workflow.structured_plan["steps"]
    ] == [2, 3]
    assert resume_workflow.structured_plan["steps"][0]["params"] == {
        "name": "保存",
        "control_type": "Button",
    }

    assert [step["step_id"] for step in stored_plan["steps"]] == [1, 2, 3]
    assert stored_plan["steps"][1]["params"]["control_type"] == "Button"
    assert result.details["repairs"][0]["attempt"] == 1
    assert result.details["repairs"][0]["candidates"] == [
        "保存(Button)",
        "关闭(Button)",
    ]
    assert [log.get("action_type") for log in result.step_logs] == [
        "launch_app",
        "auto_repair",
        "click_element",
    ]


def test_repair_drops_resume_markers_from_sliced_dispatch() -> None:
    async def scenario():
        session_factory, db_engine = await _database()
        try:
            async with session_factory() as session:
                await _seed_workflow(session)

            dispatcher = FakeDispatcher(
                [_failure_result(), _success_result()]
            )
            planner = SimpleNamespace(
                repair=AsyncMock(return_value=_repaired_steps())
            )
            coordinator = DesktopRepairCoordinator(
                dispatcher,
                planner=planner,
                session_factory=session_factory,
                max_attempts=2,
            )

            async with session_factory() as session:
                workflow = await session.get(Workflow, 1)

            result = await coordinator.execute(
                workflow,
                {
                    "execution_log_id": 5,
                    "resume_from_step": 8,
                    "next_step_index": 8,
                    "initial_step_logs": [{"step_index": 0}],
                    "completed_idempotency_keys": ["open-wps"],
                },
            )
            return result, dispatcher.calls
        finally:
            await db_engine.dispose()

    result, calls = asyncio.run(scenario())

    assert result.success is True
    assert len(calls) == 2
    resume_context = calls[1][1]
    assert "resume_from_step" not in resume_context
    assert "next_step_index" not in resume_context
    assert "initial_step_logs" not in resume_context
    assert resume_context["step_offset"] == 1
    assert resume_context["completed_idempotency_keys"] == ["open-wps"]


def test_repair_passes_structured_candidates_to_planner() -> None:
    async def scenario():
        session_factory, db_engine = await _database()
        try:
            async with session_factory() as session:
                await _seed_workflow(session)

            dispatcher = FakeDispatcher(
                [_structured_wait_failure(), _success_result()]
            )
            planner = SimpleNamespace(
                repair=AsyncMock(return_value=_repaired_steps())
            )
            coordinator = DesktopRepairCoordinator(
                dispatcher,
                planner=planner,
                session_factory=session_factory,
                max_attempts=2,
            )

            async with session_factory() as session:
                workflow = await session.get(Workflow, 1)

            result = await coordinator.execute(workflow, {})
            return result, planner, dispatcher
        finally:
            await db_engine.dispose()

    result, planner, dispatcher = asyncio.run(scenario())

    assert result.success is True
    assert len(dispatcher.calls) == 2
    kwargs = planner.repair.await_args.kwargs
    assert kwargs["failure"]["candidate_details"][0]["automation_id"] == (
        "saveBtn"
    )
    assert kwargs["failure"]["last_locator"]["window_handle"] == 11
    record = result.details["repairs"][0]
    assert record["candidates"] == ["保存(Button)"]
    assert record["last_locator"]["window_handle"] == 11


def test_repair_is_skipped_for_other_failures() -> None:
    async def scenario():
        session_factory, db_engine = await _database()
        try:
            async with session_factory() as session:
                await _seed_workflow(session)
            failure = ExecutionResult(
                success=False,
                output="",
                error="Application is not allowlisted",
                screenshots=[],
                step_logs=[],
                duration_seconds=0.1,
                details={
                    "failed_step": {
                        "step_index": 1,
                        "action_type": "launch_app",
                        "error": "Application is not allowlisted",
                    }
                },
            )
            dispatcher = FakeDispatcher([failure])
            planner = SimpleNamespace(repair=AsyncMock())
            coordinator = DesktopRepairCoordinator(
                dispatcher,
                planner=planner,
                session_factory=session_factory,
                max_attempts=2,
            )
            async with session_factory() as session:
                workflow = await session.get(Workflow, 1)
            result = await coordinator.execute(workflow, {})
            return result, planner, dispatcher
        finally:
            await db_engine.dispose()

    result, planner, dispatcher = asyncio.run(scenario())

    assert result.success is False
    planner.repair.assert_not_awaited()
    assert len(dispatcher.calls) == 1


def test_repair_stops_after_max_attempts() -> None:
    async def scenario():
        session_factory, db_engine = await _database()
        try:
            async with session_factory() as session:
                await _seed_workflow(session)
            dispatcher = FakeDispatcher(
                [
                    _failure_result(),
                    _failure_result(),
                    _failure_result(),
                ]
            )
            planner = SimpleNamespace(
                repair=AsyncMock(return_value=_repaired_steps())
            )
            coordinator = DesktopRepairCoordinator(
                dispatcher,
                planner=planner,
                session_factory=session_factory,
                max_attempts=2,
            )
            async with session_factory() as session:
                workflow = await session.get(Workflow, 1)
            result = await coordinator.execute(workflow, {})
            return result, planner, dispatcher
        finally:
            await db_engine.dispose()

    result, planner, dispatcher = asyncio.run(scenario())

    assert result.success is False
    assert planner.repair.await_count == 2
    assert len(dispatcher.calls) == 3
    assert len(result.details["repairs"]) == 2


def test_repair_survives_planner_failure() -> None:
    async def scenario():
        session_factory, db_engine = await _database()
        try:
            async with session_factory() as session:
                await _seed_workflow(session)
            dispatcher = FakeDispatcher([_failure_result()])
            planner = SimpleNamespace(
                repair=AsyncMock(side_effect=RuntimeError("llm down"))
            )
            coordinator = DesktopRepairCoordinator(
                dispatcher,
                planner=planner,
                session_factory=session_factory,
                max_attempts=2,
            )
            async with session_factory() as session:
                workflow = await session.get(Workflow, 1)
            result = await coordinator.execute(workflow, {})
            return result, dispatcher
        finally:
            await db_engine.dispose()

    result, dispatcher = asyncio.run(scenario())

    assert result.success is False
    assert result.error == "No matching UI Automation element"
    assert len(dispatcher.calls) == 1
