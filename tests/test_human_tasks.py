from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
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
from app.models.agent import DesktopAgent
from app.models.execution import ExecutionLog
from app.models.execution_event import ExecutionEvent
from app.models.human_task import HumanTask
from app.models.human_task_event import HumanTaskEvent
from app.models.user import User
from app.models.workflow import Workflow
from app.services.human_tasks import (
    HumanTaskManager,
    HumanTaskVerificationError,
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


async def _seed_execution(
    factory: async_sessionmaker[AsyncSession],
    *,
    status: str = "awaiting_user",
) -> int:
    async with factory() as session:
        session.add(
            User(
                id=1,
                username="human-task-user",
                email="human-task@example.com",
                hashed_password="hash",
            )
        )
        workflow = Workflow(
            user_id=1,
            name="Login workflow",
            natural_language_input="login then continue",
            status="draft",
        )
        session.add(workflow)
        await session.flush()
        await activate_test_version(session, workflow)
        execution = ExecutionLog(
            workflow_id=workflow.id,
            workflow_version_id=workflow.active_version_id,
            status=status,
            step_logs=[
                {
                    "step_index": 0,
                    "step_id": 1,
                    "action": "goto",
                    "status": "success",
                },
                {
                    "type": "checkpoint",
                    "action": "checkpoint",
                    "status": "awaiting_user",
                    "next_step_index": 1,
                    "next_step_id": 2,
                    "completed_step_ids": [1],
                    "completed_idempotency_keys": ["open-page"],
                },
            ],
        )
        session.add(execution)
        await session.commit()
        await session.refresh(execution)
        return int(execution.id)


class FakeExecutionManager:
    def __init__(self) -> None:
        self.dispatched: list[int] = []
        self.cancelled: list[tuple[int, str]] = []

    async def dispatch_human_task_resume(self, execution_id: int) -> None:
        self.dispatched.append(execution_id)

    async def cancel(
        self,
        execution_id: int,
        reason: str,
        *,
        already_persisted: bool = False,
    ) -> None:
        del already_persisted
        self.cancelled.append((execution_id, reason))


class FakeVerifier:
    def __init__(self, result: VerificationResult) -> None:
        self.result = result
        self.calls: list[tuple[int, dict]] = []

    async def verify(
        self,
        task: HumanTask,
        request_verification: dict,
    ) -> VerificationResult:
        self.calls.append((int(task.id), dict(request_verification)))
        return self.result


class FakePreflight:
    def __init__(self, status: str, message: str = "") -> None:
        self.status = status
        self.message = message
        self.calls = 0

    async def check(self, execution, login_state):
        del execution, login_state
        self.calls += 1
        return SimpleNamespace(
            status=self.status,
            message=self.message,
        )


def _login_task_kwargs() -> dict:
    return {
        "step_id": 2,
        "kind": "login_required",
        "instruction": "请在打开的登录窗口完成登录。",
        "payload": {
            "current_url": "https://example.com/login",
            "verification": {
                "type": "web_logged_in",
                "url_contains": "/home",
                "success_selector": "[data-user]",
            },
        },
        "screenshot_path": "logs/execution/login.png",
        "expires_at": datetime.utcnow() + timedelta(minutes=30),
        "dedupe_key": "execution:1:step:2:login_required",
    }


def test_human_task_create_deduplicates_active_task_and_sets_execution() -> None:
    async def scenario() -> tuple[int, int, str, str]:
        factory, engine = await _database()
        try:
            execution_id = await _seed_execution(factory)
            manager = HumanTaskManager(
                session_factory=factory,
                execution_manager=FakeExecutionManager(),
            )
            first = await manager.create(execution_id, **_login_task_kwargs())
            second = await manager.create(execution_id, **_login_task_kwargs())

            async with factory() as session:
                execution = await session.get(ExecutionLog, execution_id)
                tasks = (
                    await session.execute(select(HumanTask))
                ).scalars().all()
                events = (
                    await session.execute(select(ExecutionEvent))
                ).scalars().all()
                assert execution is not None
                return (
                    int(first.id),
                    int(second.id),
                    execution.status,
                    ",".join(sorted(event.type for event in events)),
                )
        finally:
            await engine.dispose()

    first_id, second_id, status, event_types = asyncio.run(scenario())

    assert first_id == second_id
    assert status == "awaiting_user"
    assert "execution.awaiting_user" in event_types
    assert event_types.count("human_task.created") == 1


def test_resume_requires_verification_and_updates_task_and_execution_atomically() -> None:
    async def scenario() -> tuple[HumanTask, ExecutionLog, list[int], list[str]]:
        factory, engine = await _database()
        execution_manager = FakeExecutionManager()
        verifier = FakeVerifier(
            VerificationResult(
                ok=True,
                details={"checked": "logged_in"},
                login_state="ready",
            )
        )
        try:
            execution_id = await _seed_execution(factory)
            manager = HumanTaskManager(
                session_factory=factory,
                execution_manager=execution_manager,
                verifier=verifier,
            )
            task = await manager.create(
                execution_id,
                **_login_task_kwargs(),
            )
            resolved = await manager.resume(
                int(task.id),
                decision="resume",
                verification={"manual_click": True},
                actor_id=1,
            )
            repeated = await manager.resume(
                int(task.id),
                decision="resume",
                verification={},
                actor_id=1,
            )
            assert repeated.id == resolved.id

            async with factory() as session:
                stored_task = await session.get(HumanTask, task.id)
                execution = await session.get(ExecutionLog, execution_id)
                assert stored_task is not None
                assert execution is not None
                event_types = list(
                    (
                        await session.execute(
                            select(ExecutionEvent.type).order_by(
                                ExecutionEvent.id
                            )
                        )
                    ).scalars()
                )
                return (
                    stored_task,
                    execution,
                    execution_manager.dispatched,
                    event_types,
                )
        finally:
            await engine.dispose()

    task, execution, dispatched, event_types = asyncio.run(scenario())

    assert task.status == "resolved"
    assert task.resolved_at is not None
    assert task.dedupe_key is None
    assert execution.status == "running"
    assert execution.finished_at is None
    assert execution.human_task_id == task.id
    assert dispatched == [execution.id]
    assert "human_task.resolved" in event_types
    assert "execution.resumed" in event_types


def test_failed_verification_keeps_task_pending_and_does_not_dispatch() -> None:
    async def scenario() -> tuple[HumanTask, ExecutionLog, list[int], list[str]]:
        factory, engine = await _database()
        execution_manager = FakeExecutionManager()
        verifier = FakeVerifier(
            VerificationResult(
                ok=False,
                reason="未找到登录成功标志 [data-user]",
                login_state="login_required",
            )
        )
        try:
            execution_id = await _seed_execution(factory)
            manager = HumanTaskManager(
                session_factory=factory,
                execution_manager=execution_manager,
                verifier=verifier,
            )
            task = await manager.create(
                execution_id,
                **_login_task_kwargs(),
            )
            with pytest.raises(HumanTaskVerificationError) as raised:
                await manager.resume(
                    int(task.id),
                    decision="resume",
                    verification={},
                    actor_id=1,
                )
            assert raised.value.code == "verification_failed"

            async with factory() as session:
                stored_task = await session.get(HumanTask, task.id)
                execution = await session.get(ExecutionLog, execution_id)
                assert stored_task is not None
                assert execution is not None
                event_types = list(
                    (
                        await session.execute(
                            select(ExecutionEvent.type).order_by(
                                ExecutionEvent.id
                            )
                        )
                    ).scalars()
                )
                return (
                    stored_task,
                    execution,
                    execution_manager.dispatched,
                    event_types,
                )
        finally:
            await engine.dispose()

    task, execution, dispatched, event_types = asyncio.run(scenario())

    assert task.status == "pending"
    assert task.resolved_at is None
    assert execution.status == "awaiting_user"
    assert dispatched == []
    assert "execution.resume_failed" in event_types


def test_resume_preflight_failure_keeps_task_pending() -> None:
    async def scenario() -> tuple[HumanTask, list[int], int]:
        factory, engine = await _database()
        execution_manager = FakeExecutionManager()
        preflight = FakePreflight("blocked", "原执行主机不可用")
        try:
            execution_id = await _seed_execution(factory)
            manager = HumanTaskManager(
                session_factory=factory,
                execution_manager=execution_manager,
                verifier=FakeVerifier(
                    VerificationResult(ok=True, login_state="ready")
                ),
                preflight_checker=preflight,
            )
            task = await manager.create(
                execution_id,
                **_login_task_kwargs(),
            )
            with pytest.raises(HumanTaskVerificationError) as raised:
                await manager.resume(
                    int(task.id),
                    decision="resume",
                    verification={},
                    actor_id=1,
                )
            assert raised.value.code == "preflight_blocked"
            async with factory() as session:
                stored = await session.get(HumanTask, task.id)
                assert stored is not None
                return stored, execution_manager.dispatched, preflight.calls
        finally:
            await engine.dispose()

    task, dispatched, preflight_calls = asyncio.run(scenario())

    assert task.status == "pending"
    assert dispatched == []
    assert preflight_calls == 1


def test_resume_requires_original_execution_host_when_agents_are_online() -> None:
    async def scenario() -> HumanTask:
        factory, engine = await _database()
        try:
            execution_id = await _seed_execution(factory)
            async with factory() as session:
                execution = await session.get(ExecutionLog, execution_id)
                assert execution is not None
                execution.host_id = 7
                await session.commit()
            manager = HumanTaskManager(
                session_factory=factory,
                execution_manager=FakeExecutionManager(),
                verifier=FakeVerifier(
                    VerificationResult(ok=True, login_state="ready")
                ),
                online_agent_ids=lambda: [8],
            )
            task = await manager.create(
                execution_id,
                **_login_task_kwargs(),
            )
            with pytest.raises(HumanTaskVerificationError) as raised:
                await manager.resume(
                    int(task.id),
                    decision="resume",
                    verification={},
                    actor_id=1,
                )
            assert raised.value.code == "preflight_blocked"
            async with factory() as session:
                stored = await session.get(HumanTask, task.id)
                assert stored is not None
                return stored
        finally:
            await engine.dispose()

    assert asyncio.run(scenario()).status == "pending"


def test_resume_rejects_lost_original_agent_session() -> None:
    async def scenario() -> tuple[HumanTask, str]:
        factory, engine = await _database()
        try:
            execution_id = await _seed_execution(factory)
            async with factory() as session:
                session.add(
                    DesktopAgent(
                        id=7,
                        user_id=1,
                        name="pinned-agent",
                        platform="windows",
                        version="0.2.0",
                        environment="local",
                        capabilities=["interactive_desktop"],
                        status="online",
                        token_hash="hash",
                        human_access_url=(
                            "https://console.example.test/vm/7"
                            "?token=must-not-leak"
                        ),
                        human_access_instructions=(
                            "Open console token=must-not-leak"
                        ),
                    )
                )
                execution = await session.get(
                    ExecutionLog,
                    execution_id,
                )
                assert execution is not None
                execution.host_id = 7
                execution.host_environment = "local"
                execution.host_bound = True
                execution.host_affinity_hash = "old-fingerprint"
                await session.commit()
            manager = HumanTaskManager(
                session_factory=factory,
                execution_manager=FakeExecutionManager(),
                verifier=FakeVerifier(
                    VerificationResult(ok=True, login_state="ready")
                ),
                online_agent_ids=lambda: [7],
                host_session_fingerprint=lambda _agent_id: "new-fingerprint",
            )
            task = await manager.create(
                execution_id,
                **_login_task_kwargs(),
            )
            assert task.payload["host"]["human_access_info"]["url"] == (
                "https://console.example.test/vm/7"
            )
            assert "must-not-leak" not in (
                task.payload["host"]["human_access_info"][
                    "instructions"
                ]
            )
            with pytest.raises(HumanTaskVerificationError) as raised:
                await manager.resume(
                    int(task.id),
                    decision="resume",
                    verification={},
                    actor_id=1,
                )
            async with factory() as session:
                stored = await session.get(HumanTask, task.id)
                assert stored is not None
                return stored, raised.value.reason
        finally:
            await engine.dispose()

    task, reason = asyncio.run(scenario())
    assert task.status == "pending"
    assert "host_session_lost" in reason


def test_resume_without_a_declared_verification_condition_is_rejected() -> None:
    async def scenario() -> HumanTask:
        factory, engine = await _database()
        try:
            execution_id = await _seed_execution(factory)
            manager = HumanTaskManager(
                session_factory=factory,
                execution_manager=FakeExecutionManager(),
                verifier=FakeVerifier(
                    VerificationResult(ok=True, login_state="ready")
                ),
            )
            kwargs = _login_task_kwargs()
            kwargs["payload"] = {"current_url": "https://example.com"}
            task = await manager.create(execution_id, **kwargs)
            with pytest.raises(HumanTaskVerificationError) as raised:
                await manager.resume(
                    int(task.id),
                    decision="resume",
                    verification={},
                    actor_id=1,
                )
            assert raised.value.code == "verification_missing"
            async with factory() as session:
                stored = await session.get(HumanTask, task.id)
                assert stored is not None
                return stored
        finally:
            await engine.dispose()

    task = asyncio.run(scenario())
    assert task.status == "pending"


def test_expired_task_cannot_resume_and_releases_execution_lock() -> None:
    async def scenario() -> tuple[HumanTask, ExecutionLog, list[int]]:
        factory, engine = await _database()
        execution_manager = FakeExecutionManager()
        try:
            execution_id = await _seed_execution(factory)
            manager = HumanTaskManager(
                session_factory=factory,
                execution_manager=execution_manager,
                verifier=FakeVerifier(
                    VerificationResult(ok=True, login_state="ready")
                ),
            )
            kwargs = _login_task_kwargs()
            kwargs["expires_at"] = datetime.utcnow() - timedelta(seconds=1)
            task = await manager.create(execution_id, **kwargs)
            with pytest.raises(HumanTaskVerificationError) as raised:
                await manager.resume(
                    int(task.id),
                    decision="resume",
                    verification={},
                    actor_id=1,
                )
            assert raised.value.code == "task_expired"
            async with factory() as session:
                stored_task = await session.get(HumanTask, task.id)
                execution = await session.get(ExecutionLog, execution_id)
                assert stored_task is not None
                assert execution is not None
                return (
                    stored_task,
                    execution,
                    execution_manager.dispatched,
                )
        finally:
            await engine.dispose()

    task, execution, dispatched = asyncio.run(scenario())

    assert task.status == "expired"
    assert task.resolved_at is not None
    assert execution.status == "failed"
    assert dispatched == []


def test_cancel_updates_task_and_execution_and_calls_runtime_cancel() -> None:
    async def scenario() -> tuple[HumanTask, ExecutionLog, list[tuple[int, str]]]:
        factory, engine = await _database()
        execution_manager = FakeExecutionManager()
        try:
            execution_id = await _seed_execution(factory)
            manager = HumanTaskManager(
                session_factory=factory,
                execution_manager=execution_manager,
            )
            task = await manager.create(
                execution_id,
                **_login_task_kwargs(),
            )
            cancelled = await manager.cancel(
                int(task.id),
                reason="用户取消",
                actor_id=1,
            )
            with pytest.raises(HumanTaskVerificationError) as raised:
                await manager.resume(
                    int(task.id),
                    decision="resume",
                    verification={},
                    actor_id=1,
                )
            assert raised.value.code == "task_cancelled"
            async with factory() as session:
                execution = await session.get(ExecutionLog, execution_id)
                assert execution is not None
                return cancelled, execution, execution_manager.cancelled
        finally:
            await engine.dispose()

    task, execution, cancelled = asyncio.run(scenario())

    assert task.status == "cancelled"
    assert execution.status == "cancelled"
    assert cancelled == [(execution.id, "用户取消")]


def test_concurrent_resume_dispatches_once() -> None:
    async def scenario() -> tuple[int, list[int]]:
        factory, engine = await _database()
        execution_manager = FakeExecutionManager()
        try:
            execution_id = await _seed_execution(factory)
            manager = HumanTaskManager(
                session_factory=factory,
                execution_manager=execution_manager,
                verifier=FakeVerifier(
                    VerificationResult(ok=True, login_state="ready")
                ),
            )
            task = await manager.create(
                execution_id,
                **_login_task_kwargs(),
            )
            results = await asyncio.gather(
                *[
                    manager.resume(
                        int(task.id),
                        decision="resume",
                        verification={},
                        actor_id=1,
                    )
                    for _ in range(4)
                ],
                return_exceptions=True,
            )
            assert not [item for item in results if isinstance(item, Exception)]
            return int(task.id), execution_manager.dispatched
        finally:
            await engine.dispose()

    task_id, dispatched = asyncio.run(scenario())

    assert task_id > 0
    assert len(dispatched) == 1


def test_cross_manager_concurrent_resume_claims_task_once() -> None:
    async def scenario() -> tuple[list[object], list[int]]:
        factory, engine = await _database()
        execution_manager = FakeExecutionManager()
        try:
            execution_id = await _seed_execution(factory)
            creator = HumanTaskManager(
                session_factory=factory,
                execution_manager=execution_manager,
                verifier=FakeVerifier(
                    VerificationResult(ok=True, login_state="ready")
                ),
            )
            task = await creator.create(
                execution_id,
                **_login_task_kwargs(),
            )
            managers = [
                HumanTaskManager(
                    session_factory=factory,
                    execution_manager=execution_manager,
                    verifier=FakeVerifier(
                        VerificationResult(
                            ok=True,
                            login_state="ready",
                        )
                    ),
                )
                for _ in range(2)
            ]
            results = await asyncio.gather(
                *[
                    manager.resume(
                        int(task.id),
                        decision="resume",
                        verification={},
                        actor_id=1,
                    )
                    for manager in managers
                ],
                return_exceptions=True,
            )
            return list(results), execution_manager.dispatched
        finally:
            await engine.dispose()

    results, dispatched = asyncio.run(scenario())

    assert len([item for item in results if isinstance(item, Exception)]) <= 1
    assert len(dispatched) == 1


def test_human_task_audit_records_each_status_transition() -> None:
    async def scenario() -> list[tuple[str, str, str]]:
        factory, engine = await _database()
        try:
            execution_id = await _seed_execution(factory)
            manager = HumanTaskManager(
                session_factory=factory,
                execution_manager=FakeExecutionManager(),
                verifier=FakeVerifier(
                    VerificationResult(ok=True, login_state="ready")
                ),
            )
            task = await manager.create(
                execution_id,
                **_login_task_kwargs(),
            )
            await manager.resume(
                int(task.id),
                decision="resume",
                verification={},
                actor_id=1,
            )
            async with factory() as session:
                rows = (
                    await session.execute(
                        select(HumanTaskEvent).order_by(HumanTaskEvent.id)
                    )
                ).scalars().all()
                return [
                    (
                        row.event_type,
                        str(row.from_status or ""),
                        row.to_status,
                    )
                    for row in rows
                ]
        finally:
            await engine.dispose()

    events = asyncio.run(scenario())

    assert events[0][0] == "created"
    assert events[0][2] == "pending"
    assert events[-1][0] == "resolved"
    assert events[-1][1] == "pending"
    assert events[-1][2] == "resolved"


def test_create_from_result_derives_kind_url_and_safe_payload() -> None:
    from app.services.human_tasks import build_human_task_spec

    result = SimpleNamespace(
        error="AUTH_REQUIRED: 登录已失效",
        screenshots=["logs/execution/auth.png"],
        details={
            "awaiting_user": True,
            "failed_step": {
                "step_index": 1,
                "step_id": 2,
                "action": "assert_logged_in",
                "failure_category": "auth_required",
                "current_url": (
                    "https://example.com/login?token=secret#fragment"
                ),
                "verification": {
                    "type": "web_logged_in",
                    "success_selector": "[data-user]",
                },
                "interaction_kind": "captcha_required",
            },
        },
    )

    spec = build_human_task_spec(
        result,
        execution_id=7,
        workflow_version_id=3,
        browser_profile_id=5,
        login_state="expired",
    )

    assert spec["kind"] == "captcha_required"
    assert spec["step_id"] == 2
    assert spec["screenshot_path"] == "logs/execution/auth.png"
    assert spec["payload"]["current_url"] == (
        "https://example.com/login"
    )
    assert "secret" not in str(spec)
    assert spec["payload"]["verification"]["success_selector"] == (
        "[data-user]"
    )
    assert spec["payload"]["browser_profile_id"] == 5


def test_legacy_checkpoint_task_uses_checkpoint_as_server_verification() -> None:
    async def scenario() -> tuple[HumanTask, ExecutionLog]:
        factory, engine = await _database()
        try:
            execution_id = await _seed_execution(factory)
            manager = HumanTaskManager(
                session_factory=factory,
                execution_manager=FakeExecutionManager(),
            )
            task = await manager.create(
                execution_id,
                step_id=2,
                kind="manual_confirmation",
                instruction="确认历史窗口状态",
                payload={
                    "legacy": True,
                    "verification": {"type": "legacy_checkpoint"},
                },
                dedupe_key="legacy:execution:1",
            )
            resolved = await manager.resume(
                int(task.id),
                decision="resume",
                verification={},
                actor_id=1,
            )
            async with factory() as session:
                execution = await session.get(ExecutionLog, execution_id)
                assert execution is not None
                return resolved, execution
        finally:
            await engine.dispose()

    task, execution = asyncio.run(scenario())

    assert task.status == "resolved"
    assert task.payload["resolution"]["type"] == "legacy_checkpoint"
    assert execution.status == "running"


def test_visual_candidate_resume_requires_persisted_candidate_id() -> None:
    async def scenario():
        factory, engine = await _database()
        execution_manager = FakeExecutionManager()
        try:
            execution_id = await _seed_execution(factory)
            manager = HumanTaskManager(
                session_factory=factory,
                execution_manager=execution_manager,
                verifier=FakeVerifier(
                    VerificationResult(ok=True, login_state="ready")
                ),
            )
            task = await manager.create(
                execution_id,
                step_id=2,
                kind="visual_ambiguous",
                instruction="请选择正确的目标",
                payload={
                    "action": "click",
                    "risk_level": "read_only",
                    "current_url": "https://example.com/orders",
                    "candidates": [
                        {
                            "candidate_id": "cand_abc123",
                            "target": "导出",
                            "bbox": [100, 200, 180, 240],
                            "confidence": 0.94,
                            "evidence": "按钮文字",
                            "expected_after": "开始下载",
                        },
                        {
                            "candidate_id": "cand_def456",
                            "target": "导出全部",
                            "bbox": [100, 250, 180, 290],
                            "confidence": 0.93,
                            "evidence": "按钮文字",
                            "expected_after": "开始下载",
                        },
                    ],
                    "verification": {"type": "visual_selection"},
                },
                dedupe_key="execution:1:step:2:visual_ambiguous",
            )
            with pytest.raises(HumanTaskVerificationError) as missing:
                await manager.resume(
                    int(task.id),
                    decision="resume",
                    verification={},
                    actor_id=1,
                )
            assert missing.value.code == "candidate_missing"
            with pytest.raises(HumanTaskVerificationError) as invalid:
                await manager.resume(
                    int(task.id),
                    decision="resume",
                    verification={"candidate_id": "cand_missing"},
                    actor_id=1,
                )
            assert invalid.value.code == "candidate_invalid"

            resolved = await manager.resume(
                int(task.id),
                decision="resume",
                verification={
                    "candidate_id": "cand_abc123",
                    "action": "delete",
                    "bbox": [0, 0, 1, 1],
                },
                actor_id=1,
            )
            async with factory() as session:
                stored_task = await session.get(HumanTask, task.id)
                execution = await session.get(ExecutionLog, execution_id)
                assert stored_task is not None
                assert execution is not None
                checkpoint = execution.step_logs[-1]
                return (
                    resolved,
                    stored_task,
                    execution,
                    checkpoint,
                    execution_manager.dispatched,
                )
        finally:
            await engine.dispose()

    task, stored_task, execution, checkpoint, dispatched = asyncio.run(
        scenario()
    )

    assert task.status == "resolved"
    assert stored_task.payload["resolution"]["candidate_id"] == "cand_abc123"
    assert stored_task.payload["resolution"]["action"] == "click"
    assert checkpoint["visual_selection"]["candidate_id"] == "cand_abc123"
    assert checkpoint["visual_selection"]["action"] == "click"
    assert execution.status == "running"
    assert dispatched == [execution.id]


def test_one_execution_only_exposes_one_active_human_task() -> None:
    async def scenario():
        factory, engine = await _database()
        try:
            execution_id = await _seed_execution(factory)
            manager = HumanTaskManager(
                session_factory=factory,
                execution_manager=FakeExecutionManager(),
            )
            first = await manager.create(
                execution_id,
                step_id=2,
                kind="login_required",
                instruction="先登录",
                payload={
                    "verification": {
                        "type": "web_logged_in",
                        "success_selector": "[data-user]",
                    }
                },
            )
            second = await manager.create(
                execution_id,
                step_id=3,
                kind="visual_ambiguous",
                instruction="再选择目标",
                payload={
                    "verification": {"type": "visual_selection"},
                    "action": "click",
                    "risk_level": "read_only",
                    "candidates": [
                        {
                            "candidate_id": "cand_x",
                            "target": "导出",
                            "bbox": [1, 2, 3, 4],
                            "confidence": 0.9,
                        }
                    ],
                },
                dedupe_key="different-active-key",
            )
            async with factory() as session:
                active = (
                    await session.execute(
                        select(HumanTask).where(
                            HumanTask.execution_id == execution_id,
                            HumanTask.status == "pending",
                        )
                    )
                ).scalars().all()
                return first.id, second.id, len(active)
        finally:
            await engine.dispose()

    first_id, second_id, active_count = asyncio.run(scenario())

    assert first_id == second_id
    assert active_count == 1
