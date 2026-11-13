from __future__ import annotations

from pathlib import Path
from typing import Any


PATH_KEYS = {
    "save_path",
    "output_path",
    "directory",
    "folder",
    "path",
}
FILENAME_KEYS = {
    "document_name",
    "filename",
    "file_name",
    "doc_name",
}
CONTENT_KEYS = {
    "document_content",
    "content",
    "text",
    "body",
}
WPS_MARKERS = (
    "wps",
    "kwps.application",
    ".docx",
    ".doc",
    "word 文档",
)


def _normalized_key(value: object) -> str:
    return str(value).strip().lower().replace("-", "_")


def _find_first_string(
    value: object,
    keys: set[str],
) -> str | None:
    if isinstance(value, dict):
        variable_name = _normalized_key(value.get("name", ""))
        if variable_name in keys:
            for value_key in ("default", "value", "content"):
                candidate = value.get(value_key)
                if isinstance(candidate, str) and candidate.strip():
                    return candidate.strip()
        for key, child in value.items():
            if (
                _normalized_key(key) in keys
                and isinstance(child, str)
                and child.strip()
            ):
                return child.strip()
        for child in value.values():
            found = _find_first_string(child, keys)
            if found:
                return found
    elif isinstance(value, (list, tuple, set)):
        for child in value:
            found = _find_first_string(child, keys)
            if found:
                return found
    return None


def _contains_wps_marker(value: object) -> bool:
    if isinstance(value, str):
        normalized = value.casefold()
        return any(marker in normalized for marker in WPS_MARKERS)
    if isinstance(value, dict):
        return any(_contains_wps_marker(child) for child in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_contains_wps_marker(child) for child in value)
    return False


def _document_path(
    directory_value: str | None,
    filename_value: str | None,
) -> str:
    default_dir = Path.home() / "Documents"
    directory = Path(directory_value).expanduser() if directory_value else default_dir
    filename = (filename_value or "NL-RPA文档").strip()
    if Path(filename).suffix:
        document_name = filename
    else:
        document_name = f"{filename}.docx"

    if directory.suffix and directory.name == document_name:
        return str(directory)
    if directory.suffix and not directory.is_dir():
        return str(directory)
    return str(directory / document_name)


def build_desktop_document_plan(
    structured_plan: dict[str, Any],
) -> dict[str, Any] | None:
    """Build a deterministic WPS document action plan when detected."""

    if not isinstance(structured_plan, dict):
        return None
    if not _contains_wps_marker(structured_plan):
        return None

    directory = _find_first_string(structured_plan, PATH_KEYS)
    filename = _find_first_string(structured_plan, FILENAME_KEYS)
    content = _find_first_string(structured_plan, CONTENT_KEYS) or ""
    path = _document_path(directory, filename)

    return {
        "trigger": structured_plan.get(
            "trigger",
            {"type": "manual", "cron": None},
        ),
        "executor_type": "windows",
        "required_capabilities": ["wps"],
        "steps": [
            {
                "step_id": 1,
                "action": "create_document",
                "params": {
                    "path": path,
                    "content": content,
                    "progid": "kwps.Application",
                    "overwrite": True,
                },
                "depends_on": [],
            }
        ],
        "variables": structured_plan.get("variables", []),
    }


__all__ = ["build_desktop_document_plan"]
