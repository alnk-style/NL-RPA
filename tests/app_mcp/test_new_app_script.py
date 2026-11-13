from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.engine.app_mcp.contracts import ToolCallContext
from app.engine.app_mcp.manifest import load_manifest
from app.engine.app_mcp.runtime import load_runtime
from scripts.new_app_mcp import AppTemplateError, create_app_package

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_template_generates_valid_manifest_and_adapter(tmp_path: Path) -> None:
    apps_root = tmp_path / "mcp_apps"
    apps_root.mkdir()
    target = create_app_package(
        app_id="example_app",
        name="Example App",
        apps_root=apps_root,
        templates_root=PROJECT_ROOT / "mcp_templates",
    )

    manifest = load_manifest(target / "manifest.yaml")
    assert manifest.id == "example_app"
    assert manifest.name == "Example App"
    assert len(manifest.tools) >= 2
    assert (target / "adapter.py").is_file()
    assert (target / "tests" / "test_adapter.py").is_file()
    assert "ExampleAppAdapter" in (target / "adapter.py").read_text(
        encoding="utf-8"
    )

    async def scenario() -> None:
        runtime = load_runtime(
            target,
            export_root=tmp_path / "exports",
            connectors={},
        )
        try:
            probe = await runtime.probe(refresh=True)
            result = await runtime.execute_tool(
                "example_status",
                {},
                context=ToolCallContext(),
            )
            assert probe.installed is True
            assert result.success is True
        finally:
            await runtime.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "app_id",
    ["../escape", "Upper", "1bad", "bad-name", ""],
)
def test_template_rejects_invalid_app_ids(
    tmp_path: Path,
    app_id: str,
) -> None:
    apps_root = tmp_path / "mcp_apps"
    apps_root.mkdir()
    with pytest.raises(AppTemplateError):
        create_app_package(
            app_id=app_id,
            name="Example",
            apps_root=apps_root,
            templates_root=PROJECT_ROOT / "mcp_templates",
        )


def test_template_refuses_to_overwrite_existing_app(tmp_path: Path) -> None:
    apps_root = tmp_path / "mcp_apps"
    existing = apps_root / "example_app"
    existing.mkdir(parents=True)
    with pytest.raises(AppTemplateError, match="already exists"):
        create_app_package(
            app_id="example_app",
            name="Example",
            apps_root=apps_root,
            templates_root=PROJECT_ROOT / "mcp_templates",
        )
