from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
ENTRY_POINT = ROOT / "app" / "main.py"
BINARIES_DIR = ROOT / "desktop" / "src-tauri" / "binaries"
BUILD_DIR = ROOT / "build" / "pyinstaller" / "backend"
DEFAULT_TARGET_TRIPLE = "x86_64-pc-windows-msvc"
PLAYWRIGHT_BROWSERS_DIR = Path(
    os.environ.get(
        "PLAYWRIGHT_BROWSERS_PATH",
        Path(
            os.environ.get(
                "LOCALAPPDATA",
                Path.home() / "AppData" / "Local",
            )
        )
        / "ms-playwright",
    )
).expanduser()
REQUIRED_PLAYWRIGHT_DIRS = (
    "chromium-1169",
    "chromium_headless_shell-1169",
    "ffmpeg-1011",
    "winldd-1007",
)

HIDDEN_IMPORTS = (
    "uvicorn",
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    "sqlalchemy",
    "sqlalchemy.dialects.sqlite.aiosqlite",
    "aiosqlite",
    "app.models",
    "app.engine",
    "app.agents",
    "app.scheduler",
    "app.notifications",
    "app.engine.installed_apps",
    "app.engine.installed_app_mcp",
    "app.engine.app_mcp.scoped_desktop",
    "app.engine.app_mcp.process_main",
    "app.engine.app_mcp.backends",
    "app.engine.app_mcp.backends.api",
    "app.engine.app_mcp.backends.browser",
    "app.engine.app_mcp.backends.cli",
    "app.engine.app_mcp.backends.desktop",
    "app.metrics",
    "app.ops",
    "app.security",
    "app.migrations",
    "app.regression",
    "asyncpg",
    "redis",
    "redis.asyncio",
    "mcp",
    "mcp.client.streamable_http",
    "comtypes",
    "comtypes.client",
    "win32gui",
    "win32ui",
    "PIL",
    "PIL.Image",
)

COLLECT_SUBMODULES = (
    "app.models",
    "app.engine",
    "app.agents",
    "app.scheduler",
    "app.notifications",
    "app.metrics",
    "app.ops",
    "app.security",
)

EXCLUDED_MODULES = (
    "IPython",
    "jupyter",
    "matplotlib",
    "notebook",
    "numpy",
    "pandas",
    "pywinauto",
    "win32com",
    "pytest",
    "scipy",
    "tkinter",
    "app.testing",
    "embedded_postgres",
    "fakeredis",
)


def builtin_app_data_args() -> list[str]:
    apps_root = ROOT / "mcp_apps"
    entries: list[str] = []
    for path in sorted(apps_root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(apps_root)
        if (
            "__pycache__" in relative.parts
            or path.suffix.lower() in {".pyc", ".pyo"}
        ):
            continue
        destination = "mcp_apps"
        if relative.parent != Path("."):
            destination = (
                "mcp_apps/" + relative.parent.as_posix()
            )
        entries.append(
            f"--add-data={path}{os.pathsep}{destination}"
        )
    return entries


def application_profile_data_args() -> list[str]:
    profiles_root = ROOT / "desktop_agent" / "profiles"
    return [
        f"--add-data={path}{os.pathsep}desktop_agent/profiles"
        for path in sorted(profiles_root.glob("*.json"))
        if path.is_file()
    ]


def pyinstaller_args() -> list[str]:
    args = [
        str(ENTRY_POINT),
        "--noconfirm",
        "--clean",
        "--onefile",
        "--name=backend-server",
        "--paths",
        str(ROOT),
        "--distpath",
        str(BINARIES_DIR),
        "--workpath",
        str(BUILD_DIR),
        "--specpath",
        str(BUILD_DIR),
        f"--add-data={ROOT / 'docker'}{os.pathsep}docker",
    ]
    args.extend(builtin_app_data_args())
    args.extend(application_profile_data_args())
    args.extend(
        f"--add-data="
        f"{PLAYWRIGHT_BROWSERS_DIR / directory}"
        f"{os.pathsep}ms-playwright/{directory}"
        for directory in REQUIRED_PLAYWRIGHT_DIRS
    )
    args.extend(f"--hidden-import={module}" for module in HIDDEN_IMPORTS)
    args.extend(
        f"--collect-submodules={package}"
        for package in COLLECT_SUBMODULES
    )
    args.extend(
        f"--exclude-module={module}"
        for module in EXCLUDED_MODULES
    )
    return args


def tauri_sidecar_path(
    executable: Path,
    target_triple: str,
) -> Path:
    return executable.with_name(
        f"{executable.stem}-{target_triple}{executable.suffix}"
    )


def run_pyinstaller(args: Sequence[str]) -> None:
    try:
        from PyInstaller.__main__ import run
    except ImportError as exc:
        raise SystemExit(
            "PyInstaller is required. Run "
            "`python -m pip install -r requirements.txt` first."
        ) from exc

    run(list(args))


def ensure_playwright_browser() -> None:
    subprocess.run(
        [
            sys.executable,
            "-m",
            "playwright",
            "install",
            "chromium",
        ],
        check=True,
    )
    missing = [
        directory
        for directory in REQUIRED_PLAYWRIGHT_DIRS
        if not (PLAYWRIGHT_BROWSERS_DIR / directory).is_dir()
    ]
    if missing:
        raise FileNotFoundError(
            "Playwright browser directories are missing: "
            + ", ".join(missing)
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the NL-RPA backend sidecar with PyInstaller.",
    )
    parser.add_argument(
        "--target-triple",
        default=os.environ.get(
            "TAURI_ENV_TARGET_TRIPLE",
            DEFAULT_TARGET_TRIPLE,
        ),
        help="Target triple used by Tauri externalBin.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the PyInstaller command without running it.",
    )
    return parser.parse_args()


def main() -> int:
    options = parse_args()
    if os.name != "nt":
        raise SystemExit(
            "The desktop sidecars must be built on Windows."
        )

    BINARIES_DIR.mkdir(parents=True, exist_ok=True)
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    args = pyinstaller_args()

    if options.dry_run:
        print(" ".join(args))
        return 0

    ensure_playwright_browser()
    run_pyinstaller(args)
    executable = BINARIES_DIR / "backend-server.exe"
    if not executable.exists():
        raise FileNotFoundError(
            f"PyInstaller did not create {executable}"
        )

    tauri_path = tauri_sidecar_path(
        executable,
        options.target_triple,
    )
    shutil.copy2(executable, tauri_path)
    print(f"Backend sidecar: {executable}")
    print(f"Tauri sidecar: {tauri_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
