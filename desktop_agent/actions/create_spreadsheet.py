"""Create an Excel-compatible spreadsheet through WPS/Office COM."""

from __future__ import annotations

import asyncio
import contextvars
from pathlib import Path
from typing import Any

from desktop_agent.actions import ActionResult, register_action


_PROGID = contextvars.ContextVar(
    "create_spreadsheet_progid",
    default="ket.Application",
)


async def handle_create_spreadsheet(
    params: dict[str, Any],
    context: dict[str, Any],
) -> ActionResult:
    del context
    raw_path = params.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError("create_spreadsheet requires a non-empty path")
    path = Path(raw_path.strip()).expanduser()
    sheet_name = str(params.get("sheet_name") or "").strip()
    if not sheet_name:
        raise ValueError("create_spreadsheet requires sheet_name")
    columns = params.get("columns")
    if not isinstance(columns, list) or not columns:
        raise ValueError("create_spreadsheet requires columns")
    normalized_columns = [
        dict(column)
        for column in columns
        if isinstance(column, dict)
    ]
    if len(normalized_columns) != len(columns):
        raise ValueError("create_spreadsheet columns must be objects")
    widths = params.get("column_widths")
    if not isinstance(widths, list):
        widths = []
    progid = str(
        params.get("progid") or "ket.Application"
    ).strip() or "ket.Application"
    overwrite = bool(params.get("overwrite", True))

    token = _PROGID.set(progid)
    try:
        await asyncio.to_thread(
            _create_spreadsheet,
            path,
            sheet_name,
            normalized_columns,
            widths,
            overwrite,
        )
    finally:
        _PROGID.reset(token)
    return ActionResult(
        success=True,
        message=f"Spreadsheet created: {path}",
        data={
            "path": str(path),
            "sheet_name": sheet_name,
            "column_count": len(normalized_columns),
            "progid": progid,
        },
    )


def _create_spreadsheet(
    path: Path,
    sheet_name: str,
    columns: list[dict[str, Any]],
    widths: list[Any],
    overwrite: bool,
) -> None:
    import pythoncom
    import win32com.client

    if path.exists() and not overwrite:
        raise ValueError(f"Spreadsheet already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()

    pythoncom.CoInitialize()
    application = None
    workbook = None
    try:
        application = win32com.client.Dispatch(_PROGID.get())
        application.Visible = False
        application.DisplayAlerts = 0
        workbook = application.Workbooks.Add()
        sheet = workbook.Worksheets.Item(1)
        sheet.Name = sheet_name

        max_rows = max(
            (
                len(column.get("values") or [])
                for column in columns
                if isinstance(column.get("values"), list)
            ),
            default=0,
        )
        for column_index, column in enumerate(columns, start=1):
            header = str(column.get("header") or "").strip()
            if not header:
                raise ValueError("spreadsheet column header is required")
            sheet.Cells(1, column_index).Value = header
            values = column.get("values")
            if not isinstance(values, list):
                values = []
            for row_index, value in enumerate(values, start=2):
                sheet.Cells(row_index, column_index).Value = value

            formula = column.get("formula")
            if isinstance(formula, str) and formula.strip():
                for row_index in range(2, max_rows + 2):
                    sheet.Cells(
                        row_index,
                        column_index,
                    ).Formula = formula.format(row=row_index)

        for index, width in enumerate(widths, start=1):
            try:
                sheet.Columns(index).ColumnWidth = float(width)
            except (TypeError, ValueError):
                continue
        workbook.SaveAs(str(path))
    finally:
        if workbook is not None:
            try:
                workbook.Close(False)
            except Exception:
                pass
        if application is not None:
            try:
                application.Quit()
            except Exception:
                pass
        pythoncom.CoUninitialize()


register_action("create_spreadsheet", handle_create_spreadsheet)

__all__ = ["handle_create_spreadsheet"]
