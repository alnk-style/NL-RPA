from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from desktop_agent.actions import ActionResult, register_action
from desktop_agent.actions._win32 import (
    activate_window,
    find_windows,
    foreground_window,
    window_rect,
)

SCREENSHOT_SUFFIX = ".png"


def _safe_file_stem(value: object) -> str:
    return "".join(
        char
        for char in str(value or "")
        if char.isalnum() or char in "-_"
    ) or "step"


def _file_name(value: object, step_index: int) -> str:
    stem = _safe_file_stem(value)
    if stem.lower().endswith(SCREENSHOT_SUFFIX):
        stem = stem[: -len(SCREENSHOT_SUFFIX)]
    return f"{stem}-{step_index}{SCREENSHOT_SUFFIX}"


def _output_path(
    params: dict[str, Any],
    context: dict[str, Any],
) -> tuple[Path, bool]:
    """Return the target path and whether the legacy log directory is used."""

    task_id = context.get("task_id", "unknown")
    step_index = int(context.get("step_index", 0))
    log_dir = Path(str(context.get("log_dir", "logs")))

    raw_path = params.get("path")
    if raw_path is not None and not isinstance(raw_path, str):
        raise ValueError("screenshot path must be a string")

    raw_name = params.get("name")
    if raw_name is not None and not isinstance(raw_name, str):
        raise ValueError("screenshot name must be a string")

    candidate = (raw_path or "").strip()
    if candidate:
        target = Path(candidate).expanduser()
        if target.is_dir() or candidate.endswith(("\\", "/")):
            name = (
                _safe_file_stem(raw_name)
                if raw_name
                else f"step-{step_index}"
            )
            if not name.lower().endswith(SCREENSHOT_SUFFIX):
                name = f"{name}{SCREENSHOT_SUFFIX}"
            return target / name, False
        if not target.suffix:
            target = target.with_name(f"{target.name}{SCREENSHOT_SUFFIX}")
        return target, False

    output_dir = log_dir / f"task-{task_id}"
    return output_dir / _file_name(raw_name, step_index), True


def _window_handle(spec: object, context: dict[str, Any]) -> int:
    if isinstance(spec, str):
        normalized = spec.strip().lower()
        if normalized in {"last", "last_window"}:
            handle = context.get("last_window_handle")
            if handle is None:
                raise ValueError("screenshot window=last 但当前没有已激活的窗口")
            return int(handle)
        if normalized in {"active", "foreground"}:
            handle = foreground_window()
            if handle is None:
                raise ValueError("找不到当前活动窗口")
            return handle
        return _matching_window({"title": spec})

    if isinstance(spec, dict):
        return _matching_window(spec)

    raise ValueError(
        "screenshot window must be an object or a window title string"
    )


def _matching_window(spec: dict[str, Any]) -> int:
    title = spec.get("title")
    class_name = spec.get("class_name")
    pid_value = spec.get("pid")

    if title is not None and not isinstance(title, str):
        raise ValueError("screenshot window title must be a string")
    if class_name is not None and not isinstance(class_name, str):
        raise ValueError("screenshot window class_name must be a string")

    pid: int | None = None
    if pid_value is not None:
        try:
            pid = int(pid_value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "screenshot window pid must be an integer"
            ) from exc

    if pid is None and not title and not class_name:
        raise ValueError(
            "screenshot window requires title, class_name, or pid"
        )

    try:
        index = int(spec.get("index", 0))
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "screenshot window index must be an integer"
        ) from exc
    if index < 0:
        raise ValueError("screenshot window index must not be negative")

    windows = find_windows(
        title=title,
        class_name=class_name,
        pid=pid,
        visible_only=True,
    )
    if index >= len(windows):
        raise ValueError(f"找不到要截图的窗口：{title or class_name or pid}")
    return int(windows[index].handle)


def _region_bbox(region: object) -> tuple[int, int, int, int] | None:
    if region is None:
        return None
    if not isinstance(region, dict):
        raise ValueError("screenshot region must be an object")

    x = int(region["x"])
    y = int(region["y"])
    width = int(region["width"])
    height = int(region["height"])
    if width <= 0 or height <= 0:
        raise ValueError("screenshot region width and height must be positive")
    return x, y, x + width, y + height


async def handle_screenshot(
    params: dict[str, Any],
    context: dict[str, Any],
) -> ActionResult:
    try:
        from PIL import ImageGrab
    except ImportError as exc:
        raise RuntimeError("screenshot requires Pillow") from exc

    output_path, legacy_dir = _output_path(params, context)
    if legacy_dir:
        output_path.parent.mkdir(parents=True, exist_ok=True)
    elif not output_path.parent.is_dir():
        raise ValueError(f"截图保存目录不存在：{output_path.parent}")

    overwrite = params.get("overwrite", True)
    if not isinstance(overwrite, bool):
        raise ValueError("screenshot overwrite must be a boolean")
    if not overwrite and output_path.exists():
        raise ValueError(f"截图文件已存在：{output_path}")

    window_spec = params.get("window")
    region = params.get("region")
    if window_spec is not None and region is not None:
        raise ValueError("screenshot 不能同时指定 window 和 region")

    if window_spec is not None:
        handle = await asyncio.to_thread(_window_handle, window_spec, context)
        left, top, right, bottom = await asyncio.to_thread(
            window_rect,
            handle,
        )
        if right - left <= 0 or bottom - top <= 0:
            await asyncio.to_thread(activate_window, handle, 2.0)
            await asyncio.sleep(0.15)
            left, top, right, bottom = await asyncio.to_thread(
                window_rect,
                handle,
            )
        if right - left <= 0 or bottom - top <= 0:
            raise ValueError("目标窗口尺寸无效，恢复后仍可能已最小化")
        bbox = (left, top, right, bottom)
        image = await asyncio.to_thread(
            ImageGrab.grab,
            bbox=bbox,
            all_screens=True,
        )
    else:
        bbox = _region_bbox(region)
        if bbox is None:
            image = await asyncio.to_thread(ImageGrab.grab)
        else:
            image = await asyncio.to_thread(ImageGrab.grab, bbox=bbox)
    await asyncio.to_thread(image.save, output_path)

    return ActionResult(
        success=True,
        message=f"Screenshot saved: {output_path}",
        screenshot_path=str(output_path),
        data={
            "region": region,
            "window": window_spec,
            "path": str(output_path),
        },
    )


register_action("screenshot", handle_screenshot)

__all__ = ["handle_screenshot"]
