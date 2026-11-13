"""可移植迁移：SQLite 旧库补列、版本记录与幂等。"""

from __future__ import annotations

import asyncio

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.database import Base, run_schema_migrations
from app.migrations import SCHEMA_VERSION, applied_migrations


LEGACY_SCHEMA = [
    """
    CREATE TABLE workflow (
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL,
        name VARCHAR(255) NOT NULL,
        natural_language_input TEXT NOT NULL,
        status VARCHAR(20) NOT NULL DEFAULT 'draft',
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE execution_log (
        id INTEGER PRIMARY KEY,
        workflow_id INTEGER NOT NULL,
        status VARCHAR(20) NOT NULL DEFAULT 'running',
        started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        finished_at TIMESTAMP,
        error_message TEXT,
        screenshots JSON,
        step_logs JSON
    )
    """,
    """
    CREATE TABLE agent (
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL,
        name VARCHAR(255) NOT NULL,
        platform VARCHAR(50) NOT NULL DEFAULT 'windows',
        version VARCHAR(50) NOT NULL,
        capabilities JSON,
        status VARCHAR(20) NOT NULL DEFAULT 'offline',
        token_hash VARCHAR(128) NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
]


async def _column_names(engine, table: str) -> set[str]:
    async with engine.connect() as connection:
        columns = await connection.run_sync(
            lambda sync: inspect(sync).get_columns(table)
        )
    return {column["name"] for column in columns}


def test_portable_migrations_upgrade_legacy_sqlite(tmp_path) -> None:
    async def scenario() -> None:
        database = tmp_path / "legacy.db"
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{database.as_posix()}"
        )
        async with engine.begin() as connection:
            for statement in LEGACY_SCHEMA:
                await connection.execute(text(statement))
            await connection.execute(
                text(
                    "INSERT INTO workflow "
                    "(id, user_id, name, natural_language_input) "
                    "VALUES (1, 1, 'legacy', 'x')"
                )
            )
            await connection.execute(
                text(
                    "INSERT INTO agent "
                    "(id, user_id, name, version, token_hash) "
                    "VALUES (1, 1, 'agent', '1', 'hash')"
                )
            )
            await connection.execute(
                text(
                    "INSERT INTO execution_log "
                    "(id, workflow_id, status, step_logs) "
                    "VALUES (1, 1, 'awaiting_user', :step_logs)"
                ),
                {
                    "step_logs": (
                        '[{"type":"checkpoint","action":"checkpoint",'
                        '"status":"awaiting_user","next_step_index":1,'
                        '"step_id":2}]'
                    )
                },
            )

        # create_all 不会修改已存在的旧表，迁移负责补列。
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            executed = await run_schema_migrations(connection)
            applied = await applied_migrations(connection)

        assert executed == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
        assert max(applied) == SCHEMA_VERSION

        workflow_columns = await _column_names(engine, "workflow")
        assert "executor_type" in workflow_columns
        assert "required_capabilities" in workflow_columns
        assert "workspace_id" in workflow_columns
        assert "execution_mode" in workflow_columns
        assert "active_version_id" in workflow_columns
        assert "execution_policy" in workflow_columns
        assert "schedule_timezone" in workflow_columns
        assert "draft_revision" in workflow_columns

        execution_columns = await _column_names(engine, "execution_log")
        assert "artifacts" in execution_columns
        assert "metrics" in execution_columns
        assert "workspace_id" in execution_columns
        assert "workflow_version_id" in execution_columns
        assert "execution_policy_snapshot" in execution_columns
        assert "host_id" in execution_columns
        assert "browser_profile_id" in execution_columns
        assert "human_task_id" in execution_columns
        assert "host_environment" in execution_columns
        assert "host_bound" in execution_columns
        assert "host_affinity_hash" in execution_columns
        assert "host_reason" in execution_columns

        human_task_columns = await _column_names(engine, "human_task")
        assert "dedupe_key" in human_task_columns
        assert "verification_started_at" in human_task_columns

        agent_columns = await _column_names(engine, "agent")
        assert "allowed_apps" in agent_columns
        assert "allowed_doc_dirs" in agent_columns
        assert "workspace_id" in agent_columns
        assert "environment" in agent_columns
        assert "last_capabilities_at" in agent_columns
        assert "human_access_url" in agent_columns
        assert "human_access_instructions" in agent_columns
        assert "connection_security" in agent_columns

        credential_columns = await _column_names(engine, "credential")
        assert "user_id" in credential_columns
        assert "scope" in credential_columns
        assert "name" in credential_columns

        mcp_columns = await _column_names(engine, "mcp_server")
        assert "version" in mcp_columns
        assert "fingerprint" in mcp_columns
        assert "signature" in mcp_columns
        assert "toolset_fingerprint" in mcp_columns

        async with engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        "SELECT executor_type, required_capabilities "
                        "FROM workflow WHERE id = 1"
                    )
                )
            ).one()
            assert row[0] == "web"
            assert row[1] == "[]"
            legacy_task = (
                await connection.execute(
                    text(
                        "SELECT kind, status, dedupe_key, payload "
                        "FROM human_task WHERE execution_id = 1"
                    )
                )
            ).one()
            assert legacy_task[0] == "manual_confirmation"
            assert legacy_task[1] == "pending"
            assert legacy_task[2] == "legacy:execution:1"
            assert '"legacy_checkpoint"' in legacy_task[3]

        # 再次运行不重复执行。
        async with engine.begin() as connection:
            again = await run_schema_migrations(connection)
        assert again == []
        await engine.dispose()

    asyncio.run(scenario())


def test_migration_normalizes_isolated_agent_environment_to_vm(
    tmp_path,
) -> None:
    async def scenario() -> None:
        database = tmp_path / "isolated-agent.db"
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{database.as_posix()}"
        )
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            await connection.execute(
                text(
                    "INSERT INTO agent "
                    "(id, user_id, name, platform, version, capabilities, "
                    "allowed_apps, allowed_doc_dirs, environment, "
                    "connection_security, status, token_hash, created_at) "
                    "VALUES (1, 1, 'legacy-vm', 'windows', '0.1.0', "
                    "'[\"window\"]', '[]', '[]', 'isolated', 'unknown', "
                    "'offline', 'hash', CURRENT_TIMESTAMP)"
                )
            )
            executed = await run_schema_migrations(connection)
            row = (
                await connection.execute(
                    text(
                        "SELECT environment FROM agent WHERE id = 1"
                    )
                )
            ).one()

        assert executed == list(range(1, 11))
        assert row[0] == "vm"
        await engine.dispose()

    asyncio.run(scenario())
