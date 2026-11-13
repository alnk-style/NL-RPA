from __future__ import annotations

import asyncio
import html
import zipfile
from pathlib import Path
from typing import Any

from app.agents.desktop_planner import validate_task_graph_plan
from app.engine.task_runtime import StepResult, TaskRuntime
from desktop_agent.actions import ACTION_REGISTRY
from desktop_agent.actions import create_document as document_module
from desktop_agent.actions import create_spreadsheet as spreadsheet_module
from desktop_agent.actions import copy_selected_text as copy_module


def build_kugou_lyrics_plan(
    output_dir: Path,
    lyrics: str,
) -> dict[str, Any]:
    document_path = output_dir / "第一推动力.docx"
    spreadsheet_path = output_dir / "第一推动力歌词.xlsx"
    return {
        "executor_type": "hybrid",
        "variables": [
            {"name": "source_lyrics", "default": ""},
            {"name": "lyrics", "default": ""},
            {"name": "lyrics_lines", "default": []},
            {"name": "char_counts", "default": []},
        ],
        "steps": [
            {
                "step_id": 1,
                "executor": "windows",
                "action": "copy_selected_text",
                "result_var": "lyrics",
                "params": {"keys": "^a^c", "settle_seconds": 0},
            },
            {
                "step_id": 2,
                "executor": "transform",
                "action": "split_lines",
                "result_var": "lyrics_lines",
                "params": {"text": lyrics},
            },
            {
                "step_id": 3,
                "executor": "transform",
                "action": "count_chars",
                "result_var": "char_counts",
                "params": {"values": "${lyrics_lines}"},
            },
            {
                "step_id": 4,
                "executor": "files",
                "action": "create_document",
                "params": {
                    "path": str(document_path),
                    "content": "${lyrics}",
                },
                "postconditions": [
                    {
                        "type": "file_non_empty",
                        "path": "${document_path}",
                    }
                ],
            },
            {
                "step_id": 5,
                "executor": "files",
                "action": "create_spreadsheet",
                "params": {
                    "path": str(spreadsheet_path),
                    "sheet_name": "歌词",
                    "columns": [
                        {
                            "header": "歌词",
                            "values": "${lyrics_lines}",
                        },
                        {
                            "header": "字数",
                            "values": "${char_counts}",
                        },
                    ],
                    "column_widths": [80, 12],
                },
                "postconditions": [
                    {
                        "type": "file_non_empty",
                        "path": "${spreadsheet_path}",
                    }
                ],
            },
        ],
        "variables_extra": {
            "document_path": str(document_path),
            "spreadsheet_path": str(spreadsheet_path),
        },
    }


def _minimal_docx(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/'
        'content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxml'
        'formats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/'
        'vnd.openxmlformats-officedocument.wordprocessingml.document.main'
        '+xml"/></Types>'
    )
    relationships = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/'
        '2006/relationships"><Relationship Id="rId1" Type="http://schemas.'
        'openxmlformats.org/officeDocument/2006/relationships/officeDocument"'
        ' Target="word/document.xml"/></Relationships>'
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/'
        'wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>'
        + html.escape(content)
        + "</w:t></w:r></w:p></w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", relationships)
        archive.writestr("word/document.xml", document)


def _minimal_xlsx(path: Path, columns: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    headers = "".join(
        f"<c r=\"{chr(65 + index)}1\" t=\"inlineStr\"><is><t>"
        f"{html.escape(str(column.get('header') or ''))}</t></is></c>"
        for index, column in enumerate(columns)
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/'
        'content-types"><Default Extension="rels" ContentType="application/'
        'vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/'
        'vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType='
        '"application/vnd.openxmlformats-officedocument.spreadsheetml.'
        'worksheet+xml"/></Types>'
    )
    relationships = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/'
        '2006/relationships"><Relationship Id="rId1" Type="http://schemas.'
        'openxmlformats.org/officeDocument/2006/relationships/officeDocument"'
        ' Target="xl/workbook.xml"/></Relationships>'
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/'
        '2006/main"><sheets><sheet name="歌词" sheetId="1" r:id="rId1" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships"/></sheets></workbook>'
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/'
        '2006/relationships"><Relationship Id="rId1" Type="http://schemas.'
        'openxmlformats.org/officeDocument/2006/relationships/worksheet"'
        ' Target="worksheets/sheet1.xml"/></Relationships>'
    )
    worksheet = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/'
        f'2006/main"><sheetData><row r="1">{headers}</row></sheetData>'
        "</worksheet>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", relationships)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/worksheets/sheet1.xml", worksheet)


async def run_fixture_plan(
    plan: dict[str, Any],
    variables: dict[str, Any],
) -> Any:
    async def execute_step(
        step: dict[str, Any],
        state: dict[str, Any],
    ) -> StepResult:
        handler = ACTION_REGISTRY.get(str(step.get("action") or ""))
        if handler is None:
            raise RuntimeError(f"No fixture handler for {step.get('action')}")
        result = await handler(step.get("params") or {}, {})
        return StepResult(
            success=result.success,
            message=result.message,
            text=result.text,
            data=dict(result.data),
        )

    runtime = TaskRuntime(step_executor=execute_step)
    return await runtime.execute(plan, {"variables": variables})


def test_kugou_lyrics_exports_word_and_excel(
    tmp_path: Path,
    monkeypatch,
) -> None:
    lyrics = "第一句\n第二句\n第三句"
    monkeypatch.setattr(copy_module, "_send_keys", lambda _keys: None)
    monkeypatch.setattr(
        copy_module,
        "_read_clipboard_text",
        lambda: lyrics,
    )
    monkeypatch.setattr(
        document_module,
        "_create_document",
        lambda path, content, _progid, _overwrite: _minimal_docx(
            Path(path),
            content,
        ),
    )
    monkeypatch.setattr(
        spreadsheet_module,
        "_create_spreadsheet",
        lambda path, _sheet, columns, _widths, _overwrite: _minimal_xlsx(
            Path(path),
            columns,
        ),
    )
    plan = build_kugou_lyrics_plan(tmp_path, "${lyrics}")
    variables = {
        "lyrics": "",
        **plan.pop("variables_extra"),
    }
    validated = validate_task_graph_plan(
        plan,
        allowed_doc_dirs=(str(tmp_path),),
    )

    for _ in range(3):
        result = asyncio.run(run_fixture_plan(validated, variables))
        assert result.success is True, result.error
        assert result.variables["lyrics_lines"] == [
            "第一句",
            "第二句",
            "第三句",
        ]
        assert result.variables["char_counts"] == [3, 3, 3]
        with zipfile.ZipFile(tmp_path / "第一推动力.docx") as archive:
            assert "word/document.xml" in archive.namelist()
        with zipfile.ZipFile(tmp_path / "第一推动力歌词.xlsx") as archive:
            assert "xl/worksheets/sheet1.xml" in archive.namelist()
