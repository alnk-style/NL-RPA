from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from app.engine.app_mcp.contracts import (
    AdapterEnvironment,
    ToolCallContext,
    bind_call_context,
)
from app.engine.connectors.base import connector_result
from app.engine.connectors.desktop_bridge import DesktopBridgeConnector
from app.engine.runner import ExecutionResult
from mcp_apps.generic_desktop.adapter import GenericDesktopAdapter

PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_DIR = PROJECT_ROOT / "mcp_apps" / "generic_desktop"


class FakeDesktopConnector:
    def __init__(self) -> None:
        self.observations: list[dict[str, Any]] = []
        self.actions: list[tuple[dict[str, Any], dict[str, Any]]] = []

    async def auth_status(self) -> dict[str, Any]:
        return connector_result(
            True,
            data={"connected": True, "online_agents": [3]},
        )

    async def capabilities(self) -> dict[str, Any]:
        return connector_result(
            True,
            data={"actions": ["launch_app", "activate_window", "read_text"]},
            online_agents=[3],
        )

    async def observe(
        self,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.observations.append(dict(context or {}))
        return connector_result(
            True,
            data={
                "windows": [{"title": "Untitled - Notepad"}],
                "elements": [{"name": "Text editor"}],
            },
        )

    async def act(
        self,
        action: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.actions.append((dict(action), dict(context or {})))
        return connector_result(
            True,
            data={
                "action": action["action"],
                "params": action.get("params") or {},
                "output": "desktop ok",
            },
        )


class FailingDesktopConnector(FakeDesktopConnector):
    async def act(
        self,
        action: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return connector_result(
            False,
            error=("Access denied for C:\\Users\\private\\app.exe token=super-secret"),
        )


class FakeConnectionManager:
    def get_online_agents(self) -> list[int]:
        return [3]


class FakeDesktopDispatcher:
    def __init__(self) -> None:
        self.connection_manager = FakeConnectionManager()
        self.calls: list[tuple[Any, dict[str, Any]]] = []

    async def inspect(
        self,
        request: dict[str, Any] | None = None,
        *,
        agent_id: int | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        return {
            "success": True,
            "windows": [{"title": "Untitled - Notepad"}],
            "elements": [],
        }

    async def dispatch(
        self,
        workflow: Any,
        context: dict[str, Any] | None = None,
    ) -> ExecutionResult:
        self.calls.append((workflow, dict(context or {})))
        step = workflow.structured_plan["steps"][0]
        return ExecutionResult(
            success=True,
            output="desktop bridge ok",
            error=None,
            screenshots=[],
            step_logs=[
                {
                    "step_index": 0,
                    "step_id": step.get("step_id"),
                    "executor": "windows",
                    "action": step.get("action"),
                    "status": "success",
                }
            ],
            duration_seconds=0.01,
        )


def _adapter(connector: FakeDesktopConnector) -> GenericDesktopAdapter:
    return GenericDesktopAdapter(
        AdapterEnvironment(
            app_dir=APP_DIR,
            export_root=Path(".tmp") / "desktop-test",
            connectors={"desktop": connector},
            options={},
        )
    )


def test_generic_desktop_probe_and_observe() -> None:
    async def scenario() -> None:
        connector = FakeDesktopConnector()
        adapter = _adapter(connector)
        try:
            probe = await adapter.probe()
            observed = await adapter.execute(
                "desktop_observe",
                {"title": "Notepad"},
            )
        finally:
            await adapter.close()

        assert probe.installed is True
        assert probe.running is True
        assert observed.success is True
        assert observed.data["windows"][0]["title"].startswith("Untitled")
        assert connector.observations == [{"title": "Notepad"}]

    asyncio.run(scenario())


def test_generic_desktop_act_passes_injected_context() -> None:
    async def scenario() -> None:
        connector = FakeDesktopConnector()
        adapter = _adapter(connector)
        context = ToolCallContext(
            request_id="desktop-request",
            user_id=7,
            workflow_id=11,
            execution_id=12,
        )
        try:
            with bind_call_context(context):
                result = await adapter.execute(
                    "desktop_launch",
                    {"path": "notepad.exe"},
                )
        finally:
            await adapter.close()

        assert result.success is True
        assert result.data["action"] == "launch_app"
        action, call_context = connector.actions[0]
        assert action["params"] == {"path": "notepad.exe"}
        assert call_context["workflow_id"] == 11
        assert call_context["user_id"] == 7
        assert call_context["dispatch_context"]["execution_log_id"] == 12

    asyncio.run(scenario())


def test_generic_desktop_requires_workflow_context_for_actions() -> None:
    async def scenario() -> None:
        connector = FakeDesktopConnector()
        adapter = _adapter(connector)
        try:
            result = await adapter.execute(
                "desktop_read_text",
                {"name": "Text editor"},
            )
        finally:
            await adapter.close()

        assert result.success is False
        assert result.error is not None
        assert result.error.code == "missing_context"
        assert connector.actions == []

    asyncio.run(scenario())


def test_generic_desktop_sanitizes_connector_errors() -> None:
    async def scenario() -> None:
        adapter = _adapter(FailingDesktopConnector())
        context = ToolCallContext(
            request_id="desktop-error",
            workflow_id=11,
        )
        try:
            with bind_call_context(context):
                result = await adapter.execute(
                    "desktop_activate",
                    {"title": "Private App"},
                )
        finally:
            await adapter.close()

        assert result.success is False
        assert result.error is not None
        assert "C:\\Users" not in result.message
        assert "super-secret" not in result.message

    asyncio.run(scenario())


def test_generic_desktop_reuses_desktop_bridge_connector() -> None:
    async def scenario() -> None:
        dispatcher = FakeDesktopDispatcher()
        connector = DesktopBridgeConnector(dispatcher)
        adapter = _adapter(connector)
        context = ToolCallContext(
            request_id="desktop-bridge",
            user_id=7,
            workflow_id=15,
            execution_id=16,
        )
        try:
            with bind_call_context(context):
                result = await adapter.execute(
                    "desktop_launch",
                    {"path": "notepad.exe"},
                )
        finally:
            await adapter.close()

        assert result.success is True
        assert len(dispatcher.calls) == 1
        workflow, dispatch_context = dispatcher.calls[0]
        step = workflow.structured_plan["steps"][0]
        assert step["action"] == "launch_app"
        assert step["params"] == {"path": "notepad.exe"}
        assert dispatch_context["execution_log_id"] == 16

    asyncio.run(scenario())
