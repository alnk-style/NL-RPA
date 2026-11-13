from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.actions.mcp import MCPActionAdapter
from app.actions.protocol import StepStatus
from app.actions.registry import default_action_registry
from app.actions.windows import WindowsActionAdapter
from app.engine.runner import ExecutionResult


WINDOWS_ACTIONS = {
    "launch_app",
    "activate_window",
    "click_element",
    "click_relative",
    "type_text",
    "press_hotkey",
    "wait",
    "screenshot",
    "read_text",
    "read_window_text",
    "close_app",
}


def test_windows_and_mcp_catalog_are_generic_and_closed() -> None:
    registry = default_action_registry()
    windows = registry.catalog(executor="windows")
    assert {item.id for item in windows.actions} == WINDOWS_ACTIONS
    assert "fetch_kugou_lyrics" not in {item.id for item in windows.actions}
    assert "create_document" not in {item.id for item in windows.actions}
    legacy = registry.catalog(executor="windows", include_legacy=True)
    assert "fetch_kugou_lyrics" in {item.id for item in legacy.actions}

    mcp = registry.catalog(executor="mcp")
    assert {item.id for item in mcp.actions} == {"call_tool"}
    assert registry.validate(
        "windows",
        "launch_app",
        {"path": "notepad.exe"},
    ).valid
    assert not registry.validate(
        "windows",
        "launch_app",
        {"path": "notepad.exe", "shell": "cmd /c whoami"},
    ).valid


def test_windows_adapter_converts_dispatcher_result() -> None:
    async def scenario():
        dispatcher = SimpleNamespace()

        async def dispatch(workflow, context):
            return ExecutionResult(
                success=True,
                output="launched",
                error=None,
                screenshots=[],
                step_logs=[
                    {
                        "status": "success",
                        "action": "launch_app",
                    }
                ],
                duration_seconds=0.3,
            )

        dispatcher.dispatch = dispatch
        adapter = WindowsActionAdapter(dispatcher)
        context = SimpleNamespace(
            workflow=SimpleNamespace(id=1, user_id=1, name="w"),
            variables={},
            execution_id=5,
            services={"execution_context": {}},
        )
        return await adapter.execute(
            context,
            "launch_app",
            {"path": "notepad.exe"},
        )

    result = asyncio.run(scenario())
    assert result.status is StepStatus.SUCCESS
    assert result.message == "launched"


def test_mcp_adapter_converts_registry_outcome() -> None:
    class Outcome:
        success = True
        result = {"content": [{"type": "text", "text": "ok"}]}
        error = None
        error_category = SimpleNamespace(value="unknown")
        risk_level = "read_only"
        duration_ms = 12.0
        audit_id = 9
        server = "local"
        tool = "echo"
        requires_confirmation = False

        def to_step_details(self):
            return {"server": self.server, "tool": self.tool}

    class Registry:
        async def call_tool(self, *args, **kwargs):
            return Outcome()

    async def scenario():
        adapter = MCPActionAdapter(Registry())
        context = SimpleNamespace(
            workflow=SimpleNamespace(id=1, user_id=1, workspace_id=None),
            variables={},
            execution_id=5,
            services={"execution_context": {}},
        )
        return await adapter.execute(
            context,
            "call_tool",
            {
                "server": "local",
                "tool": "echo",
                "arguments": {"text": "hi"},
            },
        )

    result = asyncio.run(scenario())
    assert result.status is StepStatus.SUCCESS
    assert result.value == "ok"
