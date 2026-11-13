"""WPS 文档创建试点：桌面整链路与失败续跑回归。

链路为 ``ExecutionManager -> DesktopRepairCoordinator ->
DesktopDispatcher -> TaskExecutor（真实桌面 Agent 执行器）``。
本地没有安装 WPS 时用 COM 写入桩验证同一条链路；安装 WPS 的机器会
额外执行一次真实 COM 冒烟用例。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.engine.desktop_dispatcher import DesktopDispatcher
from app.engine.desktop_repair import DesktopRepairCoordinator
from app.engine.execution_manager import ExecutionManager
from app.models.agent import DesktopAgent
from app.models.execution import ExecutionLog
from app.models.user import User
from app.models.workflow import Workflow
from tests.support.versioned_workflows import activate_test_version
from desktop_agent.actions import create_document as create_document_module
from desktop_agent.actions import insert_image as insert_image_module
from desktop_agent.config import AgentConfig
from desktop_agent.executor import TaskExecutor


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


async def _seed_pilot_records(
    session: AsyncSession,
    *,
    document: Path,
    image: Path,
) -> Workflow:
    session.add(
        User(
            id=1,
            username="pilot",
            email="pilot@example.com",
            hashed_password="hash",
        )
    )
    session.add(
        DesktopAgent(
            id=1,
            user_id=1,
            name="DESKTOP-PILOT",
            platform="windows",
            version="0.1.0",
            capabilities=["wps"],
            status="online",
            token_hash="hash",
            allowed_doc_dirs=[str(document.parent)],
        )
    )
    workflow = Workflow(
        id=1,
        user_id=1,
        name="WPS 文档创建试点",
        natural_language_input="创建 WPS 文档并插入图片",
        structured_plan={
            "executor_type": "windows",
            "steps": [
                {
                    "step_id": 1,
                    "action": "create_document",
                    "params": {
                        "path": str(document),
                        "content": "NL-RPA WPS 试点内容",
                    },
                    "idempotency_key": "wps-doc-created",
                },
                {
                    "step_id": 2,
                    "action": "insert_image",
                    "params": {
                        "path": str(document),
                        "image_path": str(image),
                    },
                    "idempotency_key": "wps-image-inserted",
                },
            ],
        },
        executor_type="windows",
        required_capabilities=["wps"],
    )
    session.add(workflow)
    await session.flush()
    await activate_test_version(
        session,
        workflow,
        plan=workflow.structured_plan,
        action_catalog_version="legacy-2026-10-22",
    )
    await session.commit()
    await session.refresh(workflow)
    return workflow


class _AgentClient:
    def __init__(self, agent: _InProcessAgent) -> None:
        self.agent = agent

    async def send_event(self, event: dict[str, Any]) -> None:
        dispatcher = self.agent.dispatcher
        if dispatcher is None:
            raise RuntimeError("Dispatcher is not attached")
        await dispatcher.handle_agent_event(event)


class _InProcessAgent:
    """把派发消息直接交给真实 TaskExecutor，模拟一台在线桌面 Agent。"""

    def __init__(self, config: AgentConfig) -> None:
        self.dispatcher: DesktopDispatcher | None = None
        self.client = _AgentClient(self)
        self.executor = TaskExecutor(config, self.client)
        self._events: dict[int, asyncio.Event] = {}
        self._results: dict[int, dict[str, Any]] = {}
        self.tasks: list[asyncio.Task[None]] = []

    def get_online_agents(self) -> list[int]:
        return [1]

    def register_task(self, task_id: int) -> asyncio.Event:
        event = asyncio.Event()
        self._events[task_id] = event
        return event

    def complete_task(self, task_id: int, result: dict[str, Any]) -> None:
        self._results[task_id] = dict(result)
        event = self._events.get(task_id)
        if event is not None:
            event.set()

    def get_result(self, task_id: int) -> dict[str, Any] | None:
        return self._results.pop(task_id, None)

    def clear_task(self, task_id: int) -> None:
        self._events.pop(task_id, None)
        self._results.pop(task_id, None)

    async def send_task(
        self,
        agent_id: int,
        task_payload: dict[str, Any],
    ) -> None:
        message_type = str(task_payload.get("type") or "")
        if message_type == "desktop.task":
            self.tasks.append(
                asyncio.create_task(
                    self.executor.execute(dict(task_payload)),
                    name=f"pilot-agent-{task_payload.get('task_id')}",
                )
            )
            return
        if message_type == "desktop.cancel":
            await self.executor.cancel()


def _agent_config(root: Path) -> AgentConfig:
    config = AgentConfig(
        server_url="http://127.0.0.1:8000",
        agent_id=1,
        token="token",
        allowed_apps=[],
        allowed_doc_dirs=[str(root)],
        log_dir=str(root / "agent-logs"),
        heartbeat_interval=60,
    )
    config._config_path = root / "agent-config.json"
    return config


def test_wps_document_pilot_resumes_after_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    failures = {"remaining": 1}
    document = tmp_path / "report.docx"
    image = tmp_path / "shot.png"
    image.write_bytes(b"png")

    def fake_create_document(
        path: Path,
        content: str,
        progid: str,
        overwrite: bool,
    ) -> None:
        calls.append(f"create:{Path(path).name}")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(content, encoding="utf-8")

    def fake_insert_image(
        path: Path,
        image_path: Path,
        progid: str,
        position: str,
        save: bool,
    ) -> None:
        calls.append(f"insert:{Path(path).name}")
        if failures["remaining"] > 0:
            failures["remaining"] -= 1
            raise RuntimeError("WPS 文档服务繁忙")
        Path(path).write_text("with-image", encoding="utf-8")

    monkeypatch.setattr(
        create_document_module,
        "_create_document",
        fake_create_document,
    )
    monkeypatch.setattr(
        insert_image_module,
        "_insert_image",
        fake_insert_image,
    )

    async def scenario() -> tuple[dict[str, Any], ExecutionLog, int]:
        session_factory, engine = await _database()
        try:
            async with session_factory() as session:
                await _seed_pilot_records(
                    session,
                    document=document,
                    image=image,
                )

            agent = _InProcessAgent(_agent_config(tmp_path))
            dispatcher = DesktopDispatcher(
                agent,
                session_factory=session_factory,
            )
            agent.dispatcher = dispatcher
            repair = DesktopRepairCoordinator(
                dispatcher,
                session_factory=session_factory,
            )
            manager = ExecutionManager(
                SimpleNamespace(),
                session_factory=session_factory,
                desktop_repair=repair,
            )
            manager.online_agent_ids = lambda: [1]
            execution = await manager.start_workflow(1)
            await manager.execute_existing(execution.id)

            async with session_factory() as session:
                failed = await session.get(ExecutionLog, execution.id)
                assert failed is not None
                assert failed.status == "failed"
                checkpoint = dict(failed.step_logs[-1])

            await manager.resume(execution.id)
            await manager.execute_existing(execution.id)

            async with session_factory() as session:
                final = await session.get(ExecutionLog, execution.id)
                assert final is not None
                return checkpoint, final, len(agent.tasks)
        finally:
            await engine.dispose()

    checkpoint, final, task_count = asyncio.run(scenario())

    assert checkpoint["type"] == "checkpoint"
    assert checkpoint["next_step_index"] == 1
    assert checkpoint["completed_idempotency_keys"] == [
        "wps-doc-created"
    ]
    assert final.status == "success"
    assert [entry["status"] for entry in final.step_logs] == [
        "skipped",
        "success",
    ]
    assert calls == [
        "create:report.docx",
        "insert:report.docx",
        "insert:report.docx",
    ]
    assert document.read_text(encoding="utf-8") == "with-image"
    assert task_count == 2


def _wps_com_available() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import winreg
    except ImportError:
        return False
    try:
        winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "kwps.Application").Close()
    except OSError:
        return False
    return True


@pytest.mark.skipif(
    not _wps_com_available(),
    reason="本机未注册 WPS COM（kwps.Application）",
)
def test_wps_document_creation_real_com(tmp_path: Path) -> None:
    document = tmp_path / "wps-pilot.docx"

    result = asyncio.run(
        create_document_module.handle_create_document(
            {
                "path": str(document),
                "content": "NL-RPA WPS 试点内容",
            },
            {},
        )
    )

    assert result.success is True
    assert document.is_file()
    assert document.stat().st_size > 0
