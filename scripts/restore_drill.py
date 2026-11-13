"""恢复演练：备份 → 校验 → 恢复到临时目录 → 行数与文件核对。

用法：
    .\\.venv\\Scripts\\python.exe scripts/restore_drill.py
    .\\.venv\\Scripts\\python.exe scripts/restore_drill.py \
        --source-db C:\\Users\\Administrator\\.nl-rpa\\data\\rpa.db
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings
from app.ops.backup import create_backup, restore_backup, verify_backup


def _default_source_db() -> Path:
    from sqlalchemy.engine import make_url

    url = make_url(settings.DATABASE_URL)
    database = url.database
    return Path(database) if database else Path("rpa.db")


def _table_counts(database: Path) -> dict[str, int]:
    connection = sqlite3.connect(str(database))
    try:
        names = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        return {
            name: int(
                connection.execute(
                    f'SELECT COUNT(*) FROM "{name}"'
                ).fetchone()[0]
            )
            for name in names
        }
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="备份恢复演练")
    parser.add_argument(
        "--source-db",
        type=Path,
        default=None,
        help="默认使用 .env 的 SQLite 数据库",
    )
    parser.add_argument(
        "--workdir",
        type=Path,
        default=None,
        help="演练目录（默认系统临时目录）",
    )
    parser.add_argument(
        "--include",
        action="append",
        default=None,
    )
    parser.add_argument(
        "--drill-key",
        action="store_true",
        help="使用演练专用临时密钥（仅当生产密钥无效时）",
    )
    args = parser.parse_args(argv)

    from cryptography.fernet import Fernet

    source_db = args.source_db or _default_source_db()
    if not source_db.is_file():
        raise SystemExit(f"源数据库不存在：{source_db}")
    credential_key = (
        Fernet.generate_key().decode()
        if args.drill_key
        else settings.CREDENTIAL_ENCRYPTION_KEY
    )

    workdir = args.workdir or Path(tempfile.gettempdir()) / (
        f"nl-rpa-restore-drill-{time.strftime('%Y%m%d-%H%M%S')}"
    )
    workdir.mkdir(parents=True, exist_ok=True)
    backup_dir = workdir / "backup"
    restore_dir = workdir / "restore"
    if backup_dir.exists():
        raise SystemExit(f"演练目录已存在：{backup_dir}")

    includes = args.include or []
    manifest = asyncio.run(
        create_backup(
            database_url=f"sqlite+aiosqlite:///{source_db.as_posix()}",
            target_dir=backup_dir,
            source_dirs=[Path(item) for item in includes],
            credential_key=credential_key,
        )
    )
    verification = verify_backup(backup_dir)
    if not verification["ok"]:
        raise SystemExit(f"备份校验失败：{verification['errors']}")
    report = restore_backup(
        backup_dir,
        target_data_dir=restore_dir,
        database_url=(
            f"sqlite+aiosqlite:///"
            f"{(restore_dir / 'data' / 'rpa.db').as_posix()}"
        ),
        force=True,
    )
    restored_db = Path(report["database"]["path"])
    source_counts = _table_counts(source_db)
    restored_counts = _table_counts(restored_db)
    missing = {
        name: (count, restored_counts.get(name, 0))
        for name, count in source_counts.items()
        if restored_counts.get(name, 0) != count
    }
    result = {
        "ok": not missing,
        "source_db": str(source_db),
        "backup_dir": str(backup_dir),
        "restore_dir": str(restore_dir),
        "key_fingerprint": manifest["key_reference"]["fingerprint"],
        "table_counts": restored_counts,
        "mismatched_tables": missing,
        "verification": verification,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not missing else 1


if __name__ == "__main__":
    raise SystemExit(main())
