from collections.abc import AsyncGenerator
from datetime import datetime
import json

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings
from app.migrations import (
    add_column,
    add_index,
    column_exists,
    run_versioned_migrations,
)


class Base(DeclarativeBase):
    pass


engine = create_async_engine(settings.DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def _ensure_workflow_executor_columns(
    connection: AsyncConnection,
) -> None:
    await add_column(
        connection,
        "workflow",
        "executor_type",
        "VARCHAR(20) NOT NULL DEFAULT 'web'",
    )
    await add_column(
        connection,
        "workflow",
        "required_capabilities",
        "JSON",
    )

    await connection.execute(
        text(
            """
            UPDATE workflow
            SET executor_type = 'web'
            WHERE executor_type IS NULL
            """
        )
    )
    await connection.execute(
        text(
            """
            UPDATE workflow
            SET required_capabilities = '[]'
            WHERE required_capabilities IS NULL
            """
        )
    )
    await add_index(
        connection,
        "workflow",
        "ix_workflow_executor_type",
        "executor_type",
    )


async def _ensure_agent_config_columns(
    connection: AsyncConnection,
) -> None:
    await add_column(connection, "agent", "allowed_apps", "JSON")
    await add_column(connection, "agent", "allowed_doc_dirs", "JSON")

    await connection.execute(
        text(
            """
            UPDATE agent
            SET allowed_apps = '[]'
            WHERE allowed_apps IS NULL
            """
        )
    )
    await connection.execute(
        text(
            """
            UPDATE agent
            SET allowed_doc_dirs = '[]'
            WHERE allowed_doc_dirs IS NULL
            """
        )
    )


async def _ensure_execution_artifact_columns(
    connection: AsyncConnection,
) -> None:
    await add_column(connection, "execution_log", "artifacts", "JSON")
    await add_column(connection, "execution_log", "metrics", "JSON")


async def _ensure_workspace_columns(
    connection: AsyncConnection,
) -> None:
    """阶段 5：给既有表补 workspace_id（可空，向后兼容）。"""

    targets = (
        "workflow",
        "execution_log",
        "credential",
        "agent",
        "mcp_server",
        "tool_call_audit",
    )
    for table in targets:
        added = await add_column(
            connection,
            table,
            "workspace_id",
            "INTEGER",
        )
        if added:
            await add_index(
                connection,
                table,
                f"ix_{table}_workspace_id",
                "workspace_id",
            )


async def _ensure_plugin_integrity_columns(
    connection: AsyncConnection,
) -> None:
    for column in ("version", "fingerprint", "signature"):
        await add_column(
            connection,
            "mcp_server",
            column,
            "VARCHAR(255)",
        )
    await add_column(
        connection,
        "mcp_server",
        "toolset_fingerprint",
        "VARCHAR(128)",
    )


async def _ensure_workflow_versioning_columns(
    connection: AsyncConnection,
) -> None:
    from app.schemas.execution_policy import default_execution_policy_dict

    await add_column(
        connection,
        "workflow",
        "execution_mode",
        "VARCHAR(20) NOT NULL DEFAULT 'structured'",
    )
    await add_column(connection, "workflow", "execution_policy", "JSON")
    await add_column(connection, "workflow", "active_version_id", "INTEGER")
    await add_column(
        connection,
        "workflow",
        "schedule_timezone",
        "VARCHAR(64) NOT NULL DEFAULT 'Asia/Shanghai'",
    )
    await add_column(
        connection,
        "workflow",
        "draft_revision",
        "INTEGER NOT NULL DEFAULT 0",
    )
    await add_index(
        connection,
        "workflow",
        "ix_workflow_execution_mode",
        "execution_mode",
    )
    await add_index(
        connection,
        "workflow",
        "ix_workflow_active_version_id",
        "active_version_id",
    )

    has_plan = await column_exists(
        connection,
        "workflow",
        "structured_plan",
    )
    has_code = await column_exists(
        connection,
        "workflow",
        "generated_code",
    )
    selected_plan = "structured_plan" if has_plan else "NULL"
    selected_code = "generated_code" if has_code else "NULL"
    result = await connection.execute(
        text(
            "SELECT id, execution_mode, "
            f"{selected_plan} AS structured_plan, "
            f"{selected_code} AS generated_code "
            "FROM workflow ORDER BY id"
        )
    )
    policy_json = json.dumps(
        default_execution_policy_dict(),
        ensure_ascii=False,
    )
    await connection.execute(
        text(
            "UPDATE workflow SET execution_policy = :policy "
            "WHERE execution_policy IS NULL"
        ),
        {"policy": policy_json},
    )
    await connection.execute(
        text(
            "UPDATE workflow SET schedule_timezone = 'Asia/Shanghai' "
            "WHERE schedule_timezone IS NULL OR schedule_timezone = ''"
        )
    )
    await connection.execute(
        text(
            "UPDATE workflow SET draft_revision = 0 "
            "WHERE draft_revision IS NULL"
        )
    )

    for row in result.fetchall():
        workflow_id = int(row[0])
        structured = _json_object(row[2])
        generated_code = (
            str(row[3]) if row[3] is not None else ""
        )
        if generated_code.strip():
            execution_mode = "legacy_code"
            plan = {
                "schema_version": 1,
                "execution_mode": "legacy_code",
                "generated_code": generated_code,
                "migrated_from": "workflow_draft",
            }
            catalog_version = "legacy-2026-10-22"
        elif structured and structured.get("steps"):
            execution_mode = "structured"
            plan = {
                **structured,
                "schema_version": structured.get("schema_version", 1),
                "execution_mode": "structured",
            }
            catalog_version = "2026-10-28.1"
        else:
            await connection.execute(
                text(
                    "UPDATE workflow SET execution_mode = 'structured' "
                    "WHERE id = :workflow_id"
                ),
                {"workflow_id": workflow_id},
            )
            continue

        await connection.execute(
            text(
                "UPDATE workflow SET execution_mode = :mode, "
                "execution_policy = COALESCE(execution_policy, :policy) "
                "WHERE id = :workflow_id"
            ),
            {
                "mode": execution_mode,
                "policy": policy_json,
                "workflow_id": workflow_id,
            },
        )
        existing = await connection.execute(
            text(
                "SELECT id FROM workflow_version "
                "WHERE workflow_id = :workflow_id AND version = 1"
            ),
            {"workflow_id": workflow_id},
        )
        if existing.first() is None:
            await connection.execute(
                text(
                    "INSERT INTO workflow_version "
                    "(workflow_id, version, plan, validation, "
                    "action_catalog_version, created_at) "
                    "VALUES (:workflow_id, 1, :plan, :validation, "
                    ":catalog_version, CURRENT_TIMESTAMP)"
                ),
                {
                    "workflow_id": workflow_id,
                    "plan": json.dumps(plan, ensure_ascii=False),
                    "validation": json.dumps(
                        {
                            "valid": True,
                            "migrated": True,
                            "legacy_autosnapshot": True,
                        },
                        ensure_ascii=False,
                    ),
                    "catalog_version": catalog_version,
                },
            )
        version_result = await connection.execute(
            text(
                "SELECT id FROM workflow_version "
                "WHERE workflow_id = :workflow_id AND version = 1"
            ),
            {"workflow_id": workflow_id},
        )
        version_id = version_result.scalar_one()
        await connection.execute(
            text(
                "UPDATE workflow SET active_version_id = :version_id "
                "WHERE id = :workflow_id"
            ),
            {
                "version_id": int(version_id),
                "workflow_id": workflow_id,
            },
        )


async def _ensure_execution_snapshot_columns(
    connection: AsyncConnection,
) -> None:
    await add_column(
        connection,
        "execution_log",
        "workflow_version_id",
        "INTEGER",
    )
    await add_column(
        connection,
        "execution_log",
        "execution_policy_snapshot",
        "JSON",
    )
    await add_column(connection, "execution_log", "host_id", "INTEGER")
    await add_column(connection, "execution_log", "human_task_id", "INTEGER")
    await add_column(
        connection,
        "execution_log",
        "trigger_type",
        "VARCHAR(50) NOT NULL DEFAULT 'manual'",
    )
    await add_column(
        connection,
        "execution_log",
        "cancel_requested_at",
        "TIMESTAMP",
    )
    await add_index(
        connection,
        "execution_log",
        "ix_execution_log_workflow_version_id",
        "workflow_version_id",
    )


async def _ensure_connection_and_agent_columns(
    connection: AsyncConnection,
) -> None:
    await add_column(
        connection,
        "agent",
        "environment",
        "VARCHAR(20) NOT NULL DEFAULT 'local'",
    )
    await add_column(
        connection,
        "agent",
        "last_capabilities_at",
        "TIMESTAMP",
    )
    await add_index(
        connection,
        "agent",
        "ix_agent_environment",
        "environment",
    )
    await add_column(connection, "credential", "user_id", "INTEGER")
    await add_column(
        connection,
        "credential",
        "scope",
        "VARCHAR(32) NOT NULL DEFAULT 'workflow'",
    )
    await add_column(
        connection,
        "credential",
        "name",
        "VARCHAR(255)",
    )
    await add_index(
        connection,
        "credential",
        "ix_credential_user_id",
        "user_id",
    )
    await add_index(
        connection,
        "credential",
        "ix_credential_scope",
        "scope",
    )
    if await column_exists(connection, "credential", "workflow_id"):
        await connection.execute(
            text(
                "UPDATE credential SET user_id = ("
                "SELECT workflow.user_id FROM workflow "
                "WHERE workflow.id = credential.workflow_id"
                ") WHERE user_id IS NULL"
            )
        )
    await connection.execute(
        text(
            "UPDATE credential SET scope = 'workflow' "
            "WHERE scope IS NULL OR scope = ''"
        )
    )
    await connection.execute(
        text(
            "UPDATE credential SET name = key "
            "WHERE name IS NULL OR name = ''"
        )
    )


async def _ensure_human_task_recovery_columns(
    connection: AsyncConnection,
) -> None:
    await add_column(
        connection,
        "execution_log",
        "browser_profile_id",
        "INTEGER",
    )
    await add_column(
        connection,
        "human_task",
        "dedupe_key",
        "VARCHAR(255)",
    )
    await add_column(
        connection,
        "human_task",
        "verification_started_at",
        "TIMESTAMP",
    )
    await connection.execute(
        text(
            "CREATE INDEX IF NOT EXISTS "
            "ix_human_task_execution_status "
            "ON human_task (execution_id, status)"
        )
    )
    await connection.execute(
        text(
            "CREATE INDEX IF NOT EXISTS "
            "ix_human_task_status_expires "
            "ON human_task (status, expires_at)"
        )
    )
    await connection.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "uq_human_task_pending_dedupe "
            "ON human_task (dedupe_key) "
            "WHERE dedupe_key IS NOT NULL AND status = 'pending'"
        )
    )
    await add_index(
        connection,
        "execution_event",
        "ix_execution_event_execution_id_id",
        "execution_id",
    )
    await add_index(
        connection,
        "human_task_event",
        "ix_human_task_event_human_task_id",
        "human_task_id",
    )

    if not await column_exists(
        connection,
        "execution_log",
        "human_task_id",
    ):
        return
    result = await connection.execute(
        text(
            "SELECT id, step_logs FROM execution_log "
            "WHERE status = 'awaiting_user' "
            "AND human_task_id IS NULL "
            "ORDER BY id"
        )
    )
    now = datetime.utcnow()
    for row in result.fetchall():
        execution_id = int(row[0])
        step_logs = _json_array(row[1])
        checkpoint = next(
            (
                entry
                for entry in reversed(step_logs)
                if isinstance(entry, dict)
                and entry.get("type") == "checkpoint"
            ),
            {},
        )
        step_id = _optional_int(
            checkpoint.get("step_id")
            or checkpoint.get("next_step_id")
        )
        dedupe_key = f"legacy:execution:{execution_id}"
        existing = (
            await connection.execute(
                text(
                    "SELECT id FROM human_task "
                    "WHERE execution_id = :execution_id "
                    "AND status = 'pending' "
                    "ORDER BY id DESC LIMIT 1"
                ),
                {"execution_id": execution_id},
            )
        ).first()
        if existing is not None:
            task_id = int(existing[0])
        else:
            payload = {
                "legacy": True,
                "execution_id": execution_id,
                "login_state": checkpoint.get("login_state"),
                "verification": {
                    "type": "legacy_checkpoint",
                },
            }
            await connection.execute(
                text(
                    "INSERT INTO human_task "
                    "(execution_id, step_id, kind, dedupe_key, "
                    "instruction, payload, screenshot_path, status, "
                    "created_at, resolved_at, expires_at) "
                    "VALUES (:execution_id, :step_id, "
                    "'manual_confirmation', :dedupe_key, "
                    ":instruction, :payload, NULL, 'pending', "
                    ":created_at, NULL, NULL)"
                ),
                {
                    "execution_id": execution_id,
                    "step_id": step_id,
                    "dedupe_key": dedupe_key,
                    "instruction": (
                        "历史执行正在等待人工处理。"
                        "请确认原执行窗口状态后继续；"
                        "执行会重新验证当前检查点。"
                    ),
                    "payload": json.dumps(
                        payload,
                        ensure_ascii=False,
                    ),
                    "created_at": now,
                },
            )
            inserted_task = (
                await connection.execute(
                    text(
                        "SELECT id FROM human_task "
                        "WHERE dedupe_key = :dedupe_key "
                        "ORDER BY id DESC LIMIT 1"
                    ),
                    {"dedupe_key": dedupe_key},
                )
            ).scalar_one()
            task_id = int(inserted_task)
        await connection.execute(
            text(
                "UPDATE execution_log SET human_task_id = :task_id "
                "WHERE id = :execution_id"
            ),
            {
                "task_id": task_id,
                "execution_id": execution_id,
            },
        )


async def _ensure_host_routing_columns(
    connection: AsyncConnection,
) -> None:
    await add_column(
        connection,
        "execution_log",
        "host_environment",
        "VARCHAR(20)",
    )
    await add_column(
        connection,
        "execution_log",
        "host_bound",
        "BOOLEAN NOT NULL DEFAULT FALSE",
    )
    await add_column(
        connection,
        "execution_log",
        "host_affinity_hash",
        "VARCHAR(128)",
    )
    await add_column(
        connection,
        "execution_log",
        "host_reason",
        "VARCHAR(1000)",
    )
    await add_column(
        connection,
        "agent",
        "human_access_url",
        "VARCHAR(1000)",
    )
    await add_column(
        connection,
        "agent",
        "human_access_instructions",
        "TEXT",
    )
    await add_column(
        connection,
        "agent",
        "connection_security",
        "VARCHAR(20) NOT NULL DEFAULT 'unknown'",
    )
    await connection.execute(
        text(
            "UPDATE agent SET environment = 'vm' "
            "WHERE environment = 'isolated'"
        )
    )
    await connection.execute(
        text(
            "UPDATE agent SET environment = 'local' "
            "WHERE environment IS NULL OR environment = ''"
        )
    )


def _json_object(value: object) -> dict[str, object] | None:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        return dict(parsed) if isinstance(parsed, dict) else None
    return None


def _json_array(value: object) -> list[object]:
    if isinstance(value, list):
        return list(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return list(parsed) if isinstance(parsed, list) else []
    return []


def _optional_int(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def init_db() -> None:
    from app import models  # noqa: F401

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await run_schema_migrations(connection)


async def run_schema_migrations(connection: AsyncConnection) -> list[int]:
    """可移植迁移入口：SQLite / PostgreSQL 共用一套加法式步骤。"""

    return await run_versioned_migrations(
        connection,
        [
            (
                1,
                "workflow-executor-columns",
                _ensure_workflow_executor_columns,
            ),
            (
                2,
                "agent-config-columns",
                _ensure_agent_config_columns,
            ),
            (
                3,
                "execution-artifacts-metrics-columns",
                _ensure_execution_artifact_columns,
            ),
            (
                4,
                "workspace-columns",
                _ensure_workspace_columns,
            ),
            (
                5,
                "mcp-plugin-integrity-columns",
                _ensure_plugin_integrity_columns,
            ),
            (
                6,
                "workflow-versioning-columns",
                _ensure_workflow_versioning_columns,
            ),
            (
                7,
                "execution-snapshot-columns",
                _ensure_execution_snapshot_columns,
            ),
            (
                8,
                "connection-and-agent-columns",
                _ensure_connection_and_agent_columns,
            ),
            (
                9,
                "human-task-recovery-and-events",
                _ensure_human_task_recovery_columns,
            ),
            (
                10,
                "host-routing-and-agent-environment",
                _ensure_host_routing_columns,
            ),
        ],
    )
