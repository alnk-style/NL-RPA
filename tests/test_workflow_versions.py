from __future__ import annotations

import asyncio
import json

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.database import Base, run_schema_migrations
from app.migrations import applied_migrations
from app.models.workflow import Workflow
from app.models.workflow_version import WorkflowVersion


LEGACY_V5_SCHEMA = [
    """
    CREATE TABLE workflow (
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL,
        name VARCHAR(255) NOT NULL,
        natural_language_input TEXT NOT NULL,
        structured_plan JSON,
        generated_code TEXT,
        dag_definition JSON,
        trigger_type VARCHAR(50) NOT NULL DEFAULT 'manual',
        executor_type VARCHAR(20) NOT NULL DEFAULT 'web',
        required_capabilities JSON,
        cron_expression VARCHAR(100),
        status VARCHAR(20) NOT NULL DEFAULT 'draft',
        notification_config JSON,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        workspace_id INTEGER,
        execution_mode VARCHAR(20),
        active_version_id INTEGER,
        schedule_timezone VARCHAR(64),
        draft_revision INTEGER
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
        step_logs JSON,
        artifacts JSON,
        metrics JSON,
        workspace_id INTEGER
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
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        allowed_apps JSON,
        allowed_doc_dirs JSON,
        workspace_id INTEGER
    )
    """,
    """
    CREATE TABLE credential (
        id INTEGER PRIMARY KEY,
        workflow_id INTEGER NOT NULL,
        workspace_id INTEGER,
        key VARCHAR(255) NOT NULL,
        encrypted_value BLOB NOT NULL
    )
    """,
    """
    CREATE TABLE mcp_server (
        id INTEGER PRIMARY KEY,
        workspace_id INTEGER,
        version VARCHAR(255),
        fingerprint VARCHAR(255),
        signature VARCHAR(255),
        toolset_fingerprint VARCHAR(128)
    )
    """,
]


def test_workflow_version_model_serializes_immutable_plan() -> None:
    workflow = Workflow(id=1, name="w", natural_language_input="goal")
    version = WorkflowVersion(
        id=1,
        workflow_id=1,
        version=1,
        plan={"execution_mode": "structured", "steps": []},
        validation={"valid": True},
        action_catalog_version="2026-10-28.1",
    )
    workflow.id = 1
    assert version.workflow_id == 1
    assert version.plan["execution_mode"] == "structured"


def test_explicit_structured_mode_ignores_stale_generated_code() -> None:
    workflow = Workflow(
        id=1,
        user_id=1,
        name="structured",
        natural_language_input="goal",
        execution_mode="structured",
        executor_type="web",
        structured_plan={
            "execution_mode": "structured",
            "executor_type": "web",
            "variables": [],
            "steps": [
                {
                    "step_id": 1,
                    "executor": "web",
                    "action": "goto",
                    "params": {"url": "https://example.com"},
                }
            ],
        },
        generated_code="await page.goto('https://example.com')",
    )

    from app.services.workflow_versions import WorkflowVersionService

    plan, _validation, catalog_version = (
        WorkflowVersionService._snapshot(workflow)
    )

    assert plan["execution_mode"] == "structured"
    assert "generated_code" not in plan
    assert catalog_version == "2026-10-28.1"


def test_migration_backfills_legacy_structured_and_empty_workflows(
    tmp_path,
) -> None:
    async def scenario() -> None:
        database = tmp_path / "migration.db"
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{database.as_posix()}"
        )
        async with engine.begin() as connection:
            for statement in LEGACY_V5_SCHEMA:
                await connection.execute(text(statement))
            rows = [
                (
                    1,
                    "code",
                    json.dumps(
                        {
                            "executor_type": "web",
                            "execution_mode": "structured",
                            "steps": [
                                {
                                    "step_id": 1,
                                    "executor": "web",
                                    "action": "goto",
                                    "params": {"url": "https://example.com"},
                                }
                            ],
                        }
                    ),
                    "async def main(page):\n    await page.goto('https://example.com')\n",
                ),
                (
                    2,
                    "structured",
                    json.dumps(
                        {
                            "executor_type": "web",
                            "execution_mode": "structured",
                            "steps": [
                                {
                                    "step_id": 1,
                                    "executor": "web",
                                    "action": "goto",
                                    "params": {"url": "https://example.com"},
                                }
                            ],
                        }
                    ),
                    None,
                ),
                (3, "empty", None, None),
            ]
            for workflow_id, name, plan, code in rows:
                await connection.execute(
                    text(
                        "INSERT INTO workflow "
                        "(id, user_id, name, natural_language_input, "
                        "structured_plan, generated_code) "
                        "VALUES (:id, 1, :name, 'goal', :plan, :code)"
                    ),
                    {
                        "id": workflow_id,
                        "name": name,
                        "plan": plan,
                        "code": code,
                    },
                )
            await connection.execute(
                text(
                    "INSERT INTO credential "
                    "(id, workflow_id, key, encrypted_value) "
                    "VALUES (1, 1, 'token', 'secret')"
                )
            )

        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            executed = await run_schema_migrations(connection)
            applied = await applied_migrations(connection)

        assert executed == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
        assert max(applied) == 10

        async with engine.connect() as connection:
            workflows = (
                await connection.execute(
                    text(
                        "SELECT id, execution_mode, active_version_id "
                        "FROM workflow ORDER BY id"
                    )
                )
            ).fetchall()
            versions = (
                await connection.execute(
                    text(
                        "SELECT workflow_id, version, plan, validation "
                        "FROM workflow_version ORDER BY workflow_id"
                    )
                )
            ).fetchall()
            credential = (
                await connection.execute(
                    text(
                        "SELECT user_id, scope FROM credential "
                        "WHERE id = 1"
                    )
                )
            ).one()

        assert workflows[0][1] == "legacy_code"
        assert workflows[0][2] is not None
        assert workflows[1][1] == "structured"
        assert workflows[1][2] is not None
        assert workflows[2][1] == "structured"
        assert workflows[2][2] is None
        assert len(versions) == 2
        assert {row[0] for row in versions} == {1, 2}
        assert json.loads(versions[0][2])["execution_mode"] == "legacy_code"
        assert json.loads(versions[1][2])["execution_mode"] == "structured"
        assert json.loads(versions[0][3])["legacy_autosnapshot"] is True
        assert credential[0] == 1
        assert credential[1] == "workflow"

        async with engine.begin() as connection:
            assert await run_schema_migrations(connection) == []
        await engine.dispose()

    asyncio.run(scenario())
