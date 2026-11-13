from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.web_catalog import WebPlanError
from app.agents.web_planner import WebPlannerAgent


def _config() -> SimpleNamespace:
    return SimpleNamespace(
        DEEPSEEK_API_KEY="test-key",
        DEEPSEEK_BASE_URL="https://api.deepseek.com",
        DEEPSEEK_MODEL="deepseek-chat",
    )


def _intent() -> dict:
    return {
        "executor_type": "web",
        "trigger": {"type": "cron", "cron": "0 9 * * *"},
        "steps": [
            {
                "step_id": 1,
                "action": "navigate",
                "target": "https://example.com/messages",
            }
        ],
        "variables": [{"name": "messages"}],
    }


def _planner_response() -> str:
    return json.dumps(
        {
            "steps": [
                {
                    "step_id": 1,
                    "action": "goto",
                    "target": "打开消息页",
                    "params": {
                        "url": "https://example.com/messages"
                    },
                },
                {
                    "step_id": 2,
                    "action": "click",
                    "target": "登录按钮",
                    "locator": {
                        "strategy": "role",
                        "value": "button",
                        "name": "登录",
                        "fallbacks": [
                            {
                                "strategy": "testid",
                                "value": "login-button",
                            }
                        ],
                    },
                },
                {
                    "step_id": 3,
                    "action": "extract_table",
                    "params": {
                        "selector": "table",
                        "variable": "messages",
                    },
                },
            ]
        },
        ensure_ascii=False,
    )


def test_web_planner_returns_validated_structured_plan() -> None:
    observation = {
        "kind": "browser",
        "url": "https://example.com/messages",
        "elements": [
            {
                "name": "登录",
                "best_locator": {
                    "strategy": "role",
                    "value": "button",
                    "name": "登录",
                },
            }
        ],
    }

    async def scenario() -> tuple[dict, AsyncMock]:
        with patch("app.agents.web_planner.LLMClient") as client_class:
            chat = AsyncMock(return_value=_planner_response())
            client_class.return_value.chat = chat
            agent = WebPlannerAgent(_config())
            plan = await agent.plan(
                _intent(),
                observation=observation,
                start_url="https://example.com/messages",
                detailed_plan=_intent(),
            )
            return plan, chat

    plan, chat = asyncio.run(scenario())

    assert plan["executor_type"] == "web"
    assert plan["execution_mode"] == "structured"
    assert plan["trigger"] == {"type": "cron", "cron": "0 9 * * *"}
    assert plan["variables"] == [{"name": "messages"}]
    assert [step["step_id"] for step in plan["steps"]] == [1, 2, 3]
    assert plan["steps"][1]["locator"]["strategy"] == "role"
    assert all(
        step["executor"] == "web" for step in plan["steps"]
    )
    prompt = chat.await_args.args[0][1]["content"]
    assert "网页动作目录" in prompt
    assert "- goto：" in prompt
    assert '"best_locator"' in prompt
    assert "https://example.com/messages" in prompt


def test_web_planner_rejects_action_outside_catalog() -> None:
    async def scenario() -> None:
        with patch("app.agents.web_planner.LLMClient") as client_class:
            client_class.return_value.chat = AsyncMock(
                return_value=json.dumps(
                    {
                        "steps": [
                            {
                                "step_id": 1,
                                "action": "navigate",
                                "params": {
                                    "url": "https://example.com"
                                },
                            }
                        ]
                    }
                )
            )
            agent = WebPlannerAgent(_config())
            await agent.plan(_intent())

    with pytest.raises(WebPlanError, match="动作不在网页动作目录"):
        asyncio.run(scenario())


def test_web_planner_reports_model_error() -> None:
    async def scenario() -> None:
        with patch("app.agents.web_planner.LLMClient") as client_class:
            client_class.return_value.chat = AsyncMock(
                return_value='{"error": "缺少目标网址"}'
            )
            agent = WebPlannerAgent(_config())
            await agent.plan(_intent())

    with pytest.raises(WebPlanError, match="缺少目标网址"):
        asyncio.run(scenario())


def test_web_planner_rejects_invalid_json() -> None:
    async def scenario() -> None:
        with patch("app.agents.web_planner.LLMClient") as client_class:
            client_class.return_value.chat = AsyncMock(
                return_value="not-json"
            )
            agent = WebPlannerAgent(_config())
            await agent.plan(_intent())

    with pytest.raises(WebPlanError, match="有效的 JSON"):
        asyncio.run(scenario())
