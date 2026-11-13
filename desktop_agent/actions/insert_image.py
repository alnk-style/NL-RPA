from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from desktop_agent.actions import ActionResult, register_action


async def handle_insert_image(
    params: dict[str, Any],
    context: dict[str, Any],
) -> ActionResult:
    raw_path = params.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError("insert_image requires a non-empty path")

    raw_image = params.get("image_path")
    if not isinstance(raw_image, str) or not raw_image.strip():
        raise ValueError("insert_image requires a non-empty image_path")

    path = Path(raw_path.strip()).expanduser()
    image_path = Path(raw_image.strip()).expanduser()
    if not image_path.is_file():
        raise ValueError(f"图片文件不存在：{image_path}")

    progid = str(params.get("progid", "kwps.Application")).strip()
    if not progid:
        raise ValueError("insert_image requires a non-empty progid")

    position = str(params.get("position", "end")).strip().lower() or "end"
    if position not in {"end", "cursor"}:
        raise ValueError("insert_image position must be end or cursor")

    save = params.get("save", True)
    if not isinstance(save, bool):
        raise ValueError("insert_image save must be a boolean")

    await asyncio.to_thread(
        _insert_image,
        path,
        image_path,
        progid,
        position,
        save,
    )
    return ActionResult(
        success=True,
        message=f"Image inserted: {image_path} -> {path}",
        data={
            "path": str(path),
            "image_path": str(image_path),
            "position": position,
            "progid": progid,
        },
    )


def _insert_image(
    path: Path,
    image_path: Path,
    progid: str,
    position: str,
    save: bool,
) -> None:
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    application = None
    document = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)

        application = win32com.client.Dispatch(progid)
        application.Visible = False
        application.DisplayAlerts = 0
        if path.exists():
            document = application.Documents.Open(str(path))
        else:
            document = application.Documents.Add()

        shapes = document.InlineShapes
        if position == "cursor":
            shapes.AddPicture(str(image_path), False, True)
        else:
            # wdCollapseEnd = 0，把图片插到文档末尾
            target = document.Content
            target.Collapse(0)
            shapes.AddPicture(str(image_path), False, True, target)

        if save:
            document.Save()
    finally:
        if document is not None:
            try:
                document.Close(0)
            except Exception:
                pass
        try:
            if application is not None:
                application.Quit()
        except Exception:
            pass
        pythoncom.CoUninitialize()


register_action("insert_image", handle_insert_image)

__all__ = ["handle_insert_image"]
