"""流程凭据管理接口。

接口只返回凭据键名，永远不回显明文；明文只在执行时解密注入到
运行时上下文。密钥使用现有 Fernet 密钥加密后存库。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.config import get_settings
from app.database import get_db
from app.models.user import User
from app.models.workflow import Workflow
from app.security.credentials import (
    delete_credential,
    list_credential_keys,
    save_credential,
)
from app.security.workspaces import (
    WorkspaceContext,
)
from app.api.workspace_deps import get_workspace_context

router = APIRouter(
    prefix="/api/workflows/{workflow_id}/credentials",
    tags=["凭据管理"],
)

CREDENTIAL_KEY_PATTERN = r"^[A-Za-z0-9_.-]{1,255}$"


class CredentialUpsertRequest(BaseModel):
    value: str = Field(min_length=1, max_length=10000)


async def _get_owned_workflow(
    workflow_id: int,
    user_id: int,
    db: AsyncSession,
    workspace_id: int | None = None,
) -> Workflow:
    statement = select(Workflow).where(
        Workflow.id == workflow_id,
        Workflow.user_id == user_id,
    )
    if workspace_id is not None:
        statement = statement.where(
            Workflow.workspace_id == workspace_id
        )
    result = await db.execute(statement)
    workflow = result.scalar_one_or_none()
    if workflow is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow not found",
        )
    return workflow


@router.get("")
async def list_credentials(
    workflow_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(get_workspace_context),
) -> dict[str, Any]:
    workflow = await _get_owned_workflow(
        workflow_id,
        current_user.id,
        db,
        context.workspace_id if context.isolation_enabled else None,
    )
    keys = await list_credential_keys(db, workflow.id)
    return {
        "workflow_id": workflow.id,
        "keys": keys,
    }


@router.put("/{key}")
async def upsert_credential(
    workflow_id: int,
    payload: CredentialUpsertRequest,
    key: str = Path(pattern=CREDENTIAL_KEY_PATTERN),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(get_workspace_context),
) -> dict[str, Any]:
    workflow = await _get_owned_workflow(
        workflow_id,
        current_user.id,
        db,
        context.workspace_id if context.isolation_enabled else None,
    )
    await save_credential(
        db,
        get_settings(),
        workflow.id,
        key,
        payload.value,
        workspace_id=(
            context.workspace_id
            if context.isolation_enabled
            else workflow.workspace_id
        ),
    )
    await db.commit()
    return {
        "status": "saved",
        "workflow_id": workflow.id,
        "key": key,
    }


@router.delete("/{key}")
async def remove_credential(
    workflow_id: int,
    key: str = Path(pattern=CREDENTIAL_KEY_PATTERN),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(get_workspace_context),
) -> dict[str, Any]:
    workflow = await _get_owned_workflow(
        workflow_id,
        current_user.id,
        db,
        context.workspace_id if context.isolation_enabled else None,
    )
    deleted = await delete_credential(db, workflow.id, key)
    await db.commit()
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Credential not found",
        )
    return {
        "status": "deleted",
        "workflow_id": workflow.id,
        "key": key,
    }


__all__ = [
    "CREDENTIAL_KEY_PATTERN",
    "CredentialUpsertRequest",
    "router",
]
