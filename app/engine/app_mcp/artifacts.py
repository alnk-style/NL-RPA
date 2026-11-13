"""Normalize MCP file references into platform artifact records."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from app.engine.artifacts import (
    is_absolute_target,
    sanitize_artifact_name,
)
from app.engine.connectors.untrusted import sanitize_untrusted_text

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def extract_mcp_artifacts(
    wrapped: dict[str, Any],
    *,
    server: str,
    tool: str,
    max_artifacts: int = 100,
) -> list[dict[str, Any]]:
    payload = wrapped if isinstance(wrapped, dict) else {}
    structured = payload.get("structured")
    if structured is None:
        structured = payload.get("data")
    if not isinstance(structured, dict):
        return []

    candidates: list[Any] = []
    top_level = structured.get("artifacts")
    if isinstance(top_level, list):
        candidates.extend(top_level)
    nested = structured.get("data")
    if isinstance(nested, dict):
        nested_artifacts = nested.get("artifacts")
        if isinstance(nested_artifacts, list):
            candidates.extend(nested_artifacts)

    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    limit = max(0, min(int(max_artifacts), 500))
    for index, candidate in enumerate(candidates):
        if len(records) >= limit:
            break
        if not isinstance(candidate, dict):
            continue
        record = _normalize_artifact(
            candidate,
            server=server,
            tool=tool,
        )
        path = str(record.get("path") or "")
        if path:
            key = os.path.normcase(os.path.abspath(os.path.expanduser(path)))
        else:
            key = f"__missing__:{index}"
        if key in seen:
            continue
        seen.add(key)
        records.append(record)
    return records


def _normalize_artifact(
    value: dict[str, Any],
    *,
    server: str,
    tool: str,
) -> dict[str, Any]:
    raw_path = value.get("path")
    path_text = str(raw_path).strip() if raw_path is not None else ""
    name = sanitize_artifact_name(
        str(value.get("name") or Path(path_text).name or "artifact")
    )
    media_type = sanitize_untrusted_text(
        value.get("media_type")
        or value.get("mime_type")
        or value.get("type")
        or "application/octet-stream",
        max_chars=120,
    )
    summary = sanitize_untrusted_text(
        value.get("summary") or "",
        max_chars=500,
    )
    size = _coerce_size(value.get("size"))
    error: str | None = None
    resolved_path = Path(path_text) if path_text else None

    if not path_text:
        error = "MCP artifact path is missing."
    elif _CONTROL_CHARS.search(path_text):
        error = "MCP artifact path contains invalid control characters."
    elif path_text.lower().startswith("file:"):
        error = "MCP artifact URI must use a filesystem path."
    elif not is_absolute_target(path_text):
        error = "MCP artifact path must be absolute."
    elif resolved_path is not None:
        try:
            resolved_path = resolved_path.expanduser().resolve(strict=False)
        except OSError:
            error = "MCP artifact path could not be resolved."
        else:
            if not resolved_path.is_file():
                error = "MCP artifact file does not exist."
            elif size is None:
                try:
                    size = resolved_path.stat().st_size
                except OSError:
                    error = "MCP artifact metadata could not be read."

    return {
        "name": name,
        "path": str(resolved_path) if resolved_path is not None else path_text,
        "media_type": media_type,
        "size": size,
        "summary": summary,
        "source": {"server": str(server), "tool": str(tool)},
        "error": error,
    }


def _coerce_size(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        size = int(value)
    except (TypeError, ValueError):
        return None
    return size if size >= 0 else None


__all__ = ["extract_mcp_artifacts"]
