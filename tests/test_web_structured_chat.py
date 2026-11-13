from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
from fastapi import FastAPI

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("DEEPSEEK_API_KEY", "test-key")
os.environ.setdefault("CREDENTIAL_ENCRYPTION_KEY", "test-encryption-key")
os.environ.setdefault("JWT_SECRET_KEY", "test-encryption-key")

from app.agents.web_catalog import WebPlanError
from app.api import chat as chat_api
from app.api import chat_router
from app.api.dependencies import get_current_user
from app.api.observations import get_browser_observer
from app.api.workspace_deps import get_workspace_context
from app.config import settings
from app.engine.browser_observer import ObservationError
from app.protocols import ObservationKind, ObservationSnapshot

INTENT = {
    "executor_type": "web",
    "trigger": {"type": "manual", "cron": None},
    "steps": [
        {
            "step_id": 1,
            "action": "navigate",
            "target": "https://example.com/messages",
        }
    ],
    "variables": [],
}

DETAILED = {
    "trigger": {"type": "manual", "cron": None},
    "steps": [
        {
            "step_id": 1,
            "action": "navigate",
            "target": "https://example.com/messages",
            "selector": None,
            "params": {"url": "https://example.com/messages"},
        }
    ],
    "variables": [],
}

STRUCTURED = {
    "executor_type": "web",
    "execution_mode": "structured",
    "trigger": {"type": "manual", "cron": None},
    "steps": [
        {
            "step_id": 1,
            "executor": "web",
            "action": "goto",
            "target": "打开消息页",
            "params": {"url": "https://example.com/messages"},
            "retry_policy": {
                "max_attempts": 2,
                "backoff_seconds": 1.0,
            },
            "depends_on": [],
        }
    ],
    "variables": [],
}

GENERATED_CODE = "await page.goto('https://example.com/messages')"


def _snapshot() -> ObservationSnapshot:
    return ObservationSnapshot(
        kind=ObservationKind.BROWSER,
        source="playwright",
        url="https://example.com/messages",
        title="消息中心",
        elements=[
            {
                "name": "登录",
                "best_locator": {
                    "strategy": "role",
                    "value": "button",
                    "name": "登录",
                },
            }
        ],
    )


def _app(
    *,
    web_planner,
    observer=None,
    code_generator=None,
    step_planner=None,
) -> FastAPI:
    app = FastAPI()
    app.include_router(chat_router)
    app.dependency_overrides[chat_api.get_intent_parser_agent] = (
        lambda: SimpleNamespace(parse=AsyncMock(return_value=dict(INTENT)))
    )
    app.dependency_overrides[chat_api.get_step_planner_agent] = (
        lambda: (
            step_planner
            if step_planner is not None
            else SimpleNamespace(
                plan=AsyncMock(return_value=dict(DETAILED))
            )
        )
    )
    app.dependency_overrides[chat_api.get_code_generator_agent] = (
        lambda: (
            code_generator
            if code_generator is not None
            else SimpleNamespace(
                generate=AsyncMock(return_value=GENERATED_CODE)
            )
        )
    )
    app.dependency_overrides[chat_api.get_web_planner_agent] = (
        lambda: web_planner
    )
    app.dependency_overrides[chat_api.get_mcp_tool_catalog] = (
        lambda: SimpleNamespace(
            list_tools=AsyncMock(return_value=[])
        )
    )
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=1
    )
    app.dependency_overrides[get_workspace_context] = (
        lambda: SimpleNamespace(
            isolation_enabled=False,
            workspace_id=None,
        )
    )
    if observer is not None:
        app.dependency_overrides[get_browser_observer] = lambda: observer
    chat_api.session_contexts.clear()
    return app


async def _message(app: FastAPI, session_id: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        return await client.post(
            "/api/chat/message",
            json={
                "session_id": session_id,
                "message": "抓取消息页",
                "workflow_id": None,
            },
        )


def test_chat_uses_structured_planner_when_enabled(monkeypatch) -> None:
    monkeypatch.setattr(settings, "WEB_STRUCTURED_ACTIONS", True)
    monkeypatch.setattr(settings, "OBSERVE_BEFORE_PLAN", False)

    async def scenario() -> tuple[httpx.Response, AsyncMock, AsyncMock]:
        web_planner = SimpleNamespace(
            plan=AsyncMock(return_value=dict(STRUCTURED))
        )
        generator = SimpleNamespace(generate=AsyncMock())
        app = _app(
            web_planner=web_planner,
            code_generator=generator,
        )
        response = await _message(app, "structured-ok")
        return response, web_planner.plan, generator.generate

    response, plan, generate = asyncio.run(scenario())

    assert response.status_code == 200
    payload = response.json()
    assert payload["structured_plan"]["execution_mode"] == "structured"
    assert payload["generated_code"] is None
    assert payload["needs_confirmation"] is True
    assert "结构化" in payload["reply"]
    plan.assert_awaited_once()
    generate.assert_not_awaited()


def test_chat_fails_closed_when_structured_plan_fails(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "WEB_STRUCTURED_ACTIONS", True)
    monkeypatch.setattr(settings, "OBSERVE_BEFORE_PLAN", False)

    async def scenario() -> tuple[httpx.Response, AsyncMock, AsyncMock]:
        web_planner = SimpleNamespace(
            plan=AsyncMock(
                side_effect=WebPlanError("缺少目标网址")
            )
        )
        generator = SimpleNamespace(
            generate=AsyncMock(return_value=GENERATED_CODE)
        )
        app = _app(
            web_planner=web_planner,
            code_generator=generator,
        )
        response = await _message(app, "structured-fallback")
        return response, web_planner.plan, generator.generate

    response, plan, generate = asyncio.run(scenario())

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "structured_draft_failed"
    plan.assert_awaited_once()
    generate.assert_not_awaited()


def test_chat_observes_page_before_structured_planning(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "WEB_STRUCTURED_ACTIONS", True)
    monkeypatch.setattr(settings, "OBSERVE_BEFORE_PLAN", True)

    async def scenario() -> tuple[httpx.Response, AsyncMock, AsyncMock]:
        web_planner = SimpleNamespace(
            plan=AsyncMock(return_value=dict(STRUCTURED))
        )
        observer = SimpleNamespace(
            observe=AsyncMock(return_value=_snapshot())
        )
        app = _app(web_planner=web_planner, observer=observer)
        response = await _message(app, "structured-observed")
        return response, observer.observe, web_planner.plan

    response, observe, plan = asyncio.run(scenario())

    assert response.status_code == 200
    observe.assert_awaited_once()
    assert observe.await_args.args[0] == (
        "https://example.com/messages"
    )
    observation = plan.await_args.kwargs["observation"]
    assert observation["url"] == "https://example.com/messages"
    assert observation["elements"][0]["name"] == "登录"


def test_chat_fails_closed_when_observation_fails(monkeypatch) -> None:
    monkeypatch.setattr(settings, "WEB_STRUCTURED_ACTIONS", True)
    monkeypatch.setattr(settings, "OBSERVE_BEFORE_PLAN", True)

    async def scenario() -> tuple[httpx.Response, AsyncMock, AsyncMock]:
        web_planner = SimpleNamespace(plan=AsyncMock())
        observer = SimpleNamespace(
            observe=AsyncMock(
                side_effect=ObservationError(
                    "Timeout 30000ms exceeded"
                )
            )
        )
        generator = SimpleNamespace(
            generate=AsyncMock(return_value=GENERATED_CODE)
        )
        app = _app(
            web_planner=web_planner,
            observer=observer,
            code_generator=generator,
        )
        response = await _message(app, "observation-fallback")
        return response, web_planner.plan, generator.generate

    response, plan, generate = asyncio.run(scenario())

    assert response.status_code == 422
    plan.assert_not_awaited()
    generate.assert_not_awaited()


def test_chat_ignores_legacy_switch_and_uses_structured_planner(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "WEB_STRUCTURED_ACTIONS", False)
    monkeypatch.setattr(settings, "OBSERVE_BEFORE_PLAN", False)

    async def scenario() -> tuple[httpx.Response, AsyncMock, AsyncMock]:
        web_planner = SimpleNamespace(
            plan=AsyncMock(return_value=dict(STRUCTURED))
        )
        generator = SimpleNamespace(
            generate=AsyncMock(return_value=GENERATED_CODE)
        )
        app = _app(
            web_planner=web_planner,
            code_generator=generator,
        )
        response = await _message(app, "legacy-path")
        return response, web_planner.plan, generator.generate

    response, plan, generate = asyncio.run(scenario())

    assert response.status_code == 200
    assert response.json()["generated_code"] is None
    plan.assert_awaited_once()
    generate.assert_not_awaited()


def test_chat_keeps_task_graph_v2_without_generated_code(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "WEB_STRUCTURED_ACTIONS", False)
    monkeypatch.setattr(settings, "OBSERVE_BEFORE_PLAN", False)
    runtime_plan = {
        "executor_type": "hybrid",
        "trigger": {"type": "manual", "cron": None},
        "variables": [{"name": "lyrics", "default": ""}],
        "steps": [
            {
                "step_id": 1,
                "executor": "transform",
                "action": "split_lines",
                "result_var": "lyrics_lines",
                "params": {"text": "${lyrics}"},
                "depends_on": [],
            }
        ],
    }

    async def scenario():
        planner = SimpleNamespace(
            plan=AsyncMock(return_value=dict(runtime_plan))
        )
        generator = SimpleNamespace(generate=AsyncMock())
        app = _app(
            web_planner=SimpleNamespace(plan=AsyncMock()),
            code_generator=generator,
            step_planner=planner,
        )
        response = await _message(app, "runtime-v2")
        return response, generator.generate

    response, generate = asyncio.run(scenario())

    assert response.status_code == 200
    payload = response.json()
    assert payload["generated_code"] is None
    assert payload["executor_type"] == "hybrid"
    assert payload["structured_plan"]["steps"][0]["result_var"] == (
        "lyrics_lines"
    )
    generate.assert_not_awaited()


def test_chat_ignores_legacy_web_switch_for_new_structured_drafts(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "STRUCTURED_EXECUTION_ENABLED", True)
    monkeypatch.setattr(settings, "WEB_STRUCTURED_ACTIONS", False)
    monkeypatch.setattr(settings, "OBSERVE_BEFORE_PLAN", False)

    async def scenario():
        web_planner = SimpleNamespace(
            plan=AsyncMock(return_value=dict(STRUCTURED))
        )
        generator = SimpleNamespace(generate=AsyncMock())
        app = _app(
            web_planner=web_planner,
            code_generator=generator,
        )
        response = await _message(app, "canonical-structured")
        return response, web_planner.plan, generator.generate

    response, plan, generate = asyncio.run(scenario())

    assert response.status_code == 200
    assert response.json()["structured_plan"]["execution_mode"] == "structured"
    assert response.json()["generated_code"] is None
    plan.assert_awaited_once()
    generate.assert_not_awaited()


def test_chat_fails_closed_when_structured_planning_fails(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "STRUCTURED_EXECUTION_ENABLED", True)
    monkeypatch.setattr(settings, "WEB_STRUCTURED_ACTIONS", True)
    monkeypatch.setattr(settings, "OBSERVE_BEFORE_PLAN", False)

    async def scenario():
        web_planner = SimpleNamespace(
            plan=AsyncMock(
                side_effect=WebPlanError("missing goal URL")
            )
        )
        generator = SimpleNamespace(
            generate=AsyncMock(return_value=GENERATED_CODE)
        )
        app = _app(
            web_planner=web_planner,
            code_generator=generator,
        )
        response = await _message(app, "structured-fail-closed")
        return response, generator.generate

    response, generate = asyncio.run(scenario())

    assert response.status_code == 422
    generate.assert_not_awaited()
