from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings
from app.engine.runner import PlaywrightRunner


def _target_url() -> str:
    parser = argparse.ArgumentParser(
        description=(
            "Open Microsoft Edge with the NL-RPA profile so the "
            "user can sign in once."
        )
    )
    parser.add_argument(
        "url",
        nargs="?",
        default="https://www.bilibili.com",
        help=(
            "URL to open for the one-time login. "
            "Default: https://www.bilibili.com"
        ),
    )
    return parser.parse_args().url


def main() -> int:
    target_url = _target_url()
    settings_path = Path.home() / ".nl-rpa" / "browser-settings.json"
    os.environ.setdefault(
        "NL_RPA_BROWSER_SETTINGS",
        str(settings_path),
    )
    settings = get_settings()
    settings.BROWSER_MODE = "edge"
    runner = PlaywrightRunner(settings)
    executable = runner._resolve_edge_executable()

    if runner.edge_user_data_dir:
        user_data_dir = Path(
            runner.edge_user_data_dir
        ).expanduser()
    else:
        user_data_dir = Path.home() / ".nl-rpa" / "edge-profile"

    profile_directory = runner.edge_profile_directory
    if profile_directory in {"", "auto"}:
        profile_directory = "Default"

    profile_path = user_data_dir / profile_directory
    profile_path.mkdir(parents=True, exist_ok=True)

    print(
        "Opening Microsoft Edge for the one-time login. "
        f"Sign in at {target_url}, then close Edge completely."
    )
    subprocess.Popen(
        [
            str(executable),
            f"--user-data-dir={user_data_dir}",
            f"--profile-directory={profile_directory}",
            "--no-first-run",
            "--no-default-browser-check",
            target_url,
        ],
        close_fds=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
