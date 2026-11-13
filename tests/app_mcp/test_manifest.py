from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.engine.app_mcp.manifest import AppManifest, load_manifest

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _manifest_payload(
    *,
    tools: list[dict] | None = None,
) -> dict:
    return {
        "schema_version": 1,
        "id": "sample_app",
        "name": "Sample App",
        "version": "1.0.0",
        "platform": "cross_platform",
        "backend_priority": ["api", "cli"],
        "entrypoint": "adapter:SampleAdapter",
        "tools": tools
        or [
            {
                "name": "sample_read",
                "description": "Read sample data",
                "input_schema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                "risk_level": "read_only",
                "output_description": "Sample data",
            }
        ],
    }


def test_load_demo_manifest() -> None:
    manifest = load_manifest(PROJECT_ROOT / "mcp_apps" / "demo_app" / "manifest.yaml")

    assert manifest.id == "demo_app"
    assert manifest.version == "0.1.0"
    assert {tool.name for tool in manifest.tools} == {
        "demo_status",
        "demo_execute",
        "demo_export",
    }


def test_duplicate_tool_names_are_rejected() -> None:
    tool = _manifest_payload()["tools"][0]
    payload = _manifest_payload(tools=[tool, dict(tool)])

    with pytest.raises(ValidationError, match="工具名必须唯一"):
        AppManifest.model_validate(payload)


def test_invalid_risk_level_is_rejected() -> None:
    tool = _manifest_payload()["tools"][0]
    tool["risk_level"] = "dangerous"

    with pytest.raises(ValidationError, match="risk_level"):
        AppManifest.model_validate(_manifest_payload(tools=[tool]))


def test_invalid_input_schema_is_rejected() -> None:
    tool = _manifest_payload()["tools"][0]
    tool["input_schema"] = {
        "type": "object",
        "properties": {"value": {"type": "not-a-json-schema-type"}},
    }

    with pytest.raises(ValidationError, match="输入 Schema"):
        AppManifest.model_validate(_manifest_payload(tools=[tool]))


def test_host_mode_defaults_to_subprocess_and_accepts_in_process() -> None:
    payload = _manifest_payload()
    default_manifest = AppManifest.model_validate(payload)
    assert default_manifest.local.host_mode == "subprocess"

    payload["local"] = {"host_mode": "in_process"}
    manifest = AppManifest.model_validate(payload)
    assert manifest.local.host_mode == "in_process"


def test_invalid_host_mode_is_rejected() -> None:
    payload = _manifest_payload()
    payload["local"] = {"host_mode": "remote"}
    with pytest.raises(ValidationError, match="host_mode"):
        AppManifest.model_validate(payload)


def test_generic_desktop_uses_in_process_host() -> None:
    manifest = load_manifest(
        PROJECT_ROOT / "mcp_apps" / "generic_desktop" / "manifest.yaml"
    )
    assert manifest.local.host_mode == "in_process"
    assert manifest.local.startup_timeout_seconds == 5
    assert manifest.local.unsupported_reason is None
