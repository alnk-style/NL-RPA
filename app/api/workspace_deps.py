"""workspace FastAPI 依赖（与 security 模块解耦，避免循环导入）。"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.database import get_db
from app.models.user import User
from app.security.workspaces import (
    WORKSPACE_HEADER,
    WorkspaceContext,
    resolve_workspace,
    workspace_isolation_enabled,
)


async def get_workspace_context(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> WorkspaceContext:
    """解析当前请求的 workspace；开关关闭时保持旧行为。"""

    if not workspace_isolation_enabled():
        return WorkspaceContext(
            user=current_user,
            workspace=None,
            isolation_enabled=False,
        )

    raw = (
        request.headers.get(WORKSPACE_HEADER)
        or request.query_params.get("workspace_id")
    )
    workspace_id: int | None = None
    if raw is not None and str(raw).strip():
        try:
            workspace_id = int(str(raw).strip())
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="workspace_id 必须是整数",
            ) from exc
    workspace = await resolve_workspace(db, current_user, workspace_id)
    if workspace is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workspace not found",
        )
    return WorkspaceContext(
        user=current_user,
        workspace=workspace,
        isolation_enabled=True,
    )


__all__ = ["get_workspace_context"]
