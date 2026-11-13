"""Structured MCP result selection and safe result-path traversal."""

from __future__ import annotations

from typing import Any


class ResultPathError(ValueError):
    """A result path is malformed or cannot be resolved."""


def validate_result_path(value: Any) -> str:
    if not isinstance(value, str):
        raise ResultPathError("result_path must be a string.")
    path = value.strip()
    if not path:
        raise ResultPathError("result_path must not be empty.")
    if path.startswith("$"):
        raise ResultPathError("result_path does not support expressions.")

    if path.startswith("/"):
        raw_segments = path[1:].split("/")
        segments = [_decode_pointer(segment) for segment in raw_segments]
        if any(segment == "" for segment in segments):
            raise ResultPathError("result_path contains an empty pointer segment.")
    else:
        segments = path.split(".")
        if any(segment == "" for segment in segments):
            raise ResultPathError("result_path contains an empty path segment.")
    if not segments:
        raise ResultPathError("result_path must not be empty.")
    return path


def extract_result_value(
    wrapped: dict[str, Any],
    result_path: str | None = None,
) -> Any:
    payload = wrapped if isinstance(wrapped, dict) else {}
    structured = payload.get("structured")
    if structured is None:
        structured = payload.get("data")

    if result_path is not None:
        path = validate_result_path(result_path)
        if structured is None:
            raise ResultPathError("MCP returned no structured result for result_path.")
        return _resolve_path(structured, path)

    if structured is not None:
        return structured
    return payload.get("text", "")


def _resolve_path(value: Any, path: str) -> Any:
    segments = (
        [_decode_pointer(item) for item in path[1:].split("/")]
        if path.startswith("/")
        else path.split(".")
    )
    current = value
    for segment in segments:
        if isinstance(current, dict):
            if segment not in current:
                raise ResultPathError(f"result_path does not exist: {path}")
            current = current[segment]
            continue
        if isinstance(current, list):
            if not segment.isdigit():
                raise ResultPathError(f"result_path array index is invalid: {path}")
            index = int(segment)
            if index < 0 or index >= len(current):
                raise ResultPathError(
                    f"result_path array index is out of range: {path}"
                )
            current = current[index]
            continue
        raise ResultPathError(f"result_path cannot traverse a scalar value: {path}")
    return current


def _decode_pointer(segment: str) -> str:
    decoded = segment.replace("~1", "/").replace("~0", "~")
    if "~" in decoded and "~0" not in segment and "~1" not in segment:
        raise ResultPathError("result_path contains an invalid JSON Pointer escape.")
    return decoded


__all__ = [
    "ResultPathError",
    "extract_result_value",
    "validate_result_path",
]
