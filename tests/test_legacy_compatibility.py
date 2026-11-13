from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.actions.registry import default_action_registry
from app.actions.validation import validate_workflow_draft
from app.engine.engine import ExecutionEngine
from app.engine.runner import ExecutionResult


def test_new_catalog_excludes_specialized_and_script_actions() -> None:
    catalog = default_action_registry().catalog(include_legacy=False)
    ids = {(item.executor, item.id) for item in catalog.actions}
    for forbidden in (
        ("web", "code_step"),
        ("windows", "fetch_kugou_lyrics"),
        ("windows", "create_document"),
        ("windows", "create_spreadsheet"),
        ("windows", "insert_image"),
    ):
        assert forbidden not in ids


def test_legacy_catalog_keeps_historical_aliases() -> None:
    registry = default_action_registry()
    legacy = registry.catalog(include_legacy=True)
    ids = {(item.executor, item.id) for item in legacy.actions}
    assert ("web", "code_step") in ids
    assert ("transform", "split_lines") in ids
    assert registry.resolve("web", "navigate").definition.id == "goto"


def test_structured_validation_rejects_script_and_unknown_actions() -> None:
    script = validate_workflow_draft(
        {
            "execution_mode": "structured",
            "steps": [
                {
                    "step_id": 1,
                    "executor": "web",
                    "action": "code_step",
                    "params": {"script": "document.body.innerHTML = ''"},
                }
            ],
        }
    )
    assert script.valid is False
    assert any("forbidden action" in error for error in script.errors)

    unknown = validate_workflow_draft(
        {
            "execution_mode": "structured",
            "steps": [
                {
                    "step_id": 1,
                    "executor": "web",
                    "action": "not_real",
                    "params": {},
                }
            ],
        }
    )
    assert unknown.valid is False


def test_structured_script_fails_closed_without_code_execution() -> None:
    async def scenario() -> tuple[bool, Exception | None]:
        config = SimpleNamespace(
            WEB_STRUCTURED_ACTIONS=True,
            LEGACY_CODE_EXECUTION_ENABLED=True,
            EXECUTION_TIMEOUT=10,
            SCREENSHOT_ENABLED=False,
            SCREENSHOT_TIMEOUT_MS=1000,
            SCREENSHOT_FULL_PAGE=False,
            BROWSER_MODE="isolated",
            BROWSER_HEADLESS=True,
            MAX_RETRY=1,
            DOCKER_SANDBOX_IMAGE="unused",
        )
        execution_engine = ExecutionEngine(config)
        sandbox_called = False

        async def sandbox_execute(*args, **kwargs):
            nonlocal sandbox_called
            sandbox_called = True
            return ExecutionResult(
                success=True,
                output="",
                error=None,
                screenshots=[],
                step_logs=[],
                duration_seconds=0,
            )

        execution_engine.sandbox.execute_with_fallback = sandbox_execute
        workflow = SimpleNamespace(
            execution_mode="structured",
            executor_type="web",
            structured_plan={
                "execution_mode": "structured",
                "steps": [
                    {
                        "step_id": 1,
                        "executor": "web",
                        "action": "code_step",
                        "params": {"script": "alert(1)"},
                    }
                ],
            },
            generated_code="async def main(page):\n    pass\n",
        )
        error: Exception | None = None
        try:
            await execution_engine.execute(workflow, {})
        except Exception as exc:  # noqa: BLE001
            error = exc
        return sandbox_called, error

    sandbox_called, error = asyncio.run(scenario())
    assert sandbox_called is False
    assert isinstance(error, ValueError)


def test_legacy_code_requires_explicit_enable_switch() -> None:
    async def scenario(enabled: bool) -> tuple[bool, Exception | None]:
        config = SimpleNamespace(
            WEB_STRUCTURED_ACTIONS=True,
            LEGACY_CODE_EXECUTION_ENABLED=enabled,
            EXECUTION_TIMEOUT=10,
            SCREENSHOT_ENABLED=False,
            SCREENSHOT_TIMEOUT_MS=1000,
            SCREENSHOT_FULL_PAGE=False,
            BROWSER_MODE="isolated",
            BROWSER_HEADLESS=True,
            MAX_RETRY=1,
            DOCKER_SANDBOX_IMAGE="unused",
        )
        execution_engine = ExecutionEngine(config)
        called = False

        async def sandbox_execute(*args, **kwargs):
            nonlocal called
            called = True
            return ExecutionResult(
                success=True,
                output="legacy",
                error=None,
                screenshots=[],
                step_logs=[],
                duration_seconds=0.1,
            )

        execution_engine.sandbox.execute_with_fallback = sandbox_execute
        workflow = SimpleNamespace(
            execution_mode="legacy_code",
            executor_type="web",
            structured_plan=None,
            generated_code=(
                "async def main(page):\n"
                "    await page.goto('https://example.com')\n"
            ),
        )
        error: Exception | None = None
        try:
            await execution_engine.execute(workflow, {})
        except Exception as exc:  # noqa: BLE001
            error = exc
        return called, error

    disabled_called, disabled_error = asyncio.run(scenario(False))
    assert disabled_called is False
    assert isinstance(disabled_error, ValueError)

    enabled_called, enabled_error = asyncio.run(scenario(True))
    assert enabled_called is True
    assert enabled_error is None
