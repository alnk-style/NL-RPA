from __future__ import annotations

import asyncio
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.agents.debug_agent import DebugAgent
from app.agents.template_agent import TemplateManagerAgent


def _config() -> SimpleNamespace:
    return SimpleNamespace(
        DEEPSEEK_API_KEY="test-key",
        DEEPSEEK_BASE_URL="https://api.deepseek.com",
        DEEPSEEK_MODEL="deepseek-chat",
    )


def _workflow(workflow_id: int, name: str) -> SimpleNamespace:
    now = datetime(2026, 9, 16, 12, 0, 0)
    return SimpleNamespace(
        id=workflow_id,
        user_id=1,
        name=name,
        description=f"{name} description",
        natural_language_input="打开示例网站",
        structured_plan={"steps": []},
        generated_code="async def main(page):\n    pass",
        dag_definition=None,
        trigger_type="manual",
        cron_expression=None,
        status="draft",
        notification_config=None,
        created_at=now,
        updated_at=now,
    )


def test_debug_agent_fix() -> None:
    original_code = """async def main(page):
    await page.click("#old-selector")
"""
    error_log = "TimeoutError: selector #old-selector was not found"
    fixed_code = """```python
from playwright.async_api import Error as PlaywrightError


async def main(page):
    try:
        await page.click("button#submit")
        await screenshot("step_1_fixed_click")
    except PlaywrightError as exc:
        raise RuntimeError("Click failed") from exc
```"""

    async def scenario() -> tuple[str, AsyncMock]:
        with patch("app.agents.debug_agent.LLMClient") as client_class:
            client = client_class.return_value
            client.chat = AsyncMock(return_value=fixed_code)
            agent = DebugAgent(_config())
            result = await agent.fix(
                original_code,
                error_log,
                screenshot_path="logs/execution/failure.png",
            )
            return result, client.chat

    result, chat = asyncio.run(scenario())

    assert "#old-selector" not in result
    assert "async def main(page)" in result
    assert "```" not in result
    messages = chat.await_args.args[0]
    assert "logs/execution/failure.png" in messages[1]["content"]
    assert error_log in messages[1]["content"]


def test_debug_agent_repair_steps() -> None:
    response = """
{
  "steps": [
    {
      "step_id": 2,
      "action": "click",
      "locator": {"strategy": "role", "value": "button", "name": "登录"}
    },
    {
      "step_id": 3,
      "action": "screenshot",
      "params": {"name": "home"}
    }
  ]
}
"""
    plan = {
        "steps": [
            {
                "step_id": 1,
                "action": "goto",
                "params": {"url": "https://example.com"},
            },
            {
                "step_id": 2,
                "action": "click",
                "locator": {"strategy": "css", "value": "#old"},
            },
            {"step_id": 3, "action": "screenshot", "params": {}},
        ]
    }
    failure = {
        "step_index": 1,
        "step_id": 2,
        "action": "click",
        "error": "未找到元素：css=#old",
        "failure_category": "locator_not_found",
    }
    observation = {
        "kind": "browser",
        "url": "https://example.com",
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

    async def scenario() -> tuple[list[dict], AsyncMock]:
        with patch("app.agents.debug_agent.LLMClient") as client_class:
            client = client_class.return_value
            client.chat = AsyncMock(return_value=response)
            agent = DebugAgent(_config())
            steps = await agent.repair_steps(
                plan=plan,
                step_index=1,
                failure=failure,
                observation=observation,
                action_catalog="- click：点击元素",
            )
            return steps, client.chat

    steps, chat = asyncio.run(scenario())

    assert [step["action"] for step in steps] == ["click", "screenshot"]
    assert steps[0]["locator"]["strategy"] == "role"
    messages = chat.await_args.args[0]
    prompt = messages[1]["content"]
    assert "失败步骤序号（从 0 开始）：1" in prompt
    assert "角色" in prompt or "role" in prompt
    assert "点击元素" in prompt
    assert '"strategy": "role"' in prompt


def test_debug_agent_repair_steps_reports_error() -> None:
    async def scenario() -> None:
        with patch("app.agents.debug_agent.LLMClient") as client_class:
            client = client_class.return_value
            client.chat = AsyncMock(
                return_value='{"error": "观察中没有可用定位"}'
            )
            agent = DebugAgent(_config())
            await agent.repair_steps(
                plan={"steps": []},
                step_index=0,
                failure={"step_index": 0},
                observation={"elements": []},
            )

    with pytest.raises(ValueError, match="没有可用定位"):
        asyncio.run(scenario())


def test_template_agent_save_load() -> None:
    async def scenario() -> tuple[str, dict, bool]:
        with tempfile.TemporaryDirectory() as temp_dir:
            agent = TemplateManagerAgent()
            agent.templates_dir = Path(temp_dir)
            path = await agent.save(_workflow(7, "Daily report"))
            file_exists = Path(path).exists()
            data = await agent.load(7)
            return path, data, file_exists

    path, data, file_exists = asyncio.run(scenario())

    assert Path(path).name == "workflow_7.json"
    assert file_exists is True
    assert data["id"] == 7
    assert data["name"] == "Daily report"
    assert data["created_at"] == "2026-09-16T12:00:00"


def test_template_agent_list_delete() -> None:
    async def scenario() -> tuple[list[dict], bool, bool, bool]:
        with tempfile.TemporaryDirectory() as temp_dir:
            agent = TemplateManagerAgent()
            agent.templates_dir = Path(temp_dir)
            await agent.save(_workflow(1, "First"))
            await agent.save(_workflow(2, "Second"))

            templates = await agent.list_templates()
            deleted = await agent.delete(1)
            delete_missing = await agent.delete(1)

            try:
                await agent.load(1)
            except FileNotFoundError:
                load_missing = True
            else:
                load_missing = False

            return templates, deleted, delete_missing, load_missing

    templates, deleted, delete_missing, load_missing = asyncio.run(scenario())

    assert [template["id"] for template in templates] == [1, 2]
    assert all(template["path"] for template in templates)
    assert deleted is True
    assert delete_missing is False
    assert load_missing is True


if __name__ == "__main__":
    test_debug_agent_fix()
    test_template_agent_save_load()
    test_template_agent_list_delete()
    print("debug and template agent tests passed")
