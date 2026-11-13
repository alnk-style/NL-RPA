"""备份、校验与恢复（SQLite 文件快照 + 可移植逻辑导出）。

备份内容：

1. 数据库：SQLite 用 ``VACUUM INTO`` 生成一致性快照；其他数据库用
   JSONL 逻辑导出（表、行、时间与二进制列编码）。
2. 文件：logs / artifacts 等目录逐文件复制并记录 sha256。
3. 密钥引用：只记录 Fernet 密钥的 sha256 指纹（不落明文），备份时
   校验密钥合法性；无效密钥直接拒绝并给出轮换说明。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import shutil
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app import models  # noqa: F401
from app.database import Base
from app.migrations import SCHEMA_VERSION
from app.version import __version__


class BackupError(RuntimeError):
    """备份/恢复前置条件不满足。"""


def validate_fernet_key(value: str | bytes | None) -> bool:
    if value is None:
        return False
    key = value.encode("ascii") if isinstance(value, str) else value
    try:
        decoded = base64.urlsafe_b64decode(key)
    except (binascii.Error, ValueError):
        return False
    return len(decoded) == 32


def key_fingerprint(value: str | bytes) -> str:
    key = value.encode("utf-8") if isinstance(value, str) else bytes(value)
    return "sha256:" + hashlib.sha256(key).hexdigest()


def key_rotation_guide() -> str:
    return "\n".join(
        [
            "# 凭据密钥轮换说明",
            "",
            "1. 生成新密钥：",
            "   python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\"",
            "2. 先备份数据库与 logs/artifacts（本目录即备份快照）。",
            "3. 停止后端服务，避免轮换期间写入新凭据。",
            "4. 执行：",
            "   .\\.venv\\Scripts\\python.exe scripts/rotate_credentials.py "
            "--old-key <旧密钥> --new-key <新密钥>",
            "5. 把 .env 的 CREDENTIAL_ENCRYPTION_KEY 更新为新密钥并重启。",
            "6. 启动后用测试凭据解密验证；失败时用备份 + 旧密钥回退。",
            "",
            "注意：当前 .env 中的 CREDENTIAL_ENCRYPTION_KEY 若是占位值，",
            "必须先完成一次轮换再执行备份/升级。",
        ]
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sqlite_path(database_url: str) -> Path | None:
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite":
        return None
    database = url.database
    if not database or database == ":memory:":
        return None
    return Path(database)


def _encode_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return {"__type__": "datetime", "value": value.isoformat()}
    if isinstance(value, (bytes, bytearray)):
        return {
            "__type__": "bytes",
            "value": base64.b64encode(bytes(value)).decode("ascii"),
        }
    return value


def _decode_value(value: Any) -> Any:
    if isinstance(value, dict) and "__type__" in value:
        kind = value.get("__type__")
        raw = value.get("value")
        if kind == "bytes":
            return base64.b64decode(str(raw))
        if kind == "datetime":
            return str(raw)
    return value


def _snapshot_sqlite(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        connection = sqlite3.connect(str(source))
        try:
            connection.execute("VACUUM INTO ?", (str(target),))
        finally:
            connection.close()
    except sqlite3.Error:
        shutil.copyfile(source, target)


async def _logical_dump(database_url: str, target: Path) -> dict[str, Any]:
    engine = create_async_engine(database_url)
    counts: dict[str, int] = {}
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8") as handle:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
                for table in Base.metadata.sorted_tables:
                    rows = (
                        await connection.execute(select(table))
                    ).mappings().all()
                    counts[table.name] = len(rows)
                    for row in rows:
                        handle.write(
                            json.dumps(
                                {
                                    "table": table.name,
                                    "row": {
                                        key: _encode_value(value)
                                        for key, value in dict(row).items()
                                    },
                                },
                                ensure_ascii=False,
                                default=str,
                            )
                            + "\n"
                        )
    finally:
        await engine.dispose()
    return {"tables": counts}


def _copy_files(
    source_dirs: Sequence[Path],
    target_root: Path,
    *,
    exclude: Path | None = None,
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    used_labels: set[str] = set()
    excluded = exclude.resolve() if exclude is not None else None
    for position, source in enumerate(source_dirs):
        source = Path(source)
        if not source.exists():
            continue
        label = source.name or f"dir-{position}"
        while label in used_labels:
            label = f"{label}-{position}"
        used_labels.add(label)
        for file_path in sorted(source.rglob("*")):
            if not file_path.is_file():
                continue
            if excluded is not None:
                resolved = file_path.resolve()
                if (
                    resolved == excluded
                    or excluded in resolved.parents
                ):
                    # 备份目录位于被备份目录内部时，跳过自身避免递归。
                    continue
            relative = file_path.relative_to(source)
            destination = target_root / label / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(file_path, destination)
            entries.append(
                {
                    "label": label,
                    "relative_path": relative.as_posix(),
                    "source": str(file_path),
                    "file": str(destination.relative_to(target_root.parent)),
                    "size": destination.stat().st_size,
                    "sha256": _sha256_file(destination),
                }
            )
    return entries


async def create_backup(
    *,
    database_url: str,
    target_dir: Path | str,
    source_dirs: Iterable[Path | str] = (),
    credential_key: str | None = None,
    app_version: str = __version__,
) -> dict[str, Any]:
    """创建一致性备份并写入 manifest.json（返回清单）。"""

    if credential_key is not None and not validate_fernet_key(credential_key):
        raise BackupError(
            "CREDENTIAL_ENCRYPTION_KEY 不是合法 Fernet 密钥，"
            "备份已拒绝。\n" + key_rotation_guide()
        )

    target = Path(target_dir)
    if target.exists() and any(target.iterdir()):
        raise BackupError(f"备份目录必须为空：{target}")
    target.mkdir(parents=True, exist_ok=True)

    database_entries: list[dict[str, Any]] = []
    sqlite_source = _sqlite_path(database_url)
    dump_info: dict[str, Any] = {}
    if sqlite_source is not None and sqlite_source.is_file():
        snapshot = target / "database" / "rpa.sqlite3"
        _snapshot_sqlite(sqlite_source, snapshot)
        database_entries.append(
            {
                "kind": "sqlite",
                "file": str(snapshot.relative_to(target)),
                "size": snapshot.stat().st_size,
                "sha256": _sha256_file(snapshot),
            }
        )
    else:
        dump = target / "database" / "dump.jsonl"
        dump_info = await _logical_dump(database_url, dump)
        database_entries.append(
            {
                "kind": "logical-jsonl",
                "file": str(dump.relative_to(target)),
                "size": dump.stat().st_size,
                "sha256": _sha256_file(dump),
                "tables": dump_info.get("tables", {}),
            }
        )

    files = _copy_files(
        [Path(item) for item in source_dirs],
        target / "files",
        exclude=target,
    )
    rotation_path = target / "KEY_ROTATION.md"
    rotation_path.write_text(key_rotation_guide(), encoding="utf-8")

    manifest = {
        "app_version": app_version,
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.utcnow().isoformat(),
        "database_url": database_url.split("@")[-1]
        if "@" in database_url
        else database_url,
        "database": database_entries,
        "files": files,
        "key_reference": {
            "algorithm": "fernet",
            "valid": credential_key is None
            or validate_fernet_key(credential_key),
            "fingerprint": (
                key_fingerprint(credential_key)
                if credential_key is not None
                else None
            ),
            "rotation_doc": rotation_path.name,
        },
    }
    (target / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest


def verify_backup(backup_dir: Path | str) -> dict[str, Any]:
    backup_root = Path(backup_dir)
    manifest_path = backup_root / "manifest.json"
    errors: list[str] = []
    checked = 0
    if not manifest_path.is_file():
        return {"ok": False, "checked": 0, "errors": ["manifest 缺失"]}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in manifest.get("database", []):
        checked += 1
        path = backup_root / entry["file"]
        if not path.is_file():
            errors.append(f"缺少数据库文件：{entry['file']}")
            continue
        if _sha256_file(path) != entry["sha256"]:
            errors.append(f"数据库校验和不匹配：{entry['file']}")
    for entry in manifest.get("files", []):
        checked += 1
        path = backup_root / entry["file"]
        if not path.is_file():
            errors.append(f"缺少文件：{entry['file']}")
            continue
        if _sha256_file(path) != entry["sha256"]:
            errors.append(f"文件校验和不匹配：{entry['file']}")
    return {"ok": not errors, "checked": checked, "errors": errors}


def _restore_logical_sqlite(
    dump_path: Path,
    target_database: Path,
) -> dict[str, int]:
    target_database.parent.mkdir(parents=True, exist_ok=True)
    if target_database.exists():
        target_database.unlink()
    counts: dict[str, int] = {}
    rows_by_table: dict[str, list[dict[str, Any]]] = {}
    with dump_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            payload = json.loads(line)
            rows_by_table.setdefault(payload["table"], []).append(
                {
                    key: _decode_value(value)
                    for key, value in payload["row"].items()
                }
            )
    table_map = {table.name: table for table in Base.metadata.sorted_tables}
    sync_engine = create_engine(f"sqlite:///{target_database.as_posix()}")
    try:
        Base.metadata.create_all(sync_engine)
        with sync_engine.begin() as sync_connection:
            sync_connection.execute(text("PRAGMA foreign_keys=OFF"))
            for name, rows in rows_by_table.items():
                table = table_map.get(name)
                if table is None or not rows:
                    continue
                sync_connection.execute(table.insert(), rows)
                counts[name] = len(rows)
    finally:
        sync_engine.dispose()
    return counts


def restore_backup(
    backup_dir: Path | str,
    *,
    target_data_dir: Path | str,
    database_url: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """校验并恢复备份到目标目录。"""

    backup_root = Path(backup_dir)
    verification = verify_backup(backup_root)
    if not verification["ok"]:
        raise BackupError(
            "备份校验失败：" + "; ".join(verification["errors"])
        )
    manifest = json.loads(
        (backup_root / "manifest.json").read_text(encoding="utf-8")
    )
    target = Path(target_data_dir)
    target.mkdir(parents=True, exist_ok=True)

    restored_database: dict[str, Any] = {}
    for entry in manifest.get("database", []):
        source = backup_root / entry["file"]
        if entry["kind"] == "sqlite":
            if database_url:
                target_database = _sqlite_path(database_url)
            else:
                target_database = target / "data" / "rpa.db"
            if target_database is None:
                raise BackupError("恢复目标必须是 SQLite 数据库")
            if target_database.exists() and not force:
                raise BackupError(
                    f"目标数据库已存在：{target_database}（使用 --force 覆盖）"
                )
            target_database.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target_database)
            restored_database = {
                "kind": "sqlite",
                "path": str(target_database),
            }
        else:
            target_database = target / "data" / "rpa.db"
            if target_database.exists() and not force:
                raise BackupError(
                    f"目标数据库已存在：{target_database}（使用 --force 覆盖）"
                )
            counts = _restore_logical_sqlite(source, target_database)
            restored_database = {
                "kind": "logical-jsonl",
                "path": str(target_database),
                "tables": counts,
            }

    restored_files: list[str] = []
    for entry in manifest.get("files", []):
        source = backup_root / entry["file"]
        destination = target / entry["label"] / entry["relative_path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        restored_files.append(str(destination))

    return {
        "ok": True,
        "backup": str(backup_root),
        "target_data_dir": str(target),
        "database": restored_database,
        "files": restored_files,
        "app_version": manifest.get("app_version"),
        "schema_version": manifest.get("schema_version"),
        "key_reference": manifest.get("key_reference"),
    }


__all__ = [
    "BackupError",
    "create_backup",
    "key_fingerprint",
    "key_rotation_guide",
    "restore_backup",
    "validate_fernet_key",
    "verify_backup",
]
