from __future__ import annotations

import asyncio
from typing import Any

from desktop_agent.actions import ActionResult, register_action
from desktop_agent.actions._uia import (
    describe_candidates,
    element_text,
    find_element,
)
from desktop_agent.uia_observation import candidate_labels


async def handle_read_text(
    params: dict[str, Any],
    context: dict[str, Any],
) -> ActionResult:
    automation_id = _optional_string(params.get("automation_id"))
    name = _optional_string(params.get("name"))
    control_type = _optional_string(params.get("control_type"))
    if not any((automation_id, name, control_type)):
        raise ValueError(
            "read_text requires automation_id, name, or control_type"
        )

    element = await asyncio.to_thread(
        find_element,
        context,
        automation_id=automation_id,
        name=name,
        control_type=control_type,
    )
    if element is None:
        candidates = await asyncio.to_thread(
            describe_candidates,
            context,
            name=name,
            automation_id=automation_id,
            control_type=control_type,
        )
        labels = candidate_labels(candidates)
        message = "No matching UI Automation element found"
        if labels:
            message = (
                "No matching UI Automation element；"
                "当前窗口可用控件："
                + "、".join(labels)
            )
        data: dict[str, Any] = {"candidates": candidates}
        if labels:
            data["candidate_labels"] = labels
        last_locator = context.get("last_locator")
        if isinstance(last_locator, dict):
            data["last_locator"] = dict(last_locator)
        return ActionResult(
            success=False,
            message=message,
            data=data,
        )

    text = await asyncio.to_thread(element_text, element)
    data = {}
    last_locator = context.get("last_locator")
    if isinstance(last_locator, dict):
        data["last_locator"] = dict(last_locator)
    return ActionResult(
        success=True,
        message="Text read",
        text=text,
        window_handle=context.get("last_window_handle"),
        data=data,
    )


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


register_action("read_text", handle_read_text)

__all__ = ["handle_read_text"]
