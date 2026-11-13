from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.engine.code_validator import CodeValidator, ValidationResult
from app.engine.engine import ExecutionEngine
from app.engine.runner import ExecutionResult


def _config() -> SimpleNamespace:
    return SimpleNamespace(
        DOCKER_SANDBOX_IMAGE="rpa-sandbox:test",
        EXECUTION_TIMEOUT=30,
        MAX_RETRY=3,
        WEB_STRUCTURED_ACTIONS=False,
    )


def _success_result(output: str = "ok") -> ExecutionResult:
    return ExecutionResult(
        success=True,
        output=output,
        error=None,
        screenshots=[],
        step_logs=[],
        duration_seconds=0.1,
    )


def _assert_value_error(coroutine) -> None:
    try:
        asyncio.run(coroutine)
    except ValueError:
        return
    raise AssertionError("Expected ValueError")


def test_engine_execute() -> None:
    async def scenario() -> tuple[ExecutionResult, ExecutionEngine]:
        engine = ExecutionEngine(_config())
        expected = _success_result()
        engine.sandbox.execute_with_fallback = AsyncMock(return_value=expected)
        workflow = SimpleNamespace(
            generated_code="await page.goto('https://example.com')",
            structured_plan={"variables": {"query": "demo"}},
        )
        context = {
            "credentials": {"username": "demo"},
            "variables": {"query": "override"},
            "user_context": {"tenant": "acme"},
        }

        result = await engine.execute(workflow, context)
        return result, engine

    result, engine = asyncio.run(scenario())

    assert result.success is True
    engine.sandbox.execute_with_fallback.assert_awaited_once()
    code, execution_context = engine.sandbox.execute_with_fallback.await_args.args
    assert code == "await page.goto('https://example.com')"
    assert execution_context["credentials"] == {"username": "demo"}
    assert execution_context["variables"] == {"query": "override"}
    assert execution_context["user_context"] == {"tenant": "acme"}


def test_engine_strips_main_guard_before_execution() -> None:
    async def scenario() -> str:
        engine = ExecutionEngine(_config())
        engine.sandbox.execute_with_fallback = AsyncMock(
            return_value=_success_result()
        )
        workflow = SimpleNamespace(
            generated_code=(
                "async def main(page):\n"
                "    await page.goto('https://example.com')\n\n"
                "if __name__ == '__main__':\n"
                "    asyncio.run(main(None))\n"
            ),
            structured_plan={},
        )

        await engine.execute(workflow)
        return engine.sandbox.execute_with_fallback.await_args.args[0]

    executed_code = asyncio.run(scenario())

    assert "async def main(page):" in executed_code
    assert "__name__" not in executed_code
    assert "__main__" not in executed_code


def test_engine_execute_no_code() -> None:
    engine = ExecutionEngine(_config())
    workflow = SimpleNamespace(
        generated_code="",
        structured_plan={},
    )

    _assert_value_error(engine.execute(workflow))


def test_engine_executes_legacy_runtime_helper_calls() -> None:
    async def scenario() -> tuple[ExecutionResult, ExecutionEngine]:
        engine = ExecutionEngine(_config())
        engine.sandbox.execute_with_fallback = AsyncMock(
            return_value=_success_result("legacy helpers")
        )
        workflow = SimpleNamespace(
            generated_code=(
                "async def main(page):\n"
                "    await wait_for(2000)\n"
                '    await log("legacy")\n'
            ),
            structured_plan={},
        )

        result = await engine.execute(workflow)
        return result, engine

    result, engine = asyncio.run(scenario())

    assert result.success is True
    engine.sandbox.execute_with_fallback.assert_awaited_once()


def test_engine_dry_run() -> None:
    async def scenario() -> tuple[ExecutionResult, ExecutionEngine, dict]:
        engine = ExecutionEngine(_config())
        expected = _success_result("dry run")
        engine.execute_code = AsyncMock(return_value=expected)
        context = {"variables": {"query": "demo"}}

        result = await engine.dry_run(
            "log('dry run')",
            context,
        )
        return result, engine, context

    result, engine, original_context = asyncio.run(scenario())

    assert result.success is True
    code, execution_context = engine.execute_code.await_args.args
    assert code == "log('dry run')"
    assert execution_context["variables"] == {"query": "demo"}
    assert execution_context["dry_run"] is True
    assert "dry_run" not in original_context


def test_engine_rejects_unsafe_code() -> None:
    async def scenario() -> ExecutionEngine:
        engine = ExecutionEngine(_config())
        workflow = SimpleNamespace(
            generated_code="import os\nos.system('whoami')",
            structured_plan={},
        )
        await engine.execute(workflow)
        return engine

    _assert_value_error(scenario())


def test_engine_execute_with_debug_retries() -> None:
    async def scenario() -> tuple[ExecutionResult, ExecutionEngine]:
        engine = ExecutionEngine(_config())
        failed = ExecutionResult(
            success=False,
            output="",
            error="temporary failure",
            screenshots=[],
            step_logs=[],
            duration_seconds=0.1,
        )
        succeeded = _success_result("recovered")
        engine.execute = AsyncMock(
            side_effect=[failed, failed, succeeded],
        )

        result = await engine.execute_with_debug(
            SimpleNamespace(generated_code="log('recover')"),
        )
        return result, engine

    result, engine = asyncio.run(scenario())

    assert result.success is True
    assert engine.execute.await_count == 3


def test_engine_uses_structured_web_executor_when_enabled() -> None:
    async def scenario() -> tuple[ExecutionResult, ExecutionEngine]:
        engine = ExecutionEngine(_config())
        engine.config.WEB_STRUCTURED_ACTIONS = True
        expected = _success_result("structured")
        engine.web_executor = SimpleNamespace(
            execute=AsyncMock(return_value=expected)
        )
        engine.sandbox.execute_with_fallback = AsyncMock()
        workflow = SimpleNamespace(
            generated_code=None,
            structured_plan={
                "steps": [
                    {
                        "action": "goto",
                        "params": {"url": "https://example.com"},
                    }
                ]
            },
        )

        result = await engine.execute(
            workflow,
            {"variables": {"query": "demo"}},
        )
        return result, engine

    result, engine = asyncio.run(scenario())

    assert result.success is True
    assert result.output == "structured"
    engine.web_executor.execute.assert_awaited_once()
    workflow, context = engine.web_executor.execute.await_args.args
    assert workflow.structured_plan["steps"][0]["action"] == "goto"
    assert context["variables"] == {"query": "demo"}
    assert context["credentials"] == {}
    engine.sandbox.execute_with_fallback.assert_not_awaited()


def test_engine_keeps_legacy_plan_on_code_path_when_flag_enabled() -> None:
    async def scenario() -> tuple[ExecutionResult, ExecutionEngine]:
        engine = ExecutionEngine(_config())
        engine.config.WEB_STRUCTURED_ACTIONS = True
        engine.web_executor = SimpleNamespace(
            execute=AsyncMock(return_value=_success_result("structured"))
        )
        engine.sandbox.execute_with_fallback = AsyncMock(
            return_value=_success_result("legacy")
        )
        workflow = SimpleNamespace(
            generated_code="log('legacy')",
            structured_plan={
                "steps": [
                    {
                        "action": "navigate",
                        "params": {"url": "https://example.com"},
                    }
                ]
            },
        )

        result = await engine.execute(workflow)
        return result, engine

    result, engine = asyncio.run(scenario())

    assert result.success is True
    assert result.output == "legacy"
    engine.sandbox.execute_with_fallback.assert_awaited_once()
    engine.web_executor.execute.assert_not_awaited()


def test_engine_rejects_invalid_explicit_structured_plan() -> None:
    async def scenario() -> ExecutionEngine:
        engine = ExecutionEngine(_config())
        engine.config.WEB_STRUCTURED_ACTIONS = True
        workflow = SimpleNamespace(
            generated_code="log('legacy')",
            structured_plan={
                "execution_mode": "structured",
                "steps": [{"action": "navigate", "params": {}}],
            },
        )
        await engine.execute(workflow)
        return engine

    _assert_value_error(scenario())


def test_engine_ignores_structured_plan_when_flag_disabled() -> None:
    async def scenario() -> tuple[ExecutionResult, ExecutionEngine]:
        engine = ExecutionEngine(_config())
        engine.web_executor = SimpleNamespace(
            execute=AsyncMock(return_value=_success_result("structured"))
        )
        engine.sandbox.execute_with_fallback = AsyncMock(
            return_value=_success_result("legacy")
        )
        workflow = SimpleNamespace(
            generated_code="log('legacy')",
            structured_plan={
                "steps": [
                    {
                        "action": "goto",
                        "params": {"url": "https://example.com"},
                    }
                ]
            },
        )

        result = await engine.execute(workflow)
        return result, engine

    result, engine = asyncio.run(scenario())

    assert result.output == "legacy"
    engine.sandbox.execute_with_fallback.assert_awaited_once()
    engine.web_executor.execute.assert_not_awaited()


def test_validator_safe_code() -> None:
    result = CodeValidator.validate(
        """
import asyncio

async def run():
    await page.goto("https://example.com")
    await page.click("#submit")
"""
    )

    assert isinstance(result, ValidationResult)
    assert result.is_safe is True
    assert result.violations == []
    assert result.warnings == []


def test_validator_forbidden_import() -> None:
    result = CodeValidator.validate("import subprocess")

    assert result.is_safe is False
    assert any("subprocess" in violation for violation in result.violations)


def test_validator_forbidden_call() -> None:
    result = CodeValidator.validate("eval('1 + 1')")

    assert result.is_safe is False
    assert any("eval" in violation for violation in result.violations)


def test_validator_open_suggests_save_file() -> None:
    result = CodeValidator.validate('open("out.csv", "w")')

    assert result.is_safe is False
    assert any(
        "save_file" in violation for violation in result.violations
    )


def test_validator_rejects_await_on_sync_log() -> None:
    result = CodeValidator.validate(
        """
async def main(page):
    await log("finished")
""",
        strict_runtime_helpers=True,
    )

    assert result.is_safe is False
    assert any(
        "log" in violation and "without await" in violation
        for violation in result.violations
    )


def test_validator_rejects_wait_for_delay_call() -> None:
    result = CodeValidator.validate(
        """
async def main(page):
    await wait_for(2000)
""",
        strict_runtime_helpers=True,
    )

    assert result.is_safe is False
    assert any(
        "wait_for" in violation and "wait_for_timeout" in violation
        for violation in result.violations
    )


def test_validator_allows_legacy_helper_calls_with_warnings() -> None:
    result = CodeValidator.validate(
        """
async def main(page):
    await wait_for(2000)
    await log("finished")
"""
    )

    assert result.is_safe is True
    assert result.violations == []
    assert len(result.warnings) == 2


def test_validator_accepts_documented_runtime_helpers() -> None:
    result = CodeValidator.validate(
        """
async def main(page):
    await page.wait_for_timeout(2000)
    await wait_for("#ready", 5000)
    log("ready")
    await screenshot("step_1_ready")
    table = await extract_table("table")
    await save_file("report.csv", "name\\nAlice\\n")
    return table
"""
    )

    assert result.is_safe is True
    assert result.violations == []


def test_validator_multiple_violations() -> None:
    result = CodeValidator.validate(
        """
import os
import subprocess
result = eval("1 + 1")
"""
    )

    assert result.is_safe is False
    assert len(result.violations) >= 3


def test_validator_result_structure() -> None:
    result = ValidationResult(
        is_safe=False,
        violations=["Forbidden import: os"],
        warnings=["review manually"],
    )

    assert result.is_safe is False
    assert result.violations == ["Forbidden import: os"]
    assert result.warnings == ["review manually"]


if __name__ == "__main__":
    test_engine_execute()
    test_engine_execute_no_code()
    test_engine_dry_run()
    test_engine_rejects_unsafe_code()
    test_engine_execute_with_debug_retries()
    test_validator_safe_code()
    test_validator_forbidden_import()
    test_validator_forbidden_call()
    test_validator_multiple_violations()
    test_validator_result_structure()
    print("engine tests passed")
