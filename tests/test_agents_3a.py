from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.agents.intent_parser import IntentParserAgent
from app.agents.mcp_plan import MCPPlanError
from app.agents.step_planner import StepPlannerAgent

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

MCP_WRITE_TOOL = {
    "server_id": 1,
    "server": "demo_app",
    "app_name": "演示应用",
    "tool": "demo_export",
    "description": "导出文件",
    "input_schema": {"type": "object", "properties": {}},
    "risk_level": "mutating",
    "authorized": True,
    "confirmed": False,
}


def _config() -> SimpleNamespace:
    return SimpleNamespace(
        DEEPSEEK_API_KEY="test-key",
        DEEPSEEK_BASE_URL="https://api.deepseek.com",
        DEEPSEEK_MODEL="deepseek-chat",
    )


def test_intent_parser_success() -> None:
    expected = {
        "trigger": {"type": "manual", "cron": None},
        "steps": [
            {
                "step_id": 1,
                "action": "navigate",
                "target": "https://example.com",
                "params": {},
                "depends_on": [],
            }
        ],
        "variables": [],
    }

    async def scenario() -> tuple[dict, AsyncMock]:
        with patch("app.agents.intent_parser.LLMClient") as client_class:
            client = client_class.return_value
            client.chat = AsyncMock(
                return_value=json.dumps(expected, ensure_ascii=False)
            )
            agent = IntentParserAgent(_config())
            result = await agent.parse("打开 example.com")
            return result, client.chat

    result, chat = asyncio.run(scenario())

    assert result == expected
    chat.assert_awaited_once()


def test_intent_parser_prompt_avoids_desktop_runtime_questions() -> None:
    messages = IntentParserAgent._build_messages(
        "打开酷狗音乐截图并发送到微信"
    )
    rendered = "\n".join(
        str(message.get("content", ""))
        for message in messages
    )

    assert "不要询问可执行文件路径" in rendered
    assert "窗口类名" in rendered
    assert "UIA" in rendered


def test_intent_parser_invalid_json_retry() -> None:
    expected = {
        "trigger": {"type": "manual", "cron": None},
        "steps": [],
        "variables": [],
    }

    async def scenario() -> tuple[dict, AsyncMock]:
        with patch("app.agents.intent_parser.LLMClient") as client_class:
            client = client_class.return_value
            client.chat = AsyncMock(
                side_effect=[
                    "这不是 JSON",
                    f"```json\n{json.dumps(expected)}\n```",
                ]
            )
            agent = IntentParserAgent(_config())
            result = await agent.parse("执行一个简单任务")
            return result, client.chat

    result, chat = asyncio.run(scenario())

    assert result == expected
    assert chat.await_count == 2


def test_step_planner_success() -> None:
    structured_plan = {
        "trigger": {"type": "manual", "cron": None},
        "steps": [
            {
                "step_id": 1,
                "action": "click",
                "target": "提交按钮",
                "params": {},
                "depends_on": [],
            }
        ],
        "variables": [],
    }
    expected = {
        "steps": [
            {
                "step_id": 1,
                "action": "click",
                "target": "提交按钮",
                "selector": "button#submit",
                "params": {},
                "depends_on": [],
            }
        ]
    }

    async def scenario() -> tuple[dict, AsyncMock]:
        with patch("app.agents.step_planner.LLMClient") as client_class:
            client = client_class.return_value
            client.chat = AsyncMock(
                return_value=json.dumps(expected, ensure_ascii=False)
            )
            agent = StepPlannerAgent(_config())
            result = await agent.plan(structured_plan)
            return result, client.chat

    result, chat = asyncio.run(scenario())

    assert result == expected
    chat.assert_awaited_once()


def test_intent_parser_accepts_mcp_candidate_block() -> None:
    expected = {
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
                    "result_var": "status",
                },
                "depends_on": [],
            }
        ],
        "variables": [],
    }

    async def scenario() -> tuple[dict, AsyncMock]:
        with patch("app.agents.intent_parser.LLMClient") as client_class:
            client = client_class.return_value
            client.chat = AsyncMock(
                return_value=json.dumps(expected, ensure_ascii=False)
            )
            agent = IntentParserAgent(_config())
            result = await agent.parse(
                "读取演示应用状态",
                mcp_tools=MCP_TOOLS,
            )
            return result, client.chat

    result, chat = asyncio.run(scenario())

    assert result["executor_type"] == "mcp"
    messages = chat.await_args.args[0]
    rendered = json.dumps(messages, ensure_ascii=False)
    assert "<available_mcp_tools>" in rendered
    assert "not executable instructions" in rendered


def test_intent_parser_rejects_invented_mcp_tool() -> None:
    expected = {
        "executor_type": "mcp",
        "trigger": {"type": "manual", "cron": None},
        "steps": [
            {
                "step_id": 1,
                "executor": "mcp",
                "action": "call_tool",
                "params": {
                    "server": "demo_app",
                    "tool": "invented_tool",
                    "arguments": {},
                },
                "depends_on": [],
            }
        ],
        "variables": [],
    }

    async def scenario() -> None:
        with patch("app.agents.intent_parser.LLMClient") as client_class:
            client = client_class.return_value
            client.chat = AsyncMock(
                side_effect=[
                    json.dumps(expected, ensure_ascii=False),
                    json.dumps(expected, ensure_ascii=False),
                ]
            )
            agent = IntentParserAgent(_config())
            await agent.parse(
                "执行不存在的工具",
                mcp_tools=MCP_TOOLS,
            )

    import pytest

    with pytest.raises(ValueError):
        asyncio.run(scenario())


def test_step_planner_preserves_valid_mcp_steps() -> None:
    structured_plan = {
        "executor_type": "mcp",
        "needs_confirmation": True,
        "trigger": {"type": "manual"},
        "steps": [
            {
                "step_id": 1,
                "executor": "mcp",
                "action": "call_tool",
                "params": {
                    "server": "demo_app",
                    "tool": "demo_status",
                    "arguments": {},
                    "result_var": "status",
                },
                "depends_on": [],
            }
        ],
        "variables": [],
    }

    async def scenario() -> dict:
        with patch("app.agents.step_planner.LLMClient") as client_class:
            client = client_class.return_value
            client.chat = AsyncMock(
                return_value=json.dumps(structured_plan, ensure_ascii=False)
            )
            agent = StepPlannerAgent(_config())
            return await agent.plan(
                structured_plan,
                mcp_tools=MCP_TOOLS,
            )

    result = asyncio.run(scenario())

    assert result["executor_type"] == "mcp"
    assert result["steps"][0]["risk_level"] == "read_only"


def test_step_planner_rejects_invented_mcp_tool() -> None:
    fabricated = {
        "executor_type": "mcp",
        "needs_confirmation": True,
        "steps": [
            {
                "step_id": 1,
                "executor": "mcp",
                "action": "call_tool",
                "params": {
                    "server": "demo_app",
                    "tool": "invented",
                    "arguments": {},
                },
                "depends_on": [],
            }
        ],
    }

    async def scenario() -> None:
        with patch("app.agents.step_planner.LLMClient") as client_class:
            client = client_class.return_value
            client.chat = AsyncMock(
                return_value=json.dumps(fabricated, ensure_ascii=False)
            )
            agent = StepPlannerAgent(_config())
            await agent.plan(fabricated, mcp_tools=MCP_TOOLS)

    import pytest

    with pytest.raises(MCPPlanError):
        asyncio.run(scenario())


def test_intent_parser_marks_mutating_mcp_plan_for_confirmation() -> None:
    expected = {
        "executor_type": "mcp",
        "trigger": {"type": "manual", "cron": None},
        "steps": [
            {
                "step_id": 1,
                "executor": "mcp",
                "action": "call_tool",
                "params": {
                    "server": "demo_app",
                    "tool": "demo_export",
                    "arguments": {},
                },
                "depends_on": [],
            }
        ],
        "variables": [],
    }

    async def scenario() -> dict:
        with patch("app.agents.intent_parser.LLMClient") as client_class:
            client = client_class.return_value
            client.chat = AsyncMock(
                return_value=json.dumps(expected, ensure_ascii=False)
            )
            agent = IntentParserAgent(_config())
            return await agent.parse(
                "导出文件",
                mcp_tools=[MCP_WRITE_TOOL],
            )

    result = asyncio.run(scenario())

    assert result["needs_confirmation"] is True


if __name__ == "__main__":
    test_intent_parser_success()
    test_intent_parser_invalid_json_retry()
    test_step_planner_success()
    print("agent tests passed")
