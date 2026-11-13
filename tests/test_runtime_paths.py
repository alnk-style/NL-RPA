from __future__ import annotations

import sys
from pathlib import Path

import runtime_paths as runtime_paths_module
from desktop_agent.config import _resolve_config_path
from runtime_paths import (
    DATA_DIR_ENV,
    stable_agent_config_path,
    stable_data_dir,
    stable_database_url,
    stable_logs_dir,
)


def test_stable_paths_use_environment_override(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "nl-rpa"
    monkeypatch.setenv(DATA_DIR_ENV, str(root))

    assert stable_data_dir() == root
    assert stable_agent_config_path() == root / "config.json"
    assert stable_logs_dir() == root / "logs"
    assert stable_logs_dir().is_dir()
    assert stable_database_url() == (
        "sqlite+aiosqlite:///"
        + (root / "data" / "rpa.db").as_posix()
    )


def test_appdata_agent_config_redirects_to_stable_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv(DATA_DIR_ENV, str(tmp_path))
    appdata_config = Path(
        r"C:\Users\Administrator\AppData\Roaming"
        r"\com.nlrpa.desktop\config.json"
    )

    resolved = _resolve_config_path(
        appdata_config,
        explicit_path=False,
    )

    assert resolved == tmp_path / "config.json"


def test_explicit_agent_config_path_is_preserved(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv(DATA_DIR_ENV, str(tmp_path / "stable"))
    explicit_path = tmp_path / "custom" / "config.json"

    resolved = _resolve_config_path(
        explicit_path,
        explicit_path=True,
    )

    assert resolved == explicit_path


def test_frozen_resource_root_uses_meipass(
    tmp_path: Path,
    monkeypatch,
) -> None:
    bundle_root = tmp_path / "bundle"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle_root), raising=False)

    assert runtime_paths_module.resource_root() == bundle_root.resolve()
    assert runtime_paths_module.builtin_apps_dir() == (
        bundle_root / "mcp_apps"
    ).resolve()


def test_app_mcp_logs_use_stable_data_dir(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv(DATA_DIR_ENV, str(tmp_path))

    logs_dir = runtime_paths_module.app_mcp_logs_dir()

    assert logs_dir == tmp_path.resolve() / "logs" / "app_mcp"
    assert logs_dir.is_dir()


def test_resource_root_can_be_injected_for_tests(tmp_path: Path) -> None:
    assert runtime_paths_module.resource_root(tmp_path) == tmp_path.resolve()
    assert runtime_paths_module.builtin_apps_dir(tmp_path) == (
        tmp_path / "mcp_apps"
    ).resolve()


def test_generated_apps_dir_uses_stable_data_dir(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv(DATA_DIR_ENV, str(tmp_path))

    generated = runtime_paths_module.generated_apps_dir()

    assert generated == tmp_path.resolve() / "apps"
    assert generated.is_dir()
