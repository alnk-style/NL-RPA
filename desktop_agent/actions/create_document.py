from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from desktop_agent.actions import ActionResult, register_action


async def handle_create_document(
    params: dict[str, Any],
    context: dict[str, Any],
) -> ActionResult:
    raw_path = params.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError("create_document requires a non-empty path")

    path = Path(raw_path.strip()).expanduser()
    content = str(params.get("content", ""))
    progid = str(params.get("progid", "kwps.Application")).strip()
    overwrite = bool(params.get("overwrite", True))
    if not progid:
        raise ValueError("create_document requires a non-empty progid")

    await asyncio.to_thread(
        _create_document,
        path,
        content,
        progid,
        overwrite,
    )
    return ActionResult(
        success=True,
        message=f"Document created: {path}",
        data={
            "path": str(path),
            "content_length": len(content),
            "progid": progid,
        },
    )


def _create_document(
    path: Path,
    content: str,
    progid: str,
    overwrite: bool,
) -> None:
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    application = None
    document = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if overwrite and path.exists():
            path.unlink()

        application = win32com.client.Dispatch(progid)
        application.Visible = False
        application.DisplayAlerts = 0
        document = application.Documents.Add()
        document.Content.Text = content
        document.SaveAs(str(path))
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


register_action("create_document", handle_create_document)

__all__ = ["handle_create_document"]
