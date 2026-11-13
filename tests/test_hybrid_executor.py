from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.engine.execution_manager import ExecutionManager
from app.engine.hybrid_executor import HybridExecutor
from app.engine.runner import ExecutionResult
from app.models.execution import ExecutionLog
from app.models.user import User
from app.models.workflow import Workflow
from app.models.workflow_version import WorkflowVersion


async def _activate_workflow(
    session: AsyncSession,
    workflow: Workflow,
) -> WorkflowVersion:
    version = WorkflowVersion(
        workflow_id=workflow.id,
        version=1,
        plan=dict(workflow.structured_plan or {}),
        validation={"valid": True},
        action_catalog_version="2026-10-28.1",
    )
    session.add(version)
    await session.flush()
    workflow.active_version_id = version.id
    workflow.status = "published"
    return version


class FakeWebExecutor:
    """记录网页步骤调用，并模拟同一个浏览器页的复用。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.pages: list[Any] = []
        self.hooks: dict[int, Any] = {}
        self.concurrency = 0
        self.max_concurrency = 0

    @asynccontextmanager
    async def browser_session(self) -> Any:
        yield object()

    async def execute_steps(
        self,
        steps: list[dict[str, Any]],
        *,
        page: Any,
        context: dict[str, Any] | None = None,
        execution_id: str | None = None,
        step_offset: int = 0,
        completed_idempotency_keys: list[str] | None = None,
        **_: Any,
    ) -> ExecutionResult:
        step = steps[0]
        self.calls.append(step)
        self.pages.append(page)
        self.concurrency += 1
        self.max_concurrency = max(
            self.max_concurrency,
            self.concurrency,
        )
        try:
            hook = self.hooks.get(int(step.get("step_id") or 0))
            if hook is not None:
                await hook(context or {})
            keys = list(completed_idempotency_keys or [])
            key = step.get("idempotency_key")
            if key and key not in keys:
                keys.append(key)
            return ExecutionResult(
                success=True,
                output=f"web {step.get('action')} ok",
                error=None,
                screenshots=[],
                step_logs=[
                    {
                        "step_index": step_offset,
                        "step_id": step.get("step_id"),
                        "executor": "web",
                        "action": step.get("action"),
                        "status": "success",
                    }
                ],
                duration_seconds=0.01,
                details={
                    "executor": "web",
                    "completed_idempotency_keys": keys,
                },
            )
        finally:
            self.concurrency -= 1


class FakeDesktopExecutor:
    """模拟桌面步骤执行器，可注入多次结果。"""

    def __init__(
        self,
        results: list[ExecutionResult] | None = None,
    ) -> None:
        self.results = list(results or [])
        self.calls: list[tuple[Any, dict[str, Any]]] = []
        self.hooks: dict[int, Any] = {}

    async def execute(
        self,
        workflow: Any,
        context: dict[str, Any] | None = None,
    ) -> ExecutionResult:
        request = dict(context or {})
        self.calls.append((workflow, request))
        step = workflow.structured_plan["steps"][0]
        hook = self.hooks.get(int(step.get("step_id") or 0))
        if hook is not None:
            await hook(request)
        if self.results:
            return self.results.pop(0)
        keys = list(request.get("completed_idempotency_keys") or [])
        key = step.get("idempotency_key")
        if key and key not in keys:
            keys.append(key)
        return ExecutionResult(
            success=True,
            output=f"desktop {step.get('action')} ok",
            error=None,
            screenshots=[],
            step_logs=[
                {
                    "step_index": request.get("step_offset", 0),
                    "step_id": step.get("step_id"),
                    "executor": "windows",
                    "action": step.get("action"),
                    "status": "success",
                }
            ],
            duration_seconds=0.01,
            details={
                "completed_idempotency_keys": keys,
            },
        )


def _engine(web: FakeWebExecutor) -> Any:
    return SimpleNamespace(web_executor=web)


def test_hybrid_routes_steps_by_executor_and_reuses_page() -> None:
    async def scenario() -> tuple[ExecutionResult, FakeWebExecutor, FakeDesktopExecutor]:
        web = FakeWebExecutor()
        desktop = FakeDesktopExecutor()
        executor = HybridExecutor(_engine(web), desktop)
        workflow = SimpleNamespace(
            id=1,
            user_id=1,
            name="mix",
            executor_type="web",
            structured_plan={
                "executor_type": "web",
                "steps": [
                    {
                        "step_id": 1,
                        "executor": "web",
                        "action": "goto",
                        "params": {"url": "https://example.com"},
                        "depends_on": [],
                    },
                    {
                        "step_id": 2,
                        "executor": "windows",
                        "action": "launch_app",
                        "params": {"path": "notepad.exe"},
                        "depends_on": [1],
                    },
                    {
                        "step_id": 3,
                        "executor": "web",
                        "action": "click",
                        "params": {},
                        "depends_on": [2],
                    },
                ],
            },
        )
        result = await executor.execute(workflow)
        return result, web, desktop

    result, web, desktop = asyncio.run(scenario())

    assert result.success is True
    assert [call["action"] for call in web.calls] == ["goto", "click"]
    assert web.pages[0] is web.pages[1]
    assert len(desktop.calls) == 1
    desktop_step = desktop.calls[0][0].structured_plan["steps"][0]
    assert desktop_step["action"] == "launch_app"
    assert desktop.calls[0][1]["step_offset"] == 1
    assert [entry["status"] for entry in result.step_logs] == [
        "success",
        "success",
        "success",
    ]


def test_hybrid_task_runtime_passes_variables_across_adapters() -> None:
    async def scenario() -> tuple[ExecutionResult, FakeDesktopExecutor]:
        web = FakeWebExecutor()
        desktop = FakeDesktopExecutor(
            results=[
                ExecutionResult(
                    success=True,
                    output="copied",
                    error=None,
                    screenshots=[],
                    step_logs=[
                        {
                            "step_index": 0,
                            "step_id": 1,
                            "executor": "windows",
                            "action": "copy_selected_text",
                            "status": "success",
                            "result": {"text": "第一句\n第二句"},
                        }
                    ],
                    duration_seconds=0.01,
                    details={},
                ),
                ExecutionResult(
                    success=True,
                    output="spreadsheet",
                    error=None,
                    screenshots=[],
                    step_logs=[
                        {
                            "step_index": 2,
                            "step_id": 3,
                            "executor": "files",
                            "action": "create_spreadsheet",
                            "status": "success",
                        }
                    ],
                    duration_seconds=0.01,
                    details={},
                ),
            ]
        )
        executor = HybridExecutor(_engine(web), desktop)
        workflow = SimpleNamespace(
            id=1,
            user_id=1,
            name="runtime",
            executor_type="hybrid",
            structured_plan={
                "executor_type": "hybrid",
                "variables": [
                    {"name": "lyrics", "default": ""},
                    {"name": "lyrics_lines", "default": []},
                ],
                "steps": [
                    {
                        "step_id": 1,
                        "executor": "windows",
                        "action": "copy_selected_text",
                        "result_var": "lyrics",
                        "params": {"keys": "^a^c"},
                    },
                    {
                        "step_id": 2,
                        "executor": "transform",
                        "action": "split_lines",
                        "result_var": "lyrics_lines",
                        "params": {"text": "${lyrics}"},
                        "depends_on": [1],
                    },
                    {
                        "step_id": 3,
                        "executor": "files",
                        "action": "create_spreadsheet",
                        "params": {
                            "path": "lyrics.xlsx",
                            "sheet_name": "歌词",
                            "columns": [
                                {
                                    "header": "歌词",
                                    "values": "${lyrics_lines}",
                                }
                            ],
                        },
                        "depends_on": [2],
                    },
                ],
            },
        )
        return await executor.execute(workflow), desktop

    result, desktop = asyncio.run(scenario())

    assert result.success is True, result.error
    assert (result.details or {})["execution_mode"] == "task_runtime"
    assert (result.details or {})["extracted"]["lyrics_lines"] == [
        "第一句",
        "第二句",
    ]
    assert len(desktop.calls) == 2
    assert desktop.calls[0][0].structured_plan["steps"][0]["action"] == (
        "copy_selected_text"
    )
    assert desktop.calls[1][0].structured_plan["steps"][0]["action"] == (
        "create_spreadsheet"
    )


def test_hybrid_resolves_web_executor_from_router() -> None:
    async def scenario() -> tuple[ExecutionResult, FakeWebExecutor]:
        web = FakeWebExecutor()
        router = SimpleNamespace(
            web_engine=SimpleNamespace(web_executor=web),
        )
        executor = HybridExecutor(router)
        workflow = SimpleNamespace(
            id=1,
            executor_type="web",
            structured_plan={
                "steps": [
                    {
                        "step_id": 1,
                        "executor": "web",
                        "action": "goto",
                        "depends_on": [],
                    }
                ]
            },
        )
        return await executor.execute(workflow), web

    result, web = asyncio.run(scenario())

    assert result.success is True
    assert [call["action"] for call in web.calls] == ["goto"]


def test_task_runtime_exposes_data_counts_in_step_logs() -> None:
    async def scenario() -> ExecutionResult:
        executor = HybridExecutor(
            _engine(FakeWebExecutor()),
            FakeDesktopExecutor(),
        )
        workflow = SimpleNamespace(
            id=1,
            user_id=1,
            executor_type="hybrid",
            structured_plan={
                "executor_type": "hybrid",
                "variables": [
                    {
                        "name": "rows",
                        "default": [
                            {"amount": 1200},
                            {"amount": 700},
                            {"amount": 1500},
                        ],
                    },
                ],
                "steps": [
                    {
                        "step_id": 1,
                        "executor": "transform",
                        "action": "filter",
                        "result_var": "large_rows",
                        "params": {
                            "values": "${rows}",
                            "field": "amount",
                            "operator": "gt",
                            "cast": "number",
                            "value": 1000,
                        },
                        "depends_on": [],
                    }
                ],
            },
        )
        return await executor.execute(workflow)

    result = asyncio.run(scenario())

    assert result.success is True, result.error
    data_log = next(
        entry for entry in result.step_logs
        if entry.get("action") == "filter"
    )
    assert data_log["verification"]["input_count"] == 3
    assert data_log["verification"]["output_count"] == 2


def test_hybrid_runs_safe_branches_in_parallel() -> None:
    async def scenario() -> tuple[ExecutionResult, FakeWebExecutor]:
        web = FakeWebExecutor()
        desktop = FakeDesktopExecutor()
        web_started = asyncio.Event()
        desktop_started = asyncio.Event()
        both_started = asyncio.Event()

        async def web_hook(_context: dict[str, Any]) -> None:
            web_started.set()
            if desktop_started.is_set():
                both_started.set()
            await asyncio.wait_for(both_started.wait(), timeout=1)

        async def desktop_hook(_context: dict[str, Any]) -> None:
            desktop_started.set()
            if web_started.is_set():
                both_started.set()
            await asyncio.wait_for(both_started.wait(), timeout=1)

        web.hooks[1] = web_hook
        desktop.hooks[2] = desktop_hook
        executor = HybridExecutor(_engine(web), desktop)
        workflow = SimpleNamespace(
            id=1,
            user_id=1,
            name="branches",
            executor_type="web",
            structured_plan={
                "steps": [
                    {
                        "step_id": 1,
                        "executor": "web",
                        "action": "goto",
                        "depends_on": [],
                    },
                    {
                        "step_id": 2,
                        "executor": "windows",
                        "action": "launch_app",
                        "depends_on": [],
                    },
                    {
                        "step_id": 3,
                        "executor": "web",
                        "action": "click",
                        "depends_on": [1, 2],
                    },
                ]
            },
        )
        result = await executor.execute(workflow)
        return result, web

    result, web = asyncio.run(scenario())

    assert result.success is True
    assert web.max_concurrency == 1  # 同一浏览器页串行
    assert web_started_hooks_ran(result)


def web_started_hooks_ran(result: ExecutionResult) -> bool:
    actions = [
        entry.get("action") for entry in result.step_logs
    ]
    return actions == ["goto", "launch_app", "click"]


def test_hybrid_skips_conditional_and_blocked_steps() -> None:
    async def scenario() -> tuple[ExecutionResult, FakeWebExecutor]:
        web = FakeWebExecutor()
        desktop = FakeDesktopExecutor(
            results=[
                ExecutionResult(
                    success=False,
                    output="",
                    error="launch failed",
                    screenshots=[],
                    step_logs=[
                        {
                            "step_index": 1,
                            "step_id": 2,
                            "executor": "windows",
                            "action": "launch_app",
                            "status": "failed",
                        }
                    ],
                    duration_seconds=0.01,
                    details={
                        "failed_step": {
                            "step_id": 2,
                            "action": "launch_app",
                            "error": "launch failed",
                            "failure_category": "app_not_found",
                        }
                    },
                )
            ]
        )
        executor = HybridExecutor(_engine(web), desktop)
        workflow = SimpleNamespace(
            id=1,
            user_id=1,
            name="conditions",
            executor_type="web",
            structured_plan={
                "steps": [
                    {
                        "step_id": 1,
                        "executor": "web",
                        "action": "goto",
                        "depends_on": [],
                    },
                    {
                        "step_id": 2,
                        "executor": "windows",
                        "action": "launch_app",
                        "depends_on": [1],
                    },
                    {
                        "step_id": 3,
                        "executor": "web",
                        "action": "click",
                        "depends_on": [2],
                    },
                    {
                        "step_id": 4,
                        "executor": "web",
                        "action": "extract_text",
                        "depends_on": [],
                        "condition": {
                            "type": "variable",
                            "name": "flag",
                            "equals": True,
                        },
                    },
                    {
                        "step_id": 5,
                        "executor": "web",
                        "action": "screenshot",
                        "depends_on": [],
                    },
                ]
            },
        )
        result = await executor.execute(
            workflow,
            {"variables": {"flag": False}},
        )
        return result, web

    result, web = asyncio.run(scenario())

    assert result.success is False
    assert (result.details or {})["failed_step"]["step_id"] == 2
    assert set((result.details or {})["skipped_steps"]) == {3, 4}
    executed_actions = [
        call["action"] for call in web.calls
    ]
    assert executed_actions.count("screenshot") == 1
    assert "click" not in executed_actions
    assert "extract_text" not in executed_actions
    blocked = [
        entry
        for entry in result.step_logs
        if entry.get("skip_reason") == "dependency_failed"
    ]
    assert blocked and blocked[0]["step_id"] == 3


def test_hybrid_resume_skips_completed_idempotency_keys() -> None:
    async def scenario() -> tuple[ExecutionResult, FakeWebExecutor]:
        web = FakeWebExecutor()
        desktop = FakeDesktopExecutor()
        executor = HybridExecutor(_engine(web), desktop)
        workflow = SimpleNamespace(
            id=1,
            user_id=1,
            name="resume",
            executor_type="web",
            structured_plan={
                "steps": [
                    {
                        "step_id": 1,
                        "executor": "web",
                        "action": "goto",
                        "idempotency_key": "open-page",
                        "depends_on": [],
                    },
                    {
                        "step_id": 2,
                        "executor": "windows",
                        "action": "type_text",
                        "idempotency_key": "type-text",
                        "depends_on": [1],
                    },
                ]
            },
        )
        result = await executor.execute(
            workflow,
            {"completed_idempotency_keys": ["open-page"]},
        )
        return result, web

    result, web = asyncio.run(scenario())

    assert result.success is True
    assert web.calls == []
    assert [entry["status"] for entry in result.step_logs] == [
        "skipped",
        "success",
    ]
    assert (result.details or {})["completed_idempotency_keys"] == [
        "open-page",
        "type-text",
    ]


def test_dag_definition_edges_control_execution_order() -> None:
    async def scenario() -> tuple[ExecutionResult, list[str]]:
        web = FakeWebExecutor()
        desktop = FakeDesktopExecutor()
        order: list[str] = []

        async def record_web(_context: dict[str, Any]) -> None:
            order.append("click")

        async def record_desktop(_context: dict[str, Any]) -> None:
            order.append("launch_app")

        web.hooks[2] = record_web
        desktop.hooks[1] = record_desktop
        executor = HybridExecutor(_engine(web), desktop)
        workflow = SimpleNamespace(
            id=1,
            user_id=1,
            name="dag-definition",
            executor_type="web",
            structured_plan={
                "executor_type": "web",
                "steps": [
                    {
                        "step_id": 2,
                        "executor": "web",
                        "action": "click",
                    },
                    {
                        "step_id": 1,
                        "executor": "windows",
                        "action": "launch_app",
                    },
                ],
            },
            dag_definition={
                "nodes": [
                    {"id": "step-1", "kind": "step"},
                    {"id": "step-2", "kind": "step"},
                ],
                "edges": [
                    {"id": "e1", "source": "step-1", "target": "step-2"}
                ],
            },
        )
        result = await executor.execute(workflow)
        return result, order

    result, order = asyncio.run(scenario())

    assert result.success is True
    assert order == ["launch_app", "click"]


def test_hybrid_rejects_cyclic_plan() -> None:
    async def scenario() -> ExecutionResult:
        executor = HybridExecutor(_engine(FakeWebExecutor()))
        workflow = SimpleNamespace(
            id=1,
            executor_type="web",
            structured_plan={
                "steps": [
                    {
                        "step_id": 1,
                        "executor": "web",
                        "action": "a",
                        "depends_on": [2],
                    },
                    {
                        "step_id": 2,
                        "executor": "web",
                        "action": "b",
                        "depends_on": [1],
                    },
                ]
            },
        )
        return await executor.execute(workflow)

    result = asyncio.run(scenario())

    assert result.success is False
    assert "环" in (result.error or "")


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


def test_manager_persists_and_reuses_hybrid_checkpoint() -> None:
    async def scenario() -> tuple[ExecutionLog, FakeDesktopExecutor]:
        session_factory, engine = await _database()
        try:
            async with session_factory() as session:
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
                    name="hybrid-resume",
                    natural_language_input="test",
                    executor_type="web",
                    structured_plan={
                        "steps": [
                            {
                                "step_id": 1,
                                "executor": "web",
                                "action": "goto",
                                "idempotency_key": "open-page",
                                "depends_on": [],
                            },
                            {
                                "step_id": 2,
                                "executor": "windows",
                                "action": "type_text",
                                "idempotency_key": "type-text",
                                "depends_on": [1],
                            },
                        ]
                    },
                )
                session.add(workflow)
                await session.flush()
                await _activate_workflow(session, workflow)
                await session.commit()
                await session.refresh(workflow)
                workflow_id = workflow.id

            web = FakeWebExecutor()
            desktop = FakeDesktopExecutor(
                results=[
                    ExecutionResult(
                        success=False,
                        output="",
                        error="type failed",
                        screenshots=[],
                        step_logs=[
                            {
                                "step_index": 1,
                                "step_id": 2,
                                "executor": "windows",
                                "action": "type_text",
                                "status": "failed",
                            }
                        ],
                        duration_seconds=0.01,
                        details={
                            "failed_step": {
                                "step_id": 2,
                                "action": "type_text",
                                "error": "type failed",
                            }
                        },
                    )
                ]
            )
            manager = ExecutionManager(
                _engine(web),
                session_factory=session_factory,
            )
            manager._hybrid = HybridExecutor(
                _engine(web),
                desktop,
                session_factory=session_factory,
            )
            execution = await manager.start_workflow(workflow_id)
            await manager.execute_existing(execution.id)

            async with session_factory() as session:
                stored = await session.get(ExecutionLog, execution.id)
                assert stored is not None
                checkpoint = stored.step_logs[-1]
                assert checkpoint["type"] == "checkpoint"
                assert checkpoint["completed_idempotency_keys"] == [
                    "open-page"
                ]

            await manager.resume(execution.id)
            await manager.execute_existing(execution.id)
            async with session_factory() as session:
                final = await session.get(ExecutionLog, execution.id)
                assert final is not None
                return final, desktop
        finally:
            await engine.dispose()

    final, desktop = asyncio.run(scenario())

    assert final.status == "success"
    assert [entry["status"] for entry in final.step_logs] == [
        "skipped",
        "success",
    ]
    assert desktop.calls[-1][1]["completed_idempotency_keys"] == [
        "open-page"
    ]


def test_manager_routes_mixed_plan_to_hybrid_not_engine() -> None:
    async def scenario() -> ExecutionManager:
        session_factory, engine = await _database()
        try:
            async with session_factory() as session:
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
                    name="mixed",
                    natural_language_input="test",
                    executor_type="web",
                    structured_plan={
                        "steps": [
                            {
                                "step_id": 1,
                                "executor": "web",
                                "action": "goto",
                                "depends_on": [],
                            },
                            {
                                "step_id": 2,
                                "executor": "windows",
                                "action": "launch_app",
                                "depends_on": [1],
                            },
                        ]
                    },
                )
                session.add(workflow)
                await session.flush()
                await _activate_workflow(session, workflow)
                await session.commit()
                await session.refresh(workflow)
                workflow_id = workflow.id

            class Engine:
                def __init__(self) -> None:
                    self.web_executor = FakeWebExecutor()
                    self.execute_with_debug = self._execute
                    self.called = False

                async def _execute(self, *_args: Any, **_kwargs: Any) -> Any:
                    self.called = True
                    raise AssertionError("engine should not be used")

            engine_stub = Engine()
            manager = ExecutionManager(
                engine_stub,
                session_factory=session_factory,
            )
            manager._hybrid = HybridExecutor(
                engine_stub,
                FakeDesktopExecutor(),
                session_factory=session_factory,
            )
            execution = await manager.start_workflow(workflow_id)
            await manager.execute_existing(execution.id)
            assert engine_stub.called is False
            return manager
        finally:
            await engine.dispose()

    asyncio.run(scenario())
