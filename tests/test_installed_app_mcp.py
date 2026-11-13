from __future__ import annotations

from pathlib import Path

import pytest

from app.engine.app_mcp.manifest import load_manifest
from app.engine.installed_apps import InstalledApp, app_key_for_path


def _installed_app(tmp_path: Path) -> InstalledApp:
    executable = tmp_path / "WPS Office" / "ksolaunch.exe"
    executable.parent.mkdir()
    executable.write_bytes(b"MZ")
    return InstalledApp(
        key=app_key_for_path(executable),
        name="WPS Office",
        executable_path=str(executable),
        source="start_menu",
    )


def test_create_installed_app_package_is_deterministic(
    tmp_path: Path,
) -> None:
    from app.engine.installed_app_mcp import (
        create_installed_app_package,
        load_generated_integration,
    )

    app = _installed_app(tmp_path)
    root = tmp_path / "generated"

    first = create_installed_app_package(app, root=root)
    second = create_installed_app_package(app, root=root)

    assert first.app_id == second.app_id
    assert first.app_id.startswith("wps_office_")
    assert first.app_dir == root / first.app_id
    manifest = load_manifest(first.app_dir / "manifest.yaml")
    assert manifest.id == first.app_id
    assert manifest.local.host_mode == "in_process"
    assert manifest.config["executable_path"] == app.executable_path
    assert [tool.name for tool in manifest.tools] == [
        "app_launch",
        "app_observe",
        "app_activate",
        "app_read_text",
    ]
    assert (first.app_dir / "adapter.py").is_file()
    integration = load_generated_integration(first.app_dir)
    assert integration is not None
    assert integration["app_key"] == app.key
    assert integration["executable_path"] == app.executable_path


def test_remove_installed_app_package_is_scoped(
    tmp_path: Path,
) -> None:
    from app.engine.installed_app_mcp import (
        create_installed_app_package,
        remove_installed_app_package,
    )

    app = _installed_app(tmp_path)
    root = tmp_path / "generated"
    generated = create_installed_app_package(app, root=root)

    with pytest.raises(ValueError):
        remove_installed_app_package("../outside", root=root)

    assert remove_installed_app_package(generated.app_id, root=root)
    assert not generated.app_dir.exists()
    assert not remove_installed_app_package(generated.app_id, root=root)
