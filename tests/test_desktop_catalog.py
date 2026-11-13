from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.agents.desktop_catalog import (  # noqa: E402
    DESKTOP_ACTIONS,
    capabilities_for_actions,
    desktop_action_catalog_text,
)
from desktop_agent.action_validator import (  # noqa: E402
    REGISTERED_ACTION_TYPES,
)
from desktop_agent.actions import ACTION_REGISTRY  # noqa: E402


def test_catalog_only_exposes_implemented_agent_actions() -> None:
    assert set(DESKTOP_ACTIONS) <= set(ACTION_REGISTRY)
    assert set(DESKTOP_ACTIONS) <= REGISTERED_ACTION_TYPES


def test_catalog_covers_every_registered_agent_action() -> None:
    assert set(ACTION_REGISTRY) <= set(DESKTOP_ACTIONS)


def test_capabilities_are_derived_from_actions() -> None:
    assert capabilities_for_actions(["click_element", "read_text"]) == ["uia"]
    assert capabilities_for_actions(["launch_app", "type_text"]) == []
    assert capabilities_for_actions(
        ["activate_window", "screenshot", "click_element"]
    ) == ["screenshot", "uia", "window"]


def test_catalog_text_lists_every_action() -> None:
    text = desktop_action_catalog_text()

    for action in DESKTOP_ACTIONS:
        assert action in text


def test_catalog_exposes_file_clipboard_paste() -> None:
    assert "copy_file_to_clipboard" in DESKTOP_ACTIONS
    assert "path" in DESKTOP_ACTIONS["copy_file_to_clipboard"]["params"]


def test_catalog_exposes_selected_text_capture() -> None:
    assert "copy_selected_text" in DESKTOP_ACTIONS
    assert "keys" in DESKTOP_ACTIONS["copy_selected_text"]["params"]


def test_catalog_exposes_spreadsheet_generation() -> None:
    assert "create_spreadsheet" in DESKTOP_ACTIONS
    assert "columns" in DESKTOP_ACTIONS["create_spreadsheet"]["params"]


def test_catalog_exposes_relative_window_click() -> None:
    assert "click_relative" in DESKTOP_ACTIONS
    assert set(DESKTOP_ACTIONS["click_relative"]["params"]) >= {
        "x",
        "y",
    }


def test_catalog_exposes_window_text_reader() -> None:
    assert "read_window_text" in DESKTOP_ACTIONS
    assert "title" in DESKTOP_ACTIONS["read_window_text"]["params"]


def test_catalog_exposes_fixed_domain_kugou_lyrics_fetch() -> None:
    assert "fetch_kugou_lyrics" in DESKTOP_ACTIONS
    assert "query" in DESKTOP_ACTIONS["fetch_kugou_lyrics"]["params"]
