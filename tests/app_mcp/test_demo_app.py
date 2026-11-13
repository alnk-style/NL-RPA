from __future__ import annotations

import asyncio
from pathlib import Path

from app.engine.app_mcp.contracts import (
    AdapterEnvironment,
    ToolCallContext,
    bind_call_context,
)
from mcp_apps.demo_app.adapter import DemoAppAdapter

PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_DIR = PROJECT_ROOT / "mcp_apps" / "demo_app"


def _adapter(export_root: Path) -> DemoAppAdapter:
    return DemoAppAdapter(
        AdapterEnvironment(
            app_dir=APP_DIR,
            export_root=export_root,
            connectors={},
            options={},
        )
    )


def test_demo_status_returns_success() -> None:
    async def scenario() -> None:
        adapter = _adapter(Path(".tmp") / "demo-test")
        try:
            probe = await adapter.probe()
            result = await adapter.execute("demo_status", {})
        finally:
            await adapter.close()

        assert probe.installed is True
        assert probe.running is True
        assert result.success is True
        assert result.data["version"] == "0.1.0"
        assert result.data["runtime"]["installed"] is True

    asyncio.run(scenario())


def test_demo_execute_returns_structured_result_with_context() -> None:
    async def scenario() -> None:
        adapter = _adapter(Path(".tmp") / "demo-test")
        context = ToolCallContext(
            request_id="request-1",
            user_id=3,
            workflow_id=4,
            execution_id=5,
        )
        try:
            with bind_call_context(context):
                result = await adapter.execute(
                    "demo_execute",
                    {"value": "hello", "note": "demo"},
                )
        finally:
            await adapter.close()

        assert result.success is True
        assert result.data["value"] == "hello"
        assert result.data["note"] == "demo"
        assert result.data["context"]["workflow_id"] == 4
        assert result.data["context"]["execution_id"] == 5

    asyncio.run(scenario())


def test_demo_export_writes_only_inside_export_root(tmp_path: Path) -> None:
    async def scenario() -> None:
        export_root = tmp_path / "exports"
        adapter = _adapter(export_root)
        try:
            result = await adapter.execute(
                "demo_export",
                {"text": "hello export", "file_name": "result.txt"},
            )
        finally:
            await adapter.close()

        assert result.success is True
        assert result.data["file_name"] == "result.txt"
        exported = Path(result.data["path"])
        assert exported.is_absolute()
        assert exported.parent == export_root.resolve()
        assert exported.read_text(encoding="utf-8") == "hello export"
        assert result.data["size"] == len(b"hello export")
        assert result.data["summary"] == "hello export"
        assert result.artifacts[0].path == exported

    asyncio.run(scenario())


def test_demo_export_rejects_path_traversal(tmp_path: Path) -> None:
    async def scenario() -> None:
        export_root = tmp_path / "exports"
        adapter = _adapter(export_root)
        try:
            result = await adapter.execute(
                "demo_export",
                {"text": "blocked", "file_name": "../outside.txt"},
            )
        finally:
            await adapter.close()

        assert result.success is False
        assert result.error is not None
        assert result.error.code == "invalid_file_name"
        assert not (tmp_path / "outside.txt").exists()

    asyncio.run(scenario())
