from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from desktop_agent.action_validator import ActionValidator
from desktop_agent.actions import ACTION_REGISTRY
from desktop_agent.actions import create_document as create_document_module
from desktop_agent.actions import insert_image as insert_image_module
from desktop_agent.config import AgentConfig


def test_create_document_action_is_registered_and_allowlisted(
    tmp_path: Path,
) -> None:
    assert "create_document" in ACTION_REGISTRY

    document_path = tmp_path / "report.docx"
    result = ActionValidator.validate(
        {
            "type": "create_document",
            "params": {"path": str(document_path)},
        },
        AgentConfig(
            server_url="http://127.0.0.1:8000",
            agent_id=1,
            token="token",
            allowed_doc_dirs=[str(tmp_path)],
        ),
    )

    assert result.allowed is True


def test_create_document_handler_calls_wps_writer(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[tuple[Path, str, str, bool]] = []

    def fake_create(
        path: Path,
        content: str,
        progid: str,
        overwrite: bool,
    ) -> None:
        calls.append((path, content, progid, overwrite))

    monkeypatch.setattr(
        create_document_module,
        "_create_document",
        fake_create,
    )
    document_path = tmp_path / "report.docx"
    result = asyncio.run(
        create_document_module.handle_create_document(
            {
                "path": str(document_path),
                "content": "hello",
            },
            {},
        )
    )

    assert result.success is True
    assert calls == [
        (document_path, "hello", "kwps.Application", True),
    ]


def test_create_document_initializes_com_on_worker_thread(
    tmp_path: Path,
    monkeypatch,
) -> None:
    events: list[str] = []
    saved_paths: list[str] = []

    class FakeDocument:
        def __init__(self) -> None:
            self.Content = SimpleNamespace(Text="")

        def SaveAs(self, path: str) -> None:
            saved_paths.append(path)

        def Close(self, _mode: int) -> None:
            pass

    class FakeApplication:
        def __init__(self) -> None:
            self.Visible = True
            self.DisplayAlerts = 1
            self.Documents = SimpleNamespace(Add=lambda: FakeDocument())

        def Quit(self) -> None:
            pass

    application = FakeApplication()
    fake_pythoncom = SimpleNamespace(
        CoInitialize=lambda: events.append("initialize"),
        CoUninitialize=lambda: events.append("uninitialize"),
    )
    fake_client = SimpleNamespace(
        Dispatch=lambda _progid: application,
    )
    fake_win32com = SimpleNamespace(client=fake_client)
    monkeypatch.setitem(sys.modules, "pythoncom", fake_pythoncom)
    monkeypatch.setitem(sys.modules, "win32com", fake_win32com)
    monkeypatch.setitem(sys.modules, "win32com.client", fake_client)

    document_path = tmp_path / "report.docx"
    create_document_module._create_document(
        document_path,
        "hello",
        "kwps.Application",
        False,
    )

    assert events == ["initialize", "uninitialize"]
    assert saved_paths == [str(document_path)]


def _agent_config(tmp_path: Path) -> AgentConfig:
    return AgentConfig(
        server_url="http://127.0.0.1:8000",
        agent_id=1,
        token="token",
        allowed_doc_dirs=[str(tmp_path)],
    )


def test_create_spreadsheet_writes_rows_and_formulas(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from desktop_agent.actions import create_spreadsheet as module

    captured: dict[str, object] = {}

    def fake_write(path, sheet_name, columns, widths, overwrite):
        captured.update(
            {
                "path": path,
                "sheet_name": sheet_name,
                "columns": columns,
                "widths": widths,
                "overwrite": overwrite,
            }
        )

    monkeypatch.setattr(module, "_create_spreadsheet", fake_write)
    result = asyncio.run(
        module.handle_create_spreadsheet(
            {
                "path": str(tmp_path / "歌词.xlsx"),
                "sheet_name": "歌词",
                "columns": [
                    {"header": "歌词", "values": ["第一句", "第二句"]},
                    {"header": "字数", "formula": "=LEN(A{row})"},
                ],
                "column_widths": [80, 12],
            },
            {},
        )
    )

    assert result.success is True
    assert captured["sheet_name"] == "歌词"
    assert captured["columns"][1]["formula"] == "=LEN(A{row})"


def test_spreadsheet_writer_populates_cells_without_real_office(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from desktop_agent.actions import create_spreadsheet as module

    events: list[str] = []
    cells: dict[tuple[int, int], dict[str, object]] = {}

    class FakeCell:
        def __init__(self, row: int, column: int) -> None:
            self.record = cells.setdefault((row, column), {})

        @property
        def Value(self):
            return self.record.get("value")

        @Value.setter
        def Value(self, value):
            self.record["value"] = value

        @property
        def Formula(self):
            return self.record.get("formula")

        @Formula.setter
        def Formula(self, value):
            self.record["formula"] = value

    class FakeColumns:
        def __init__(self) -> None:
            self.widths: dict[int, float] = {}

        def __call__(self, index: int):
            columns = self

            class Column:
                @property
                def ColumnWidth(self):
                    return columns.widths[index]

                @ColumnWidth.setter
                def ColumnWidth(self, value):
                    columns.widths[index] = value

            return Column()

    class FakeSheet:
        def __init__(self) -> None:
            self.Name = ""
            self.Columns = FakeColumns()

        def Cells(self, row: int, column: int) -> FakeCell:
            return FakeCell(row, column)

    class FakeWorkbook:
        def __init__(self) -> None:
            self.sheet = FakeSheet()
            self.Worksheets = SimpleNamespace(
                Item=lambda _index: self.sheet
            )

        def SaveAs(self, path: str) -> None:
            events.append(f"save:{path}")
            Path(path).write_bytes(b"xlsx")

        def Close(self, _save: bool) -> None:
            events.append("close")

    class FakeApplication:
        def __init__(self) -> None:
            self.Visible = True
            self.DisplayAlerts = 1
            self.workbook = FakeWorkbook()
            self.Workbooks = SimpleNamespace(Add=lambda: self.workbook)

        def Quit(self) -> None:
            events.append("quit")

    application = FakeApplication()
    fake_pythoncom = SimpleNamespace(
        CoInitialize=lambda: events.append("initialize"),
        CoUninitialize=lambda: events.append("uninitialize"),
    )
    fake_client = SimpleNamespace(Dispatch=lambda _progid: application)
    monkeypatch.setitem(sys.modules, "pythoncom", fake_pythoncom)
    monkeypatch.setitem(
        sys.modules,
        "win32com",
        SimpleNamespace(client=fake_client),
    )
    monkeypatch.setitem(sys.modules, "win32com.client", fake_client)
    target = tmp_path / "歌词.xlsx"

    module._create_spreadsheet(
        target,
        "歌词",
        [
            {"header": "歌词", "values": ["第一句", "第二句"]},
            {"header": "字数", "formula": "=LEN(A{row})"},
        ],
        [80, 12],
        True,
    )

    assert cells[(1, 1)]["value"] == "歌词"
    assert cells[(2, 1)]["value"] == "第一句"
    assert cells[(3, 1)]["value"] == "第二句"
    assert cells[(2, 2)]["formula"] == "=LEN(A2)"
    assert cells[(3, 2)]["formula"] == "=LEN(A3)"
    assert application.workbook.sheet.Columns.widths == {1: 80, 2: 12}
    assert events[0] == "initialize"
    assert events[-1] == "uninitialize"


@pytest.mark.parametrize(
    ("params", "message"),
    [
        (
            {
                "path": "lyrics.csv",
                "sheet_name": "歌词",
                "columns": [{"header": "歌词", "values": ["a"]}],
            },
            ".xlsx",
        ),
        (
            {
                "path": "lyrics.xlsx",
                "sheet_name": "",
                "columns": [{"header": "歌词", "values": ["a"]}],
            },
            "sheet_name",
        ),
        (
            {
                "path": "lyrics.xlsx",
                "sheet_name": "歌词",
                "columns": [],
            },
            "columns",
        ),
    ],
)
def test_validate_create_spreadsheet_rejects_invalid_params(
    tmp_path: Path,
    params: dict,
    message: str,
) -> None:
    params["path"] = str(tmp_path / str(params["path"]))
    result = ActionValidator.validate(
        {"type": "create_spreadsheet", "params": params},
        _agent_config(tmp_path),
    )

    assert result.allowed is False
    assert message in (result.reason or "")


def test_create_spreadsheet_rejects_path_outside_allowlist(
    tmp_path: Path,
) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()

    result = ActionValidator.validate(
        {
            "type": "create_spreadsheet",
            "params": {
                "path": str(outside / "lyrics.xlsx"),
                "sheet_name": "歌词",
                "columns": [{"header": "歌词", "values": ["a"]}],
            },
        },
        AgentConfig(
            server_url="http://127.0.0.1:8000",
            agent_id=1,
            token="token",
            allowed_doc_dirs=[str(allowed)],
        ),
    )

    assert result.allowed is False
    assert "outside allowlist" in (result.reason or "")


def test_insert_image_action_is_registered_and_allowlisted(
    tmp_path: Path,
) -> None:
    assert "insert_image" in ACTION_REGISTRY

    image = tmp_path / "shot.png"
    image.write_bytes(b"png")

    result = ActionValidator.validate(
        {
            "type": "insert_image",
            "params": {
                "path": str(tmp_path / "report.docx"),
                "image_path": str(image),
            },
        },
        _agent_config(tmp_path),
    )

    assert result.allowed is True


def test_validate_insert_image_blocks_missing_image(
    tmp_path: Path,
) -> None:
    result = ActionValidator.validate(
        {
            "type": "insert_image",
            "params": {
                "path": str(tmp_path / "report.docx"),
                "image_path": str(tmp_path / "missing.png"),
            },
        },
        _agent_config(tmp_path),
    )

    assert result.allowed is False
    assert result.reason is not None
    assert "不存在" in result.reason


def test_insert_image_handler_calls_wps_writer(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[tuple[Path, Path, str, str, bool]] = []

    def fake_insert(
        path: Path,
        image_path: Path,
        progid: str,
        position: str,
        save: bool,
    ) -> None:
        calls.append((path, image_path, progid, position, save))

    monkeypatch.setattr(
        insert_image_module,
        "_insert_image",
        fake_insert,
    )
    image = tmp_path / "shot.png"
    image.write_bytes(b"png")
    document = tmp_path / "report.docx"

    result = asyncio.run(
        insert_image_module.handle_insert_image(
            {
                "path": str(document),
                "image_path": str(image),
            },
            {},
        )
    )

    assert result.success is True
    assert calls == [
        (document, image, "kwps.Application", "end", True),
    ]


def test_insert_image_requires_existing_image(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="不存在"):
        asyncio.run(
            insert_image_module.handle_insert_image(
                {
                    "path": str(tmp_path / "report.docx"),
                    "image_path": str(tmp_path / "missing.png"),
                },
                {},
            )
        )


def test_insert_image_appends_picture_and_saves(
    tmp_path: Path,
    monkeypatch,
) -> None:
    events: list[str] = []
    calls: list[tuple] = []

    class FakeRange:
        def Collapse(self, direction: int) -> None:
            calls.append(("collapse", direction))

    class FakeShapes:
        def AddPicture(self, *args) -> None:
            calls.append(("picture", *args))

    class FakeDocument:
        def __init__(self) -> None:
            self.Content = FakeRange()
            self.InlineShapes = FakeShapes()

        def Save(self) -> None:
            calls.append(("save",))

        def Close(self, _mode: int) -> None:
            calls.append(("close",))

    class FakeApplication:
        def __init__(self) -> None:
            self.Visible = True
            self.DisplayAlerts = 1
            self.Documents = SimpleNamespace(
                Open=lambda _path: FakeDocument(),
                Add=FakeDocument,
            )

        def Quit(self) -> None:
            pass

    application = FakeApplication()
    fake_pythoncom = SimpleNamespace(
        CoInitialize=lambda: events.append("initialize"),
        CoUninitialize=lambda: events.append("uninitialize"),
    )
    fake_client = SimpleNamespace(Dispatch=lambda _progid: application)
    monkeypatch.setitem(sys.modules, "pythoncom", fake_pythoncom)
    monkeypatch.setitem(sys.modules, "win32com", SimpleNamespace(client=fake_client))
    monkeypatch.setitem(sys.modules, "win32com.client", fake_client)

    document = tmp_path / "report.docx"
    document.write_bytes(b"docx")
    image = tmp_path / "shot.png"
    image.write_bytes(b"png")

    insert_image_module._insert_image(
        document,
        image,
        "kwps.Application",
        "end",
        True,
    )

    assert events == ["initialize", "uninitialize"]
    assert calls[0] == ("collapse", 0)
    assert calls[1][:4] == ("picture", str(image), False, True)
    assert isinstance(calls[1][4], FakeRange)
    assert calls[2] == ("save",)
