from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.actions.protocol import StepStatus
from app.actions.registry import default_action_registry
from app.actions.validation import validate_workflow_draft
from app.actions.web import WebActionAdapter
from app.engine.runner import ExecutionResult


WEB_ACTIONS = {
    "goto",
    "click",
    "fill",
    "press",
    "select_option",
    "check",
    "uncheck",
    "wait_for",
    "wait_for_response",
    "upload",
    "download",
    "extract_text",
    "extract_table",
    "extract_fields",
    "collect_links",
    "paginate",
    "screenshot",
    "assert_text",
    "assert_logged_in",
}


def test_web_catalog_matches_design_and_schema_is_closed() -> None:
    registry = default_action_registry()
    catalog = registry.catalog(executor="web")
    assert {item.id for item in catalog.actions} == WEB_ACTIONS
    for item in catalog.actions:
        assert item.params_schema["type"] == "object"
        assert item.params_schema["additionalProperties"] is False
    assert registry.resolve("web", "navigate").definition.id == "goto"
    legacy = registry.catalog(executor="web", include_legacy=True)
    assert "code_step" in {item.id for item in legacy.actions}


def test_web_plan_validation_rejects_script_and_unknown_params() -> None:
    script = validate_workflow_draft(
        {
            "steps": [
                {
                    "step_id": 1,
                    "executor": "web",
                    "action": "code_step",
                    "params": {"script": "alert(1)"},
                }
            ]
        }
    )
    assert script.valid is False
    params = validate_workflow_draft(
        {
            "steps": [
                {
                    "step_id": 1,
                    "executor": "web",
                    "action": "goto",
                    "params": {
                        "url": "https://example.com",
                        "unknown": True,
                    },
                }
            ]
        }
    )
    assert params.valid is False


def test_web_adapter_converts_executor_outcomes() -> None:
    async def success(step, state):
        return ExecutionResult(
            success=True,
            output="ok",
            error=None,
            screenshots=[],
            step_logs=[
                {
                    "status": "success",
                    "result": {"text": "hello"},
                }
            ],
            duration_seconds=0.2,
            details={"extracted": {"text": "hello"}},
        )

    async def awaiting(step, state):
        return ExecutionResult(
            success=False,
            output="",
            error="AUTH_REQUIRED",
            screenshots=[],
            step_logs=[
                {
                    "status": "failed",
                    "failure_category": "auth_required",
                }
            ],
            duration_seconds=0.1,
            details={"awaiting_user": True},
        )

    async def scenario() -> tuple:
        class FakeWebExecutor:
            def __init__(self) -> None:
                self.outcomes = [success, awaiting]

            async def execute_steps(self, *args, **kwargs):
                handler = self.outcomes.pop(0)
                return await handler(args, kwargs)

        executor = FakeWebExecutor()
        adapter = WebActionAdapter(executor)
        context = SimpleNamespace(
            variables={"rows": [1]},
            services={"page": object(), "execution_context": {}},
            execution_id=7,
        )
        first = await adapter.execute(
            context,
            "extract_text",
            {"variable": "text"},
        )
        second = await adapter.execute(
            context,
            "assert_logged_in",
            {},
        )
        return first, second

    first, second = asyncio.run(scenario())
    assert first.status is StepStatus.SUCCESS
    assert first.variables["text"] == "hello"
    assert second.status is StepStatus.AWAITING_USER
    assert second.human_action is not None
