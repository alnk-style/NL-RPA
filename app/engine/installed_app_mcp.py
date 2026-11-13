"""Generate and remove scoped MCP packages for installed applications."""

from __future__ import annotations

import json
import re
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from app.engine.installed_apps import (
    InstalledApp,
    app_key_for_path,
    normalize_executable_path,
)
from runtime_paths import stable_data_dir

_APP_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_SLUG_RE = re.compile(r"[^a-z0-9]+")
_ADAPTER_SOURCE = '''"""Generated scoped desktop MCP adapter."""

from app.engine.app_mcp.scoped_desktop import ScopedDesktopAdapter

InstalledDesktopAdapter = ScopedDesktopAdapter

__all__ = ["InstalledDesktopAdapter"]
'''


@dataclass(frozen=True)
class GeneratedInstalledApp:
    app_id: str
    app_dir: Path
    app_key: str
    name: str
    executable_path: str


def generated_app_id(name: str, app_key: str) -> str:
    slug = _SLUG_RE.sub("_", str(name or "").casefold()).strip("_")
    if not slug:
        slug = "installed_app"
    if not slug[0].isalpha():
        slug = f"app_{slug}"
    slug = slug[:40].rstrip("_")
    return f"{slug}_{str(app_key)[:8].lower()}"


def create_installed_app_package(
    app: InstalledApp,
    *,
    root: str | Path | None = None,
) -> GeneratedInstalledApp:
    executable = normalize_executable_path(app.executable_path)
    if executable is None:
        raise ValueError("Installed application executable is unavailable.")
    computed_key = app_key_for_path(executable)
    if app.key and app.key != computed_key:
        raise ValueError("Installed application key does not match its path.")
    apps_root = (
        Path(root).expanduser().resolve()
        if root is not None
        else (stable_data_dir() / "apps").resolve()
    )
    apps_root.mkdir(parents=True, exist_ok=True)
    app_id = generated_app_id(app.name, computed_key)
    target = apps_root / app_id
    if target.exists():
        integration = load_generated_integration(target)
        if (
            integration is not None
            and integration.get("app_key") == computed_key
            and integration.get("executable_path") == str(executable)
        ):
            return _generated(
                app_id,
                target,
                computed_key,
                app.name,
                executable,
            )
        raise FileExistsError(
            f"Generated application directory already exists: {app_id}"
        )

    temporary = apps_root / f".{app_id}.{uuid.uuid4().hex}.tmp"
    temporary.mkdir(parents=False, exist_ok=False)
    try:
        _write_package(
            temporary,
            app_id=app_id,
            app_key=computed_key,
            name=app.name,
            executable=executable,
            source=app.source,
        )
        temporary.replace(target)
    except Exception:
        if temporary.is_dir():
            shutil.rmtree(temporary, ignore_errors=True)
        raise
    return _generated(
        app_id,
        target,
        computed_key,
        app.name,
        executable,
    )


def _write_package(
    directory: Path,
    *,
    app_id: str,
    app_key: str,
    name: str,
    executable: Path,
    source: str,
) -> None:
    manifest = {
        "schema_version": 1,
        "id": app_id,
        "name": name,
        "version": "0.1.0",
        "description": (
            f"Scoped desktop MCP integration for {name}."
        ),
        "platform": "windows",
        "backend_priority": ["desktop"],
        "entrypoint": "adapter:InstalledDesktopAdapter",
        "config": {
            "connector": "desktop",
            "executable_path": str(executable),
        },
        "local": {
            "host_mode": "in_process",
            "auto_start": False,
            "startup_timeout_seconds": 5,
        },
        "tools": _tool_specs(name),
    }
    (directory / "manifest.yaml").write_text(
        yaml.safe_dump(
            manifest,
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (directory / "adapter.py").write_text(
        _ADAPTER_SOURCE,
        encoding="utf-8",
    )
    integration = {
        "kind": "installed_app_mcp",
        "app_id": app_id,
        "app_key": app_key,
        "name": name,
        "executable_path": str(executable),
        "source": source,
        "template": "scoped_desktop_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    (directory / "integration.json").write_text(
        json.dumps(integration, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _tool_specs(name: str) -> list[dict[str, Any]]:
    return [
        {
            "name": "app_launch",
            "description": f"Launch the configured {name} executable.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "wait_for_window": {"type": "boolean"},
                    "window_timeout": {
                        "type": "number",
                        "exclusiveMinimum": 0,
                    },
                },
                "additionalProperties": False,
            },
            "risk_level": "mutating",
            "output_description": "Desktop Agent launch result.",
        },
        {
            "name": "app_observe",
            "description": f"Observe windows for {name}.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "class_name": {"type": "string"},
                    "pid": {"type": "integer"},
                    "max_elements": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 1000,
                    },
                },
                "additionalProperties": False,
            },
            "risk_level": "read_only",
            "output_description": "Desktop windows and UI elements.",
        },
        {
            "name": "app_activate",
            "description": f"Activate a {name} window.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "class_name": {"type": "string"},
                    "pid": {"type": "integer"},
                    "index": {"type": "integer", "minimum": 0},
                },
                "additionalProperties": False,
            },
            "risk_level": "mutating",
            "output_description": "Desktop Agent activation result.",
        },
        {
            "name": "app_read_text",
            "description": f"Read text from a {name} control.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "automation_id": {"type": "string"},
                    "name": {"type": "string"},
                    "control_type": {"type": "string"},
                },
                "additionalProperties": False,
            },
            "risk_level": "read_only",
            "output_description": "Text and metadata from the selected control.",
        },
    ]


def load_generated_integration(
    app_dir: str | Path,
) -> dict[str, Any] | None:
    path = Path(app_dir).expanduser().resolve() / "integration.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("kind") != "installed_app_mcp":
        return None
    return payload


def generated_integration_for_key(
    app_key: str,
    *,
    root: str | Path | None = None,
) -> dict[str, Any] | None:
    apps_root = (
        Path(root).expanduser().resolve()
        if root is not None
        else (stable_data_dir() / "apps").resolve()
    )
    if not apps_root.is_dir():
        return None
    for directory in sorted(apps_root.iterdir()):
        if not directory.is_dir() or directory.name.startswith("."):
            continue
        payload = load_generated_integration(directory)
        if payload is not None and payload.get("app_key") == app_key:
            return payload
    return None


def generated_integrations_by_key(
    *,
    root: str | Path | None = None,
) -> dict[str, dict[str, Any]]:
    apps_root = (
        Path(root).expanduser().resolve()
        if root is not None
        else (stable_data_dir() / "apps").resolve()
    )
    integrations: dict[str, dict[str, Any]] = {}
    if not apps_root.is_dir():
        return integrations
    for directory in sorted(apps_root.iterdir()):
        if not directory.is_dir() or directory.name.startswith("."):
            continue
        payload = load_generated_integration(directory)
        if payload is None:
            continue
        app_key = str(payload.get("app_key") or "").strip()
        if app_key:
            integrations[app_key] = payload
    return integrations


def remove_installed_app_package(
    app_id: str,
    *,
    root: str | Path | None = None,
) -> bool:
    normalized = str(app_id or "").strip()
    if not _APP_ID_RE.fullmatch(normalized):
        raise ValueError("Generated app ID is invalid.")
    apps_root = (
        Path(root).expanduser().resolve()
        if root is not None
        else (stable_data_dir() / "apps").resolve()
    )
    target = (apps_root / normalized).resolve()
    if target.parent != apps_root:
        raise ValueError("Generated app path escaped its root.")
    if not target.is_dir():
        return False
    integration = load_generated_integration(target)
    if integration is None:
        return False
    shutil.rmtree(target)
    return True


def _generated(
    app_id: str,
    app_dir: Path,
    app_key: str,
    name: str,
    executable: Path,
) -> GeneratedInstalledApp:
    return GeneratedInstalledApp(
        app_id=app_id,
        app_dir=app_dir,
        app_key=app_key,
        name=name,
        executable_path=str(executable),
    )


__all__ = [
    "GeneratedInstalledApp",
    "create_installed_app_package",
    "generated_app_id",
    "generated_integrations_by_key",
    "generated_integration_for_key",
    "load_generated_integration",
    "remove_installed_app_package",
]
