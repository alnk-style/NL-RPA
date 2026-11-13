from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ActionResult:
    success: bool
    message: str = ""
    pid: int | None = None
    window_handle: int | None = None
    screenshot_path: str | None = None
    text: str | None = None
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        return {
            key: value
            for key, value in payload.items()
            if value is not None
        }


ActionHandler = Callable[
    [dict[str, Any], dict[str, Any]],
    Awaitable[ActionResult],
]

ACTION_REGISTRY: dict[str, ActionHandler] = {}


def register_action(action_type: str, handler: ActionHandler) -> None:
    normalized = action_type.strip()
    if not normalized:
        raise ValueError("action_type must not be empty")
    if not callable(handler):
        raise TypeError("handler must be callable")
    ACTION_REGISTRY[normalized] = handler


def get_action(action_type: str) -> ActionHandler | None:
    return ACTION_REGISTRY.get(action_type)


# Import handlers after the registry declarations so they can register
# themselves without a circular import.
from desktop_agent.actions import (  # noqa: E402, F401
    activate_window,
    click_element,
    click_relative,
    close_app,
    copy_file,
    copy_selected_text,
    create_document,
    create_spreadsheet,
    fetch_kugou_lyrics,
    insert_image,
    launch_app,
    press_hotkey,
    read_text,
    read_window_text,
    screenshot_action,
    type_text,
    wait_action,
)

__all__ = [
    "ACTION_REGISTRY",
    "ActionHandler",
    "ActionResult",
    "get_action",
    "register_action",
]
