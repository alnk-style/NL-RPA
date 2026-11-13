from __future__ import annotations

# ruff: noqa: E402

import asyncio
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("DEEPSEEK_API_KEY", "test-key")
os.environ.setdefault("CREDENTIAL_ENCRYPTION_KEY", "test-encryption-key")
os.environ.setdefault("JWT_SECRET_KEY", "test-encryption-key")

from app.api import chat_router, executions_router, workflows_router
from app.api import chat as chat_api
from app.api import dependencies as auth_api
from app.api import executions as executions_api
from app.agents.desktop_planner import DesktopExecutionContext
from app.config import settings
from app.database import Base, get_db
from app.engine.runner import ExecutionResult
from app.models.agent import DesktopAgent
from app.models.execution import ExecutionLog
from app.models.user import User
from app.models.workflow import Workflow
from app.models.workflow_version import WorkflowVersion


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
    return session_factory(), engine


async def _seed_workflow(
    session: AsyncSession,
    *,
    workflow_id: int = 1,
    status: str = "draft",
) -> Workflow:
    user = User(
        id=1,
        username="tester",
        email="tester@example.com",
        hashed_password="hash",
    )
    workflow = Workflow(
        id=workflow_id,
        user_id=1,
        name="Example workflow",
        description="Test workflow",
        natural_language_input="Open example.com",
        structured_plan={
            "schema_version": 1,
            "execution_mode": "structured",
            "executor_type": "web",
            "variables": [],
            "steps": [
                {
                    "step_id": 1,
                    "executor": "web",
                    "action": "goto",
                    "params": {"url": "https://example.com"},
                }
            ],
        },
        generated_code=None,
        status=status,
    )
    session.add_all([user, workflow])
    await session.flush()
    if status == "published":
        version = WorkflowVersion(
            workflow_id=workflow.id,
            version=1,
            plan=dict(workflow.structured_plan),
            validation={"valid": True},
            action_catalog_version="2026-10-28.1",
        )
        session.add(version)
        await session.flush()
        workflow.active_version_id = version.id
    await session.commit()
    await session.refresh(workflow)
    return workflow


async def _request(
    app: FastAPI,
    method: str,
    path: str,
    **kwargs,
) -> httpx.Response:
    # 生产环境中 executions/workflows 路由由 main.py 统一挂载
    # get_current_user；这里的临时 app 用 override 模拟同一契约。
    if (
        not settings.DEV_AUTH_BYPASS
        and auth_api.get_current_user not in app.dependency_overrides
    ):
        app.dependency_overrides[auth_api.get_current_user] = (
            lambda: SimpleNamespace(id=1, username="tester")
        )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        return await client.request(method, path, **kwargs)


def test_api_package_exports() -> None:
    assert chat_router.prefix == "/api/chat"
    assert executions_router.prefix == "/api/executions"
    assert workflows_router.prefix == "/api/workflows"


def test_mcp_candidate_with_app_hint_routes_to_desktop_planner() -> None:
    plan = {
        "executor_type": "mcp",
        "app_hint": "酷狗音乐",
        "needs_clarification": ["请提供 AutomationId"],
        "steps": [
            {
                "executor": "mcp",
                "action": "call_tool",
                "params": {
                    "server": "酷狗音乐",
                    "tool": "app_launch",
                },
            }
        ],
    }

    assert chat_api._is_direct_desktop_plan(plan)
    chat_api._strip_desktop_runtime_clarifications(plan)
    assert "needs_clarification" not in plan


def test_get_current_user() -> None:
    class FakeSession:
        async def get(self, _model, user_id: int):
            if user_id == 7:
                return SimpleNamespace(id=7, username="tester")
            return None

    token = jwt.encode(
        {"sub": "7"},
        settings.CREDENTIAL_ENCRYPTION_KEY,
        algorithm="HS256",
    )
    user = asyncio.run(
        auth_api.get_current_user(token=token, db=FakeSession())
    )

    assert user.id == 7
    assert user.username == "tester"

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            auth_api.get_current_user(
                token="not-a-jwt",
                db=FakeSession(),
            )
        )
    assert exc_info.value.status_code == 401


def test_dev_auth_bypass_creates_single_local_user(monkeypatch) -> None:
    async def scenario() -> tuple[int, str, int]:
        session, engine = await _database()
        try:
            monkeypatch.setattr(settings, "DEV_AUTH_BYPASS", True)

            first_user = await auth_api.get_current_user(
                token=None,
                db=session,
            )
            second_user = await auth_api.get_current_user(
                token=None,
                db=session,
            )
            users = (
                await session.execute(select(User))
            ).scalars().all()

            assert first_user.id == second_user.id
            return first_user.id, first_user.username, len(users)
        finally:
            await session.close()
            await engine.dispose()

    user_id, username, user_count = asyncio.run(scenario())

    assert user_id > 0
    assert username == "local-dev"
    assert user_count == 1


def test_auth_me_returns_dev_user(monkeypatch) -> None:
    from app.api.auth import router as auth_router

    async def scenario() -> tuple[int, dict]:
        session, engine = await _database()
        try:
            monkeypatch.setattr(settings, "DEV_AUTH_BYPASS", True)

            app = FastAPI()
            app.include_router(auth_router)

            async def override_db():
                yield session

            app.dependency_overrides[get_db] = override_db
            response = await _request(app, "GET", "/api/auth/me")
            return response.status_code, response.json()
        finally:
            await session.close()
            await engine.dispose()

    status_code, payload = asyncio.run(scenario())

    assert status_code == 200
    assert payload["username"] == "local-dev"
    assert payload["email"] == "local-dev@example.com"


def test_chat_message_first() -> None:
    app = FastAPI()
    app.include_router(chat_router)
    parsed_plan = {
        "trigger": {"type": "manual", "cron": None},
        "steps": [{"step_id": 1, "action": "navigate"}],
        "variables": [],
    }
    detailed_plan = {
        "trigger": {"type": "manual", "cron": None},
        "steps": [
            {
                "step_id": 1,
                "action": "navigate",
                "selector": None,
                "params": {"url": "https://example.com"},
            }
        ],
        "variables": [],
    }
    generated_code = "await page.goto('https://example.com')"
    structured_plan = {
        "schema_version": 1,
        "execution_mode": "structured",
        "executor_type": "web",
        "trigger": {"type": "manual", "cron": None},
        "steps": [
            {
                "step_id": 1,
                "executor": "web",
                "action": "goto",
                "params": {"url": "https://example.com"},
            }
        ],
        "variables": [],
    }
    intent_agent = SimpleNamespace(parse=AsyncMock(return_value=parsed_plan))
    planner_agent = SimpleNamespace(
        plan=AsyncMock(return_value=detailed_plan)
    )
    generator_agent = SimpleNamespace(
        generate=AsyncMock(return_value=generated_code)
    )

    app.dependency_overrides[
        chat_api.get_intent_parser_agent
    ] = lambda: intent_agent
    app.dependency_overrides[chat_api.get_step_planner_agent] = lambda: (
        planner_agent
    )
    app.dependency_overrides[chat_api.get_code_generator_agent] = lambda: (
        generator_agent
    )
    app.dependency_overrides[chat_api.get_web_planner_agent] = (
        lambda: SimpleNamespace(
            plan=AsyncMock(return_value=structured_plan)
        )
    )
    chat_api.session_contexts.clear()

    response = asyncio.run(
        _request(
            app,
            "POST",
            "/api/chat/message",
            json={
                "session_id": "session-first",
                "message": "Open example.com",
                "workflow_id": None,
            },
        )
    )

    assert response.status_code == 200
    payload = response.json()
    reply = payload.pop("reply")
    assert isinstance(reply, str) and reply
    assert "https://example.com" in reply
    assert payload == {
        "structured_plan": structured_plan,
        "generated_code": None,
        "needs_confirmation": True,
        "suggestions": ["修改参数", "直接保存", "试运行"],
        "executor_type": "web",
    }
    intent_agent.parse.assert_awaited_once_with("Open example.com")
    planner_agent.plan.assert_awaited_once_with(parsed_plan)
    generator_agent.generate.assert_not_awaited()
    context = chat_api.session_contexts["session-first"]
    assert context[0] == {
        "role": "user",
        "content": "Open example.com",
    }
    assert context[1]["structured_plan"] == structured_plan
    assert context[1]["generated_code"] is None


def test_chat_message_modify() -> None:
    async def scenario() -> tuple[dict, Workflow]:
        session, engine = await _database()
        try:
            workflow = await _seed_workflow(
                session,
                status="published",
            )
            refined_plan = {
                "schema_version": 1,
                "execution_mode": "structured",
                "executor_type": "web",
                "trigger": {"type": "manual", "cron": None},
                "steps": [
                    {
                        "step_id": 1,
                        "executor": "web",
                        "action": "goto",
                        "params": {"url": "https://example.org"},
                    }
                ],
                "variables": [],
            }
            new_code = "await page.goto('https://example.org')"
            planner_agent = SimpleNamespace(
                plan=AsyncMock(return_value=refined_plan)
            )
            generator_agent = SimpleNamespace(
                generate=AsyncMock(return_value=new_code)
            )

            app = FastAPI()
            app.include_router(chat_router)

            async def override_db():
                yield session

            app.dependency_overrides[get_db] = override_db
            app.dependency_overrides[
                chat_api.get_step_planner_agent
            ] = lambda: planner_agent
            app.dependency_overrides[
                chat_api.get_code_generator_agent
            ] = lambda: generator_agent
            app.dependency_overrides[
                chat_api.get_web_planner_agent
            ] = lambda: SimpleNamespace(
                plan=AsyncMock(return_value=refined_plan)
            )
            chat_api.session_contexts.clear()

            response = await _request(
                app,
                "POST",
                "/api/chat/modify",
                json={
                    "session_id": "session-modify",
                    "modification": "Change the URL to example.org",
                    "workflow_id": workflow.id,
                },
            )
            await session.refresh(workflow)
            return response.json(), workflow
        finally:
            await session.close()
            await engine.dispose()

    payload, workflow = asyncio.run(scenario())

    assert payload["structured_plan"]["steps"][0]["params"]["url"] == (
        "https://example.org"
    )
    assert payload["generated_code"] is None
    assert payload["needs_confirmation"] is True
    assert workflow.structured_plan == payload["structured_plan"]
    assert workflow.generated_code is None


def test_chat_routes_desktop_plan_to_windows_executor() -> None:
    app = FastAPI()
    app.include_router(chat_router)
    parsed_plan = {
        "executor_type": "windows",
        "needs_clarification": ["请提供窗口类名和 UIA 定位信息"],
        "trigger": {"type": "manual", "cron": None},
        "steps": [
            {
                "step_id": 1,
                "action": "navigate",
                "target": "微信",
            }
        ],
        "variables": [],
    }
    step_plan = {
        **parsed_plan,
        "steps": [
            {
                **parsed_plan["steps"][0],
                "params": {},
            }
        ],
    }
    windows_plan = {
        "executor_type": "windows",
        "required_capabilities": ["window"],
        "trigger": {"type": "manual", "cron": None},
        "steps": [
            {
                "step_id": 1,
                "action": "launch_app",
                "params": {"path": "wechat.exe"},
                "depends_on": [],
            }
        ],
        "variables": [],
    }
    intent_agent = SimpleNamespace(parse=AsyncMock(return_value=parsed_plan))
    planner_agent = SimpleNamespace(
        plan=AsyncMock(return_value=step_plan)
    )
    desktop_planner = SimpleNamespace(
        plan=AsyncMock(return_value=windows_plan)
    )
    generator_agent = SimpleNamespace(generate=AsyncMock())

    app.dependency_overrides[
        chat_api.get_intent_parser_agent
    ] = lambda: intent_agent
    app.dependency_overrides[chat_api.get_step_planner_agent] = lambda: (
        planner_agent
    )
    app.dependency_overrides[chat_api.get_code_generator_agent] = lambda: (
        generator_agent
    )
    app.dependency_overrides[chat_api.get_desktop_planner_agent] = lambda: (
        desktop_planner
    )
    app.dependency_overrides[chat_api.get_desktop_context] = lambda: (
        DesktopExecutionContext(
            allowed_apps=("wechat.exe",),
            capabilities=frozenset({"window", "uia"}),
            online_agent_count=1,
            paired_agent_count=1,
        )
    )
    chat_api.session_contexts.clear()

    response = asyncio.run(
        _request(
            app,
            "POST",
            "/api/chat/message",
            json={
                "session_id": "desktop-app",
                "message": "打开微信",
                "workflow_id": None,
            },
        )
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["executor_type"] == "windows"
    assert payload["structured_plan"] == windows_plan
    assert payload["generated_code"] is None
    assert payload["needs_confirmation"] is True
    assert "窗口类名" not in payload["reply"]
    planner_agent.plan.assert_not_awaited()
    desktop_planner.plan.assert_awaited_once()
    generator_agent.generate.assert_not_awaited()


def test_chat_uses_live_snapshot_for_desktop_planning() -> None:
    app = FastAPI()
    app.include_router(chat_router)
    parsed_plan = {
        "executor_type": "windows",
        "app_hint": "记事本",
        "trigger": {"type": "manual", "cron": None},
        "steps": [{"step_id": 1, "action": "navigate", "target": "记事本"}],
        "variables": [],
    }
    windows_plan = {
        "executor_type": "windows",
        "required_capabilities": ["uia"],
        "trigger": {"type": "manual", "cron": None},
        "steps": [
            {
                "step_id": 1,
                "action": "click_element",
                "params": {"automation_id": "15", "control_type": "Document"},
                "depends_on": [],
            }
        ],
        "variables": [],
    }
    intent_agent = SimpleNamespace(parse=AsyncMock(return_value=parsed_plan))
    planner_agent = SimpleNamespace(
        plan=AsyncMock(return_value=dict(parsed_plan))
    )
    desktop_planner = SimpleNamespace(
        plan=AsyncMock(return_value=windows_plan)
    )
    generator_agent = SimpleNamespace(generate=AsyncMock())

    class SnapshotDispatcher:
        def __init__(self) -> None:
            self.requests: list[dict] = []

        async def inspect(
            self,
            request: dict,
            agent_id: int | None = None,
            timeout: float | None = None,
        ) -> dict:
            self.requests.append(request)
            if request.get("window"):
                return {
                    "success": True,
                    "windows": [],
                    "target": {"handle": 7, "title": "记事本"},
                    "elements": [
                        {
                            "name": "文本编辑器",
                            "automation_id": "15",
                            "control_type": "Document",
                            "depth": 1,
                        }
                    ],
                    "error": None,
                }
            return {
                "success": True,
                "windows": [
                    {
                        "handle": 7,
                        "title": "记事本",
                        "class_name": "Notepad",
                        "pid": 10,
                        "process_name": "notepad.exe",
                    }
                ],
                "target": None,
                "elements": [],
                "error": None,
            }

    dispatcher = SnapshotDispatcher()

    app.dependency_overrides[chat_api.get_intent_parser_agent] = lambda: (
        intent_agent
    )
    app.dependency_overrides[chat_api.get_step_planner_agent] = lambda: (
        planner_agent
    )
    app.dependency_overrides[chat_api.get_code_generator_agent] = lambda: (
        generator_agent
    )
    app.dependency_overrides[chat_api.get_desktop_planner_agent] = lambda: (
        desktop_planner
    )
    app.dependency_overrides[chat_api.get_desktop_context] = lambda: (
        DesktopExecutionContext(
            allowed_apps=("notepad.exe",),
            capabilities=frozenset({"window", "uia"}),
            agent_ids=(1,),
            online_agent_count=1,
            paired_agent_count=1,
        )
    )
    app.dependency_overrides[chat_api.get_desktop_dispatcher] = lambda: (
        dispatcher
    )
    chat_api.session_contexts.clear()

    response = asyncio.run(
        _request(
            app,
            "POST",
            "/api/chat/message",
            json={
                "session_id": "desktop-snapshot",
                "message": "在记事本里输入周报",
                "workflow_id": None,
            },
        )
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["executor_type"] == "windows"
    assert payload["structured_plan"] == windows_plan
    assert len(dispatcher.requests) == 2
    assert dispatcher.requests[1]["window"]["handle"] == 7
    snapshot = desktop_planner.plan.await_args.kwargs["snapshot"]
    assert snapshot["elements"][0]["automation_id"] == "15"
    assert "记事本" in payload["reply"]
    generator_agent.generate.assert_not_awaited()


def test_chat_asks_to_pair_agent_for_desktop_request() -> None:
    app = FastAPI()
    app.include_router(chat_router)
    parsed_plan = {
        "executor_type": "windows",
        "trigger": {"type": "manual", "cron": None},
        "steps": [{"step_id": 1, "action": "navigate", "target": "微信"}],
        "variables": [],
    }
    intent_agent = SimpleNamespace(parse=AsyncMock(return_value=parsed_plan))
    planner_agent = SimpleNamespace(plan=AsyncMock(return_value=parsed_plan))
    desktop_planner = SimpleNamespace(plan=AsyncMock())
    generator_agent = SimpleNamespace(generate=AsyncMock())

    app.dependency_overrides[chat_api.get_intent_parser_agent] = lambda: (
        intent_agent
    )
    app.dependency_overrides[chat_api.get_step_planner_agent] = lambda: (
        planner_agent
    )
    app.dependency_overrides[chat_api.get_code_generator_agent] = lambda: (
        generator_agent
    )
    app.dependency_overrides[chat_api.get_desktop_planner_agent] = lambda: (
        desktop_planner
    )
    app.dependency_overrides[chat_api.get_desktop_context] = lambda: (
        DesktopExecutionContext()
    )
    chat_api.session_contexts.clear()

    response = asyncio.run(
        _request(
            app,
            "POST",
            "/api/chat/message",
            json={
                "session_id": "desktop-no-agent",
                "message": "打开微信",
                "workflow_id": None,
            },
        )
    )

    assert response.status_code == 200
    payload = response.json()
    assert "桌面 Agent" in payload["reply"]
    assert payload["needs_confirmation"] is False
    desktop_planner.plan.assert_not_awaited()
    generator_agent.generate.assert_not_awaited()


def test_chat_asks_to_allowlist_apps_when_empty() -> None:
    app = FastAPI()
    app.include_router(chat_router)
    parsed_plan = {
        "executor_type": "windows",
        "trigger": {"type": "manual", "cron": None},
        "steps": [{"step_id": 1, "action": "navigate", "target": "微信"}],
        "variables": [],
    }
    intent_agent = SimpleNamespace(parse=AsyncMock(return_value=parsed_plan))
    planner_agent = SimpleNamespace(plan=AsyncMock(return_value=parsed_plan))
    desktop_planner = SimpleNamespace(plan=AsyncMock())
    generator_agent = SimpleNamespace(generate=AsyncMock())

    app.dependency_overrides[chat_api.get_intent_parser_agent] = lambda: (
        intent_agent
    )
    app.dependency_overrides[chat_api.get_step_planner_agent] = lambda: (
        planner_agent
    )
    app.dependency_overrides[chat_api.get_code_generator_agent] = lambda: (
        generator_agent
    )
    app.dependency_overrides[chat_api.get_desktop_planner_agent] = lambda: (
        desktop_planner
    )
    app.dependency_overrides[chat_api.get_desktop_context] = lambda: (
        DesktopExecutionContext(
            online_agent_count=1,
            paired_agent_count=1,
        )
    )
    chat_api.session_contexts.clear()

    response = asyncio.run(
        _request(
            app,
            "POST",
            "/api/chat/message",
            json={
                "session_id": "desktop-empty-allowlist",
                "message": "打开微信",
                "workflow_id": None,
            },
        )
    )

    assert response.status_code == 200
    payload = response.json()
    assert "白名单" in payload["reply"]
    assert payload["needs_confirmation"] is False
    desktop_planner.plan.assert_not_awaited()
    generator_agent.generate.assert_not_awaited()


def test_executions_run() -> None:
    async def scenario() -> tuple[dict, list[ExecutionLog]]:
        session, engine = await _database()
        try:
            workflow = await _seed_workflow(
                session,
                status="published",
            )
            execution_engine = SimpleNamespace(
                execute_with_debug=AsyncMock(
                    return_value=ExecutionResult(
                        success=True,
                        output="finished",
                        error=None,
                        screenshots=["logs/run-1/step_00_goto.png"],
                        step_logs=[
                            {
                                "step_index": 0,
                                "action": "goto",
                                "status": "success",
                                "screenshot_path": (
                                    "logs/run-1/step_00_goto.png"
                                ),
                            }
                        ],
                        duration_seconds=0.25,
                    )
                )
            )

            app = FastAPI()
            app.include_router(executions_router)
            app.state.engine = execution_engine

            async def override_db():
                yield session

            app.dependency_overrides[get_db] = override_db
            response = await _request(
                app,
                "POST",
                f"/api/executions/{workflow.id}/run",
            )
            logs = (
                await session.execute(
                    ExecutionLog.__table__.select().where(
                        ExecutionLog.workflow_id == workflow.id
                    )
                )
            ).mappings().all()
            return response.json(), [ExecutionLog(**dict(row)) for row in logs]
        finally:
            await session.close()
            await engine.dispose()

    payload, logs = asyncio.run(scenario())

    assert payload["status"] == "success"
    assert payload["success"] is True
    assert payload["output"] == "finished"
    assert len(logs) == 1
    assert logs[0].status == "success"
    assert logs[0].finished_at is not None
    assert logs[0].screenshots == ["logs/run-1/step_00_goto.png"]
    assert logs[0].step_logs[0]["action"] == "goto"


def test_executions_run_published_workflow() -> None:
    async def scenario() -> int:
        session, engine = await _database()
        try:
            workflow = await _seed_workflow(
                session,
                status="published",
            )
            execution_engine = SimpleNamespace(
                execute_with_debug=AsyncMock(
                    return_value=ExecutionResult(
                        success=True,
                        output="finished",
                        error=None,
                        screenshots=[],
                        step_logs=[],
                        duration_seconds=0.1,
                    )
                )
            )

            app = FastAPI()
            app.include_router(executions_router)
            app.state.engine = execution_engine

            async def override_db():
                yield session

            app.dependency_overrides[get_db] = override_db
            response = await _request(
                app,
                "POST",
                f"/api/executions/{workflow.id}/run",
            )
            return response.status_code
        finally:
            await session.close()
            await engine.dispose()

    assert asyncio.run(scenario()) == 200


def test_executions_list_for_current_user() -> None:
    async def scenario() -> tuple[int, list[dict]]:
        session, engine = await _database()
        try:
            workflow = await _seed_workflow(session)
            user = await session.get(User, 1)
            assert user is not None
            other_user = User(
                id=2,
                username="other",
                email="other@example.com",
                hashed_password="hash",
            )
            session.add(other_user)
            other_workflow = Workflow(
                user_id=other_user.id,
                name="Other workflow",
                description="Other",
                natural_language_input="Other",
                generated_code="pass",
                status="draft",
            )
            session.add(other_workflow)
            await session.commit()
            await session.refresh(other_workflow)
            session.add_all(
                [
                    ExecutionLog(
                        workflow_id=workflow.id,
                        status="success",
                    ),
                    ExecutionLog(
                        workflow_id=other_workflow.id,
                        status="failed",
                    ),
                ]
            )
            await session.commit()

            app = FastAPI()
            app.include_router(executions_router)

            async def override_db():
                yield session

            async def override_user() -> User:
                return user

            app.dependency_overrides[get_db] = override_db
            app.dependency_overrides[auth_api.get_current_user] = (
                override_user
            )
            response = await _request(
                app,
                "GET",
                "/api/executions",
            )
            return response.status_code, response.json()
        finally:
            await session.close()
            await engine.dispose()

    status_code, executions = asyncio.run(scenario())

    assert status_code == 200
    assert len(executions) == 1
    assert executions[0]["workflow_name"] == "Example workflow"
    assert executions[0]["status"] == "success"


def test_executions_logs() -> None:
    async def scenario() -> list[dict]:
        session, engine = await _database()
        try:
            workflow = await _seed_workflow(session)
            now = datetime(2026, 9, 16, 12, 0, 0)
            session.add_all(
                [
                    ExecutionLog(
                        workflow_id=workflow.id,
                        status="failed",
                        started_at=now - timedelta(minutes=5),
                        error_message="old",
                    ),
                    ExecutionLog(
                        workflow_id=workflow.id,
                        status="success",
                        started_at=now,
                    ),
                ]
            )
            await session.commit()

            app = FastAPI()
            app.include_router(executions_router)

            async def override_db():
                yield session

            app.dependency_overrides[get_db] = override_db
            response = await _request(
                app,
                "GET",
                f"/api/executions/{workflow.id}/logs",
            )
            assert response.status_code == 200
            return response.json()
        finally:
            await session.close()
            await engine.dispose()

    logs = asyncio.run(scenario())

    assert [log["status"] for log in logs] == ["success", "failed"]


def test_executions_screenshot(tmp_path, monkeypatch) -> None:
    async def scenario() -> httpx.Response:
        screenshot = tmp_path / "step_00_goto.png"
        screenshot.write_bytes(b"png")
        session, engine = await _database()
        try:
            workflow = await _seed_workflow(session)
            log = ExecutionLog(
                workflow_id=workflow.id,
                status="success",
                screenshots=[str(screenshot)],
            )
            session.add(log)
            await session.commit()
            await session.refresh(log)

            app = FastAPI()
            app.include_router(executions_router)

            async def override_db():
                yield session

            app.dependency_overrides[get_db] = override_db
            return await _request(
                app,
                "GET",
                f"/api/executions/{log.id}/screenshot/0",
            )
        finally:
            await session.close()
            await engine.dispose()

    monkeypatch.setattr(executions_api, "LOGS_DIR", tmp_path)
    response = asyncio.run(scenario())

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == b"png"


def _allowlisted_screenshot_response(
    tmp_path: Path,
    *,
    allowed_doc_dirs: list[str],
) -> httpx.Response:
    async def scenario() -> httpx.Response:
        pictures = tmp_path / "pictures"
        pictures.mkdir(exist_ok=True)
        screenshot = pictures / "2026-09-24.png"
        screenshot.write_bytes(b"png")

        session, engine = await _database()
        try:
            workflow = await _seed_workflow(session)
            session.add(
                DesktopAgent(
                    id=1,
                    user_id=1,
                    name="local-pc",
                    platform="windows",
                    version="0.1.0",
                    capabilities=["screenshot"],
                    allowed_apps=[],
                    allowed_doc_dirs=allowed_doc_dirs,
                    status="online",
                    token_hash="hash",
                )
            )
            log = ExecutionLog(
                workflow_id=workflow.id,
                status="success",
                screenshots=[str(screenshot)],
            )
            session.add(log)
            await session.commit()
            await session.refresh(log)

            app = FastAPI()
            app.include_router(executions_router)

            async def override_db():
                yield session

            app.dependency_overrides[get_db] = override_db
            return await _request(
                app,
                "GET",
                f"/api/executions/{log.id}/screenshot/0",
            )
        finally:
            await session.close()
            await engine.dispose()

    return asyncio.run(scenario())


def test_executions_screenshot_allows_agent_allowlisted_dir(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(executions_api, "LOGS_DIR", tmp_path / "logs")

    response = _allowlisted_screenshot_response(
        tmp_path,
        allowed_doc_dirs=[str(tmp_path / "pictures")],
    )

    assert response.status_code == 200
    assert response.content == b"png"


def test_executions_screenshot_rejects_path_outside_allowlist(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(executions_api, "LOGS_DIR", tmp_path / "logs")

    response = _allowlisted_screenshot_response(
        tmp_path,
        allowed_doc_dirs=[],
    )

    assert response.status_code == 404


def test_websocket_live() -> None:
    log = SimpleNamespace(
        id=1,
        status="success",
        started_at=datetime(2026, 10, 21, 15, 34, 29),
        finished_at=datetime(2026, 10, 21, 15, 34, 37),
        screenshots=["logs/run-1/step_00_goto.png"],
        step_logs=[
            {
                "step_index": 0,
                "action": "goto",
                "status": "success",
                "screenshot_path": "logs/run-1/step_00_goto.png",
            }
        ],
    )

    class FakeResult:
        def scalar_one_or_none(self):
            return log

    class FakeSession:
        async def execute(self, _statement):
            return FakeResult()

        async def get(self, _model, user_id):
            return SimpleNamespace(id=user_id, username="tester")

        async def rollback(self) -> None:
            return None

    session = FakeSession()
    app = FastAPI()
    app.include_router(executions_router)

    async def override_db():
        yield session

    app.dependency_overrides[get_db] = override_db
    token = jwt.encode(
        {"sub": "1"},
        settings.CREDENTIAL_ENCRYPTION_KEY,
        algorithm="HS256",
    )
    with TestClient(app) as client:
        with client.websocket_connect(
            f"/api/executions/{log.id}/live?token={token}"
        ) as websocket:
            first = websocket.receive_json()
            second = websocket.receive_json()
            third = websocket.receive_json()

    assert first == {
        "type": "status",
        "execution_id": log.id,
        "status": "success",
        "started_at": log.started_at.isoformat(),
        "finished_at": log.finished_at.isoformat(),
    }
    assert second["type"] == "step"
    assert second["step_log"]["action"] == "goto"
    assert second["screenshot_path"] == "logs/run-1/step_00_goto.png"
    assert third == {
        "type": "completed",
        "execution_id": log.id,
        "status": "success",
        "started_at": log.started_at.isoformat(),
        "finished_at": log.finished_at.isoformat(),
    }
