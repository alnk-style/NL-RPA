"""最小 workspace 概念与隔离谓词。

默认 ``WORKSPACE_ISOLATION=false``：所有查询与新建行为保持阶段 4 的
单用户语义；打开开关后，请求通过 ``X-Workspace-Id`` 头（或查询参数
``workspace_id``）选择工作区，未指定时使用当前用户的默认工作区。

历史数据（``workspace_id IS NULL``）在开关打开后的启动阶段执行一次
回填：按 ``user_id``（执行记录按 workflow 归属）写入默认工作区。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import get_settings
from app.models.agent import DesktopAgent
from app.models.credential import Credential
from app.models.execution import ExecutionLog
from app.models.mcp import MCPServer, ToolCallAudit
from app.models.user import User
from app.models.workflow import Workflow
from app.models.workspace import Workspace

DEFAULT_WORKSPACE_SLUG = "default"
WORKSPACE_HEADER = "X-Workspace-Id"


def workspace_isolation_enabled(config: Any | None = None) -> bool:
    if config is not None:
        return bool(getattr(config, "WORKSPACE_ISOLATION", False))
    try:
        return bool(get_settings().WORKSPACE_ISOLATION)
    except Exception:  # noqa: BLE001
        return False


def default_workspace_quota(config: Any | None = None) -> dict[str, int]:
    def _value(name: str, fallback: int) -> int:
        try:
            source = config if config is not None else get_settings()
            return max(0, int(getattr(source, name, fallback)))
        except Exception:  # noqa: BLE001
            return fallback

    return {
        "max_concurrent_executions": _value(
            "WORKSPACE_DEFAULT_MAX_CONCURRENT_EXECUTIONS",
            2,
        ),
        "max_concurrent_agent_tasks": _value(
            "WORKSPACE_DEFAULT_MAX_CONCURRENT_AGENT_TASKS",
            2,
        ),
    }


def workspace_quota(
    workspace: Workspace | None,
    config: Any | None = None,
) -> dict[str, int]:
    quota = default_workspace_quota(config)
    if workspace is not None and isinstance(workspace.quota, dict):
        for key in ("max_concurrent_executions", "max_concurrent_agent_tasks"):
            raw = workspace.quota.get(key)
            if isinstance(raw, (int, float)) and raw >= 0:
                quota[key] = int(raw)
    return quota


async def ensure_default_workspace(
    session: AsyncSession,
    user: User,
    *,
    config: Any | None = None,
) -> Workspace:
    result = await session.execute(
        select(Workspace).where(
            Workspace.owner_id == user.id,
            Workspace.slug == DEFAULT_WORKSPACE_SLUG,
        )
    )
    workspace = result.scalar_one_or_none()
    if workspace is not None:
        return workspace

    settings = config if config is not None else get_settings()
    workspace = Workspace(
        owner_id=user.id,
        name=str(
            getattr(settings, "DEFAULT_WORKSPACE_NAME", "默认工作区")
        ),
        slug=DEFAULT_WORKSPACE_SLUG,
        is_default=True,
        quota=default_workspace_quota(settings),
    )
    session.add(workspace)
    await session.flush()
    await session.refresh(workspace)
    return workspace


async def resolve_workspace(
    session: AsyncSession,
    user: User,
    workspace_id: int | None = None,
    *,
    config: Any | None = None,
) -> Workspace | None:
    if workspace_id is None:
        return await ensure_default_workspace(
            session,
            user,
            config=config,
        )
    workspace = await session.get(Workspace, int(workspace_id))
    if workspace is None or workspace.owner_id != user.id:
        return None
    return workspace


@dataclass
class WorkspaceContext:
    user: User
    workspace: Workspace | None
    isolation_enabled: bool

    @property
    def workspace_id(self) -> int | None:
        return self.workspace.id if self.workspace is not None else None

    def scope(self, statement: Any, model: Any) -> Any:
        return apply_workspace_scope(statement, model, self)


def apply_workspace_scope(
    statement: Any,
    model: Any,
    context: WorkspaceContext | None,
) -> Any:
    if (
        context is None
        or not context.isolation_enabled
        or context.workspace is None
    ):
        return statement
    return statement.where(model.workspace_id == context.workspace.id)


async def list_user_workspaces(
    session: AsyncSession,
    user: User,
) -> list[Workspace]:
    await ensure_default_workspace(session, user)
    result = await session.execute(
        select(Workspace)
        .where(Workspace.owner_id == user.id)
        .order_by(Workspace.id)
    )
    return list(result.scalars().all())


async def backfill_workspace_ids(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    config: Any | None = None,
) -> dict[str, int]:
    """给历史行补齐默认工作区；返回每张表更新的行数。"""

    counts: dict[str, int] = {}
    async with session_factory() as session:
        users = list(
            (
                await session.execute(select(User))
            ).scalars().all()
        )
        for user in users:
            workspace = await ensure_default_workspace(
                session,
                user,
                config=config,
            )
            statements = (
                (
                    "workflow",
                    update(Workflow)
                    .where(
                        Workflow.user_id == user.id,
                        Workflow.workspace_id.is_(None),
                    )
                    .values(workspace_id=workspace.id),
                ),
                (
                    "agent",
                    update(DesktopAgent)
                    .where(
                        DesktopAgent.user_id == user.id,
                        DesktopAgent.workspace_id.is_(None),
                    )
                    .values(workspace_id=workspace.id),
                ),
                (
                    "mcp_server",
                    update(MCPServer)
                    .where(
                        MCPServer.user_id == user.id,
                        MCPServer.workspace_id.is_(None),
                    )
                    .values(workspace_id=workspace.id),
                ),
                (
                    "tool_call_audit",
                    update(ToolCallAudit)
                    .where(
                        ToolCallAudit.user_id == user.id,
                        ToolCallAudit.workspace_id.is_(None),
                    )
                    .values(workspace_id=workspace.id),
                ),
            )
            for name, statement in statements:
                result = await session.execute(statement)
                counts[name] = counts.get(name, 0) + int(
                    result.rowcount or 0
                )

            # workflow 派生表（执行记录、凭据）没有直接的 user_id。
            workflow_ids = select(Workflow.id).where(
                Workflow.user_id == user.id
            )
            for name, table in (
                ("execution_log", ExecutionLog),
                ("credential", Credential),
            ):
                result = await session.execute(
                    table.__table__.update()
                    .where(
                        table.workflow_id.in_(workflow_ids),
                        table.workspace_id.is_(None),
                    )
                    .values(workspace_id=workspace.id)
                )
                counts[name] = counts.get(name, 0) + int(
                    result.rowcount or 0
                )
        await session.commit()
    return counts


async def workspace_row_counts(
    session: AsyncSession,
    workspace_id: int,
) -> dict[str, int]:
    """诊断与测试用：统计 workspace 下各表行数。"""

    tables = {
        "workflow": "workflow",
        "execution_log": "execution_log",
        "credential": "credential",
        "agent": "agent",
        "mcp_server": "mcp_server",
        "tool_call_audit": "tool_call_audit",
    }
    counts: dict[str, int] = {}
    for name, table in tables.items():
        value = await session.execute(
            text(
                f"SELECT COUNT(*) FROM {table} "
                "WHERE workspace_id = :workspace_id"
            ),
            {"workspace_id": workspace_id},
        )
        counts[name] = int(value.scalar_one())
    return counts


__all__ = [
    "DEFAULT_WORKSPACE_SLUG",
    "WORKSPACE_HEADER",
    "WorkspaceContext",
    "apply_workspace_scope",
    "backfill_workspace_ids",
    "default_workspace_quota",
    "ensure_default_workspace",
    "list_user_workspaces",
    "resolve_workspace",
    "workspace_isolation_enabled",
    "workspace_quota",
    "workspace_row_counts",
]
