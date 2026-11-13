from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

import app.database as database
from app.database import Base
from app.engine import ExecutionRouter
from app.engine.runner import ExecutionResult
from app.models.user import User
from app.models.workflow import Workflow


def _execution_result(output: str) -> ExecutionResult:
    return ExecutionResult(
        success=True,
        output=output,
        error=None,
        screenshots=[],
        step_logs=[],
        duration_seconds=0.1,
    )


def test_router_web_workflow() -> None:
    async def scenario() -> tuple[
        ExecutionResult,
        ExecutionResult,
        SimpleNamespace,
        SimpleNamespace,
        SimpleNamespace,
        dict,
    ]:
        expected = _execution_result("web")
        web_engine = SimpleNamespace(
            execute=AsyncMock(return_value=expected),
        )
        desktop_dispatcher = SimpleNamespace(dispatch=AsyncMock())
        router = ExecutionRouter(web_engine, desktop_dispatcher)
        workflow = SimpleNamespace(executor_type="web")
        context = {"variables": {"url": "https://example.com"}}

        result = await router.execute(workflow, context)
        return (
            result,
            expected,
            web_engine,
            desktop_dispatcher,
            workflow,
            context,
        )

    (
        result,
        expected,
        web_engine,
        desktop_dispatcher,
        workflow,
        context,
    ) = asyncio.run(scenario())

    assert result is expected
    web_engine.execute.assert_awaited_once_with(workflow, context)
    desktop_dispatcher.dispatch.assert_not_awaited()


def test_router_windows_workflow() -> None:
    async def scenario() -> tuple[
        ExecutionResult,
        ExecutionResult,
        SimpleNamespace,
        SimpleNamespace,
        SimpleNamespace,
        dict,
    ]:
        expected = _execution_result("windows")
        web_engine = SimpleNamespace(execute=AsyncMock())
        desktop_dispatcher = SimpleNamespace(
            dispatch=AsyncMock(return_value=expected),
        )
        router = ExecutionRouter(web_engine, desktop_dispatcher)
        workflow = SimpleNamespace(executor_type="windows")
        context = {"required_capabilities": ["wps"]}

        result = await router.execute(workflow, context)
        return (
            result,
            expected,
            web_engine,
            desktop_dispatcher,
            workflow,
            context,
        )

    (
        result,
        expected,
        web_engine,
        desktop_dispatcher,
        workflow,
        context,
    ) = asyncio.run(scenario())

    assert result is expected
    desktop_dispatcher.dispatch.assert_awaited_once_with(workflow, context)
    web_engine.execute.assert_not_awaited()


def test_router_default_is_web() -> None:
    async def scenario() -> tuple[
        tuple[str, list[str] | None],
        tuple[str, list[str] | None],
    ]:
        database_engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
        )
        async with database_engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        session_factory = async_sessionmaker(
            database_engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        async with session_factory() as session:
            session.add(
                User(
                    id=1,
                    username="router-user",
                    email="router@example.com",
                    hashed_password="hash",
                )
            )
            workflow = Workflow(
                user_id=1,
                name="Browser workflow",
                natural_language_input="Open example.com",
            )
            constructed_defaults = (
                workflow.executor_type,
                workflow.required_capabilities,
            )
            session.add(workflow)
            await session.commit()
            await session.refresh(workflow)
            persisted_defaults = (
                workflow.executor_type,
                workflow.required_capabilities,
            )

        await database_engine.dispose()
        return constructed_defaults, persisted_defaults

    assert asyncio.run(scenario()) == (("web", []), ("web", []))


def test_legacy_workflow_gets_executor_defaults(monkeypatch) -> None:
    async def scenario() -> tuple[set[str], tuple[str, str]]:
        database_engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
        )
        async with database_engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    CREATE TABLE workflow (
                        id INTEGER PRIMARY KEY,
                        user_id INTEGER NOT NULL,
                        name VARCHAR(255) NOT NULL,
                        natural_language_input TEXT NOT NULL
                    )
                    """
                )
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO workflow (
                        id,
                        user_id,
                        name,
                        natural_language_input
                    ) VALUES (
                        1,
                        1,
                        'Legacy workflow',
                        'Open example.com'
                    )
                    """
                )
            )

        monkeypatch.setattr(database, "engine", database_engine)
        await database.init_db()
        await database.init_db()

        async with database_engine.connect() as connection:
            columns = {
                row[1]
                for row in (
                    await connection.execute(
                        text("PRAGMA table_info(workflow)")
                    )
                ).fetchall()
            }
            defaults = (
                await connection.execute(
                    text(
                        """
                        SELECT executor_type, required_capabilities
                        FROM workflow
                        WHERE id = 1
                        """
                    )
                )
            ).one()

        await database_engine.dispose()
        return columns, (defaults[0], defaults[1])

    columns, defaults = asyncio.run(scenario())

    assert {"executor_type", "required_capabilities"} <= columns
    assert defaults == ("web", "[]")
