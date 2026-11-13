from __future__ import annotations

import csv
import hashlib
import io
import os
import tempfile
import zipfile
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from app.actions.protocol import StepError, StepResult, StepStatus
from app.actions.variables import resolve_template
from app.actions.xlsx_minimal import build_xlsx_bytes


FILE_ACTIONS = frozenset(
    {"write_csv", "write_xlsx", "save_artifact", "copy_file", "zip"}
)


async def execute_file_action(
    action: str,
    params: dict[str, Any],
    *,
    variables: dict[str, Any] | None = None,
    allowed_roots: Iterable[str | Path] | None = None,
) -> StepResult:
    normalized = str(action or "").strip().lower()
    if normalized not in FILE_ACTIONS:
        return _failed(
            "business_validation_failed",
            f"Unsupported file action: {action}",
        )
    roots = _roots(allowed_roots)
    try:
        values = resolve_template(dict(params or {}), variables or {})
        if not isinstance(values, dict):
            raise ValueError("File action params must be an object")
        artifacts = _dispatch(normalized, values, roots)
    except PermissionError as exc:
        return _failed("permission_denied", str(exc))
    except FileNotFoundError as exc:
        return _failed("artifact_missing", str(exc))
    except (TypeError, ValueError, OSError) as exc:
        category = (
            "data_empty"
            if "empty" in str(exc).lower()
            else "business_validation_failed"
        )
        return _failed(category, str(exc))
    result = StepResult(
        status=StepStatus.SUCCESS,
        message=f"{normalized} completed",
        value=artifacts[0]["path"] if len(artifacts) == 1 else artifacts,
        artifacts=artifacts,
    )
    result_var = str(values.get("result_var") or "").strip()
    if result_var:
        result.variables[result_var] = result.value
    return result


def _dispatch(
    action: str,
    params: dict[str, Any],
    roots: list[Path],
) -> list[dict[str, Any]]:
    if action == "write_csv":
        return [_write_csv(params, roots)]
    if action == "write_xlsx":
        return [_write_xlsx_action(params, roots)]
    if action == "save_artifact":
        return [_save_artifact(params, roots)]
    if action == "copy_file":
        return [_copy_file(params, roots)]
    return [_zip_files(params, roots)]


def _write_csv(params: dict[str, Any], roots: list[Path]) -> dict[str, Any]:
    target = _allowed_target(str(params.get("path") or ""), roots)
    _require_writable_target(
        target,
        bool(params.get("overwrite", True)),
    )
    rows = _rows(params.get("rows", params.get("values")))
    _require_rows(rows, bool(params.get("allow_empty", False)))
    headers = _headers(params.get("headers"), rows)
    _require_headers(rows, headers)
    stream = io.StringIO()
    writer = csv.DictWriter(
        stream,
        fieldnames=headers,
        extrasaction="ignore",
    )
    writer.writeheader()
    for row in rows:
        writer.writerow({
            header: row.get(header, "") for header in headers
        })
    _atomic_write_text(
        target,
        stream.getvalue(),
        encoding=str(params.get("encoding") or "utf-8-sig"),
    )
    return _artifact(target)


def _write_xlsx_action(
    params: dict[str, Any],
    roots: list[Path],
) -> dict[str, Any]:
    target = _allowed_target(str(params.get("path") or ""), roots)
    _require_writable_target(
        target,
        bool(params.get("overwrite", True)),
    )
    rows = _rows(params.get("rows", params.get("values")))
    _require_rows(rows, bool(params.get("allow_empty", False)))
    headers = _headers(params.get("headers"), rows)
    _require_headers(rows, headers)
    data = build_xlsx_bytes(
        rows,
        headers=headers,
        sheet_name=str(params.get("sheet_name") or "Sheet1"),
    )
    _atomic_write_bytes(target, data)
    return _artifact(target)


def _save_artifact(
    params: dict[str, Any],
    roots: list[Path],
) -> dict[str, Any]:
    target = _allowed_target(str(params.get("path") or ""), roots)
    _require_writable_target(
        target,
        bool(params.get("overwrite", True)),
    )
    source_path = str(params.get("source_path") or "").strip()
    if source_path:
        source = _allowed_target(source_path, roots, must_exist=True)
        data = source.read_bytes()
    else:
        content = params.get("content", params.get("variable"))
        if content is None or str(content) == "":
            raise ValueError("save_artifact content is empty")
        data = str(content).encode(
            str(params.get("encoding") or "utf-8")
        )
    _atomic_write_bytes(target, data)
    return _artifact(target)


def _copy_file(
    params: dict[str, Any],
    roots: list[Path],
) -> dict[str, Any]:
    source = _allowed_target(
        str(params.get("source") or ""),
        roots,
        must_exist=True,
    )
    target = _allowed_target(str(params.get("destination") or ""), roots)
    _require_writable_target(
        target,
        bool(params.get("overwrite", True)),
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_bytes(target, source.read_bytes())
    return _artifact(target)


def _zip_files(
    params: dict[str, Any],
    roots: list[Path],
) -> dict[str, Any]:
    target = _allowed_target(
        str(params.get("destination") or ""),
        roots,
    )
    _require_writable_target(
        target,
        bool(params.get("overwrite", True)),
    )
    sources = params.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("zip sources cannot be empty")
    files = [
        _allowed_target(str(item), roots, must_exist=True)
        for item in sources
    ]
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for source in files:
            archive.write(source, arcname=source.name)
    _atomic_write_bytes(target, stream.getvalue())
    return _artifact(target)


def write_xlsx(
    path: Path,
    *,
    rows: list[Mapping[str, object]],
    headers: list[str] | None = None,
    sheet_name: str = "Sheet1",
) -> None:
    materialized = [dict(row) for row in rows]
    selected = headers or _headers(None, materialized)
    path.write_bytes(
        build_xlsx_bytes(
            materialized,
            headers=selected,
            sheet_name=sheet_name,
        )
    )


def _roots(values: Iterable[str | Path] | None) -> list[Path]:
    roots: list[Path] = []
    for value in values or []:
        root = Path(value).expanduser().resolve()
        if root not in roots:
            roots.append(root)
    return roots


def _allowed_target(
    value: str,
    roots: list[Path],
    *,
    must_exist: bool = False,
) -> Path:
    if not value.strip():
        raise ValueError("Path cannot be empty")
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        if not roots:
            raise PermissionError("No allowed export directory configured")
        candidate = roots[0] / candidate
    resolved = candidate.resolve(strict=False)
    if not any(
        resolved == root or root in resolved.parents for root in roots
    ):
        raise PermissionError(f"Path is outside allowed directories: {resolved}")
    if must_exist and not resolved.is_file():
        raise FileNotFoundError(str(resolved))
    return resolved


def _require_writable_target(
    target: Path,
    overwrite: bool,
) -> None:
    if target.exists() and not overwrite:
        raise ValueError(f"Target already exists: {target}")


def _rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError("rows must resolve to an array")
    if any(not isinstance(row, Mapping) for row in value):
        raise ValueError("rows must contain objects")
    return [dict(row) for row in value]


def _headers(value: Any, rows: list[dict[str, Any]]) -> list[str]:
    if isinstance(value, list) and value:
        return [str(item) for item in value]
    headers: list[str] = []
    for row in rows:
        for key in row:
            if str(key) not in headers:
                headers.append(str(key))
    return headers


def _require_rows(rows: list[dict[str, Any]], allow_empty: bool) -> None:
    if rows:
        return
    if not allow_empty:
        raise ValueError("empty rows")
    raise FileNotFoundError("zero-row file was not created")


def _require_headers(
    rows: list[dict[str, Any]],
    headers: list[str],
) -> None:
    if not headers:
        raise ValueError("headers cannot be empty")
    for row in rows:
        missing = [header for header in headers if header not in row]
        if missing:
            raise ValueError(
                "Missing required columns: " + ", ".join(missing)
            )


def _atomic_write_text(path: Path, value: str, *, encoding: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "w", encoding=encoding, newline="") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def _atomic_write_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def _artifact(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size <= 0:
        raise FileNotFoundError("Generated artifact is missing or empty")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "path": str(path),
        "name": path.name,
        "size": path.stat().st_size,
        "sha256": digest,
        "delivered": False,
        "delivered_path": None,
    }


def _failed(category: str, message: str) -> StepResult:
    return StepResult(
        status=StepStatus.FAILED,
        message=message,
        error=StepError(category=category, message=message),
    )


__all__ = ["FILE_ACTIONS", "execute_file_action", "write_xlsx"]
