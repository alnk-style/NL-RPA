"""备份清单、校验和、恢复演练与密钥轮换。"""

from __future__ import annotations

import asyncio
import sqlite3
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.database import Base, run_schema_migrations
from app.models.credential import Credential
from app.models.mcp import MCPServer
from app.models.user import User
from app.models.workflow import Workflow
from app.ops.backup import (
    BackupError,
    create_backup,
    key_fingerprint,
    restore_backup,
    validate_fernet_key,
    verify_backup,
)
from app.ops.upgrade import (
    check_upgrade_compatibility,
    run_upgrade,
)
from app.security.credentials import encrypt_value, load_credential_values
from scripts.rotate_credentials import rotate


async def _seed_database(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            await run_schema_migrations(connection)
        session_factory = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        async with session_factory() as session:
            session.add(
                User(
                    id=1,
                    username="tester",
                    email="t@example.com",
                    hashed_password="x",
                )
            )
            workflow = Workflow(
                user_id=1,
                name="中文流程",
                natural_language_input="备份演练",
            )
            session.add(workflow)
            await session.commit()
    finally:
        await engine.dispose()


def _sqlite_counts(database) -> dict[str, int]:
    connection = sqlite3.connect(str(database))
    try:
        return {
            name: connection.execute(
                f'SELECT COUNT(*) FROM "{name}"'
            ).fetchone()[0]
            for name in ("user", "workflow")
        }
    finally:
        connection.close()


def test_backup_verify_restore_roundtrip(tmp_path) -> None:
    source_db = tmp_path / "source.db"
    database_url = f"sqlite+aiosqlite:///{source_db.as_posix()}"
    asyncio.run(_seed_database(database_url))

    logs = tmp_path / "logs"
    logs.mkdir()
    artifact = logs / "run.txt"
    artifact.write_text("artifact-content", encoding="utf-8")

    key = Fernet.generate_key().decode()
    backup_dir = tmp_path / "backup"
    manifest = asyncio.run(
        create_backup(
            database_url=database_url,
            target_dir=backup_dir,
            source_dirs=[logs],
            credential_key=key,
        )
    )
    assert manifest["key_reference"]["valid"] is True
    assert manifest["key_reference"]["fingerprint"] == key_fingerprint(key)
    assert manifest["database"][0]["kind"] == "sqlite"
    assert manifest["files"][0]["sha256"]
    assert (backup_dir / "KEY_ROTATION.md").is_file()
    assert verify_backup(backup_dir)["ok"] is True

    restore_dir = tmp_path / "restored"
    restored_db = restore_dir / "data" / "rpa.db"
    report = restore_backup(
        backup_dir,
        target_data_dir=restore_dir,
        database_url=(
            f"sqlite+aiosqlite:///{restored_db.as_posix()}"
        ),
    )
    assert report["ok"] is True
    assert _sqlite_counts(source_db) == _sqlite_counts(restored_db)
    assert (restore_dir / "logs" / "run.txt").read_text(
        encoding="utf-8"
    ) == "artifact-content"

    # 篡改文件后校验失败。
    (backup_dir / manifest["files"][0]["file"]).write_text(
        "tampered",
        encoding="utf-8",
    )
    verification = verify_backup(backup_dir)
    assert verification["ok"] is False
    assert any("校验和" in error for error in verification["errors"])


def test_backup_refuses_invalid_key_with_rotation_guide(tmp_path) -> None:
    database_url = (
        f"sqlite+aiosqlite:///{(tmp_path / 'db.db').as_posix()}"
    )
    asyncio.run(_seed_database(database_url))
    with pytest.raises(BackupError) as exc_info:
        asyncio.run(
            create_backup(
                database_url=database_url,
                target_dir=tmp_path / "backup",
                credential_key="your-fernet-key-here",
            )
        )
    message = str(exc_info.value)
    assert "轮换" in message
    assert "Fernet" in message
    assert validate_fernet_key("your-fernet-key-here") is False


def test_logical_dump_and_restore_to_new_sqlite(tmp_path) -> None:
    # 指向不存在的 SQLite 文件时走逻辑导出分支。
    database_url = (
        f"sqlite+aiosqlite:///{(tmp_path / 'empty.db').as_posix()}"
    )
    manifest = asyncio.run(
        create_backup(
            database_url=database_url,
            target_dir=tmp_path / "backup",
            credential_key=None,
        )
    )
    assert manifest["database"][0]["kind"] == "logical-jsonl"
    restore_dir = tmp_path / "restore"
    report = restore_backup(
        tmp_path / "backup",
        target_data_dir=restore_dir,
        database_url=(
            f"sqlite+aiosqlite:///"
            f"{(restore_dir / 'data' / 'rpa.db').as_posix()}"
        ),
    )
    assert report["ok"] is True
    assert _sqlite_counts(restore_dir / "data" / "rpa.db") == {
        "user": 0,
        "workflow": 0,
    }


def test_backup_target_inside_source_is_excluded(tmp_path) -> None:
    source_db = tmp_path / "source.db"
    database_url = f"sqlite+aiosqlite:///{source_db.as_posix()}"
    asyncio.run(_seed_database(database_url))

    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "app.log").write_text("log-line", encoding="utf-8")
    # 默认 CLI 行为：目标位于 logs/backups/ 下且同时备份 logs。
    target = logs / "backups" / "current"
    manifest = asyncio.run(
        create_backup(
            database_url=database_url,
            target_dir=target,
            source_dirs=[logs],
            credential_key=None,
        )
    )
    assert [entry["relative_path"] for entry in manifest["files"]] == [
        "app.log"
    ]
    assert verify_backup(target)["ok"] is True


def test_upgrade_compatibility_and_auto_backup(tmp_path) -> None:
    database_url = (
        f"sqlite+aiosqlite:///{(tmp_path / 'upgrade.db').as_posix()}"
    )
    asyncio.run(_seed_database(database_url))
    compatibility = asyncio.run(
        check_upgrade_compatibility(database_url)
    )
    assert compatibility["ok"] is True
    assert compatibility["database_schema_version"] > 0

    key = Fernet.generate_key().decode()
    report = asyncio.run(
        run_upgrade(
            database_url,
            backup_dir=tmp_path / "pre-upgrade",
            credential_key=key,
        )
    )
    assert report["ok"] is True
    assert (tmp_path / "pre-upgrade" / "manifest.json").is_file()
    assert "restore.py" in report["rollback"]

    # 数据库版本高于应用版本时拒绝升级。
    connection = sqlite3.connect(str(tmp_path / "upgrade.db"))
    try:
        connection.execute(
            "INSERT OR REPLACE INTO schema_migration (version, name) "
            "VALUES (99, 'future')"
        )
        connection.commit()
    finally:
        connection.close()
    blocked = asyncio.run(check_upgrade_compatibility(database_url))
    assert blocked["ok"] is False
    with pytest.raises(BackupError):
        asyncio.run(
            run_upgrade(
                database_url,
                backup_dir=tmp_path / "blocked",
                credential_key=key,
            )
        )


def test_rotate_credentials_reencrypts_values(tmp_path) -> None:
    database_url = (
        f"sqlite+aiosqlite:///{(tmp_path / 'rotate.db').as_posix()}"
    )
    old_key = Fernet.generate_key().decode()
    new_key = Fernet.generate_key().decode()
    old_config = SimpleNamespace(CREDENTIAL_ENCRYPTION_KEY=old_key)

    async def seed() -> None:
        engine = create_async_engine(database_url)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            session_factory = async_sessionmaker(
                engine,
                class_=AsyncSession,
                expire_on_commit=False,
            )
            async with session_factory() as session:
                session.add(
                    User(
                        id=1,
                        username="tester",
                        email="t@example.com",
                        hashed_password="x",
                    )
                )
                workflow = Workflow(
                    user_id=1,
                    name="rotate",
                    natural_language_input="x",
                )
                session.add(workflow)
                await session.commit()
                await session.refresh(workflow)
                session.add(
                    Credential(
                        workflow_id=workflow.id,
                        key="password",
                        encrypted_value=encrypt_value(
                            old_config,
                            "s3cret",
                        ),
                    )
                )
                session.add(
                    MCPServer(
                        user_id=1,
                        name="mcp",
                        transport="http",
                        url="http://127.0.0.1:1/mcp",
                        secret_encrypted=encrypt_value(
                            old_config,
                            "token",
                        ),
                    )
                )
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(seed())
    counts = asyncio.run(rotate(database_url, old_key, new_key))
    assert counts == {"credentials": 1, "mcp_secrets": 1}

    async def verify() -> str:
        engine = create_async_engine(database_url)
        try:
            session_factory = async_sessionmaker(
                engine,
                class_=AsyncSession,
                expire_on_commit=False,
            )
            async with session_factory() as session:
                values = await load_credential_values(
                    session,
                    SimpleNamespace(
                        CREDENTIAL_ENCRYPTION_KEY=new_key
                    ),
                    1,
                )
            return values["password"]
        finally:
            await engine.dispose()

    assert asyncio.run(verify()) == "s3cret"
