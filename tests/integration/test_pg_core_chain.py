"""SQLite / PostgreSQL 双跑核心执行链路。

本机没有 Docker/PG 时自动 skip；安装 ``embedded_postgres`` 后可直接
在 Windows 上启动本地 PG（EUC_CN 集簇编码，服务 UTF-8 客户端）。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.database import Base, run_schema_migrations
from app.engine.execution_manager import ExecutionManager
from app.engine.runner import ExecutionResult
from app.migrations import SCHEMA_VERSION, applied_migrations
from app.models.execution import ExecutionLog
from app.models.user import User
from app.models.workflow import Workflow
from app.testing.postgres import postgres_url
from tests.support.versioned_workflows import activate_test_version


async def _run_core_chain(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
            await connection.run_sync(Base.metadata.create_all)
            await run_schema_migrations(connection)
            applied = await applied_migrations(connection)
        assert max(applied) == SCHEMA_VERSION

        session_factory = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        async with session_factory() as session:
            session.add(
                User(
                    id=1,
                    username="pg-tester",
                    email="pg@example.com",
                    hashed_password="x",
                )
            )
            workflow = Workflow(
                user_id=1,
                name="中文流程",
                natural_language_input="demo",
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
                    output="chain-ok",
                    error=None,
                    screenshots=[],
                    step_logs=[{"step_index": 0, "status": "success"}],
                    duration_seconds=0.05,
                )
            )
        )
        manager = ExecutionManager(
            fake_engine,
            session_factory=session_factory,
            config=SimpleNamespace(
                AUTH_REQUIRED_PAUSE=False,
                WEB_EXPORT_DIRS="",
                WORKSPACE_DEFAULT_MAX_CONCURRENT_EXECUTIONS=2,
                WORKSPACE_DEFAULT_MAX_CONCURRENT_AGENT_TASKS=2,
            ),
        )
        execution = await manager.start_workflow(workflow.id)
        await manager.execute_existing(execution.id)

        async with session_factory() as session:
            stored = await session.get(ExecutionLog, execution.id)
            name = await session.scalar(
                text("SELECT name FROM workflow WHERE id = :id"),
                {"id": workflow.id},
            )
        assert stored is not None
        assert stored.status == "success"
        assert name == "中文流程"
    finally:
        await engine.dispose()


def test_sqlite_core_execution_chain(tmp_path) -> None:
    url = f"sqlite+aiosqlite:///{(tmp_path / 'chain.db').as_posix()}"
    asyncio.run(_run_core_chain(url))


def test_postgres_core_execution_chain() -> None:
    with postgres_url() as url:
        if url is None:
            pytest.skip(
                "PostgreSQL 不可用：设置 TEST_POSTGRES_URL 或安装 "
                "embedded_postgres 后重跑"
            )
        asyncio.run(_run_core_chain(url))
