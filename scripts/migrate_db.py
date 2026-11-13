"""数据库迁移 CLI（Alembic 等价的最小实现）。

用法：

    .\\.venv\\Scripts\\python.exe scripts/migrate_db.py --status
    .\\.venv\\Scripts\\python.exe scripts/migrate_db.py
    .\\.venv\\Scripts\\python.exe scripts/migrate_db.py \
        --database-url postgresql+asyncpg://user:pass@host/db

迁移步骤全部是加法式（CREATE TABLE / ADD COLUMN / CREATE INDEX），
SQLite 与 PostgreSQL 共用；失败时请使用备份恢复回滚。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app import models  # noqa: F401
from app.config import settings
from app.database import Base, run_schema_migrations
from app.migrations import SCHEMA_VERSION, applied_migrations


def backup_sqlite_database(database_url: str) -> Path | None:
    url = make_url(database_url)
    if not url.drivername.startswith("sqlite"):
        return None
    value = str(url.database or "").strip()
    if not value:
        return None
    database = Path(value).expanduser()
    if not database.is_file():
        return None
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = database.with_name(f"{database.name}.bak-{timestamp}")
    shutil.copy2(database, backup)
    return backup


async def migrate(database_url: str) -> dict:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            executed = await run_schema_migrations(connection)
            applied = await applied_migrations(connection)
        return {
            "database_url": _safe_url(database_url),
            "schema_version": SCHEMA_VERSION,
            "executed": executed,
            "applied": [
                {"version": version, "name": name}
                for version, name in sorted(applied.items())
            ],
        }
    finally:
        await engine.dispose()


def _safe_url(url: str) -> str:
    if "@" not in url:
        return url
    scheme, _, remainder = url.partition("://")
    _, _, host = remainder.rpartition("@")
    return f"{scheme}://***@{host}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="NL-RPA 数据库迁移")
    parser.add_argument(
        "--database-url",
        default=settings.DATABASE_URL,
        help="默认使用 .env 的 DATABASE_URL",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="只读查询已应用版本（仍会创建 schema_migration 表）",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="跳过 SQLite 迁移前文件备份（不推荐）",
    )
    args = parser.parse_args(argv)

    if not args.no_backup:
        backup = backup_sqlite_database(args.database_url)
        if backup is not None:
            print(
                json.dumps(
                    {"backup": str(backup)},
                    ensure_ascii=False,
                )
            )
    report = asyncio.run(migrate(args.database_url))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
