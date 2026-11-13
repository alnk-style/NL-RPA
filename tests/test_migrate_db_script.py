from __future__ import annotations

from pathlib import Path

from scripts.migrate_db import backup_sqlite_database


def test_migrate_db_backup_copies_existing_sqlite_file(
    tmp_path: Path,
) -> None:
    database = tmp_path / "rpa.db"
    database.write_bytes(b"sqlite-data")
    backup = backup_sqlite_database(
        f"sqlite+aiosqlite:///{database.as_posix()}"
    )
    assert backup is not None
    assert backup.is_file()
    assert backup.read_bytes() == b"sqlite-data"
    assert backup.name.startswith("rpa.db.bak-")


def test_migrate_db_backup_skips_missing_database(
    tmp_path: Path,
) -> None:
    backup = backup_sqlite_database(
        f"sqlite+aiosqlite:///{(tmp_path / 'missing.db').as_posix()}"
    )
    assert backup is None
