from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.engine.execution_manager import (
    ExecutionManager,
    ExecutionStateError,
)
from app.engine.runner import ExecutionResult
from app.models.browser_profile import BrowserProfile
from app.models.execution import ExecutionLog
from app.models.execution_event import ExecutionEvent
from app.models.human_task import HumanTask
from app.models.user import User
from app.models.workflow import Workflow
from app.services.human_tasks import (
    HumanTaskManager,
    VerificationResult,
)
from tests.support.versioned_workflows import activate_test_version


async def _database():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    return factory, engine


async def _seed(factory: async_sessionmaker[AsyncSession]) -> Workflow:
    async with factory() as session:
        session.add(
            User(
                id=1,
                username="integration-owner",
                email="integration-owner@example.com",
                hashed_password="hash",
            )
        )
        session.add(
            BrowserProfile(
                user_id=1,
                name="Automation",
                user_data_dir="C:/rpa-profile",
                profile_directory="Default",
                login_state="expired",
            )
        )
        workflow = Workflow(
            user_id=1,
            name="Login and download",
            natural_language_input="login then download",
            status="draft",
        )
        session.add(workflow)
        await session.flush()
        await activate_test_version(session, workflow)
        await session.commit()
        await session.refresh(workflow)
        return workflow


class SequenceEngine:
    def __init__(self, results: list[ExecutionResult]) -> None:
        self.results = list(results)
        self.contexts: list[dict] = []
        self.calls = 0

    async def execute_with_debug(
        self,
        _workflow,
        context: dict | None = None,
    ) -> ExecutionResult:
        self.calls += 1
        self.contexts.append(dict(context or {}))
        if len(self.results) > 1:
            return self.results.pop(0)
        return self.results[0]


class FakeVerifier:
    def __init__(self, result: VerificationResult) -> None:
        self.result = result

    async def verify(self, task: HumanTask, request: dict) -> VerificationResult:
        del task, request
        return self.result


def _config() -> SimpleNamespace:
    return SimpleNamespace(
        AUTH_REQUIRED_PAUSE=True,
        WEB_EXPORT_DIRS="",
    )


def _auth_result() -> ExecutionResult:
    return ExecutionResult(
        success=False,
        output="",
        error="AUTH_REQUIRED: 登录已失效",
        screenshots=["logs/execution/auth.png"],
        step_logs=[
            {
                "step_index": 0,
                "step_id": 1,
                "action": "goto",
                "status": "success",
                "idempotency_key": "open-page",
            },
            {
                "step_index": 1,
                "step_id": 2,
                "action": "assert_logged_in",
                "status": "failed",
            },
        ],
        duration_seconds=0.2,
        details={
            "awaiting_user": True,
            "failed_step": {
                "step_index": 1,
                "step_id": 2,
                "action": "assert_logged_in",
                "failure_category": "auth_required",
                "current_url": "https://example.com/login",
                "verification": {
                    "type": "web_logged_in",
                    "success_selector": "[data-user]",
                },
            },
            "completed_idempotency_keys": ["open-page"],
            "extracted": {"before_login": "kept"},
            "login_state": "expired",
        },
    )


def _success_result() -> ExecutionResult:
    return ExecutionResult(
        success=True,
        output="downloaded",
        error=None,
        screenshots=[],
        step_logs=[
            {
                "step_index": 0,
                "step_id": 1,
                "action": "goto",
                "status": "skipped",
                "idempotency_key": "open-page",
            },
            {
                "step_index": 1,
                "step_id": 2,
                "action": "assert_logged_in",
                "status": "success",
            },
            {
                "step_index": 2,
                "step_id": 3,
                "action": "download",
                "status": "success",
                "idempotency_key": "download-report",
            },
        ],
        duration_seconds=0.1,
        details={
            "completed_idempotency_keys": [
                "open-page",
                "download-report",
            ],
            "extracted": {"download_count": 1},
            "login_state": "ready",
        },
    )


def test_auth_result_creates_human_task_checkpoint_and_event() -> None:
    async def scenario():
        factory, engine = await _database()
        manager = None
        try:
            workflow = await _seed(factory)
            fake_engine = SequenceEngine([_auth_result()])
            manager = ExecutionManager(
                fake_engine,
                session_factory=factory,
                config=_config(),
            )
            execution = await manager.start_workflow(workflow.id)
            await manager.execute_existing(execution.id)
            async with factory() as session:
                stored = await session.get(ExecutionLog, execution.id)
                assert stored is not None
                task = (
                    await session.execute(
                        select(HumanTask).where(
                            HumanTask.execution_id == execution.id
                        )
                    )
                ).scalar_one()
                events = list(
                    (
                        await session.execute(
                            select(ExecutionEvent.type)
                        )
                    ).scalars()
                )
                return stored, task, events, fake_engine.contexts
        finally:
            if manager is not None:
                await manager.shutdown()
            await engine.dispose()

    execution, task, events, contexts = asyncio.run(scenario())

    assert execution.status == "awaiting_user"
    assert execution.human_task_id == task.id
    assert execution.browser_profile_id is not None
    assert task.kind == "login_required"
    assert task.status == "pending"
    checkpoint = execution.step_logs[-1]
    assert checkpoint["type"] == "checkpoint"
    assert checkpoint["schema_version"] == 3
    assert checkpoint["execution_id"] == execution.id
    assert checkpoint["workflow_version_id"] == execution.workflow_version_id
    assert checkpoint["browser_profile_id"] == execution.browser_profile_id
    assert checkpoint["completed_step_ids"] == [1]
    assert checkpoint["completed_idempotency_keys"] == ["open-page"]
    assert checkpoint["variables"] == {"before_login": "kept"}
    assert "execution.awaiting_user" in events
    assert "human_task.created" in events
    assert contexts[0]["browser_profile"]["profile_directory"] == "Default"


def test_old_resume_after_login_rejects_execution_with_pending_human_task() -> None:
    async def scenario():
        factory, engine = await _database()
        manager = None
        try:
            workflow = await _seed(factory)
            manager = ExecutionManager(
                SequenceEngine([_auth_result(), _success_result()]),
                session_factory=factory,
                config=_config(),
            )
            execution = await manager.start_workflow(workflow.id)
            await manager.execute_existing(execution.id)
            with pytest.raises(ExecutionStateError, match="human task"):
                await manager.resume_after_login(execution.id)
            return execution.id
        finally:
            if manager is not None:
                await manager.shutdown()
            await engine.dispose()

    assert asyncio.run(scenario()) > 0


def test_human_task_resume_uses_checkpoint_and_does_not_repeat_side_effect() -> None:
    async def scenario():
        factory, engine = await _database()
        manager = None
        try:
            workflow = await _seed(factory)
            fake_engine = SequenceEngine(
                [_auth_result(), _success_result()]
            )
            manager = ExecutionManager(
                fake_engine,
                session_factory=factory,
                config=_config(),
            )
            execution = await manager.start_workflow(workflow.id)
            await manager.execute_existing(execution.id)
            async with factory() as session:
                task = (
                    await session.execute(
                        select(HumanTask).where(
                            HumanTask.execution_id == execution.id
                        )
                    )
                ).scalar_one()

            human_tasks = HumanTaskManager(
                session_factory=factory,
                execution_manager=manager,
                verifier=FakeVerifier(
                    VerificationResult(
                        ok=True,
                        login_state="ready",
                        details={"selector": "[data-user]"},
                    )
                ),
            )
            await human_tasks.resume(
                task.id,
                decision="resume",
                verification={},
                actor_id=1,
            )
            for _ in range(100):
                async with factory() as session:
                    stored = await session.get(ExecutionLog, execution.id)
                    assert stored is not None
                    if stored.status == "success":
                        break
                await asyncio.sleep(0.01)
            return stored, fake_engine.contexts
        finally:
            if manager is not None:
                await manager.shutdown()
            await engine.dispose()

    execution, contexts = asyncio.run(scenario())

    assert execution.status == "success"
    assert len(contexts) == 2
    assert contexts[1]["resume_from_step"] == 1
    assert contexts[1]["next_step_id"] == 2
    assert contexts[1]["completed_step_ids"] == [1]
    assert contexts[1]["completed_idempotency_keys"] == ["open-page"]
    assert contexts[1]["variables"] == {"before_login": "kept"}
    assert contexts[1]["login_state"] == "expired"
    assert contexts[1]["browser_profile_id"] is not None
    assert contexts[1]["browser_profile"]["profile_directory"] == "Default"


def test_cancel_stops_runtime_and_preserves_terminal_state() -> None:
    async def scenario():
        factory, engine = await _database()
        manager = None
        try:
            workflow = await _seed(factory)
            manager = ExecutionManager(
                SequenceEngine([_auth_result()]),
                session_factory=factory,
                config=_config(),
            )
            execution = await manager.start_workflow(workflow.id)
            await manager.execute_existing(execution.id)

            await manager.cancel(
                execution.id,
                "用户停止",
                already_persisted=True,
            )
            async with factory() as session:
                stored = await session.get(ExecutionLog, execution.id)
                assert stored is not None
                return stored
        finally:
            if manager is not None:
                await manager.shutdown()
            await engine.dispose()

    execution = asyncio.run(scenario())
    assert execution.status == "cancelled"
    assert execution.finished_at is not None
    assert execution.error_message == "用户停止"


def test_explicit_human_action_pauses_even_when_auth_flag_is_disabled() -> None:
    result = ExecutionResult(
        success=False,
        output="",
        error="需要确认目标窗口",
        screenshots=[],
        step_logs=[
            {
                "step_index": 0,
                "step_id": 1,
                "action": "click_element",
                "status": "failed",
            }
        ],
        duration_seconds=0.1,
        details={
            "awaiting_user": True,
            "human_action": {
                "step_id": 1,
                "kind": "manual_confirmation",
                "instruction": "请确认目标窗口已打开",
                "verification": {
                    "type": "manual_confirmation",
                },
                "follow_up_verification": {
                    "type": "next_step_postcondition",
                },
            },
        },
    )

    async def scenario():
        factory, engine = await _database()
        manager = None
        try:
            workflow = await _seed(factory)
            manager = ExecutionManager(
                SequenceEngine([result]),
                session_factory=factory,
                config=SimpleNamespace(
                    AUTH_REQUIRED_PAUSE=False,
                    WEB_EXPORT_DIRS="",
                ),
            )
            execution = await manager.start_workflow(workflow.id)
            await manager.execute_existing(execution.id)
            async with factory() as session:
                stored = await session.get(ExecutionLog, execution.id)
                task = (
                    await session.execute(
                        select(HumanTask).where(
                            HumanTask.execution_id == execution.id
                        )
                    )
                ).scalar_one()
                return stored, task
        finally:
            if manager is not None:
                await manager.shutdown()
            await engine.dispose()

    execution, task = asyncio.run(scenario())

    assert execution.status == "awaiting_user"
    assert task.kind == "manual_confirmation"


def test_visual_ambiguity_creates_task_and_resumes_selected_candidate() -> None:
    visual_result = ExecutionResult(
        success=False,
        output="",
        error="视觉候选存在歧义",
        screenshots=["logs/execution/visual.png"],
        step_logs=[
            {
                "step_index": 0,
                "step_id": 1,
                "action": "click",
                "status": "failed",
                "failure_category": "visual_ambiguous",
            }
        ],
        duration_seconds=0.1,
        details={
            "awaiting_user": True,
            "failed_step": {
                "step_index": 0,
                "step_id": 1,
                "action": "click",
                "failure_category": "visual_ambiguous",
                "current_url": "https://example.com/orders",
                "human_action": {
                    "kind": "visual_ambiguous",
                    "instruction": "请选择正确的导出按钮",
                    "current_url": "https://example.com/orders",
                    "verification": {"type": "visual_selection"},
                    "visual": {
                        "action": "click",
                        "risk_level": "read_only",
                        "execution_allowed": True,
                        "provider": "remote_vlm",
                        "model": "fake-vlm",
                        "candidates": [
                            {
                                "candidate_id": "cand_1",
                                "target": "导出",
                                "bbox": [100, 200, 180, 240],
                                "confidence": 0.94,
                                "evidence": "按钮文字",
                            }
                        ],
                    },
                },
            },
        },
    )
    success_result = ExecutionResult(
        success=True,
        output="visual click completed",
        error=None,
        screenshots=[],
        step_logs=[
            {
                "step_index": 0,
                "step_id": 1,
                "action": "click",
                "status": "success",
                "verification": {"passed": True},
            }
        ],
        duration_seconds=0.1,
    )

    async def scenario():
        factory, engine = await _database()
        manager = None
        try:
            workflow = await _seed(factory)
            fake_engine = SequenceEngine([visual_result, success_result])
            manager = ExecutionManager(
                fake_engine,
                session_factory=factory,
                config=SimpleNamespace(
                    AUTH_REQUIRED_PAUSE=False,
                    WEB_EXPORT_DIRS="",
                ),
            )
            execution = await manager.start_workflow(workflow.id)
            await manager.execute_existing(execution.id)
            async with factory() as session:
                task = (
                    await session.execute(
                        select(HumanTask).where(
                            HumanTask.execution_id == execution.id
                        )
                    )
                ).scalar_one()

            human_tasks = HumanTaskManager(
                session_factory=factory,
                execution_manager=manager,
            )
            await human_tasks.resume(
                task.id,
                decision="resume",
                verification={"candidate_id": "cand_1"},
                actor_id=1,
            )
            for _ in range(100):
                async with factory() as session:
                    stored = await session.get(ExecutionLog, execution.id)
                    assert stored is not None
                    if stored.status == "success":
                        break
                await asyncio.sleep(0.01)
            return task, stored, fake_engine.contexts
        finally:
            if manager is not None:
                await manager.shutdown()
            await engine.dispose()

    task, execution, contexts = asyncio.run(scenario())

    assert task.kind == "visual_ambiguous"
    assert task.payload["candidates"][0]["candidate_id"] == "cand_1"
    assert execution.status == "success"
    assert contexts[1]["visual_selection"]["candidate_id"] == "cand_1"
    assert contexts[1]["visual_selection"]["step_id"] == 1
