from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.agents.code_generator import (
    CODE_GENERATOR_SYSTEM_PROMPT,
    CodeGeneratorAgent,
)
from app.agents.validator_wrapper import generate_and_validate


def _config() -> SimpleNamespace:
    return SimpleNamespace(
        DEEPSEEK_API_KEY="test-key",
        DEEPSEEK_BASE_URL="https://api.deepseek.com",
        DEEPSEEK_MODEL="deepseek-chat",
    )


def test_code_generator_success() -> None:
    generated_code = """```python
from playwright.async_api import Error as PlaywrightError


async def main(page):
    try:
        await page.goto("https://example.com")
        await screenshot("step_1_open_page")
    except PlaywrightError as exc:
        raise RuntimeError("Playwright step failed") from exc
    except Exception as exc:
        raise RuntimeError("Unexpected workflow error") from exc
```"""

    async def scenario() -> tuple[str, AsyncMock]:
        with patch("app.agents.code_generator.LLMClient") as client_class:
            client = client_class.return_value
            client.chat = AsyncMock(return_value=generated_code)
            agent = CodeGeneratorAgent(_config())
            result = await agent.generate(
                {
                    "steps": [
                        {
                            "step_id": 1,
                            "action": "navigate",
                            "target": "https://example.com",
                        }
                    ]
                },
                {"variables": {}},
            )
            return result, client.chat

    result, chat = asyncio.run(scenario())

    assert "async def main" in result
    assert "playwright" in result
    assert "```" not in result
    assert chat.await_args.kwargs["temperature"] == 0.2


def test_code_generator_prompt_documents_runtime_helper_signatures() -> None:
    prompt = CODE_GENERATOR_SYSTEM_PROMPT

    assert "await screenshot(name)" in prompt
    assert "log(message)" in prompt
    assert "await extract_table(selector)" in prompt
    assert "await wait_for(selector, timeout)" in prompt
    assert "await page.wait_for_timeout(milliseconds)" in prompt
    assert "await save_file(path, content)" in prompt
    assert "do not await log" in prompt
    assert "requires an authenticated session" in prompt
    assert "never save" in prompt
    assert "empty results" in prompt
    assert "page.url alone" in prompt
    assert "AUTH_REQUIRED" in prompt


def test_generate_and_validate_retry() -> None:
    dangerous_code = """import os


async def main(page):
    os.system("echo unsafe")
"""
    safe_code = """from playwright.async_api import Error as PlaywrightError


async def main(page):
    try:
        await page.goto("https://example.com")
        await screenshot("step_1_open_page")
    except PlaywrightError as exc:
        raise RuntimeError("Playwright step failed") from exc
    except Exception as exc:
        raise RuntimeError("Unexpected workflow error") from exc
"""

    async def scenario() -> tuple[str, list[str], AsyncMock]:
        with patch("app.agents.code_generator.LLMClient") as client_class:
            client = client_class.return_value
            client.chat = AsyncMock(
                side_effect=[dangerous_code, safe_code]
            )
            agent = CodeGeneratorAgent(_config())
            code, warnings = await generate_and_validate(
                agent,
                {"steps": [{"step_id": 1, "action": "navigate"}]},
                {"variables": {}},
            )
            return code, warnings, client.chat

    code, warnings, chat = asyncio.run(scenario())

    assert code == safe_code.strip()
    assert warnings == []
    assert chat.await_count == 2
    retry_messages = chat.await_args_list[1].args[0]
    assert "Forbidden import: os" in retry_messages[1]["content"]


def test_generate_retries_when_open_is_used() -> None:
    unsafe_code = """async def main(page):
    with open("out.csv", "w", encoding="utf-8") as handle:
        handle.write("a,b")
"""
    safe_code = """async def main(page):
    await save_file("out.csv", "a,b")
"""

    async def scenario() -> tuple[str, AsyncMock]:
        with patch("app.agents.code_generator.LLMClient") as client_class:
            client = client_class.return_value
            client.chat = AsyncMock(side_effect=[unsafe_code, safe_code])
            agent = CodeGeneratorAgent(_config())
            code = await agent.generate(
                {"steps": []},
                {"variables": {}},
            )
            return code, client.chat

    code, chat = asyncio.run(scenario())

    assert code == safe_code.strip()
    assert chat.await_count == 2
    retry_messages = chat.await_args_list[1].args[0]
    assert "save_file" in retry_messages[1]["content"]


def test_generate_retries_when_helper_contract_is_wrong() -> None:
    legacy_code = """async def main(page):
    await wait_for(2000)
    await log("finished")
"""
    corrected_code = """async def main(page):
    await page.wait_for_timeout(2000)
    log("finished")
"""

    async def scenario() -> tuple[str, AsyncMock]:
        with patch("app.agents.code_generator.LLMClient") as client_class:
            client = client_class.return_value
            client.chat = AsyncMock(
                side_effect=[legacy_code, corrected_code]
            )
            agent = CodeGeneratorAgent(_config())
            code = await agent.generate(
                {"steps": []},
                {"variables": {}},
            )
            return code, client.chat

    code, chat = asyncio.run(scenario())

    assert code == corrected_code.strip()
    assert chat.await_count == 2
    retry_messages = chat.await_args_list[1].args[0]
    feedback = retry_messages[1]["content"]
    assert "page.wait_for_timeout" in feedback
    assert "without await" in feedback


if __name__ == "__main__":
    test_code_generator_success()
    test_generate_and_validate_retry()
    print("code generator tests passed")
