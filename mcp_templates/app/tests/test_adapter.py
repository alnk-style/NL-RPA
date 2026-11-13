from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.engine.app_mcp.contracts import ToolCallContext
from app.engine.app_mcp.runtime import load_runtime

APP_DIR = Path(__file__).resolve().parents[1]

if "__TEMPLATE_GUARD__":
    pytest.skip(
        "Template placeholder is replaced when a new app package is created.",
        allow_module_level=True,
    )


def test_generated_adapter_contract(tmp_path: Path) -> None:
    async def scenario() -> None:
        runtime = load_runtime(
            APP_DIR,
            export_root=tmp_path / "exports",
            connectors={},
        )
        try:
            probe = await runtime.probe(refresh=True)
            status = await runtime.execute_tool(
                "example_status",
                {},
                ToolCallContext(request_id="generated-test"),
            )
            write = await runtime.execute_tool(
                "example_write",
                {"value": "sample"},
                ToolCallContext(request_id="generated-write"),
            )
        finally:
            await runtime.close()

        assert probe.installed is True
        assert status.success is True
        assert write.success is True
        assert write.data["value"] == "sample"

    asyncio.run(scenario())
