from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile

from app.actions.files import write_xlsx


def test_xlsx_generated_by_runtime_has_parseable_xml(
    tmp_path: Path,
) -> None:
    target = tmp_path / "sample.xlsx"
    write_xlsx(
        target,
        rows=[{"name": "A", "amount": 1}],
        headers=["name", "amount"],
        sheet_name="Data",
    )
    with ZipFile(target) as archive:
        for name in (
            "[Content_Types].xml",
            "_rels/.rels",
            "xl/workbook.xml",
            "xl/_rels/workbook.xml.rels",
            "xl/worksheets/sheet1.xml",
        ):
            ElementTree.fromstring(archive.read(name))
