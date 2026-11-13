from __future__ import annotations

from pathlib import Path

import pytest

from app.engine.app_mcp.artifacts import extract_mcp_artifacts
from app.engine.app_mcp.results import (
    ResultPathError,
    extract_result_value,
    validate_result_path,
)
from app.engine.connectors.untrusted import wrap_untrusted_result


def _wrapped(structured: object, text: str = "") -> dict:
    return wrap_untrusted_result(
        {
            "text": text,
            "structured": structured,
            "is_error": False,
        },
        server="demo_app",
        tool="demo_export",
    )


def test_result_uses_structured_value_before_text() -> None:
    wrapped = _wrapped({"status": "ready"})

    assert wrapped["data"] == {"status": "ready"}
    assert wrapped["structured"] == {"status": "ready"}
    assert extract_result_value(wrapped) == {"status": "ready"}


def test_structured_content_is_preferred_over_legacy_data() -> None:
    wrapped = wrap_untrusted_result(
        {
            "text": "fallback",
            "structuredContent": {"value": "structured"},
            "data": {"value": "legacy"},
            "is_error": False,
        },
        server="demo_app",
        tool="demo_status",
    )

    assert wrapped["data"] == {"value": "structured"}
    assert extract_result_value(wrapped) == {"value": "structured"}


def test_result_falls_back_to_text() -> None:
    wrapped = wrap_untrusted_result(
        {
            "text": "plain response",
            "is_error": False,
        },
        server="demo_app",
        tool="demo_status",
    )

    assert extract_result_value(wrapped) == "plain response"


def test_dot_path_extracts_nested_value() -> None:
    wrapped = _wrapped({"artifacts": [{"path": "C:\\exports\\a.txt"}]})

    value = extract_result_value(wrapped, "artifacts.0.path")

    assert value == "C:\\exports\\a.txt"


def test_json_pointer_extracts_nested_value() -> None:
    wrapped = _wrapped({"metadata": {"name": "report", "tags": ["one", "two"]}})

    value = extract_result_value(wrapped, "/metadata/tags/1")

    assert value == "two"


def test_result_path_missing_raises_clear_error() -> None:
    wrapped = _wrapped({"artifacts": []})

    with pytest.raises(ResultPathError, match="out of range"):
        extract_result_value(wrapped, "artifacts.0.path")


def test_result_path_cannot_traverse_scalar() -> None:
    wrapped = _wrapped({"status": "ready"})

    with pytest.raises(ResultPathError, match="cannot traverse"):
        extract_result_value(wrapped, "status.value")


@pytest.mark.parametrize(
    "path",
    ["", "$.status", "status..value", "/status//value"],
)
def test_invalid_result_path_is_rejected(path: str) -> None:
    with pytest.raises(ResultPathError):
        validate_result_path(path)


def test_mcp_artifacts_are_normalized_and_deduplicated(
    tmp_path: Path,
) -> None:
    exported = tmp_path / "report.csv"
    exported.write_text("name,count\n", encoding="utf-8")
    wrapped = _wrapped(
        {
            "artifacts": [
                {
                    "name": "report.csv",
                    "path": str(exported),
                    "media_type": "text/csv",
                    "size": 11,
                    "summary": "report",
                }
            ],
            "data": {
                "artifacts": [
                    {
                        "name": "duplicate.csv",
                        "path": str(exported),
                    }
                ]
            },
        }
    )

    records = extract_mcp_artifacts(
        wrapped,
        server="demo_app",
        tool="demo_export",
    )

    assert len(records) == 1
    record = records[0]
    assert record["name"] == "report.csv"
    assert record["path"] == str(exported)
    assert record["media_type"] == "text/csv"
    assert record["size"] == 11
    assert record["summary"] == "report"
    assert record["source"] == {
        "server": "demo_app",
        "tool": "demo_export",
    }
    assert record["error"] is None


def test_missing_mcp_artifact_is_retained_with_error(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing.txt"
    wrapped = _wrapped(
        {"data": {"artifacts": [{"name": "missing.txt", "path": str(missing)}]}}
    )

    records = extract_mcp_artifacts(
        wrapped,
        server="demo_app",
        tool="demo_export",
    )

    assert len(records) == 1
    assert records[0]["path"] == str(missing)
    assert records[0]["error"]


def test_relative_mcp_artifact_is_rejected() -> None:
    wrapped = _wrapped(
        {"artifacts": [{"name": "relative.txt", "path": "relative.txt"}]}
    )

    records = extract_mcp_artifacts(
        wrapped,
        server="demo_app",
        tool="demo_export",
    )

    assert len(records) == 1
    assert records[0]["error"]
