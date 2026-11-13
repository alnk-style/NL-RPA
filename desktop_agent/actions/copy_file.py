from __future__ import annotations

import asyncio
import io
import struct
from pathlib import Path
from typing import Any

from desktop_agent.actions import ActionResult, register_action

IMAGE_SUFFIXES = {
    ".bmp",
    ".gif",
    ".jpeg",
    ".jpg",
    ".png",
    ".tif",
    ".tiff",
    ".webp",
}


async def handle_copy_file_to_clipboard(
    params: dict[str, Any],
    context: dict[str, Any],
) -> ActionResult:
    raw_path = params.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError("copy_file_to_clipboard requires a non-empty path")

    path = Path(raw_path.strip()).expanduser().resolve(strict=False)
    if not path.is_file():
        raise ValueError(f"文件不存在：{path}")

    await asyncio.to_thread(_copy_file_to_clipboard, path)
    return ActionResult(
        success=True,
        message=f"File copied to clipboard: {path}",
        window_handle=context.get("last_window_handle"),
        data={"path": str(path)},
    )


def _copy_file_to_clipboard(path: Path) -> None:
    if path.suffix.casefold() in IMAGE_SUFFIXES:
        _copy_image_to_clipboard(path)
        return
    _copy_file_drop_to_clipboard(path)


def _copy_image_to_clipboard(path: Path) -> None:
    import win32clipboard
    import win32con

    payload = _image_dib_payload(path)
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_DIB, payload)
    finally:
        win32clipboard.CloseClipboard()


def _image_dib_payload(path: Path) -> bytes:
    from PIL import Image

    with Image.open(path) as image:
        output = io.BytesIO()
        image.convert("RGB").save(output, format="BMP")
    return output.getvalue()[14:]


def _copy_file_drop_to_clipboard(path: Path) -> None:
    import win32clipboard
    import win32con

    # CF_HDROP with one wide-character path behaves like copying a file
    # from Explorer, so target applications can paste it natively.
    dropfiles = struct.pack("<IiiII", 20, 0, 0, 0, 1)
    payload = dropfiles + f"{path}\0\0".encode("utf-16le")

    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_HDROP, payload)
    finally:
        win32clipboard.CloseClipboard()


register_action("copy_file_to_clipboard", handle_copy_file_to_clipboard)

__all__ = ["handle_copy_file_to_clipboard"]
