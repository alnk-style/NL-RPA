from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


def test_merge_installed_apps_deduplicates_and_prefers_shortcut_names(
    tmp_path: Path,
) -> None:
    from app.engine.installed_apps import (
        AppCandidate,
        app_key_for_path,
        merge_installed_app_candidates,
    )

    first = tmp_path / "WPS Office" / "ksolaunch.exe"
    second = tmp_path / "KuGou.exe"
    first.parent.mkdir()
    first.write_bytes(b"MZ")
    second.write_bytes(b"MZ")

    apps = merge_installed_app_candidates(
        [
            AppCandidate(
                name="WPS Office",
                executable_path=second,
                source="start_menu",
            ),
            AppCandidate(
                name="WPS",
                executable_path=str(first).upper(),
                source="uninstall_registry",
            ),
            AppCandidate(
                name="WPS Office",
                executable_path=first,
                source="start_menu",
            ),
        ]
    )

    by_name = {app.name: app for app in apps}
    assert set(by_name) == {"WPS Office"}
    app = by_name["WPS Office"]
    assert Path(app.executable_path) == first
    assert app.key == app_key_for_path(first)
    assert len(app.key) == 64


def test_merge_rejects_non_executable_and_missing_paths(
    tmp_path: Path,
) -> None:
    from app.engine.installed_apps import (
        AppCandidate,
        merge_installed_app_candidates,
    )

    text_file = tmp_path / "readme.txt"
    text_file.write_text("not an app", encoding="utf-8")
    missing = tmp_path / "missing.exe"

    assert merge_installed_app_candidates(
        [
            AppCandidate("Text", text_file, "start_menu"),
            AppCandidate("Missing", missing, "app_paths"),
        ]
    ) == []


@pytest.mark.skipif(os.name != "nt", reason="Windows icon extraction")
def test_render_app_icon_returns_png() -> None:
    from app.engine.installed_apps import render_app_icon_png

    png = render_app_icon_png(sys.executable)

    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(png) > 100
