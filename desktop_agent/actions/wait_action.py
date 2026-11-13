from __future__ import annotations

import asyncio
import time
from typing import Any

from desktop_agent.actions import ActionResult, register_action
from desktop_agent.actions._uia import describe_candidates, find_element
from desktop_agent.actions._win32 import (
    describe_window_candidates,
    find_windows,
)
from desktop_agent.uia_observation import candidate_labels


async def handle_wait(
    params: dict[str, Any],
    context: dict[str, Any],
) -> ActionResult:
    try:
        seconds = float(params.get("seconds", 1))
    except (TypeError, ValueError) as exc:
        raise ValueError("wait seconds must be a number") from exc
    if seconds < 0:
        raise ValueError("wait seconds must not be negative")

    condition = params.get("condition")
    if condition is None:
        await asyncio.sleep(seconds)
        return ActionResult(
            success=True,
            message=f"Waited {seconds:g} seconds",
        )

    condition = str(condition)
    if condition not in {
        "window_appears",
        "window_disappears",
        "element_appears",
    }:
        raise ValueError(f"Unsupported wait condition: {condition}")

    deadline = time.monotonic() + seconds
    while time.monotonic() <= deadline:
        if await _condition_met(condition, params, context):
            return ActionResult(
                success=True,
                message=f"Condition met: {condition}",
            )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        await asyncio.sleep(min(0.1, remaining))

    details: dict[str, Any] = {}
    if condition == "element_appears":
        automation_id = _optional_string(params.get("automation_id"))
        name = _optional_string(params.get("name"))
        control_type = _optional_string(params.get("control_type"))
        if any((automation_id, name, control_type)):
            candidates = await asyncio.to_thread(
                describe_candidates,
                context,
                name=name,
                automation_id=automation_id,
                control_type=control_type,
            )
            _attach_candidates(details, candidates)
    elif condition == "window_appears":
        candidates = await asyncio.to_thread(
            describe_window_candidates,
            limit=8,
        )
        _attach_candidates(details, candidates)

    _attach_last_locator(details, context)
    message = f"Timed out waiting for {condition}"
    labels = details.get("candidate_labels")
    if labels:
        message += "；当前可用候选：" + "、".join(labels)
    return ActionResult(
        success=False,
        message=message,
        data=details,
    )


async def _condition_met(
    condition: str,
    params: dict[str, Any],
    context: dict[str, Any],
) -> bool:
    if condition in {"window_appears", "window_disappears"}:
        title = params.get("title")
        if not isinstance(title, str) or not title:
            raise ValueError(f"{condition} requires title")
        windows = await asyncio.to_thread(find_windows, title=title)
        exists = bool(windows)
        if condition == "window_appears":
            return exists
        return not exists

    automation_id = _optional_string(params.get("automation_id"))
    name = _optional_string(params.get("name"))
    control_type = _optional_string(params.get("control_type"))
    if not any((automation_id, name, control_type)):
        raise ValueError(
            "element_appears requires automation_id, name, or control_type"
        )
    element = await asyncio.to_thread(
        find_element,
        context,
        automation_id=automation_id,
        name=name,
        control_type=control_type,
    )
    return element is not None


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _attach_candidates(
    details: dict[str, Any],
    candidates: list[Any],
) -> None:
    if not candidates:
        return
    details["candidates"] = candidates
    labels = candidate_labels(candidates)
    if labels:
        details["candidate_labels"] = labels


def _attach_last_locator(
    details: dict[str, Any],
    context: dict[str, Any],
) -> None:
    last_locator = context.get("last_locator")
    if isinstance(last_locator, dict):
        details["last_locator"] = dict(last_locator)


register_action("wait", handle_wait)

__all__ = ["handle_wait"]
