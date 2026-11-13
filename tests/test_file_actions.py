from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import zipfile
from pathlib import Path

from app.actions.files import execute_file_action
from app.actions.protocol import StepStatus
from app.actions.registry import default_action_registry
from app.engine.task_runtime import TaskRuntime


def _run(action: str, params: dict, root: Path):
    return asyncio.run(
        execute_file_action(
            action,
            params,
            variables={},
            allowed_roots=[root],
        )
    )


def test_write_csv_has_bom_and_artifact_metadata(tmp_path: Path) -> None:
    target = tmp_path / "rows.csv"
    result = _run(
        "write_csv",
        {
            "path": str(target),
            "rows": [{"name": "A", "amount": 12}],
            "headers": ["name", "amount"],
        },
        tmp_path,
    )
    assert result.status is StepStatus.SUCCESS
    assert target.read_bytes().startswith(b"\xef\xbb\xbf")
    rows = list(
        csv.DictReader(
            io.StringIO(target.read_text(encoding="utf-8-sig"))
        )
    )
    assert rows == [{"name": "A", "amount": "12"}]
    artifact = result.artifacts[0]
    assert artifact["size"] == target.stat().st_size
    assert artifact["sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()


def test_write_xlsx_is_valid_ooxml_with_rows(tmp_path: Path) -> None:
    target = tmp_path / "rows.xlsx"
    result = _run(
        "write_xlsx",
        {
            "path": str(target),
            "rows": [
                {"name": "A", "amount": 12},
                {"name": "B", "amount": 20},
            ],
            "headers": ["name", "amount"],
            "sheet_name": "Data",
        },
        tmp_path,
    )
    assert result.status is StepStatus.SUCCESS
    with zipfile.ZipFile(target) as archive:
        names = set(archive.namelist())
        assert "[Content_Types].xml" in names
        assert "xl/workbook.xml" in names
        sheet = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
    assert "name" in sheet
    assert "amount" in sheet
    assert ">A<" in sheet
    assert ">B<" in sheet


def test_empty_or_zero_row_files_fail(tmp_path: Path) -> None:
    empty = _run(
        "write_csv",
        {"path": str(tmp_path / "empty.csv"), "rows": []},
        tmp_path,
    )
    assert empty.status is StepStatus.FAILED
    assert empty.error is not None
    assert empty.error.category == "data_empty"

    zero = _run(
        "write_csv",
        {
            "path": str(tmp_path / "zero.csv"),
            "rows": [],
            "allow_empty": True,
        },
        tmp_path,
    )
    assert zero.status is StepStatus.FAILED
    assert zero.error is not None
    assert zero.error.category == "artifact_missing"


def test_missing_required_fields_and_outside_paths_fail(
    tmp_path: Path,
) -> None:
    missing = _run(
        "write_csv",
        {
            "path": str(tmp_path / "missing.csv"),
            "rows": [{"name": "A"}],
            "headers": ["name", "amount"],
        },
        tmp_path,
    )
    assert missing.status is StepStatus.FAILED
    assert missing.error is not None
    assert missing.error.category == "business_validation_failed"

    outside = _run(
        "write_csv",
        {
            "path": str(tmp_path.parent / "outside.csv"),
            "rows": [{"name": "A"}],
        },
        tmp_path,
    )
    assert outside.status is StepStatus.FAILED
    assert outside.error is not None
    assert outside.error.category == "permission_denied"


def test_save_artifact_copy_file_and_zip(tmp_path: Path) -> None:
    saved = _run(
        "save_artifact",
        {
            "path": str(tmp_path / "note.txt"),
            "content": "hello",
        },
        tmp_path,
    )
    assert saved.status is StepStatus.SUCCESS

    copied = _run(
        "copy_file",
        {
            "source": str(tmp_path / "note.txt"),
            "destination": str(tmp_path / "copy.txt"),
        },
        tmp_path,
    )
    assert copied.status is StepStatus.SUCCESS
    assert (tmp_path / "copy.txt").read_text(encoding="utf-8") == "hello"

    zipped = _run(
        "zip",
        {
            "destination": str(tmp_path / "bundle.zip"),
            "sources": [
                str(tmp_path / "note.txt"),
                str(tmp_path / "copy.txt"),
            ],
        },
        tmp_path,
    )
    assert zipped.status is StepStatus.SUCCESS
    with zipfile.ZipFile(tmp_path / "bundle.zip") as archive:
        assert set(archive.namelist()) == {"note.txt", "copy.txt"}


def test_file_actions_honor_overwrite_false(tmp_path: Path) -> None:
    target = tmp_path / "existing.csv"
    target.write_text("keep\n", encoding="utf-8")

    rejected = _run(
        "write_csv",
        {
            "path": str(target),
            "rows": [{"name": "new"}],
            "overwrite": False,
        },
        tmp_path,
    )
    assert rejected.status is StepStatus.FAILED
    assert rejected.error is not None
    assert rejected.error.category == "business_validation_failed"
    assert target.read_text(encoding="utf-8") == "keep\n"

    source = tmp_path / "source.txt"
    source.write_text("source", encoding="utf-8")
    destination = tmp_path / "destination.txt"
    destination.write_text("destination", encoding="utf-8")
    copied = _run(
        "copy_file",
        {
            "source": str(source),
            "destination": str(destination),
            "overwrite": False,
        },
        tmp_path,
    )
    assert copied.status is StepStatus.FAILED
    assert destination.read_text(encoding="utf-8") == "destination"


def test_files_catalog_and_task_runtime_dispatch_without_desktop(
    tmp_path: Path,
) -> None:
    catalog = default_action_registry().catalog(executor="files")
    assert {item.id for item in catalog.actions} == {
        "write_csv",
        "write_xlsx",
        "save_artifact",
        "copy_file",
        "zip",
    }

    async def scenario():
        desktop_called = False

        async def desktop_step(step, state):
            nonlocal desktop_called
            desktop_called = True
            raise AssertionError("files must not call desktop")

        runtime = TaskRuntime(
            step_executor=desktop_step,
            action_registry=default_action_registry(),
            action_services={"allowed_roots": [tmp_path]},
        )
        result = await runtime.execute(
            {
                "variables": {"rows": [{"name": "A"}]},
                "steps": [
                    {
                        "step_id": 1,
                        "executor": "files",
                        "action": "write_csv",
                        "params": {
                            "path": str(tmp_path / "runtime.csv"),
                            "values": "${rows}",
                        },
                    }
                ],
            }
        )
        return result, desktop_called

    result, desktop_called = asyncio.run(scenario())
    assert result.success is True
    assert desktop_called is False
    assert result.artifacts
