from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet
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
    recover_interrupted_executions,
)
from app.engine.runner import ExecutionResult
from app.models.agent import DesktopAgent
from app.models.credential import Credential
from app.models.desktop_task import DesktopTask
from app.models.execution import ExecutionLog
from app.models.user import User
from app.models.workflow import Workflow
from tests.support.versioned_workflows import activate_test_version


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
    workflow = Workflow(
        user_id=1,
        name="Test workflow",
        natural_language_input="Test",
        status="draft",
    )
    session.add(workflow)
    await session.flush()
    await activate_test_version(session, workflow)
    await session.commit()
    await session.refresh(workflow)
    return workflow


class BlockingEngine:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.calls = 0

    async def execute_with_debug(
        self,
        _workflow,
        _context=None,
    ) -> ExecutionResult:
        self.calls += 1
        if self.calls == 1:
            self.started.set()
            await asyncio.Event().wait()
        return ExecutionResult(
            success=True,
            output="resumed",
            error=None,
            screenshots=[],
            step_logs=[],
            duration_seconds=0.1,
        )


def test_execution_manager_pauses_and_resumes() -> None:
    async def scenario() -> None:
        session_factory, engine = await _database()
        manager: ExecutionManager | None = None
        try:
            async with session_factory() as session:
                workflow = await _seed_workflow(session)

            fake_engine = BlockingEngine()
            manager = ExecutionManager(
                fake_engine,
                session_factory=session_factory,
            )
            execution = await manager.start_workflow(workflow.id)
            await asyncio.wait_for(fake_engine.started.wait(), timeout=1)

            paused = await manager.pause(execution.id)
            assert paused.status == "paused"
            assert paused.finished_at is not None

            async with session_factory() as session:
                stored = await session.get(ExecutionLog, execution.id)
                assert stored is not None
                assert stored.status == "paused"
                assert stored.finished_at is not None

            resumed = await manager.resume(execution.id)
            assert resumed.status == "running"
            assert resumed.finished_at is None

            for _ in range(100):
                async with session_factory() as session:
                    stored = await session.get(
                        ExecutionLog,
                        execution.id,
                    )
                    if stored is not None and stored.status == "success":
                        break
                await asyncio.sleep(0.01)

            assert stored is not None
            assert stored.status == "success"
            assert fake_engine.calls == 2
        finally:
            if manager is not None:
                await manager.shutdown()
            await engine.dispose()

    asyncio.run(scenario())


def test_recover_interrupted_executions_pauses_stale_records() -> None:
    async def scenario() -> None:
        session_factory, engine = await _database()
        try:
            async with session_factory() as session:
                workflow = await _seed_workflow(session)
                execution = ExecutionLog(
                    workflow_id=workflow.id,
                    status="running",
                )
                session.add(execution)
                await session.flush()
                task = DesktopTask(
                    workflow_id=workflow.id,
                    execution_log_id=execution.id,
                    status="dispatched",
                    actions=[],
                )
                session.add(task)
                await session.commit()
                execution_id = execution.id
                task_id = task.id

            recovered = await recover_interrupted_executions(
                session_factory
            )
            assert recovered == 1

            async with session_factory() as session:
                stored_execution = await session.get(
                    ExecutionLog,
                    execution_id,
                )
                stored_task = await session.get(DesktopTask, task_id)

            assert stored_execution is not None
            assert stored_execution.status == "paused"
            assert stored_execution.finished_at is not None
            assert stored_task is not None
            assert stored_task.status == "paused"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_execution_manager_rejects_terminal_pause() -> None:
    async def scenario() -> None:
        session_factory, engine = await _database()
        try:
            async with session_factory() as session:
                workflow = await _seed_workflow(session)
                execution = ExecutionLog(
                    workflow_id=workflow.id,
                    status="success",
                )
                session.add(execution)
                await session.commit()
                await session.refresh(execution)

            manager = ExecutionManager(
                SimpleNamespace(),
                session_factory=session_factory,
            )
            with pytest.raises(
                ExecutionStateError,
                match="Only running",
            ):
                await manager.pause(execution.id)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_execution_manager_routes_windows_workflow_to_repair() -> None:
    async def scenario() -> tuple[SimpleNamespace, SimpleNamespace]:
        session_factory, engine = await _database()
        try:
            async with session_factory() as session:
                workflow = await _seed_workflow(session)
                workflow.executor_type = "windows"
                await session.commit()
                workflow_id = workflow.id

            execution_engine = SimpleNamespace(
                execute_with_debug=AsyncMock()
            )
            repair = SimpleNamespace(
                execute=AsyncMock(
                    return_value=ExecutionResult(
                        success=True,
                        output="repaired",
                        error=None,
                        screenshots=[],
                        step_logs=[],
                        duration_seconds=0.2,
                    )
                )
            )
            manager = ExecutionManager(
                execution_engine,
                session_factory=session_factory,
                desktop_repair=repair,
            )
            execution = await manager.start_workflow(workflow_id)
            await manager.execute_existing(execution.id)
            return execution_engine, repair
        finally:
            await engine.dispose()

    execution_engine, repair = asyncio.run(scenario())

    repair.execute.assert_awaited_once()
    execution_engine.execute_with_debug.assert_not_awaited()


def test_execution_manager_keeps_web_execution_on_engine() -> None:
    async def scenario() -> SimpleNamespace:
        session_factory, engine = await _database()
        try:
            async with session_factory() as session:
                workflow = await _seed_workflow(session)
                workflow_id = workflow.id

            execution_engine = SimpleNamespace(
                execute_with_debug=AsyncMock(
                    return_value=ExecutionResult(
                        success=True,
                        output="web",
                        error=None,
                        screenshots=[],
                        step_logs=[],
                        duration_seconds=0.1,
                    )
                )
            )
            repair = SimpleNamespace(execute=AsyncMock())
            manager = ExecutionManager(
                execution_engine,
                session_factory=session_factory,
                desktop_repair=repair,
            )
            execution = await manager.start_workflow(workflow_id)
            await manager.execute_existing(execution.id)
            return repair
        finally:
            await engine.dispose()

    repair = asyncio.run(scenario())

    repair.execute.assert_not_awaited()


def test_execution_manager_delivers_artifacts(tmp_path: Path) -> None:
    async def scenario() -> None:
        session_factory, engine = await _database()
        try:
            async with session_factory() as session:
                workflow = await _seed_workflow(session)
                session.add(
                    DesktopAgent(
                        user_id=1,
                        name="DESKTOP-TEST",
                        version="0.1.0",
                        token_hash="hash",
                        allowed_doc_dirs=[str(tmp_path / "docs")],
                    )
                )
                await session.commit()

            documents = tmp_path / "docs"
            documents.mkdir()
            artifact_source = tmp_path / "logs" / "result.csv"
            artifact_source.parent.mkdir(parents=True, exist_ok=True)
            artifact_source.write_text("a,b\n1,2\n", encoding="utf-8")
            artifact_target = documents / "result.csv"

            artifact_engine = SimpleNamespace(
                execute_with_debug=AsyncMock(
                    return_value=ExecutionResult(
                        success=True,
                        output="exported",
                        error=None,
                        screenshots=[],
                        step_logs=[],
                        duration_seconds=0.1,
                        artifacts=[
                            {
                                "name": "result.csv",
                                "path": str(artifact_source),
                                "requested_path": str(artifact_target),
                                "size": artifact_source.stat().st_size,
                            }
                        ],
                    )
                )
            )
            manager = ExecutionManager(
                artifact_engine,
                session_factory=session_factory,
                config=SimpleNamespace(WEB_EXPORT_DIRS=""),
            )
            execution = await manager.start_workflow(workflow.id)
            await manager.execute_existing(execution.id)

            async with session_factory() as session:
                stored = await session.get(ExecutionLog, execution.id)
                assert stored is not None
                assert stored.status == "success"
                assert stored.artifacts is not None
                assert stored.artifacts[0]["delivered"] is True
                assert stored.artifacts[0]["delivered_path"] == (
                    str(artifact_target)
                )
            assert artifact_target.exists()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_execution_manager_persists_repaired_plan() -> None:
    repaired_plan = {
        "executor_type": "web",
        "execution_mode": "structured",
        "steps": [
            {
                "step_id": 1,
                "executor": "web",
                "action": "goto",
                "params": {"url": "https://example.com"},
                "retry_policy": {
                    "max_attempts": 2,
                    "backoff_seconds": 1.0,
                },
                "depends_on": [],
            }
        ],
        "variables": [],
    }

    async def scenario() -> None:
        session_factory, engine = await _database()
        try:
            async with session_factory() as session:
                workflow = await _seed_workflow(session)
                workflow_id = workflow.id

            execution_engine = SimpleNamespace(
                execute_with_debug=AsyncMock(
                    return_value=ExecutionResult(
                        success=True,
                        output="repaired",
                        error=None,
                        screenshots=[],
                        step_logs=[
                            {
                                "step_index": 0,
                                "action": "auto_repair",
                                "status": "repaired",
                            }
                        ],
                        duration_seconds=0.1,
                        details={"repaired_plan": repaired_plan},
                    )
                )
            )
            manager = ExecutionManager(
                execution_engine,
                session_factory=session_factory,
            )
            execution = await manager.start_workflow(workflow_id)
            await manager.execute_existing(execution.id)

            async with session_factory() as session:
                stored = await session.get(Workflow, workflow_id)
                assert stored is not None
                assert stored.structured_plan == repaired_plan
                log = await session.get(ExecutionLog, execution.id)
                assert log is not None
                assert log.step_logs[0]["action"] == "auto_repair"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


class CapturingEngine:
    def __init__(self, results: list[ExecutionResult]) -> None:
        self.results = list(results)
        self.contexts: list[dict | None] = []

    async def execute_with_debug(
        self,
        _workflow,
        context: dict | None = None,
    ) -> ExecutionResult:
        self.contexts.append(dict(context or {}))
        if len(self.results) > 1:
            return self.results.pop(0)
        return self.results[0]


def _manager_config(key: str, **overrides) -> SimpleNamespace:
    values = {
        "CREDENTIAL_ENCRYPTION_KEY": key,
        "AUTH_REQUIRED_PAUSE": True,
        "WEB_EXPORT_DIRS": "",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _encrypt(key: str, value: str) -> bytes:
    return Fernet(key).encrypt(value.encode("utf-8"))


def test_execution_manager_injects_credentials_and_redacts_logs() -> None:
    key = Fernet.generate_key().decode()
    secret = "super-secret"

    async def scenario() -> tuple[dict | None, ExecutionLog]:
        session_factory, engine = await _database()
        try:
            async with session_factory() as session:
                workflow = await _seed_workflow(session)
                session.add(
                    Credential(
                        workflow_id=workflow.id,
                        key="password",
                        encrypted_value=_encrypt(key, secret),
                    )
                )
                await session.commit()
                workflow_id = workflow.id

            result = ExecutionResult(
                success=False,
                output=f"output contains {secret}",
                error=f"failed with {secret}",
                screenshots=[],
                step_logs=[
                    {
                        "step_index": 0,
                        "action": "fill",
                        "status": "failed",
                        "error": f"fill failed: {secret}",
                    }
                ],
                duration_seconds=0.1,
            )
            execution_engine = CapturingEngine([result])
            manager = ExecutionManager(
                execution_engine,
                session_factory=session_factory,
                config=_manager_config(key),
            )
            execution = await manager.start_workflow(workflow_id)
            await manager.execute_existing(execution.id)

            async with session_factory() as session:
                stored = await session.get(ExecutionLog, execution.id)
                assert stored is not None
                return execution_engine.contexts[0], stored
        finally:
            await engine.dispose()

    context, stored = asyncio.run(scenario())

    assert context is not None
    assert context["credentials"] == {"password": secret}
    assert secret not in str(stored.step_logs)
    assert secret not in str(stored.error_message)
    assert "***" in str(stored.error_message)


def test_execution_manager_awaits_user_and_resumes_from_checkpoint() -> None:
    key = Fernet.generate_key().decode()
    awaiting_result = ExecutionResult(
        success=False,
        output="",
        error="AUTH_REQUIRED: 登录已失效，请重新登录",
        screenshots=[],
        step_logs=[
            {
                "step_index": 0,
                "action": "goto",
                "status": "success",
            },
            {
                "step_index": 1,
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
                "error": "AUTH_REQUIRED: 登录已失效，请重新登录",
            },
            "completed_idempotency_keys": ["open-page"],
        },
    )
    success_result = ExecutionResult(
        success=True,
        output="done",
        error=None,
        screenshots=[],
        step_logs=[
            {
                "step_index": 0,
                "action": "goto",
                "status": "success",
            },
            {
                "step_index": 1,
                "action": "assert_logged_in",
                "status": "failed",
            },
            {
                "step_index": 1,
                "action": "assert_logged_in",
                "status": "success",
            }
        ],
        duration_seconds=0.1,
    )

    async def scenario() -> tuple[ExecutionLog, list[dict | None]]:
        session_factory, engine = await _database()
        try:
            async with session_factory() as session:
                workflow = await _seed_workflow(session)
                workflow_id = workflow.id

            execution_engine = CapturingEngine(
                [awaiting_result, success_result]
            )
            manager = ExecutionManager(
                execution_engine,
                session_factory=session_factory,
                config=_manager_config(key),
            )
            execution = await manager.start_workflow(workflow_id)
            await manager.execute_existing(execution.id)

            async with session_factory() as session:
                stored = await session.get(ExecutionLog, execution.id)
                assert stored is not None
                assert stored.status == "awaiting_user"
                checkpoint = stored.step_logs[-1]
                assert checkpoint["type"] == "checkpoint"
                assert checkpoint["next_step_index"] == 1
                assert checkpoint["completed_idempotency_keys"] == [
                    "open-page"
                ]
                assert stored.finished_at is not None

            resumed = await manager.resume_after_login(execution.id)
            assert resumed.status == "running"
            await manager.execute_existing(execution.id)

            async with session_factory() as session:
                stored = await session.get(ExecutionLog, execution.id)
                assert stored is not None
                return stored, execution_engine.contexts
        finally:
            await engine.dispose()

    stored, contexts = asyncio.run(scenario())

    assert stored.status == "success"
    assert contexts[1] is not None
    assert contexts[1]["resume_from_step"] == 1
    assert contexts[1]["completed_idempotency_keys"] == ["open-page"]
    assert [
        entry["action"]
        for entry in contexts[1]["initial_step_logs"]
    ] == ["goto", "assert_logged_in"]
    assert all(
        entry.get("type") != "checkpoint"
        for entry in stored.step_logs
    )
    assert [entry["step_index"] for entry in stored.step_logs] == [
        0,
        1,
        1,
    ]


def test_execution_manager_keeps_failed_without_auth_pause() -> None:
    key = Fernet.generate_key().decode()
    awaiting_result = ExecutionResult(
        success=False,
        output="",
        error="AUTH_REQUIRED",
        screenshots=[],
        step_logs=[],
        duration_seconds=0.1,
        details={"awaiting_user": True},
    )

    async def scenario() -> ExecutionLog:
        session_factory, engine = await _database()
        try:
            async with session_factory() as session:
                workflow = await _seed_workflow(session)
                workflow_id = workflow.id

            manager = ExecutionManager(
                CapturingEngine([awaiting_result]),
                session_factory=session_factory,
                config=_manager_config(key, AUTH_REQUIRED_PAUSE=False),
            )
            execution = await manager.start_workflow(workflow_id)
            await manager.execute_existing(execution.id)
            async with session_factory() as session:
                stored = await session.get(ExecutionLog, execution.id)
                assert stored is not None
                return stored
        finally:
            await engine.dispose()

    stored = asyncio.run(scenario())

    assert stored.status == "failed"


def test_resume_after_login_rejects_completed_execution() -> None:
    key = Fernet.generate_key().decode()

    async def scenario() -> None:
        session_factory, engine = await _database()
        try:
            async with session_factory() as session:
                workflow = await _seed_workflow(session)
                execution = ExecutionLog(
                    workflow_id=workflow.id,
                    status="success",
                )
                session.add(execution)
                await session.commit()
                await session.refresh(execution)

            manager = ExecutionManager(
                SimpleNamespace(),
                session_factory=session_factory,
                config=_manager_config(key),
            )
            with pytest.raises(
                ExecutionStateError,
                match="awaiting_user",
            ):
                await manager.resume_after_login(execution.id)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


class CapturingDesktopExecutor:
    def __init__(self, results: list[ExecutionResult]) -> None:
        self.results = list(results)
        self.contexts: list[dict] = []

    async def execute(
        self,
        _workflow,
        context: dict | None = None,
    ) -> ExecutionResult:
        self.contexts.append(dict(context or {}))
        if len(self.results) > 1:
            return self.results.pop(0)
        return self.results[0]


def test_desktop_failure_writes_checkpoint_and_resumes() -> None:
    failed_result = ExecutionResult(
        success=False,
        output="",
        error="WPS 文档服务繁忙",
        screenshots=[],
        step_logs=[
            {
                "step_index": 0,
                "step_id": 1,
                "action_type": "create_document",
                "status": "success",
                "idempotency_key": "wps-doc-created",
            }
        ],
        duration_seconds=0.2,
        details={
            "failed_step": {
                "step_index": 1,
                "step_id": 2,
                "action_type": "insert_image",
                "idempotency_key": "wps-image-inserted",
                "error": "WPS 文档服务繁忙",
            },
            "completed_idempotency_keys": ["wps-doc-created"],
        },
    )
    success_result = ExecutionResult(
        success=True,
        output="done",
        error=None,
        screenshots=[],
        step_logs=[
            {
                "step_index": 0,
                "step_id": 1,
                "action_type": "create_document",
                "status": "skipped",
                "idempotency_key": "wps-doc-created",
            },
            {
                "step_index": 1,
                "step_id": 2,
                "action_type": "insert_image",
                "status": "success",
                "idempotency_key": "wps-image-inserted",
            },
        ],
        duration_seconds=0.1,
    )

    async def scenario() -> tuple[ExecutionLog, list[dict]]:
        session_factory, engine = await _database()
        try:
            async with session_factory() as session:
                workflow = await _seed_workflow(session)
                workflow.executor_type = "windows"
                await session.commit()
                workflow_id = workflow.id

            desktop_executor = CapturingDesktopExecutor(
                [failed_result, success_result]
            )
            manager = ExecutionManager(
                SimpleNamespace(),
                session_factory=session_factory,
                desktop_repair=desktop_executor,
            )
            execution = await manager.start_workflow(workflow_id)
            await manager.execute_existing(execution.id)

            async with session_factory() as session:
                stored = await session.get(ExecutionLog, execution.id)
                assert stored is not None
                assert stored.status == "failed"
                checkpoint = stored.step_logs[-1]
                assert checkpoint["type"] == "checkpoint"
                assert checkpoint["next_step_index"] == 1
                assert checkpoint["completed_idempotency_keys"] == [
                    "wps-doc-created"
                ]

            resumed = await manager.resume(execution.id)
            assert resumed.status == "running"
            await manager.execute_existing(execution.id)

            async with session_factory() as session:
                final = await session.get(ExecutionLog, execution.id)
                assert final is not None
                return final, desktop_executor.contexts
        finally:
            await engine.dispose()

    stored, contexts = asyncio.run(scenario())

    assert stored.status == "success"
    assert contexts[1]["resume_from_step"] == 1
    assert contexts[1]["next_step_index"] == 1
    assert contexts[1]["completed_idempotency_keys"] == [
        "wps-doc-created"
    ]
    assert [entry["status"] for entry in stored.step_logs] == [
        "skipped",
        "success",
    ]
    assert all(
        entry.get("type") != "checkpoint"
        for entry in stored.step_logs
    )


class BlockingDesktopExecutor:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.contexts: list[dict] = []
        self.calls = 0

    async def execute(
        self,
        _workflow,
        context: dict | None = None,
    ) -> ExecutionResult:
        self.calls += 1
        self.contexts.append(dict(context or {}))
        if self.calls == 1:
            self.started.set()
            await asyncio.Event().wait()
        return ExecutionResult(
            success=True,
            output="resumed",
            error=None,
            screenshots=[],
            step_logs=[
                {
                    "step_index": 0,
                    "step_id": 1,
                    "action_type": "create_document",
                    "status": "skipped",
                    "idempotency_key": "wps-doc-created",
                },
                {
                    "step_index": 1,
                    "step_id": 2,
                    "action_type": "insert_image",
                    "status": "success",
                },
            ],
            duration_seconds=0.1,
        )


def test_desktop_resume_keeps_checkpoint_when_agent_unavailable() -> None:
    failed_result = ExecutionResult(
        success=False,
        output="",
        error="WPS 文档服务繁忙",
        screenshots=[],
        step_logs=[
            {
                "step_index": 0,
                "step_id": 1,
                "action_type": "create_document",
                "status": "success",
                "idempotency_key": "wps-doc-created",
            }
        ],
        duration_seconds=0.2,
        details={
            "failed_step": {
                "step_index": 1,
                "step_id": 2,
                "action_type": "insert_image",
                "error": "WPS 文档服务繁忙",
            },
            "completed_idempotency_keys": ["wps-doc-created"],
        },
    )
    unavailable_result = ExecutionResult(
        success=False,
        output="",
        error="No available desktop agent",
        screenshots=[],
        step_logs=[],
        duration_seconds=0.0,
    )

    async def scenario() -> ExecutionLog:
        session_factory, engine = await _database()
        try:
            async with session_factory() as session:
                workflow = await _seed_workflow(session)
                workflow.executor_type = "windows"
                await session.commit()
                workflow_id = workflow.id

            desktop_executor = CapturingDesktopExecutor(
                [failed_result, unavailable_result]
            )
            manager = ExecutionManager(
                SimpleNamespace(),
                session_factory=session_factory,
                desktop_repair=desktop_executor,
            )
            execution = await manager.start_workflow(workflow_id)
            await manager.execute_existing(execution.id)

            await manager.resume(execution.id)
            await manager.execute_existing(execution.id)

            async with session_factory() as session:
                stored = await session.get(ExecutionLog, execution.id)
                assert stored is not None
                return stored
        finally:
            await engine.dispose()

    stored = asyncio.run(scenario())

    assert stored.status == "failed"
    checkpoint = stored.step_logs[-1]
    assert checkpoint["type"] == "checkpoint"
    assert checkpoint["next_step_index"] == 1
    assert checkpoint["completed_idempotency_keys"] == [
        "wps-doc-created"
    ]
    assert [
        entry["status"]
        for entry in stored.step_logs
        if entry.get("type") != "checkpoint"
    ] == ["success"]


class RepeatingBlockingDesktopExecutor:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.calls = 0

    async def execute(
        self,
        _workflow,
        context: dict | None = None,
    ) -> ExecutionResult:
        self.calls += 1
        self.started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


def test_desktop_second_pause_extends_checkpoint() -> None:
    async def scenario() -> ExecutionLog:
        session_factory, engine = await _database()
        manager: ExecutionManager | None = None
        try:
            async with session_factory() as session:
                workflow = await _seed_workflow(session)
                workflow.executor_type = "windows"
                await session.commit()
                workflow_id = workflow.id

            desktop_executor = RepeatingBlockingDesktopExecutor()
            manager = ExecutionManager(
                SimpleNamespace(),
                session_factory=session_factory,
                desktop_repair=desktop_executor,
            )
            execution = await manager.start_workflow(workflow_id)
            await asyncio.wait_for(
                desktop_executor.started.wait(),
                timeout=1,
            )

            async with session_factory() as session:
                stored = await session.get(ExecutionLog, execution.id)
                assert stored is not None
                stored.step_logs = [
                    {
                        "step_index": 0,
                        "step_id": 1,
                        "action_type": "create_document",
                        "status": "success",
                        "idempotency_key": "wps-doc-created",
                    }
                ]
                await session.commit()

            await manager.pause(execution.id)
            await manager.resume(execution.id)
            for _ in range(200):
                if desktop_executor.calls >= 2:
                    break
                await asyncio.sleep(0.005)
            assert desktop_executor.calls >= 2

            async with session_factory() as session:
                stored = await session.get(ExecutionLog, execution.id)
                assert stored is not None
                step_logs = [
                    entry
                    for entry in stored.step_logs
                    if entry.get("type") != "checkpoint"
                ]
                step_logs[0] = {
                    "step_index": 0,
                    "step_id": 1,
                    "action_type": "create_document",
                    "status": "skipped",
                    "idempotency_key": "wps-doc-created",
                }
                step_logs.append(
                    {
                        "step_index": 1,
                        "step_id": 2,
                        "action_type": "insert_image",
                        "status": "success",
                        "idempotency_key": "wps-image-inserted",
                    }
                )
                stored.step_logs = [
                    *step_logs,
                    *[
                        entry
                        for entry in stored.step_logs
                        if entry.get("type") == "checkpoint"
                    ],
                ]
                await session.commit()

            await manager.pause(execution.id)

            async with session_factory() as session:
                stored = await session.get(ExecutionLog, execution.id)
                assert stored is not None
                return stored
        finally:
            if manager is not None:
                await manager.shutdown()
            await engine.dispose()

    stored = asyncio.run(scenario())

    assert stored.status == "paused"
    checkpoint = stored.step_logs[-1]
    assert checkpoint["type"] == "checkpoint"
    assert checkpoint["next_step_index"] == 2
    assert checkpoint["completed_idempotency_keys"] == [
        "wps-doc-created",
        "wps-image-inserted",
    ]


def test_desktop_pause_writes_checkpoint_and_resumes() -> None:
    async def scenario() -> tuple[ExecutionLog, list[dict]]:
        session_factory, engine = await _database()
        manager: ExecutionManager | None = None
        try:
            async with session_factory() as session:
                workflow = await _seed_workflow(session)
                workflow.executor_type = "windows"
                await session.commit()
                workflow_id = workflow.id

            desktop_executor = BlockingDesktopExecutor()
            manager = ExecutionManager(
                SimpleNamespace(),
                session_factory=session_factory,
                desktop_repair=desktop_executor,
            )
            execution = await manager.start_workflow(workflow_id)
            await asyncio.wait_for(
                desktop_executor.started.wait(),
                timeout=1,
            )

            async with session_factory() as session:
                stored = await session.get(ExecutionLog, execution.id)
                assert stored is not None
                stored.step_logs = [
                    {
                        "step_index": 0,
                        "step_id": 1,
                        "action_type": "create_document",
                        "status": "success",
                        "idempotency_key": "wps-doc-created",
                    }
                ]
                await session.commit()

            paused = await manager.pause(execution.id)
            assert paused.status == "paused"

            async with session_factory() as session:
                paused_log = await session.get(
                    ExecutionLog,
                    execution.id,
                )
                assert paused_log is not None
                checkpoint = paused_log.step_logs[-1]
                assert checkpoint["type"] == "checkpoint"
                assert checkpoint["next_step_index"] == 1
                assert checkpoint["completed_idempotency_keys"] == [
                    "wps-doc-created"
                ]

            resumed = await manager.resume(execution.id)
            assert resumed.status == "running"
            await manager.execute_existing(execution.id)

            async with session_factory() as session:
                final = await session.get(ExecutionLog, execution.id)
                assert final is not None
                return final, desktop_executor.contexts
        finally:
            if manager is not None:
                await manager.shutdown()
            await engine.dispose()

    stored, contexts = asyncio.run(scenario())

    assert stored.status == "success"
    assert contexts[1]["resume_from_step"] == 1
    assert contexts[1]["completed_idempotency_keys"] == [
        "wps-doc-created"
    ]
    assert [entry["status"] for entry in stored.step_logs] == [
        "skipped",
        "success",
    ]
