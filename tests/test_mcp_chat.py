from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

os.environ.setdefault("DEEPSEEK_API_KEY", "test-key")
os.environ.setdefault("CREDENTIAL_ENCRYPTION_KEY", "test-encryption-key")

from app import models  # noqa: F401
from app.api import chat as chat_api
from app.api import chat_router
from app.api.dependencies import get_current_user
from app.api.workspace_deps import get_workspace_context
from app.database import Base, get_db
from app.models.user import User
from app.models.workflow import Workflow

MCP_TOOLS = [
    {
        "server_id": 1,
        "server": "demo_app",
        "app_name": "演示应用",
        "tool": "demo_status",
        "description": "读取应用状态",
        "input_schema": {"type": "object", "properties": {}},
        "risk_level": "read_only",
        "authorized": True,
        "confirmed": False,
    }
]

MCP_INTENT = {
    "executor_type": "mcp",
    "trigger": {"type": "manual", "cron": None},
    "steps": [
        {
            "step_id": 1,
            "executor": "mcp",
            "action": "call_tool",
            "params": {
                "server": "demo_app",
                "tool": "demo_status",
                "arguments": {},
                "result_var": "app_status",
            },
            "depends_on": [],
        }
    ],
    "variables": [],
}

MCP_PLAN = {
    "executor_type": "mcp",
    "needs_confirmation": True,
    "trigger": {"type": "manual", "cron": None},
    "steps": MCP_INTENT["steps"],
    "variables": [],
}


def _overrides(
    app: FastAPI,
    *,
    catalog,
    intent=None,
    planner=None,
    generator=None,
    web_planner=None,
) -> dict:
    intent_parser = intent or SimpleNamespace(
        parse=AsyncMock(return_value=dict(MCP_INTENT))
    )
    step_planner = planner or SimpleNamespace(
        plan=AsyncMock(return_value=dict(MCP_PLAN))
    )
    code_generator = generator or SimpleNamespace(generate=AsyncMock())
    web_agent = web_planner or SimpleNamespace(plan=AsyncMock())
    app.include_router(chat_router)
    app.dependency_overrides[chat_api.get_intent_parser_agent] = lambda: intent_parser
    app.dependency_overrides[chat_api.get_step_planner_agent] = lambda: step_planner
    app.dependency_overrides[chat_api.get_code_generator_agent] = lambda: code_generator
    app.dependency_overrides[chat_api.get_web_planner_agent] = lambda: web_agent
    app.dependency_overrides[chat_api.get_mcp_tool_catalog] = lambda: catalog
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1)
    app.dependency_overrides[get_workspace_context] = lambda: SimpleNamespace(
        isolation_enabled=False,
        workspace_id=None,
    )
    chat_api.session_contexts.clear()
    return {
        "intent": intent_parser,
        "planner": step_planner,
        "generator": code_generator,
        "web_planner": web_agent,
    }


async def _post(app: FastAPI, path: str, payload: dict) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        return await client.post(path, json=payload)


def test_chat_generates_mcp_only_plan_without_code() -> None:
    async def scenario():
        catalog = SimpleNamespace(list_tools=AsyncMock(return_value=list(MCP_TOOLS)))
        app = FastAPI()
        mocks = _overrides(app, catalog=catalog)
        response = await _post(
            app,
            "/api/chat/message",
            {
                "session_id": "mcp-only",
                "message": "读取演示应用状态",
            },
        )
        return response, mocks, catalog

    response, mocks, catalog = asyncio.run(scenario())

    assert response.status_code == 200
    payload = response.json()
    assert payload["executor_type"] == "mcp"
    assert payload["needs_confirmation"] is True
    assert payload["generated_code"] is None
    assert payload["structured_plan"]["steps"][0]["executor"] == "mcp"
    assert "demo_app.demo_status" in payload["reply"]
    catalog.list_tools.assert_awaited_once()
    assert mocks["intent"].parse.await_args.kwargs["mcp_tools"] == MCP_TOOLS
    assert mocks["planner"].plan.await_args.kwargs["mcp_tools"] == MCP_TOOLS
    mocks["generator"].generate.assert_not_awaited()
    mocks["web_planner"].plan.assert_not_awaited()


def test_modify_mcp_workflow_keeps_mcp_execution_path() -> None:
    async def scenario():
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
                    email="tester@example.com",
                    hashed_password="hash",
                )
            )
            workflow = Workflow(
                user_id=1,
                name="mcp-flow",
                natural_language_input="status",
                executor_type="mcp",
                structured_plan=dict(MCP_PLAN),
            )
            session.add(workflow)
            await session.commit()
            await session.refresh(workflow)
            workflow_id = workflow.id

        async def override_db():
            async with session_factory() as session:
                yield session

        catalog = SimpleNamespace(list_tools=AsyncMock(return_value=list(MCP_TOOLS)))
        app = FastAPI()
        mocks = _overrides(app, catalog=catalog)
        app.dependency_overrides[get_db] = override_db
        response = await _post(
            app,
            "/api/chat/modify",
            {
                "session_id": "mcp-modify",
                "modification": "保留原步骤并补充说明",
                "workflow_id": workflow_id,
            },
        )
        await engine.dispose()
        return response, mocks

    response, mocks = asyncio.run(scenario())

    assert response.status_code == 200
    payload = response.json()
    assert payload["executor_type"] == "mcp"
    assert payload["generated_code"] is None
    assert payload["structured_plan"]["steps"][0]["executor"] == "mcp"
    mocks["planner"].plan.assert_awaited_once()
    mocks["generator"].generate.assert_not_awaited()
    mocks["web_planner"].plan.assert_not_awaited()


def test_chat_without_mcp_catalog_asks_for_app_configuration() -> None:
    async def scenario():
        catalog = SimpleNamespace(
            list_tools=AsyncMock(return_value=[])
        )
        app = FastAPI()
        _overrides(app, catalog=catalog)
        return await _post(
            app,
            "/api/chat/message",
            {
                "session_id": "mcp-unavailable",
                "message": "读取应用状态",
            },
        )

    response = asyncio.run(scenario())

    assert response.status_code == 200
    payload = response.json()
    assert payload["needs_confirmation"] is False
    assert "安装或配置应用" in payload["reply"]
