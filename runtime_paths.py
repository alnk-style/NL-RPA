from __future__ import annotations

import os
import sys
from pathlib import Path


DATA_DIR_ENV = "NL_RPA_DATA_DIR"
RESOURCE_ROOT_ENV = "NL_RPA_RESOURCE_ROOT"


def resource_root(root: str | Path | None = None) -> Path:
    if root is not None:
        return Path(root).expanduser().resolve()
    override = os.getenv(RESOURCE_ROOT_ENV)
    if override:
        return Path(override).expanduser().resolve()
    if getattr(sys, "frozen", False):
        bundle_root = getattr(sys, "_MEIPASS", None)
        if bundle_root:
            return Path(bundle_root).expanduser().resolve()
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def builtin_apps_dir(root: str | Path | None = None) -> Path:
    return resource_root(root) / "mcp_apps"


def stable_data_dir() -> Path:
    override = os.getenv(DATA_DIR_ENV)
    candidates: list[Path] = []
    if override:
        candidates.append(Path(override).expanduser())

    candidates.append(Path.home() / ".nl-rpa")
    if os.name == "nt":
        candidates.append(
            Path(
                os.environ.get(
                    "PROGRAMDATA",
                    r"C:\ProgramData",
                )
            )
            / "NL-RPA"
        )

    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
        except OSError:
            continue
    return candidates[-1]


def stable_database_url(data_dir: Path | None = None) -> str:
    root = Path(data_dir) if data_dir is not None else stable_data_dir()
    database_path = root / "data" / "rpa.db"
    database_path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite+aiosqlite:///{database_path.as_posix()}"


def stable_agent_config_path(data_dir: Path | None = None) -> Path:
    root = Path(data_dir) if data_dir is not None else stable_data_dir()
    return root / "config.json"


def stable_logs_dir(data_dir: Path | None = None) -> Path:
    root = Path(data_dir) if data_dir is not None else stable_data_dir()
    logs_dir = root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    return logs_dir


def app_mcp_logs_dir(data_dir: Path | None = None) -> Path:
    logs_dir = stable_logs_dir(data_dir) / "app_mcp"
    logs_dir.mkdir(parents=True, exist_ok=True)
    return logs_dir


def generated_apps_dir(
    data_dir: Path | None = None,
    *,
    create: bool = True,
) -> Path:
    root = Path(data_dir) if data_dir is not None else stable_data_dir()
    apps_dir = root / "apps"
    if create:
        apps_dir.mkdir(parents=True, exist_ok=True)
    return apps_dir


__all__ = [
    "DATA_DIR_ENV",
    "RESOURCE_ROOT_ENV",
    "app_mcp_logs_dir",
    "builtin_apps_dir",
    "generated_apps_dir",
    "resource_root",
    "stable_agent_config_path",
    "stable_data_dir",
    "stable_database_url",
    "stable_logs_dir",
]
