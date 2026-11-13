"""升级前兼容检查、自动备份与迁移执行。"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

from app import models  # noqa: F401
from app.database import Base, run_schema_migrations
from app.migrations import SCHEMA_VERSION
from app.ops.backup import BackupError, create_backup
from app.version import __version__


async def read_database_schema_version(database_url: str) -> int:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            def _read(sync_connection) -> int:
                inspector = inspect(sync_connection)
                if not inspector.has_table("schema_migration"):
                    return 0
                value = sync_connection.execute(
                    text("SELECT MAX(version) FROM schema_migration")
                ).scalar()
                return int(value or 0)

            return int(await connection.run_sync(_read))
    finally:
        await engine.dispose()


async def check_upgrade_compatibility(
    database_url: str,
    *,
    app_schema_version: int = SCHEMA_VERSION,
) -> dict[str, Any]:
    database_version = await read_database_schema_version(database_url)
    if database_version > app_schema_version:
        return {
            "ok": False,
            "database_schema_version": database_version,
            "app_schema_version": app_schema_version,
            "reason": (
                "数据库 schema 版本高于当前应用，拒绝降级启动；"
                "请使用对应版本的应用或恢复备份。"
            ),
        }
    return {
        "ok": True,
        "database_schema_version": database_version,
        "app_schema_version": app_schema_version,
        "reason": "compatible",
    }


async def run_upgrade(
    database_url: str,
    *,
    backup_dir: Path | str | None = None,
    source_dirs: Iterable[Path | str] = (),
    credential_key: str | None = None,
    app_version: str = __version__,
) -> dict[str, Any]:
    """兼容检查 → 自动备份 → 执行迁移；返回回滚说明。"""

    compatibility = await check_upgrade_compatibility(database_url)
    if not compatibility["ok"]:
        raise BackupError(compatibility["reason"])

    target = (
        Path(backup_dir)
        if backup_dir is not None
        else Path(".tmp")
        / "backups"
        / f"pre-upgrade-{time.strftime('%Y%m%d-%H%M%S')}"
    )
    manifest = await create_backup(
        database_url=database_url,
        target_dir=target,
        source_dirs=source_dirs,
        credential_key=credential_key,
        app_version=app_version,
    )

    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            applied = await run_schema_migrations(connection)
    finally:
        await engine.dispose()

    return {
        "ok": True,
        "applied": applied,
        "compatibility": compatibility,
        "backup_dir": str(target),
        "manifest_database": manifest.get("database"),
        "rollback": (
            f"python scripts/restore.py --backup {target} "
            f"--target <data_dir> --force"
        ),
    }


__all__ = [
    "check_upgrade_compatibility",
    "read_database_schema_version",
    "run_upgrade",
]
