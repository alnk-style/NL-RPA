"""轻量 schema 迁移工具。

项目同时支持 SQLite 与 PostgreSQL，早期的 ``PRAGMA table_info`` 兼容
迁移只适用于 SQLite。这里提供基于 SQLAlchemy inspector 的可移植
列检查与加法式迁移，作为 Alembic 的等价最小实现：

- ``Base.metadata.create_all`` 负责新建表；
- 本模块负责给既有表补列，SQLite / PostgreSQL 通用；
- 迁移均为加法式、可逆（回滚 = 使用备份恢复或重建库）。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncConnection

SCHEMA_VERSION = 10

MigrationStep = tuple[int, str, Callable[[AsyncConnection], Awaitable[None]]]


async def column_exists(
    connection: AsyncConnection,
    table_name: str,
    column_name: str,
) -> bool:
    def _inspect(sync_connection):
        inspector = inspect(sync_connection)
        if not inspector.has_table(table_name):
            return True
        columns = {
            str(column["name"])
            for column in inspector.get_columns(table_name)
        }
        return column_name in columns

    return bool(await connection.run_sync(_inspect))


async def add_column(
    connection: AsyncConnection,
    table_name: str,
    column_name: str,
    ddl_type: str,
) -> bool:
    """Add a column when missing; returns True when a DDL ran."""

    if await column_exists(connection, table_name, column_name):
        return False
    await connection.execute(
        text(
            f"ALTER TABLE {table_name} "
            f"ADD COLUMN {column_name} {ddl_type}"
        )
    )
    return True


async def add_index(
    connection: AsyncConnection,
    table_name: str,
    index_name: str,
    column_name: str,
) -> bool:
    """Create an index when missing（SQLite / PostgreSQL 通用）。"""

    def _inspect(sync_connection):
        inspector = inspect(sync_connection)
        if not inspector.has_table(table_name):
            return True
        names = {
            str(index.get("name"))
            for index in inspector.get_indexes(table_name)
        }
        return index_name in names

    if await connection.run_sync(_inspect):
        return False
    await connection.execute(
        text(
            f"CREATE INDEX IF NOT EXISTS {index_name} "
            f"ON {table_name} ({column_name})"
        )
    )
    return True


async def ensure_migration_table(connection: AsyncConnection) -> None:
    await connection.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS schema_migration (
                version INTEGER PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    )


async def applied_migrations(
    connection: AsyncConnection,
) -> dict[int, str]:
    await ensure_migration_table(connection)
    result = await connection.execute(
        text("SELECT version, name FROM schema_migration")
    )
    return {
        int(row[0]): str(row[1])
        for row in result.fetchall()
    }


async def run_versioned_migrations(
    connection: AsyncConnection,
    steps: Sequence[MigrationStep],
) -> list[int]:
    """按版本顺序执行加法式迁移，跳过已应用版本。"""

    applied = await applied_migrations(connection)
    executed: list[int] = []
    for version, name, handler in sorted(steps, key=lambda item: item[0]):
        if version in applied:
            continue
        await handler(connection)
        await connection.execute(
            text(
                "INSERT INTO schema_migration (version, name) "
                "VALUES (:version, :name)"
            ),
            {"version": version, "name": name},
        )
        executed.append(version)
    return executed


__all__ = [
    "SCHEMA_VERSION",
    "add_column",
    "add_index",
    "applied_migrations",
    "column_exists",
    "ensure_migration_table",
    "run_versioned_migrations",
]
