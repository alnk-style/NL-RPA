"""MCP Server 注册、连接测试、工具授权与审计查询接口。

除 ``GET/PATCH /api/mcp/settings`` 外，其余接口都在 MCP 客户端开关
之后，默认关闭；密钥只回显是否已配置，工具清单里的风险级别与授权
状态按 JSON 字段存储。开关可在应用「连接器与 MCP」页面切换，并
优先于 ``.env`` 里的 ``MCP_CLIENT`` 默认值。
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    status,
)
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import runtime_flags
from app.api.dependencies import get_current_user
from app.api.workspace_deps import get_workspace_context
from app.config import get_settings
from app.database import AsyncSessionLocal, get_db
from app.engine.mcp_client import MCPClientError, sdk_available
from app.engine.mcp_registry import MCPRegistry
from app.engine.mcp_tool_catalog import MCPToolCatalog
from app.models.mcp import MCPServer
from app.models.user import User
from app.security.redaction import redact_payload
from app.security.workspaces import (
    WorkspaceContext,
)

router = APIRouter(prefix="/api/mcp", tags=["MCP"])

RiskLevel = Literal["read_only", "mutating", "destructive"]
Transport = Literal["http", "stdio"]


class MCPServerCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    version: str | None = Field(default=None, max_length=100)
    fingerprint: str | None = Field(default=None, max_length=128)
    signature: str | None = Field(default=None, max_length=255)
    description: str | None = None
    transport: Transport = "http"
    url: str | None = None
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    auth_token: str | None = None
    enabled: bool = True
    default_risk: RiskLevel = "mutating"
    timeout_seconds: float | None = Field(default=None, gt=0, le=3600)


class MCPServerUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    version: str | None = Field(default=None, max_length=100)
    fingerprint: str | None = Field(default=None, max_length=128)
    signature: str | None = Field(default=None, max_length=255)
    description: str | None = None
    url: str | None = None
    command: str | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None
    headers: dict[str, str] | None = None
    auth_token: str | None = None
    enabled: bool | None = None
    default_risk: RiskLevel | None = None
    timeout_seconds: float | None = Field(default=None, gt=0, le=3600)


class MCPToolAuthorizeRequest(BaseModel):
    authorized: bool = True
    confirmed: bool = False
    risk_level: RiskLevel | None = None


class MCPSettingsUpdateRequest(BaseModel):
    enabled: bool


class MCPPinFingerprintRequest(BaseModel):
    version: str | None = Field(default=None, max_length=100)
    sign: bool = False


def require_mcp_enabled() -> None:
    if not runtime_flags.resolve("mcp_client", get_settings()):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "MCP 客户端未启用，"
                "请在「连接器与 MCP」页面打开开关。"
            ),
        )


def _mcp_settings_payload() -> dict[str, Any]:
    config = get_settings()
    return {
        "enabled": runtime_flags.resolve("mcp_client", config),
        "source": runtime_flags.source("mcp_client"),
        "env_default": runtime_flags.env_default("mcp_client", config),
        "sdk_available": sdk_available(),
        "stdio_enabled": bool(
            getattr(config, "MCP_STDIO_ENABLED", False)
        ),
    }


@router.get("/settings")
async def read_mcp_settings(
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """读取 MCP 开关状态；关闭时也可访问，供设置页展示开关。"""

    return _mcp_settings_payload()


@router.patch("/settings")
async def update_mcp_settings(
    payload: MCPSettingsUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """切换 MCP 开关，写入数据库并立即生效。"""

    await runtime_flags.set_override(
        db,
        "mcp_client",
        payload.enabled,
        updated_by=f"user:{current_user.id}",
    )
    return _mcp_settings_payload()


def get_mcp_registry(request: Request) -> MCPRegistry:
    registry = getattr(request.app.state, "mcp_registry", None)
    if registry is None:
        registry = MCPRegistry(
            session_factory=AsyncSessionLocal,
            config=get_settings(),
        )
        request.app.state.mcp_registry = registry
    return registry


def serialize_server(server: MCPServer) -> dict[str, Any]:
    config = dict(server.config or {})
    return {
        "id": server.id,
        "name": server.name,
        "description": server.description,
        "transport": server.transport,
        "url": server.url,
        "command": server.command,
        "args": list(server.args or []),
        "env_keys": sorted(str(key) for key in (server.env or {})),
        "headers": redact_payload(dict(server.headers or {})),
        "enabled": bool(server.enabled),
        "default_risk": server.default_risk,
        "timeout_seconds": server.timeout_seconds,
        "has_secret": bool(server.secret_encrypted),
        "workspace_id": server.workspace_id,
        "local_app_id": config.get("local_app_id"),
        "local_managed": bool(config.get("local_managed")),
        "version": server.version,
        "fingerprint": server.fingerprint,
        "signature": server.signature,
        "toolset_fingerprint": server.toolset_fingerprint,
        "tools": list(server.tools or []),
        "last_error": server.last_error,
        "last_tested_at": (
            server.last_tested_at.isoformat()
            if server.last_tested_at
            else None
        ),
        "created_at": (
            server.created_at.isoformat() if server.created_at else None
        ),
        "updated_at": (
            server.updated_at.isoformat() if server.updated_at else None
        ),
    }


@router.get(
    "/catalog",
    dependencies=[Depends(require_mcp_enabled)],
)
async def list_mcp_catalog(
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    registry: MCPRegistry = Depends(get_mcp_registry),
    context: WorkspaceContext = Depends(get_workspace_context),
) -> dict[str, Any]:
    settings = get_settings()
    catalog = MCPToolCatalog(
        registry,
        max_tools=int(
            getattr(settings, "MCP_CATALOG_MAX_TOOLS", 50) or 50
        ),
    )
    tools = await catalog.list_tools(
        user_id=current_user.id,
        workspace_id=(
            context.workspace_id if context.isolation_enabled else None
        ),
        q=q,
        limit=limit,
        session=db,
    )
    return {
        "enabled": True,
        "tools": tools,
        "metadata_trust": "untrusted",
    }


@router.get(
    "/servers",
    dependencies=[Depends(require_mcp_enabled)],
)
async def list_servers(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    registry: MCPRegistry = Depends(get_mcp_registry),
    context: WorkspaceContext = Depends(get_workspace_context),
) -> dict[str, Any]:
    servers = await registry.list_servers(
        user_id=current_user.id,
        workspace_id=(
            context.workspace_id if context.isolation_enabled else None
        ),
        session=db,
    )
    return {
        "enabled": True,
        "sdk_available": sdk_available(),
        "servers": [serialize_server(server) for server in servers],
    }


@router.post(
    "/servers",
    dependencies=[Depends(require_mcp_enabled)],
)
async def create_server(
    payload: MCPServerCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    registry: MCPRegistry = Depends(get_mcp_registry),
    context: WorkspaceContext = Depends(get_workspace_context),
) -> dict[str, Any]:
    if payload.transport == "http" and not (payload.url or "").strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="HTTP 传输必须填写 url",
        )
    if payload.transport == "stdio" and not (payload.command or "").strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="stdio 传输必须填写 command",
        )
    try:
        server = await registry.create_server(
            payload.model_dump(),
            user_id=current_user.id,
            workspace_id=(
                context.workspace_id
                if context.isolation_enabled
                else None
            ),
            session=db,
        )
    except MCPClientError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="同名 MCP Server 已存在",
        ) from exc
    return {"server": serialize_server(server)}


@router.patch(
    "/servers/{server_id}",
    dependencies=[Depends(require_mcp_enabled)],
)
async def update_server(
    server_id: int,
    payload: MCPServerUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    registry: MCPRegistry = Depends(get_mcp_registry),
    context: WorkspaceContext = Depends(get_workspace_context),
) -> dict[str, Any]:
    server = await registry.get_server(
        server_id,
        user_id=current_user.id,
        workspace_id=(
            context.workspace_id if context.isolation_enabled else None
        ),
        session=db,
    )
    if server is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="MCP Server 不存在",
        )
    data = payload.model_dump(exclude_unset=True)
    secret = data.pop("auth_token", None)
    if secret:
        from app.security.credentials import encrypt_value

        try:
            server.secret_encrypted = encrypt_value(
                get_settings(),
                str(secret),
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"无法加密访问令牌：{exc}",
            ) from exc
    for field in (
        "name",
        "version",
        "fingerprint",
        "signature",
        "description",
        "url",
        "command",
        "args",
        "env",
        "headers",
        "enabled",
        "default_risk",
        "timeout_seconds",
    ):
        if field in data and data[field] is not None:
            setattr(server, field, data[field])
    if context.isolation_enabled and server.workspace_id is None:
        server.workspace_id = context.workspace_id
    await db.commit()
    await db.refresh(server)
    registry.client_pool.drop(server_id)
    return {"server": serialize_server(server)}


@router.post(
    "/servers/{server_id}/fingerprint",
    dependencies=[Depends(require_mcp_enabled)],
)
async def pin_fingerprint(
    server_id: int,
    payload: MCPPinFingerprintRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    registry: MCPRegistry = Depends(get_mcp_registry),
    context: WorkspaceContext = Depends(get_workspace_context),
) -> dict[str, Any]:
    result = await registry.pin_fingerprint(
        server_id,
        version=payload.version,
        sign=payload.sign,
        user_id=current_user.id,
        workspace_id=(
            context.workspace_id if context.isolation_enabled else None
        ),
        session=db,
    )
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error") or "指纹固定失败",
        )
    return result


@router.delete(
    "/servers/{server_id}",
    dependencies=[Depends(require_mcp_enabled)],
)
async def delete_server(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    registry: MCPRegistry = Depends(get_mcp_registry),
    context: WorkspaceContext = Depends(get_workspace_context),
) -> dict[str, Any]:
    deleted = await registry.delete_server(
        server_id,
        user_id=current_user.id,
        workspace_id=(
            context.workspace_id if context.isolation_enabled else None
        ),
        session=db,
    )
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="MCP Server 不存在",
        )
    return {"status": "deleted", "server_id": server_id}


@router.post(
    "/servers/{server_id}/test",
    dependencies=[Depends(require_mcp_enabled)],
)
async def test_server(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    registry: MCPRegistry = Depends(get_mcp_registry),
    context: WorkspaceContext = Depends(get_workspace_context),
) -> dict[str, Any]:
    result = await registry.test_server(
        server_id,
        user_id=current_user.id,
        workspace_id=(
            context.workspace_id if context.isolation_enabled else None
        ),
        session=db,
    )
    if not result.get("success") and result.get("error") == "MCP Server 不存在":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="MCP Server 不存在",
        )
    return result


@router.post(
    "/servers/{server_id}/tools/{tool}/authorize",
    dependencies=[Depends(require_mcp_enabled)],
)
async def authorize_tool(
    server_id: int,
    tool: str,
    payload: MCPToolAuthorizeRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    registry: MCPRegistry = Depends(get_mcp_registry),
    context: WorkspaceContext = Depends(get_workspace_context),
) -> dict[str, Any]:
    server = await registry.get_server(
        server_id,
        user_id=current_user.id,
        workspace_id=(
            context.workspace_id if context.isolation_enabled else None
        ),
        session=db,
    )
    if server is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="MCP Server 不存在",
        )
    result = await registry.authorize_tool(
        server_id,
        tool,
        authorized=payload.authorized,
        confirmed=payload.confirmed,
        risk_level=payload.risk_level,
        user_id=current_user.id,
        workspace_id=(
            context.workspace_id if context.isolation_enabled else None
        ),
        authorized_by=f"user:{current_user.id}",
        session=db,
    )
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error") or "授权失败",
        )
    return result


@router.get(
    "/audits",
    dependencies=[Depends(require_mcp_enabled)],
)
async def list_audits(
    server_id: int | None = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    registry: MCPRegistry = Depends(get_mcp_registry),
    context: WorkspaceContext = Depends(get_workspace_context),
) -> dict[str, Any]:
    audits = await registry.list_audits(
        user_id=current_user.id,
        workspace_id=(
            context.workspace_id if context.isolation_enabled else None
        ),
        server_id=server_id,
        limit=limit,
        session=db,
    )
    return {"audits": audits}


@router.get(
    "/connectors",
    dependencies=[Depends(require_mcp_enabled)],
)
async def connector_capabilities(
    request: Request,
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    connectors = getattr(request.app.state, "connectors", None)
    if connectors is None:
        registry = get_mcp_registry(request)
        connectors = _fallback_connector_registry(registry)
    auth_status = await connectors.auth_status()
    return {
        "connectors": connectors.names(),
        "capabilities": await connectors.capabilities(),
        "auth_status": auth_status,
    }


def _fallback_connector_registry(registry: MCPRegistry) -> Any:
    from app.engine.connectors.base import ConnectorRegistry
    from app.engine.connectors.mcp_connector import MCPConnector

    connectors = ConnectorRegistry()
    connectors.register(MCPConnector(registry), aliases=["mcp_client"])
    return connectors


__all__ = [
    "get_mcp_registry",
    "require_mcp_enabled",
    "router",
    "serialize_server",
]
