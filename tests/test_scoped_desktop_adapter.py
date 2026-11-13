from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.engine.app_mcp.contracts import (
    AdapterEnvironment,
    ToolCallContext,
    bind_call_context,
)
from app.engine.app_mcp.scoped_desktop import ScopedDesktopAdapter


class FakeDesktopConnector:
    def __init__(self) -> None:
        self.actions: list[tuple[str, dict]] = []

    async def auth_status(self) -> dict:
        return {
            "success": True,
            "data": {"connected": True, "online_agents": [1]},
        }

    async def observe(self, payload: dict) -> dict:
        return {
            "success": True,
            "data": {
                "windows": [{"title": "WPS Office"}],
                "elements": [],
            },
        }

    async def act(self, action: dict, context: dict) -> dict:
        self.actions.append((action["action"], action["params"]))
        return {
            "success": True,
            "data": {"state": "window_found"},
        }


def test_scoped_adapter_ignores_model_supplied_launch_path(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "WPS Office" / "ksolaunch.exe"
    executable.parent.mkdir()
    executable.write_bytes(b"MZ")
    connector = FakeDesktopConnector()
    adapter = ScopedDesktopAdapter(
        AdapterEnvironment(
            app_dir=tmp_path,
            export_root=tmp_path / "exports",
            connectors={"desktop": connector},
            options={
                "connector": "desktop",
                "executable_path": str(executable),
            },
        )
    )
    context = ToolCallContext(
        request_id="scope",
        user_id=1,
        workflow_id=2,
    )

    async def scenario():
        with bind_call_context(context):
            return await adapter.execute(
                "app_launch",
                {
                    "path": r"C:\Windows\System32\notepad.exe",
                    "wait_for_window": True,
                },
            )

    result = asyncio.run(scenario())

    assert result.success is True
    assert connector.actions == [
        (
            "launch_app",
            {
                "path": str(executable),
                "wait_for_window": True,
            },
        )
    ]
