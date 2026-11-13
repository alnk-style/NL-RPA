from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from app.engine.app_mcp.catalog import AppCatalog
from app.engine.app_mcp.contracts import (
    ActionResult,
    AppProbe,
    FileArtifact,
    ToolCallContext,
)
from app.engine.app_mcp.runtime import load_runtime
from scripts.run_app_mcp_server import validate_bind_host

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class SlowAdapter:
    def __init__(self, delay: float = 0.2) -> None:
        self.delay = delay
        self.active = 0
        self.max_active = 0

    async def probe(self) -> AppProbe:
        return AppProbe(installed=True, running=True, version="0.1.0")

    async def observe(self) -> dict[str, Any]:
        return {"ok": True}

    async def execute(
        self,
        action: str,
        args: dict[str, Any],
    ) -> ActionResult:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(self.delay)
            return ActionResult.ok(data={"action": action})
        finally:
            self.active -= 1

    async def close(self) -> None:
        return None


class ArtifactAdapter(SlowAdapter):
    def __init__(self, path: Path) -> None:
        super().__init__(delay=0)
        self.path = path

    async def execute(
        self,
        action: str,
        args: dict[str, Any],
    ) -> ActionResult:
        return ActionResult.ok(
            artifacts=[
                FileArtifact(
                    name="outside.txt",
                    path=self.path,
                    size=1,
                )
            ]
        )


def test_load_runtime_uses_manifest_entrypoint(tmp_path: Path) -> None:
    runtime = load_runtime(
        PROJECT_ROOT / "mcp_apps" / "demo_app",
        export_root=tmp_path / "exports",
    )
    try:
        assert runtime.manifest.id == "demo_app"
        assert type(runtime.adapter).__name__ == "DemoAppAdapter"
    finally:
        asyncio.run(runtime.close())


def test_runtime_times_out_adapter_execution(tmp_path: Path) -> None:
    runtime = load_runtime(
        PROJECT_ROOT / "mcp_apps" / "demo_app",
        export_root=tmp_path / "exports",
    )
    slow = SlowAdapter()
    runtime.adapter = slow
    runtime.timeout_seconds = 0.01

    async def scenario() -> ActionResult:
        try:
            return await runtime.execute_tool(
                "demo_status",
                {},
                ToolCallContext(request_id="timeout"),
            )
        finally:
            await runtime.close()

    result = asyncio.run(scenario())

    assert result.success is False
    assert result.error is not None
    assert result.error.code == "timeout"


def test_runtime_serializes_calls_when_concurrency_is_one(
    tmp_path: Path,
) -> None:
    runtime = load_runtime(
        PROJECT_ROOT / "mcp_apps" / "demo_app",
        export_root=tmp_path / "exports",
        max_concurrency=1,
    )
    slow = SlowAdapter(delay=0.03)
    runtime.adapter = slow
    runtime.max_concurrency = 1
    runtime._semaphore = asyncio.Semaphore(1)

    async def scenario() -> None:
        try:
            await asyncio.gather(
                runtime.execute_tool(
                    "demo_status",
                    {},
                    ToolCallContext(request_id="one"),
                ),
                runtime.execute_tool(
                    "demo_status",
                    {},
                    ToolCallContext(request_id="two"),
                ),
            )
        finally:
            await runtime.close()

    asyncio.run(scenario())

    assert slow.max_active == 1


def test_runtime_rejects_artifact_outside_export_root(
    tmp_path: Path,
) -> None:
    export_root = tmp_path / "exports"
    runtime = load_runtime(
        PROJECT_ROOT / "mcp_apps" / "demo_app",
        export_root=export_root,
    )
    runtime.adapter = ArtifactAdapter(tmp_path / "outside.txt")

    async def scenario() -> ActionResult:
        try:
            return await runtime.execute_tool(
                "demo_status",
                {},
                ToolCallContext(request_id="artifact"),
            )
        finally:
            await runtime.close()

    result = asyncio.run(scenario())

    assert result.success is False
    assert result.error is not None
    assert result.error.code == "artifact_path_denied"


def test_catalog_exposes_loaded_tools() -> None:
    runtime = load_runtime(
        PROJECT_ROOT / "mcp_apps" / "demo_app",
        export_root=PROJECT_ROOT / ".tmp" / "app-mcp-catalog",
    )
    catalog = AppCatalog()
    try:
        catalog.register(runtime)
        assert catalog.get("demo_app") is runtime
        assert catalog.tool("demo_app", "demo_status") is not None
        assert {item["name"] for item in catalog.tools("demo_app")} == {
            "demo_status",
            "demo_execute",
            "demo_export",
        }
    finally:
        asyncio.run(catalog.close())


def test_startup_script_rejects_non_loopback_hosts() -> None:
    with pytest.raises(SystemExit, match="loopback"):
        validate_bind_host("0.0.0.0")
