"""执行队列后端契约测试（Redis 使用 fakeredis，不依赖外网）。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.engine.execution_manager import ExecutionManager
from app.engine.queue_backends import (
    InProcessExecutionQueue,
    QueueWorker,
    RedisExecutionQueue,
    build_execution_queue,
)
from app.engine.runner import ExecutionResult
from app.models.execution import ExecutionLog
from app.models.user import User
from app.models.workflow import Workflow
from tests.support.versioned_workflows import activate_test_version


def test_inprocess_queue_contract() -> None:
    async def scenario() -> None:
        queue = InProcessExecutionQueue()
        assert await queue.dequeue(timeout=0.01) is None
        await queue.enqueue(7)
        message = await queue.dequeue(timeout=0.1)
        assert message is not None
        assert message.execution_id == 7
        await queue.ack(message)
        await queue.nack(message)
        await queue.close()

    asyncio.run(scenario())


def test_redis_queue_roundtrip_and_retry() -> None:
    fakeredis = pytest.importorskip("fakeredis")

    async def scenario() -> None:
        client = fakeredis.aioredis.FakeRedis(decode_responses=True)
        queue = RedisExecutionQueue(
            "redis://unused",
            max_attempts=2,
            client_factory=lambda _url: client,
        )
        await queue.enqueue(11)
        message = await queue.dequeue(timeout=1)
        assert message is not None
        assert message.execution_id == 11
        await queue.ack(message)

        # 第一次失败重新入队，第二次失败进入 failed 列表。
        await queue.enqueue(12)
        message = await queue.dequeue(timeout=1)
        assert message is not None
        await queue.nack(message)
        requeued = await queue.dequeue(timeout=1)
        assert requeued is not None
        assert requeued.execution_id == 12
        await queue.nack(requeued)
        assert await client.llen(queue.failed_name) == 1
        await queue.close()

    asyncio.run(scenario())


def test_queue_worker_processes_and_nacks() -> None:
    async def scenario() -> None:
        queue = InProcessExecutionQueue()
        processed: list[int] = []
        attempts = {"count": 0}

        async def handler(execution_id: int) -> None:
            attempts["count"] += 1
            if execution_id == 2:
                raise RuntimeError("boom")
            processed.append(execution_id)

        worker = QueueWorker(queue, handler, poll_timeout=0.1)
        await queue.enqueue(1)
        await queue.enqueue(2)
        assert (await worker.drain_once()) is not None
        assert (await worker.drain_once()) is not None
        assert processed == [1]
        # in-process nack 没有持久化重试语义，失败消息不重新入队。
        assert (await worker.drain_once()) is None
        assert processed == [1]
        assert attempts["count"] == 2

    asyncio.run(scenario())


def test_execution_manager_uses_external_queue() -> None:
    async def scenario() -> None:
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
        async with session_factory() as session:
            session.add(
                User(
                    id=1,
                    username="tester",
                    email="t@example.com",
                    hashed_password="x",
                )
            )
            workflow = Workflow(
                user_id=1,
                name="queued",
                natural_language_input="x",
            )
            session.add(workflow)
            await session.flush()
            await activate_test_version(session, workflow)
            await session.commit()
            await session.refresh(workflow)

        fake_engine = SimpleNamespace(
            execute_with_debug=AsyncMock(
                return_value=ExecutionResult(
                    success=True,
                    output="queued-ok",
                    error=None,
                    screenshots=[],
                    step_logs=[],
                    duration_seconds=0.01,
                )
            )
        )
        queue = InProcessExecutionQueue()
        manager = ExecutionManager(
            fake_engine,
            session_factory=session_factory,
            config=SimpleNamespace(
                AUTH_REQUIRED_PAUSE=False,
                WEB_EXPORT_DIRS="",
                WORKSPACE_DEFAULT_MAX_CONCURRENT_EXECUTIONS=2,
                WORKSPACE_DEFAULT_MAX_CONCURRENT_AGENT_TASKS=2,
            ),
            queue=queue,
        )
        execution = await manager.start_workflow(workflow.id)
        assert queue.qsize() == 1
        assert manager.is_active(execution.id) is False

        worker = QueueWorker(queue, manager.run_local, poll_timeout=0.1)
        assert (await worker.drain_once()) is not None
        async with session_factory() as session:
            stored = await session.get(ExecutionLog, execution.id)
        assert stored is not None
        assert stored.status == "success"
        await engine.dispose()

    asyncio.run(scenario())


def test_build_execution_queue_switch() -> None:
    disabled = build_execution_queue(
        SimpleNamespace(EXTERNAL_QUEUE=False, QUEUE_BACKEND="redis")
    )
    assert disabled is None

    inprocess = build_execution_queue(
        SimpleNamespace(EXTERNAL_QUEUE=True, QUEUE_BACKEND="inprocess")
    )
    assert isinstance(inprocess, InProcessExecutionQueue)

    redis = build_execution_queue(
        SimpleNamespace(
            EXTERNAL_QUEUE=True,
            QUEUE_BACKEND="redis",
            REDIS_URL="redis://localhost:6379/0",
        )
    )
    assert isinstance(redis, RedisExecutionQueue)
