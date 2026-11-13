from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
ENTRY_POINT = ROOT / "desktop_agent" / "main.py"
BINARIES_DIR = ROOT / "desktop" / "src-tauri" / "binaries"
BUILD_DIR = ROOT / "build" / "pyinstaller" / "desktop-agent"
DEFAULT_TARGET_TRIPLE = "x86_64-pc-windows-msvc"

HIDDEN_IMPORTS = (
    "websockets",
    "pywinauto",
    "pywinauto.keyboard",
    "pywinauto.mouse",
    "win32gui",
    "win32api",
    "win32con",
    "PIL",
    "PIL.Image",
    "PIL.ImageGrab",
)

COLLECT_SUBMODULES = (
    "desktop_agent.actions",
    "pywinauto",
    "win32com",
    "comtypes",
)

EXCLUDED_MODULES = (
    "apscheduler",
    "docker",
    "fastapi",
    "matplotlib",
    "numpy",
    "openai",
    "pandas",
    "playwright",
    "pytest",
    "scipy",
    "sqlalchemy",
    "tkinter",
    "uvicorn",
)


def pyinstaller_args() -> list[str]:
    args = [
        str(ENTRY_POINT),
        "--noconfirm",
        "--clean",
        "--onefile",
        "--name=desktop-agent",
        "--paths",
        str(ROOT),
        "--distpath",
        str(BINARIES_DIR),
        "--workpath",
        str(BUILD_DIR),
        "--specpath",
        str(BUILD_DIR),
    ]
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the NL-RPA desktop Agent with PyInstaller.",
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

    run_pyinstaller(args)
    executable = BINARIES_DIR / "desktop-agent.exe"
    if not executable.exists():
        raise FileNotFoundError(
            f"PyInstaller did not create {executable}"
        )

    tauri_path = tauri_sidecar_path(
        executable,
        options.target_triple,
    )
    shutil.copy2(executable, tauri_path)
    print(f"Desktop Agent: {executable}")
    print(f"Tauri sidecar: {tauri_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
