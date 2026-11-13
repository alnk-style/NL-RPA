from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DESKTOP_DIR = ROOT / "desktop"
BUNDLE_DIR = DESKTOP_DIR / "src-tauri" / "target" / "release" / "bundle"
DEFAULT_GITHUB_MIRROR = "https://ghproxy.net"


@dataclass(frozen=True)
class BuildStep:
    label: str
    cwd: Path
    command: list[str]


def npm_command() -> str:
    if os.name == "nt":
        command = shutil.which("npm.cmd")
    else:
        command = shutil.which("npm")
    if command is None:
        raise SystemExit("npm is required but was not found on PATH.")
    return command


def build_plan() -> list[BuildStep]:
    npm = npm_command()
    return [
        BuildStep(
            label="Install backend dependencies",
            cwd=ROOT,
            command=[
                sys.executable,
                "-m",
                "pip",
                "install",
                "-r",
                str(ROOT / "requirements.txt"),
            ],
        ),
        BuildStep(
            label="Install desktop Agent dependencies",
            cwd=ROOT,
            command=[
                sys.executable,
                "-m",
                "pip",
                "install",
                "-r",
                str(ROOT / "desktop_agent" / "requirements.txt"),
            ],
        ),
        BuildStep(
            label="Build backend sidecar",
            cwd=ROOT,
            command=[
                sys.executable,
                str(ROOT / "scripts" / "build_backend.py"),
            ],
        ),
        BuildStep(
            label="Build desktop Agent sidecar",
            cwd=ROOT,
            command=[
                sys.executable,
                str(ROOT / "scripts" / "build_agent.py"),
            ],
        ),
        BuildStep(
            label="Install desktop dependencies",
            cwd=DESKTOP_DIR,
            command=[npm, "install"],
        ),
        BuildStep(
            label="Build Tauri installer",
            cwd=DESKTOP_DIR,
            command=[npm, "run", "tauri", "build"],
        ),
    ]


def build_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.setdefault(
        "TAURI_BUNDLER_TOOLS_GITHUB_MIRROR",
        DEFAULT_GITHUB_MIRROR,
    )
    return environment


def run_plan(plan: list[BuildStep]) -> None:
    environment = build_environment()
    for index, step in enumerate(plan, start=1):
        print(f"\n[{index}/{len(plan)}] {step.label}")
        subprocess.run(
            step.command,
            cwd=step.cwd,
            check=True,
            env=environment,
        )


def main() -> int:
    run_plan(build_plan())
    print(f"\nInstaller output directory: {BUNDLE_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
