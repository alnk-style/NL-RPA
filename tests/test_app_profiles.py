from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.engine.app_profiles import (
    ProfileValidationError,
    load_app_profiles,
    resolve_profile,
    validate_profile,
)


def _profile() -> dict:
    return {
        "schema_version": 1,
        "app_id": "kugou",
        "display_name": "酷狗音乐",
        "executable_path": r"D:\KGMusic\KuGou.exe",
        "window": {"title": "酷狗音乐", "class_name": "kugou_ui"},
        "recipes": {
            "search_song": {
                "verified": False,
                "steps": [
                    {"action": "click_relative", "x": 0.78, "y": 0.06},
                    {
                        "action": "type_text",
                        "text": "${query}",
                        "method": "clipboard",
                    },
                    {"action": "press_hotkey", "keys": "{ENTER}"},
                ],
            }
        },
    }


def test_resolve_profile_by_display_name() -> None:
    loaded = resolve_profile("打开酷狗音乐", [_profile()])

    assert loaded is not None
    assert loaded["app_id"] == "kugou"


def test_load_app_profiles_reads_json_directory(
    tmp_path: Path,
) -> None:
    target = tmp_path / "kugou.json"
    target.write_text(
        json.dumps(_profile(), ensure_ascii=False),
        encoding="utf-8",
    )

    profiles = load_app_profiles(tmp_path)

    assert [profile["app_id"] for profile in profiles] == ["kugou"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", 2),
        ("window", None),
        ("recipes", []),
        ("executable_path", ""),
    ],
)
def test_validate_profile_rejects_required_field_errors(
    field: str,
    value: object,
) -> None:
    profile = _profile()
    profile[field] = value

    with pytest.raises(ProfileValidationError):
        validate_profile(profile)


def test_validate_profile_rejects_relative_coordinate_outside_range() -> None:
    profile = _profile()
    profile["recipes"]["search_song"]["steps"][0]["x"] = 1.2

    with pytest.raises(ProfileValidationError, match="0..1"):
        validate_profile(profile)


def test_profile_requires_confirmation_for_unverified_recipe() -> None:
    from desktop_agent.action_validator import ActionValidator

    result = ActionValidator.validate_profile_recipe(
        _profile(),
        "search_song",
        manual_confirmation=False,
    )

    assert result.allowed is False
    assert "verified" in (result.reason or "")


def test_builtin_kugou_profile_is_loadable() -> None:
    root = Path("desktop_agent") / "profiles"
    profiles = load_app_profiles(root)
    kugou = next(
        profile for profile in profiles if profile["app_id"] == "kugou"
    )

    assert kugou["display_name"] == "酷狗音乐"
    assert kugou["recipes"]
    search_actions = [
        step["action"]
        for step in kugou["recipes"]["search_song"]["steps"]
    ]
    lyrics_actions = [
        step["action"]
        for step in kugou["recipes"]["open_lyrics"]["steps"]
    ]
    assert "click_element" in search_actions
    assert "click_element" in lyrics_actions
    assert "click_relative" not in search_actions
